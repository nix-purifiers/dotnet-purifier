# SPDX-License-Identifier: MIT
"""Bound the OS-dependent artifact names of the native capture fixtures."""


def shared_library(rid):
    for prefix, extension in (("linux", ".so"), ("osx", ".dylib"), ("win", ".dll")):
        if rid.startswith(prefix + "-"):
            return extension
    raise AssertionError("unexpected runtime identifier " + rid)


def validate_artifacts(assembly, rid, artifacts):
    """Require the library and allow only its platform's debug companions.

    NativeAOT's _CopyAotSymbols names Unix symbols after the full binary,
    and Windows symbols after TargetName. Darwin dsymutil produces a bundle;
    LLVM may additionally emit per-architecture relocations and remarks.
    Sources: Microsoft.NETCore.Native.{targets,Publish.targets} in the locked
    Microsoft.DotNet.ILCompiler package; llvmorg-21.1.0 llvm/tools/dsymutil/
    {dsymutil.cpp,DwarfLinkerForBinary.cpp} and llvm/lib/TargetParser/Triple.cpp.
    Unknown new output names require review rather than a subtree wildcard.
    This checks names, not the validity of binary or debug information.
    """
    assert isinstance(assembly, str) and assembly and assembly not in (".", "..")
    assert not any(c in assembly for c in ("/", "\\", "\x00")), assembly
    assert isinstance(artifacts, list) and all(isinstance(p, str) for p in artifacts), artifacts
    assert len(set(artifacts)) == len(artifacts), ("duplicate artifacts", artifacts)
    extension = shared_library(rid)
    library = assembly + extension
    allowed = {library}
    bundle_core = set()
    if extension == ".so":
        allowed.add(library + ".dbg")
    elif extension == ".dll":
        allowed.add(assembly + ".pdb")
    else:
        root = library + ".dSYM/Contents/"
        bundle_core = {root + "Info.plist", root + "Resources/DWARF/" + library}
        allowed.update(bundle_core)
        # RID and LLVM use different names for Apple's ARM architecture.
        arch = {"arm64": "aarch64", "x64": "x86_64"}.get(rid.removeprefix("osx-"))
        if arch:
            allowed.add(root + "Resources/Relocations/" + arch + "/" + library + ".yml")
        allowed.add(root + "Resources/Remarks/" + library)
    actual = set(artifacts)
    assert library in actual, ("missing native library", library, artifacts)
    assert actual <= allowed, ("unexpected native artifacts", sorted(actual - allowed), rid)
    if bundle_core and actual != {library}:
        assert bundle_core <= actual, ("incomplete dSYM bundle", artifacts)
