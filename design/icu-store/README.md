# Private Darwin ICU store diagnostic

This candidate registers `current-icu-store-diagnostic` only in Darwin checks.
It has not executed a Darwin runtime. The original `current-app-runs` and all
other mandatory checks remain present. Product wrappers, SDK selection, sandbox
profiles and network permissions do not change. The cache-only preflight includes
both the selected ICU output and declared `psutil` closure. An always-run read-only
retainer copies available diagnostic JSON and local NAR metadata without
building or replaying any check when flake-check fails.
Missing/null output metadata is partial failure evidence, not realized-output proof.

`probe.nix` takes the existing pure current App and the exact runtime selected by
that App's builder. It does not select another SDK/runtime. Four private copies
of the byte-identical App run with/without versioned symlinks to declared Apple
ICU output and with/without the app-local ICU environment value. The program and its
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
An outer controller starts a new session and process group before imports, runtime
identity capture, dependency inspection and case execution. It polls scratch
(128 MiB), aggregate resident memory (2 GiB) and inherited-process-group members (8),
with a 120-second deadline and 512-KiB helper output cap. Sampling uses only the
declared Python/`psutil` helper, with a one-second/256-KiB bound. It enumerates
PIDs, filters by the owned process group before querying RSS, and rechecks group
membership afterward. Only owned-group rows and declared provider identities are
returned. An exited PID is skipped; access denial is a failure. Missing/denied/malformed sampling is a
failure. Only the controller-created process group is signalled. The local descendant
cleanup test covers inherited group membership. Descendants that change group
or session escape this accounting/cleanup; there is no whole-session or general
process-tree containment claim. The fixed probe and fixture App do not request
daemonization or group/session changes.

These are polled controller/input bounds, not hard OS or Nix daemon quotas.
A sample may take one second; termination has a five-second allowance. Setup and
identity are inside the outer deadline. Scratch scanning does not follow store
symlinks. The controller gives the helper private HOME/TMPDIR; their exact values
are retained and shared by all four case controls. Core dumps are disabled;
there is no process-wide file-size, virtual-memory, GC or globalization-mode
limit. Local Python tests exercise guard failures and owned descendant cleanup;
actual Darwin sampler behavior, cache trust and runtime results remain unproven.

The preceding Apple `ps` 237 diagnostic exited with `rss: requires entitlement`
before collecting any sample. The replacement preserves RSS accounting through
`psutil`'s macOS `PROC_PIDTASKINFO` resident-byte query. It does not remove the RSS
limit or use a host `ps` fallback. The module and extension paths/hashes and helper
identity are retained; the supplied module closure must pass the cache-only guard.
This source rationale does not establish that the API succeeds in the Darwin sandbox.

Before publication/native execution, independently review the frozen source and exact
Darwin evaluation, enforce the existing cache-only guard for the selected ICU
output closure plus the exact declared `psutil` output, and obtain the manager's native CI authorization. No local native
run, new host/profile access, SDK/default activation or source toolchain build is
authorized by this document. The fixture must remain separate from current
mandatory success gates and must not suppress their failures.
