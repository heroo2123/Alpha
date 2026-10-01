# Gate 3 A5/A6 retained-source and causality audit

**A5/A6 NOT QUALIFIED; G3-L NO-GO.** Astra/high completed the routed
offline acceptance audit against main `b3cea1c`. This is an inventory and
acceptance finding, not approval of a source dossier, new protocol, provider
request, decoder or launch. Missing identities remain null in the
[machine observations](V11_R09_GATE3_A5A6_OFFLINE_AUDIT_20261001.json).
The [acceptance criteria](V11_R09_GATE3_IDENTITY_ACCEPTANCE_20261001.md)
remain unchanged. No C/J/E/A crossing: **91/200, formal 1/50;
NOT_READY_TO_FUND**.

## Evidence actually recovered

The stdlib-only [audit](../tools/v11_r09_gate3_a5a6_audit.py) reads retained
manifests, response bodies, receipt JSON, indexes and GRIB headers. It makes no
network request, loads no native decoder, and reads no decoded forecast values
or labels. It asserts hashes/lengths and index/range relationships, emitting
diagnostic header hashes, never launch pins. Raw bodies remain in their
existing private stores. This local read does not retrospectively authorize
the historical collection or certify its collection clocks.

| Retained evidence | Fresh observation | Acceptance limit |
| --- | --- | --- |
| `capture-v1` | Manifest matches public export; all 30 response bodies / 21,203,504 bytes match; includes 22 successful indexes, five 404s, a 429, a 503, one listing; 29 additional planned indexes were not attempted | Historical exploration; no GRIB fields; no separate receipt directory; release binding remains `OBSERVED_HEADER_ONLY` |
| `capture-current-1c8836a` | Manifest matches public export; all 17 response bodies / 11,868,660 bytes and 17 receipt files match; four indexes, 12 IFS fields and one listing | Name means current at historical capture time, not current for Gate 3; all field runs are September 29 00Z and receipts September 30 |
| Twelve IFS fields | Actual raw-header templates 4.8/4.11, packing 5.42; each 206 range length and object total agree with the body; exactly one selected index row agrees with range, member, parameter and step | Native max/min interval fields, not the requested instantaneous `2t` trajectory fields; original release documentation and causal four-phase receipts absent |
| Six loose exploratory fields plus `initial-metadata.json` | All six raw SHA-256s and index hashes/selected rows match; four IFS extrema fields and two AIFS instantaneous `2t` fields | No response receipt is bound by this loose metadata; no authenticated original release or independent pre-acquisition section pin |
| Two loose AIFS fields | September 29 00Z, template 4.1, members 0/1, ensemble size 51, data type 10, process ID 2, packing 5.42, regular 1440 x 721 grid | Narrow actual header observation; no full 51-member/native-hour coverage, freshness or A5 release qualification |

The loose IFS files and capture objects can duplicate content; these counts
are artifact observations, not independent samples. Section hashes computed
from those files are **post-receipt diagnostics**. The audit explicitly leaves
`independently_frozen_section_pins` null. It does not decode, re-encode or
resource-qualify CCSDS.

The historical point-panel stores retain GRIB/index hash references rather
than original GRIB bodies, as documented in
[the reviewed panel](V11_R09_MULTIMODEL_HISTORICAL_PANEL.md). Their relational
integrity cannot recover the original source bytes or historical receipt.
The named repositories, manifests, source documents and retained capture roots
are the scope of this audit; it is not an assertion that no additional evidence
exists anywhere on the host or outside it.

## Which independently reviewed support can be reused

1. The `23c11e0` mapping report and completed terminal agree on report hash
   `d2a617ce9155b1b1614210510e04bfd09bdd27620236e6cfee6a2c74650bd1af`.
   Main's entire `v11_r09_gate3_launch_v4.py` is byte-identical to that reviewed
   source, SHA-256 `a2fe4d99666ef744927cfba49de03726b3fc695a98e7836dc1f3642781cbe978`.
   Its independent PASS supports exact adapter grammar, shared ECMWF perturbed
   object semantics and refusal of unsupported purposes. It expressly grants
   **no real provider qualification**. It can support the historical code-review
   inventory entry after proper assembly, not fill the compound source identities.
2. The original independent native-extrema reviewer output was recovered at
   `/home/alphaadmin/AlphaV11_R09ExtremaReview/review.log` and hashed in the
   machine report. It records actual reproduction of both captures, header
   semantics and 312 passes / two skips, with a completed
   `R09_EXTREMA_INDEPENDENT_REVIEW_PASS` marker. Those are **prior reviewer
   results**, not tests rerun here. They support the historical research findings
   integrated from `fef0d0d`; they do not attest operational model releases,
   future-run readiness, denial expiry or launch authority. This log is not a
   detached G3-L terminal or an assembled sealed `review_ref`.
3. The accepted static MEMFS inventory and A2/A3 dossier retain their previously
   stated installed-byte scope. Neither supplies provider release semantics or
   independent current-run section pins. The queued A1 repair cannot close A5/A6.

This audit produces new independently inspected historical byte observations;
it does not independently approve its own generator or a launch package.

## Provider-by-provider A5/A6 result

