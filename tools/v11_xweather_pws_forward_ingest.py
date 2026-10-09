"""Forward (not retroactive) ingest of the separately-running Xweather
PWSweather collector's retained artifacts into a V11 EvidenceStore, so the
existing PWSQualityWorker (polymarket_scanner/v11/pws_runtime.py, provider=
'XWEATHER_PWSWEATHER') can compute live PWS defensive-QC health each cycle.

This is NOT the retroactive ABLATION replay in
tools/v11_xweather_pws_history_replay.py, which deliberately pins the store's
clock to each artifact's own historical `capture_received_epoch` so a
counterfactual replay can be scored against true historical receipt order.
This module does the opposite on purpose: every V11 receipt written here
(`received_at`/`available_at` on both the raw and normalized EvidenceStore
captures) is the store's own REAL clock at the moment this tool runs --
never backdated to the collector's receipt, and never to any other artifact
timestamp. The collector's own receipt is retained only as untrusted
provenance inside the payload (`collector_received_epoch`, `artifact`,
`artifact_sha256`, `raw_sha256`, `key_slot`), never as the EvidenceStore's
own notion of availability. Concretely: V11's receipt for a given artifact is
always >= the collector's receipt for it, generally by however long this tool
was last run after the collector wrote it. Any consumer that depends on
*first-receipt* precedence (e.g. a future receipt-time "lead" comparison)
must treat this conservative, later V11 receipt as the authoritative Alpha
receipt time for this provider -- it is never the collector's own artifact
timestamp substituted in its place.

Journal/artifact verification is REUSED, not reimplemented, from the already
reviewed `tools/v11_xweather_pws_forward_evidence` module (`_load_journal`,
`_read_bounded`, `_decode_raw`, `MAX_ARTIFACT_BYTES`) -- same SHA-pinned
journal lineage, same symlink/path-traversal refusal, same artifact-name
allowlist regex. This module does NOT reuse
`v11_xweather_pws_history_replay.verified_history`, because that function's
`MAX_REPLAY_ARTIFACTS` bound (400) is a whole-journal cap that will refuse
the entire, ever-growing retained journal once the live collector has written
that many artifacts (~13h at the observed ~2min cadence) -- exactly the
defect flagged in the Oct9 replay-QC evidence note. A forward ingest lane
must instead WINDOW (ingest only artifacts whose collector receipt falls in a
bounded, sliding, recent window) and bound the per-run ingest COUNT, never
raise a whole-history bound. Artifacts outside the window or already
ingested (by raw content hash) are never even read off disk.

Idempotent by raw content hash (`raw_sha256`): a record id is always
`xweather-raw:<raw_sha256>` / `xweather-normalized:<raw_sha256>`, so
re-running this tool over an overlapping artifact set never creates a second
receipt for content already archived, and never renews an existing receipt's
time. Artifacts are selected and ingested in ascending collector-receipt
order within each run. An artifact whose collector receipt is in the future
relative to the store's real clock, or older than the configured window, is
never ingested -- the former is refused outright (a future collector receipt
indicates clock skew or tampering and halts the run); the latter is silently
left for a human to decide whether a wider one-off backfill is warranted (out
of scope for this tool, which never raises its own window to "all history").

This tool performs NO network request and never opens, globs, or reads any
file whose name contains the collector's API key material; it only opens the
already-retained `journal.jsonl` and `xweather-*.json` artifact files.

Namespace: this ingests into a caller-supplied CHALLENGER:* namespace only --
the same already-reviewed convention `tools/v11_real_input_capture.py`'s own
`collect()` uses for genuine forward real-input capture
(`EvidenceStore(path, 'CHALLENGER:real-input-capture')`). The live financial
namespace `V11_PAPER` is refused unconditionally (no financial/paper/trading
authority is intended or granted anywhere in this module). `ABLATION:*` is
also refused here: it is reserved for the retroactive counterfactual replay,
whose clock-pinned-to-history design is the opposite of this module's real
forward receipt, and conflating the two namespaces would blur a real forward
QC signal with a replay that structurally cannot produce one.

Then calls the existing archive/QC path (`pws_quality.archive_neighborhood`,
the same function `pws_runtime.PWSQualityWorker.step()` calls internally) via
`PWSQualityWorker` itself for provider='XWEATHER_PWSWEATHER', using the
caller's own reviewed official/policy. No field anywhere in this module sets
financial_authority, settlement_authority, lead_advantage_verified, or any
other trading/settlement authority true.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, finite, identity
from polymarket_scanner.v11.weather_sources import PWS_PROVIDERS, XWEATHER_ENDPOINT, parse_xweather_json
from tools.v11_xweather_pws_forward_evidence import MAX_ARTIFACT_BYTES, _decode_raw, _load_journal, _read_bounded

VERSION = "alpha_v11_xweather_pws_forward_ingest_v1"
PROVIDER = "XWEATHER_PWSWEATHER"
EXPECTED_SCHEMA = "ALPHA_V11_XWEATHER_GROUPED_PWS_SOURCE_ONLY_V1"
INGEST_SCOPE = "FORWARD_REAL_RECEIPT_NOT_BACKDATED"
# Bounded per-run ingest count; any remaining backlog inside the window is
# simply left for the next run (an incremental forward sync, never a reason
# to widen this into a backlog raiser). 60 artifacts ~= the reviewed
# katl-madis-public-draft-20261008 policy's 7200s history_seconds at the
# collector's observed ~120s cadence.
MAX_ARTIFACTS_PER_RUN = 60
# Matches the reviewed policy's history_seconds; a window, never "all history".
DEFAULT_WINDOW_SECONDS = 7200.0


def _existing(store: EvidenceStore, key: str) -> dict | None:
    try:
        return store.get(key)
    except EvidenceError as exc:
        if str(exc) != "EVIDENCE_MISSING":
            raise
    return None


def select_forward_batch(root: Path, store: EvidenceStore, *, now: float, window_seconds: float,
                          max_artifacts: int = MAX_ARTIFACTS_PER_RUN) -> list[dict]:
    """Journal-verified, ascending-by-collector-receipt batch of artifacts that
    are (a) not already ingested into `store` (checked by raw content hash,
    cheaply, before any file is even opened) and (b) whose collector-declared
    receipt is >= `now - window_seconds` (older artifacts are simply skipped,
    not read). A verified artifact whose own `capture_received_epoch` turns
    out to be strictly greater than `now` is refused outright -- never
    silently dropped -- since that can only mean clock skew between hosts or
    tampering. `max_artifacts` additionally bounds how many verified,
    not-yet-ingested artifacts a single call returns; this is deliberately a
    per-run cap, not a whole-journal cap, so a long-running collector can
    never make this function refuse to operate at all (contrast
    `v11_xweather_pws_history_replay.verified_history`'s whole-journal
    MAX_REPLAY_ARTIFACTS bound, which this module does not reuse).
    """
    if not 0.0 < finite(window_seconds) <= 86400.0:
        raise EvidenceError("XWEATHER_FORWARD_WINDOW_INVALID")
    if type(max_artifacts) is not int or not 1 <= max_artifacts <= 200:
        raise EvidenceError("XWEATHER_FORWARD_BATCH_BOUND_INVALID")
    now = finite(now)
    lower_bound = now - window_seconds
    rows = _load_journal(root)
    # Only the journal-declared receipt (not yet independently verified) is
    # used for this cheap pre-filter; every row that survives it is still
    # fully hash/schema verified below before anything is trusted. Rows
    # outside the window are never opened at all. No upper bound is applied
    # here -- a declared-future row must reach the explicit refusal below,
    # not be silently filtered away as if it were simply "too old".
    candidates = [row for row in rows if finite(row["received_epoch"]) >= lower_bound]
    out: list[dict] = []
    for row in sorted(candidates, key=lambda r: (r["received_epoch"], r["artifact"])):
        raw_id = "xweather-raw:" + row["source_sha256"]
        if _existing(store, raw_id) is not None:
            continue
        path = root / row["artifact"]
        blob = _read_bounded(path, MAX_ARTIFACT_BYTES)
        if hashlib.sha256(blob).hexdigest() != row["artifact_sha256"]:
            raise EvidenceError("XWEATHER_FORWARD_ARTIFACT_SHA_JOURNAL_MISMATCH:" + row["artifact"])
        record = json.loads(blob)
        if type(record) is not dict:
            raise EvidenceError("XWEATHER_FORWARD_ARTIFACT_SHAPE")
        if record.get("schema") != EXPECTED_SCHEMA:
            raise EvidenceError("XWEATHER_FORWARD_SCHEMA_UNRECOGNIZED")
        if record.get("endpoint") != XWEATHER_ENDPOINT:
            raise EvidenceError("XWEATHER_FORWARD_ENDPOINT_UNRECOGNIZED")
        if record.get("raw_sha256") != row["source_sha256"]:
            raise EvidenceError("XWEATHER_FORWARD_RAW_SHA_JOURNAL_MISMATCH:" + row["artifact"])
        received_epoch = finite(record.get("capture_received_epoch"))
        if received_epoch != finite(row["received_epoch"]):
            raise EvidenceError("XWEATHER_FORWARD_RECEIPT_JOURNAL_MISMATCH:" + row["artifact"])
        if received_epoch > now:
            raise EvidenceError("XWEATHER_FORWARD_COLLECTOR_RECEIPT_IN_FUTURE:" + row["artifact"])
        raw_body = _decode_raw(record)
        params = record.get("request_parameters")
        if type(params) is not dict:
            raise EvidenceError("XWEATHER_FORWARD_PARAMS_MISSING")
        out.append(dict(artifact=row["artifact"], key_slot=int(row["key_slot"]),
                         artifact_sha256=row["artifact_sha256"], collector_received_epoch=received_epoch,
                         raw_sha256=row["source_sha256"], raw_body=raw_body, params=params))
        if len(out) >= max_artifacts:
            break
    return out


def open_forward_store(path: Path, namespace: str) -> EvidenceStore:
    """One real, forward-receipt EvidenceStore; refuses V11_PAPER and ABLATION:*.

    CHALLENGER:* reuses the namespace convention already reviewed for genuine
    forward real-input capture in `tools/v11_real_input_capture.py`'s own
    `collect()`. No other namespace family is an established convention for
    this kind of forward, non-ablation, non-production capture, so anything
    else -- including the live V11_PAPER namespace -- is refused rather than
    guessed at.
    """
    if not namespace.startswith("CHALLENGER:"):
        raise EvidenceError("XWEATHER_FORWARD_NAMESPACE_MUST_BE_CHALLENGER")
    return EvidenceStore(path, namespace)


def ingest_forward_batch(store: EvidenceStore, batch: list[dict], *, event_id: str, station: str,
                         latitude: float, longitude: float) -> list[dict]:
    """Idempotently archive each selected artifact using the STORE'S OWN real
    clock (never the collector's `capture_received_epoch`, never backdated)
    as the V11 `received_at`/`available_at`. The collector's own receipt is
    retained only as provenance inside the payload.

    Idempotent by `raw_sha256`: re-running this over an overlapping batch
    never creates a second receipt for content already archived, and never
    renews an existing receipt's time (the existing record is returned
    unchanged). `parse_xweather_json` is always called with its own DEFAULT
    observation-age gate, exactly as `pws_quality`'s normalized-vs-raw
    re-parse calls it; widening it here would archive observations the
    reviewed QC re-parse drops, and QC would then (correctly) refuse every
    such row as PWS_NORMALIZED_RAW_MISMATCH. The age is measured against the
    conservative (later) V11 receipt, so backlog observations older than that
    default are dropped, never re-timed.
    """
    identity(event_id)
    identity(station, maximum=32)
    channel = PWS_PROVIDERS[PROVIDER]["channel_prefix"] + station
    results = []
    for art in batch:
        raw_id = "xweather-raw:" + art["raw_sha256"]
        normalized_id = "xweather-normalized:" + art["raw_sha256"]
        prior_raw = _existing(store, raw_id)
        if prior_raw is not None:
            raw, duplicate = prior_raw, True
        else:
            raw = store.capture(raw_id, event_id=event_id, kind="PWS_OBSERVATION", provider=PROVIDER,
                source_identity=channel, revision="FORWARD_INGEST",
                payload=dict(raw_body=art["raw_body"].decode("utf-8"), raw_sha256=art["raw_sha256"],
                             request_parameters=art["params"], station_latitude=latitude,
                             station_longitude=longitude, artifact=art["artifact"], key_slot=art["key_slot"],
                             artifact_sha256=art["artifact_sha256"],
                             collector_received_epoch=art["collector_received_epoch"],
                             ingest_scope=INGEST_SCOPE),
                evidence_class="PUBLIC_OBSERVED")
            duplicate = False
        prior_normalized = _existing(store, normalized_id)
        if prior_normalized is not None:
            normalized = prior_normalized
        else:
            parsed = parse_xweather_json(art["raw_body"], raw_sha256=art["raw_sha256"],
                received_at=raw["body"]["received_at"], params=art["params"], latitude=latitude,
                longitude=longitude)
            observed = max((finite(o["observed_at"]) for o in parsed["observations"]), default=None)
            derived = dict(parsed, raw_evidence_id=raw["id"], raw_evidence_sha256=raw["sha256"],
                          feature_ready_at=raw["body"]["received_at"], settlement_station_context=station,
                          observed_time_scope="LATEST_ACCEPTED_PROVIDER_OBSERVATION", ingest_scope=INGEST_SCOPE)
            normalized = store.capture(normalized_id, event_id=event_id, kind="PWS_OBSERVATION", provider=PROVIDER,
                source_identity=channel, revision="FORWARD_INGEST", payload=derived, observed_at=observed,
                evidence_class="PUBLIC_OBSERVED")
        results.append(dict(artifact=art["artifact"], raw_id=raw["id"], normalized_id=normalized["id"],
                            duplicate_content_skipped=duplicate,
                            v11_receipt_minus_collector_receipt_seconds=(
                                raw["body"]["received_at"] - art["collector_received_epoch"])))
    return results


def run_quality_step(store: EvidenceStore, *, event_id: str, official, policy, command_id: str,
                     health_policy=None, sync_probe=None) -> dict:
    """Invoke the existing, UNMODIFIED `PWSQualityWorker` / `archive_neighborhood`
    path for provider='XWEATHER_PWSWEATHER', using whatever XWEATHER_PWSWEATHER
    PWS_OBSERVATION captures already exist in `store` for this event/station
    (this run's forward ingest plus any prior run's).

    `PWSQualityWorker.step()` first requires a fresh RuntimeHealth clock-
    liveness sample with no `clock_reasons`. On a brand-new store this is
    `CLOCK_RECOVERY_SAMPLES_PENDING` for the first `recovery_samples`
    consecutive samples spaced >= `recovery_spacing_seconds` apart in real
    monotonic time -- i.e. this genuinely needs >=2 separate real invocations
    of this tool, spaced apart in real wall-clock time, against the SAME
    persistent store, before a QC record is produced. That is intentional
    (see module docstring) and is exactly why the real forward evidence run
    for this tool is performed twice, a few minutes apart.

    `health_policy`/`sync_probe` default to a real, conservative policy and
    the real local clock-sync probe (`runtime_health.local_sync_status`);
    tests may inject a synthetic policy/probe (same
    `SYNTHETIC_OFF_HOST_FIXTURE` mechanism `test_v11_runtime_health.py`
    already uses) so recovery can be exercised without a real wall-clock wait.
    """
    from polymarket_scanner.v11.pws_runtime import PWSQualityPlan, PWSQualitySettings, PWSQualityWorker
    from polymarket_scanner.v11.runtime_health import HealthPolicy, RuntimeHealth, SourceNeed
    from polymarket_scanner.v11.runtime_health import local_sync_status
    policy_obj = health_policy or HealthPolicy(VERSION, 120.0, 120.0, 2.0, 2, 60.0, ("forward-ingest",))
    health = RuntimeHealth(store, policy_obj,
        account_id="xweather-forward-ingest-no-account", scopes={event_id: ("PWS_OBSERVATION_LEAD",)},
        sources=(SourceNeed(event_id, "PWS_OBSERVATION_LEAD", "PWS_OBSERVATION", "ALPHA_PWS_QC",
                            official.station, policy.fresh_seconds),),
        sync_probe=sync_probe or local_sync_status)
    plan = PWSQualityPlan(event_id, official, policy, provider=PROVIDER)
    worker = PWSQualityWorker(store, health, PWSQualitySettings((plan,)))
    return worker.step(command_id)


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xweather-root", type=Path, default=Path("/home/alphaadmin/AlphaV11_XweatherEvaluation"))
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--namespace", required=True, help="Must start with CHALLENGER:")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--station", required=True)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--window-seconds", type=float, default=DEFAULT_WINDOW_SECONDS)
    parser.add_argument("--max-artifacts", type=int, default=MAX_ARTIFACTS_PER_RUN)
    parser.add_argument("--config", type=Path, default=None,
                        help="Optional reviewed real-input config (tools/v11_real_input_capture.load_plan "
                             "shape); when given, also runs the PWSQualityWorker QC step afterward.")
    parser.add_argument("--command-id", default=None, help="Required with --config.")
    args = parser.parse_args(argv)

    try:
        store = open_forward_store(args.store, args.namespace)
        now = store.clock()
        batch = select_forward_batch(args.xweather_root, store, now=now, window_seconds=args.window_seconds,
                                      max_artifacts=args.max_artifacts)
        ingested = ingest_forward_batch(store, batch, event_id=args.event_id, station=args.station,
            latitude=args.latitude, longitude=args.longitude)
        out = dict(version=VERSION, ingest_scope=INGEST_SCOPE, selected=len(batch), ingested=ingested,
                   financial_authority=False, settlement_authority=False, lead_advantage_verified=False,
                   trading_influence_permitted=False)
        if args.config is not None:
            if not args.command_id:
                raise EvidenceError("XWEATHER_FORWARD_COMMAND_ID_REQUIRED_WITH_CONFIG")
            from tools.v11_real_input_capture import load_plan
            plan = load_plan(args.config)
            qc = run_quality_step(store, event_id=args.event_id, official=plan.official, policy=plan.quality,
                                  command_id=args.command_id)
            out["qc"] = dict(qc["body"]["details"])
        print(json.dumps(out, sort_keys=True, default=str))
        return 0
    except (EvidenceError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps(dict(outcome="GATED", reason=str(exc), financial_authority=False,
                              acceptance_granted=False), sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
