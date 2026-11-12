# SPDX-License-Identifier: MIT
#
# readLock: reads a dotnet-purifier.lock.json into the lock in memory of
# the purifier contract (CONTRACT.md of github:nix-purifiers/purifier):
#
#   purifier  "dotnet-purifier"
#   entries   the download entries of the build, each with its hash; a
#             workload pack that exists for one machine carries `systems`,
#             and a package that a packages.lock.json records carries
#             `nugetContentHash`, the hash NuGet's lock mode checks
#   outputs   each project by name, as { kind, path, assemblyName,
#             targetFrameworks, frameworkReferences, artifacts, ... }
#   problems  why the lockfile cannot be used, each naming the command
#             that fixes it
#
# and, for the builder, `sdk`, `targets`, `tools`, `workloads`, `projects`,
# `localPackages` and `sdkPackages`.
#
# The lockfile is what `dotnet-purifier lock` writes:
#
#   version         the format, 1
#   sdk             the .NET SDK the lock run used, as { version, major }
#   targets         the projects or solution the lock was made for
#   definitionHash  the hash of the build definition (definition.nix)
#   tools           whether the build has local tools in
#                   .config/dotnet-tools.json
#   workloads       the SDK workloads the build installs, whose packs are
#                   among `artifacts`
#   projects        the projects of the build, each as { name, path, kind,
#                   assemblyName, targetFrameworks, frameworkReferences,
#                   artifacts }; `kind` is "exe", "package" (a packable
#                   library), "library" or "test". An executable also has
#                   { selfContained, runtimeIdentifier }, and a package
#                   { packageId, packageVersion }. `artifacts` lists the
#                   files the project's build produces, relative to its
#                   output, as the lock run produced them.
#   artifacts       every NuGet package the build downloads, as a download
#                   entry { path, url, mirrors, hash }: `path` is where the
#                   package lies in a NuGet package folder,
#                   <id>/<version>/<id>.<version>.nupkg, `url` its
#                   flat-container URL, `mirrors` an empty list (a feed
#                   serves a version at one URL for good) and `hash` the
#                   SRI SHA-512 of the .nupkg file. A workload pack for one
#                   machine also has `systems`.
#   localPackages   every package that comes from a folder feed inside the
#                   build, as { path, file, hash }, where `file` is the
#                   package's file relative to the build root
#   sdkPackages     every package that comes from the folder feed the SDK
#                   carries (share/dotnet/library-packs) and from no feed,
#                   as { path, file, hash }, where `file` is the package's
#                   file in that feed
#   contentHash     the SRI SHA-256 of the lockfile without contentHash, as
#                   builtins.toJSON writes it
#
# An entry of artifacts, localPackages or sdkPackages that a
# packages.lock.json of the build records with the same hash has
# `hashFrom`, that file's path relative to the build root, in place of
# `hash`.
{
  lib,
  # The build root.
  src,
  # The lockfile.
  lockFile ? src + "/dotnet-purifier.lock.json",
  # The directory of NuGet's packages.lock.json files, at the paths of
  # their projects. Another directory than `src` takes the place of the
  # files in `src`.
  packagesLock ? src,
  # Leaves out the problem of a stale lock.
  allowStale ? false,
}:
let
  lock = lib.importJSON lockFile;
  formatVersion = 1;
  name = toString lockFile;
  fields = [
    "sdk"
    "targets"
    "definitionHash"
    "tools"
    "workloads"
    "projects"
    "artifacts"
    "localPackages"
    "sdkPackages"
    "contentHash"
  ];
  missingFields = lib.filter (field: !(lock ? ${field})) fields;

  # The SRI SHA-256 of the lockfile without its contentHash, as the JSON
  # builtins.toJSON writes: sorted keys and no whitespace, as
  # `content_hash` in cli/dotnet_purifier.py writes it too.
  contentHash = builtins.convertHash {
    hash = builtins.hashString "sha256" (builtins.toJSON (removeAttrs lock [ "contentHash" ]));
    hashAlgo = "sha256";
    toHashFormat = "sri";
  };

  # The hash that a packages.lock.json records for the package at `path`,
  # or null. NuGet's lock mode records the SHA-512 of each .nupkg as its
  # contentHash, which equals the file's hash for an unsigned package.
  nugetLockHash =
    file: path:
    let
      parts = lib.splitString "/" path;
      id = builtins.elemAt parts 0;
      version = builtins.elemAt parts 1;
      nugetLock = lib.importJSON "${packagesLock}/${file}";
      matches = lib.concatMap (
        packages:
        lib.mapAttrsToList (_: package: package.contentHash) (
          lib.filterAttrs (
            packageName: package:
            lib.toLower packageName == id
            && lib.toLower (package.resolved or "") == version
            && package ? contentHash
          ) packages
        )
      ) (builtins.attrValues (nugetLock.dependencies or { }));
    in
    if matches == [ ] then null else "sha512-${builtins.head matches}";

  unresolved = lib.filter (
    entry: entry ? hashFrom && nugetLockHash entry.hashFrom entry.path == null
  ) (lock.artifacts ++ lock.localPackages ++ lock.sdkPackages);

  # The packages.lock.json files of the build: NuGet's lock mode writes one
  # beside each project that uses it.
  nugetLocks = lib.filter (file: builtins.pathExists "${packagesLock}/${file}") (
    map (p: (if dirOf p.path == "." then "" else "${dirOf p.path}/") + "packages.lock.json") (
      lock.projects or [ ]
    )
  );

  # NuGet's own content hash of the package at `path`, from the first
  # packages.lock.json that records it, or null. A restore in lock mode
  # checks each installed package against it, and for a signed package it
  # differs from the hash of the file, since NuGet leaves the signature
  # out.
  nugetContentHash =
    path: lib.findFirst (hash: hash != null) null (map (file: nugetLockHash file path) nugetLocks);

  withHash =
    entry:
    let
      own =
        if entry ? hashFrom then
          removeAttrs entry [ "hashFrom" ] // { hash = nugetLockHash entry.hashFrom entry.path; }
        else
          entry;
      nugetHash = nugetContentHash entry.path;
    in
    own // lib.optionalAttrs (nugetHash != null) { nugetContentHash = nugetHash; };

  stale =
    import ./definition.nix {
      inherit lib src packagesLock;
      inherit (lock) targets;
    } != lock.definitionHash;

  relock = "Run `dotnet-purifier lock --force` at the build root and commit the lockfile.";

  problems =
    if (lock.version or null) != formatVersion then
      [
        "dotnet-purifier: ${name} has format ${
          toString (lock.version or "unknown")
        }; this dotnet-purifier reads format ${toString formatVersion}. ${relock}"
      ]
    else if missingFields != [ ] then
      [
        "dotnet-purifier: ${name} lacks ${lib.concatStringsSep ", " missingFields}, so it is not a lockfile `dotnet-purifier lock` wrote. ${relock}"
      ]
    else if lock.contentHash != contentHash then
      [
        "dotnet-purifier: ${name} was edited after `dotnet-purifier lock` wrote it: its content no longer has the hash it records. ${relock}"
      ]
    else
      lib.optional (stale && !allowStale)
        "dotnet-purifier: ${name} does not match the build: a project, solution, .props or .targets file, a NuGet configuration, global.json, the tool manifest, a packages.lock.json or a package of a folder feed changed since `dotnet-purifier lock` ran. Run `dotnet-purifier lock` at the build root and commit the lockfile, or pass `allowStaleLock = true` to build anyway."
      ++ map (
        entry:
        "dotnet-purifier: ${entry.hashFrom} no longer records ${entry.path}, whose hash ${name} reads from it. Run `dotnet-purifier lock` at the build root and commit the lockfile."
      ) unresolved;

  usable = problems == [ ];
in
{
  purifier = "dotnet-purifier";
  inherit problems;
  entries = if usable then map withHash lock.artifacts else [ ];
  outputs =
    if usable then lib.listToAttrs (map (p: lib.nameValuePair p.name p) lock.projects) else { };
}
// lib.optionalAttrs usable {
  inherit (lock)
    sdk
    targets
    tools
    workloads
    projects
    ;
  localPackages = map withHash lock.localPackages;
  sdkPackages = map withHash lock.sdkPackages;
}
