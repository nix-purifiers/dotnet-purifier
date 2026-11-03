# SPDX-License-Identifier: MIT
"""Unit tests of the lock tool's parsing and path logic.

  DOTNET_PURIFIER_CLI=cli/dotnet_purifier.py python3 -m unittest discover -s tests/unit
"""

import base64
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("dotnet_purifier", os.environ["DOTNET_PURIFIER_CLI"])
assert spec is not None and spec.loader is not None
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def sri(data):
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


class ManifestJson(unittest.TestCase):
    def test_comments_and_trailing_commas(self):
        text = '{ // a comment\n "a": [1, 2,], /* block */ "b": {"c": 3,}, }'
        self.assertEqual(cli.read_manifest_json(text), {"a": [1, 2], "b": {"c": 3}})

    def test_strings_keep_their_content(self):
        text = '{"url": "https://example.org//x", "s": "a,]", "t": "/* not a comment */"}'
        self.assertEqual(
            cli.read_manifest_json(text),
            {"url": "https://example.org//x", "s": "a,]", "t": "/* not a comment */"},
        )


class WorkloadPacks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        manifest = root / "share" / "dotnet" / "sdk-manifests" / "10.0.100" / "example" / "10.0.1"
        manifest.mkdir(parents=True)
        (manifest / "WorkloadManifest.json").write_text(
            """{
              "workloads": {
                "tools": { "packs": ["Plain", "PerHost"], "extends": ["base"] },
                "base": { "packs": ["Any", "WindowsOnly"] },
              },
              "packs": {
                "Plain": { "kind": "sdk", "version": "1.0.0" },
                "PerHost": { "kind": "sdk", "version": "2.0.0",
                  "alias-to": { "linux-x64": "PerHost.linux-x64", "linux-arm64": "PerHost.linux-arm64",
                    "osx-arm64": "PerHost.osx-arm64" } },
                "Any": { "kind": "sdk", "version": "3.0.0", "alias-to": { "any": "Any.Real" } },
                "WindowsOnly": { "kind": "sdk", "version": "4.0.0", "alias-to": { "win-x64": "W.win-x64" } },
              },
            }"""
        )
        self.dotnet = root / "bin" / "dotnet"

    def tearDown(self):
        self.tmp.cleanup()

    def test_packs_of_every_build_host_with_extends(self):
        self.assertEqual(
            cli.workload_packs(self.dotnet, ["tools"]),
            [
                ("any.real", "3.0.0", None),
                ("perhost.linux-arm64", "2.0.0", ["aarch64-linux"]),
                ("perhost.linux-x64", "2.0.0", ["x86_64-linux"]),
                ("perhost.osx-arm64", "2.0.0", ["aarch64-darwin"]),
                ("plain", "1.0.0", None),
            ],
        )


