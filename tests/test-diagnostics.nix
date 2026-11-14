# SPDX-License-Identifier: MIT
# Keep fixture test-host failures inspectable without changing the native
# builder's test-framework interface or relaxing its sandbox or timeouts.
{ lib }:
test:
assert lib.hasInfix "dotnet test " test.installPhase;
test.overrideAttrs (old: {
  preInstall =
    (old.preInstall or "")
    + "\n"
    + ''
      mkdir -p "$TMPDIR/dotnet-host-trace"
      export DOTNET_HOST_TRACE=1
      export DOTNET_HOST_TRACEFILE="$TMPDIR/dotnet-host-trace"
      export COREHOST_TRACE=1
      export COREHOST_TRACEFILE="$TMPDIR/dotnet-host-trace/legacy.log"
    '';
  installPhase =
    builtins.replaceStrings [ "dotnet test " ] [ "dotnet test --diag \"$TMPDIR/vstest.log\" " ]
      old.installPhase;
  failureHook =
    (old.failureHook or "")
    + "\n"
    + ''
      for dotnetDiagnosticLog in "$TMPDIR"/vstest* "$TMPDIR"/dotnet-host-trace/*; do
        if [ -f "$dotnetDiagnosticLog" ]; then
          echo "dotnet test diagnostic: $dotnetDiagnosticLog" >&2
          cat "$dotnetDiagnosticLog" >&2
        fi
      done
    '';
})
