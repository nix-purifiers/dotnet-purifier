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

    def test_input_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'App.dll').symlink_to('/not-followed')
            with self.assertRaisesRegex(AssertionError, 'symlink'):
                probe.inventory(root)

if __name__ == '__main__':
    unittest.main()
