# SPDX-License-Identifier: MIT
#
# The dotnet-purifier scope, `pkgs.dotnet-purifier`, which the default
# package overlay adds to the package set.
{ callPackage, lib, ... }:
{
  # The command that locks a .NET build's NuGet downloads and runs dotnet
  # from them.
  dotnet-purifier = callPackage ./dotnet-purifier { };

  # The builder (pkgs/build-dotnet-project/default.nix), with the shared purifier
  # library of this flake, `lib.purifier`.
  buildDotnetProject = callPackage ./build-dotnet-project/default.nix {
    inherit (lib) purifier;
  };
}
