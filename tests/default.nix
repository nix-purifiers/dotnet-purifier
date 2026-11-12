# SPDX-License-Identifier: MIT
#
# The flake's checks. Every one builds in the sandbox; the only network
# access is the fixed-output fetches of locked packages and pinned sources.
{
  pkgs,
  # The flake's default package overlay, which adds the dotnet-purifier
  # scope.
  overlay,
}:
let
  inherit (pkgs) lib;

  # Every fixture builds the way a consumer's package does: through
  # the builder of the scope in a package set the overlay applies to.
  build = (pkgs.extend overlay).dotnet-purifier.buildDotnetProject;

  # An executable that depends on a library of the same build, a packable
  # library and an xunit test project, both multi-targeted, with packages
  # from nuget.org.
  multi = build { src = ./fixtures/multi; };

  # Packages from a second feed of nuget.config with package source
  # mapping, from a folder feed in the build's tree and from an MSBuild SDK
  # package; a build that runs a local tool; a self-contained executable
  # for linux-x64 with the runtime pack of that platform; and an ASP.NET
  # Core executable.
  sources = build { src = ./fixtures/sources; };

  # A WebAssembly executable relinked with Emscripten, from the packs of
  # the wasm-tools workload.
  workloads = build { src = ./fixtures/workloads; };

  # FluentValidation at a pinned commit, a real-world project its authors
  # do not lock with dotnet-purifier: its lockfile lives here, outside its
  # source.
  fluentvalidation = build {
    src = pkgs.fetchFromGitHub {
      owner = "FluentValidation";
      repo = "FluentValidation";
      rev = "f95dbd5c8166a10e38f7a8bca4d752abd641a453";
      hash = "sha256-HNc7HxW4gLvOVVSY6yo3+NEOjNsIojemcj1dZ/KZhUM=";
    };
    lockFile = ./real-world/fluentvalidation/dotnet-purifier.lock.json;
  };

  # A lockfile changed by `changes`, with its content hash computed again,
  # so that it fails only for the change.
  multiLock = lib.importJSON ./fixtures/multi/dotnet-purifier.lock.json;
  sourcesLock = lib.importJSON ./fixtures/sources/dotnet-purifier.lock.json;
  # A hash of some package, which no entry of another package has.
  someHash = (lib.findFirst (a: a ? hash) null multiLock.artifacts).hash;
  seal =
    lock:
    lock
    // {
      contentHash = builtins.convertHash {
        hash = builtins.hashString "sha256" (builtins.toJSON (removeAttrs lock [ "contentHash" ]));
        hashAlgo = "sha256";
        toHashFormat = "sri";
      };
    };
  lockFile = name: lock: builtins.toFile "${name}.lock.json" (builtins.toJSON lock);
  multiWith =
    name: lock: args:
    build (
      {
        src = ./fixtures/multi;
        lockFile = lockFile name lock;
      }
      // args
    );

  # The build definition changed since the lock was made.
  staleLock = seal (
    multiLock // { definitionHash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="; }
  );
  stale = multiWith "stale" staleLock { };
  staleAllowed = multiWith "stale" staleLock { allowStaleLock = true; };
  # A package's hash was edited by hand, without a relock.
  tampered = multiWith "tampered" (
    multiLock
    // {
      artifacts = map (a: removeAttrs a [ "hashFrom" ] // { hash = someHash; }) multiLock.artifacts;
    }
  ) { };
  # A lockfile of another format, and one that lacks a field.
  otherFormat = multiWith "other-format" (seal (multiLock // { version = 2; })) { };
  malformed = multiWith "malformed" (seal (removeAttrs multiLock [ "artifacts" ])) { };
  # The lock lacks a package the build needs: Humanizer.Core, which the
  # Greeter library references.
  missing = multiWith "missing" (seal (
    multiLock
    // {
      artifacts = lib.filter (a: !(lib.hasPrefix "humanizer.core/" a.path)) multiLock.artifacts;
    }
  )) { };
  # The package of the folder feed in the build differs from the one the
  # lock records.
  tamperedLocal = build {
    src = ./fixtures/sources;
    lockFile = lockFile "tampered-local" (
      seal (
        sourcesLock
        // {
          localPackages = map (
            p: removeAttrs p [ "hashFrom" ] // { hash = someHash; }
          ) sourcesLock.localPackages;
        }
      )
    );
  };
  # The multi fixture without its lockfile, built from the one kept beside
  # it.
  external = build {
    src = lib.cleanSourceWith {
      src = ./fixtures/multi;
      filter = path: _type: baseNameOf path != "dotnet-purifier.lock.json";
    };
    lockFile = ./fixtures/multi/dotnet-purifier.lock.json;
  };

  # The same, with NuGet's lockfile of the App project also kept outside
  # `src`, in a directory that holds it at the project's path.
  externalPackagesLock = build {
    src = lib.cleanSourceWith {
      src = ./fixtures/multi;
      filter =
        path: _type:
        !(lib.elem (baseNameOf path) [
          "dotnet-purifier.lock.json"
          "packages.lock.json"
        ]);
    };
    lockFile = ./fixtures/multi/dotnet-purifier.lock.json;
    packagesLock = lib.fileset.toSource {
      root = ./fixtures/multi;
      fileset = ./fixtures/multi/src/App/packages.lock.json;
    };
  };

  # True when evaluating the package names of a build fails, as a throw.
  evaluationFails =
    build: !(builtins.tryEval (builtins.deepSeq (builtins.attrNames build.packages) true)).success;

  # A check that passes when `condition` holds and fails evaluation with
  # `message` otherwise.
  expect =
    name: condition: message:
    if condition then pkgs.runCommand name { } "touch $out" else throw "${name}: ${message}";

  # A check that the build of `drv` fails, with `text` in its log.
  failsWith =
    name: drv: text:
    pkgs.runCommand name { failure = pkgs.testers.testBuildFailure drv; } ''
      grep -F ${lib.escapeShellArg text} "$failure/testBuildFailure.log"
      touch "$out"
    '';

  # Fails unless command $1 prints $2.
  prints =
    name: command: expected:
    pkgs.runCommand name { } ''
      output="$(${command})"
      if [ "$output" != ${lib.escapeShellArg expected} ]; then
        echo "printed $output, expected "${lib.escapeShellArg expected} >&2
        exit 1
      fi
      touch "$out"
    '';
