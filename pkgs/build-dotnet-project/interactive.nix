# SPDX-License-Identifier: MIT
#
# What the dotnet-purifier command builds for a .NET build at `src`, with
# the package set the command was built with: `packageFolder`, the locked
# packages that prefetch installs and restores read; `sdk`, the locked SDK
# with the runtime of every target framework; `runtimeLibraries`, the
# libraries a self-contained .NET runtime loads at run time, which
# `dotnet-purifier dotnet` puts on LD_LIBRARY_PATH as the Nix build's
# wrapper does; and `release`, the packages that publish uploads.
{
  pkgs,
  # The shared purifier library, `lib.purifier` of the dotnet-purifier
  # flake.
  purifier,
  src,
  lockFile,
  # The directory of the packages.lock.json files, the build root or the
  # one --tool-lockfile names.
  packagesLock,
}:
let
  build = pkgs.callPackage ./default.nix { inherit purifier; } {
    inherit src lockFile packagesLock;
  };
in
{
  inherit (build)
    dependencies
    packageFolder
    sdk
    release
    ;
  runtimeLibraries = pkgs.symlinkJoin {
    name = "dotnet-purifier-runtime-libraries";
    paths = map pkgs.lib.getLib build.runtimeLibraries;
  };
}
