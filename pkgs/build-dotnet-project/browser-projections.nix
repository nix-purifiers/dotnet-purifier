# SPDX-License-Identifier: MIT
{
  lib,
  purifier,
  runCommand,
  python3,
  packages,
  projects,
  targets,
}:
lib.mapAttrs (
  name: options:
  let
    project = projects.${name} or (throw "dotnet-purifier: unknown browser project ${name}");
    producer = packages.${name};
    resources = purifier.resourceProjections {
      inherit producer runCommand python3;
      name = "dotnet-${name}-browser-resources";
      roots."." = "lib/${name}/wwwroot";
    };
    webjar =
      if options ? webjar then
        purifier.webjarProjections (
          {
            inherit producer runCommand python3;
            name = "dotnet-${name}-browser-webjar";
            assets = resources.web.directory;
          }
          // options.webjar
        )
      else
        { };
  in
  assert lib.assertMsg (
    builtins.isAttrs options && builtins.attrNames (removeAttrs options [ "webjar" ]) == [ ]
  ) "dotnet-purifier: browser target ${name} accepts only optional webjar coordinates";
  assert lib.assertMsg (
    !(options ? webjar)
    || (
      builtins.isAttrs options.webjar
      &&
        builtins.attrNames (
          removeAttrs options.webjar [
            "groupId"
            "artifactId"
            "version"
          ]
        ) == [ ]
      && options.webjar ? artifactId
      && options.webjar ? version
    )
  ) "dotnet-purifier: WebJar coordinates require artifactId and version, with optional groupId";
  assert lib.assertMsg (
    project.kind == "exe" && lib.hasPrefix "browser-" (project.runtimeIdentifier or "")
  ) "dotnet-purifier: browser target ${name} must be a captured browser executable";
  resources // webjar
) targets
