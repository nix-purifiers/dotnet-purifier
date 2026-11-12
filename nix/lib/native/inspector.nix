# SPDX-License-Identifier: MIT
{ runCommand, sdk }:
runCommand "dotnet-native-export-inspector"
  {
    nativeBuildInputs = [ sdk ];
    dontConfigureNuget = true;
  }
  ''
    export HOME="$TMPDIR/home"
    export DOTNET_CLI_HOME="$HOME"
    export DOTNET_CLI_TELEMETRY_OPTOUT=1
    mkdir -p "$HOME" project empty-feed
    major=$(dotnet --version)
    major="''${major%%.*}"
    cat > project/Inspect.csproj <<XML
    <Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>
    <OutputType>Exe</OutputType><TargetFramework>net$major.0</TargetFramework>
    <UseAppHost>false</UseAppHost><NuGetAudit>false</NuGetAudit>
    <InvariantGlobalization>true</InvariantGlobalization>
    </PropertyGroup></Project>
    XML
    cp ${./InspectExports.cs} project/Program.cs
    dotnet restore project/Inspect.csproj --source "$PWD/empty-feed"
    dotnet publish project/Inspect.csproj --no-restore -c Release -o "$out"
  ''
