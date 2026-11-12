# SPDX-License-Identifier: MIT
#
# The `dotnet-purifier` library namespace, which holds the lock reader of
# the purifier contract:
#
#   lib.dotnet-purifier.readLock { src, lockFile ? src + "/dotnet-purifier.lock.json" }
#
# The builder needs a package set, so it is the package
# `buildDotnetProject` that the default package overlay adds.
{ ... }:
{

  overlay = final: prev: {
    dotnet-purifier = (prev.dotnet-purifier or { }) // {
      # The lock in memory of the purifier contract, from a build root and
      # its dotnet-purifier.lock.json (lib-overlays/default/lock.nix).
      readLock = args: import ./lock.nix ({ lib = final; } // args);
    };
  };

}
