# SPDX-License-Identifier: MIT
"""Bound one diagnostic process group; never broaden sandbox permissions."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time


def scratch_size(roots):
    total = entries = 0
    for root in roots:
        if not root.exists():
            continue
        for directory, folders, files in os.walk(root, followlinks=False):
            for name in folders + files:
                entries += 1
                assert entries <= 8192, 'scratch entry bound'
                try:
                    info = (Path(directory) / name).lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    total += info.st_size
    return total


def ps_rows(text, group):
    rows = []
    for line in text.splitlines():
        fields = line.split()
        assert len(fields) == 4 and all(x.isdecimal() for x in fields), 'unexpected numeric ps format'
        pid, parent, process_group, rss_kib = map(int, fields)
        if process_group == group:
            rows.append(dict(pid=pid, parent=parent, group=process_group, rssBytes=rss_kib * 1024))
    return rows


def sample(ps, group, bounded_run, cwd, env):
    row = bounded_run([ps, '-A', '-o', 'pid=,ppid=,pgid=,rss='], cwd, env,
                      timeout=1, limit=262144)
    assert row['exitCode'] == 0 and row['stopReason'] is None, 'resource ps unavailable or bounded out'
    return ps_rows(row['output'], group)


def kill_group(group):
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass


def execute(command, ps, out, bounded_run, *, deadline=120, output_limit=524288,
            scratch_limit=134217728, rss_limit=2147483648, process_limit=8,
            interval=0.2, sampler=sample, scratch_parent=None, ps_version=None):
    started = time.monotonic()
    receipt = dict(command=command, limits=dict(deadlineSeconds=deadline, outputBytes=output_limit,
                   scratchBytes=scratch_limit, rssBytes=rss_limit, processes=process_limit,
                   sampleIntervalSeconds=interval, psDeadlineSeconds=1, terminationGraceSeconds=5),
                   maxObservedRssBytes=0, maxObservedProcesses=0, maxObservedScratchBytes=0,
                   samples=0, stopReason=None,
                   scope='Polled controller bounds, not hard daemon/OS quotas; only owned session is stopped')
    provider = Path(ps).resolve(strict=True)
    with provider.open('rb') as file:
        data = file.read(16 * 1024**2 + 1)
    assert len(data) <= 16 * 1024**2, 'ps provider size bound'
    receipt['psProvider'] = dict(selected=ps, resolved=str(provider),
                                 sha256=hashlib.sha256(data).hexdigest(),
                                 declaredPackageVersion=ps_version)
    output = bytearray()
    with tempfile.TemporaryDirectory(prefix='icu-controller-', dir=scratch_parent) as temp:
        root = Path(temp).resolve()
        work, home, scratch = (root / name for name in ('work', 'home', 'tmp'))
        for directory in (work, home, scratch):
            directory.mkdir()
        env = dict(os.environ, HOME=str(home), TMPDIR=str(scratch))
        receipt['selectedEnvironment'] = dict(HOME=str(home), TMPDIR=str(scratch))
        receipt['cwd'] = str(work)
        child = subprocess.Popen(command, cwd=work, env=env, start_new_session=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        receipt['ownedSession'] = child.pid
        reason = None
        next_sample = started
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map() or child.poll() is None:
                    now = time.monotonic()
                    if now - started >= deadline:
                        reason = 'deadline'
                        break
                    if now >= next_sample:
                        rows = sampler(ps, child.pid, bounded_run, work, env)
                        if child.poll() is None:
                            assert any(row['pid'] == child.pid for row in rows), 'owned helper absent from ps'
                        size = scratch_size([root] + ([] if out.is_relative_to(root) else [out]))
                        rss = sum(row['rssBytes'] for row in rows)
                        receipt['samples'] += 1
                        receipt['maxObservedRssBytes'] = max(receipt['maxObservedRssBytes'], rss)
                        receipt['maxObservedProcesses'] = max(receipt['maxObservedProcesses'], len(rows))
                        receipt['maxObservedScratchBytes'] = max(receipt['maxObservedScratchBytes'], size)
                        if size > scratch_limit:
                            reason = 'scratch-limit'
                        elif rss > rss_limit:
                            reason = 'rss-limit'
                        elif len(rows) > process_limit:
                            reason = 'process-limit'
                        if reason:
                            break
                        next_sample = time.monotonic() + interval
                    for key, _ in selector.select(0.05):
                        data = os.read(key.fileobj.fileno(), 8192)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        remaining = output_limit - len(output)
                        output.extend(data[:remaining])
                        if len(data) > remaining:
                            reason = 'output-limit'
                            break
                    if reason:
                        break
        except Exception as error:
            reason = 'monitor-error'
            receipt['monitorError'] = str(error)[:2048]
        finally:
            # Session was created by this controller; no external process IDs or
            # process-name matching are used for signalling.
            kill_group(child.pid)
            try:
                receipt['exitCode'] = child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                reason = reason or 'termination-timeout'
                receipt['exitCode'] = None
            child.stdout.close()
        receipt['stopReason'] = reason
        receipt['elapsedSeconds'] = time.monotonic() - started
        receipt['output'] = output.decode('utf-8', errors='replace')
        receipt['success'] = reason is None and receipt['exitCode'] == 0
    out.mkdir(parents=True, exist_ok=True)
    (out / 'controller.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(receipt['output'], end='', flush=True)
    print('ICU_STORE_CONTROLLER ' + json.dumps({k: v for k, v in receipt.items() if k != 'output'}), flush=True)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ps', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--ps-version', required=True)
    parser.add_argument('--probe-helper', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('probe', args.probe_helper)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    assert command, 'diagnostic command required'
    receipt = execute(command, args.ps, args.out, probe.run, ps_version=args.ps_version)
    sys.exit(0 if receipt['success'] else 1)
