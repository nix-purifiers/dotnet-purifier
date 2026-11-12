# SPDX-License-Identifier: MIT
"""Verify repeated native captures without changing their SDK RID or hash."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--repo', type=Path, required=True)
parser.add_argument('--fixture', choices=['native42', 'native43', 'native-multi'], required=True)
parser.add_argument('--capture', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
repo = args.repo.resolve()
fixture = repo / 'tests/fixtures' / args.fixture
out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
relative = 'tests/fixtures/' + args.fixture + '/dotnet-purifier.lock.json'
golden_bytes = subprocess.check_output(['git', '-C', str(repo), 'show', 'HEAD:' + relative])
first_bytes = args.capture.read_bytes()
(out / 'committed-capture.lock.json').write_bytes(golden_bytes)
(out / 'capture-1.lock.json').write_bytes(first_bytes)
golden, first = json.loads(golden_bytes), json.loads(first_bytes)
cli = ['nix', 'run', str(repo) + '#default', '--']


def checked_lock(path):
    env = dict(os.environ, NATIVE_PROFILE_FLAKE=str(repo),
               NATIVE_PROFILE_SOURCE=str(fixture), NATIVE_PROFILE_LOCK=str(path))
    expression = '''let
      f = builtins.getFlake ("path:" + builtins.getEnv "NATIVE_PROFILE_FLAKE");
      lock = f.lib.dotnet-purifier.readLock {
        src = builtins.toPath (builtins.getEnv "NATIVE_PROFILE_SOURCE");
        lockFile = builtins.toPath (builtins.getEnv "NATIVE_PROFILE_LOCK");
      };
    in { inherit (lock) problems; outputs = builtins.attrNames lock.outputs; }'''
    result = subprocess.check_output(['nix', 'eval', '--impure', '--json', '--expr', expression],env=env,text=True)
    value = json.loads(result)
    assert value['problems'] == [], value
    return value


# Both raw captures must independently pass the actual Nix consumer's hash and
# definition checks. Keep all RID/hash bytes in the retained artifacts.
readers = {'committed': checked_lock(out/'committed-capture.lock.json'),
           'first': checked_lock(out/'capture-1.lock.json')}
# Complete the installed CLI's native preparation before capturing the SDK
# property's JSON stream. Prefetch may itself emit setup output.
subprocess.run(cli+['prefetch'],cwd=fixture,check=True)
observations, expected_changes = {}, set()
assert len(golden['projects']) == len(first['projects'])
for index, project in enumerate(first['projects']):
    native = project.get('nativePublish')
    if native is None:
        continue
    old = golden['projects'][index]['nativePublish']
    assert native['runtimeIdentifierSource'] == old['runtimeIdentifierSource'] == 'sdk-host'
    command = cli + ['dotnet','msbuild',project['path'],
                     '-getProperty:NETCoreSdkVersion,NETCoreSdkPortableRuntimeIdentifier']
    raw = subprocess.check_output(command,cwd=fixture,text=True)
    (out/('sdk-properties-'+str(index)+'.json')).write_text(raw)
    properties = json.loads(raw)['Properties']
    assert properties['NETCoreSdkVersion'] == first['sdk']['version'], properties
    rid = properties['NETCoreSdkPortableRuntimeIdentifier']
    assert rid and native['runtimeIdentifier'] == rid, (native, properties)
    observations[project['name']] = {'command':command,'properties':properties}
    if old['runtimeIdentifier'] != rid:
        expected_changes.add(('projects',index,'nativePublish','runtimeIdentifier'))
assert observations
if expected_changes:
    expected_changes.add(('contentHash',))


def differences(a,b,path=()):
    if type(a) is not type(b):
        return {path}
    if isinstance(a,dict):
        assert set(a)==set(b), (path,set(a),set(b))
        return set().union(*(differences(a[k],b[k],path+(k,)) for k in a))
    if isinstance(a,list):
        assert len(a)==len(b), path
        return set().union(*(differences(x,y,path+(i,)) for i,(x,y) in enumerate(zip(a,b))))
    return set() if a==b else {path}


actual_changes = differences(golden,first)
assert actual_changes == expected_changes, (actual_changes,expected_changes)
if not expected_changes:
    assert first_bytes == golden_bytes, 'same SDK capture profile changed bytes'
second_path = out/'capture-2.lock.json'
subprocess.run(cli+['lock','--force','--lockfile',str(second_path)],cwd=fixture,check=True)
assert second_path.read_bytes() == first_bytes, 'repeated native SDK capture changed bytes'
readers['second'] = checked_lock(second_path)
# The focused native consumer must consume this exact captured fixture lock.
assert (fixture/'dotnet-purifier.lock.json').read_bytes() == first_bytes
system = subprocess.check_output(['nix','eval','--impure','--raw','--expr','builtins.currentSystem'],text=True).strip()
check = 'native-multi' if args.fixture == 'native-multi' else 'native-ffi'
replay_command = ['nix','build','--no-link','--print-out-paths',str(repo)+'#checks.'+system+'.'+check]
replay_outputs = subprocess.check_output(replay_command,text=True).splitlines()
assert replay_outputs
(out/'verification.json').write_text(json.dumps({
    'fixture':args.fixture,'machine':platform.machine(),'sdkObservations':observations,
    'changedPaths':[list(p) for p in sorted(actual_changes,key=str)],
    'committedContentHash':golden['contentHash'],'capturedContentHash':first['contentHash'],
    'nativeNixReaders':readers,'repeatedCaptureByteIdentical':True,
    'capturedLockNativeReplay':{'command':replay_command,'outputs':replay_outputs},
    'ridOrHashNormalization':False,
},indent=2)+'\n')
