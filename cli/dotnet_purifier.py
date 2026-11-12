# SPDX-License-Identifier: MIT
"""dotnet-purifier: lock a .NET build's NuGet downloads, and run dotnet from them.

  dotnet-purifier lock [--force] [PROJECT_OR_SOLUTION...]
      Runs the build once with an empty NuGet package folder and records
      every package NuGet installs in dotnet-purifier.lock.json at the build
      root, with the projects the build has. The default is the one
      solution at the build root, or else every project file below it. The
      lock is renewed only when the build definition changed, unless
      --force is given.

  dotnet-purifier update [PACKAGE...]
      Updates the build's package references, every package or the named
      ones, with the SDK's own `dotnet package update`, and relocks. `lock`
      keeps the versions the project files name.

  dotnet-purifier prefetch
      Realizes what the development loop and an editor need, with garbage
      collector roots in .dotnet-purifier/ beside the lockfile: the locked
      packages, which it installs in the NuGet package folder, the SDK with
      the runtime of every target framework, the local tools and the
      workloads. It then
      restores every target, so an editor finds what a restore writes.
      Afterwards both work with the network cut off.

  dotnet-purifier publish [ARG...]
      Builds the release with Nix, the packages of the packable projects,
      and runs `dotnet nuget push` on each, with the given arguments (a
      --source and an --api-key, for example).

  dotnet-purifier dotnet [ARG...]
      Runs dotnet from the last prefetch, prefetching first when the lock
      changed since: every restore reads only the locked packages.

Every command takes --lockfile PATH, a lockfile to use in place of the one
at the build root. Without --lockfile, the environment variable
DOTNET_PURIFIER_LOCKFILE names it. --tool-lockfile DIR, or the environment
variable DOTNET_PURIFIER_TOOL_LOCKFILE, names a directory whose
packages.lock.json files, at the paths of their projects, take the place
of those in the build: `lock` and `update` write NuGet's lockfiles there
and leave the build's own as they are.

The package that installs this script sets the environment variables
DOTNET_PURIFIER_NIX_LIB (the Nix library directory), DOTNET_PURIFIER_FLAKE
(the dotnet-purifier flake, whose `lib.purifier` the builds use),
DOTNET_PURIFIER_NIXPKGS
(the nixpkgs the SDKs come from), DOTNET_PURIFIER_SYSTEM (the Nix system)
and DOTNET_PURIFIER_DEFAULT_SDK (the SDK major version used when the build
has no global.json).
"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

LOCK_NAME = "dotnet-purifier.lock.json"

# The lockfile that --lockfile names, which replaces the one at the build
# root, so that a packaging flake can keep the lockfile of a project it does
# not change.
LOCK_OVERRIDE = None


def lock_path(root):
    return LOCK_OVERRIDE or root / LOCK_NAME


# The directory that --tool-lockfile names, whose packages.lock.json files,
# at the paths of their projects, replace those in the build.
TOOL_LOCK_OVERRIDE = None


def tool_lock_dir(root):
    return TOOL_LOCK_OVERRIDE or root


def state_dir(root):
    """The directory of prefetch's garbage-collector roots and record,
    beside the lockfile."""
    return lock_path(root).parent / STATE_DIR
FORMAT_VERSION = 1
STATE_DIR = ".dotnet-purifier"
PROJECT_SUFFIXES = (".csproj", ".fsproj", ".vbproj")
SOLUTION_SUFFIXES = (".sln", ".slnx")

# Directories that never belong to the build definition or the build input.
SKIPPED_DIRS = {"bin", "obj", ".git", STATE_DIR, ".vs", ".idea", "node_modules"}

# The files whose content decides what a restore installs. A .nupkg in the
# tree is a package of a folder feed.
DEFINITION_NAMES = {"global.json", "nuget.config", "dotnet-tools.json", "packages.lock.json"}
DEFINITION_SUFFIXES = PROJECT_SUFFIXES + SOLUTION_SUFFIXES + (".props", ".targets", ".nupkg")

# MSBuild properties every build of the purifier passes, in the lock run
# and in the Nix sandbox alike. NuGet's vulnerability audit downloads
# advisories that a sandboxed build cannot reach.
BUILD_PROPERTIES = ["-p:Configuration=Release", "-p:NuGetAudit=false"]

# A framework-dependent build that the `dotnet` command runs needs no
# apphost. A self-contained build needs one, since nothing else starts it.
NO_APPHOST = "-p:UseAppHost=false"

# The runtime identifiers of the machines a Nix build of the lockfile may
# run on. Some packs are chosen for the machine that builds, such as the
# Crossgen2 compiler of ReadyToRun builds; the lock run restores each
# project once more for each of these machines, so the lock holds their
# packs too.
BUILD_HOSTS = ["linux-x64", "linux-arm64", "osx-arm64"]

# The Nix system of each machine of BUILD_HOSTS, for the `systems` of a
# download entry that only one machine needs.
HOST_SYSTEMS = {"linux-x64": "x86_64-linux", "linux-arm64": "aarch64-linux", "osx-arm64": "aarch64-darwin"}

# The lock run builds in one MSBuild process. Worker nodes talk over pipes
# that MSBuild puts in /tmp on Unix, which not every machine that locks
# lets a build write, and a lock run gains little from parallel builds.
# What a build restores does not depend on these switches.
LOCK_RUN_SWITCHES = ["-m:1", "-nodeReuse:false"]


def reference_packs_only(env, directory, dotnet):
    """Points the SDK's packs at a directory with only the targeting packs
    (*.Ref) the SDK ships with. The SDK then installs every runtime and
    host pack it needs from NuGet, where the lock records it, rather than
    taking the ones a given SDK ships for the machine it runs on. The
    targeting packs hold reference assemblies, which are the same on every
    machine, and the package pruning data a restore needs. MSBuild reads
    environment variables as properties."""
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    packs = Path(dotnet).parent.parent / "share" / "dotnet" / "packs"
    for pack in sorted(packs.glob("*.Ref")):
        (directory / pack.name).symlink_to(pack)
    env["NETCoreTargetingPackRoot"] = str(directory)
    return env


def fail(message):
    print(f"dotnet-purifier: {message}", file=sys.stderr)
    sys.exit(1)


def info(message):
    print(f"dotnet-purifier: {message}", file=sys.stderr)


def build_root():
    root = Path.cwd()
    if not any(p.suffix in PROJECT_SUFFIXES + SOLUTION_SUFFIXES for p in walk(root)):
        fail(f"{root} has no project or solution files; run dotnet-purifier from the root of a .NET build")
    return root


def walk(root):
    """Every file below root, outside the skipped directories."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def sha512_sri(path):
    digest = hashlib.sha512()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return "sha512-" + base64.b64encode(digest.digest()).decode()


