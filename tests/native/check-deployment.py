# SPDX-License-Identifier: MIT
"""Bind Darwin deployment requirements to completed native consumer calls."""
import importlib.util
import json
from pathlib import Path
import platform
import sys

assert sys.platform == 'darwin', 'deployment evidence requires native Darwin'
proof = json.loads(Path(sys.argv[1]).read_text())
spec = importlib.util.spec_from_file_location('link_context', sys.argv[3])
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)
rows = proof if isinstance(proof, list) else [proof]
receipts = []
for row in rows:
    if isinstance(proof, list):
        assert row['foreignThreadGcCalls'] == 100 and row['managedCollectionsObserved']
    else:
        assert row['actualCall'] == 44 and row['actualCompilerRootMetadataJoined']
    producer = Path(row['producer'])
    assert str(producer).startswith('/nix/store/'), producer
    deployment = json.loads((producer / 'native-proof/deployment.json').read_text())
    receipts.append(dict(producer=str(producer), nativeConsumerSucceeded=True,
                         **context.runtime_assessment(deployment, platform.mac_ver()[0])))
Path(sys.argv[2]).write_text(json.dumps(receipts, indent=2) + '\n')