class InstalledPackages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.build = self.root / "build"
        self.packages = self.root / "packages"
        self.sdk = self.root / "sdk"
        (self.build / "feed").mkdir(parents=True)
        (self.sdk / "share" / "dotnet" / "library-packs").mkdir(parents=True)
        (self.sdk / "share" / "dotnet" / "sdk").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, package_id, version, data, source):
        directory = self.packages / package_id / version
        directory.mkdir(parents=True)
        (directory / f"{package_id}.{version}.nupkg").write_bytes(data)
        (directory / ".nupkg.metadata").write_text(json.dumps({"version": 2, "source": source}))

    @mock.patch.object(cli, "service_index_base", lambda _source, _cache: "https://feed.example/flat/")
    def test_kinds_of_source(self):
        self.install("remote", "1.0.0", b"remote", "https://feed.example/v3/index.json")
        self.install("local", "2.0.0", b"local", str(self.build / "feed"))
        (self.build / "feed" / "Local.2.0.0.nupkg").write_bytes(b"local")
        library_packs = self.sdk / "share" / "dotnet" / "library-packs"
        self.install("fromsdk", "3.0.0", b"sdk", str(library_packs))
        (library_packs / "FromSdk.3.0.0.nupkg").write_bytes(b"sdk")

        artifacts, local, sdk_packages = cli.installed_packages(
            self.packages, self.build, [], self.sdk / "bin" / "dotnet"
        )
        self.assertEqual(
            artifacts,
            [
                {
                    "path": "remote/1.0.0/remote.1.0.0.nupkg",
                    "url": "https://feed.example/flat/remote/1.0.0/remote.1.0.0.nupkg",
                    "mirrors": [],
                    "hash": sri(b"remote"),
                }
            ],
        )
        self.assertEqual(
            local,
            [{"path": "local/2.0.0/local.2.0.0.nupkg", "file": "feed/Local.2.0.0.nupkg", "hash": sri(b"local")}],
        )
        self.assertEqual(
            sdk_packages,
            [{"path": "fromsdk/3.0.0/fromsdk.3.0.0.nupkg", "file": "FromSdk.3.0.0.nupkg", "hash": sri(b"sdk")}],
        )

    def test_folder_feed_outside_the_build_fails(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.install("stray", "1.0.0", b"stray", str(outside))
        with self.assertRaises(SystemExit):
            cli.installed_packages(self.packages, self.build, [], self.sdk / "bin" / "dotnet")


class LocalPackageFile(unittest.TestCase):
    def test_flat_and_hierarchical_feeds_ignore_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed = Path(tmp)
            (feed / "a" / "1.0.0").mkdir(parents=True)
            (feed / "a" / "1.0.0" / "a.1.0.0.nupkg").write_bytes(b"")
            (feed / "Example.B.2.0.0.nupkg").write_bytes(b"")
            self.assertEqual(cli.local_package_file(feed, "a", "1.0.0").name, "a.1.0.0.nupkg")
            self.assertEqual(cli.local_package_file(feed, "example.b", "2.0.0").name, "Example.B.2.0.0.nupkg")
            self.assertIsNone(cli.local_package_file(feed, "c", "1.0.0"))


class Projects(unittest.TestCase):
    def project(self, **fields):
        record = {
            "name": "P",
            "path": "src/P/P.csproj",
            "assemblyName": "p",
            "targetFrameworks": ["net10.0", "net8.0"],
            "selfContained": False,
            "runtimeIdentifier": "",
        }
        record.update(fields)
        return record

    def test_build_commands_by_kind(self):
        exe = cli.build_commands(self.project(kind="exe"))
        self.assertEqual(exe[0][:4], ["publish", "src/P/P.csproj", "-f", "net10.0"])
        self.assertIn(cli.NO_APPHOST, exe[0])
        self.assertNotIn(cli.NO_APPHOST, cli.build_commands(self.project(kind="exe", selfContained=True))[0])
        self.assertEqual(cli.build_commands(self.project(kind="package"))[0][0], "pack")
        self.assertEqual(cli.build_commands(self.project(kind="test"))[0][0], "build")

    def test_artifacts_by_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "P.1.0.0.nupkg").write_bytes(b"")
            (out / "P.1.0.0.snupkg").write_bytes(b"")
            (out / "other.txt").write_bytes(b"")
            self.assertEqual(
                cli.artifacts_of(self.project(kind="package"), out), ["P.1.0.0.nupkg", "P.1.0.0.snupkg"]
            )
        self.assertEqual(cli.artifacts_of(self.project(kind="exe"), None), ["bin/p"])
        self.assertEqual(
            cli.artifacts_of(self.project(kind="exe", runtimeIdentifier="browser-wasm"), None), ["lib/P/wwwroot"]
        )
        self.assertEqual(cli.artifacts_of(self.project(kind="library"), None), ["lib/P/p.dll"])
        self.assertEqual(cli.artifacts_of(self.project(kind="test"), None), [])


class DefinitionHash(unittest.TestCase):
    def test_reads_definition_files_only_and_changes_with_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "obj").mkdir()
            (root / "src" / "A.csproj").write_text("<Project />")
            (root / "src" / "Program.cs").write_text("class P {}")
            (root / "obj" / "B.csproj").write_text("<Project />")
            (root / "NuGet.Config").write_text("<configuration />")
            self.assertEqual(cli.definition_files(root), ["NuGet.Config", "src/A.csproj"])
            before = cli.definition_hash(root, ["src/A.csproj"])
            (root / "src" / "Program.cs").write_text("class Q {}")
            self.assertEqual(cli.definition_hash(root, ["src/A.csproj"]), before)
            (root / "src" / "A.csproj").write_text("<Project Sdk='x' />")
            self.assertNotEqual(cli.definition_hash(root, ["src/A.csproj"]), before)


