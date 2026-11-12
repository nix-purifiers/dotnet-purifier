# SPDX-License-Identifier: MIT
#
# The dotnet-purifier command (cli/dotnet_purifier.py). It takes the .NET
# project SDK from the caller's installation for local capture and
# development. Nix fetches locked dependencies separately; full sandboxed
# project builds select their immutable SDK independently.
{
  lib,
  writeShellApplication,
  python3,
  path,
  stdenv,
  # The SDK major version for a build without a global.json.
  defaultSdk ? 10,
}:
let
  runtime = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [
      ../../lib-overlays
      ../../pkgs
      ../../cli
      ../../flake.lock
    ];
  };
in
writeShellApplication {
  name = "dotnet-purifier";
  text = ''
    export DOTNET_PURIFIER_WORKLOAD_MODE_HELPER=${./workload-mode.py}
    export DOTNET_PURIFIER_NIX_LIB=${runtime}/pkgs/build-dotnet-project
    export DOTNET_PURIFIER_FLAKE=${../..}
    export DOTNET_PURIFIER_NIXPKGS=${path}
    export DOTNET_PURIFIER_SYSTEM=${stdenv.hostPlatform.system}
    export DOTNET_PURIFIER_DEFAULT_SDK=${toString defaultSdk}
    exec ${lib.getExe python3} ${../../cli/dotnet_purifier.py} "$@"
  '';
}
