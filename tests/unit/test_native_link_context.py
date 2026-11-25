# SPDX-License-Identifier: MIT
"""Do not turn a lower link target into an unsupported runtime claim."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'link_context', os.environ['DOTNET_PURIFIER_NATIVE_LINK_CONTEXT'])
link = importlib.util.module_from_spec(spec)
spec.loader.exec_module(link)


class NativeDeployment(unittest.TestCase):
    def test_strip_provider_rejects_missing_or_unexpected_selection(self):
        with patch.object(link.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(AssertionError, 'strip not selected'):
                link.strip_provider('/nix/store/expected/bin/strip', '1010.6')
        with patch.object(link.shutil, 'which', return_value='/unexpected/strip'), \
             patch.object(link.Path, 'resolve', side_effect=[Path('/unexpected/strip'), Path('/expected/strip')]):
            with self.assertRaisesRegex(AssertionError, 'unexpected strip provider'):
                link.strip_provider('/expected/strip', '1010.6')

    def test_strip_provider_hashes_actual_selected_bytes(self):
        expected = Path('/nix/store/provider/bin/strip')
        with patch.object(link.shutil, 'which', return_value='/nix/store/selector/bin/strip'), \
             patch.object(link.Path, 'resolve', return_value=expected), \
             patch.object(link.Path, 'open', return_value=io.BytesIO(b'provider bytes')):
            result = link.strip_provider(expected, '1010.6')
        self.assertEqual(result['resolved'], str(expected))
        self.assertEqual(result['sha256'], link.hashlib.sha256(b'provider bytes').hexdigest())
        self.assertEqual(result['declaredPackageVersion'], '1010.6')

    def context(self):
        return {'nixMinimumMacOS': '14.0', 'dependencies': [
            {'path': '/nix/store/icu/lib/libicucore.A.dylib', 'minimumMacOS': '14.0.0'}]}

    def test_lower_target_is_recorded_without_claiming_lower_runtime_support(self):
        result = link.assess(self.context(), 'clang Answer.o --target=arm64-apple-macos12.0')
        self.assertEqual(result['linkTargetMacOS'], '12.0')
        self.assertEqual(link.version(result['effectiveMinimumMacOS']), (14, 0, 0))
        self.assertFalse(result['linkTargetCoversDependencies'])
        self.assertFalse(result['lowerTargetRuntimeCoverageClaimed'])
        observed = link.runtime_assessment(result, '15.7.2')
        self.assertEqual(observed['executedHostMacOS'], '15.7.2')
        self.assertEqual(observed['runtimeScope'], 'actual executed host only')

    def test_host_below_dependency_minimum_is_rejected_despite_target12(self):
        result = link.assess(self.context(), '--target=arm64-apple-macos12.0')
        with self.assertRaisesRegex(AssertionError, 'runtime host below'):
            link.runtime_assessment(result, '13.6')

    def test_dependency_cannot_silently_raise_nix_platform_minimum(self):
        context = self.context()
        context['nixMinimumMacOS'] = '13.0'
        with self.assertRaisesRegex(AssertionError, 'dependencies exceed'):
            link.assess(context, '--target=arm64-apple-macos12.0')

    def test_explicit_higher_project_target_remains_a_runtime_requirement(self):
        result = link.assess(self.context(), '--target=arm64-apple-macos15.0')
        self.assertEqual(result['effectiveMinimumMacOS'], '15.0')
        with self.assertRaisesRegex(AssertionError, 'runtime host below'):
            link.runtime_assessment(result, '14.7')
        self.assertEqual(link.runtime_assessment(result, '15.7')['executedHostMacOS'], '15.7')

    def test_ambiguous_or_missing_actual_target_is_rejected(self):
        for log in ('clang Answer.o', '--target=arm64-apple-macos12.0 --target=arm64-apple-macos14.0'):
            with self.subTest(log=log), self.assertRaisesRegex(AssertionError, 'ambiguous or absent'):
                link.assess(self.context(), log)

    def test_reads_real_macho_load_command_layout_as_data(self):
        command = struct.pack('<IIIIII', 50, 24, 1, 14 << 16, (14 << 16) | (4 << 8), 0)
        header = struct.pack('<IiiIIIII', 0xfeedfacf, 0x100000c, 0, 6, 1, len(command), 0, 0)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'metadata.dylib'
            path.write_bytes(header + command)
            self.assertEqual(link.macho(path)['minimumMacOS'], '14.0.0')
            path.write_bytes(b'bad header')
            with self.assertRaisesRegex(AssertionError, 'Mach-O64'):
                link.macho(path)

    def test_context_survives_failed_capture_and_failure_output(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, SDKROOT='/host/sdk'), \
             patch.object(link, 'dependency_metadata', return_value=self.context()['dependencies']):
            with self.assertRaisesRegex(AssertionError, 'non-store SDKROOT'):
                link.capture(temp, '/nix/store/icu/lib/libicucore.dylib', '14.0', '/nix/store/stubs', '/nix/store/sdk')
            saved = json.loads((Path(temp) / 'link-environment.json').read_text())
            self.assertEqual(saved['SDKROOT'], '/host/sdk')
            (Path(temp) / 'dotnet-info.txt').write_text('SDK test-only\nRuntime test-only\n')
            log = Path(temp) / 'failed-link.log'
            log.write_text('clang Answer.o --target=arm64-apple-macos12.0 -licucore\n')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                link.failure(temp, log)
            lines = output.getvalue().splitlines()
            self.assertTrue(any(line.startswith('NATIVE_LINK_COMMAND ') for line in lines))
            self.assertTrue(lines[-1].startswith('NATIVE_LINK_CONTEXT '))
            self.assertEqual(json.loads(lines[-1].removeprefix('NATIVE_LINK_CONTEXT '))['SDKROOT'], '/host/sdk')
            self.assertEqual(json.loads(lines[-1].removeprefix('NATIVE_LINK_CONTEXT '))['dotnetInfo'],
                             'SDK test-only\nRuntime test-only\n')

    def test_long_sdk_info_does_not_displace_original_error_from_nix_tail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = ''.join('SDK retained line %d\n' % i for i in range(80))
            (root / 'dotnet-info.txt').write_text(info)
            (root / 'link-environment.json').write_text(json.dumps({'SDKROOT': '/nix/store/sdk'}))
            log = root / 'native-link.log'
            log.write_text('clang Answer.o --target=arm64-apple-macos12.0\n')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                link.failure(root, log)
            lines = ['ACTUAL_PRODUCER_ERROR retained sentinel'] + output.getvalue().splitlines()
            self.assertIn('ACTUAL_PRODUCER_ERROR retained sentinel', lines[-25:])
            context = json.loads(lines[-1].removeprefix('NATIVE_LINK_CONTEXT '))
            self.assertEqual(context['dotnetInfo'], info)

    def test_actual_library_provider_join_uses_produced_load_metadata(self):
        context = self.context()
        context['icuLibrary'] = '/nix/store/icu/lib/libicucore.dylib'
        actual = [
            {'path': '/nix/store/producer/lib/libanswer.dylib', 'minimumMacOS': '12.0',
             'loads': ['/nix/store/icu/lib/libicucore.A.dylib']},
            {'path': '/nix/store/icu/lib/libicucore.A.dylib', 'minimumMacOS': '14.0', 'loads': []},
        ]
        result = link.assess(context, '--target=arm64-apple-macos12.0', actual)
        self.assertTrue(result['actualProducedDependenciesInspected'])
        self.assertEqual(result['actualICUProviderPaths'], ['/nix/store/icu/lib/libicucore.A.dylib'])
        self.assertFalse(result['lowerTargetRuntimeCoverageClaimed'])

    def test_actual_library_cannot_fall_back_to_host_icu(self):
        context = self.context()
        context['icuLibrary'] = '/nix/store/icu/lib/libicucore.dylib'
        actual = [{'path': '/nix/store/producer/lib/libanswer.dylib', 'minimumMacOS': '12.0',
                   'loads': ['/usr/lib/libicucore.A.dylib']}]
        with self.assertRaisesRegex(AssertionError, 'host or unexpected ICU provider'):
            link.assess(context, '--target=arm64-apple-macos12.0', actual)