def is_nuget_lock(path):
    return path.name.lower() == "packages.lock.json"


def nuget_locks(directory):
    """The packages.lock.json files below a directory, by their path
    relative to it. Symbolic links are left out."""
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(directory).as_posix(): path
        for path in walk(directory)
        if is_nuget_lock(path) and not path.is_symlink()
    }


def definition_sources(root):
    """The files that define what a restore installs, as their paths
    relative to root, in sorted order, to the file each one is read from.
    With a tool lockfile directory, its packages.lock.json files take the
    place of those in the build. Symbolic links are left out."""
    tool = tool_lock_dir(root)
    sources = {}
    for path in walk(root):
        if path.is_symlink() or (is_nuget_lock(path) and tool != root):
            continue
        if path.name.lower() in DEFINITION_NAMES or path.suffix.lower() in DEFINITION_SUFFIXES:
            sources[path.relative_to(root).as_posix()] = path
    if tool != root:
        sources.update(nuget_locks(tool))
    return dict(sorted(sources.items()))


def definition_files(root):
    """The paths of the definition files relative to root, in sorted
    order."""
    return list(definition_sources(root))


def definition_hash(root, targets):
    """The SRI SHA-256 of the build definition: of a text whose first line
    is the targets as JSON and whose other lines are each definition file's
    path and the hex SHA-256 of its content, separated by a tab.
    lib-overlays/default/definition.nix computes the same hash at evaluation time, so
    a Nix build knows when the lock is stale."""
    lines = [json.dumps(targets, separators=(",", ":"), ensure_ascii=False)]
    for relative, path in definition_sources(root).items():
        lines.append(f"{relative}\t{hashlib.sha256(path.read_bytes()).hexdigest()}")
    text = "\n".join(lines) + "\n"
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


def sdk_major(root):
    """The SDK major version that global.json names, or else the default
    that the package sets."""
    global_json = root / "global.json"
    if global_json.is_file():
        version = json.loads(global_json.read_text()).get("sdk", {}).get("version")
        if version:
            return int(version.split(".")[0])
    return int(os.environ["DOTNET_PURIFIER_DEFAULT_SDK"])


def nix_build(expr, out_link=None):
    command = ["nix", "build", "--impure", "--print-out-paths", "--expr", expr]
    command += ["--out-link", str(out_link)] if out_link else ["--no-link"]
    result = subprocess.run(command, stdout=subprocess.PIPE, text=True, check=False)
    if result.returncode != 0:
        fail("nix build failed")
    return result.stdout.split()


def nixpkgs_expr():
    nixpkgs = os.environ["DOTNET_PURIFIER_NIXPKGS"]
    system = os.environ["DOTNET_PURIFIER_SYSTEM"]
    return f'(import {nixpkgs} {{ system = "{system}"; }})'


def sdk(major):
    """The nixpkgs .NET SDK of a major version, which a Nix build of the
    lockfile uses too."""
    # nix build realizes every output in the package's outputsToInstall,
    # the SDK's man pages among them, and prints them in no fixed order.
    outputs = nix_build(f"{nixpkgs_expr()}.dotnetCorePackages.sdk_{major}_0")
    for output in outputs:
        if (Path(output) / "bin" / "dotnet").exists():
            return output
    fail(f"the .NET SDK {major} that nixpkgs built has no bin/dotnet")


def with_sdk_on_path(env, dotnet):
    """The environment with the SDK first on PATH, as a build that runs
    `dotnet` itself expects, and as the Nix build has it. DOTNET_ROOT names
    the SDK too: MSBuild starts task hosts through the SDK's MSBuild
    apphost, which finds the runtime only through it."""
    env["PATH"] = f"{Path(dotnet).parent}{os.pathsep}{env.get('PATH', '')}"
    env["DOTNET_ROOT"] = str(Path(dotnet).parent.parent / "share" / "dotnet")
    return env


def dotnet_env(home, dotnet):
    # .NET keeps its named mutexes under XDG_RUNTIME_DIR.
    (home / "run").mkdir(mode=0o700)
    env = with_sdk_on_path(dict(os.environ), dotnet)
    env.update(
        HOME=str(home),
        XDG_RUNTIME_DIR=str(home / "run"),
        DOTNET_CLI_HOME=str(home),
        NUGET_PACKAGES=str(home / "packages"),
        NUGET_HTTP_CACHE_PATH=str(home / "http-cache"),
        NUGET_PLUGINS_CACHE_PATH=str(home / "plugins-cache"),
        NUGET_SCRATCH=str(home / "scratch"),
        DOTNET_CLI_TELEMETRY_OPTOUT="1",
        DOTNET_NOLOGO="1",
        DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
        DOTNET_SKIP_WORKLOAD_INTEGRITY_CHECK="1",
        # The SDK looks for workload updates on nuget.org in the background.
        # It reads this variable as true only when it says "true".
        DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE="true",
    )
    return reference_packs_only(env, home / "packs", dotnet)


