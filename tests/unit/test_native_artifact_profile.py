# SPDX-License-Identifier: MIT
"""Cross-OS artifact differences must not hide unrelated published files."""
import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location(
    'artifact_profile', os.environ['DOTNET_PURIFIER_NATIVE_ARTIFACT_HELPER'])
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


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
