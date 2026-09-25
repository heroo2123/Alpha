# Local independent PAPER guardian verification

Base published commit: `02fe3f2c28882b04f32381df395f17baa79d590b`.
Base tree: `2e75cf8d79ebe38bb6bce5cd044efacc61bd2936`.
Local source preserves that history; no production host/service or V10 change.

Scope: independently scheduled finite Linux PAPER process, durable cancellation
recovery, original guardian lease at common-account/maker admission and inside the
write transaction, typed candidate requirement, resource limits and bounded input.
All account/market/model/health fixtures are synthetic. No empirical calibration,
exchange cancellation, authenticated identity or operating acceptance is inferred.

The tests use WSL2 Ubuntu 24.04.2 LTS and the existing locked Python 3.11.16 venv.
They execute canonical Git-clean-filter inputs on a native Linux mirror, preserving
executable modes and avoiding Windows CRLF/mode artifacts. Bounded tmpfs test
fixtures retain failed cases; monitoring/locks and source/mirror hash checks use
the existing external verification runner. No private source or runtime evidence
is copied into this document or Git.

Early checks retained for transparency:

- Collection attempt: one pytest collection error (`request` is reserved as a
  parameter name); no test pass claimed. Renamed the test parameter.
- First mechanics run: 35 passed, two failed / 10.87 s. Both were new fixture
  errors: an incomplete synthetic terminal proof and passing transition keyword
  arguments directly to a signal callback. Corrected both fixtures.
- Integrated development run: 284 passed, two failed / 46.42 s. Both were new
  maker fixture expectations: the existing revalidation API takes only quote ID,
  and proposal commit failures produce a GATED audit rather than raising. Tests
  now assert the actual retirement/gating behavior and unchanged quote exposure.

Final focused: **46 passed / 13.53 s / exit 0**, session **96608**. Both new test
modules passed, including actual sibling process stop/death, busy candidate/FD
failure, guardian stop/death, SQLite contention/restart, archive exhaustion, bounded
malformed/deep JSON, environment/resources, cancellation interruption/recovery,
late explicit reconciliation, account/basket/maker lease fences and transaction
expiry. Candidate default identities and the explicit guardian requirement passed.
The health publication interleaving test retains conservative cancellation and
does not claim acceptable continuous cancellation frequency.

Full integrated regression: **4294 passed, four existing FastAPI deprecation
warnings / 600.37 s / exit 0**, session **55623**. The runner's separate monotonic
elapsed measure is 576.53 s; the quoted test duration is pytest's own result.
All 771 source/mirror canonical inputs remained unchanged. Peak monitored fixture
storage was 457,412,608 bytes; maximum reported child RSS 174,976 KiB. No resource
stop reason or remaining test/guardian process. This full run includes both earlier
PWS continuations and the final guardian code; the older 4195-pass run remains
attributed only to its temperature replay milestone.

Exact final canonical-input map: **771 non-document files**, SHA-256
`bd3956d5ca5d5d848909eeb02f68535dc60f717996c122205bc80d4e51aa8dd9`.
Focused and full source/mirror identities remained unchanged before/after execution.
All ten staged source/test blobs were separately compared with that manifest.
Core canonical blob SHA-256 identities:

| File | SHA-256 |
|---|---|
| `polymarket_scanner/v11/guardian_lease.py` | `ea9a8c1717cae78ec74d395472559183ce575a46141890960bc279d2f06fcbf7` |
| `polymarket_scanner/v11/paper_guardian.py` | `49f40750afdbedb56924088fbd1c63c9a421afdc5c374ce66f4a82d97214a75e` |
| `tests/test_v11_paper_guardian.py` | `68cb427139fdfc9d820453aa4c72cb4e09b7fb30727290e571a76194c0f0f686` |

External local verification metadata/logs (not copied into Git):

- Root: `/home/hero_/.local/share/alpha-v11-tools/canonical-verification-20260925T155035Z/`.
- Manifest: `non-document-input-manifest.json` under that root.
- Focused result: `evidence/20260925T155049.076048Z-guardian-final-focused/result.json`.
- Full run: `evidence/20260925T155219.681215Z-guardian-full-regression/`.
- Focused output SHA-256: `09c812b1dc276dd362631c85e9f528fba1268cc51b5d777d69a98d14c15f7fb5`.
- Full output SHA-256: `a5953492410e0109b2c9cee716cd52b87a3a5d17dfe4904601da9cac9f61267e`.

The publishing commit/tree is resolved without a self-referential hash using
`git log -1 --format='%H %T' -- docs/V11_GUARDIAN_ISOLATION_EVIDENCE.md`.

Review before testing corrected durable pending-plan retention across restart,
an uncatchable terminal wall alarm, archive-limit configuration binding, bounded
deep-JSON failure handling and the explicit sanitized launch helper. Read-only
peer checks are implementation review, not independent production/security
commissioning or full original-spec acceptance.

R37 earns only its newly demonstrated bounded core and integration units: C/J.
Prior 83 + 2 = **85/200 = 42.5%, approximately 43%**; formal **1/50 (2%)**.
No E/A, authentication, deployment or independent acceptance credit. Before
publication, the exact staged set was reviewed: V11 source, synthetic tests and
documentation only; no private input, raw evidence database, secret/credential or
wallet material. All five authoritative private input hashes still match both
manifests; private contents were not copied or emitted by the check.

Unpassed gates: supported cancel-only authentication; separate protected UID and
state/credential custody; network and resource isolation from an untrusted candidate;
external order/terminal reconciliation and GTD; isolated deployment, restart/reboot
and operational capacity; coherent health publication and actual churn/latency;
independent security/acceptance review. NOT_READY_TO_FUND; V10 DEFERRED.
