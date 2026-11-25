# SPDX-License-Identifier: MIT
"""Bounded native diagnostic; never modifies the packaged App or its runtime."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import resource
import selectors
import subprocess
import sys
import tempfile
import time


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(argv, cwd, env, timeout=15, limit=65536, separate_stderr=False):
    child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE if separate_stderr else subprocess.STDOUT)
    output = bytearray()
    errors = bytearray()
    reason = None
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ, 'stdout')
        if separate_stderr:
            selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
        while selector.get_map():
            if time.monotonic() >= deadline:
                reason = 'timeout'
                break
            for key, _ in selector.select(0.05):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                remaining = limit - len(output) - len(errors)
                target = errors if key.data == 'stderr' else output
                target.extend(data[:remaining])
                if len(data) > remaining:
                    reason = 'output-limit'
                    break
            if reason:
                break
    if reason:
        child.kill()
    try:
        code = child.wait(timeout=max(0.1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        reason = 'timeout'
        child.kill()
        code = child.wait()
    child.stdout.close()
    if separate_stderr:
        child.stderr.close()
    row = dict(argv=argv, cwd=str(cwd), exitCode=code, stopReason=reason,
               output=output.decode('utf-8', errors='replace'))
    if separate_stderr:
        row['stderr'] = errors.decode('utf-8', errors='replace')
    return row


def classify(row):
    row['expectedAppResult'] = False
    if row['exitCode'] == 0 and row['stopReason'] is None:
        try:
            row['parsedOutput'] = json.loads(row['output'])
        except json.JSONDecodeError as error:
            row['parseError'] = str(error)
        else:
            row['expectedAppResult'] = row['parsedOutput'] == {'runtime': '10.0.15', 'value': 'locked'}
    return row


def inventory(directory):
    files = {}
    size = 0
    entries = 0
    for path in Path(directory).rglob('*'):
        entries += 1
        assert entries <= 2048, 'App entry count bound'
        assert not path.is_symlink(), ('unexpected input symlink', str(path))
        if path.is_dir():
            continue
        assert path.is_file(), ('unexpected file kind', str(path))
        size += path.stat().st_size
        assert len(files) < 1024 and size <= 16 * 1024**2, 'App inventory bound'
        data = path.read_bytes()
        files[str(path.relative_to(directory))] = dict(
            size=len(data), sha256=hashlib.sha256(data).hexdigest())
    assert {'App.dll', 'App.runtimeconfig.json', 'App.deps.json'} <= files.keys()
    return dict(sorted(files.items()))


def clone(source, target, expected):
    target.mkdir()
    for name, item in expected.items():
        data = (source / name).read_bytes()
        assert len(data) == item['size'] and hashlib.sha256(data).hexdigest() == item['sha256']
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    assert inventory(target) == expected


def probe(args):
    assert sys.platform == 'darwin', 'native Darwin required'
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    started = time.monotonic()
    args.out.mkdir()
    receipt = dict(scope='Exact App/runtime fixture only; no culture/ABI/universal support claim',
                   hostMacOS=platform.mac_ver()[0], cases=[], complete=False)
    output = args.out / 'probe.json'

    def save():
        output.write_text(json.dumps(receipt, indent=2) + '\n')

    save()
    try:
        identity = load('identity', args.identity_helper)
        link = load('link', args.link_helper)
        receipt['runtimeIdentity'] = identity.collect(args.dotnet)
        runtime_identity = receipt['runtimeIdentity']
        assert 'error' not in runtime_identity['executable']
        assert runtime_identity['runtimes'].get('exitCode') == 0 and runtime_identity['runtimes'].get('stopReason') is None
        candidates = [row for row in runtime_identity['coreLibCandidates'] if row['version'] == '10.0.15']
        assert len(candidates) == 1 and all('error' not in candidates[0][key] for key in ('coreLib', 'buildVersion')), 'runtime10.0.15 identity unavailable'
        receipt['candidateIcuDependencies'] = link.dependency_metadata(args.icu / 'libicucore.dylib')
        expected = inventory(args.app)
        receipt['appFiles'] = expected
        config = json.loads((args.app / 'App.runtimeconfig.json').read_text())
        receipt['appRuntimeConfig'] = config
        receipt['inheritedGlobalizationEnvironment'] = {key: os.environ.get(key) for key in (
            'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT', 'DOTNET_SYSTEM_GLOBALIZATION_APPLOCALICU',
            'DOTNET_ROOT', 'DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH')}
        invariant = config.get('runtimeOptions', {}).get('configProperties', {}).get('System.Globalization.Invariant')
        assert invariant in (None, False), 'invariant project configuration would mask ICU initialization'
        assert os.environ.get('DOTNET_SYSTEM_GLOBALIZATION_INVARIANT', '').lower() in ('', '0', 'false'), 'invariant environment would mask ICU initialization'
        # This fixture must not already select an app-local provider.
        assert not config.get('runtimeOptions', {}).get('configProperties', {}).get('System.Globalization.AppLocalIcu')
        libraries = [args.icu / ('lib' + name + '.78.1.dylib')
                     for name in ('icuuc', 'icui18n', 'icudata')]
        assert all(path.is_file() for path in libraries), 'exact ICU library version unavailable'
        receipt['candidateIcuLibraries'] = [identity.file_identity(path, 64 * 1024**2) for path in libraries]
        assert all('error' not in row for row in receipt['candidateIcuLibraries'])
        save()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for libs, app_local in ((False, None), (True, None), (False, '78.1'), (True, '78.1')):
                assert time.monotonic() - started < 120, 'overall diagnostic deadline'
                directory = root / ('libraries-' + str(libs) + '-local-' + str(app_local))
                clone(args.app, directory, expected)
                links = {}
                if libs:
                    for library in libraries:
                        destination = directory / library.name
                        assert not destination.exists()
                        destination.symlink_to(library)
                        links[destination.name] = str(library)
                env = dict(os.environ)
                env.pop('DOTNET_SYSTEM_GLOBALIZATION_APPLOCALICU', None)
                if app_local is not None:
                    env['DOTNET_SYSTEM_GLOBALIZATION_APPLOCALICU'] = app_local
                row = dict(libraries=libs, appLocal=app_local, symlinks=links,
                           selectedEnvironment={'DOTNET_SYSTEM_GLOBALIZATION_APPLOCALICU': app_local})
                row.update(run([args.dotnet, str(directory / 'App.dll')], directory, env,
                               timeout=min(15, 120 - (time.monotonic() - started))))
                receipt['cases'].append(row)
                classify(row)
                save()
                # Logs survive a failure elsewhere in flake-check; JSON is retained
                # in the successful diagnostic output even for negative cases.
                print('ICU_STORE_CASE ' + json.dumps(row), flush=True)
        receipt['complete'] = True
        receipt['storeCaseExpectedAppResult'] = receipt['cases'][-1]['expectedAppResult']
        # This is observational: the original required App test remains unchanged.
    finally:
        save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('app', 'icu', 'out', 'identity-helper', 'link-helper'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--dotnet', required=True)
    probe(parser.parse_args())
