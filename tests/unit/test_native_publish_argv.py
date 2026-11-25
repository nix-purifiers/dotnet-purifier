# SPDX-License-Identifier: MIT
"""Check the actual generated publish command without running an SDK."""
import json
import os
from pathlib import Path
import subprocess
import unittest


class NativePublishArguments(unittest.TestCase):
    def test_generated_publish_keeps_output_and_logger_arguments_separate(self):
        source = json.loads(Path(os.environ['DOTNET_PURIFIER_NATIVE_PUBLISH_PHASE']).read_text())
        lines = source['phase'].splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith('dotnet publish '))
        command = lines[start]
        while command.endswith('\\'):
            start += 1
            command += '\n' + lines[start]
        # Exercise real shell quoting with a space in the build directory.
        env = dict(os.environ, TMPDIR='/tmp/native publish arguments', rid='osx-arm64')
        shell = 'dotnet() { printf "%s\\0" "$@"; }\n' + command
        raw = subprocess.check_output(['bash', '-c', shell], env=env)
        args = [arg.decode() for arg in raw.split(b'\0') if arg]
        self.assertEqual(args[args.index('-o') + 1], env['TMPDIR'] + '/native-publish')
        if source['darwin']:
            self.assertIn('-fl', args)
            self.assertIn('-flp:LogFile=' + env['TMPDIR'] + '/native-link.log;Verbosity=diagnostic', args)
        else:
            self.assertNotIn('-fl', args)
            self.assertFalse(any(arg.startswith('-flp:') for arg in args))
