#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Retain source, derivation, output and native rebuild evidence for tier review."""
import json
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent.parent
out = pathlib.Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=False)


def run(name, *command):
    with (out / name).open('w') as log, (out / (name + '.stderr')).open('w') as errors:
        result = subprocess.run(command, cwd=root, stdout=log, stderr=errors)
    if result.returncode:
        raise SystemExit(f'{command!r} failed; see {out / name}')
    return (out / name).read_text()


if run('status.txt', 'git', 'status', '--porcelain').strip():
    raise SystemExit('native receipts require a clean committed source tree')
revision = run('revision.txt', 'git', 'rev-parse', 'HEAD').strip()
subprocess.run(['git', 'archive', '--output', str(out / 'source.tar'), 'HEAD'],
               cwd=root, check=True)
metadata = json.loads(run('source.json', 'nix', 'flake', 'metadata', '--json', '.'))
system = run('system.txt', 'nix', 'eval', '--raw', '--impure',
             '--expr', 'builtins.currentSystem').strip()
run('sandbox.txt', 'nix', 'config', 'show', 'sandbox')
versions = json.loads(run('sdk-versions.json', 'nix', 'eval', '--json', '--impure',
    '--expr', '(let f = builtins.getFlake "path:' + str(root) + '"; '
    'p = import f.inputs.nixpkgs { system = "' + system + '"; }; '
    'in { sdk = p.dotnetCorePackages.sdk_10_0.version; '
    'runtime8 = p.dotnetCorePackages.runtime_8_0.version; '
    'runtime9 = p.dotnetCorePackages.runtime_9_0.version; '
    'runtime10 = p.dotnetCorePackages.runtime_10_0.version; '
    'sdk11 = (p.dotnetCorePackages.buildDotnetSdk ' + str(root / 'tests/toolchains/dotnet11.nix') + ').sdk_11_0.version; '
    'runtime11 = (p.dotnetCorePackages.buildDotnetSdk ' + str(root / 'tests/toolchains/dotnet11.nix') + ').runtime_11_0.version; })'))
checks = ['current-app-runs', 'current-app11-runs', 'unit-tests', 'multi-app-runs', 'multi-tests', 'multi-release-publishes',
          'sources-web-runs', 'workloads-wasm-relinked',
          'realworld-fluentvalidation', 'realworld-fluentvalidation-tests']
# Darwin's binary SDK declares narrow system-service sandbox profiles.
# Relaxed mode permits those profiles; it still sandboxes ordinary builds.
sandbox = 'relaxed' if sys.platform == 'darwin' else 'true'
receipts = {}
for check in checks:
    ref = f'.#checks.{system}.{check}'
    derivations = json.loads(run(check + '-derivation.json', 'nix', 'derivation', 'show', ref))
    derivations = derivations.get('derivations', derivations)
    if any(d.get('env', {}).get('__noChroot') == '1' for d in derivations.values()):
        raise SystemExit(f'{check} explicitly disables the sandbox')
    builds = json.loads(run(check + '-build.json', 'nix', 'build', '--no-link',
                           '--json', '-L', '--option', 'sandbox', sandbox, ref))
    # Force actual execution even when the initial build was substituted.
    run(check + '-rebuild.log', 'nix', 'build', '--no-link', '--rebuild', '-L',
        '--option', 'sandbox', sandbox, ref)
    paths = [path for build in builds for path in build['outputs'].values()]
    outputs = json.loads(run(check + '-outputs.json', 'nix', 'path-info', '--json', *paths))
    receipts[check] = {'builds': builds, 'outputs': outputs, 'rebuild': 'success'}
(out / 'receipt.json').write_text(json.dumps({
    'repository': 'nix-purifiers/dotnet-purifier', 'revision': revision,
    'source': metadata, 'system': system, 'sdkVersions': versions,
    'sandbox': sandbox, 'checks': receipts,
}, indent=2) + '\n')
