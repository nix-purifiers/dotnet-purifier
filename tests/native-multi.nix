# SPDX-License-Identifier: MIT
{ pkgs, build }:
let
  project = build {
    src = ./fixtures/native-multi;
    nativeProducts.answer = { project = "Answer"; exports = [ "native_answer" ]; };
  };
in pkgs.runCommand "dotnet-native-multi-framework-input" { nativeBuildInputs = [pkgs.python3]; } ''
  mkdir "$out"
  python3 - ${project.nativePackages.answer} "$out/proof.json" <<'PY'
import ctypes,hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);proof=root/'native-proof'
selection=json.loads((proof/'compiler-input.json').read_text())
metadata=json.loads((proof/'managed-exports.json').read_text())
assert selection['framework']=='net10.0'
assert 'net10.0' in Path(selection['compilerInput']).parts
assert 'net10.0' in Path(selection['targetPath']).parts
assert selection['compilerInputSha256']==metadata['sha256']==hashlib.sha256((proof/'managed-input.dll').read_bytes()).hexdigest()
assert selection['targetPathSha256']==hashlib.sha256((proof/'build-target.dll').read_bytes()).hexdigest()
model=json.loads((root/'native-products.json').read_text())
library=ctypes.CDLL(str(root/model['library']))
function=library.native_answer;function.argtypes=[ctypes.c_int];function.restype=ctypes.c_int
assert function(1)==44
Path(sys.argv[2]).write_text(json.dumps({'framework':'net10.0','actualCompilerRootMetadataJoined':True,'actualCall':44},indent=2)+'\n')
PY
''
