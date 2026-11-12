#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# CI explicitly opts into a packaged fixture toolchain. The local CLI
# itself never installs or substitutes a project SDK.
set -euo pipefail
nix build --impure --out-link "$RUNNER_TEMP/fixture-sdk" --expr '
  let
    f = builtins.getFlake ("path:" + builtins.getEnv "GITHUB_WORKSPACE");
    p = import f.inputs.nixpkgs { };
    packages = p.dotnetCorePackages;
  in packages.combinePackages [ packages.sdk_10_0 packages.runtime_8_0 packages.runtime_9_0 ]
'
echo "DOTNET_PURIFIER_DOTNET=$RUNNER_TEMP/fixture-sdk/bin/dotnet" >> "$GITHUB_ENV"
