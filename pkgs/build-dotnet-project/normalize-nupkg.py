# SPDX-License-Identifier: MIT
"""Rewrites NuGet packages with a fixed time on every entry.

  python3 normalize-nupkg.py PACKAGE...

A package keeps its entries, their order, their content and their
compression. Only the time of each entry changes, to 1980-01-01, the
earliest a zip file can hold. A pattern that matches no file is skipped.
"""

import sys
import zipfile
from pathlib import Path

FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def normalize(path):
    with zipfile.ZipFile(path) as source:
        entries = [(info, source.read(info)) for info in source.infolist()]
    temporary = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(temporary, "w") as target:
        for info, data in entries:
            fixed = zipfile.ZipInfo(info.filename, FIXED_TIME)
            fixed.compress_type = info.compress_type
            fixed.external_attr = info.external_attr
            fixed.create_system = info.create_system
            target.writestr(fixed, data)
    temporary.replace(path)


def main():
    for argument in sys.argv[1:]:
        path = Path(argument)
        if path.is_file():
            normalize(path)


if __name__ == "__main__":
    main()