class NuGetLockWriteBack(unittest.TestCase):
    def test_writes_missing_and_changed_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            build, root = Path(tmp) / "build", Path(tmp) / "root"
            for project in ("A", "B", "C"):
                (build / project).mkdir(parents=True)
                (root / project).mkdir(parents=True)
                (build / project / "packages.lock.json").write_text(f"{project} resolved")
            (root / "B" / "packages.lock.json").write_text("B resolved")
            (root / "C" / "packages.lock.json").write_text("C stale")
            self.assertEqual(cli.write_back_nuget_locks(build, root), ["A/packages.lock.json", "C/packages.lock.json"])
            for project in ("A", "B", "C"):
                self.assertEqual((root / project / "packages.lock.json").read_text(), f"{project} resolved")


class ToolLockfileDirectory(unittest.TestCase):
    def tearDown(self):
        cli.TOOL_LOCK_OVERRIDE = None

    def tree(self, tmp):
        root, tool = Path(tmp) / "root", Path(tmp) / "tool"
        (root / "A").mkdir(parents=True)
        (root / "A" / "A.csproj").write_text("<Project />")
        (root / "A" / "packages.lock.json").write_text("tree")
        return root, tool

    def test_its_files_replace_those_of_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, tool = self.tree(tmp)
            (tool / "A").mkdir(parents=True)
            (tool / "A" / "packages.lock.json").write_text("outside")
            own = cli.definition_hash(root, ["A/A.csproj"])
            cli.TOOL_LOCK_OVERRIDE = tool
            sources = cli.definition_sources(root)
            self.assertEqual(sources["A/packages.lock.json"], tool / "A" / "packages.lock.json")
            self.assertEqual(sources["A/A.csproj"], root / "A" / "A.csproj")
            self.assertNotEqual(cli.definition_hash(root, ["A/A.csproj"]), own)

    def test_lock_run_starts_from_the_build_until_the_directory_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, tool = self.tree(tmp)
            build = Path(tmp) / "build"
            (build / "A").mkdir(parents=True)
            (build / "A" / "packages.lock.json").write_text("tree")
            cli.TOOL_LOCK_OVERRIDE = tool
            cli.use_tool_locks(build, root)
            self.assertEqual((build / "A" / "packages.lock.json").read_text(), "tree")
            self.assertEqual(cli.write_back_nuget_locks(build, tool), ["A/packages.lock.json"])
            self.assertEqual((root / "A" / "packages.lock.json").read_text(), "tree")
            (tool / "A" / "packages.lock.json").write_text("outside")
            cli.use_tool_locks(build, root)
            self.assertEqual((build / "A" / "packages.lock.json").read_text(), "outside")


class LockfileLocation(unittest.TestCase):
    def tearDown(self):
        cli.LOCK_OVERRIDE = None

    def test_environment_variable_and_flag(self):
        with mock.patch.object(cli.sys, "argv", ["dotnet-purifier"]), mock.patch.dict(
            os.environ, {"DOTNET_PURIFIER_LOCKFILE": "/elsewhere/env.lock.json"}
        ):
            with self.assertRaises(SystemExit):
                cli.main()
            self.assertEqual(cli.lock_path(Path("/build")), Path("/elsewhere/env.lock.json"))
            self.assertEqual(cli.state_dir(Path("/build")), Path("/elsewhere/.dotnet-purifier"))
        argv = ["dotnet-purifier", "--lockfile", "/flag/flag.lock.json"]
        with mock.patch.object(cli.sys, "argv", argv), mock.patch.dict(
            os.environ, {"DOTNET_PURIFIER_LOCKFILE": "/elsewhere/env.lock.json"}
        ):
            with self.assertRaises(SystemExit):
                cli.main()
            self.assertEqual(cli.lock_path(Path("/build")), Path("/flag/flag.lock.json"))

    def test_default_is_the_build_root(self):
        self.assertEqual(cli.lock_path(Path("/build")), Path("/build/dotnet-purifier.lock.json"))
        self.assertEqual(cli.state_dir(Path("/build")), Path("/build/.dotnet-purifier"))


if __name__ == "__main__":
    unittest.main()
