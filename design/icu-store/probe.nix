# SPDX-License-Identifier: MIT
# Bounded observational diagnostic. The caller must supply the
# exact current App and its selected runtime, not a replacement SDK/runtime.
{
  pkgs,
  app,
  runtime,
}:
assert pkgs.stdenv.hostPlatform.isDarwin;
pkgs.runCommand "dotnet-current-icu-store-probe"
  {
    nativeBuildInputs = [ pkgs.python3 ];
    # No propagated runtime/SDK sandbox profile or local-network permission.
    nativeLinkPrerequisites = [
      pkgs.darwin.ICU
      pkgs.python3Packages.psutil
    ];
  }
  ''
    python3 ${./controller.py} --owned-sampler ${./resource-sampler.py} \
      --psutil-site ${pkgs.python3Packages.psutil}/${pkgs.python3.sitePackages} \
      --ps-version ${pkgs.lib.escapeShellArg pkgs.python3Packages.psutil.version} \
      --out "$out" --probe-helper ${./probe.py} -- \
      python3 ${./probe.py} \
      --app ${app}/lib/App --dotnet ${pkgs.lib.getExe' runtime "dotnet"} \
      --icu ${pkgs.darwin.ICU}/lib --out "$out" \
      --identity-helper ${../../tests/runtime-identity.py} \
      --link-helper ${../../pkgs/dotnet-native-products/link-context.py}
  ''
