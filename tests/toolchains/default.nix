# SPDX-License-Identifier: MIT
# Exact released SDK point; the primary nixpkgs pin still packages an RC.
{ system }:
let
  flake = builtins.getFlake (toString ../..);
  pkgs = import flake.inputs.nixpkgs { inherit system; };
in
pkgs.extend (
  final: prev: {
    dotnetCorePackages = prev.dotnetCorePackages.overrideScope (
      _: _: import ./released.nix { inherit (prev) dotnetCorePackages; }
    );
  }
)
