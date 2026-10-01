# Gate 3 decoder MEMFS point-of-use observation

**Status: unreviewed offline observation; G3-L NO-GO.** This candidate traces
one bounded synthetic ecCodes decode in the Alpha development virtual
environment. It supplies runtime evidence for the installed MEMFS library's
use of definition bytes. It does not authenticate the library's upstream
source or build, qualify provider data, grant CCSDS use on a provider path,
or authorize launch, SHADOW promotion, or financial activity.

## Reproduction and exact inputs

From the repository root, run the committed candidate with:

```sh
ulimit -c 0
PYTHONDONTWRITEBYTECODE=1 /home/alphaadmin/AlphaV11_Dev/venv/bin/python \
  docs/V11_R09_GATE3_DECODER_POINTOFUSE_OBSERVATION_20261001_probe.py
```

The [full machine report](V11_R09_GATE3_DECODER_POINTOFUSE_OBSERVATION_20261001.json)
records the tested precommit worktree state, source and shim hashes,
compiler invocation, installed-library identity, negative controls,
all observed MEMFS calls, every path and per-call cross-check. Its
`repo_worktree_clean: false` reflects evidence regeneration before the
four-file candidate commit. The exact committed tree and postcommit run
are bound in the separate repair terminal. The
[shim](V11_R09_GATE3_DECODER_POINTOFUSE_OBSERVATION_20261001_shim.c)
source is compiled into a temporary shared object.
The target `libeccodes_memfs.so` was 39,767,864 bytes with SHA-256
`f984057845fd0a569dd775907d4629b83b09434959436863790492aae32e5fb9`.
The companion independently reviewed
[static inventory](V11_R09_GATE3_MEMFS_STATIC_OBSERVATION_20261001.md)
identifies those same installed bytes. Neither hash is an upstream signature.

## Interposition mechanism and observed results

The shim intercepts `codes_memfs_open` and `codes_memfs_exists`. It resolves
the real symbols from the already mapped exact library with
`dlopen(path, RTLD_NOLOAD | RTLD_LAZY)`, calls them, and records their
results. For an open stream, it copies the returned bytes to a temporary
file, restores the original stream position, and returns the real stream
to the decoder. Compilation uses the local host `gcc`; the compiled shim
is temporary and has no authority outside this probe. Each trace write and
flush is checked, including the final completion record. The capture child
calls the shim's completion function after decode and reports the call count
separately. The parser requires typed records with contiguous unique indices,
one final completion record, and matching counts. It refuses malformed,
duplicate, gapped, missing-suffix, missing-completion and truncated records.
The call count is observed, not a fixed acceptance constant.

The negative probe with a nonexistent library path and the shim active
aborted with the shim's `FATAL` diagnostic (exit `-6`). With the shim
absent, the same small decode succeeded and produced no trace. A `/dev/full`
trace sink caused a `FATAL` abort. The trace mutation controls rejected a
missing suffix, missing completion, duplicate, gap, invalid type and truncated
record. A harmless changed sibling source was rejected before its marker could
execute. The main
capture completed without stderr. It recorded **86** open calls over **71**
distinct paths and **90** exists calls over **90** paths. No open returned
null; `exists` returned true for 71 paths and false for 19. All 86 opened
byte streams matched both the static inventory's SHA-256 and an independent
direct read from the installed library at that inventory's offset and size.
All 90 exists values matched inventory presence. The
`/MEMFS/definitions/grib2/templates/template.5.42.def` open occurred
at call 169 and matched SHA-256
`54013f4db7aebac06cdd37b38243ae8e06871d9e8725dcedf2cbbc7362ad9827`.

The probe imports the unchanged committed bounded synthetic decode from the
[decoder build observation](V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001.md).
It compares the sibling bytes to the pinned reviewed commit
`95f03393467fd65e851721dedc9fffe991889552` and blob
`d814a6f5a9139647bc68d8a782d97b2046b62051` before executing exactly
the verified byte snapshot. This is a local source check, not authenticated
upstream provenance. The capture child blocks Python `socket.socket` and uses
60 CPU seconds and 512 MiB address-space limits. Compilation and negative
subprocesses have 60-second timeouts but do not inherit those resource limits;
the main capture has a 90-second wall timeout. The independent review used an
inherited native socket/connect seccomp denial, which is not a built-in probe
guarantee. Simple packing succeeded; both IFS ENS and AIFS ENS
synthetic CCSDS template 5.42 messages decoded to **290.0 K**. The
installed library SHA-256 and size matched before and after the capture.
The probe's `POINTOFUSE_OBSERVED_CONSISTENT` status means only that this
bounded observation's explicit internal checks passed.

## What remains open

This samples one synthetic decoder execution and its 176 intercepted
MEMFS calls, not every possible ecCodes path, all provider messages, or
every entry of the 7,073-path static inventory. The shim's passive
read-back may differ from a decoder run without instrumentation in ways
outside the reported output checks. The installed wheel's inconsistent
RECORD and missing authenticated upstream source/build lineage remain
unresolved in the decoder build observation. Review must independently
reproduce this exact candidate and assess the probe, shim and claims
before integration. Even an accepted point-of-use observation would not
close those build or G3-L requirements.

No provider request, service change, credential use, real order, financial
action, or V10/AxiomTrade action occurred. No G3-L PASS or forward SHADOW
sample follows from this result. The supplementary engineering score stays
**91/200 (45.5%), formal 1/50; NOT_READY_TO_FUND**.
