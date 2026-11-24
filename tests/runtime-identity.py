# SPDX-License-Identifier: MIT
"""Bounded CI diagnostics for the explicitly selected fixture executable.

Candidate runtime files are an inventory, not proof of the failing process's
loaded assembly. This helper never selects or substitutes an SDK/runtime.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import time


def command(argv, limit=65536, timeout=15):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    reason = None
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            if time.monotonic() >= deadline:
                reason = 'timeout'
                break
            for key, _ in selector.select(min(0.1, max(0, deadline-time.monotonic()))):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                remaining = limit-len(output)
                output.extend(data[:remaining])
                if len(data) > remaining:
                    reason = 'output-limit'
                    break
            if reason:
                break
    if reason:
        process.kill()
    try:
        code = process.wait(timeout=max(0.1, deadline-time.monotonic()))
    except subprocess.TimeoutExpired:
        reason = 'timeout'
        process.kill()
        code = process.wait()
    process.stdout.close()
    return {'argv': argv, 'exitCode': code, 'stopReason': reason,
            'output': output.decode('utf-8', errors='replace')}


def file_identity(path, limit):
    path = Path(path)
    record = {'path': str(path)}
    try:
        record['resolvedPath'] = str(path.resolve())
        with path.open('rb') as stream:
            data = stream.read(limit+1)
        if len(data) > limit:
            record['error'] = 'size-limit'
        else:
            record.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
            if path.name == '.version':
                record['text'] = data.decode('utf-8', errors='replace')
    except (OSError, RuntimeError) as error:
        record['error'] = str(error)
    return record


def collect(dotnet):
    record = {'schemaVersion': 1, 'executable': file_identity(dotnet, 64*1024*1024),
              'scope': 'Selected fixture executable and candidate runtime inventory; loaded CoreLib/source attribution remains unproven.'}
    for key, argument in [('info', '--info'), ('runtimes', '--list-runtimes')]:
        try:
            record[key] = command([dotnet, argument])
        except OSError as error:
            record[key] = {'error': str(error)}
    record['coreLibCandidates'] = []
    listing = record['runtimes']
    if listing.get('exitCode') == 0 and listing.get('stopReason') is None:
        for line in listing['output'].splitlines():
            parts = line.split(' ', 2)
            if len(parts) != 3 or parts[0] != 'Microsoft.NETCore.App':
                continue
            version, parent = parts[1:]
            if '/' in version or '\\' in version or version in ('.', '..') or not parent.startswith('[') or not parent.endswith(']'):
                continue
            root = Path(parent[1:-1])/version
            if not root.is_absolute():
                continue
            if len(record['coreLibCandidates']) == 8:
                record['candidateLimitReached'] = True
                break
            record['coreLibCandidates'].append({
                'version': version,
                'coreLib': file_identity(root/'System.Private.CoreLib.dll', 64*1024*1024),
                'buildVersion': file_identity(root/'.version', 4096)})
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dotnet', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'runtime-identity.json').write_text(json.dumps(collect(args.dotnet), indent=2)+'\n')