def run(dotnet, args, cwd, env, capture=False):
    result = subprocess.run(
        [dotnet, *args],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE if capture else None,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        fail(f"`dotnet {' '.join(args)}` failed")
    return result.stdout


def default_targets(root):
    solutions = sorted(p for p in root.iterdir() if p.suffix in SOLUTION_SUFFIXES)
    if len(solutions) == 1:
        return [solutions[0].name]
    return [str(p.relative_to(root)) for p in walk(root) if p.suffix in PROJECT_SUFFIXES]


def project_files(dotnet, root, targets, env):
    """The project files the targets name, relative to root."""
    projects = []
    for target in targets:
        path = root / target
        if path.suffix in SOLUTION_SUFFIXES:
            out = run(dotnet, ["sln", str(path), "list"], root, env, capture=True)
            for line in out.splitlines():
                line = line.strip().replace("\\", "/")
                if line.endswith(PROJECT_SUFFIXES):
                    projects.append(str((path.parent / line).resolve().relative_to(root.resolve())))
        elif path.suffix in PROJECT_SUFFIXES:
            projects.append(target)
        else:
            fail(f"{target} is neither a project nor a solution")
    return sorted(set(projects))


def describe(dotnet, root, project, env):
    """A project's lock record, from its evaluated MSBuild properties."""
    names = [
        "AssemblyName",
        "OutputType",
        "IsPackable",
        "IsTestProject",
        "TargetFramework",
        "TargetFrameworks",
        "PackageId",
        "PackageVersion",
        "SelfContained",
        "RuntimeIdentifier",
        "PublishAot",
        "NativeLib",
        "NETCoreSdkPortableRuntimeIdentifier",
    ]
    out = run(
        dotnet,
        ["msbuild", project, *BUILD_PROPERTIES, *(f"-getProperty:{n}" for n in names), "-getItem:FrameworkReference"],
        root,
        env,
        capture=True,
    )
    data = json.loads(out)
    props = data["Properties"]
    if props["TargetFramework"]:
        frameworks = [props["TargetFramework"]]
    else:
        frameworks = [f for f in props["TargetFrameworks"].split(";") if f]
    if props["IsTestProject"].lower() == "true":
        kind = "test"
    elif props["OutputType"].lower() in ("exe", "winexe"):
        kind = "exe"
    elif props["IsPackable"].lower() != "false":
        kind = "package"
    else:
        kind = "library"
    references = sorted({i["Identity"] for i in data.get("Items", {}).get("FrameworkReference", [])})
    record = {
        "name": Path(project).stem,
        "path": project,
        "kind": kind,
        "assemblyName": props["AssemblyName"],
        "targetFrameworks": frameworks,
        "frameworkReferences": references,
    }
    if kind == "exe":
        record["selfContained"] = props["SelfContained"].lower() == "true"
        record["runtimeIdentifier"] = props["RuntimeIdentifier"]
    if kind in ("library", "package") and props.get("PublishAot", "").lower() == "true" and props.get("NativeLib", "").lower() == "shared":
        record["nativePublish"] = {
            "kind": "shared",
            "runtimeIdentifier": props["RuntimeIdentifier"] or props["NETCoreSdkPortableRuntimeIdentifier"],
            "runtimeIdentifierSource": "project" if props["RuntimeIdentifier"] else "sdk-host",
        }
    if kind == "package":
        record["packageId"] = props["PackageId"]
        record["packageVersion"] = props["PackageVersion"]
    return record


def build_commands(project):
    """The dotnet commands that build a project, in the lock run and in the
    Nix sandbox alike. pkgs/build-dotnet-project/default.nix runs the same
    commands for each kind."""
    path = project["path"]
    if project["kind"] == "exe":
        framework = ["-f", project["targetFrameworks"][0]]
        apphost = [] if project["selfContained"] else [NO_APPHOST]
        return [["publish", path, *framework, *BUILD_PROPERTIES, *apphost, "-o", "{out}"]]
    if project["kind"] == "package":
        return [["pack", path, *BUILD_PROPERTIES, NO_APPHOST, "-o", "{out}"]]
    return [["build", path, *BUILD_PROPERTIES, NO_APPHOST]]



def native_publish_command(project):
    """Publish an evaluated NativeAOT shared library, separately from its
    ordinary managed build or NuGet package."""
    native = project.get("nativePublish")
    if native is None:
        return None
    rid = ["-r", native["runtimeIdentifier"]] if native["runtimeIdentifier"] else []
    return ["publish", project["path"], "-f", project["targetFrameworks"][0],
            *rid, *BUILD_PROPERTIES, "-o", "{nativeOut}"]


def artifacts_of(project, out):
    """The files a project's Nix build produces, relative to the build's
    output, as the lock run produced them: the packages a packable library
    packs to, the command of an executable and the assembly of any other
    library. A test project produces none."""
    name, assembly = project["name"], project["assemblyName"]
    if project["kind"] == "package":
        return sorted(p.name for p in out.iterdir() if p.suffix in (".nupkg", ".snupkg"))
    if project["kind"] == "exe" and project["runtimeIdentifier"].startswith(("browser-", "wasi-")):
        return [f"lib/{name}/wwwroot"]
    if project["kind"] == "exe":
        return [f"bin/{assembly}"]
    if project["kind"] == "library":
        return [f"lib/{name}/{assembly}.dll"]
    return []


def service_index_base(source, cache):
    """The flat-container base URL of a NuGet V3 feed."""
    if source not in cache:
        if not source.endswith("/index.json"):
            fail(f"package source {source} is not a NuGet V3 feed; dotnet-purifier fetches only from V3 feeds")
        with urllib.request.urlopen(source) as response:
            index = json.load(response)
        bases = [r["@id"] for r in index["resources"] if r["@type"].startswith("PackageBaseAddress/3.0.0")]
        if not bases:
            fail(f"{source} has no PackageBaseAddress resource")
        cache[source] = bases[0].rstrip("/") + "/"
    return cache[source]


def local_package_file(feed, package_id, version):
    """The .nupkg of a package in a folder feed, in a flat or a
    hierarchical layout."""
    name = f"{package_id}.{version}.nupkg"
    for path in sorted(feed.rglob("*.nupkg")):
        if path.name.lower() == name:
            return path
    return None


def configured_sources(dotnet, build, env):
    """The enabled package sources of the build's NuGet configuration."""
    out = run(dotnet, ["nuget", "list", "source", "--format", "Short"], build, env, capture=True)
    sources = []
    for line in out.splitlines():
        flag, _, source = line.strip().partition(" ")
        if flag.startswith("E") and source:
            sources.append(source.strip())
    return sources


def fetch_from_feeds(package_id, version, sources, bases, expected=None):
    """The URL and SRI SHA-512 of a package on the first of the http(s)
    sources that serves it, and, when `expected` is given, serves a file
    with that hash. Returns None when no source does."""
    name = f"{package_id}.{version}.nupkg"
    for source in sources:
        if not source.startswith(("https://", "http://")):
            continue
        url = f"{service_index_base(source, bases)}{package_id}/{version}/{name}"
        found = prefetch_hash(url)
        if found is not None and (expected is None or found == expected):
            return url, found
    return None


def prefetch_hash(url):
    """The SRI SHA-512 of the file at a URL, fetched into the store with
    the fetcher a Nix build of the lockfile uses, or None when it cannot be
    fetched."""
    result = subprocess.run(
        ["nix", "store", "prefetch-file", "--json", "--hash-type", "sha512", url],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)["hash"]


def find_on_feeds(package_id, version, nupkg, sources, bases):
    """The URL of a package on the first of the sources that serves the
    same file, for a package NuGet installed without recording its feed.
    `dotnet tool restore` installs packages that way."""
    found = fetch_from_feeds(package_id, version, sources, bases, expected=sha512_sri(nupkg))
    if found is None:
        fail(f"{package_id} {version} was installed without its feed, and no configured feed serves the same file")
    return found[0]


def installed_workloads(dotnet, build, env):
    """The workloads installed in the lock run's .NET home."""
    out = run(dotnet, ["workload", "list", "--machine-readable"], build, env, capture=True)
    for line in reversed(out.splitlines()):
        if line.startswith("{"):
            return sorted(json.loads(line)["installed"])
    fail("`dotnet workload list --machine-readable` printed no JSON")


def read_manifest_json(text):
    """Parses a workload manifest. The SDK reads manifests as JSON with
    comments and trailing commas, so they are removed first, outside
    strings."""
    tokens = re.finditer(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|,(?=\s*[\]}])', text, re.DOTALL)
    kept, last = [], 0
    for token in tokens:
        if not token.group().startswith('"'):
            kept.append(text[last : token.start()])
            last = token.end()
    kept.append(text[last:])
    return json.loads("".join(kept))


