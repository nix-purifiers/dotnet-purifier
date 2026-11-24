# SPDX-License-Identifier: MIT
"""Cross-OS artifact differences must not hide unrelated published files."""
import importlib.util
import os
import copy
import json
from pathlib import Path
import runpy
import tempfile
from unittest.mock import patch
import unittest

spec = importlib.util.spec_from_file_location(
    'artifact_profile', os.environ['DOTNET_PURIFIER_NATIVE_ARTIFACT_HELPER'])
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)
cache_spec = importlib.util.spec_from_file_location(
    'cache_prerequisites', os.environ['DOTNET_PURIFIER_NATIVE_CACHE_HELPER'])
cache = importlib.util.module_from_spec(cache_spec)
cache_spec.loader.exec_module(cache)



class NativeArtifactProfile(unittest.TestCase):
    def darwin(self):
        root = 'Answer.dylib.dSYM/Contents/'
        return ['Answer.dylib', root + 'Info.plist',
                root + 'Resources/DWARF/Answer.dylib']

    def test_platform_library_and_symbols(self):
        for rid, artifacts in [
            ('linux-x64', ['Answer.so', 'Answer.so.dbg']),
            ('osx-arm64', self.darwin()),
            ('osx-x64', self.darwin()),
            ('win-x64', ['Answer.dll', 'Answer.pdb']),
        ]:
            with self.subTest(rid=rid):
                profile.validate_artifacts('Answer', rid, artifacts)

    def test_symbols_can_be_disabled(self):
        for rid, library in [('linux-x64', 'Answer.so'),
                             ('osx-arm64', 'Answer.dylib'), ('win-x64', 'Answer.dll')]:
            profile.validate_artifacts('Answer', rid, [library])

    def test_llvm_optional_bundle_outputs(self):
        root = 'Answer.dylib.dSYM/Contents/Resources/'
        for rid, arch in [('osx-arm64', 'aarch64'), ('osx-x64', 'x86_64')]:
            profile.validate_artifacts('Answer', rid, self.darwin() + [
                root + 'Relocations/' + arch + '/Answer.dylib.yml',
                root + 'Remarks/Answer.dylib'])

    def test_unrelated_artifact_is_rejected_even_with_library(self):
        for extra in ['unrelated-extra.bin', '../Answer.dylib', '/Answer.dylib',
                      'Answer.so.dbg', 'Answer.dylib.dSYM/Contents/unrelated.bin',
                      'Answer.dylib.dSYM/Contents/Resources/DWARF/Other.dylib',
                      'Answer.dylib.dSYM/Contents/Resources/Relocations/x86_64/Answer.dylib.yml']:
            with self.subTest(extra=extra), self.assertRaises(AssertionError):
                profile.validate_artifacts('Answer', 'osx-arm64', self.darwin() + [extra])

    def test_missing_library_duplicate_and_partial_bundle_rejected(self):
        for artifacts in [self.darwin()[1:], self.darwin() + ['Answer.dylib'],
                          self.darwin()[:-1], ['Answer.dylib', 'Answer.dylib.dSYM']]:
            with self.subTest(artifacts=artifacts), self.assertRaises(AssertionError):
                profile.validate_artifacts('Answer', 'osx-arm64', artifacts)

    def test_linux_and_windows_reject_foreign_or_unrelated_symbols(self):
        for rid, artifacts in [('linux-x64', ['Answer.so', 'Answer.pdb']),
                               ('win-x64', ['Answer.dll', 'Other.pdb'])]:
            with self.subTest(rid=rid), self.assertRaises(AssertionError):
                profile.validate_artifacts('Answer', rid, artifacts)


