# SPDX-License-Identifier: MIT
#
# buildDotnetProject: sandboxed builds of a .NET build, from the
# dotnet-purifier.lock.json at its root. The build needs no Nix files of
# its own: the lock names every NuGet package the build installs, with its
# hash, and every project of the build with its kind.
#
# The file is a package for callPackage: the first argument set is what it
# takes from the package set, and the result is the builder. The
# dotnet-purifier overlay (`overlays.default`) adds it to the scope
# `pkgs.dotnet-purifier` as `buildDotnetProject`, so a package written for
# callPackage takes the scope like any other dependency:
#
#   { dotnet-purifier }:
#   (dotnet-purifier.buildDotnetProject { src = ./.; }).packages.App
#
# and `buildDotnetProject.override` replaces what it takes from the set.
# The builder returns
#
#   packages.<name>  project <name>, named after its project file:
#                    an executable project is published under lib/<name>,
#                    with a command in bin/ named after its assembly; a
#                    packable library is its .nupkg, the file
#                    `dotnet nuget push` uploads; any other library is its
#                    build output under lib/<name>. Its attribute
#                    `artifacts."<file>"` is each file the lock names for
#                    the project, a path into its output known without
#                    building.
#   tests            the test run of the build: `dotnet test` of every test
#                    project, with each project's run as tests.projects.<name>
#   release          the packages of the packable projects, which
#                    `dotnet-purifier publish` uploads
#   fetches          the download entries the build fetches, as the
#                    purifier library takes them
#   packageFolder    the locked packages installed in a NuGet package folder
#   sdk              the locked SDK with the runtime of every target framework
#   runtimeLibraries the libraries a self-contained .NET runtime loads at
#                    run time, which the wrapper of a self-contained
#                    executable puts on LD_LIBRARY_PATH
#
# Every build runs the command that `dotnet-purifier lock` ran for the
# project's kind (`build_commands` in cli/dotnet_purifier.py), with the
# same MSBuild properties and the build's own NuGet configuration, so it
# asks for what the lock run installed.
{
  lib,
  stdenv,
  callPackage,
  fetchurl,
  runCommand,
  unzip,
  zstd,
  linkFarm,
  symlinkJoin,
  # The shared purifier library (`lib.purifier` of
  # github:nix-purifiers/purifier).
  purifier,
  dotnetCorePackages,
  makeWrapper,
  writeText,
  autoPatchelfHook,
  python3,
  icu,
  openssl,
  zlib,
  krb5,
}:
{
  # The root of the .NET build.
  src,
  # The lockfile; the default is the one dotnet-purifier writes at the
  # build root. A lockfile kept outside `src` lets a packaging flake build
  # a project whose author does not lock it, and it replaces any lockfile
  # in `src`.
  lockFile ? src + "/dotnet-purifier.lock.json",
  # The directory of NuGet's packages.lock.json files, at the paths of
  # their projects. A directory kept outside `src` lets a packaging flake
  # keep its own copies, which the build uses in place of those in `src`.
  packagesLock ? src,
  # The .NET SDK. The default is the nixpkgs SDK of the major version the
  # lock was made with.
  dotnet-sdk ? null,
  # Tools the build needs beyond the SDK.
  nativeBuildInputs ? [ ],
  # Libraries the build links against.
  buildInputs ? [ ],
  # Fetchers for the purifier library's fetchEntry and tree, such as a
  # `fetchurl` that reads a company mirror or passes credentials.
  fetchers ? { },
  # Filters the build root before it is copied into the store, as the
  # `filter` of builtins.path. The default leaves out build outputs,
  # version control, the purifier's state and Nix result links.
  srcFilter ? null,
  # Builds with a lock that no longer matches the build definition, rather
  # than failing evaluation. A restore that asks for a package the lock
  # does not hold still fails.
  allowStaleLock ? false,
}:
let
  # The lock in memory (lock.nix), which checkLock of the purifier library
  # fails with its problems, each naming the command that fixes it.
  lock = purifier.checkLock (
    import ./lock.nix {
      inherit
        lib
        lockFile
        src
        packagesLock
        ;
      allowStale = allowStaleLock;
    }
  );
  # The downloads this machine needs.
  downloads = purifier.entriesFor stdenv.hostPlatform.system lock;

  lockedSdk = dotnetCorePackages."sdk_${toString lock.sdk.major}_0";
  sdk =
    if dotnet-sdk != null then
      dotnet-sdk
    else if lockedSdk.version != lock.sdk.version then
      lib.warn "dotnet-purifier: ${toString lockFile} was made with the .NET SDK ${lock.sdk.version}, and the build uses ${lockedSdk.version}. A restore that asks for packages the lock does not hold means the lock needs renewing with the same SDK." lockedSdk
    else
      lockedSdk;

  defaultSrcFilter =
    path: type:
    let
      name = baseNameOf path;
    in
    !(
      (
        type == "directory"
        && lib.elem name [
          "bin"
          "obj"
          ".git"
          ".dotnet-purifier"
          ".vs"
        ]
      )
      || lib.hasPrefix "result" name
    );

  filteredSrc = builtins.path {
    path = src;
    name = "dotnet-source";
    filter = if srcFilter != null then srcFilter else defaultSrcFilter;
  };

  # The build's source, with the packages.lock.json files of
  # `packagesLock` in place of its own when that is another directory.
  buildSrc =
    if packagesLock == src then
      filteredSrc
    else
      runCommand "dotnet-source" { } ''
        cp -r --no-preserve=mode ${filteredSrc} "$out"
        find "$out" -iname packages.lock.json -delete
        cd ${packagesLock}
        find . -iname packages.lock.json -exec install -D -m 644 {} "$out/{}" \;
      '';

  # The downloads laid out at their paths by the shared purifier library,
  # each a link to its fixed-output fetch.
  dependencies = purifier.tree (
    {
      inherit
        fetchurl
        runCommand
        unzip
        zstd
        python3
        writeText
        ;
      name = "dotnet-purifier-feed";
      entries = downloads;
    }
    // fetchers
  );

  packageFolder = callPackage ./package-folder.nix {
    inherit dependencies;
    inherit downloads;
    inherit (lock) localPackages sdkPackages;
    inherit sdk;
    src = filteredSrc;
  };

  # A NuGet configuration whose only source is the locked packages, for
  # `dotnet tool restore`, which reads the sources of a configuration
  # rather than `RestoreSources` (`locked_config` in cli/dotnet_purifier.py).
  toolConfig = writeText "nuget.config" ''
    <?xml version="1.0" encoding="utf-8"?>
    <configuration>
      <packageSources>
        <clear />
        <add key="dotnet-purifier" value="${packageFolder}" />
      </packageSources>
      <packageSourceMapping>
        <clear />
      </packageSourceMapping>
    </configuration>
  '';

  # The MSBuild properties of `BUILD_PROPERTIES` and `NO_APPHOST` in
  # cli/dotnet_purifier.py.
  buildProperties = [
    "-p:Configuration=Release"
    "-p:NuGetAudit=false"
  ];
  noApphost = "-p:UseAppHost=false";

  # The setup shared by every build. The build's package folder starts
  # with the locked packages installed, as a tree of links into
  # `packageFolder`. MSBuild reads environment variables as properties, so
  # `RestoreSources` makes every restore read only the locked packages.
  # Nix checked each package against the SHA-512 that the lock run
  # recorded, after NuGet had verified the package's signature in that
  # run; verifying it again needs the network, for revocation checks.
  # `NETCoreTargetingPackRoot` points the SDK's packs at a directory with
  # only the targeting packs (*.Ref) the SDK ships with, as in the lock
  # run, so the SDK takes every runtime and host pack from the locked
  # packages rather than from the packs the SDK ships for the machine it
  # runs on. The targeting packs are the same on every machine.
  dotnetSetup = ''
    # Every dotnet command says, when it fails, what a failure to find a
    # package means here.
    dotnet() {
      command dotnet "$@" || {
        local status=$?
        echo "dotnet-purifier: \`dotnet $1\` failed. If NuGet could not find a package (NU1101, NU1102 or NU1103), the build asks for a package that ${baseNameOf (toString lockFile)} does not hold: run \`dotnet-purifier lock\` at the build root and commit the lockfile." >&2
        return $status
      }
    }
    export HOME="$TMPDIR/home"
    export DOTNET_CLI_HOME="$HOME"
    export XDG_RUNTIME_DIR="$TMPDIR/run"
    export NUGET_PACKAGES="$TMPDIR/nuget-packages"
    export NUGET_HTTP_CACHE_PATH="$TMPDIR/nuget-http-cache"
    export DOTNET_CLI_TELEMETRY_OPTOUT=1
    export DOTNET_NOLOGO=1
    export DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
    export DOTNET_SKIP_WORKLOAD_INTEGRITY_CHECK=1
    export DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=true
    export DOTNET_NUGET_SIGNATURE_VERIFICATION=false
    export MSBUILDDISABLENODEREUSE=1
    export RestoreSources=${packageFolder}
    # MSBuild starts task hosts through the SDK's MSBuild apphost, which
    # finds the runtime only through DOTNET_ROOT.
    export DOTNET_ROOT="$dotnetSdk/share/dotnet"
    export NETCoreTargetingPackRoot="$TMPDIR/packs"
    mkdir -p "$HOME" "$XDG_RUNTIME_DIR" "$NUGET_PACKAGES" "$NETCoreTargetingPackRoot"
    ln -s "$dotnetSdk"/share/dotnet/packs/*.Ref "$NETCoreTargetingPackRoot"/
    chmod 700 "$XDG_RUNTIME_DIR"
    cp -rs --no-preserve=mode ${packageFolder}/. "$NUGET_PACKAGES"
    ${lib.optionalString lock.tools "dotnet tool restore --configfile ${toolConfig}"}
    ${lib.optionalString (lock.workloads != [ ]) ''
      dotnet workload install ${lib.escapeShellArgs lock.workloads} --skip-manifest-update \
        --source ${packageFolder}
      ${lib.optionalString stdenv.hostPlatform.isLinux ''autoPatchelf "$HOME/.dotnet/packs"''}
    ''}
  ''
  + lib.optionalString stdenv.hostPlatform.isDarwin ''
    # The compilers record the paths of the sources and of the PDB in the
    # assemblies they build. Linux builds run in /build, and darwin builds
    # in a directory new for every build, so the paths are mapped to /build
    # and a rebuild gives the same files. MSBuild reads PathMap as the
    # compilers' path map.
    export PathMap="$NIX_BUILD_TOP=/build"
  '';

  # A workload install puts its packs under $HOME/.dotnet, and the native
  # tools among them, such as Emscripten's clang and Node.js, run on Linux
  # once patched; darwin runs them as they come, and patchelf reads only
  # ELF files. Emscripten runs on the system's Python on Linux.
  workloadInputs = lib.optionalAttrs (lock.workloads != [ ]) {
    nativeBuildInputs = lib.optional stdenv.hostPlatform.isLinux autoPatchelfHook ++ [ python3 ];
    buildInputs = lib.optionals stdenv.hostPlatform.isLinux [
      stdenv.cc.cc.lib
      zlib
    ];
  };

  # The major version of a target framework such as net10.0 or
  # net8.0-windows.
  frameworkMajor =
    tfm:
    let
      m = builtins.match "net([0-9]+)\\.[0-9]+.*" tfm;
    in
    if m == null then
      throw "dotnet-purifier: an executable project targets ${tfm}, and dotnet-purifier runs executables only on .NET 5 and newer."
    else
      builtins.head m;

  # The runtime a framework-dependent executable runs on. It is the
  # ASP.NET Core runtime when the project references the ASP.NET Core
  # shared framework, and the plain .NET runtime otherwise.
  runtimeOf =
    p:
    let
      major = frameworkMajor (builtins.head p.targetFrameworks);
    in
    if lib.elem "Microsoft.AspNetCore.App" p.frameworkReferences then
      dotnetCorePackages."aspnetcore_${major}_0"
    else
      dotnetCorePackages."runtime_${major}_0";

  # The libraries a self-contained .NET runtime loads at run time rather
  # than links against.
  runtimeLibraries = [
    icu
    openssl
    krb5
  ];

  mkBuild =
    p: attrs:
    stdenv.mkDerivation (
      {
        pname = p.name;
        version = p.packageVersion or "0";
        src = buildSrc;
        # The SDK of the build: the locked one, or for a test project, the
        # locked one with the runtimes the project's tests run on.
        dotnetSdk = attrs.dotnetSdk or sdk;
        nativeBuildInputs = [
          (attrs.dotnetSdk or sdk)
        ]
        ++ nativeBuildInputs
        ++ (workloadInputs.nativeBuildInputs or [ ])
        ++ (attrs.nativeBuildInputs or [ ]);
        buildInputs = buildInputs ++ (workloadInputs.buildInputs or [ ]) ++ (attrs.buildInputs or [ ]);
        # The build reads the build's own NuGet configuration, as the lock
        # run did, rather than one the SDK's setup hook writes.
        dontConfigureNuget = true;
        dontConfigure = true;
        dontBuild = true;
        # GNU strip reads the PE files .NET assemblies are, and stripping
        # one breaks it.
        dontStrip = true;
        passthru = {
          inherit (p) path kind assemblyName;
        };
      }
      // removeAttrs attrs [
        "nativeBuildInputs"
        "buildInputs"
        "dotnetSdk"
      ]
    );

  publish = p: apphost: ''
    dotnet publish ${lib.escapeShellArg p.path} -f ${lib.escapeShellArg (builtins.head p.targetFrameworks)} \
      ${lib.escapeShellArgs (buildProperties ++ apphost)} -o "$out/lib/${p.name}"
  '';

  # A framework-dependent executable runs on the nixpkgs .NET runtime.
  mkFrameworkDependent =
    p:
    mkBuild p {
      nativeBuildInputs = [ makeWrapper ];
      installPhase = ''
        runHook preInstall
        ${dotnetSetup}
        ${publish p [ noApphost ]}
        makeWrapper ${lib.getExe' (runtimeOf p) "dotnet"} "$out/bin/${p.assemblyName}" \
          --add-flags "$out/lib/${p.name}/${p.assemblyName}.dll"
        runHook postInstall
      '';
      meta.mainProgram = p.assemblyName;
    };

  # A self-contained executable carries its runtime, from the runtime pack
  # of its platform; its apphost starts it.
  mkSelfContained =
    p:
    mkBuild p {
      nativeBuildInputs = [
        makeWrapper
        autoPatchelfHook
      ];
      buildInputs = [
        stdenv.cc.cc.lib
        zlib
      ]
      ++ runtimeLibraries;
      # The runtime's tracing provider links against LTTng, which it loads
      # only when tracing is on.
      autoPatchelfIgnoreMissingDeps = [ "liblttng-ust.so.*" ];
      installPhase = ''
        runHook preInstall
        ${dotnetSetup}
        ${publish p [ ]}
        makeWrapper "$out/lib/${p.name}/${p.assemblyName}" "$out/bin/${p.assemblyName}" \
          --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath runtimeLibraries}
        runHook postInstall
      '';
      meta.mainProgram = p.assemblyName;
    };

  # A WebAssembly executable is the files a web server serves, under
  # lib/<name>/wwwroot.
  mkWebAssembly =
    p:
    mkBuild p {
      installPhase = ''
        runHook preInstall
        ${dotnetSetup}
        ${publish p [ ]}
        runHook postInstall
      '';
    };

  isWebAssembly =
    p: lib.hasPrefix "browser-" p.runtimeIdentifier || lib.hasPrefix "wasi-" p.runtimeIdentifier;

  mkPackage =
    p:
    mkBuild p {
      installPhase = ''
        runHook preInstall
        ${dotnetSetup}
        dotnet pack ${lib.escapeShellArg p.path} ${
          lib.escapeShellArgs (buildProperties ++ [ noApphost ])
        } -o "$out"
        # dotnet pack stamps each entry of a package with the time it ran;
        # the packages get a fixed time instead, so a rebuild gives the
        # same files.
        python3 ${./normalize-nupkg.py} "$out"/*.nupkg "$out"/*.snupkg
        runHook postInstall
      '';
      nativeBuildInputs = [ python3 ];
      # The packages are what NuGet publishes, byte for byte.
      dontFixup = true;
    };

  mkLibrary =
    p:
    mkBuild p {
      installPhase = ''
        runHook preInstall
        ${dotnetSetup}
        dotnet build ${lib.escapeShellArg p.path} ${
          lib.escapeShellArgs (buildProperties ++ [ noApphost ])
        } \
          -o "$out/lib/${p.name}"
        runHook postInstall
      '';
    };

  # `dotnet test` runs the tests once for each target framework of the
  # project, each on the runtime of its framework, so the SDK of a test
  # build carries the nixpkgs runtimes of the other frameworks too.
  testSdk = p: sdkWithRuntimesFor p.targetFrameworks;

  # The locked SDK with the nixpkgs runtimes of the target frameworks that
  # are not its own.
  sdkWithRuntimesFor =
    frameworks:
    let
      majorOf =
        tfm:
        let
          m = builtins.match "net([0-9]+)\\.[0-9]+.*" tfm;
        in
        if m == null then null else builtins.head m;
      others = lib.unique (
        lib.filter (major: major != null && major != toString lock.sdk.major) (map majorOf frameworks)
      );
    in
    if others == [ ] then
      sdk
    else
      dotnetCorePackages.combinePackages (
        [ sdk ] ++ map (major: dotnetCorePackages."runtime_${major}_0") others
      );

  mkTest =
    p:
    mkBuild p {
      dotnetSdk = testSdk p;
      installPhase = ''
        runHook preInstall
        ${dotnetSetup}
        dotnet test ${lib.escapeShellArg p.path} ${lib.escapeShellArgs (buildProperties ++ [ noApphost ])}
        touch "$out"
        runHook postInstall
      '';
    };

  mkProject =
    p:
    let
      drv =
        if p.kind == "exe" then
          if isWebAssembly p then
            mkWebAssembly p
          else if p.selfContained then
            mkSelfContained p
          else
            mkFrameworkDependent p
        else if p.kind == "package" then
          mkPackage p
        else
          mkLibrary p;
    in
    drv.overrideAttrs (old: {
      passthru = old.passthru // {
        artifacts = lib.genAttrs p.artifacts (file: "${drv}/${file}");
      };
    });

  byName = projects: lib.listToAttrs (map (p: lib.nameValuePair p.name p) projects);
  ofKind = kinds: byName (lib.filter (p: lib.elem p.kind kinds) lock.projects);

  packages = lib.mapAttrs (_: mkProject) (ofKind [
    "exe"
    "package"
    "library"
  ]);
  testProjects = lib.mapAttrs (_: mkTest) (ofKind [ "test" ]);
in
{
  inherit
    packages
    dependencies
    packageFolder
    runtimeLibraries
    ;
  # The SDK with the runtime of every target framework of the build, which
  # `dotnet-purifier dotnet` runs, so `dotnet test` runs every framework.
  sdk = sdkWithRuntimesFor (lib.concatMap (p: p.targetFrameworks) lock.projects);
  tests =
    (linkFarm "dotnet-purifier-tests" (
      lib.mapAttrsToList (name: path: { inherit name path; }) testProjects
    )).overrideAttrs
      { passthru.projects = testProjects; };
  release = symlinkJoin {
    name = "dotnet-purifier-release";
    paths = builtins.attrValues (lib.filterAttrs (_: p: p.kind == "package") packages);
  };
  fetches = lock.entries;
}
