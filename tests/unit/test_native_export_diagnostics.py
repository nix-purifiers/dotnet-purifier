# SPDX-License-Identifier: MIT
"""Execute the verifier with synthetic headers and a stand-in nm, never native code."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch


VERIFY = Path(os.environ.get('DOTNET_PURIFIER_NATIVE_VERIFY',
              Path(__file__).resolve().parents[2] / 'pkgs/dotnet-native-products/verify.py'))


class NativeExportDiagnostics(unittest.TestCase):
    def test_detached_unit_directory_uses_explicit_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / 'test_diagnostics.py'
            shutil.copyfile(__file__, copied)
            result = subprocess.run([os.sys.executable, '-B', str(copied),
                'NativeExportDiagnostics.test_actual_verifier_still_accepts_one_owned_library', '-v'],
                env=dict(os.environ, DOTNET_PURIFIER_NATIVE_VERIFY=str(VERIFY)),
                cwd=tmp, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def execute(self, rows, nm_stdout='0000000000001234 T _native_answer\n', section_flags=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            published = root / 'publish'; published.mkdir()
            for name, kind in rows:
                data = struct.pack('<IIII', 0xfeedfacf, 0x0100000c, 0, kind) + bytes(16)
                if section_flags is not None:
                    data = struct.pack('<IIIIIIII', 0xfeedfacf, 0x0100000c, 0, kind, 1, 152, 0, 0)
                    data += struct.pack('<II16sQQQQiiII', 0x19, 152, b'__TEXT', 0x1000, 0x1000, 0, 0, 5, 5, 1, 0)
                    data += struct.pack('<16s16sQQIIIIIIII', b'__managedcode', b'__TEXT', 0x1234, 0x40, 0, 2, 0, 0, section_flags, 0, 0, 0)
                (published / name).write_bytes(data)
            assembly = root / 'Answer.dll'; assembly.write_bytes(b'stand-in managed compiler input')
            (root / 'Answer.ilc.rsp').write_text('stand-in response graph')
            spec = dict(assembly='Answer', exports=['native_answer'], system='aarch64-darwin', library='libanswer.dylib', name='answer')
            metadata = dict(assembly='Answer', assemblyVersion='1.0.0', sha256=hashlib.sha256(assembly.read_bytes()).hexdigest(),
                            exports=[dict(entryPoint='native_answer', callingConventions=[], returnType='Int32', parameters=[])])
            for name, value in [('spec.json', spec), ('metadata.json', metadata)]:
                (root / name).write_text(json.dumps(value))
            argv = ['verify.py', str(root/'spec.json'), str(published), str(root/'metadata.json'), str(root/'out'), str(assembly)]
            calls = []
            def nm(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, nm_stdout, '')
            output = io.StringIO(); error = None
            previous = Path.cwd()
            try:
                os.chdir(root)
                with patch('sys.argv', argv), patch('subprocess.run', side_effect=nm), patch('shutil.which', return_value=None), contextlib.redirect_stdout(output):
                    try: runpy.run_path(str(VERIFY), run_name='__main__')
                    except AssertionError as caught: error = str(caught)
            finally:
                os.chdir(previous)
            record = json.loads((root/'out/native-products.json').read_text()) if (root/'out/native-products.json').exists() else None
            diagnostics = [json.loads(line.split(' ', 1)[1]) for line in output.getvalue().splitlines() if line.startswith('NATIVE_EXPORT_DIAGNOSTIC ')]
            return error, calls, diagnostics, record

    def test_actual_verifier_still_accepts_one_owned_library(self):
        error, calls, diagnostics, record = self.execute([('Answer.dylib', 6), ('Answer.dSYM', 10)])
        self.assertIsNone(error)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ['nm', '-gU'])
        self.assertEqual(diagnostics, [])
        self.assertEqual(record['nativePublishedPath'], 'Answer.dylib')

    def test_missing_export_keeps_gate_and_reports_actual_nm_output(self):
        error, calls, diagnostics, record = self.execute([('Answer.dylib', 6)], '00001234 T _unrelated\n')
        self.assertIn('expected exactly one', error)
        self.assertIsNone(record)
        row = diagnostics[0]
        self.assertEqual(row['matchingLibraries'], 0)
        candidate = row['candidates'][0]
        self.assertEqual(candidate['nmArgv'], calls[0])
        self.assertEqual(candidate['nmStdout'], '00001234 T _unrelated\n')
        self.assertEqual(candidate['selectedExportsMissing'], ['native_answer'])

    def test_two_matching_libraries_still_fail_cardinality(self):
        error, _, diagnostics, record = self.execute([('first.dylib', 6), ('second.dylib', 6)])
        self.assertIn('expected exactly one', error)
        self.assertEqual(diagnostics[0]['matchingLibraries'], 2)
        self.assertEqual(len(diagnostics[0]['candidates']), 2)
        self.assertIsNone(record)

    def test_non_dylib_is_observed_without_relaxing_kind_gate(self):
        error, calls, diagnostics, record = self.execute([('Answer.bundle', 8)])
        self.assertIn('expected exactly one', error)
        self.assertEqual(calls, [])
        self.assertEqual(diagnostics[0]['candidates'][0]['kind'], 8)
        self.assertIsNone(record)

    def test_retained_nm_output_has_explicit_truncation(self):
        error, _, diagnostics, _ = self.execute([('Answer.dylib', 6)], 'x'*40000)
        self.assertIn('expected exactly one', error)
        row = diagnostics[0]['candidates'][0]
        self.assertEqual(len(row['nmStdout']), 32768)
        self.assertTrue(row['nmStdoutTruncated'])

    def test_section_observation_distinguishes_code_flags_without_accepting_s(self):
        for flags in (0x80000400, 0):
            with self.subTest(flags=flags):
                error, _, diagnostics, record = self.execute([('Answer.dylib', 6)],
                    '0000000000001234 S _native_answer\n', section_flags=flags)
                self.assertIn('expected exactly one', error)
                self.assertIsNone(record)
                section = diagnostics[0]['candidates'][0]['sections'][0]
                self.assertEqual(section, dict(name='__managedcode', segment='__TEXT',
                    address=0x1234, size=0x40, flags=flags, segmentInitialProtection=5))

    def test_section_metadata_bounds_are_diagnostic_only(self):
        # Run the actual helper definition without the verifier's CLI entry.
        import ast
        tree = ast.parse(VERIFY.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name=='section_diagnostics')
        scope = {'struct':struct}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(VERIFY), 'exec'), scope)
        read = scope['section_diagnostics']
        self.assertIn('inspectionError', read(bytes(4)))
        header = bytearray(32); struct.pack_into('<II', header, 16, 257, 0)
        self.assertIn('inspectionError', read(header))


if __name__ == '__main__':
    unittest.main()