def workload_packs(dotnet, workloads):
    """The packs that installing the workloads installs, on every machine
    of BUILD_HOSTS, as (id, version, systems) with the id and version in
    lower case, where `systems` lists the Nix systems that need the pack,
    or is None when every machine needs it. They come from
    the workload manifests the SDK ships with, which a workload install
    with --skip-manifest-update reads: a workload names its packs and the
    workloads it extends, and a pack names its version and, when it
    differs by machine, the package for each machine (`alias-to`)."""
    manifests = Path(dotnet).parent.parent / "share" / "dotnet" / "sdk-manifests"
    definitions, packs = {}, {}
    for path in sorted(manifests.glob("*/*/*/WorkloadManifest.json")) + sorted(manifests.glob("*/*/WorkloadManifest.json")):
        manifest = read_manifest_json(path.read_text())
        definitions.update(manifest.get("workloads", {}))
        packs.update(manifest.get("packs", {}))
    names, seen, pending = set(), set(), list(workloads)
    while pending:
        workload = pending.pop()
        if workload in seen:
            continue
        seen.add(workload)
        definition = definitions[workload]
        names.update(definition.get("packs", []))
        pending.extend(definition.get("extends", []))
    hosts = {}
    for name in names:
        pack = packs[name]
        alias = pack.get("alias-to")
        for host in BUILD_HOSTS:
            package_id = alias.get(host, alias.get("any")) if alias else name
            # A pack with no package for a machine does not apply there,
            # as the Python of Emscripten does not on Linux.
            if package_id:
                hosts.setdefault((package_id.lower(), pack["version"].lower()), set()).add(host)
    result = []
    for (package_id, version), needed_by in sorted(hosts.items()):
        systems = None if needed_by == set(BUILD_HOSTS) else sorted(HOST_SYSTEMS[h] for h in needed_by)
        result.append((package_id, version, systems))
    return result


def workload_artifacts(dotnet, workloads, sources, bases, known):
    """The download entries of the workloads' packs that are not among the
    known paths."""
    artifacts = []
    for package_id, version, systems in workload_packs(dotnet, workloads):
        path = f"{package_id}/{version}/{package_id}.{version}.nupkg"
        if path in known:
            continue
        found = fetch_from_feeds(package_id, version, sources, bases)
        if found is None:
            fail(f"no configured feed serves the workload pack {package_id} {version}")
        entry = {"path": path, "url": found[0], "mirrors": [], "hash": found[1]}
        if systems is not None:
            entry["systems"] = systems
        artifacts.append(entry)
    return artifacts


