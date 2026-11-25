# SPDX-License-Identifier: MIT
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('probe', Path(__file__).parents[1] / 'probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

class BoundedProbe(unittest.TestCase):
    def test_child_cwd_env_and_combined_output(self):
        with tempfile.TemporaryDirectory() as root:
            row = probe.run([sys.executable, '-c',
                             'import os,sys;print(os.getcwd());print(os.environ["PROBE_CASE"]);print("err",file=sys.stderr)'],
                            Path(root), dict(os.environ, PROBE_CASE='selected'))
            self.assertEqual(row['exitCode'], 0)
            self.assertIsNone(row['stopReason'])
            self.assertIn(str(Path(root).resolve()), row['output'])
            self.assertIn('selected', row['output'])
            self.assertIn('err', row['output'])

    def test_child_output_cap_is_enforced(self):
        row = probe.run([sys.executable, '-c', 'print("x"*10000)'], Path.cwd(), os.environ, limit=100)
        self.assertEqual(row['stopReason'], 'output-limit')
        self.assertEqual(len(row['output']), 100)

    def test_separate_stderr_preserves_stdout_json(self):
        row = probe.run([sys.executable, '-c',
                         'import sys;print("{\\\"ok\\\":true}");print("ps warning",file=sys.stderr)'],
                        Path.cwd(), os.environ, separate_stderr=True)
        self.assertEqual(row['exitCode'], 0)
        self.assertEqual(row['output'].strip(), '{"ok":true}')
        self.assertEqual(row['stderr'].strip(), 'ps warning')

    def test_separate_streams_share_one_output_bound(self):
        row = probe.run([sys.executable, '-c',
                         'import os;os.write(1,b"a"*50);os.write(2,b"b"*10000)'],
                        Path.cwd(), os.environ, limit=100, separate_stderr=True)
        self.assertEqual(row['stopReason'], 'output-limit')
        self.assertEqual(len(row['output'])+len(row['stderr']), 100)

    def test_deadline_stops_owned_child(self):
        row = probe.run([sys.executable, '-c', 'import time;time.sleep(5)'], Path.cwd(), os.environ, timeout=0.1)
        self.assertEqual(row['stopReason'], 'timeout')
        self.assertNotEqual(row['exitCode'], 0)

    def test_clone_preserves_exact_bytes_and_rejects_source_drift(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'source'
            source.mkdir()
            for name in ('App.dll', 'App.runtimeconfig.json', 'App.deps.json'):
                (source / name).write_bytes(name.encode())
            inventory = probe.inventory(source)
            probe.clone(source, root / 'copy', inventory)
            self.assertEqual(probe.inventory(root / 'copy'), inventory)
            (source / 'App.dll').write_bytes(b'drift')
            with self.assertRaises(AssertionError):
                probe.clone(source, root / 'changed', inventory)

    def test_malformed_success_output_remains_retained_negative_evidence(self):
        row = {'exitCode': 0, 'stopReason': None, 'output': 'unexpected non-JSON output'}
        result = probe.classify(row)
        self.assertIs(result, row)
        self.assertFalse(result['expectedAppResult'])
        self.assertEqual(result['output'], 'unexpected non-JSON output')
        self.assertIn('parseError', result)

    def test_json_is_not_success_after_nonzero_exit_or_output_limit(self):
        text = '{"runtime":"10.0.15","value":"locked"}'
        for code, reason, expected in ((0, None, True), (1, None, False), (0, 'output-limit', False)):
            with self.subTest(code=code, reason=reason):
                result = probe.classify({'exitCode': code, 'stopReason': reason, 'output': text})
                self.assertEqual(result['expectedAppResult'], expected)

    def test_input_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'App.dll').symlink_to('/not-followed')
            with self.assertRaisesRegex(AssertionError, 'symlink'):
                probe.inventory(root)

if __name__ == '__main__':
    unittest.main()
