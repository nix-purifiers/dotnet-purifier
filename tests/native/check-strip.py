# SPDX-License-Identifier: MIT
"""Native Apple strip flag bytes, local-symbol removal and surviving ABI call."""
import ctypes
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

assert sys.platform == 'darwin', 'strip evidence requires native Darwin'
provider = Path(sys.argv[1]).resolve(strict=True)
assert str(provider).startswith('/nix/store/'), provider
tools = {}
for name in ('clang', 'nm'):
    selected = shutil.which(name)
    assert selected is not None, ('missing native tool', name)
    resolved = Path(selected).resolve(strict=True)
    assert str(resolved).startswith('/nix/store/'), ('non-store native tool', name, str(resolved))
    tools[name] = dict(selected=selected, resolved=str(resolved),
                       sha256=hashlib.sha256(resolved.read_bytes()).hexdigest())
commands = []

def run(argv):
    commands.append([str(x) for x in argv])
    return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    source = root / 'probe.c'
    source.write_text('static __attribute__((noinline)) int strip_local(int x) { return x + 1; }\n'
                      '__attribute__((visibility("default"))) int strip_answer(void) { return strip_local(41); }\n')
    original = root / 'original.dylib'
    run(['clang', '-g', '-O0', '-dynamiclib', '-Wl,-install_name,@rpath/strip-probe.dylib',
         str(source), '-o', str(original)])
    before = run(['nm', '-a', str(original)])
    assert '_strip_local' in before and '_strip_answer' in before, before
    outputs = []
    for name, flags in [('plain', ['-x']), ('warning', ['-no_code_signature_warning', '-x'])]:
        directory = root / name
        directory.mkdir()
        library = directory / 'probe.dylib'
        shutil.copyfile(original, library)
        run([str(provider), *flags, str(library)])
        after = run(['nm', '-a', str(library)])
        assert '_strip_local' not in after and '_strip_answer' in after, after
        call = ctypes.CDLL(str(library)).strip_answer
        call.restype = ctypes.c_int
        call.argtypes = []
        assert call() == 42
        outputs.append(hashlib.sha256(library.read_bytes()).hexdigest())
    assert outputs[0] == outputs[1], 'warning-only flag changed output bytes'
    Path(sys.argv[2]).write_text(json.dumps(dict(
        provider=str(provider), providerSha256=hashlib.sha256(provider.read_bytes()).hexdigest(),
        commands=commands, tools=tools, strippedSha256=outputs, warningFlagBytesEqual=True,
        localSymbolRemoved=True, exportedConsumerResult=42,
        scope='one native C fixture; NativeAOT consumers remain separate required checks'), indent=2) + '\n')