def installed_packages(packages_dir, build, sources, dotnet):
    """Every package of a NuGet package folder: the downloaded ones as
    download entries { path, url, mirrors, hash }, those from folder feeds
    inside the build as { path, file, hash }, where `file` is the package's
    file in the build, and those from the folder feed the SDK carries
    (library-packs) as { path, file, hash }, where `file` is the package's
    file in that feed. `path` is where the package lies in a NuGet package
    folder."""
    bases = {}
    artifacts = []
    local = []
    sdk_packages = []
    library_packs = (Path(dotnet).parent.parent / "share" / "dotnet" / "library-packs").resolve()
    for metadata in sorted(packages_dir.glob("*/*/.nupkg.metadata")):
        version_dir = metadata.parent
        package_id = version_dir.parent.name
        version = version_dir.name
        nupkg = version_dir / f"{package_id}.{version}.nupkg"
        path = f"{package_id}/{version}/{nupkg.name}"
        source = json.loads(metadata.read_text()).get("source")
        if not source or source.startswith(("https://", "http://")):
            if source:
                url = f"{service_index_base(source, bases)}{package_id}/{version}/{nupkg.name}"
            else:
                url = find_on_feeds(package_id, version, nupkg, sources, bases)
            # A NuGet feed serves a package version at one URL for good, so
            # the entry lists no mirrors.
            artifacts.append({"path": path, "url": url, "mirrors": [], "hash": sha512_sri(nupkg)})
            continue
        feed = Path(source).resolve()
        if feed == library_packs or feed.name == "library-packs" and (feed.parent / "sdk").is_dir():
            # The SDK's copy of a package can differ between builds of the
            # SDK for different machines, as nixpkgs builds the SDK from
            # source, so the lock takes the package from a feed when one
            # serves it, the same file on every machine. A build then finds
            # the package installed and does not read the SDK's copy.
            found = fetch_from_feeds(package_id, version, sources, bases)
            if found is not None:
                artifacts.append({"path": path, "url": found[0], "mirrors": [], "hash": found[1]})
                continue
            file = local_package_file(feed, package_id, version)
            if file is None:
                fail(f"{package_id} {version} is not in the SDK's feed {source}")
            sdk_packages.append({"path": path, "file": file.name, "hash": sha512_sri(nupkg)})
            continue
        if not feed.is_relative_to(build.resolve()):
            fail(
                f"{package_id} {version} comes from the folder {source}, outside the build; "
                "dotnet-purifier takes packages from folder feeds only inside the build's tree"
            )
        file = local_package_file(feed, package_id, version)
        if file is None:
            fail(f"{package_id} {version} is not in its folder feed {source}")
        local.append({"path": path, "file": str(file.relative_to(build.resolve())), "hash": sha512_sri(nupkg)})
    return artifacts, local, sdk_packages


def lock(args, evaluate=False):
    """Locks the build. With `evaluate`, as `update` asks, the first
    restore of each target resolves again rather than keeping what a
    packages.lock.json records."""
    force = "--force" in args
    targets = [a for a in args if a != "--force"]
    root = build_root()
    targets = targets or default_targets(root)
    path = lock_path(root)
    def_hash = definition_hash(root, targets)
    if not force and path.is_file():
        old = json.loads(path.read_text())
        if old.get("version") == FORMAT_VERSION and old.get("definitionHash") == def_hash:
            info(f"{LOCK_NAME} is up to date")
            return

    major = sdk_major(root)
    dotnet = f"{sdk(major)}/bin/dotnet"
    tools = (root / ".config" / "dotnet-tools.json").is_file()

    with tempfile.TemporaryDirectory(prefix="dotnet-purifier-") as tmp:
        tmp = Path(tmp)
        build = tmp / "build"
        shutil.copytree(root, build, ignore=lambda _d, names: [n for n in names if n in SKIPPED_DIRS])
        use_tool_locks(build, root)
        home = tmp / "home"
        home.mkdir()
        env = dotnet_env(home, dotnet)
        workload_mode = os.environ.get("DOTNET_PURIFIER_WORKLOAD_MODE_HELPER", str(Path(__file__).resolve().parents[1] / "pkgs/dotnet-purifier/workload-mode.py"))
        subprocess.run([sys.executable, workload_mode, dotnet], cwd=build, env=env, check=True)

        sdk_version = run(dotnet, ["--version"], build, env, capture=True).strip()
        info(f"locking with the .NET SDK {sdk_version}")
        # A build may run the local tools, and needs its workloads, so both
        # are installed first.
        if tools:
            run(dotnet, ["tool", "restore"], build, env)
        for target in targets:
            run(dotnet, ["workload", "restore", target, "--skip-manifest-update"], build, env)
        workloads = installed_workloads(dotnet, build, env)
        # Packages set some of the properties a project's kind comes from,
        # as Microsoft.NET.Test.Sdk sets IsTestProject, so the projects are
        # restored before they are described.
        for target in targets:
            reevaluate = ["--force-evaluate"] if evaluate else []
            run(
                dotnet,
                ["restore", target, *reevaluate, *BUILD_PROPERTIES, NO_APPHOST, *LOCK_RUN_SWITCHES],
                build,
                env,
            )
        projects = [describe(dotnet, build, p, env) for p in project_files(dotnet, build, targets, env)]
        if not projects:
            fail("the build has no projects")
        if len({p["name"] for p in projects}) != len(projects):
            fail("two projects share a file name; dotnet-purifier names packages after project files")

        for project in projects:
            out = tmp / "out" / project["name"]
            for command in build_commands(project):
                run(dotnet, [*(a.replace("{out}", str(out)) for a in command), *LOCK_RUN_SWITCHES], build, env)
            project["artifacts"] = artifacts_of(project, out)
            native_command = native_publish_command(project)
            if native_command is not None:
                native_out = tmp / "native-out" / project["name"]
                run(dotnet, [*(a.replace("{nativeOut}", str(native_out)) for a in native_command), *LOCK_RUN_SWITCHES], build, env)
                project["nativePublish"]["artifacts"] = sorted(
                    str(p.relative_to(native_out)) for p in native_out.rglob("*") if p.is_file()
                )
                if not project["nativePublish"]["artifacts"]:
                    fail("native publish produced no artifacts for " + project["name"])
            # The restores for the other machines leave UseAppHost as the
            # project sets it, so the lock also holds the host packs that a
            # developer's own `dotnet build` uses, on every machine.
            restore = [a for a in build_commands(project)[0][1:] if a not in ("-o", "{out}", NO_APPHOST)]
            if "-f" in restore:
                i = restore.index("-f")
                del restore[i : i + 2]
            for host in BUILD_HOSTS:
                # A class library with no declared RID gets a host RID during
                # publish, unlike an ordinary managed build. Capture those
                # target runtime/compiler packs for each supported SDK host.
                native_rid = ["-r", host] if project.get("nativePublish", {}).get("runtimeIdentifierSource") == "sdk-host" else []
                native_host = [f"-p:NETCoreSdkPortableRuntimeIdentifier={host}"] if "nativePublish" in project else []
                run(
                    dotnet,
                    ["restore", *restore, *native_rid, *native_host, f"-p:NETCoreSdkRuntimeIdentifier={host}", *LOCK_RUN_SWITCHES],
                    build,
                    env,
                )

        # A project in NuGet's lock mode keeps the resolution its
        # packages.lock.json records. Where that file is missing, or no
        # longer matches the project, the build, or the tool lockfile
        # directory when one is given, gets the one the restores wrote, and
        # the build definition changes with it.
        if write_back_nuget_locks(build, tool_lock_dir(root)):
            def_hash = definition_hash(root, targets)

        sources = configured_sources(dotnet, build, env)
        bases = {}
        artifacts, local, sdk_packages = installed_packages(home / "packages", build, sources, dotnet)
        known = {a["path"] for a in artifacts}
        artifacts += workload_artifacts(dotnet, workloads, sources, bases, known)
        artifacts.sort(key=lambda a: a["path"])
        # The packages.lock.json files Nix reads are the ones in the build
        # root, which a restore in the copy may have rewritten.
        hashes = nuget_lock_hashes(root)
        for entries in (artifacts, local, sdk_packages):
            take_hashes_from_nuget_locks(entries, hashes)

    data = {
        "version": FORMAT_VERSION,
        "sdk": {"version": sdk_version, "major": major},
        "targets": targets,
        "definitionHash": def_hash,
        "tools": tools,
        "workloads": workloads,
        "projects": projects,
        "artifacts": artifacts,
        "localPackages": local,
        "sdkPackages": sdk_packages,
    }
    data["contentHash"] = content_hash(data)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    info(f"wrote {LOCK_NAME}: {len(projects)} projects, {len(artifacts) + len(local) + len(sdk_packages)} packages")


