# SPDX-License-Identifier: MIT
"""Reject incompatible selections using real SDK-built PE/ELF artifacts."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

verify=Path(sys.argv[1]);producer=Path(sys.argv[2]);destination=Path(sys.argv[3])
model=json.loads((producer/'native-products.json').read_text());rows=[]
with tempfile.TemporaryDirectory() as temp:
 root=Path(temp);project=root/'project';project.mkdir()
 assembly=project/'owned.dll';shutil.copyfile(producer/'native-proof/managed-input.dll',assembly)
 shutil.copytree(producer/'native-proof/compiler-responses',project/'responses')
 metadata=producer/'native-proof/managed-exports.json'
 published=producer/'native-proof/published'
 spec={'name':'answer','assembly':'Answer','exports':['native_answer'],'system':model['system'],'library':'lib/libanswer.so'}
 def run(name,selected,source,expected=None):
  path=root/(name+'.json');path.write_text(json.dumps(selected))
  result=subprocess.run([sys.executable,str(verify),str(path),str(source),str(metadata),str(root/('output-'+name)),str(assembly)],cwd=project,capture_output=True,text=True)
  if expected is None:assert result.returncode==0,result.stderr
  else:assert result.returncode!=0 and expected in result.stderr,(name,result.stderr)
  rows.append({'case':name,'exitCode':result.returncode,'expectedDiagnostic':expected})
 run('actual-native-positive',spec,published)
 run('missing-owned-export',dict(spec,exports=['not_an_export']),published,'export absent from owned PE metadata')
 run('sdk-runtime-data-is-not-project-export',dict(spec,exports=['DotNetRuntimeDebugHeader']),published,'export absent from owned PE metadata')
 run('different-managed-assembly',dict(spec,assembly='AnotherAssembly'),published,'managed assembly does not belong')
 wrong='aarch64-linux' if model['system']=='x86_64-linux' else 'x86_64-linux'
 run('wrong-native-architecture',dict(spec,system=wrong),published,'does not match selected system')
 managed=root/'managed-only';managed.mkdir();shutil.copyfile(assembly,managed/'Answer.dll')
 run('managed-dll-is-not-native',spec,managed,'expected exactly one native library')
destination.write_text(json.dumps(rows,indent=2)+'\n')
