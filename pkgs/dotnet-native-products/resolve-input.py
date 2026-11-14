# SPDX-License-Identifier: MIT
"""Select the actual managed root passed to the SDK native compiler."""
import hashlib
import json
from pathlib import Path
import shlex
import sys

expected=Path(sys.argv[1]).resolve(strict=True)
framework=sys.argv[2]
root=Path.cwd().resolve()
candidates=[]
for response in root.rglob('*.ilc.rsp'):
    for argument in shlex.split(response.read_text(),posix=True):
        if argument.startswith('-') or not argument.lower().endswith('.dll'):
            continue
        path=Path(argument)
        path=(root/path).resolve(strict=True) if not path.is_absolute() else path.resolve(strict=True)
        if path.name != expected.name: continue
        if not path.is_relative_to(root):
            raise ValueError('native compiler root is outside selected staged project')
        candidates.append((response,path))
if len(candidates)>1:
    candidates=[p for p in candidates if framework in p[0].parts or framework in p[1].parts]
assert len(candidates)==1, 'expected exactly one actual native compiler root for '+framework
response,selected=candidates[0]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
model={'framework':framework,'response':str(response.relative_to(root)),'compilerInput':str(selected.relative_to(root)),
       'compilerInputSha256':sha(selected),'targetPath':str(expected.relative_to(root)),
       'targetPathSha256':sha(expected),'sameBytes':sha(selected)==sha(expected)}
Path(sys.argv[3]).write_text(json.dumps(model,indent=2,sort_keys=True)+'\n')
print(selected)