def write_back_nuget_locks(build, target):
    """Copies each packages.lock.json in the lock run's copy of the build to
    the same path below `target`, the build or the tool lockfile directory,
    where it has none or another one, and returns the paths it wrote,
    relative to `target`."""
    written = []
    for relative, copy in nuget_locks(build).items():
        destination = target / relative
        if destination.is_file() and destination.read_bytes() == copy.read_bytes():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(copy, destination)
        info(f"wrote {destination}")
        written.append(relative)
    return written


def use_tool_locks(build, root):
    """Puts the packages.lock.json files of the tool lockfile directory in
    place of those in the lock run's copy of the build. A directory that
    does not exist yet leaves the build's own files as the start."""
    tool = tool_lock_dir(root)
    if tool == root or not tool.is_dir():
        return
    for path in nuget_locks(build).values():
        path.unlink()
    for relative, path in nuget_locks(tool).items():
        (build / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, build / relative)


def nuget_lock_hashes(root):
    """The packages that the build's packages.lock.json files record (NuGet's
    lock mode), as (id, version) in lower case to the file, relative to
    root, and the SRI SHA-512 of the .nupkg that the file records."""
    hashes = {}
    for path, source in definition_sources(root).items():
        if not is_nuget_lock(source):
            continue
        data = json.loads(source.read_text())
        for packages in data.get("dependencies", {}).values():
            for package_id, package in packages.items():
                if "contentHash" in package and "resolved" in package:
                    key = (package_id.lower(), package["resolved"].lower())
                    hashes.setdefault(key, (path, "sha512-" + package["contentHash"]))
    return hashes


def take_hashes_from_nuget_locks(entries, hashes):
    """Replaces the hash of each entry that a packages.lock.json records
    with the same hash by `hashFrom`, the file Nix reads it from."""
    for entry in entries:
        package_id, version = entry["path"].split("/")[:2]
        found = hashes.get((package_id, version))
        if found and found[1] == entry["hash"]:
            del entry["hash"]
            entry["hashFrom"] = found[0]
    return entries


def content_hash(data):
    """The SRI SHA-256 of a lockfile's content without its contentHash, as
    JSON with sorted keys and no whitespace. That is the JSON Nix's
    builtins.toJSON writes, so lib-overlays/default/lock.nix checks the same hash."""
    content = {k: v for k, v in data.items() if k != "contentHash"}
    text = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


def read_lock(root):
    path = lock_path(root)
    if not path.is_file():
        fail(f"{path} does not exist; run `dotnet-purifier lock` first")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        fail(f"{LOCK_NAME} is not valid JSON ({error}); run `dotnet-purifier lock --force`")
    if data.get("version") != FORMAT_VERSION:
        fail(f"{LOCK_NAME} has format {data.get('version')}; run `dotnet-purifier lock --force`")
    if data.get("contentHash") != content_hash(data):
        fail(f"{LOCK_NAME} was edited after `dotnet-purifier lock` wrote it; run `dotnet-purifier lock --force`")
    if data.get("definitionHash") != definition_hash(root, data["targets"]):
        fail(f"the build definition changed since {LOCK_NAME} was made; run `dotnet-purifier lock`")
    return data


