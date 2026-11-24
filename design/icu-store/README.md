# Private Darwin ICU store diagnostic

This source is deliberately absent from `checks`, workflows and product wrappers.
It has not executed a Darwin runtime. The original `current-app-runs` check and
all public source, sandbox, network and artifact contracts remain unchanged.

`probe.nix` takes the existing pure current App and the exact runtime selected by
that App's builder. It does not select another SDK/runtime. Four private copies
of the byte-identical App run with/without versioned symlinks to declared Apple
ICU 78.1 and with/without the app-local ICU environment value. The program and its
runtimeconfig/deps files do not change. Existing project app-local selection or
invariant configuration makes the experiment stop, rather than overwrite it.

The receipt binds selected runtime/CoreLib candidate identities, the original App
files/configuration, candidate ICU bytes/load metadata, and each exact command,
cwd, selected environment, combined output, exit and stop reason. Candidate file
identities do not prove which assemblies or native libraries were loaded. The
experiment is observational: negative cases remain in a completed receipt, and
`storeCaseExpectedAppResult` means only that the App printed the expected JSON.
No culture/sorting/CLDR/ABI compatibility or product fix follows from that alone.

The App inventory is limited to 16 MiB and 2,048 entries; four copies total at
most 64 MiB. ICU files are symlinks into the immutable store. Each child has a
15-second/64-KiB combined-output bound; runtime identity has two such commands.
The four case commands share a 120-second controller deadline including setup
and identity capture. Only the helper's own direct child is killed. Core dumps
are disabled; there is no process-wide file-size limit or GC/runtime switch.
These are controller/input bounds, not a hard Nix daemon disk or memory quota.
The proposed 128-MiB scratch budget has no continuous disk monitor yet; source
and resource review must reconcile that before authorizing native execution.

Before registration/execution, independently review the frozen source and exact
Darwin evaluation, enforce the existing cache-only guard for the selected ICU
output closure, and obtain the manager's native CI authorization. No local native
run, new host/profile access, SDK/default activation or source toolchain build is
authorized by this document. The fixture must remain separate from current
mandatory success gates and must not suppress their failures.
