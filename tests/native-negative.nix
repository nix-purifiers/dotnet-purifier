# SPDX-License-Identifier: MIT
{ pkgs, build }:
let
  project = build {
    src = ./fixtures/native42;
    nativeProducts.answer = {
      project = "Answer";
      exports = [ "native_answer" ];
    };
  };
  changed = build {
    src = ./fixtures/native43;
    nativeProducts.answer = { project = "Answer"; exports = [ "native_answer" ]; };
  };
in
pkgs.runCommand "dotnet-native-negative-controls"
  {
    nativeBuildInputs = [
      pkgs.python3
      pkgs.stdenv.cc.bintools
    ];
  }
  ''
    mkdir "$out"
    python3 ${./native/compiler-input.py} ${../pkgs/dotnet-native-products/resolve-input.py} ${project.nativePackages.answer} ${changed.nativePackages.answer} "$out/compiler-input.json"
    python3 ${./native/negative.py} ${../nix/lib/native/verify.py} ${project.nativePackages.answer} "$out/proof.json" ${changed.nativePackages.answer}
  ''
