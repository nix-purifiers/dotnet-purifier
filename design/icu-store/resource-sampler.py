# SPDX-License-Identifier: MIT
"""Read RSS only for the diagnostic's inherited process group."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


def owned_rows(module, group, getpgid=os.getpgid):
    assert group>0
    rows=[]
    for pid in module.pids():
        try:
            if getpgid(pid)!=group: continue
            process=module.Process(pid)
            parent=process.ppid()
            rss=process.memory_info().rss
            # Recheck membership after querying to avoid retaining another group.
            if getpgid(pid)!=group: continue
            assert type(rss) is int and rss>=0
            rows.append(dict(pid=pid,parent=parent,group=group,rssBytes=rss))
        except (ProcessLookupError,module.NoSuchProcess):
            # A process may exit during sampling. AccessDenied is not ignored.
            continue
    return rows


def identity(path, site):
    path=Path(path).resolve(strict=True)
    assert path.is_relative_to(site), 'unexpected resource module provider'
    assert path.stat().st_size<=16*1024*1024, 'resource provider size bound'
    return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site',type=Path,required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--group',type=int,required=True)
    args=parser.parse_args()
    site=args.site.resolve(strict=True)
    sys.path.insert(0,str(site))
    import psutil
    assert psutil.__version__==args.version, 'unexpected resource module version'
    provider=dict(version=psutil.__version__,module=identity(psutil.__file__,site),
                  extension=identity(psutil._psplatform.cext.__file__,site),
                  pythonExecutable=sys.executable)
    print(json.dumps(dict(provider=provider,rows=owned_rows(psutil,args.group)),separators=(',',':')))
