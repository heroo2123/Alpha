# Gate 3 provider-rights and restriction lineage — 2026-10-07

**Offline candidate for independent acceptance review. G3-L remains NO-GO.**
This package builds a deterministic lineage of every retained provider contact
and restriction relevant to the G3-L `sources.*` and `network.*` identities. It
grants no provider right, permission, resumption, qualification credit, launch
or execution authority. No provider/network request, credential, service,
order, money or root action was used. Score unchanged: **91/200; formal 1/50;
NOT_READY_TO_FUND**.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `docs/V11_GATE3_PROVIDER_RIGHTS_LINEAGE_20261007.json` | 68,591 | `6035af4eb5f401f83a23230a887ba8f02dfd8d47d5486951341ecdf13af75c76` |
| `tools/v11_gate3_provider_rights_lineage.py` | builder, closed-schema checker, request-envelope evaluator | — |
| `tests/test_v11_gate3_provider_rights_lineage.py` | 138 offline adversarial tests | — |
| `docs/review-evidence/provider-rights-lineage-20261007/*.body` | 5 recovered denial bodies | file name = SHA-256 |

Rebuild (needs the retained private roots on this host) and check (repo only):

```
python -m tools.v11_gate3_provider_rights_lineage build    # matches_committed: true
python -m tools.v11_gate3_provider_rights_lineage check    # problems: []
```

The builder pins 27 sources. It reads and verifies 25 of them (whole-file,
append-only-prefix or file-set-manifest bindings). The other 2 are raw files that
are now gone, bound only by their recorded digests. Any changed byte refuses the
build. SQLite ledgers are
hashed first, then queried from those same bytes in memory. Every cited
phrase is checked against the pinned bytes before it is used.

## Permission model

Two notions are kept separate in the schema, checker and evaluator:

* **Observed public anonymous access.** A retained response proves that a request
  was made and answered. Every contact is classified
  `OBSERVED_PUBLIC_ANONYMOUS_ACCESS_NOT_PERMISSION`. The checker rejects any
  promotion, and `later_successes_clear_hold` must stay `false`.
* **Reviewed legal/operational permission.** This needs an independently reviewed,
  dated record that binds original licence/access-term bytes (distinct from the
  review artifact) to exact origins and purposes. **No such record exists.**
  The read-only search found no licence, terms or disclaimer bytes for NOAA, NWS,
  NCEP, ECMWF or the AWS Registry; only URLs are retained.
  All three providers are therefore `NO_REVIEWED_PERMISSION`.

`evaluate_request` returns only `REFUSED` or
`ENVELOPE_CONSISTENT_NOT_AUTHORIZED`, and always `execution_authority: false`.
Against the committed lineage it refuses every real request. Three things are
missing for each: an unheld domain, a reviewed permission and a reviewed
origin/path/purpose spec. Even a positive result would still need P1's
executable package, transport review and owner exception.

## New retained facts (each bound to pinned bytes)

### ECMWF: 12 restriction events, not 3

The P1 `restriction-history.json` holds three records. Retained evidence shows a
larger history across **three** origins: the S3 bucket, the `data.ecmwf.int`
portal and the CloudFront distribution `d2zvc0wgha4k2l.cloudfront.net`.

**2026-09-29 (S3 backfill, before the recorded holds)**

* One field `FAILED HTTP_503`, after **three attempts**, at 09:57:24Z.
* 459 `ECMWF_HTTP_STATUS_OR_RANGE_IGNORED` failures between 12:07Z and 21:25Z.
  Their status codes were not retained.
* 385 messages completed only after a retry: 533 failed attempts in total,
  statuses not retained. These may overlap the 459 above.
* The backfill moved about 35.6 GB at concurrency 4.

**2026-09-30 (from the R09 transcript)**

* **Four S3 503s** of 278 bytes on 2026-08-23 index paths. Their times were not
  retained; they precede 07:46:43Z.
* 07:46:43Z: S3 `503 SlowDown` (`MRTQHTPR43KKPS2B`).
* 07:46:44Z: CloudFront `503 SlowDown` (`MRTS8Z1XKG3M5N15`, `x-cache: Error from cloudfront`).
* The three pinned denials: S3 503 at 07:48:13Z, S3 503 at 07:54:39Z and portal 429 at 07:55:15Z.

**Bodies.** Every S3/CloudFront 503 whose body survives is `SlowDown`
("Please reduce your request rate"). The portal 429 body is exactly
`Too Many Requests`. No Retry-After or expiry appears anywhere.

**Custody restored.** The P1 binding's raw files under
`AlphaV11_R09Extrema/Alpha/private-evidence/` are gone, and no copy exists by
hash under `/home/alphaadmin`. All three pinned bodies are now recovered and
committed, and each matches its retained digest:

* 07:48:13Z — rebuilt from that record's retained request-id/host-id headers.
* 07:54:39Z — byte-exact from the transcript.
* 07:55:15Z — `Too Many Requests`.

The record digests pinned by the preflight checker still verify. The two
07:46 bodies are transcript text and have no original digest; they are labelled
that way.

### NOAA GEFS: its own restriction history and an origin switch

**2026-10-04 15:44:51–15:47:03Z.** The NOMADS filter returned **HTTP 302**
(742-byte body, not retained) five times to the Brain-universe harvest. The
harvest code edited afterwards stops on `302` plus `Over Rate Limit` as
`NOAA_AKAMAI_OVER_RATE_LIMIT`. The code version that ran during the 302s is not
retained, so retry counts are unknown.

**After the 302s:**

