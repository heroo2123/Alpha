# R09 Gate 3 launch-contract addendum: independent review

**Verdict: PASS for the offline G3-P addendum only.** Reviewer: Codex
Sol/high, independent of the Astra/high author. Exact reviewed commit
`14c2413c4cef63dab35a806955fcdd3ef0942f69`, tree
`d37d7ad55a76902cae0536e6f4d21b9db6aeaa81`, parent `866a6a5`.
The completed terminal is
`V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413_terminal.json`.

The commit changes only the addendum and three coordinator documents. The
original Gate 3 protocol and historical PASS reviews remain intact. This
review accepts the addendum's clarification that the 64 KiB/25-point GEFS CGI
subregion decoder is inapplicable to the proposed S3 full-field path. It
supersedes only the old G3-P/G3-I prose that treated that CGI bound as a Gate 3
full-field bound. The reviewed 2 MiB offline GEFS preflight remains the
applicable proposal. It does not approve an S3 origin, operational release,
full-grid decoder, transport or network request.

The addendum preserves the 3 MiB index, 4 MiB IFS/AIFS field and 1 GiB total
received-body ceilings. The 2 MiB GEFS field ceiling is below the shared
4 MiB bound. The observed-size JSON matches cited SHA-256
`efefd2396c35ac672e18094d611c7a956b709f7deeabb99d1e3e2f1831211eeb`.
Per-provider maxima reproduce 775/1,275/663 slots and
190,036,975/857,962,800/421,234,398 estimated raw bytes: 2,713 slots and
1,469,234,173 bytes total, 395,492,349 above 1 GiB before overhead. The
helper's 2,125-slot fallback is correctly rejected as a launch schedule. A
bounded attempt must keep the complete denominator, count probes and failed
bodies, reserve before requests, and persist uncertain charges.

I independently repeated all six offline counterexamples with the project
venv and synthetic `make_manifest` fixture. A shortened three-slot dossier
passes `require_launch_prerequisites`; the empty-ID fixture passes too; a real
40-hex Git SHA-1 OID raises `MANIFEST_COLLECTOR_COMMIT_REQUIRED`; a one-byte
exhausted total budget permits another `begin_request`; completing its extra
byte raises `NOT_ATTEMPTED_BUDGET` while the counter stays at one; and the
feasibility calculation yields the totals above. The prior probe artifact
matches SHA-256
`31c2339a2e89cf7334c1babbec099272cd738c19ca1806d6f05c51a50eb32e63`.
These are offline helper defects, with no real transport present. They remain
required G3-I extension fixes; this PASS does not accept those helpers for
launch.

The proposed `R09_GATE3_LAUNCH_MANIFEST_V2` keeps the payload outside Git and
protected authority, uses closed typed JSON and canonical bytes, separates
actual Git OIDs from artifact SHA-256s, freezes code/dossier/cohort/time/source/
network/schedule identities, and puts authorization in a detached exact-digest
review envelope. The referenced repository `canonical` returns UTF-8 encoded,
sorted-key, compact, ASCII-escaped JSON with NaN disabled. Git reports SHA-1
object format here; HEAD and tree are 40-hex OIDs. Review and runtime must
compare the same payload digest. Missing, changed, contradictory or expired
review cannot authorize a request. The addendum explicitly grants no
bootstrap probe permission; a necessary live preflight needs its own bounded,
independently reviewed proposal first.

No P1/P2 design finding in this scoped review. The schema is a specification,
not an implemented validator or private manifest. Before G3-L, independent
review must cover the strict validator, durable reservation/stream accounting,
real transport/decoder/clock/store, actual source and access dossiers, and
the exact private manifest digest. No capture, G3-E corpus, Gate 4 learner,
Gate 5 SHADOW, model authority, publication or financial permission follows.
Score remains **91/200, formal 1/50; NOT_READY_TO_FUND**.

Verification: independent source tracing and synthetic offline reproduction;
`git diff --check 866a6a5..14c2413` clean. No code or production tests changed
in the reviewed commit, so a full regression is not claimed. The private
FINAL-REVIEWED master SHA-256 still matches its pinned value
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a`.
