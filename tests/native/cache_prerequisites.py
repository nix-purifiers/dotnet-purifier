# SPDX-License-Identifier: MIT
"""Require cached Darwin linker inputs before any native test build."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def ensure(repo, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    receipt = {'platform': sys.platform, 'sourceBuildsAllowed': False}
    path = out / 'cache-prerequisites.json'
    if sys.platform != 'darwin':
        receipt['status'] = 'not-required'
        path.write_text(json.dumps(receipt, indent=2) + '\n')
        return receipt
    env = dict(os.environ, NATIVE_LINK_FLAKE=str(Path(repo).resolve()))
    expression = '''let
      f = builtins.getFlake ("path:" + builtins.getEnv "NATIVE_LINK_FLAKE");
      p = import f.inputs.nixpkgs { overlays = [ f.overlays.default ]; };
      b = p.dotnet-purifier.buildDotnetProject {
        src = f.outPath + "/tests/fixtures/native42";
        nativeProducts.answer = {
          project = "Answer"; exports = [ "native_answer" "native_collections" ];
        };
      };
    in p.lib.unique (b.nativePackages.answer.nativeLinkPrerequisites ++
      (f.checks.${builtins.currentSystem}.current-icu-store-diagnostic.nativeLinkPrerequisites or []))'''
    evaluate = ['nix', 'eval', '--impure', '--json', '--option',
                'allow-import-from-derivation', 'false', '--expr',
                'map toString (' + expression + ')']
    receipt.update(evaluate=evaluate, status='evaluating-inputs')
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    paths = json.loads(subprocess.check_output(evaluate, env=env, text=True))
    assert len(paths) == len(set(paths)) and len(paths) >= 2 and all(p.startswith('/nix/store/') for p in paths), paths
    # Copy exact output paths and their closure from the trusted binary cache.
    # Unlike a build request, this operation cannot fall back to a compiler build.
    command = ['nix', 'copy', '--from', 'https://cache.nixos.org', *paths]
    receipt.update(evaluate=evaluate, paths=paths, command=command, status='attempting-cache-only')
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    result = subprocess.run(command, env=env, text=True, capture_output=True)
    receipt.update(exitCode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    receipt['status'] = 'cached' if result.returncode == 0 else 'unavailable'
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    result.check_returncode()
    inspect = ['nix', 'path-info', '--json', '--recursive', *paths]
    receipt['inspect'] = inspect
    receipt['closure'] = json.loads(subprocess.check_output(inspect, env=env, text=True))
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    ensure(args.repo, args.out)
