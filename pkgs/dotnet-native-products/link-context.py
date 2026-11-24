# SPDX-License-Identifier: MIT
"""Retain Darwin linker inputs and assess, without changing, deployment targets."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct


def version(text):
    values = tuple(int(x) for x in text.split('.'))
    return values + (0,) * (3 - len(values))


def packed_version(value):
    return '.'.join(map(str, (value >> 16, (value >> 8) & 255, value & 255)))


def macho(path):
    with Path(path).open('rb') as file:
        header = file.read(65536)
    assert header[:4] == b'\xcf\xfa\xed\xfe', ('not a little-endian Mach-O64', str(path))
    cpu, subtype, kind, count, size = struct.unpack_from('<iiIII', header, 4)
    assert kind == 6, ('not a dylib', str(path), kind)
    assert cpu in (0x100000c, 0x1000007), ('expected Darwin64 architecture', str(path), cpu)
    assert 32 + size <= len(header), ('load commands exceed header bound', str(path))
    minimum = None
    loads = []
    offset = 32
    for _ in range(count):
        command, length = struct.unpack_from('<II', header, offset)
        assert length >= 8 and offset + length <= 32 + size
        data = header[offset:offset + length]
        code = command & 0x7fffffff
        if code in (12, 24, 31, 35):
            name_offset = struct.unpack_from('<I', data, 8)[0]
            assert 24 <= name_offset < length
            loads.append(data[name_offset:].split(b'\0', 1)[0].decode())
        elif code == 50:
            system, raw_minimum = struct.unpack_from('<II', data, 8)
            assert system == 1, ('not macOS', str(path), system)
            minimum = packed_version(raw_minimum)
        elif code == 36:
            minimum = packed_version(struct.unpack_from('<I', data, 8)[0])
        offset += length
    assert offset == 32 + size and minimum is not None, str(path)
    return {'minimumMacOS': minimum, 'loads': loads, 'cpuType': cpu}


def dependency_metadata(library):
    pending = [Path(library)]
    seen = set()
    rows = []
    while pending:
        path = pending.pop().resolve(strict=True)
        assert str(path).startswith('/nix/store/'), ('non-store dependency', str(path))
        if path in seen:
            continue
        seen.add(path)
        assert len(seen) <= 64, 'native dependency metadata exceeds bound'
        row = dict(path=str(path), **macho(path))
        rows.append(row)
        pending.extend(Path(x) for x in row['loads'] if x.startswith('/nix/store/'))
    return rows


def capture(directory, library, nix_minimum, core_stubs, overlay_sdk):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    keys = ('SDKROOT', 'DOTNET_ROOT', 'DEVELOPER_DIR', 'MACOSX_DEPLOYMENT_TARGET',
            'NIX_LDFLAGS', 'NIX_CFLAGS_COMPILE')
    context = {key: os.environ.get(key, '') for key in keys}
    context.update(clang=shutil.which('clang'), nixMinimumMacOS=nix_minimum,
                   swiftCoreStubs=str(core_stubs), swiftOverlaySdk=str(overlay_sdk),
                   icuLibrary=str(library), buildHostMacOS=platform.mac_ver()[0])
    path = directory / 'link-environment.json'
    # Persist the environment even if dependency metadata or SDK assertions fail.
    path.write_text(json.dumps(context) + '\n')
    context['dependencies'] = dependency_metadata(library)
    path.write_text(json.dumps(context) + '\n')
    assert context['SDKROOT'].startswith('/nix/store/'), ('non-store SDKROOT', context['SDKROOT'])
    return context


def assess(context, log, produced_dependencies=None):
    targets = sorted(set(re.findall(r'--target=(?:arm64|aarch64|x86_64)-apple-macos([0-9.]+)', log)), key=version)
    assert len(targets) == 1, ('ambiguous or absent actual link target', targets)
    dependencies = context['dependencies'] if produced_dependencies is None else produced_dependencies
    providers = []
    if produced_dependencies is not None:
        prefix = str(Path(context['icuLibrary']).parent) + '/'
        for row in produced_dependencies:
            if row['path'].startswith(prefix):
                providers.append(row['path'])
            for path in row['loads']:
                if Path(path).name.startswith('libicu'):
                    assert path.startswith(prefix), ('host or unexpected ICU provider', path, prefix)
    minimum = max((row['minimumMacOS'] for row in dependencies), key=version)
    declared = max((targets[0], context['nixMinimumMacOS']), key=version)
    effective = max((declared, minimum), key=version)
    # Do not alter Apple's target. A dependency may not silently exceed both
    # the Nix baseline and the project's explicit target. A higher explicit
    # project target remains valid and is included in the runtime requirement.
    assert version(declared) >= version(minimum), (
        'dependencies exceed declared platform/project minimum', declared, minimum)
    return {'linkTargetMacOS': targets[0], 'dependencyMinimumMacOS': minimum,
            'effectiveMinimumMacOS': effective, 'nixMinimumMacOS': context['nixMinimumMacOS'],
            'linkTargetCoversDependencies': version(targets[0]) >= version(minimum),
            'lowerTargetRuntimeCoverageClaimed': False,
            'actualProducedDependenciesInspected': produced_dependencies is not None,
            'actualICUProviderPaths': sorted(providers),
            'dependencyMetadata': dependencies}


def runtime_assessment(deployment, host):
    assert host and version(host) >= version(deployment['effectiveMinimumMacOS']), (
        'runtime host below dependency minimum', host, deployment['effectiveMinimumMacOS'])
    return dict(deployment, executedHostMacOS=host, runtimeScope='actual executed host only')


def failure(directory, log):
    path = Path(directory) / 'link-environment.json'
    context = json.loads(path.read_text()) if path.is_file() else {
        key: os.environ.get(key, '') for key in ('SDKROOT', 'DOTNET_ROOT', 'NIX_LDFLAGS', 'MACOSX_DEPLOYMENT_TARGET')}
    info = Path(directory) / 'dotnet-info.txt'
    if info.is_file():
        context['dotnetInfo'] = info.read_text()[:65536]
        print('NATIVE_LINK_DOTNET_INFO ' + context['dotnetInfo'])
    if Path(log).is_file():
        lines = []
        with Path(log).open(errors='replace') as file:
            for line in file:
                if 'clang' in line and ('--target=' in line or '.dylib' in line):
                    lines.append(line.rstrip())
                    lines = lines[-4:]
        for line in lines:
            print('NATIVE_LINK_COMMAND ' + line)
    # Last, compact, so Nix's short failure tail retains the actual context.
    print('NATIVE_LINK_CONTEXT ' + json.dumps(context, separators=(',', ':')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('capture', 'assess', 'failure'))
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--log', type=Path)
    parser.add_argument('--published', type=Path)
    parser.add_argument('--icu', type=Path)
    parser.add_argument('--nix-minimum')
    parser.add_argument('--core-stubs', type=Path)
    parser.add_argument('--overlay-sdk', type=Path)
    args = parser.parse_args()
    if args.mode == 'capture':
        capture(args.directory, args.icu, args.nix_minimum, args.core_stubs, args.overlay_sdk)
    elif args.mode == 'failure':
        failure(args.directory, args.log)
    else:
        context = json.loads((args.directory / 'link-environment.json').read_text())
        assert args.published is not None, 'published library required for assessment'
        result = assess(context, args.log.read_text(), dependency_metadata(args.published))
        (args.directory / 'deployment.json').write_text(json.dumps(result, indent=2) + '\n')
