# SPDX-License-Identifier: MIT
#
# The hash of a build definition, as `definition_hash` in
# cli/dotnet_purifier.py computes it: the SRI SHA-256 of a text whose first
# line is the targets as JSON and whose other lines are each definition
# file's path and the hex SHA-256 of its content, separated by a tab, in
# sorted order. A definition file is a project, solution, .props or
# .targets file, a .nupkg of a folder feed, global.json, nuget.config,
# dotnet-tools.json or packages.lock.json, outside the directories builds
# write to. When `packagesLock` is another directory than `src`, its
# packages.lock.json files take the place of those in `src`.
{
  lib,
  # The build root.
  src,
  # The directory of the packages.lock.json files, at the paths of their
  # projects.
  packagesLock ? src,
  # The targets the lock was made for.
  targets,
}:
let
  skipped = [
    "bin"
    "obj"
    ".git"
    ".dotnet-purifier"
    ".vs"
    ".idea"
    "node_modules"
  ];
  names = [
    "global.json"
    "nuget.config"
    "dotnet-tools.json"
    "packages.lock.json"
  ];
  suffixes = [
    ".csproj"
    ".fsproj"
    ".vbproj"
    ".sln"
    ".slnx"
    ".props"
    ".targets"
    ".nupkg"
  ];
  isDefinition =
    name:
    let
      lower = lib.toLower name;
    in
    lib.elem lower names || lib.any (suffix: lib.hasSuffix suffix lower) suffixes;

  isNugetLock = name: lib.toLower name == "packages.lock.json";
  replaced = packagesLock != src;

  # The files below `dir` that `keep` takes, as { relative, file }.
  walk =
    keep: dir: prefix:
    lib.concatLists (
      lib.mapAttrsToList (
        name: type:
        let
          relative = if prefix == "" then name else "${prefix}/${name}";
        in
        if type == "directory" then
          if lib.elem name skipped then [ ] else walk keep "${dir}/${name}" relative
        else if type == "regular" && keep name then
          [
            {
              inherit relative;
              file = "${dir}/${name}";
            }
          ]
        else
          [ ]
      ) (builtins.readDir dir)
    );

  files = lib.sort (a: b: a.relative < b.relative) (
    walk (name: isDefinition name && !(replaced && isNugetLock name)) src ""
    ++ lib.optionals (replaced && builtins.pathExists packagesLock) (walk isNugetLock packagesLock "")
  );
  text =
    lib.concatStringsSep "\n" (
      [ (builtins.toJSON targets) ]
      ++ map (f: "${f.relative}\t${builtins.hashFile "sha256" f.file}") files
    )
    + "\n";
in
builtins.convertHash {
  hash = builtins.hashString "sha256" text;
  hashAlgo = "sha256";
  toHashFormat = "sri";
}
