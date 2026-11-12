# SPDX-License-Identifier: MIT
import functools
import hashlib
import http.server
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import websocket

spec = json.loads(Path(sys.argv[1]).read_text())
root = Path(spec['resources']) / 'files'
catalog = json.loads((Path(spec['resources']) / 'catalog.json').read_text())
native=Path(spec['nativeRoot'])
assert {str(p.relative_to(native)) for p in native.rglob('*') if p.is_file()} == set(catalog['files'])
for name, record in catalog['files'].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == record['sha256']
    assert (root/name).read_bytes() == (native/name).read_bytes()
if spec.get('staticOnly'):
    Path(sys.argv[2]).write_text(json.dumps({'producer':catalog['producerIdentity'],'files':len(catalog['files']),'browser':'not run on this platform; native byte equality and Java consumers only'},indent=2)+'\n')
    Path('browser.stdout').write_text('Browser runtime check is Linux-only.\n')
    Path('browser.stderr').write_text('')
    sys.exit(0)
html = b'''<!doctype html><body><pre id="result">loading</pre><script type="module">
const output=document.getElementById('result');
const original=console.log;
console.log=(...args)=>{output.textContent+=args.join(' ');original(...args);};
try {
 const {dotnet}=await import('./_framework/dotnet.js');
 await dotnet.create();
 await dotnet.run();
 output.dataset.done='true';
} catch(e) {output.textContent='FAILED '+e.stack;}
</script>'''
requests=[]
class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        requests.append(self.path)
        if spec.get('denyRuntime') and '/dotnet.native.' in self.path and self.path.endswith('.wasm'):
            self.send_error(404); return
        if self.path == '/':
            self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers();self.wfile.write(html)
        else:
            super().do_GET()
    def log_message(self,*args): pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
        with open('browser.stderr','w') as stderr:
            browser=subprocess.Popen([spec['chromium'],'--headless','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--disable-background-networking','--no-first-run','--disable-extensions','--remote-allow-origins=*','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],stdout=stderr,stderr=stderr)
            try:
                deadline=time.monotonic()+45
                active=Path(profile)/'DevToolsActivePort'
                while not active.exists():
                    assert browser.poll() is None and time.monotonic()<deadline
                    time.sleep(.1)
                port=active.read_text().splitlines()[0]
                with urllib.request.urlopen('http://127.0.0.1:'+port+'/json/list') as response:
                    targets=json.load(response)
                    target=next(t for t in targets if t.get('type') == 'page' and t.get('url') == 'about:blank')
                connection=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=5)
                serial=0
                def call(method,params):
                    global serial
                    serial+=1
                    connection.send(json.dumps({'id':serial,'method':method,'params':params}))
                    while True:
                        message=json.loads(connection.recv())
                        if message.get('id')==serial:
                            assert 'error' not in message,message
                            return message.get('result',{})
                call('Page.navigate',{'url':f'http://127.0.0.1:{server.server_port}/'})
                while True:
                    result=call('Runtime.evaluate',{'expression':'document.documentElement.outerHTML','returnByValue':True})
                    dom=result.get('result',{}).get('value','')
                    if 'data-done="true"' in dom or 'FAILED ' in dom and '<pre id="result">FAILED' in dom:
                        break
                    assert time.monotonic()<deadline,(dom,requests,targets)
                    time.sleep(.1)
                Path('browser.stdout').write_text(dom)
                if spec.get('denyRuntime'):
                    assert '<pre id="result">FAILED' in dom and 'data-done="true"' not in dom,dom
                else:
                    assert 'data-done="true"' in dom,dom
                    text=call('Runtime.evaluate',{'expression':"document.getElementById('result').textContent",'returnByValue':True})['result']['value']
                    assert text == 'loading'+spec['expected'],text
                connection.close()
            finally:
                browser.terminate()
                browser.wait(timeout=10)
    assert any('dotnet.native.' in p and p.endswith('.wasm') for p in requests), requests
    if not spec.get('denyRuntime'):
        assert any('/wasm.' in p and p.endswith('.wasm') for p in requests),requests
    Path(sys.argv[2]).write_text(json.dumps({'producer':catalog['producerIdentity'],'files':len(catalog['files']),'requests':sorted({p for p in requests if p.startswith('/_framework/') and (not spec.get('denyRuntime') or '/dotnet.native.' in p and p.endswith('.wasm'))}),'browser':'missing native runtime rejected' if spec.get('denyRuntime') else 'actual native .NET WebAssembly runtime and managed entrypoint'},indent=2)+'\n')
finally:
    server.shutdown()
