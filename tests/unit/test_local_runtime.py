# SPDX-License-Identifier: MIT
"""Selection and dependency boundary tests; real SDK proof is separate."""
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from test_cli import cli


class LocalRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = self.root / "ordinary-sdk"
        (self.host / "sdk").mkdir(parents=True)
        self.dotnet = self.host / "dotnet"
        # A protocol fixture, not evidence of .NET compatibility.
        responses = {"--version": "42.7.123-preview.4", "--list-sdks": f"42.7.123-preview.4 [{self.host}/sdk]",
                     "--list-runtimes": f"Microsoft.NETCore.App 42.7.0 [{self.host}/shared/Microsoft.NETCore.App]"}
        self.dotnet.write_text(f"#!{sys.executable}\nimport sys\nprint({responses!r}[sys.argv[1]])\n")
        self.dotnet.chmod(0o755)
        self.link = self.root / "bin" / "dotnet"
        self.link.parent.mkdir()
        self.link.symlink_to(self.dotnet)
        self.env = mock.patch.dict(os.environ, {
            "DOTNET_PURIFIER_DOTNET": str(self.link),
            "DOTNET_PURIFIER_NIX_LIB": os.environ.get("DOTNET_PURIFIER_NIX_LIB", str(Path(cli.__file__).resolve().parents[1] / "nix/lib")),
            "DOTNET_PURIFIER_NIXPKGS": "/unused-nixpkgs",
            "DOTNET_PURIFIER_SYSTEM": "x86_64-linux",
            "DOTNET_PURIFIER_FLAKE": "/unused-flake",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_host_selection_has_no_version_table_or_nix_fallback(self):
        with mock.patch.object(cli, "nix_build", side_effect=AssertionError("must not realize an SDK")):
            selected = cli.local_sdk(self.root)
        self.assertEqual(selected["dotnet"], str(self.link))
        self.assertEqual(selected["root"], str(self.host))
        self.assertEqual(selected["version"], "42.7.123-preview.4")
        self.assertEqual(cli.sdk_root(str(self.link)), self.host)
        os.environ["DOTNET_PURIFIER_DOTNET"] = str(self.root / "missing")
        with mock.patch.object(cli, "nix_build", side_effect=AssertionError("no fallback")), self.assertRaises(SystemExit):
            cli.local_sdk(self.root)

    def test_environment_keeps_user_runtime_and_native_library_settings(self):
        before = {"PATH": "/user/bin", "DOTNET_ROOT": "/user/runtime", "LD_LIBRARY_PATH": "/user/native"}
        with mock.patch.dict(os.environ, before):
            result = cli.everyday_env(self.root, {"dotnet": str(self.link), "packageFolder": "/locked/packages"})
        self.assertEqual(result["DOTNET_ROOT"], before["DOTNET_ROOT"])
        self.assertEqual(result["LD_LIBRARY_PATH"], before["LD_LIBRARY_PATH"])
        self.assertEqual(result["PATH"], str(self.link.parent) + os.pathsep + before["PATH"])
        self.assertEqual(result["RestoreSources"], "/locked/packages")

    def test_dependency_materialization_and_changed_selection_cache(self):
        downloads = self.root / "download-tree"
        package = downloads / "example/1.0.0/example.1.0.0.nupkg"
        package.parent.mkdir(parents=True)
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("Example.nuspec", "<package/>")
            archive.writestr("lib/net8.0/payload.txt", "locked")
        data = {"artifacts": [{"path": "example/1.0.0/example.1.0.0.nupkg", "hash": cli.sha512_sri(package)}],
                "localPackages": [], "sdkPackages": [], "targets": [], "tools": False, "workloads": [], "contentHash": "example"}
        selected = cli.local_sdk(self.root)

        def fetch_only(expr, out_link=None):
            self.assertTrue(expr.endswith(".dependencies"), expr)
            return [str(downloads)]

        with mock.patch.object(cli, "nix_build", side_effect=fetch_only), mock.patch.object(cli, "user_package_folder", return_value=self.root / "user-cache"):
            paths = cli.prefetch_paths(self.root, data, selected)
        self.assertEqual((Path(paths["packageFolder"]) / "example/1.0.0/lib/net8.0/payload.txt").read_text(), "locked")
        self.assertEqual(cli.prefetched(self.root, data, selected), paths)
        self.assertIsNone(cli.prefetched(self.root, data, {**selected, "runtimes": "changed host"}))
        package.write_bytes(b"corrupt")
        with mock.patch.object(cli, "nix_build", side_effect=fetch_only), self.assertRaises(SystemExit):
            cli.local_package_folder(self.root, data, cli.state_dir(self.root), selected)

    def test_sdk_feed_package_hash_is_checked_without_immutable_sdk(self):
        feed = self.host / "library-packs"
        feed.mkdir()
        package = feed / "Example.nupkg"
        package.write_bytes(b"modified")
        data = {"artifacts": [], "localPackages": [], "sdkPackages": [
            {"path": "example/1.0.0/example.1.0.0.nupkg", "file": package.name, "hash": "sha512-wrong"}]}
        state = self.root / "state"
        state.mkdir()
        with mock.patch.object(cli, "nix_build", return_value=[str(self.root)]), self.assertRaises(SystemExit):
            cli.local_package_folder(self.root, data, state, cli.local_sdk(self.root))

    def test_signed_nuget_hash_and_hashfrom_have_distinct_roles(self):
        data = {"artifacts": [{"path": "signed/1/signed.1.nupkg", "hash": "sha512-file"},
                              {"path": "unsigned/1/unsigned.1.nupkg", "hashFrom": "packages.lock.json"}],
                "localPackages": [], "sdkPackages": []}
        hashes = {("signed", "1"): ("packages.lock.json", "sha512-nuget"),
                  ("unsigned", "1"): ("packages.lock.json", "sha512-same")}
        with mock.patch.object(cli, "nuget_lock_hashes", return_value=hashes):
            entries = cli.local_package_entries(self.root, data)["artifacts"]
        self.assertEqual(entries[0]["hash"], "sha512-file")
        self.assertEqual(entries[0]["nugetContentHash"], "sha512-nuget")
        self.assertEqual(entries[1]["hash"], "sha512-same")
        with mock.patch.object(cli, "nuget_lock_hashes", return_value={}), self.assertRaises(SystemExit):
            cli.local_package_entries(self.root, data)


if __name__ == "__main__":
    unittest.main()
