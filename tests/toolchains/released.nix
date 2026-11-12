# SPDX-License-Identifier: MIT
{ dotnetCorePackages }:
let
  released =
    (dotnetCorePackages.buildDotnetSdk ./dotnet11.nix)
    // (dotnetCorePackages.buildDotnetSdk ./dotnet10-runtime.nix);
  # The binary SDK needs the user-local workload marker so workload
  # metadata is written under the isolated HOME, outside the Nix store.
  sdk = released.sdk_11_0.unwrapped.overrideAttrs (old: {
    postInstall = (old.postInstall or "") + ''
      mkdir -p "$out/share/dotnet/metadata/workloads/11.0.100"
      touch "$out/share/dotnet/metadata/workloads/11.0.100/userlocal"
    '';
  });
in
released
// {
  sdk_11_0 = released.sdk_11_0.overrideAttrs (old: {
    src = sdk;
    passthru = old.passthru // {
      unwrapped = sdk;
    };
  });
}
