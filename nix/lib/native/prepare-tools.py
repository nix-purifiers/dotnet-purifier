# SPDX-License-Identifier: MIT
"""Give captured NativeAOT compiler packages a private writable tool copy."""
from pathlib import Path
import os
import shutil
import stat
import sys

root=Path(sys.argv[1])
package=root/('runtime.'+sys.argv[2]+'.microsoft.dotnet.ilcompiler')
assert package.is_dir(), 'selected SDK native compiler package is absent from captured dependencies: '+str(package)
for version in sorted(package.iterdir()):
    if not version.is_dir(): continue
    staged=version.with_name(version.name+'.prepared')
    shutil.copytree(version,staged,symlinks=False)
    for path in staged.rglob('*'):
        path.chmod(path.stat().st_mode | stat.S_IWUSR)
    compiler=staged/'tools/ilc'
    assert compiler.is_file(), 'captured compiler package has no tools/ilc'
    compiler.chmod(compiler.stat().st_mode | 0o111)
    shutil.rmtree(version)
    staged.rename(version)
print(package)