in
{
  # The lock tool's parsing and path logic.
  unit-tests =
    pkgs.runCommand "unit-tests"
      {
        nativeBuildInputs = [ pkgs.python3 ];
        DOTNET_PURIFIER_CLI = ../cli/dotnet_purifier.py;
        DOTNET_PURIFIER_WORKLOAD_MODE_HELPER = ../pkgs/dotnet-purifier/workload-mode.py;
      }
      ''
        python3 -m unittest discover -s ${./unit} -v
        touch "$out"
      '';

  # A lockfile older than the project fails evaluation, unless the build
  # allows it.
  conformance-stale = expect "conformance-stale" (evaluationFails stale) "a stale lock evaluated";
  stale-lock-allowed = staleAllowed.packages.App;

  # A lockfile edited by hand fails evaluation.
  conformance-tampered =
    expect "conformance-tampered" (evaluationFails tampered)
      "a lockfile with an edited hash evaluated";
  # A package of a folder feed that changed since the lock fails the build.
  tampered-local-package-fails =
    failsWith "tampered-local-package-fails" tamperedLocal.packageFolder
      "the package changed since `dotnet-purifier lock` ran";

  # A package missing from the lock fails the build, which says what to run.
  conformance-missing =
    failsWith "conformance-missing" missing.packages.Greeter
      "does not hold: run `dotnet-purifier lock`";

  # A lockfile of another format or without a field fails evaluation.
  conformance-malformed = expect "conformance-malformed" (
    evaluationFails malformed && evaluationFails otherFormat
  ) "a lockfile without artifacts, or of format 2, evaluated";

  # A build whose source holds no lockfile, from one kept outside it.
  conformance-external = external.packages.App;
  conformance-external-packages-lock = externalPackagesLock.packages.App;

  multi-app = multi.packages.App;
  multi-greeter = multi.packages.Greeter;
  multi-tests = multi.tests;

  # The executable runs on the .NET runtime and reaches both its project
  # reference and its NuGet dependency.
  multi-app-runs = prints "multi-app-runs" "${
    multi.packages.App.artifacts."bin/greet"
  } Ada" ''{"greeting":"Hello, Ada! You have 3 messages."}'';

  # The packable library's package lies where the lock says, and it is the
  # release.
  multi-release = pkgs.runCommand "multi-release" { } ''
    test -f ${multi.packages.Greeter.artifacts."Example.Greeter.1.0.0.nupkg"}
    test -f ${multi.release}/Example.Greeter.1.0.0.nupkg
    touch "$out"
  '';

  # `dotnet nuget push` takes the release Nix built, and a project that
  # depends on it restores it from where it was pushed.
  multi-release-publishes =
    pkgs.runCommand "multi-release-publishes"
      {
        nativeBuildInputs = [ pkgs.dotnetCorePackages.sdk_10_0 ];
        dontConfigureNuget = true;
      }
      ''
        export HOME="$TMPDIR/home" DOTNET_CLI_HOME="$TMPDIR/home" XDG_RUNTIME_DIR="$TMPDIR/run"
        export NUGET_PACKAGES="$TMPDIR/packages" DOTNET_NOLOGO=1 DOTNET_CLI_TELEMETRY_OPTOUT=1
        export DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=true DOTNET_NUGET_SIGNATURE_VERIFICATION=false
        mkdir -p "$HOME" "$XDG_RUNTIME_DIR" feed consumer
        chmod 700 "$XDG_RUNTIME_DIR"
        dotnet nuget push ${multi.release}/Example.Greeter.1.0.0.nupkg --source "$PWD/feed"
        cat > consumer/Consumer.csproj <<'EOF'
        <Project Sdk="Microsoft.NET.Sdk">
          <PropertyGroup>
            <OutputType>Exe</OutputType>
            <TargetFramework>net10.0</TargetFramework>
            <UseAppHost>false</UseAppHost>
            <NuGetAudit>false</NuGetAudit>
          </PropertyGroup>
          <ItemGroup>
            <PackageReference Include="Example.Greeter" Version="1.0.0" />
          </ItemGroup>
        </Project>
        EOF
        echo 'System.Console.WriteLine(Example.Greeter.Greeting.For("Grace", 1));' > consumer/Program.cs
        dotnet build consumer -p:RestoreSources="$PWD/feed;${multi.packageFolder}" -o out
        output="$(dotnet out/Consumer.dll)"
        test "$output" = "Hello, Grace! You have 1 message."
        touch "$out"
      '';

  # Lint's build ran the local tool, from an MSBuild SDK package project.
  sources-lint = sources.packages.Lint;

  sources-web = sources.packages.Web;

  # The ASP.NET Core executable runs on the ASP.NET Core runtime.
  sources-web-runs = prints "sources-web-runs" "${
    sources.packages.Web.artifacts."bin/web"
  } --describe" "application: web";

  native-ffi = import ./native.nix { inherit pkgs build; };
  native-multi = import ./native-multi.nix { inherit pkgs build; };
  native-negative = import ./native-negative.nix { inherit pkgs build; };

  browser-boundaries = import ./browser-boundaries.nix { inherit pkgs; };
  browser-projections = import ./browser.nix { inherit pkgs build; };
  workloads-wasm = workloads.packages.Wasm;

  # The workload's Emscripten relinked the native runtime: the build
  # produced its own dotnet.native.wasm rather than taking the one in the
  # runtime pack.
  workloads-wasm-relinked = pkgs.runCommand "workloads-wasm-relinked" { } ''
    framework=${workloads.packages.Wasm.artifacts."lib/Wasm/wwwroot"}/_framework
    ls "$framework"/dotnet.native*.wasm
    touch "$out"
  '';

  # The real-world project: its packages, its benchmark executable, and its
  # tests on net8.0, net9.0 and net10.0.
  realworld-fluentvalidation = fluentvalidation.release;
  realworld-fluentvalidation-benchmarks =
    fluentvalidation.packages."FluentValidation.Tests.Benchmarks";
  realworld-fluentvalidation-tests = fluentvalidation.tests;
}
// lib.optionalAttrs (pkgs.stdenv.hostPlatform.system == "x86_64-linux") {
  sources-standalone = sources.packages.Standalone;

  # The self-contained executable runs on the runtime it carries, and
  # reaches the packages of the second feed and of the folder feed.
  sources-standalone-runs =
    prints "sources-standalone-runs" (sources.packages.Standalone.artifacts."bin/standalone")
      ''{"origin":"the local folder feed"}'';
}