def interactive_expr(root, attribute):
    lib = os.environ["DOTNET_PURIFIER_NIX_LIB"]
    flake = os.environ["DOTNET_PURIFIER_FLAKE"]
    purifier = f'(builtins.getFlake "path:{flake}").lib.purifier'
    tool = tool_lock_dir(root).resolve()
    return (
        f"(import {lib}/interactive.nix {{ pkgs = {nixpkgs_expr()}; "
        f"purifier = {purifier}; src = {root.resolve()}; lockFile = {lock_path(root).resolve()}; "
        f"packagesLock = {tool}; }}).{attribute}"
    )


def user_package_folder(env):
    if env.get("NUGET_PACKAGES"):
        return Path(env["NUGET_PACKAGES"])
    return Path(env.get("DOTNET_CLI_HOME") or Path.home()) / ".nuget" / "packages"


def install(package_folder, target):
    """Copies the packages of a Nix-built package folder that the target
    folder lacks into it, the way NuGet installs them."""
    for version_dir in sorted(Path(package_folder).glob("*/*")):
        dest = target / version_dir.relative_to(package_folder)
        if not (dest / ".nupkg.metadata").exists():
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(version_dir, dest, symlinks=False, copy_function=shutil.copyfile)


def locked_config(path, package_folder):
    """Writes a NuGet configuration whose only source is the locked
    packages, for `dotnet tool restore`, which reads the sources of a
    configuration rather than `RestoreSources`."""
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<configuration>\n"
        "  <packageSources>\n"
        "    <clear />\n"
        f'    <add key="dotnet-purifier" value="{package_folder}" />\n'
        "  </packageSources>\n"
        "  <packageSourceMapping>\n"
        "    <clear />\n"
        "  </packageSourceMapping>\n"
        "</configuration>\n"
    )
    return path




def everyday_env(state, paths):
    """The environment of `dotnet-purifier dotnet` and of prefetch: the
    locked SDK first on PATH, and every restore reading only the locked
    packages."""
    dotnet = f"{paths['sdk']}/bin/dotnet"
    env = with_sdk_on_path(dict(os.environ), dotnet)
    # MSBuild reads environment variables as properties, so every restore
    # that dotnet runs, explicit or implicit, reads only the locked
    # packages; NuGet takes a package folder as a feed.
    env["RestoreSources"] = paths["packageFolder"]
    env["NuGetAudit"] = "false"
    # Nix checked every package against the SHA-512 that the lock run
    # recorded, after NuGet had verified the package's signature in that
    # run. Verifying the signature again needs the network, for revocation
    # checks.
    env["DOTNET_NUGET_SIGNATURE_VERIFICATION"] = "false"
    env["DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE"] = "true"
    reference_packs_only(env, state / "packs", dotnet)
    # A self-contained build runs on the runtime NuGet installed, which
    # loads ICU and OpenSSL from the system; the Nix build's wrapper gives
    # it the same libraries.
    libraries = f"{paths['runtimeLibraries']}/lib"
    env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [libraries, env.get("LD_LIBRARY_PATH")]))
    return env


def prefetch(args):
    """Realizes what the development loop and an editor need, with garbage
    collector roots in .dotnet-purifier/ beside the lockfile, and installs
    it, so that both
    work with the network cut off."""
    if args:
        fail(f"prefetch takes no arguments, and was given {' '.join(args)}")
    root = build_root()
    data = read_lock(root)
    prefetch_paths(root, data)
    info("prefetched: `dotnet-purifier dotnet` and editors now work with no network")


def prefetch_paths(root, data):
    state = state_dir(root)
    state.mkdir(exist_ok=True)
    paths = {
        name: nix_build(interactive_expr(root, name), out_link=state / link)[0]
        for name, link in [("packageFolder", "packages"), ("runtimeLibraries", "runtime-libraries")]
    }
    # The SDK has a man output too, which nix build prints in no fixed
    # order beside the one with bin/dotnet.
    sdk_outputs = nix_build(interactive_expr(root, "sdk"), out_link=state / "sdk")
    paths["sdk"] = next((p for p in sdk_outputs if (Path(p) / "bin" / "dotnet").exists()), sdk_outputs[0])
    env = everyday_env(state, paths)
    dotnet = f"{paths['sdk']}/bin/dotnet"
    install(paths["packageFolder"], user_package_folder(env))
    if data["tools"]:
        config = locked_config(state / "nuget.config", paths["packageFolder"])
        run(dotnet, ["tool", "restore", "--configfile", str(config)], root, env)
    if data["workloads"]:
        command = ["workload", "install", *data["workloads"], "--skip-manifest-update"]
        run(dotnet, [*command, "--source", paths["packageFolder"]], root, env)
    # An editor reads what a restore writes under obj/.
    for target in data["targets"]:
        run(dotnet, ["restore", target], root, env)
    (state / "prefetched.json").write_text(json.dumps({"contentHash": data["contentHash"], **paths}, indent=2) + "\n")
    return paths


