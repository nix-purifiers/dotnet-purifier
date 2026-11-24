# SPDX-License-Identifier: MIT
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('runtime_identity', os.environ.get('DOTNET_PURIFIER_RUNTIME_IDENTITY_HELPER', str(Path(__file__).parents[1]/'runtime-identity.py')))
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


class RuntimeIdentity(unittest.TestCase):
    def test_command_retains_failure_and_bounded_output(self):
        result = identity.command([sys.executable, '-c', 'print("failure"); raise SystemExit(7)'])
        self.assertEqual(result['exitCode'], 7)
        self.assertIn('failure', result['output'])
        self.assertIsNone(result['stopReason'])
        result = identity.command([sys.executable, '-c', 'print("x"*10000)'], limit=64)
        self.assertEqual(len(result['output']), 64)
        self.assertEqual(result['stopReason'], 'output-limit')

    def test_hanging_command_is_stopped(self):
        result = identity.command([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=0.1)
        self.assertEqual(result['stopReason'], 'timeout')
        self.assertNotEqual(result['exitCode'], 0)

    def test_exact_candidate_bytes_and_commit_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root/'shared'/'11.0.0'
            runtime.mkdir(parents=True)
            (runtime/'System.Private.CoreLib.dll').write_bytes(b'candidate-only')
            (runtime/'.version').write_text('build-commit\n11.0.0\n')
            executable = root/'dotnet'
            executable.write_text('selected-executable')
            def run(argv):
                return {'argv': argv, 'exitCode': 0, 'stopReason': None,
                        'output': f'Microsoft.NETCore.App 11.0.0 [{runtime.parent}]\n' if argv[-1] == '--list-runtimes' else 'Host commit: host-commit'}
            with patch.object(identity, 'command', side_effect=run) as command:
                receipt = identity.collect(str(executable))
            self.assertEqual([call.args[0][0] for call in command.call_args_list], [str(executable)]*2)
            candidate = receipt['coreLibCandidates'][0]
            self.assertEqual(candidate['coreLib']['sha256'], hashlib.sha256(b'candidate-only').hexdigest())
            self.assertIn('build-commit', candidate['buildVersion']['text'])
            self.assertIn('unproven', receipt['scope'])

    def test_failed_listing_cannot_become_candidate_proof(self):
        with patch.object(identity, 'command', return_value={'exitCode': 1, 'stopReason': None, 'output': 'Microsoft.NETCore.App 11.0.0 [/unexpected]'}):
            receipt = identity.collect('/nonexistent/dotnet')
        self.assertEqual(receipt['coreLibCandidates'], [])
        self.assertIn('error', receipt['executable'])

    def test_oversized_file_is_not_hashed_as_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.version'
            path.write_bytes(b'abcde')
            receipt = identity.file_identity(path, 4)
        self.assertEqual(receipt['error'], 'size-limit')
        self.assertNotIn('sha256', receipt)


if __name__ == '__main__':
    unittest.main()
