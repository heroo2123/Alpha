# Gate 3 ECMWF restriction lineage: offline inventory, 2026-10-01 11:50 UTC

**G3-L NO-GO for these origins on present evidence.** This is a read-only
inventory of existing exploratory captures, not a provider-access approval,
cooldown expiry decision, preflight permission, or launch envelope. No request
was sent to a provider for this inventory.

Source: `config/v11/r09_ecmwf_extrema_public_evidence.json`, SHA-256
`6fd3fc1defd4b9d10459903a9543c3118cbc34e27e3665588463d68140cb6f11`.
Its `capture-v1` and `capture-current-1c8836a` records are historical
exploration, not Gate 3 shared-ledger receipts. The exported index rows do not
contain response headers or Retry-After values. Their body hashes and receipt
timestamps identify the preserved observations but do not establish the
provider's intended cooldown or permission to resume.

| UTC receipt | Origin | Status | Preserved index identity | Bytes / body SHA-256 |
| --- | --- | ---: | --- | --- |
| 2026-09-30 07:54:39.435641 | `ecmwf-forecasts.s3.eu-central-1.amazonaws.com` | 503 | `/20260823/00z/ifs/0p25/oper/20260823000000-3h-oper-fc.index` | 278 / `0986be0818f5c4e80bddac64bcd37d8f77da4c43acb380aaa7e4bcb677a51460` |
| 2026-09-30 07:55:15.376499 | `data.ecmwf.int` | 429 | `/forecasts/20260928/00z/ifs/0p25/enfo/20260928000000-12h-enfo-ef.index` | 17 / `3850dfdbf4489250268b5f0740240a9f4445e7c5c29e1d03aa0c5446808d7507` |

`capture-v1` also marks 29 index rows
`NOT_ATTEMPTED_ORIGIN_THROTTLED`. The separate
`capture-current-1c8836a` records four later 200 responses from
`data.ecmwf.int`, at 08:04:37, 08:05:11, 08:05:43 and 08:05:59 UTC on
September 30. Those responses occurred after the 429. They demonstrate that
requests were made and succeeded; they do **not** prove that the 429's
restriction expired, that the later requests were permitted, or that the S3
503 and public-origin 429 belonged to unrelated control domains.

Before any preflight or G3-L PASS, an independent review must reconcile both
origins with the provider/control-domain grouping, prior jobs and denial
history; recover raw status, headers, original clock and any provider expiry
evidence if available; and establish the applicable hold or leave it unresolved.
The documented later 200s must be included in that review, not used as a
cooldown shortcut. Missing headers/expiry remain `UNKNOWN`; the passage of a
calendar day does not supply them. A newly chosen origin cannot erase a
related domain's unresolved history.

The separately reviewed bounded preflight proposal required by the launch
addendum must list the exact origin, purpose, path, maximum requests and bytes,
schedule, response/denial handling and the above history before any network
request. Current-run index/object/range identity, source/access dossiers,
decoder qualification, clock/storage evidence, accepted runtime and exact
private manifest remain separate open inputs. No 14:00 UTC / 17:00 Kuwait
capture is authorized by this inventory.