class NativeRelockIntegration(unittest.TestCase):
    """Exercise the real comparison script with external commands simulated."""

    def run_profile(self, bad_inventory=None, darwin_receipts=False):
        old = {'sdk': {'version': 'test-sdk'}, 'contentHash': 'old', 'projects': [{
            'name': 'native', 'assemblyName': 'Answer', 'path': 'Answer.csproj',
            'nativePublish': {'runtimeIdentifierSource': 'sdk-host',
                              'runtimeIdentifier': 'linux-x64',
                              'artifacts': ['Answer.so', 'Answer.so.dbg']}}]}
        first = copy.deepcopy(old)
        first['contentHash'] = 'new'
        first['projects'][0]['nativePublish'].update(
            runtimeIdentifier='osx-arm64', artifacts=NativeArtifactProfile().darwin())
        if bad_inventory:
            target = old if bad_inventory == 'committed' else first
            target['projects'][0]['nativePublish']['artifacts'].append('unrelated-extra.bin')
        old_bytes = json.dumps(old).encode()
        first_bytes = json.dumps(first).encode()
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            fixture = repo / 'tests/fixtures/native42'
            fixture.mkdir(parents=True)
            capture = fixture / 'dotnet-purifier.lock.json'
            capture.write_bytes(first_bytes)
            out = repo / 'out'
            replay = repo / 'native-replay'
            replay.mkdir()
            producer = repo / 'producer'
            native_proof = producer / 'native-proof'
            native_proof.mkdir(parents=True)
            (replay / 'proof.json').write_text(json.dumps([{'producer': str(producer)}]))
            for name, contents in [('link-environment.json', '{"SDKROOT":"/nix/store/test-sdk"}'),
                                   ('native-link.log', 'simulated clang command'),
                                   ('dotnet-info.txt', 'simulated SDK runtime info')]:
                (native_proof / name).write_text(contents)


            def check_output(command, **kwargs):
                if command[0] == 'git':
                    return old_bytes
                if command[:2] == ['nix', 'eval']:
                    if command[-1] == 'builtins.currentSystem':
                        return 'aarch64-darwin'
                    return json.dumps({'problems': [], 'outputs': ['native']})
                if 'msbuild' in command:
                    return json.dumps({'Properties': {'NETCoreSdkVersion': 'test-sdk',
                        'NETCoreSdkPortableRuntimeIdentifier': 'osx-arm64'}})
                if command[:2] == ['nix', 'build']:
                    return str(replay) + '\n'
                raise AssertionError(('unexpected external command', command))

            def run(command, **kwargs):
                if 'lock' in command:
                    Path(command[-1]).write_bytes(first_bytes)
                else:
                    assert command[-1] == 'prefetch', command

            argv = ['relock-profile.py', '--repo', str(repo), '--fixture', 'native42',
                    '--capture', str(capture), '--out', str(out)]
            with patch('sys.argv', argv), patch.dict('sys.modules', artifact_profile=profile, cache_prerequisites=cache), \
                 patch('subprocess.check_output', side_effect=check_output), \
                 patch('subprocess.run', side_effect=run):
                with patch('platform.system', return_value='Darwin' if darwin_receipts else 'Linux'):
                    runpy.run_path(os.environ['DOTNET_PURIFIER_NATIVE_RELOCK_PROFILE'], run_name='__main__')
            if darwin_receipts:
                self.assertTrue((out / 'native-consumer-proof-0.json').is_file())
                for name in ('link-environment.json', 'dotnet-info.txt', 'native-link.log'):
                    self.assertEqual((out / 'native-link-receipts/producer' / name).read_bytes(),
                                     (native_proof / name).read_bytes())
            return json.loads((out / 'verification.json').read_text())

    def test_cross_os_profile_reaches_repeat_reader_and_replay(self):
        result = self.run_profile()
        self.assertTrue(result['repeatedCaptureByteIdentical'])
        self.assertEqual(set(result['nativeNixReaders']), {'committed', 'first', 'second'})
        self.assertIn(['projects', 0, 'nativePublish', 'artifacts'], result['changedPaths'])
        self.assertTrue(result['capturedLockNativeReplay']['outputs'][0].endswith('/native-replay'))

    def test_extra_artifact_in_either_inventory_stops_comparison(self):
        for inventory in ('committed', 'captured'):
            with self.subTest(inventory=inventory), self.assertRaisesRegex(
                    AssertionError, 'unexpected native artifacts'):
                self.run_profile(inventory)

    def test_failed_cache_guard_stops_relock_before_native_replay(self):
        with patch.object(cache, 'ensure', side_effect=RuntimeError('cache prerequisites unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'cache prerequisites unavailable'):
                self.run_profile()

    def test_darwin_replay_retains_link_sdk_and_consumer_receipts(self):
        result = self.run_profile(darwin_receipts=True)
        self.assertEqual(result['nativeLinkReceiptDirectories'], ['native-link-receipts/producer'])