| Provider | Supported static or historical facts | Missing acceptance evidence |
| --- | --- | --- |
| GEFS | Reviewed bounded CGI source behavior and offline refusal of guessed direct-GET mappings; historical point-panel integrity has separate scope | Original operational release/access documents and retrieval lineage, effective run interval, separately qualified full-field S3/index/purpose contracts, real header/grid/packing comparison for that path, exact selected-run readiness/index/object/range and independently frozen sections |
| IFS | Adapter grammar and independent native-extrema research review; freshly verified historical control/perturbed native fields and ranges | Release-document bytes/provenance and effective interval, licence/access and restriction adjudication, exact operational control mapping attestation, instantaneous `2t` header qualification, qualified decoder/build, all required real purpose contracts and current-run pins |
| AIFS | Adapter grammar; limited historical AIFS index evidence; newly rehashed loose point fields for control/member 1 | Release-document bytes/provenance and effective interval, licence/access and restriction adjudication, complete independently reviewed semantic profile, qualified decoder/build, required real purpose contracts, original coherent receipt/object identity and current-run pins |

For **all three providers**, all eight `sources.<provider>_*` qualified
identities remain null: operational dossier, document retrieval, access,
control mapping, GRIB/decoder build, purpose contracts, current-run range and
publication-attestation-or-absence explanation. This does not mean every
supporting fact is missing. A publication attestation may legitimately remain
absent with an independently reviewed reason; it must not be fabricated or
treated as a mandatory provider publication timestamp.

A source-code comment naming IFS Cycle 50r1 and a matching release-signature
hash do not provide original operational release bytes or their effective
interval. Metadata/document links recorded in old prose also do not supply
retained originals and retrieval provenance. No missing identity was filled
from those substitutes.

## Restriction lineage: additional retained evidence

The public export omitted response headers, but the raw manifests retain a
selected subset. The September 30 07:54:39 S3 503 has a `Date` header; the
07:55:15 public-origin 429 has an empty retained header map. Neither supplies
`Retry-After` or authenticated expiry. The collector's inspected source retains
only Date, Last-Modified, ETag, Content-Range and Retry-After when present;
these records are not a full wire-header transcript.

An **earlier S3 503 at 07:48:13.707996 UTC September 30** is preserved in
`aws-retry.json` with its 278-byte `aws-retry-body`; the body matches SHA-256
`7c21325b9a8c5d3b7f06bed411ae11e6fa6dcb490320671bd8e1d49a64956a28`.
It also has no retained Retry-After. This expands the known restriction
history; it cannot shorten a hold. Later successful public-origin responses
remain observations of success, not proof of permitted resumption or separate
control domains. Expiry/resumption adjudication stays null for all three records.

The IFS field receipts preserve ETag strings and object totals. The strings
are stored without HTTP entity-tag quotes. They are not directly acceptable
to the current strong-ETag verifier. Rehashing or adding quotes now cannot
recover a full original wire transcript or independently prove immutable
object coherence. This audit makes no claim about the provider's ETag design.

## Exact bootstrap dependency and permissible next work

For the proposal-only **October 2 14:00–17:00 UTC acquisition / October 3
target**, a normal 18:00 UTC decision with the frozen 24-hour age limit and
00Z cycle selects October 2 00Z if genuinely ready. September 29 00Z is
90 hours before that decision and cannot qualify. At this audit's October 1
time, the proposed October 2 run has not occurred. The prep checker also
requires run-scoped observations at or after that run; repackaging old evidence
must preserve its original observation time. The prospective date remains an
unapproved proposal, not rolled-forward launch permission.

The blocked dependency is:

`G3-L PASS requires reviewed current-run readiness + index/object/range +
independent section pins -> those inputs are absent from retained bytes ->
obtaining them here would require a provider request -> owner permits no such
request before G3-L PASS.`

This is a real bootstrap block for the inspected evidence and authorization,
not a reason to disable a check. The protocol addendum discusses a separately
reviewed bounded preflight, but the owner's later, stricter rule supplies no
exception. A shape-valid inventory, another origin, a later calendar date,
synthetic receipts, self-derived expected hashes or the queued A1 success
cannot break the dependency. All first real requests remain prohibited.

Two resolution routes can be described offline, neither approved here:

- Supply genuinely pre-existing, independently reviewed evidence for the exact
  eligible run/path, with original provenance and timing. This must include a
  defensible independently frozen section-pin source; historical examples do
  not suffice. Do not initiate an external transfer under the current rule.
- Seek separate owner/protocol resolution of the bounded-preflight dependency,
  followed by exact independent review of any resulting concrete proposal and
  restriction treatment before any request. Approval cannot be inferred from
  this audit or from permission for R1. No gate or protocol edit is made here.

Even resolving that dependency would leave the deliberate real-mapping
refusals, A2/A3 provenance, A4 verification, A7 decoder/resources and A8
transport/storage/time composition open. Continue the already queued A1 repair
and independently inspect its corrected exact terminal when it exists; preserve
the separately active inventory review. Further offline work can recover
authenticated originals or prepare a concrete dependency-resolution packet.
Repeated rehashing of these same historical captures will not advance A5/A6.

Verification: the reproducible audit assertions passed; all inputs above are
hashed in the machine report. No full release suite was run. The previously
accepted release `6ec371e` and its 5,460-pass / 13-skip result are unchanged.
No provider, service, authority, financial, V10, AxiomTrade or publication action.
