#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# CI explicitly opts into a packaged fixture toolchain. The local CLI
# itself never installs or substitutes a project SDK.
set -euo pipefail
if [ "${1:-}" = current ]; then
  # The current fixture's global.json pins the released .NET 11 SDK.
  selected='(import (f.outPath + "/tests/toolchains/released.nix") { inherit (p) dotnetCorePackages; }).sdk_11_0'
else
  selected='packages.combinePackages [ packages.sdk_10_0 packages.runtime_8_0 packages.runtime_9_0 ]'
fi
nix build --impure --out-link "$RUNNER_TEMP/fixture-sdk" --expr '
  let
    f = builtins.getFlake ("path:" + builtins.getEnv "GITHUB_WORKSPACE");
    p = import f.inputs.nixpkgs { };
    packages = p.dotnetCorePackages;
  in '"$selected"'
'
echo "DOTNET_PURIFIER_DOTNET=$RUNNER_TEMP/fixture-sdk/bin/dotnet" >> "$GITHUB_ENV"
