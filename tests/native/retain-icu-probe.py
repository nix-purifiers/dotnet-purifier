# SPDX-License-Identifier: MIT
"""Retain an already-realized diagnostic; never build, replay or grant access."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def retain(repo, out, runner=subprocess.run, store_root=Path('/nix/store')):
    repo, out = Path(repo).resolve(), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    receipt = dict(status='starting', observationOnly=True, productFixClaimed=False,
                   commands=[], files={})

    def read(argv):
        row = dict(argv=argv, cwd=str(repo), timeoutSeconds=30)
        receipt['commands'].append(row)
        result = runner(argv, cwd=repo, text=True, capture_output=True, timeout=30)
        row.update(exitCode=result.returncode, stdout=result.stdout[:262144], stderr=result.stderr[:262144])
        assert len(result.stdout) <= 262144 and len(result.stderr) <= 262144, 'metadata output bound'
        assert result.returncode == 0, ('metadata read failed', argv, result.returncode)
        return result.stdout

    try:
        receipt['source'] = read(['git', 'rev-parse', 'HEAD', 'HEAD^{tree}']).splitlines()
        system = read(['nix', 'eval', '--offline', '--raw', '--impure', '--expr', 'builtins.currentSystem']).strip()
        assert system == 'aarch64-darwin', ('unexpected diagnostic platform', system)
        ref = str(repo) + '#checks.' + system + '.current-icu-store-diagnostic'
        model = json.loads(read(['nix', 'eval', '--offline', '--json', '--option',
                                 'allow-import-from-derivation', 'false', ref,
                                 '--apply', 'p: { out = toString p; drv = p.drvPath; }']))
        receipt['model'] = model
        source = Path(model['out'])
        assert source.parent == store_root and not source.is_symlink(), 'unexpected store output'
        if not source.is_dir():
            receipt['status'] = 'not-realized-no-build-attempted'
        else:
            for name in ('probe.json', 'controller.json'):
                path = source / name
                row = receipt['files'][name] = {}
                try:
                    assert path.is_file() and not path.is_symlink(), 'missing regular diagnostic file'
                    with path.open('rb') as file:
                        data = file.read(2 * 1024**2 + 1)
                    assert len(data) <= 2 * 1024**2, 'diagnostic file size bound'
                    (out / name).write_bytes(data)
                    row.update(size=len(data), sha256=hashlib.sha256(data).hexdigest(), copied=True)
                    # Preserve bytes even if a diagnostic failed while writing JSON.
                    try:
                        json.loads(data)
                    except (ValueError, UnicodeError) as error:
                        row['parseError'] = str(error)
                except (OSError, AssertionError) as error:
                    row['error'] = str(error)
            receipt['outputMetadata'] = json.loads(read([
                'nix', 'path-info', '--json', '--json-format', '1', str(source)]))
            partial = any('error' in row or 'parseError' in row for row in receipt['files'].values())
            receipt['status'] = 'retained-partial-observation' if partial else 'retained-observation-only'
    except (OSError, AssertionError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        receipt.update(status='retention-error', error=str(error)[:4096])
    finally:
        (out / 'retention.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    retain(args.repo, args.out)