def prefetched(root, data):
    """The paths the last prefetch realized, when it prefetched this lock
    and they are still in the store, and None otherwise."""
    try:
        record = json.loads((state_dir(root) / "prefetched.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    paths = {k: v for k, v in record.items() if k != "contentHash"}
    if record.get("contentHash") != data["contentHash"] or not all(Path(p).exists() for p in paths.values()):
        return None
    return paths


def missing_packages(root, since):
    """The packages that restores since `since` could not find, from the
    errors NuGet records in each project.assets.json."""
    missing = set()
    for path in walk_all(root, "project.assets.json"):
        if path.stat().st_mtime < since:
            continue
        try:
            logs = json.loads(path.read_text()).get("logs", [])
        except (OSError, json.JSONDecodeError):
            continue
        for entry in logs:
            if entry.get("code") in ("NU1101", "NU1102", "NU1103"):
                missing.add(entry.get("libraryId", "?"))
    return sorted(missing)


def walk_all(root, name):
    """Every file named `name` below root, obj/ directories included."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", STATE_DIR, "node_modules")]
        if name in filenames:
            yield Path(dirpath) / name


def run_dotnet(args):
    """Runs dotnet with the locked packages, from the last prefetch, or
    from a new one when the last prefetch was of another lock."""
    root = Path.cwd()
    if not lock_path(root).exists():
        info('warning: no dotnet-purifier.lock.json found; running dotnet impurely. Run `dotnet-purifier lock` to use Nix-resolved dependencies.')
        dotnet = os.environ.get("DOTNET_PURIFIER_DOTNET") or shutil.which("dotnet")
        if not dotnet:
            dotnet = f"{sdk(sdk_major(root))}/bin/dotnet"
        os.execvp(dotnet, [dotnet, *args])
    root = build_root()
    data = read_lock(root)
    state = state_dir(root)
    paths = prefetched(root, data)
    if paths is None:
        info(f"prefetching for {lock_path(root).name}")
        paths = prefetch_paths(root, data)
    env = everyday_env(state, paths)
    # A workload install reads its packs from the locked packages, with the
    # manifests the SDK ships with, as in the lock run.
    if args[:2] in (["workload", "restore"], ["workload", "install"]) and "--source" not in args:
        args = [*args, "--skip-manifest-update", "--source", paths["packageFolder"]]
    if args[:2] == ["tool", "restore"] and "--configfile" not in args:
        args = [*args, "--configfile", str(locked_config(state / "nuget.config", paths["packageFolder"]))]
    since = time.time()
    result = subprocess.run([f"{paths['sdk']}/bin/dotnet", *args], env=env, check=False)
    if result.returncode != 0:
        missing = missing_packages(root, since)
        if missing:
            info(f"the lock does not hold {', '.join(missing)}: run `dotnet-purifier lock`, and commit the lockfile")
    sys.exit(result.returncode)


def update(args):
    """Updates the build's package references with the SDK's own
    `dotnet package update`, every package or the named ones, in the build
    itself and with the network, then relocks against the result."""
    root = build_root()
    path = lock_path(root)
    targets = json.loads(path.read_text()).get("targets") if path.is_file() else None
    targets = targets or default_targets(root)
    dotnet = f"{sdk(sdk_major(root))}/bin/dotnet"
    env = with_sdk_on_path(dict(os.environ), dotnet)
    env["DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE"] = "true"
    # `dotnet package update` restores, which rewrites the build's
    # packages.lock.json files. With a tool lockfile directory, the build
    # keeps its own, and the lock run writes the new ones there.
    kept = {} if tool_lock_dir(root) == root else {p: p.read_bytes() for p in nuget_locks(root).values()}
    for target in targets:
        run(dotnet, ["package", "update", *args, "--project", target], root, env)
    for path, content in kept.items():
        path.write_bytes(content)
    # A project in NuGet's lock mode records the new versions in its
    # packages.lock.json only when a restore evaluates it again.
    lock(["--force", *targets], evaluate=True)


def publish(args):
    """Uploads the release that Nix built: the packages of the packable
    projects, with `dotnet nuget push` and the given arguments."""
    root = build_root()
    data = read_lock(root)
    if not any(p["kind"] == "package" for p in data["projects"]):
        fail("the build has no packable projects, so it has no release to publish")
    dotnet = f"{sdk(data['sdk']['major'])}/bin/dotnet"
    release = nix_build(interactive_expr(root, "release"))[0]
    for nupkg in sorted(Path(release).glob("*.nupkg")):
        info(f"publishing {nupkg.name}")
        result = subprocess.run([dotnet, "nuget", "push", str(nupkg), *args], check=False)
        if result.returncode != 0:
            fail(f"publishing {nupkg.name} failed")


def path_option(args, flag, variable):
    """The path that `flag` gives, which it removes from args, or else the
    path the environment variable gives, or None."""
    if flag in args:
        i = args.index(flag)
        if i + 1 >= len(args):
            fail(f"{flag} takes a path")
        path = Path(args[i + 1]).resolve()
        del args[i : i + 2]
        return path
    if os.environ.get(variable):
        return Path(os.environ[variable]).resolve()
    return None


def main():
    global LOCK_OVERRIDE, TOOL_LOCK_OVERRIDE
    # On macOS, Nix unsets TMPDIR when it lies in /var/folders, where macOS
    # puts every user's, so this command has none when `nix run` or
    # `nix shell` starts it. Without TMPDIR the .NET runtime of macOS
    # cannot create a named mutex, and the SDK fails before it runs a
    # command, so every dotnet gets the directory Python's temporary files
    # use.
    if sys.platform == "darwin":
        os.environ.setdefault("TMPDIR", tempfile.gettempdir())
    args = sys.argv[1:]
    LOCK_OVERRIDE = path_option(args, "--lockfile", "DOTNET_PURIFIER_LOCKFILE")
    TOOL_LOCK_OVERRIDE = path_option(args, "--tool-lockfile", "DOTNET_PURIFIER_TOOL_LOCKFILE")
    commands = {"lock": lock, "update": update, "prefetch": prefetch, "publish": publish, "dotnet": run_dotnet}
    if not args or args[0] not in commands:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    commands[args[0]](args[1:])


if __name__ == "__main__":
    main()
