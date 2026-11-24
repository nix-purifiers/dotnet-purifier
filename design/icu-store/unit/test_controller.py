# SPDX-License-Identifier: MIT
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


root = Path(__file__).parents[1]
controller = load('controller', root / 'controller.py')
probe = load('probe', root / 'probe.py')


class Controller(unittest.TestCase):
    def call(self, source, **limits):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            return controller.execute([sys.executable, '-c', source], shutil.which('ps'),
                                      Path(temp) / 'out', probe.run,
                                      deadline=limits.pop('deadline', 3), interval=0.01,
                                      scratch_parent=temp, **limits)

    def test_process_rows_exclude_other_groups_and_convert_kib(self):
        rows = controller.ps_rows('1 0 1 500\n21 20 20 42\n20 1 20 10\n', 20)
        self.assertEqual([x['pid'] for x in rows], [21, 20])
        self.assertEqual(sum(x['rssBytes'] for x in rows), 52 * 1024)
        with self.assertRaises(AssertionError):
            controller.ps_rows('not a numeric snapshot', 20)

    def test_real_local_ps_and_successful_child(self):
        result = self.call('import time;print("controlled");time.sleep(.1)')
        self.assertTrue(result['success'], result)
        self.assertIn('controlled', result['output'])
        self.assertGreaterEqual(result['maxObservedProcesses'], 1)

    def test_output_limit_is_not_success_even_if_child_exits_zero(self):
        result = self.call('print("x"*10000)', output_limit=100)
        self.assertEqual(result['stopReason'], 'output-limit')
        self.assertFalse(result['success'])
        self.assertEqual(len(result['output']), 100)

    def test_scratch_growth_stops_child(self):
        result = self.call('import os,pathlib,time;'
                           '(pathlib.Path(os.environ["TMPDIR"])/"growth").write_bytes(b"x"*10000);'
                           'time.sleep(5)', scratch_limit=1024)
        self.assertEqual(result['stopReason'], 'scratch-limit')
        self.assertGreater(result['maxObservedScratchBytes'], 1024)

    def test_rss_and_process_limits_stop_before_next_case(self):
        for limit, reason in (({'rss_limit': 1}, 'rss-limit'), ({'process_limit': 0}, 'process-limit')):
            with self.subTest(reason=reason):
                result = self.call('import time;time.sleep(5)', **limit)
                self.assertEqual(result['stopReason'], reason, result)
                self.assertFalse(result['success'])

    def test_monitor_failure_is_fail_stop(self):
        def unavailable(*args):
            raise RuntimeError('sampler denied')
        result = self.call('import time;time.sleep(5)', sampler=unavailable)
        self.assertEqual(result['stopReason'], 'monitor-error')
        self.assertIn('sampler denied', result['monitorError'])

    def test_deadline_kills_owned_descendant_and_preserves_unrelated_process(self):
        sentinel = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(10)'])
        try:
            result = self.call('import subprocess,sys,time;'
                               'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(10)"]);'
                               'print(p.pid,flush=True);time.sleep(10)', deadline=0.3)
            self.assertEqual(result['stopReason'], 'deadline')
            self.assertIsNone(sentinel.poll())
            descendant = int(result['output'].strip())
            # This retained test runs on Linux; a zombie is terminated, not live.
            status = Path('/proc') / str(descendant) / 'stat'
            for _ in range(20):
                if not status.exists() or status.read_text().split(') ', 1)[1].startswith('Z '):
                    break
                time.sleep(0.01)
            else:
                self.fail('owned descendant survived group cleanup')
        finally:
            sentinel.kill()
            sentinel.wait()

    def test_scratch_links_do_not_traverse_immutable_providers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'provider').write_bytes(b'x' * 100000)
            (root / 'scratch').mkdir()
            (root / 'scratch' / 'link').symlink_to(root / 'provider')
            self.assertLess(controller.scratch_size([root / 'scratch']), 1024)


if __name__ == '__main__':
    unittest.main()
