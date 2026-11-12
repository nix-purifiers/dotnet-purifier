# SPDX-License-Identifier: MIT
"""Bind owned managed PE export metadata to an actual native shared library."""
import hashlib
import json
import re
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
responses=sorted(Path.cwd().rglob('*.ilc.rsp'))
assert responses, 'native compiler response graph missing'
for response in responses:
    target=proof/'compiler-responses'/response.relative_to(Path.cwd())
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(response,target)
record={'assembly':metadata['assembly'],'managedSha256':metadata['sha256'],'library':spec['library'],
        'nativePublishedPath':str(selected.relative_to(published)),
        'nativeSha256':hashlib.sha256(selected.read_bytes()).hexdigest(),
        'exports':{n:owned[n] for n in spec['exports']},'system':spec['system'],
        'runtime':{'initialization':'native-loader','lifetime':'process','unload':False}}
# Fixed-width CLI scalar and unmanaged pointer signatures can be represented
# directly in a C declaration. Aggregate layouts remain explicit metadata.
primitive={'Void':'void','SByte':'int8_t','Byte':'uint8_t','Int16':'int16_t','UInt16':'uint16_t',
           'Int32':'int32_t','UInt32':'uint32_t','Int64':'int64_t','UInt64':'uint64_t',
           'IntPtr':'intptr_t','UIntPtr':'uintptr_t','Single':'float','Double':'double'}
def ctype(value):
    if value in primitive:return primitive[value]
    if value.endswith('*'):
        base=ctype(value[:-1]);return None if base is None else base+'*'
    return None
include=out/'include';include.mkdir()
declarations=[];unsupported=[]
for entry in spec['exports']:
    method=owned[entry];result=ctype(method['returnType']);parameters=[ctype(p) for p in method['parameters']]
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',entry) or result is None or any(p is None for p in parameters):
        unsupported.append(entry);continue
    declarations.append(result+' '+entry+'('+(', '.join(p+' arg'+str(i) for i,p in enumerate(parameters)) or 'void')+');')
record['header']=None
if not unsupported:
    body='\n'.join(declarations)+'\n'
    guard='PURIFIER_NATIVE_'+metadata['sha256'][:24].upper()
    header='#ifndef '+guard+'\n#define '+guard+'\n#include <stdint.h>\n#ifdef __cplusplus\nextern "C" {\n#endif\n'+body+'#ifdef __cplusplus\n}\n#endif\n#endif\n'
    (include/(spec['name']+'.h')).write_text(header)
    (include/(spec['name']+'.cdef')).write_text(body)
    record['header']='include/'+spec['name']+'.h'
record['headerUnsupportedExports']=unsupported
pc=out/'lib/pkgconfig';pc.mkdir()
(pc/(spec['name']+'.pc')).write_text('prefix='+str(out)+'\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n\nName: '+spec['name']+'\nDescription: NativeAOT '+metadata['assembly']+'\nVersion: '+metadata['assemblyVersion']+'\nLibs: -L${libdir} -l'+spec['name']+'\nCflags: -I${includedir}\n')
(out/'native-products.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
