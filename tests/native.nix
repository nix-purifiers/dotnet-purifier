# SPDX-License-Identifier: MIT
{ pkgs, build }:
let
  projects =
    map
      (
        value:
        build {
          src = ./. + "/fixtures/native${toString value}";
          nativeProducts.answer = {
            project = "Answer";
            exports = [
              "native_answer"
              "native_collections"
            ];
          };
        }
      )
      [
        42
        43
      ];
  specs = pkgs.lib.imap0 (index: project: {
    value = 42 + index;
    projection = project.projections.answer.native;
    javaArgs = "${project.projections.answer.java-ffi}/java.args";
  }) projects;
in
pkgs.runCommand "dotnet-native-managed-gc-dependency-consumers"
  {
    nativeBuildInputs = [
      (pkgs.python3.withPackages (p: [ p.cffi ]))
      pkgs.jdk
      pkgs.stdenv.cc
      pkgs.pkg-config
    ];
    spec = builtins.toJSON specs;
    passAsFile = [ "spec" ];
  }
  ''
    mkdir "$out"
    export HOME="$TMPDIR/home"
    mkdir "$HOME"
    python3 ${./native/proof.py} "$specPath" "$out/proof.json"
    cp ${./native/Consumer.java} Consumer.java
    javac --enable-preview --release 21 Consumer.java
    cp ${./native/consumer.c} consumer.c
    while read -r library value javaArgs pkgConfig; do
      export PKG_CONFIG_PATH="$pkgConfig"
      cc consumer.c $(pkg-config --cflags --libs answer) -o c-consumer
      c++ -x c++ consumer.c $(pkg-config --cflags --libs answer) -o cpp-consumer
      ./c-consumer "$value"
      ./cpp-consumer "$value"
      java --enable-preview --enable-native-access=ALL-UNNAMED "@$javaArgs" Consumer "$library" "$value"
    done < libraries.txt
  ''