* A noaa-gefs-pds **S3** harvest was written at 16:08Z and ran at
  16:20:21–16:22:06Z. It made 1,178 GETs at concurrency 6 with
  `follow_redirects=True`. This is P1's proposed origin, used as an origin switch.
* NOMADS collection continued: harvest captures through 2026-10-05 17:56Z, and
  Shadow collection through Oct 7. The Oct 6 ledger alone holds 4,635 SUCCESS
  results, spot-checked.

**2026-09-29.** The NOAA S3 backfill (about 5.2 GB, concurrency 6, redirects
followed) had **264 messages whose first attempt failed**. The status was not
retained.

**Consequence.** P1's restriction history omits every NOAA record. The protocol
forbids inferring a separate control domain from hostnames, so NOMADS and S3 share
one `NOAA_GEFS` hold. Retained evidence cannot support P1's required
`SCOPE_INDEPENDENCE_CONFIRMED`, so P1 stays blocked on stronger grounds.

### Transport findings (historical clients, not Gate-3 code)

* **T1.** The Shadow and Brain NOMADS clients use httpx's default `trust_env=True`,
  and `PublicCollector` does not refuse that setting. The 2026-09-24 off-host probe
  environment had a `socks5h` proxy, and that probe's result file is not retained.
* **T2.** Retries happened after restrictions.
* **T3.** Both S3 clients followed redirects.
* **T4.** Volume and concurrency far exceed every Gate-3 budget.
* **T5.** Origins were switched after denials, which is failover.
* **T6.** No retained record or client uses credentials, cookies or signed URLs.
* **T7.** Custody gaps, as described above.

Historical access therefore looks anonymous. It does **not** evidence a
no-proxy, no-retry, no-redirect adapter.

## Identity impact (30 in-scope identities)

| Disposition | Count | Identities |
| --- | ---: | --- |
| Offline candidate, needs independent review | 1 | `network.restriction_domain_lineage` (this package; every retained event, all HELD) |
| Can be adjudicated offline only as HELD | 1 | `network.ecmwf_503_429_expiry_resumption_review` (no Retry-After/expiry anywhere; resumption needs provider evidence) |
| External provider document or right | 19 | `sources.*_{operational_release_dossier, release_document_retrieval, licence_anonymous_access, control_perturbed_mapping, purpose_endpoint_contracts, publication_attestation_or_absence_reason}` ×3; `network.exact_origin_path_purpose_allowlist` |
| Forward run observation | 3 | `sources.*_current_run_index_object_range` |
| Future implementation and review | 5 | `sources.*_grib_identity_decoder_build` ×3; `network.dns_tls_build_peer_policy`; `network.anonymous_no_retry_credential_policy` |
| Owner decision | 1 | `network.preflight_approval_receipts_if_used` |

**Closed by this package: 0. Qualification credit: 0.** The G3-L audit stays at
77 missing before and after. A PASS of independent review would make
`network.restriction_domain_lineage` sealable as a scoped lineage. That lineage
covers the enumerated retained roots only, with unknown statuses held. No other
identity can move without new external, forward or implementation evidence.

## What still needs a fresh external, right or forward observation

1. Authentic licence/access-term bytes for NOAA/NCEP GEFS (NOMADS and AWS NODD)
   and for ECMWF open data (portal, S3, CloudFront), with retrieval provenance and
   an independent permission review. Retrieval is itself a provider request under
   the owner rule.
2. Provider-side expiry or resumption evidence for the ECMWF and NOAA_GEFS
   holds. Elapsed time, later 200s and a different origin do not count.
3. Operational release dossiers, control/perturbed attestations and real-purpose
   endpoint contracts for GEFS, IFS and AIFS.
4. Run-scoped index/object/range receipts for the selected run.
5. A concrete Gate-3 transport with reviewed DNS/TLS/peer, no-proxy (`trust_env`
   off), no-redirect and no-retry enforcement.
6. An owner/protocol decision on the preflight route.

## Verification

* New suite: **138 passed** under normal Python and under `python -O`. Specific
  codes use explicit asserts; `pytest.raises(match=)` is not relied on.
* Rebuild from retained sources is byte-identical to the committed artifact.
* A guard fixture denies any socket connect or DNS, and an AST test bans
  network/subprocess imports.
* Mutation pass: 10 safety mutants were all killed in both modes:
  * held domain allowed;
  * pinned-denial tamper unchecked;
  * `trust_env` ignored;
  * permission supersession ignored;
  * later success clears hold;
  * required events unenforced;
  * range/response mismatch;
  * Retry-After not carried;
  * unknown accounting accepted;
  * domain narrowing.
* Adjacent suites: preflight checker, G3-L prep, identity audit and launch V4 gave
  575 passed and 1 failed. The failure is
  `test_retained_evidence_counts_and_current_runtime_drift`. It predates this lane
  (collector/ledgers/runtime baseline drift, also reported by today's
  identity-reduction lane). This lane changes no tracked file.
* Custody note: the read-only inventory opens left zero-byte `-wal`/`-shm`
  sidecars, with 2026-10-07 11:30–11:35Z mtimes, beside the retained BrainWork and
  S3 SQLite files. The main database bytes still match their pins.

## Independent review requested

Review this exact commit and artifact hash. Verify these points:

* the 27 source pins and quote checks;
* the five recovered bodies against their digests;
* the 12 ECMWF and 6 NOAA events;
* the claim that no retained fact supports permission, resumption or NOAA scope
  independence;
* every identity disposition;
* the evaluator's refusal surface.

Allowed verdicts: `PASS_IN_SCOPE_LINEAGE_NO_CREDIT` or `CHANGES_REQUIRED`.
Neither permits a request.
