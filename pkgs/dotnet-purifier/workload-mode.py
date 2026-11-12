# SPDX-License-Identifier: MIT
"""Use bundled manifests in a private SDK home, preserving project workload sets.

Invoked only by capture and sandboxed builds, with an isolated DOTNET_CLI_HOME.
No caller runtime version is selected or replaced here.
"""
import json
from pathlib import Path
import re
import subprocess
import sys


def manifest_json(text):
    # Preserve string tokens in both passes: removing a comment can expose
    # a trailing comma which was not adjacent to its closing delimiter.
    strings = r'"(?:\\.|[^"\\])*"'
    text = re.sub(strings + r'|//[^\n]*|/\*.*?\*/',
                  lambda match: match.group() if match.group().startswith('"') else '',
                  text, flags=re.DOTALL)
    text = re.sub(strings + r'|,(?=\s*[\]}])',
                  lambda match: match.group() if match.group().startswith('"') else '',
                  text, flags=re.DOTALL)
    return json.loads(text)


def configure(dotnet, root):
    global_json=root/'global.json'
    if global_json.is_file() and manifest_json(global_json.read_text()).get('sdk',{}).get('workloadVersion'):
        return 'project-workload-set'
    result=subprocess.run([dotnet,'workload','config','--help'],capture_output=True,text=True)
    if result.returncode==0 and '--update-mode' in result.stdout:
        subprocess.run([dotnet,'workload','config','--update-mode','manifests'],check=True)
        return 'bundled-manifests'
    # Older native SDKs use manifests and have no workload-set mode.
    return 'native-default'


if __name__=='__main__':
    print('dotnet-purifier: workload selection '+configure(sys.argv[1],Path.cwd()),file=sys.stderr)
