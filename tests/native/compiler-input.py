# SPDX-License-Identifier: MIT
"""Use real SDK-produced PE files to distinguish TargetPath from ILC's root."""
import hashlib,json,shlex,shutil,subprocess,sys,tempfile
from pathlib import Path
resolver,first,second,destination=map(Path,sys.argv[1:])
with tempfile.TemporaryDirectory() as temporary:
 root=Path(temporary)
 target=root/'bin/net10.0/Answer.dll';target.parent.mkdir(parents=True)
 actual=root/'obj/net10.0/Answer.dll';actual.parent.mkdir(parents=True)
 shutil.copyfile(first/'native-proof/managed-input.dll',actual)
 shutil.copyfile(second/'native-proof/managed-input.dll',target)
 assert actual.read_bytes()!=target.read_bytes()
 source=next((first/'native-proof/compiler-responses').rglob('*.ilc.rsp'))
 args=shlex.split(source.read_text(),posix=True)
 roots=[i for i,a in enumerate(args) if not a.startswith('-') and a.endswith('/Answer.dll')]
 assert len(roots)==1
 args[roots[0]]=str(actual)
 response=root/'obj/net10.0/Answer.ilc.rsp';response.write_text(shlex.join(args))
 proof=root/'selection.json'
 result=subprocess.run([sys.executable,str(resolver),str(target),'net10.0',str(proof)],cwd=root,capture_output=True,text=True)
 assert result.returncode==0,result.stderr
 model=json.loads(proof.read_text())
 assert Path(result.stdout.strip())==actual
 assert model['sameBytes'] is False
 assert model['compilerInputSha256']==hashlib.sha256(actual.read_bytes()).hexdigest()
 assert model['targetPathSha256']==hashlib.sha256(target.read_bytes()).hexdigest()
 # Ambiguous actual compiler roots fail closed.
 (response.parent/'Other.ilc.rsp').write_text(response.read_text())
 bad=subprocess.run([sys.executable,str(resolver),str(target),'net10.0',str(proof)],cwd=root,capture_output=True,text=True)
 assert bad.returncode!=0 and 'exactly one' in bad.stderr
 Path(destination).write_text(json.dumps({'actualRootSelected':True,'differentTargetBytesRecorded':True,'ambiguousRootsRejected':True},indent=2)+'\n')
