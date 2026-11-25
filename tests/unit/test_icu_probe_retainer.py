# SPDX-License-Identifier: MIT
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('retainer', os.environ['DOTNET_PURIFIER_ICU_RETAINER'])
retainer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retainer)


class Retainer(unittest.TestCase):
    def fixture(self, root, files):
        store = root / 'store'
        store.mkdir()
        output = store / 'diagnostic'
        if files is not None:
            output.mkdir()
            for name, data in files.items():
                (output / name).write_bytes(data)
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            if argv[0] == 'git':
                text = 'commit\ntree\n'
            elif 'builtins.currentSystem' in argv:
                text = 'aarch64-darwin'
            elif '--apply' in argv:
                text = json.dumps({'out': str(output), 'drv': str(store / 'diagnostic.drv')})
            else:
                text = json.dumps({str(output): {'narHash': 'retained-test-metadata'}})
            return SimpleNamespace(returncode=0, stdout=text, stderr='')
        return store, output, calls, runner

    def test_missing_output_is_recorded_without_build_or_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store, output, calls, runner = self.fixture(root, None)
            result = retainer.retain(root, root / 'receipt', runner, store)
            self.assertEqual(result['status'], 'not-realized-no-build-attempted')
            self.assertFalse(result['productFixClaimed'])
            self.assertFalse(any('build' in argv or '--rebuild' in argv for argv in calls))

    def test_retains_exact_file_bytes_and_local_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = {'probe.json': b'{"complete":true}\n', 'controller.json': b'{"success":true}\n'}
            store, output, calls, runner = self.fixture(root, files)
            result = retainer.retain(root, root / 'receipt', runner, store)
            self.assertEqual(result['status'], 'retained-observation-only')
            for name, data in files.items():
                self.assertEqual((root / 'receipt' / name).read_bytes(), data)
                self.assertTrue(result['files'][name]['copied'])
            self.assertIn(str(output), result['outputMetadata'])
            self.assertFalse(result['productFixClaimed'])

    def test_partial_invalid_json_is_preserved_as_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store, output, calls, runner = self.fixture(root, {'probe.json': b'{partial'})
            result = retainer.retain(root, root / 'receipt', runner, store)
            self.assertEqual((root / 'receipt' / 'probe.json').read_bytes(), b'{partial')
            self.assertIn('parseError', result['files']['probe.json'])
            self.assertIn('error', result['files']['controller.json'])

    def test_metadata_failure_always_leaves_a_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def denied(argv, **kwargs):
                return SimpleNamespace(returncode=1, stdout='', stderr='unavailable')
            result = retainer.retain(root, root / 'receipt', denied, root / 'store')
            self.assertEqual(result['status'], 'retention-error')
            self.assertTrue((root / 'receipt' / 'retention.json').is_file())
            self.assertEqual(result['commands'][0]['stderr'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
