# R09 Gate 3 GEFS full-field ceiling: independent review

Verdict: **PASS for the offline G3-I correction only**. Reviewer: Codex Sol/medium,
independent of the Sonnet/high authoring pass. Reviewed exact candidate
`95e07fab75a3818560378d71556efc55ce122a56` (tree
`6a0880b2b7dcf101edc246e80ea999b814cfae08`) against parent `9311649`.
Merged locally as `5d7ea98`; no publication or collection authorized.

- The change is limited to the offline collector, observed-size tool, their two
  tests, and the derived JSON. `BudgetTracker.begin_request` applies the new
  2 MiB GEFS ceiling to the S3 full-field path; its caller-configured 4 MiB
  shared maximum can still only tighten it. An over-ceiling request is refused
  before its request counter advances. IFS/AIFS retain the 4 MiB ceiling.
- The 2 MiB value exceeds the observed GEFS maximum of 245,209 B by more than
  eightfold and remains below the shared 4 MiB ceiling. The 64 KiB bound in
  `grib_fields.MAX_BYTES` applies to the production NOMADS CGI subregion path,
  not Gate 3's S3 `.idx` plus byte-range full-field path. The old bound would
  refuse all 33,759 observed GEFS messages.
- The JSON's two source SHA-256 values are unchanged from the pre-fix file and
  independently match the read-only SQLite files: ECMWF
  `dc007dc2c4482f7170b9b0ab2a1805738d2d04f91bd733507bf142b3f3426725`,
  GEFS `af2802f4cf204f76fa62c8ba231599ac0596a0e07cbaaf209b7b31a95c00e36e`.
  Only the ceiling and derived finding/usable fields change. The new JSON SHA-256
  is `efefd2396c35ac672e18094d611c7a956b709f7deeabb99d1e3e2f1831211eeb`.
- Searched executable Gate 3 tool and test paths for the old 64 KiB constant;
  no caller retains it. Historical review/protocol prose still cites it and
  must be reconciled in a fresh G3-L review, not silently treated as launch
  authority. The collector still has no real network transport.

Verification at exact candidate: 52 focused passed; affected Gate 3,
trajectory, panel and GEFS suites 265 passed, 1 skipped. Merged-tree focused
suite: 52 passed. `git diff --check 9311649..95e07fa` passed. The first
affected-suite command used a nonexistent test filename and ran zero tests;
the corrected command produced the result above.

G3-L, actual capture, corpus admission, model fit and forward SHADOW remain
OPEN. The changed bound is a change to previously reviewed G3-I assumptions;
the launch reviewer must explicitly rebind G3-P/G3-I and the exact private
manifest digest under protocol Section 7. This PASS gives no network, service,
financial, host or model-promotion authority. Score remains 91/200, formal
1/50, NOT_READY_TO_FUND.
