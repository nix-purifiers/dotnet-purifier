# SPDX-License-Identifier: MIT
"""Implicit Darwin temp paths must name the existing directory directly."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('tempdir_cli', os.environ['DOTNET_PURIFIER_CLI'])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class DarwinTempDirectory(unittest.TestCase):
    def test_implicit_symlink_path_is_canonical_without_creating_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root/'actual'
            target.mkdir()
            link = root/'alias'
            link.symlink_to(target, target_is_directory=True)
            with patch.dict(cli.os.environ, {}, clear=True), patch.object(cli.sys, 'platform', 'darwin'), patch.object(cli.tempfile, 'gettempdir', return_value=str(link)):
                cli.default_darwin_tempdir()
                self.assertEqual(cli.os.environ['TMPDIR'], str(target.resolve()))
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['actual', 'alias'])

    def test_explicit_path_and_empty_value_are_preserved(self):
        for value in ['/caller/chosen/symlink', '']:
            with self.subTest(value=value), patch.dict(cli.os.environ, {'TMPDIR': value}, clear=True), patch.object(cli.sys, 'platform', 'darwin'), patch.object(cli.tempfile, 'gettempdir') as default:
                cli.default_darwin_tempdir()
                self.assertEqual(cli.os.environ['TMPDIR'], value)
                default.assert_not_called()

    def test_linux_environment_is_unchanged(self):
        with patch.dict(cli.os.environ, {}, clear=True), patch.object(cli.sys, 'platform', 'linux'), patch.object(cli.tempfile, 'gettempdir') as default:
            cli.default_darwin_tempdir()
            self.assertNotIn('TMPDIR', cli.os.environ)
            default.assert_not_called()

    def test_main_applies_default_before_dispatch(self):
        with patch.dict(cli.os.environ, {}, clear=True), patch.object(cli.sys, 'platform', 'darwin'), patch.object(cli.sys, 'argv', ['dotnet-purifier', 'prefetch']), patch.object(cli.tempfile, 'gettempdir', return_value='/example/../canonical'), patch.object(cli, 'path_option', return_value=None), patch.object(cli, 'prefetch') as prefetch:
            prefetch.side_effect = lambda _: self.assertEqual(cli.os.environ['TMPDIR'], '/canonical')
            cli.main()
            prefetch.assert_called_once_with([])


if __name__ == '__main__':
    unittest.main()
