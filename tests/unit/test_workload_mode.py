# SPDX-License-Identifier: MIT
"""Private SDK configuration must preserve explicit project selection."""
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('workload_mode',os.environ['DOTNET_PURIFIER_WORKLOAD_MODE_HELPER'])
mode=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mode)

class WorkloadSelection(unittest.TestCase):
    def test_explicit_workload_set_is_not_overridden(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'global.json').write_text('{ // project choice\n "sdk": {"workloadVersion":"custom-preview-version",},}')
            with patch.object(mode.subprocess,'run') as run:
                self.assertEqual(mode.configure('/caller/sdk/bin/dotnet',root),'project-workload-set')
                run.assert_not_called()

    def test_commented_trailing_comma_preserves_explicit_workload_set(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'global.json').write_text('{"sdk":{"workloadVersion":"custom,}//kept", // project selection\n},}')
            with patch.object(mode.subprocess,'run') as run:
                self.assertEqual(mode.configure('/caller/sdk/bin/dotnet',root),'project-workload-set')
                run.assert_not_called()

    def test_capability_selects_manifests_on_same_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(mode.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='--update-mode <manifests|workload-set>')) as run:
                self.assertEqual(mode.configure('/caller/arbitrary-sdk/bin/dotnet',Path(temp)),'bundled-manifests')
                self.assertEqual(run.call_args_list[0].args[0],['/caller/arbitrary-sdk/bin/dotnet','workload','config','--help'])
                self.assertEqual(run.call_args_list[1].args[0],['/caller/arbitrary-sdk/bin/dotnet','workload','config','--update-mode','manifests'])

    def test_sdk_without_workload_sets_keeps_native_default(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(mode.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='Unknown subcommand config')) as run:
                self.assertEqual(mode.configure('/caller/older-sdk/bin/dotnet',Path(temp)),'native-default')
                self.assertEqual(run.call_count,1)
