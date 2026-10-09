"""Offline, retroactive replay of already-retained Xweather PWSweather captures
into the PWS defensive-QC pipeline (pws_quality.archive_neighborhood).

This exists because the live NOAA MADIS/CWOP public capture failed R09's own
QC floor (16 stations, each with fewer than 2 distinct samples inside the
600s continuity window) while a *separate*, already-running Xweather cron
collector (`/home/alphaadmin/AlphaV11_XweatherEvaluation/`) has been polling
the same KATL neighborhood every two minutes for hours. That history is real;
MADIS is just thin. This module makes the Xweather history usable without
inventing a single timestamp: every receipt time written here is the
artifact's own `capture_received_epoch`, taken from the retained collector
output, never wall-clock "now" at replay time.

Journal/artifact verification is REUSED, not reimplemented, from the already
reviewed `tools/v11_xweather_pws_forward_evidence.py` (`_load_journal`,
`_read_bounded`, `_decode_raw`) -- same SHA-pinned journal lineage, same
symlink/path-traversal refusal, same artifact-name allowlist regex.

Hard separation from live PAPER state (two independent safeguards, not one):
  1. Namespace: records land in an ABLATION:* EvidenceStore, never V11_PAPER.
     `pws_quality.neighborhood()` already sets trading_influence_permitted,
     settlement_authority and lead_advantage_verified to False unconditionally;
     the ABLATION namespace additionally makes this store structurally
     incapable of being mistaken for a live PAPER ledger.
  2. Clock: the replay store's own clock is advanced artifact-by-artifact
     through each artifact's true historical `capture_received_epoch`, in
     ascending order. EvidenceStore._append enforces CLOCK_REGRESSION across
     the whole store, so this can never be pointed at the live PAPER database
     -- doing so would either regress its clock (refused) or require running
     "ahead" of real time (refused by capture()'s SOURCE_TIME_IN_FUTURE / the
     store's own `at = finite(self.clock())` ceiling). A genuinely LIVE future
     receipt is a different, unmodified code path (tools/v11_xweather_pws_ingest
     style collection -> normalize_weather_capture -> PWSQualityWorker.step);
     this module never substitutes for it.

Known, explicit, undismissed limit (see handoff): PWSSample.provider and
samples_from_capture/current_neighborhood_heads/archive_neighborhood in
pws_quality.py (and the XWEATHER_* additions in weather_sources.py) are both
Gate-3 current-executable-binding-pinned files
(docs/V11_GATE3_CURRENT_EXECUTABLE_BINDING_20261007.json). Widening them to
accept the second provider is a real, necessary code change -- but it is a
protected root requiring an independent Gate-3 owner's manifest repin before
any live PAPER run may use it. This module and its tests exercise the real
QC/archive machinery directly and prove it accepts genuine multi-receipt
Xweather history; they do not and cannot flip the live Gate-3 admission gate,
and the current-executable-binding test suite is expected to fail until that
independent repin happens. No attempt is made here to edit that manifest or
otherwise route around the failing gate.
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

VERSION = "alpha_v11_xweather_pws_history_replay_v1"
PROVIDER = "XWEATHER_PWSWEATHER"
EXPECTED_SCHEMA = "ALPHA_V11_XWEATHER_GROUPED_PWS_SOURCE_ONLY_V1"
REPLAY_SCOPE = "RETROACTIVE_COUNTERFACTUAL_NOT_LIVE_TRADING_INPUT"
MAX_REPLAY_ARTIFACTS = 400


def _existing(store, key):
    try:
        return store.get(key)
    except EvidenceError as exc:
        if str(exc) != "EVIDENCE_MISSING":
            raise
    return None


def verified_history(root: Path, *, max_artifacts: int = MAX_REPLAY_ARTIFACTS) -> list[dict]:
    """Journal-verified, ascending-by-true-receipt list of retained artifacts.

    Reuses the already-reviewed journal/hash verification from
    `v11_xweather_pws_forward_evidence`; only the final raw-bytes + request
    params assembly needed for QC replay (rather than that module's reduced
    per-station diagnostic projection) is added here.
    """
    rows = _load_journal(root)
    if len(rows) > max_artifacts:
        raise EvidenceError("XWEATHER_REPLAY_ARTIFACT_BOUND")
    out = []
    for row in rows:
        path = root / row["artifact"]
        blob = _read_bounded(path, MAX_ARTIFACT_BYTES)
        if hashlib.sha256(blob).hexdigest() != row["artifact_sha256"]:
            raise EvidenceError("XWEATHER_ARTIFACT_SHA_JOURNAL_MISMATCH:" + row["artifact"])
        record = json.loads(blob)
        if type(record) is not dict:
            raise EvidenceError("XWEATHER_ARTIFACT_SHAPE")
        if record.get("schema") != EXPECTED_SCHEMA:
            raise EvidenceError("XWEATHER_SCHEMA_UNRECOGNIZED")
        if record.get("endpoint") != XWEATHER_ENDPOINT:
            raise EvidenceError("XWEATHER_ENDPOINT_UNRECOGNIZED")
        if record.get("raw_sha256") != row["source_sha256"]:
            raise EvidenceError("XWEATHER_RAW_SHA_JOURNAL_MISMATCH:" + row["artifact"])
        raw_body = _decode_raw(record)
        params = record.get("request_parameters")
        if type(params) is not dict:
            raise EvidenceError("XWEATHER_PARAMS_MISSING")
        received_epoch = finite(record.get("capture_received_epoch"))
        out.append(dict(artifact=row["artifact"], key_slot=int(row["key_slot"]),
                         received_epoch=received_epoch, raw_sha256=row["source_sha256"],
                         raw_body=raw_body, params=params))
    out.sort(key=lambda r: (r["received_epoch"], r["artifact"]))
    return out


def open_replay_store(path: Path, namespace: str) -> EvidenceStore:
    """One dedicated ABLATION-namespace store per replay; refuses V11_PAPER."""
    if not namespace.startswith("ABLATION:"):
        raise EvidenceError("XWEATHER_REPLAY_NAMESPACE_MUST_BE_ABLATION")
    return EvidenceStore(path, namespace, clock=lambda: open_replay_store.clock_box[0])


open_replay_store.clock_box = [0.0]


def ingest_history(store: EvidenceStore, artifacts: list[dict], *, event_id: str, station: str,
                    latitude: float, longitude: float) -> list[dict]:
    """Idempotently archive each verified artifact's raw+normalized observation.

    Record ids are keyed by the artifact's own content hash (`raw_sha256`),
    never by filename or ingest order, so re-running this over an overlapping
    artifact set never fabricates a second receipt for content already
    archived -- the first (earliest `received_epoch`) capture of a given
    raw_sha256 wins and later duplicates are skipped, matching the existing
    `first_received_at = min(...)` dedup rule inside pws_quality.neighborhood.

    Artifacts are processed in ascending `received_epoch` order regardless of
    the order passed in: whichever artifact is processed first for a given
    raw_sha256 becomes the permanent receipt for that content, so the caller
    cannot retroactively install a later receipt as "first" by handing this
    function an out-of-order list.
    """
    identity(event_id)
    identity(station, maximum=32)
    channel = PWS_PROVIDERS[PROVIDER]["channel_prefix"] + station
    results = []
    for art in sorted(artifacts, key=lambda a: (a["received_epoch"], a["artifact"])):
        raw_id = "xweather-raw:" + art["raw_sha256"]
        normalized_id = "xweather-normalized:" + art["raw_sha256"]
        prior_raw = _existing(store, raw_id)
        if prior_raw is not None:
            raw, duplicate = prior_raw, True
        else:
            open_replay_store.clock_box[0] = art["received_epoch"]
            raw = store.capture(raw_id, event_id=event_id, kind="PWS_OBSERVATION", provider=PROVIDER,
                source_identity=channel, revision="RETROACTIVE_REPLAY",
                payload=dict(raw_body=art["raw_body"].decode("utf-8"), raw_sha256=art["raw_sha256"],
                             request_parameters=art["params"], station_latitude=latitude,
                             station_longitude=longitude, artifact=art["artifact"], key_slot=art["key_slot"],
                             replay_scope=REPLAY_SCOPE),
                evidence_class="PUBLIC_OBSERVED")
            duplicate = False
        prior_normalized = _existing(store, normalized_id)
        if prior_normalized is not None:
            normalized = prior_normalized
        else:
            open_replay_store.clock_box[0] = raw["body"]["received_at"]
            parsed = parse_xweather_json(art["raw_body"], raw_sha256=art["raw_sha256"],
                received_at=raw["body"]["received_at"], params=art["params"],
                latitude=latitude, longitude=longitude)
            observed = max((finite(o["observed_at"]) for o in parsed["observations"]), default=None)
            derived = dict(parsed, raw_evidence_id=raw["id"], raw_evidence_sha256=raw["sha256"],
                          feature_ready_at=raw["body"]["received_at"], settlement_station_context=station,
                          observed_time_scope="LATEST_ACCEPTED_PROVIDER_OBSERVATION", replay_scope=REPLAY_SCOPE)
            normalized = store.capture(normalized_id, event_id=event_id, kind="PWS_OBSERVATION", provider=PROVIDER,
                source_identity=channel, revision="RETROACTIVE_REPLAY", payload=derived, observed_at=observed,
                evidence_class="PUBLIC_OBSERVED")
        results.append(dict(artifact=art["artifact"], raw_id=raw["id"], normalized_id=normalized["id"],
                            duplicate_content_skipped=duplicate))
    return results


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xweather-root", type=Path, default=Path("/home/alphaadmin/AlphaV11_XweatherEvaluation"))
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--namespace", required=True, help="Must start with ABLATION:")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--station", required=True)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument("--longitude", type=float, required=True)
    args = parser.parse_args(argv)

    history = verified_history(args.xweather_root)
    store = open_replay_store(args.store, args.namespace)
    results = ingest_history(store, history, event_id=args.event_id, station=args.station,
        latitude=args.latitude, longitude=args.longitude)
    print(json.dumps(dict(version=VERSION, replay_scope=REPLAY_SCOPE, artifact_count=len(history),
                          ingested=results, financial_authority=False, settlement_authority=False,
                          lead_advantage_verified=False, trading_influence_permitted=False), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
