# SPDX-License-Identifier: MIT
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import ctypes
class DlInfo(ctypes.Structure):
 _fields_=[('filename',ctypes.c_char_p),('base',ctypes.c_void_p),('symbol',ctypes.c_char_p),('address',ctypes.c_void_p)]
origin_api=ctypes.CDLL(None).dladdr;origin_api.argtypes=[ctypes.c_void_p,ctypes.POINTER(DlInfo)];origin_api.restype=ctypes.c_int
def origin(address):
 info=DlInfo();assert origin_api(address,ctypes.byref(info));return Path(info.filename.decode()).resolve()
from cffi import FFI
spec=json.loads(Path(sys.argv[1]).read_text());loaded=[];ffi_loaded=[];rows=[];libraries=[]
for index,item in enumerate(spec):
 p=Path(item['projection']);catalog=json.loads((p/'catalog.json').read_text());root=Path(catalog['prefix']);model=json.loads((root/'native-products.json').read_text())
 metadata=json.loads((root/'native-proof/managed-exports.json').read_text())
 assert any(r['name']=='Microsoft.Extensions.Primitives' for r in metadata['references'])
 responses=list((root/'native-proof/compiler-responses').rglob('*.ilc.rsp'));assert responses
 assert any('/nuget-packages/microsoft.extensions.primitives/10.0.0/' in r.read_text().lower() for r in responses)
 assert hashlib.sha256((root/'native-proof/published'/model['nativePublishedPath']).read_bytes()).hexdigest()==model['nativeSha256']
 module_spec=importlib.util.spec_from_file_location('loader_'+str(index),p/'purifier_native.py');module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
 lib=module.load('answer');loaded.append(lib);function=lib.native_answer;function.argtypes=[ctypes.c_int32];function.restype=ctypes.c_int32
 counter=lib.native_collections;counter.argtypes=[];counter.restype=ctypes.c_int32;before=counter()
 assert origin(ctypes.cast(function,ctypes.c_void_p))==Path(catalog['libraries']['answer']).resolve()
 assert [function(0),function(1)]==[item['value'],item['value']+2]
 assert counter()-before>=2, 'sequential calls did not collect managed objects'
 ffi=FFI();ffi.cdef((root/'include/answer.cdef').read_text());foreign=ffi.dlopen(catalog['libraries']['answer']);ffi_loaded.append((ffi,foreign))
 assert [foreign.native_answer(0),foreign.native_answer(1)]==[item['value'],item['value']+2]
 parallel_before=counter()
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: values=list(pool.map(function,range(100)))
 assert values==[item['value']+i*2 for i in range(100)]
 # Concurrent requests need not produce one distinct collection each.
 assert counter()>parallel_before, 'concurrent managed calls performed no collection'
 print(json.dumps({'value':item['value'],'observedCollections':counter()-before}),flush=True)
 rows.append({'value':item['value'],'library':catalog['libraries']['answer'],'producer':catalog['producer'],'nativeSha256':model['nativeSha256'],'managedSha256':model['managedSha256'],'foreignThreadGcCalls':100,'managedCollectionsObserved':counter()>before,'dependency':'Microsoft.Extensions.Primitives','cffi':[foreign.native_answer(0),foreign.native_answer(1)]})
 libraries.append(catalog['libraries']['answer']+' '+str(item['value'])+' '+item['javaArgs']+' '+catalog['pkgConfigDirectories'][0])
assert rows[0]['producer']!=rows[1]['producer']
assert rows[0]['nativeSha256']!=rows[1]['nativeSha256']
assert rows[0]['managedSha256']!=rows[1]['managedSha256']
# Retain both initialized runtimes and recheck after loading the second library.
assert loaded[0].native_answer(1)==44 and loaded[1].native_answer(1)==45
Path('libraries.txt').write_text('\n'.join(libraries)+'\n')
Path(sys.argv[2]).write_text(json.dumps(rows,indent=2)+'\n')
