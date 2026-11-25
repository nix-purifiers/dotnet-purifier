# SPDX-License-Identifier: MIT
"""A Darwin native test must fail before replay if cache inputs are missing."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'cache_prerequisites', os.environ['DOTNET_PURIFIER_NATIVE_CACHE_HELPER'])
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class NativeCachePrerequisites(unittest.TestCase):
    def test_non_darwin_does_not_realize_native_inputs(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cache.sys, 'platform', 'linux'), \
             patch.object(cache.subprocess, 'run') as run, \
             patch.object(cache.subprocess, 'check_output') as evaluate:
            self.assertEqual(cache.ensure(temp, temp)['status'], 'not-required')
            run.assert_not_called()
            evaluate.assert_not_called()

    def test_darwin_copies_exact_cache_outputs_without_any_build_request(self):
        paths = ['/nix/store/swift-stubs', '/nix/store/apple-sdk', '/nix/store/icu-dev']
        success = subprocess.CompletedProcess([], 0, '', '')
        with tempfile.TemporaryDirectory() as temp, patch.object(cache.sys, 'platform', 'darwin'), \
             patch.object(cache.subprocess, 'check_output', side_effect=[json.dumps(paths), json.dumps({'closure': paths})]), \
             patch.object(cache.subprocess, 'run', return_value=success) as run:
            receipt = cache.ensure(temp, temp)
            command = run.call_args.args[0]
            self.assertEqual(command, ['nix', 'copy', '--from', 'https://cache.nixos.org', *paths])
            self.assertNotIn('build', command)
            self.assertEqual(receipt['status'], 'cached')
            self.assertFalse(receipt['sourceBuildsAllowed'])

    def test_missing_cache_fails_and_is_retained_without_retry(self):
        paths = ['/nix/store/swift-stubs', '/nix/store/apple-sdk', '/nix/store/icu-dev']
        failure = subprocess.CompletedProcess([], 1, '', 'cannot build: no builders')
        with tempfile.TemporaryDirectory() as temp, patch.object(cache.sys, 'platform', 'darwin'), \
             patch.object(cache.subprocess, 'check_output', return_value=json.dumps(paths)), \
             patch.object(cache.subprocess, 'run', return_value=failure) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                cache.ensure(temp, temp)
            run.assert_called_once()
            receipt = json.loads((Path(temp) / 'cache-prerequisites.json').read_text())
            self.assertEqual(receipt['status'], 'unavailable')
            self.assertEqual(receipt['exitCode'], 1)

    def test_empty_or_duplicate_prerequisites_stop_before_copy(self):
        for paths in ([], ['/nix/store/one', '/nix/store/one']):
            with tempfile.TemporaryDirectory() as temp, patch.object(cache.sys, 'platform', 'darwin'), \
                 patch.object(cache.subprocess, 'check_output', return_value=json.dumps(paths)), \
                 patch.object(cache.subprocess, 'run') as run:
                with self.assertRaises(AssertionError):
                    cache.ensure(temp, temp)
                run.assert_not_called()
