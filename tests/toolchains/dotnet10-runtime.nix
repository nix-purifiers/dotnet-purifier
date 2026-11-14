# SPDX-License-Identifier: MIT
# Released .NET 10 runtime for the maintained-line execution fixture.
# Source and SHA-512 hashes: https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json
{ buildNetRuntime, ... }:
{
  runtime_10_0 = buildNetRuntime {
    version = "10.0.15";
    srcs = {
      linux-arm64 = {
        url = "https://builds.dotnet.microsoft.com/dotnet/Runtime/10.0.15/dotnet-runtime-10.0.15-linux-arm64.tar.gz";
        hash = "sha512-4ui5B9nrbCOczPXB/mf9mnLFb93M7cqbpByKU/8DQpOzNf3Z96tQTwm/ez8WXg0U40/J6h8GzA6G0N+wbSdLag==";
      };
      linux-x64 = {
        url = "https://builds.dotnet.microsoft.com/dotnet/Runtime/10.0.15/dotnet-runtime-10.0.15-linux-x64.tar.gz";
        hash = "sha512-qQF311vIkfcaqBTd47z3ajVvWlBoJGafGJF1RFeVSj0Tty88oEyY+g/liFFKWngfioexeTDugCEFwU+e2ygxig==";
      };
      osx-arm64 = {
        url = "https://builds.dotnet.microsoft.com/dotnet/Runtime/10.0.15/dotnet-runtime-10.0.15-osx-arm64.tar.gz";
        hash = "sha512-GFwmTZ5F8U2owLK0miTTp66kcZmag67teivxZEMCkqq8FdnCLS60SyZXak4zHKD2tWSAXrL2WI1aKQ8Ca4bM4A==";
      };
    };
  };
}
