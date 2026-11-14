# SPDX-License-Identifier: MIT
{ pkgs }:
let
  core = pkgs.lib.purifier;
  project = {
    kind = "exe";
    runtimeIdentifier = "browser-wasm";
  };
  evaluate =
    projects: targets:
    builtins.deepSeq (import ../pkgs/build-dotnet-project/browser-projections.nix {
      inherit (pkgs) lib runCommand python3;
      purifier = core;
      inherit projects targets;
      packages = {
        Browser = pkgs.emptyDirectory;
        Desktop = pkgs.emptyDirectory;
      };
    }) true;
  rejected = projects: targets: !(builtins.tryEval (evaluate projects targets)).success;
in
assert rejected {
  Browser = project // {
    runtimeIdentifier = "wasi-wasm";
  };
} { Browser = { }; };
assert rejected {
  Desktop = project // {
    runtimeIdentifier = "linux-x64";
  };
} { Desktop = { }; };
assert rejected {
  Browser = project // {
    kind = "library";
  };
} { Browser = { }; };
assert rejected { Browser = project; } { Unknown = { }; };
assert rejected { Browser = project; } {
  Browser = {
    root = "/";
  };
};
assert rejected { Browser = project; } {
  Browser.webjar = {
    artifactId = "x";
    version = "1";
    producer = pkgs.emptyDirectory;
  };
};
pkgs.runCommand "dotnet-browser-boundaries" { } "touch $out"
