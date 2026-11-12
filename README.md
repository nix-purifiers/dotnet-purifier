# dotnet-purifier

dotnet-purifier keeps .NET's development workflow on locked,
Nix-resolved dependencies. Use the normal build and test commands in your
working tree; building the project itself in the Nix sandbox is an
optional next step.

## Quickstart

From the project root:

```zsh
nix shell 'github:nix-purifiers/dotnet-purifier'
dotnet-purifier lock
dotnet-purifier dotnet build
dotnet-purifier dotnet test
```

Commit the lockfiles that the lock command writes, including the build
tool's own lockfile when it has one. Keep using the project's usual build,
test and editor commands with this setup. Nix provides the locked
dependencies; the development commands above run outside the Nix sandbox.
No project Nix files are needed for this workflow.

Sandboxed builds of the whole project are an optional
[next step](#building-the-project-with-nix).

The required wrapper is `dotnet-purifier dotnet`; pass your usual tool
arguments after it. With no purifier lockfile, it warns on standard error
and runs the ordinary tool with impure resolution.

For persistent use, add these packages through your usual Nix configuration.


## Development loop

Run `dotnet` through the purifier, with the locked packages:

```zsh
nix shell 'github:nix-purifiers/dotnet-purifier'
dotnet-purifier dotnet build
dotnet-purifier dotnet test
dotnet-purifier dotnet run \
  --project src/App
```

Every restore reads only the locked packages, with the locked SDK and the
runtime of every target framework of the build, so `dotnet test` runs each
framework's tests. `dotnet tool restore` and `dotnet workload restore` read
the locked packages too.

To add or upgrade a package, edit the project file, or use the SDK's own
commands with the network, then relock:

```zsh
nix shell 'github:nix-purifiers/dotnet-purifier'
# every package
dotnet-purifier update
dotnet-purifier update \
  Newtonsoft.Json
# after editing a project file
dotnet-purifier lock
```

`update` runs `dotnet package update` on each project, refreshes any
`packages.lock.json`, and relocks. `lock` keeps the versions the project
files name, and a project in NuGet's lock mode keeps the resolution its
`packages.lock.json` records. Where that file is missing or no longer
matches the project, `lock` writes it.

For an external build, every command takes `--lockfile <path>`, and the
environment variable `DOTNET_PURIFIER_LOCKFILE` names it too. The option
`--tool-lockfile <directory>`, or the variable
`DOTNET_PURIFIER_TOOL_LOCKFILE`, names the directory of the
`packages.lock.json` files. With either, `lock` and `update` write NuGet's
lockfiles there and leave the files in the tree as they are. A lock into a
directory that does not exist yet starts from the files in the tree. The
dev shell of the flake that calls the builder can set both variables to
the paths it passes to the builder.

dotnet-purifier is a command that runs the SDK, rather than an MSBuild
plugin. A lock run controls the NuGet package folder, the SDK and the
workloads of every restore and build it starts, and the development loop
runs `dotnet` through the same command.

To publish the packages Nix built:

```zsh
nix shell 'github:nix-purifiers/dotnet-purifier'
dotnet-purifier publish \
  --source https://api.nuget.org/v3/index.json --api-key \
  "$NUGET_API_KEY"
```

## Building the project with Nix

Once the native development workflow is working, you can also build and
test the whole project in the Nix sandbox. For this step, keep a flake or
another Nix build definition in the project's repository, or maintain an
external packaging repository that points at its sources and lockfiles.
The builder consumes the same lockfiles as the development workflow.

This builds the project in `App.csproj`. Each project is named after its
project file. The overlay `overlays.default` adds one attribute to a
package set, the scope `dotnet-purifier`, which holds the builder
`buildDotnetProject` and the command.

To package the project in a flake, which can live in the project's
repository or in another one, call the builder in the flake's outputs:

```nix
# flake.nix
{
  inputs.dotnet-purifier.url =
    "github:nix-purifiers/dotnet-purifier";
  inputs.nixpkgs.follows = "dotnet-purifier/nixpkgs";

  outputs =
    { nixpkgs, dotnet-purifier, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ dotnet-purifier.overlays.default ];
      };
      build = pkgs.dotnet-purifier.buildDotnetProject {
        src = ./.;
      };
    in
    {
      packages.${system}.default = build.packages.App;
      checks.${system}.tests = build.tests;
    };
}
```

The builder returns

- `packages.<project>`: an executable is published under `lib/<project>`
  with a command in `bin/` named after its assembly; a packable library is
  its `.nupkg`; any other library is its build output. Each has
  `artifacts."<file>"`, the files the lock records for it, known without
  building.
- `tests`: `dotnet test` of every test project, on the runtime of each
  target framework.
- `release`: the packages of the packable projects, which `publish`
  uploads.
- `fetches`: the download entries the build fetches, for SBOM, license and
  vulnerability tools.

The builder takes `src` and these optional arguments:

- `lockFile` is the lockfile, `src + "/dotnet-purifier.lock.json"` by
  default.
- `packagesLock` is the directory of NuGet's `packages.lock.json` files,
  `src` by default. Each file lies at the path of its project.
- `fetchers` goes to the fetchers of the purifier library, for a company
  mirror or credentials.
- `dotnet-sdk`, `nativeBuildInputs`, `buildInputs`, `srcFilter` and
  `allowStaleLock` are documented in `pkgs/build-dotnet-project/default.nix`.

A flake can instead keep the build external. It keeps the lockfile outside
`src` and passes it as `lockFile`, and it can keep its own copies of the
`packages.lock.json` files in a directory it passes as `packagesLock`. The
build then uses them in place of the files in `src`. This builds a project
whose author does not lock it, or whose committed `packages.lock.json` is
stale or written by another version of the SDK.

The builder takes what it needs from the package set, such as
`dotnetCorePackages` and `stdenv`, so `buildDotnetProject.override`
replaces any of it, and `dotnet-purifier.overrideScope` replaces what the
whole scope shares. `lib.dotnet-purifier.readLock { src, lockFile,
packagesLock }` reads the lock in the shape every purifier shares.

### One-off builds without a project Nix file

For an ad hoc build, the builder can also be evaluated directly from the
project root. Replace the example package name with one from your project:

```zsh
nix build --impure --expr 'let
  purifier = builtins.getFlake "github:nix-purifiers/dotnet-purifier";
  pkgs = import purifier.inputs.nixpkgs {
    system = builtins.currentSystem;
    overlays = [ purifier.overlays.default ];
  };
in
(pkgs.dotnet-purifier.buildDotnetProject { src = ./.; })
.packages.App'
```

This uses the purifier's pinned nixpkgs, matching the lock command above.
To build with ambient `<nixpkgs>` from your Nix search path instead,
replace `import purifier.inputs.nixpkgs` with `import <nixpkgs>`.
Both forms need no Nix files in your project. The build tool and language
versions must match the lockfile; with ambient nixpkgs, lock with the
purifier from that package set or pass a compatible toolchain to the builder.

## Working offline

```zsh
nix shell 'github:nix-purifiers/dotnet-purifier'
dotnet-purifier prefetch
```

`prefetch` builds the locked packages, the SDK with every runtime the build
targets and the libraries a self-contained runtime loads. It keeps them
with garbage-collector roots in the `.dotnet-purifier/` directory next to
the lockfile. It installs the packages
into your NuGet package folder, restores the local tools and the
workloads, and restores every project. Afterwards `dotnet-purifier dotnet`
uses those store paths without evaluating Nix, so the development loop works
with the network cut off. When the lock changes, the next
`dotnet-purifier dotnet` prefetches again.

## Editor

After a prefetch, the packages are in your NuGet package folder and every
project is restored, so a language server that reads a project through
MSBuild (the Roslyn language server, csharp-ls or OmniSharp) works with no
network. Run it with the locked SDK, which `prefetch` links at
`.dotnet-purifier/sdk` next to the lockfile: put `.dotnet-purifier/sdk/bin`
first on its `PATH` and set `DOTNET_ROOT` to
`.dotnet-purifier/sdk/share/dotnet`. Navigation into
a package's code decompiles it locally; source files that SourceLink
downloads need the network.

## Troubleshooting

| Message | Fix |
| --- | --- |
| `… does not match the build: a project, solution, .props or .targets file … changed` | `dotnet-purifier lock`, then commit the lockfile. A Nix build can pass `allowStaleLock = true` instead. |
| `… was edited after dotnet-purifier lock wrote it` | `dotnet-purifier lock --force`. |
| `… has format …` or `… lacks …, so it is not a lockfile dotnet-purifier lock wrote` | `dotnet-purifier lock --force`. |
| `the lock does not hold <package>: run dotnet-purifier lock` | A restore asked for a package the lock does not hold; relock. |
| `… dotnet publish failed. If NuGet could not find a package …` (in a Nix build) | The same, from a Nix build; relock. |
| `… has the hash …, and the lock records …: the package changed` | A package of a folder feed in the tree changed; relock. |
| `… no longer records …, whose hash … reads from it` | A `packages.lock.json` changed; relock. |

## Supported language repositories

The lock records packages from these sources, each by URL and hash:

- **NuGet V3 feeds** of the build's `nuget.config`, with package source
  mapping. Each package's URL is its feed's flat-container URL.
- **Folder feeds inside the build's tree**, recorded by file and hash.
- **MSBuild SDK packages**, such as `Microsoft.Build.NoTargets`.
- **Local tools** of `.config/dotnet-tools.json`, which a build may run.
- **Runtime, host and targeting packs.** The lock takes them from NuGet,
  so one lock serves x86_64-linux, aarch64-linux and aarch64-darwin.
- **SDK workloads**, such as `wasm-tools`. The lock records their packs for
  every machine, and a Nix build installs them offline.
- **The folder feed inside the SDK** (`library-packs`). The lock takes
  these packages from a feed when a feed serves them, so every machine gets
  the same file.
- **The lock mode of NuGet.** When a `packages.lock.json` records a package
  with the hash of its file, the lockfile reads the hash from it. NuGet
  hashes a signed package without its signature, so for signed packages the
  lockfile keeps the hash of the file. Every package on nuget.org is
  signed.

A lock run stops, naming the reason, on these:

- **Feeds outside NuGet V3**, and folder feeds outside the build's tree:
  the lock records a package by URL or by a file Nix can read.
- **Feeds that need credentials**: the lock run cannot authenticate yet. A
  Nix build can fetch through `fetchers.fetchurl`.
- **Workloads that need tools outside NuGet.** The Android workload needs
  the Android SDK and a JDK, which it finds on the machine; a build would
  have to provide them through `nativeBuildInputs`. The iOS, macOS and MAUI
  workloads need Xcode and build only on macOS.
- **Windows and macOS target frameworks.** They need their platform's SDK,
  which no Nix build has.

Other limits: a lock is made with the nixpkgs SDK of the major version
`global.json` names (10 when there is none), and a Nix build uses the SDK of
that major version from your nixpkgs, warning when the patch version
differs. Workload tools run interactively on NixOS with `programs.nix-ld`.

## Examples

For day-to-day work on these projects, use the native-tool setup and
commands in [Quickstart](#quickstart) and [Development loop](#development-loop).
The Nix expressions below show optional sandboxed packaging of those
projects.

[FluentValidation](https://github.com/FluentValidation/FluentValidation) is
a .NET validation library with a solution of packable libraries, a
benchmark executable and a test project. To lock it, build it with Nix and
work on it with the locked packages, clone it and run these commands in the
clone:

```zsh
nix shell 'github:nix-purifiers/dotnet-purifier'
git clone https://github.com/FluentValidation/FluentValidation
cd FluentValidation
dotnet-purifier lock
nix build --impure --expr 'let
  purifier = builtins.getFlake "github:nix-purifiers/dotnet-purifier";
  pkgs = import purifier.inputs.nixpkgs {
    system = builtins.currentSystem;
    overlays = [ purifier.overlays.default ];
  };
in
(pkgs.dotnet-purifier.buildDotnetProject { src = ./.; })
.packages.FluentValidation'
dotnet-purifier dotnet test
```

The build gives the package of the main library. Its tests are the
attribute `tests` of the same builder result, which runs them on every
framework the test project targets.

FluentValidation does not commit a dotnet-purifier lockfile. To keep the
lockfile in a flake of your own instead, run `lock` with
`--lockfile <path>` and pass the file to the builder as `lockFile`, with
FluentValidation as the `src` of the flake.

## Credits

The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo).

### NativeAOT shared-library projections

A class library which declares `PublishAot=true` and `NativeLib=Shared` can
be selected explicitly after capture:

```nix
let project = buildDotnetProject {
  src = ./my-project;
  dotnet-sdk = myImmutableSdk;
  nativeProducts.answer = {
    project = "Answer";
    exports = [ "native_answer" ];
  };
}; in project.projections.answer
```

Capture performs the ordinary managed build or NuGet pack and a separate
native publish. The lock records the native output and captures native
compiler and target-runtime packages for the supported SDK hosts. The
ordinary `packages` and `release` outputs retain their existing meaning;
`nativePackages.<name>` is the selected native producer. The `native`,
`python-ffi`, and `java-ffi` projections use that producer's actual shared
library. The selected immutable SDK drives project compilation and the
metadata inspector, including the project's `global.json` SDK selection.

Each advertised entrypoint must belong to the selected managed PE assembly,
have an `UnmanagedCallersOnly` export declaration, and be defined by the
native binary for the selected platform. The producer retains the managed
input, native compiler response files, and original native publish tree.
Original and packaged native-library hashes distinguish any Nix relocation.

Fixed-width scalar and unmanaged pointer signatures also provide derived C
headers and CFFI declarations under `include/`, plus a pkg-config file under
`lib/pkgconfig/`. Aggregate layouts and other signatures remain explicit PE
metadata; `headerUnsupportedExports` identifies exports without an automatic
header. Consumers supply compatible signatures and marshaling. No managed
DLL is treated as a native library.

NativeAOT initializes its runtime through native library loading and entry
stubs. Keep libraries loaded for process lifetime: unloading is unsupported.
The checks exercise independent source revisions, a captured NuGet library,
managed allocation and garbage collection, C/C++ callers, ctypes/CFFI, and
Java FFM through the published Java library path.

### Browser WebAssembly resources

Select captured browser projects explicitly when building:

```nix
(buildDotnetProject {
  src = ./my-project;
  browserTargets.MyBrowserApp.webjar = {
    groupId = "org.example";
    artifactId = "my-browser-app";
    version = "1.0.0";
  };
}).projections.MyBrowserApp
```

The result provides `web`, `resources`, and `java-resources` views of the
complete native `dotnet publish` `wwwroot` tree, preserving the loader,
managed WebAssembly assemblies, runtime, native relinked modules, compressed
variants, and their relative paths. `web.documentRoot` is the serving root;
`java-resources.classPath` is its resource JAR. Optional `webjar` coordinates
also provide `maven` and `ivy` repositories. Use an empty target set value
(`browserTargets.MyBrowserApp = { };`) for resource views alone.

Only captured `browser-*` executable projects qualify. WASI executables and
managed desktop executables do not expose this browser projection. The
projection preserves the application's own published assets; it does not
supply an HTML entrypoint or rewrite boot manifests. The existing raw
`packages` outputs remain available. Explicit `dotnet-sdk` selection applies
to the producer of every view.

Sandboxed WebAssembly publish also supplies `SOURCE_DATE_EPOCH` to native
MSBuild static-asset endpoint generation. This makes the generated HTTP
`Last-Modified` headers reproducible. The native task serializes its own
manifest; published manifests and browser asset bodies are not rewritten.
Existing environment-selected common targets are imported first, and explicit
project overrides of `CustomAfterMicrosoftCommonTargets` retain precedence.

Capture and sandboxed workload builds use a private SDK home. When the
project does not declare `sdk.workloadVersion` in `global.json`, SDKs that
support workload sets are configured through their native CLI to use bundled
manifests. This keeps capture consistent with the manifests used by the pure
builder, even when newer workload sets appear on the feed. The capability is
detected from the selected SDK, without a runtime-version allowlist. Explicit
project workload-set declarations retain precedence; capturing their separate
downloaded manifest set remains an open integration case.
