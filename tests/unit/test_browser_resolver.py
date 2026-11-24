# SPDX-License-Identifier: MIT
"""Exercise the actual consumer driver with an external Gradle stand-in."""
from contextlib import chdir
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

DRIVER = os.environ.get('DOTNET_PURIFIER_BROWSER_RESOLVER', str(Path(__file__).parents[1]/'browser/resolve.py'))


class BrowserResolver(unittest.TestCase):
    def test_isolated_gradle_home_preserves_real_resolver_contract(self):
        for inherited_home in [None, '/unavailable/caller gradle home']:
            with self.subTest(inherited_home=inherited_home), tempfile.TemporaryDirectory(prefix='gradle fixture ') as directory:
                root = Path(directory).resolve()
                repository = root/'repository'
                repository.mkdir()
                jar = repository/'fixture.jar'
                jar.write_bytes(b'original-webjar')
                (repository/'fixture.pom').write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"><groupId>org.example</groupId><artifactId>fixture</artifactId><version>1.0.0</version></project>')
                environment = {'GRADLE_OPTS': '-Dfixture.setting=retained'}
                if inherited_home is not None:
                    environment['GRADLE_USER_HOME'] = inherited_home
                def gradle(argv, **kwargs):
                    project = root/'gradle-consumer'
                    diagnostics = project/'cache-diagnostics.gradle'
                    self.assertEqual(argv, ['gradle','--offline','--no-daemon','--console=plain','--stacktrace','--init-script',str(diagnostics),'resolveAssets'])
                    self.assertIn('gradle.gradleUserHomeDir.absolutePath', diagnostics.read_text())
                    self.assertIn('configuredNativeDirectory', diagnostics.read_text())
                    self.assertNotIn('GRADLE_OPTS', diagnostics.read_text())
                    self.assertEqual(kwargs['cwd'], project)
                    self.assertTrue(kwargs['check'])
                    self.assertEqual(kwargs['timeout'], 180)
                    selected = Path(kwargs['env']['GRADLE_USER_HOME'])
                    self.assertEqual(selected, project/'gradle-user-home')
                    self.assertTrue(selected.is_dir())
                    self.assertEqual(kwargs['env']['GRADLE_OPTS'], environment['GRADLE_OPTS'])
                    config = json.loads((project/'config.json').read_text())
                    Path(config['report']).write_text(str(jar))
                argv = ['resolve.py',str(repository),str(root/'resolved'),str(root/'coordinate')]
                with patch.dict(os.environ, environment, clear=True), patch('sys.argv',argv), patch('subprocess.run',side_effect=gradle) as invoke, chdir(root):
                    runpy.run_path(DRIVER, run_name='__main__')
                    self.assertEqual(os.environ.get('GRADLE_USER_HOME'), inherited_home)
                invoke.assert_called_once()
                self.assertEqual((root/'resolved').read_text(), str(jar))
                self.assertEqual((root/'coordinate').read_text(), 'org.example:fixture:1.0.0\n')


if __name__ == '__main__':
    unittest.main()
