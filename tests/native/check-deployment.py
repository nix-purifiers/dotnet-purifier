# SPDX-License-Identifier: MIT
"""Bind Darwin deployment requirements to completed native consumer calls."""
import importlib.util
import hashlib
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
    model = json.loads((producer / 'native-products.json').read_text())
    library = producer / model['library']
    digest = hashlib.sha256()
    with library.open('rb') as file:
        while chunk := file.read(1024**2):
            digest.update(chunk)
    assert digest.hexdigest() == model['packagedSha256'], 'packaged library hash changed'
    environment = json.loads((producer / 'native-proof/link-environment.json').read_text())
    log = (producer / 'native-proof/native-link.log').read_text()
    # Inspect the actual final library after packaging/fixup and successful calls.
    deployment = context.assess(environment, log, context.dependency_metadata(library))
    receipts.append(dict(producer=str(producer), nativeConsumerSucceeded=True,
                         **context.runtime_assessment(deployment, platform.mac_ver()[0])))
Path(sys.argv[2]).write_text(json.dumps(receipts, indent=2) + '\n')
