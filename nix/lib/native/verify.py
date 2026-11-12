# SPDX-License-Identifier: MIT
"""Bind owned managed PE export metadata to an actual native shared library."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

spec=json.loads(Path(sys.argv[1]).read_text())
published=Path(sys.argv[2]).resolve(strict=True)
metadata=json.loads(Path(sys.argv[3]).read_text())
out=Path(sys.argv[4])
assert metadata['assembly']==spec['assembly'], 'managed assembly does not belong to selected project'
owned={e['entryPoint']:e for e in metadata['exports']}
for name in spec['exports']:
    assert name in owned, 'export absent from owned PE metadata: '+name
    conventions=[c.split(',')[0].strip() for c in owned[name]['callingConventions']]
    assert not conventions or conventions==['System.Runtime.CompilerServices.CallConvCdecl'], 'export is not a platform C ABI: '+name

matches=[]
for path in published.rglob('*'):
    if not path.is_file(): continue
    assert path.resolve().is_relative_to(published), 'native publish symlink escapes output'
    magic=path.read_bytes()[:32]
    if magic[:4]==b'\x7fELF':
        assert magic[4:6]==b'\x02\x01', 'expected 64-bit little-endian native library'
        kind,machine=struct.unpack_from('<HH',magic,16)
        if kind!=3: continue
        expected={'x86_64-linux':62,'aarch64-linux':183}.get(spec['system'])
        assert machine==expected, 'native ELF target does not match selected system'
        result=subprocess.run(['nm','-D','--defined-only','--format=posix',str(path)],check=True,capture_output=True,text=True)
        symbols={line.split()[0].split('@')[0] for line in result.stdout.splitlines() if len(line.split())>=2 and line.split()[1] in ('T','W')}
    elif magic[:4]==b'\xcf\xfa\xed\xfe':
        cpu,subtype,kind=struct.unpack_from('<III',magic,4)
        if kind!=6: continue
        assert spec['system']=='aarch64-darwin' and cpu==0x0100000c, 'native Mach-O target does not match selected system'
        result=subprocess.run(['nm','-gU',str(path)],check=True,capture_output=True,text=True)
        symbols={line.split()[-1].removeprefix('_') for line in result.stdout.splitlines() if len(line.split())>=3 and line.split()[-2] in ('T','W')}
    else: continue
    if set(spec['exports'])<=symbols: matches.append((path,symbols))
assert len(matches)==1, 'expected exactly one native library defining all selected owned exports'
selected,symbols=matches[0]
(out/'lib').mkdir(parents=True,exist_ok=True)
proof=out/'native-proof';proof.mkdir()
shutil.copytree(published,proof/'published')
shutil.copyfile(selected,out/spec['library'])
shutil.copyfile(sys.argv[3],proof/'managed-exports.json')
assembly=Path(sys.argv[5]).resolve(strict=True)
assert assembly.is_relative_to(Path.cwd().resolve()), 'managed input is outside selected project source'
assert hashlib.sha256(assembly.read_bytes()).hexdigest()==metadata['sha256']
shutil.copyfile(assembly,proof/'managed-input.dll')
record={'assembly':metadata['assembly'],'managedSha256':metadata['sha256'],'library':spec['library'],
        'nativePublishedPath':str(selected.relative_to(published)),
        'nativeSha256':hashlib.sha256(selected.read_bytes()).hexdigest(),
        'exports':{n:owned[n] for n in spec['exports']},'system':spec['system'],
        'runtime':{'initialization':'native-loader','lifetime':'process','unload':False}}
(out/'native-products.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
