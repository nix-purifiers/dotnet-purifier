# SPDX-License-Identifier: MIT
"""Installs NuGet packages into a package folder the way NuGet does.

  python3 package-folder.py PACKAGES_JSON OUT

PACKAGES_JSON lists the packages as { path, file, hash, nugetContentHash }:
`path` is <id>/<version>/<id>.<version>.nupkg, with the id and version in
lower case, `file` the .nupkg, `hash` its SRI SHA-512 and
`nugetContentHash` the content hash NuGet records for it. For each package OUT
gets the directory <id>/<version> with the .nupkg, its contents, the
nuspec as <id>.nuspec, <file>.sha512 and .nupkg.metadata. NuGet takes a
package with these files as installed, and the folder is also a feed that
a restore reads.
"""

import base64
import hashlib
import json
import shutil
import sys
import urllib.parse
import zipfile
from pathlib import Path

# The parts of a .nupkg that belong to the package format, which NuGet
# leaves out when it installs a package.
SKIPPED = ("[Content_Types].xml", "_rels/", "package/")


def sha512_sri(path):
    digest = hashlib.sha512()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return "sha512-" + base64.b64encode(digest.digest()).decode()


def install(package, out):
    path = Path(package["path"])
    package_id = path.parts[0]
    # Nix checked the downloads when it fetched them. A package of a folder
    # feed in the build or of the SDK's feed is checked here.
    found = sha512_sri(package["file"])
    if found != package["hash"]:
        sys.exit(
            f"dotnet-purifier: {package['file']} has the hash {found}, and the lock records "
            f"{package['hash']}: the package changed since `dotnet-purifier lock` ran. Run "
            "`dotnet-purifier lock` at the build root and commit the lockfile."
        )
    directory = out / path.parent
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(package["file"], out / path)
    # NuGet records its own content hash of the package, which for a signed
    # package leaves the signature out, and a restore in lock mode checks
    # it against packages.lock.json.
    content_hash = package["nugetContentHash"].removeprefix("sha512-")
    with zipfile.ZipFile(out / path) as nupkg:
        for entry in nupkg.infolist():
            if entry.is_dir() or entry.filename.startswith(SKIPPED):
                continue
            # The entries of a .nupkg have URL-encoded names.
            name = urllib.parse.unquote(entry.filename)
            if "/" not in name and name.lower().endswith(".nuspec"):
                name = f"{package_id}.nuspec"
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with nupkg.open(entry) as source, open(target, "wb") as dest:
                shutil.copyfileobj(source, dest)
    (out / f"{path}.sha512").write_text(content_hash)
    metadata = {"version": 2, "contentHash": content_hash, "source": str(out)}
    (directory / ".nupkg.metadata").write_text(json.dumps(metadata))


def main():
    packages = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for package in packages:
        install(package, out)


if __name__ == "__main__":
    main()
