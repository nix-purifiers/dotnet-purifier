# SPDX-License-Identifier: MIT
{ pkgs, build }:
let
  mkProject =
    src:
    build {
      inherit src;
      browserTargets.Wasm.webjar = {
        groupId = "org.example";
        artifactId = "dotnet-browser-fixture";
        version = "1.0.0";
      };
    };
  original = mkProject ./fixtures/workloads;
  changed = mkProject ./fixtures/workloads43;
  consumer =
    name: project: expected:
    let
      selected = project.projections.Wasm;
    in
    pkgs.runCommand "dotnet-browser-${name}-consumers"
      {
        nativeBuildInputs = [
          (pkgs.python3.withPackages (p: [ p.websocket-client ]))
          pkgs.jdk
          pkgs.gradle
        ]
        ++ pkgs.lib.optional pkgs.stdenv.hostPlatform.isLinux pkgs.chromium;
        spec = builtins.toJSON {
          resources = selected.web;
          nativeRoot = project.packages.Wasm.artifacts."lib/Wasm/wwwroot";
          inherit expected;
          staticOnly = !pkgs.stdenv.hostPlatform.isLinux;
          chromium = if pkgs.stdenv.hostPlatform.isLinux then "${pkgs.chromium}/bin/chromium" else null;
        };
        passAsFile = [ "spec" ];
      }
      ''
        mkdir "$out"
        export HOME="$TMPDIR/home"
        mkdir "$HOME"
        python3 ${./browser/proof.py} "$specPath" "$out/proof.json"
        cp browser.stdout "$out/"
        cat browser.stderr
        ${pkgs.lib.optionalString pkgs.stdenv.hostPlatform.isLinux ''
          python3 - "$specPath" <<'PY'
          import json,sys
          data=json.load(open(sys.argv[1]));data['denyRuntime']=True
          open('missing-runtime.json','w').write(json.dumps(data))
          PY
          python3 ${./browser/proof.py} missing-runtime.json "$out/missing-runtime-proof.json"
          cat browser.stdout
          cat browser.stderr
        ''}
        cp ${./browser/Resources.java} Resources.java
        javac -d . Resources.java
        java -cp . Resources ${selected.java-resources.classPath} ${selected.web.directory}
        python3 ${./browser/resolve.py} ${selected.maven} resolved.txt "$out/coordinate.txt"
        java -cp . Resources "$(cat resolved.txt)" ${selected.web.directory} META-INF/resources/webjars/dotnet-browser-fixture/1.0.0/
      '';
  first = consumer "original" original "hello from WebAssembly";
  second = consumer "changed" changed "hello from WebAssembly revision 43";
in
pkgs.runCommand "dotnet-browser-consumers"
  {
    nativeBuildInputs = [ pkgs.python3 ];
  }
  ''
    mkdir "$out"
    python3 - ${first} ${second} ${original.projections.Wasm.web.directory} ${changed.projections.Wasm.web.directory} <<'PY'
    import json,pathlib,sys,hashlib
    a,b,left,right=map(pathlib.Path,sys.argv[1:])
    assert (a/'coordinate.txt').read_bytes() != (b/'coordinate.txt').read_bytes()
    assert json.loads((a/'proof.json').read_text())['producer'] != json.loads((b/'proof.json').read_text())['producer']
    def managed(root):
        return [hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob('_framework/wasm.*.wasm')]
    assert len(managed(left)) == len(managed(right)) == 1
    assert managed(left) != managed(right), 'managed source change did not reach actual WebAssembly bytes'
    PY
    cp -r ${first} "$out/original"
    cp -r ${second} "$out/changed"
  ''
