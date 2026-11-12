# SPDX-License-Identifier: MIT
#
# The locked packages installed in a NuGet package folder, as NuGet
# installs them (package-folder.py). A build starts with these packages in
# its package folder, so the parts of the SDK that look a package up there
# before they read a feed find it offline: the resolver of MSBuild SDKs and
# `dotnet tool restore`. A restore reads the folder as its feed.
{
  runCommand,
  python3,
  # The downloaded packages, laid out at their `path` (dependencies.nix).
  dependencies,
  # The download entries, { path, url, hash }.
  downloads,
  # The packages of folder feeds in the build, { path, file, hash }, with
  # `file` relative to `src`.
  localPackages,
  # The build root the local packages come from.
  src,
  # The packages of the SDK's own folder feed, { path, file, hash }, with
  # `file` relative to that feed.
  sdkPackages,
  # The SDK whose feed they come from.
  sdk,
}:
let
  # Each package with the hash of its file, and the content hash NuGet
  # records for it: NuGet's own, from a packages.lock.json, where the
  # entry carries one.
  package = file: entry: {
    inherit (entry) path hash;
    inherit file;
    nugetContentHash = entry.nugetContentHash or entry.hash;
  };
  packages =
    map (d: package "${dependencies}/${d.path}" d) downloads
    ++ map (p: package "${src}/${p.file}" p) localPackages
    ++ map (p: package "${sdk}/share/dotnet/library-packs/${p.file}" p) sdkPackages;
in
runCommand "dotnet-purifier-packages"
  {
    nativeBuildInputs = [ python3 ];
    passAsFile = [ "packages" ];
    packages = builtins.toJSON packages;
  }
  ''
    python3 ${./package-folder.py} "$packagesPath" "$out"
  ''
