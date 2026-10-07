"""Offline provider access-right and restriction-lineage package for Gate 3.

This module builds, checks and applies a deterministic lineage of every
retained provider contact and restriction relevant to the G3-L ``sources.*``
and ``network.*`` identities. It is pure local evidence handling: it imports
no socket/HTTP/provider client, runs no subprocess, performs no DNS, and
writes nothing except an explicitly requested output path.

Two notions are kept apart throughout:

* ``OBSERVED_PUBLIC_ANONYMOUS_ACCESS`` -- a retained historical response
  proves that a request was made and answered. It is never permission.
* ``REVIEWED_LEGAL_OPERATIONAL_PERMISSION`` -- an independently reviewed,
  dated record binding original licence/access-term bytes to an exact
  origin/purpose set. None exists in retained evidence today.

The request-envelope evaluator never returns permission. Its best outcome is
``ENVELOPE_CONSISTENT_NOT_AUTHORIZED``: a real request additionally needs the
separately reviewed executable package, transport and owner exception the
preflight protocol requires. A lineage check PASS confers no G3-L credit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Optional
from urllib.parse import unquote, urlsplit

from tools.v11_gate3_evidence_preflight_checker import (
    RETAINED_DENIAL_DIGESTS, _retained_record_digest,
)
from tools.v11_r09_gate3_g3l_prep import INDEX_CAP, PROVIDERS, REQUIRED, RUN_SPECIFIC
from tools.v11_r09_gate3_launch import FIELD_LIMITS

SCHEMA = "ALPHA_V11_G3_PROVIDER_RIGHTS_LINEAGE_V1"
ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = "docs/V11_GATE3_PROVIDER_RIGHTS_LINEAGE_20261007.json"
RECOVERED_DIR = "docs/review-evidence/provider-rights-lineage-20261007"
OBSERVED = "OBSERVED_PUBLIC_ANONYMOUS_ACCESS_NOT_PERMISSION"
ENVELOPE_OK = "ENVELOPE_CONSISTENT_NOT_AUTHORIZED"
REFUSED = "REFUSED"
HOLD_CARRY = "HOLD_UNTIL_INDEPENDENT_RESUMPTION_REVIEW"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")
IN_SCOPE_IDS = tuple(f"sources.{n}" for n in REQUIRED["sources"]) + tuple(
    f"network.{n}" for n in REQUIRED["network"])


class LineageError(Exception):
    """Retained input is missing, changed, or not checkable."""


# ---------------------------------------------------------------------------
# Small strict helpers
# ---------------------------------------------------------------------------

def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=1, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def strict_loads(raw: bytes) -> Any:
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise LineageError(f"duplicate key {key!r}")
            out[key] = value
        return out

    def constant(token):
        raise LineageError(f"nonfinite constant {token}")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except LineageError:
        raise
    except ValueError as exc:
        raise LineageError(f"invalid JSON: {exc}") from None


def parse_utc(value: Any) -> Optional[datetime]:
    if type(value) is not str or not UTC_RE.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None


def epoch_to_utc(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso_to_utc(value: str) -> str:
    """Normalize a retained ``+00:00`` timestamp without changing its instant."""
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise LineageError("retained timestamp is not explicit UTC")
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read_regular(path: Path, limit: int) -> bytes:
    """Read one bounded regular file without following a final symlink."""
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise LineageError(f"SOURCE_UNAVAILABLE:{path}:{exc.strerror}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise LineageError(f"SOURCE_NOT_REGULAR:{path}")
        chunks, size = [], 0
        while chunk := os.read(fd, 1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise LineageError(f"SOURCE_TOO_LARGE:{path}")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Pinned retained inputs. A changed byte refuses the build; nothing is
# re-pinned automatically. Append-only logs are bound by an exact prefix.
# RECORD_DIGEST_ONLY sources are not read: their bytes are gone from the
# recorded path and are bound only through digests retained elsewhere.
# ---------------------------------------------------------------------------

AS_OF_UTC = "2026-10-07T12:00:00Z"
BASELINE_COMMIT = "d1c5602aa77e0d835e416d281b4a78754a3a79df"
P1_ROOT = "/home/alphaadmin/AlphaV11_Gate3EvidencePreflight/20261002-gefs-index-v1"
R09_ROOT = "/home/alphaadmin/AlphaV11_R09Extrema"
R09_PRIVATE = R09_ROOT + "/Alpha/private-evidence/r09-extrema"
BRAINWORK = "/home/alphaadmin/AlphaV11_BrainWork"
NOMADS_ROOT = "/home/alphaadmin/AlphaV11_BrainForwardUniverse"
S3_ROOT = "/home/alphaadmin/AlphaV11_BrainForwardUniverseS3"

# (source_id, path, binding, byte_length, sha256, note)
PINNED_SOURCES = (
    ("ecmwf_public_export", "config/v11/r09_ecmwf_extrema_public_evidence.json", "WHOLE_FILE", 75340,
     "6fd3fc1defd4b9d10459903a9543c3118cbc34e27e3665588463d68140cb6f11",
     "Public export of ECMWF exploratory captures capture-v1 and capture-current-1c8836a."),
    ("a5a6_audit", "docs/V11_R09_GATE3_A5A6_OFFLINE_AUDIT_20261001.json", "WHOLE_FILE", 59867,
     "1535356757b1ed2cc9069378deb51dae3e4e3b829b58ddf157c54e1d213513fa",
     "Accepted-scope A5/A6 retained-source audit; recovered the 07:48:13 S3 503."),
    ("p1_protocol", "docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_PROTOCOL_20261002.md", "WHOLE_FILE", 20274,
     "ae59812fa58ec41895b87408f0ea175988ae6d20a1f4d5dabcd96a01c2dd3a68",
     "Evidence-only preflight protocol P1: limits, hold carry-forward and GEFS scope rule."),
    ("p1_binding", "docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_PACKAGE_20261002.json", "WHOLE_FILE", 7416,
     "4e9af52d3e2f6f9037e38fb7570c1d43a3984ed25c34090aedecfc1d2549e361",
     "Public binding of the PREPARED_BLOCKED P1 package and its raw restriction sources."),
    ("p1_restriction_history", P1_ROOT + "/restriction-history.json", "WHOLE_FILE", 4133,
     "fcf4c751a9d591c09e397afd6633efa8a4a6f70312d037d6b64c619579d55636",
     "Private P1 snapshot of three ECMWF denials (INCOMPLETE_INVENTORY_NOT_LIVE_LEDGER)."),
    ("ecmwf_raw_aws_retry_json", R09_PRIVATE + "/aws-retry.json", "RECORD_DIGEST_ONLY", 632,
     "de578a0c51a8811d5bd5c84dcef4e6ece3969b3f631cd43585240c24176b6e19",
     "Raw 07:48:13 S3 503 manifest; absent from its recorded path on 2026-10-07."),
    ("ecmwf_raw_capture_v1", R09_PRIVATE + "/capture-v1/capture.json", "RECORD_DIGEST_ONLY", 20811,
     "443b4ad67c8ef71e80db2ec4eca6047032509495fc4ed39311f3913609d69806",
     "Raw capture-v1 manifest; absent from its recorded path on 2026-10-07."),
    ("r09_transcript", R09_ROOT + "/codex.log", "WHOLE_FILE", 1465365,
     "5a2cae71b1a455ecaae8ad602004cbf417d758ead283e90dcee93cad0cfa3ea8",
     "R09 extrema agent transcript (2026-09-30): printed responses, bodies and headers."),
    ("ecmwf_coordinator_db", BRAINWORK + "/historical_ecmwf_backfill_coordinator.sqlite", "WHOLE_FILE", 7897088,
     "f7769de312aa46fdb6c45e7856380e77120751324523c248d0ba0adf549b916f",
     "2026-09-29 ECMWF S3 coordinator: one field FAILED HTTP_503 and 17 DONE fields with 21 unknown-status failed attempts."),
    ("ecmwf_coordinator_code", BRAINWORK + "/historical_ecmwf_backfill_coordinator_20260929.py", "WHOLE_FILE", 18316,
     "0a7bbf1b7e69c2b8f43b7d3113f89ef6fc39dfbce65aa2d0156e84efadf50a80",
     "Coordinator client: trust_env=False, follow_redirects=False, up to 3 attempts, concurrency 4."),
    ("ecmwf_pre_repair_db", BRAINWORK + "/historical_ecmwf_pre_repair_r1_20260929T212621Z.sqlite", "WHOLE_FILE", 77934592,
     "8b8f571dc2dc8393a67374be30986874d6aa645c79bf60825455374002400f57",
     "Snapshot before targeted repair: 459 ECMWF_HTTP_STATUS_OR_RANGE_IGNORED failures, status not retained."),
    ("ecmwf_backfill_db", BRAINWORK + "/historical_ecmwf_backfill.sqlite", "WHOLE_FILE", 78983168,
     "dc007dc2c4482f7170b9b0ab2a1805738d2d04f91bd733507bf142b3f3426725",
     "Final 2026-09-29 ECMWF S3 backfill ledger: 55,488 DONE, 385 needed retries."),
    ("ecmwf_backfill_code", BRAINWORK + "/historical_ecmwf_backfill_worker.py", "WHOLE_FILE", 27751,
     "808d7606ba51660579aa6ed291e255bec19354080efb9192d92d4f6c9d4e7a65",
     "ECMWF backfill worker client: trust_env=False, follow_redirects=False, up to 3 attempts, concurrency 4."),
    ("gefs_s3_backfill_db", BRAINWORK + "/historical_gefs_backfill.sqlite", "WHOLE_FILE", 30044160,
     "af2802f4cf204f76fa62c8ba231599ac0596a0e07cbaaf209b7b31a95c00e36e",
     "2026-09-29 noaa-gefs-pds S3 backfill ledger: 33,759 DONE, 264 needed a second attempt."),
    ("gefs_s3_backfill_code", BRAINWORK + "/historical_gefs_backfill_worker.py", "WHOLE_FILE", 10601,
     "a91eca8fe96b5aaca879467126772d4e57b3e4f5e96d5e0c4acb54a2ffb167f9",
     "GEFS S3 backfill client: trust_env=False, follow_redirects=True, up to 3 attempts, concurrency 6."),
    ("nomads_errors_prefix", NOMADS_ROOT + "/errors.log", "APPEND_ONLY_PREFIX", 24562,
     "8c682313e975c6a0d45ebe8b44ec15edd8a8ae4b1833e97b697c2ba1d0ef5655",
     "Brain-universe harvest error log prefix through the last 2026-10-04 NOMADS 302 traceback."),
    ("nomads_harvest_code", "tools/v11_brain_forward_universe_harvest.py", "WHOLE_FILE", 12045,
     "f68ef2ffca02d0f3cee44fbc967479a0f6838a743b9bd1e5331e4b58fd0c5424",
     "Repo copy, byte-identical to the deployed NOMADS harvest.py (2026-10-05 17:44 version)."),
    ("nomads_harvest_code_1550", NOMADS_ROOT + "/harvest.py.bak-20261005T174411Z", "WHOLE_FILE", 11505,
     "aad532e7c64b69afd44cef421edffaa941be74a78aa8bb27507efeafe495cfce",
     "NOMADS harvest saved 2026-10-04 15:50:27, after the 302s; the version that ran during them is not retained."),
    ("nomads_check_redirect", NOMADS_ROOT + "/check_redirect.py", "WHOLE_FILE", 1002,
     "b8d5f35dfbaf25eb229f3b4d9e3613875cf41711f760dbcf64dee57e481dcbbc",
     "Diagnostic NOMADS GET written 2026-10-04 15:46:36; whether and how often it ran is not retained."),
    ("nomads_capture_manifests", NOMADS_ROOT + "/pairs", "FILE_SET_MANIFEST", 186887,
     "4ac85f7830f2de58ed87c51b9b647d638c5fefd11c8d5dee59c6c517710f807b",
     "The 21 NOMADS harvest capture.json files (field_count, feature_ready_at)."),
    ("s3_harvest_code", S3_ROOT + "/harvest_s3.py", "WHOLE_FILE", 14509,
     "9e8c7dd4ca561b863a271c07f360957d53fc5bc593999904a59cfb73391091dd",
     "noaa-gefs-pds S3 harvest written 2026-10-04 16:08, after the NOMADS 302s."),
    ("s3_harvest_db", S3_ROOT + "/forward.sqlite", "WHOLE_FILE", 1757184,
     "6494d834cb19c5d04820309be395d98c1c7eda9eddad958ef8fd28ed040f6fed",
     "S3 harvest message table: 589 messages, one idx GET plus one Range GET each."),
    ("collector_code", "polymarket_scanner/v11/collection.py", "WHOLE_FILE", 14132,
     "16651c8e44fd640a732af0734468d89f1b8beb0ca8874717a6305516a9add1a0",
     "Shadow PublicCollector: allowlist, anonymity checks, 429/Retry-After and retry policy."),
    ("continuous_day_manager", "tools/v11_continuous_day_manager.py", "WHOLE_FILE", 15084,
     "b53ee4919e6fc191acf40354266ad8bd77a93467fdc795a62279815c86241bf3",
     "Deployed KATL continuous Shadow client construction (httpx default trust_env)."),
    ("perpetual_brain_preparer", "tools/v11_perpetual_brain_preparer.py", "WHOLE_FILE", 12260,
     "f850af0493b912f4236ac784cf5a2264217c30a3dba6f85a53f1cd045447f151",
     "Deployed Brain source client construction (httpx default trust_env)."),
    ("gefs_source_doc", "docs/V11_GEFS_SOURCE.md", "WHOLE_FILE", 7748,
     "be0706b29d284e16c61ca14168440ce935694072b468a0965bb2b411cfb4d38d",
     "NOMADS adapter scope, NWS/NCEP references cited as links only, 2026-09-24 pre-HTTP proxy refusal."),
    ("forward_harvest_doc", "docs/V11_FORWARD_EVIDENCE_HARVEST_20261007.md", "WHOLE_FILE", 27237,
     "b370fb232d7f253a83d67219b0504cccd7b36ddd98031f4ee496a636b60f407e",
     "Read-only Oct 5-7 Shadow ledger counts of real NOMADS GEFS captures."),
)
SOURCE_IDS = frozenset(s[0] for s in PINNED_SOURCES)
RECOVERED_BODY_PINS = {
    "0986be0818f5c4e80bddac64bcd37d8f77da4c43acb380aaa7e4bcb677a51460": 278,
    "1de430c06d6fe99b24fb4c0608436ddb2833706c73a66b023af7cc2816686ad2": 278,
    "3850dfdbf4489250268b5f0740240a9f4445e7c5c29e1d03aa0c5446808d7507": 17,
    "3e375eb1db6ff582c8f2b105c83bf08debe236b61b9a667f13da109e04b74538": 278,
    "7c21325b9a8c5d3b7f06bed411ae11e6fa6dcb490320671bd8e1d49a64956a28": 278,
}
RECOVERED_BODY_SOURCES = {
    f"recovered_body_{sha[:12]}": (f"{RECOVERED_DIR}/{sha}.body", "WHOLE_FILE", length, sha)
    for sha, length in RECOVERED_BODY_PINS.items()
}

ECMWF_S3 = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
ECMWF_PORTAL = "https://data.ecmwf.int"
ECMWF_CDN = "https://d2zvc0wgha4k2l.cloudfront.net"
NOMADS = "https://nomads.ncep.noaa.gov"
NOAA_S3 = "https://noaa-gefs-pds.s3.amazonaws.com"
REQUIRED_DOMAIN_ORIGINS = {
    "ECMWF": frozenset({ECMWF_S3, ECMWF_PORTAL, ECMWF_CDN}),
    "NOAA_GEFS": frozenset({NOMADS, NOAA_S3}),
}
REQUIRED_DOMAIN_PROVIDERS = {"ECMWF": frozenset({"IFS", "AIFS"}), "NOAA_GEFS": frozenset({"GEFS"})}
NOMADS_302_EPOCHS = (1791128691.9629707, 1791128715.4757252, 1791128777.2054663,
                     1791128800.4655247, 1791128823.7983284)
NOMADS_302_RE = re.compile(
    rb"^\[([0-9]{10}\.[0-9]{1,7})\] (K[A-Z]{3}-2026-10-0[0-9]) RuntimeError: HTTP_OR_GRIB:302:([0-9]+)$",
    re.M)
S3_SLOWDOWN = ('<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>SlowDown</Code>'
               '<Message>Please reduce your request rate.</Message><RequestId>{rid}</RequestId>'
               '<HostId>{host}</HostId></Error>')
UNTIMED_S3_503_PATHS = (
    "/20260823/00z/ifs/0p25/oper/20260823000000-3h-oper-fc.index",
    "/20260823/00z/ifs/0p25/enfo/20260823000000-3h-enfo-ef.index",
    "/20260823/00z/aifs-ens/0p25/enfo/20260823000000-6h-enfo-cf.index",
    "/20260823/00z/aifs-ens/0p25/enfo/20260823000000-6h-enfo-pf.index",
)
PROBE_PATH = "/20260929/00z/ifs/0p25/oper/20260929000000-6h-oper-fc.index"


def _source_path(path: str) -> Path:
    return Path(path) if path.startswith("/") else ROOT / path


def _manifest(directory: Path) -> tuple[bytes, int]:
    rows, total = [], 0
    children = []
    for child in directory.iterdir():
        children.append(child)
        if len(children) > 1000:
            raise LineageError("MANIFEST_TOO_MANY_FILES")
    for child in sorted(children):
        target = child / "capture.json"
        if child.is_symlink() or not child.is_dir() or not target.exists():
            continue
        raw = read_regular(target, 1024 * 1024)
        total += len(raw)
        if total > 16 * 1024 * 1024:
            raise LineageError("MANIFEST_TOO_LARGE")
        rows.append([f"pairs/{child.name}/capture.json", _sha(raw), len(raw)])
    return json.dumps(rows, sort_keys=True, separators=(",", ":")).encode(), total


def load_sources(reader: Optional[Callable[[str, str], bytes]] = None) -> dict:
    """Read and verify every pinned source; RECORD_DIGEST_ONLY is not read.

    ``reader(source_id, path)`` may be injected by tests. For a
    FILE_SET_MANIFEST source it must return the canonical manifest bytes,
    whose total-length binding is then the manifest length itself.
    """
    loaded = {}
    for source_id, path, binding, length, digest, _note in PINNED_SOURCES:
        if binding == "RECORD_DIGEST_ONLY":
            continue
        if reader is not None:
            raw = reader(source_id, path)
            total = len(raw) if binding != "FILE_SET_MANIFEST" else length
        elif binding == "FILE_SET_MANIFEST":
            raw, total = _manifest(_source_path(path))
        else:
            raw = read_regular(_source_path(path), max(length, 1) + (16 * 1024 * 1024 if binding == "APPEND_ONLY_PREFIX" else 0))
            total = len(raw)
        if binding == "APPEND_ONLY_PREFIX":
            if len(raw) < length:
                raise LineageError(f"SOURCE_TRUNCATED:{source_id}")
            raw, total = raw[:length], length
        if total != length or _sha(raw) != digest:
            raise LineageError(f"SOURCE_CHANGED:{source_id}")
        loaded[source_id] = raw
    return loaded


def _sqlite_rows(raw: bytes, query: str) -> list:
    """Query verified database bytes in memory; the original file is untouched."""
    image = bytearray(raw)
    if len(image) >= 20 and image[18:20] == b"\x02\x02":
        image[18:20] = b"\x01\x01"  # WAL header; the retained WAL files are empty.
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(bytes(image))
        return db.execute(query).fetchall()
    finally:
        db.close()


def _quote(loaded: Mapping, source_id: str, needle: bytes) -> None:
    if needle not in loaded[source_id]:
        raise LineageError(f"QUOTE_ABSENT:{source_id}:{needle[:60]!r}")


# ---------------------------------------------------------------------------
# Denial body recovery: every recovered body must equal a retained digest
# (or, for transcript-only bodies, is labelled as transcript text).
# ---------------------------------------------------------------------------

def recover_denial_bodies(loaded: Mapping) -> dict:
    """Return {sha256: bytes} for denial bodies recoverable from retained text."""
    history = strict_loads(loaded["p1_restriction_history"])
    out = {}
    first = next(r for r in history["records"] if r["capture"] == "earlier-loose-aws-retry")
    headers = first["response"]["headers"]
    rebuilt = S3_SLOWDOWN.format(rid=headers["x-amz-request-id"], host=headers["x-amz-id-2"]).encode()
    if _sha(rebuilt) != first["response"]["sha256"]:
        raise LineageError("RECOVERY_MISMATCH:07:48:13")
    out[_sha(rebuilt)] = rebuilt
    transcript = loaded["r09_transcript"]
    for rid in ("XMW4ZE7Z6Y38S6HJ", "MRTQHTPR43KKPS2B", "MRTS8Z1XKG3M5N15"):
        match = re.search(rb'<\?xml version="1\.0" encoding="UTF-8"\?>\n<Error><Code>SlowDown</Code>'
                          rb"<Message>Please reduce your request rate\.</Message><RequestId>"
                          + rid.encode() + rb"</RequestId><HostId>[A-Za-z0-9+/=]{1,200}</HostId></Error>",
                          transcript)
        if match is None:
            raise LineageError(f"RECOVERY_ABSENT:{rid}")
        out[_sha(match.group(0))] = match.group(0)
    _quote(loaded, "r09_transcript", b"\nToo Many Requests\n")
    out[_sha(b"Too Many Requests")] = b"Too Many Requests"
    for pinned in ("7c21325b9a8c5d3b7f06bed411ae11e6fa6dcb490320671bd8e1d49a64956a28",
                   "0986be0818f5c4e80bddac64bcd37d8f77da4c43acb380aaa7e4bcb677a51460",
                   "3850dfdbf4489250268b5f0740240a9f4445e7c5c29e1d03aa0c5446808d7507"):
        if pinned not in out:
            raise LineageError(f"PINNED_BODY_NOT_RECOVERED:{pinned}")
    return out


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------

def _event(event_id, domain, origin, path, at, basis, status, kind, occurrences,
           headers, body_sha, body_bytes, custody, retained, post, evidence):
    return {
        "event_id": event_id, "control_domain": domain, "origin": origin, "path": path,
        "received_at_utc": at, "time_basis": basis, "status": status, "kind": kind,
        "occurrences": occurrences, "retry_after": None, "retry_not_before_utc": None,
        "headers_retained": headers, "body_sha256": body_sha, "body_bytes": body_bytes,
        "body_custody": custody, "retained_record": retained, "post_event_requests": post,
        "expiry_adjudication": None, "carry_forward": HOLD_CARRY, "evidence": evidence,
    }


ECMWF_POST_0930 = (
    "Same-domain requests continued after every 2026-09-30 denial: CloudFront and portal at "
    "07:46:43-44Z after S3 SlowDown, a delayed S3 retry at 07:48:13Z, capture-v1 portal index "
    "GETs 07:54:40-07:55:14Z after the 07:54:39Z S3 SlowDown, and capture-current-1c8836a "
    "portal requests 08:04:37-08:05:59Z after the 07:55:15Z 429. Later 200s do not clear holds.")


def _ecmwf_events(loaded: Mapping, bodies: Mapping) -> list:
    history = strict_loads(loaded["p1_restriction_history"])
    events = []
    for status, at, body in sorted(RETAINED_DENIAL_DIGESTS, key=lambda k: k[1]):
        record = next(r for r in history["records"]
                      if (r["response"]["status"], r["response"]["received_at"], r["response"]["sha256"])
                      == (status, at, body))
        if _retained_record_digest(record) != RETAINED_DENIAL_DIGESTS[(status, at, body)]:
            raise LineageError("RETAINED_DENIAL_DIGEST")
        response = record["response"]
        origin = ECMWF_S3 if (response.get("source") in (None, "aws")) else ECMWF_PORTAL
        path = urlsplit(response["url"]).path if "url" in response else response["path"]
        if origin == ECMWF_PORTAL:
            path = "/forecasts" + path
        kind = "HTTP_503_S3_SLOWDOWN" if status == 503 else "HTTP_429_TOO_MANY_REQUESTS"
        custody = ("RECOVERED_BYTE_EXACT_FROM_RETAINED_HEADERS_DIGEST_VERIFIED"
                   if record["capture"] == "earlier-loose-aws-retry" else
                   "RECOVERED_BYTE_EXACT_FROM_TRANSCRIPT_DIGEST_VERIFIED")
        if body not in bodies:
            raise LineageError("PINNED_BODY_NOT_RECOVERED")
        stamp = iso_to_utc(at)
        events.append(_event(
            f"ecmwf-{'s3' if origin == ECMWF_S3 else 'portal'}-{status}-{stamp[:19].replace('-', '').replace(':', '')}Z",
            "ECMWF", origin, path, stamp, "RECEIPT_CLOCK", status, kind, 1,
            dict(sorted(response["headers"].items())), body, len(bodies[body]), custody,
            {"capture": record["capture"], "response": record["response"]}, ECMWF_POST_0930,
            ["p1_restriction_history", "r09_transcript", "a5a6_audit"]))
    transcript = loaded["r09_transcript"]
    for path in UNTIMED_S3_503_PATHS:
        _quote(loaded, "r09_transcript", f"\n{path} 503 278\n".encode())
        events.append(_event(
            "ecmwf-s3-503-untimed-" + path.rsplit("/", 1)[1].replace(".index", ""), "ECMWF", ECMWF_S3,
            path, None, "UNRETAINED_TRANSCRIPT_ORDER_BEFORE_2026-09-30T07:46:43Z", 503,
            "HTTP_503_BODY_LENGTH_ONLY", 1, {}, None, 278, "LENGTH_ONLY_IN_TRANSCRIPT", None,
            ECMWF_POST_0930, ["r09_transcript"]))
    for origin, rid, date in ((ECMWF_S3, "MRTQHTPR43KKPS2B", "07:46:43"), (ECMWF_CDN, "MRTS8Z1XKG3M5N15", "07:46:44")):
        _quote(loaded, "r09_transcript", f"'date': 'Wed, 30 Sep 2026 {date} GMT'".encode())
        body = next(b for b in bodies.values() if rid.encode() in b)
        headers = {"date": f"Wed, 30 Sep 2026 {date} GMT"}
        if origin == ECMWF_S3:
            headers["x-amz-request-id"] = rid
        else:
            headers["x-cache"] = "Error from cloudfront"
            _quote(loaded, "r09_transcript", b"'x-cache': 'Error from cloudfront'")
        events.append(_event(
            f"ecmwf-{'s3' if origin == ECMWF_S3 else 'cloudfront'}-503-20260930T{date.replace(':', '')}Z",
            "ECMWF", origin, PROBE_PATH, f"2026-09-30T{date}Z", "HTTP_DATE_HEADER_SECOND_RESOLUTION",
            503, "HTTP_503_S3_SLOWDOWN", 1, headers, _sha(body), len(body),
            "RECOVERED_FROM_TRANSCRIPT_TEXT_NO_ORIGINAL_DIGEST", None, ECMWF_POST_0930, ["r09_transcript"]))
    failed = _sqlite_rows(loaded["ecmwf_coordinator_db"],
                          "select field_key, attempts, error, updated_at from fields where status='FAILED'")
    if failed != [("ECMWF_AIFS_ENS|2026-08-23|00|006|20", 3, "RuntimeError:RuntimeError:HTTP_503", 1790675844.2481875)]:
        raise LineageError("ECMWF_COORDINATOR_FACT")
    events.append(_event(
        "ecmwf-s3-503-20260929T095724Z", "ECMWF", ECMWF_S3, "UNRETAINED:" + failed[0][0],
        epoch_to_utc(failed[0][3]), "LOCAL_LEDGER_UPDATED_AT", 503, "HTTP_503_AFTER_THREE_ATTEMPTS", 1,
        {}, None, None, "NOT_RETAINED", None,
        "The same coordinator retried this field three times; the 2026-09-29 backfill continued to "
        "10:14:38-21:35:30Z with 55,488 completed messages.", ["ecmwf_coordinator_db", "ecmwf_coordinator_code"]))
    coordinator_retries = _sqlite_rows(
        loaded["ecmwf_coordinator_db"],
        "select count(*), sum(attempts-1), min(updated_at), max(updated_at) "
        "from fields where status='DONE' and attempts>1")[0]
    if coordinator_retries != (17, 21, 1790675778.8596888, 1790675933.3819606):
        raise LineageError("ECMWF_COORDINATOR_RETRY_FACT")
    events.append(_event(
        "ecmwf-s3-coordinator-retries-status-unretained-20260929", "ECMWF", ECMWF_S3,
        "UNRETAINED:17 coordinator DONE fields completed after retry",
        epoch_to_utc(coordinator_retries[2]),
        f"AGGREGATE_COMPLETION_TIMES_TO_{epoch_to_utc(coordinator_retries[3])}", None,
        "COORDINATOR_FAILED_ATTEMPT_STATUS_NOT_RETAINED", coordinator_retries[1],
        {}, None, None, "NOT_RETAINED", None,
        "17 DONE fields needed 21 failed attempts in the coordinator ledger before 09:59Z; "
        "these precede the separate final backfill ledger starting after 10:14Z and are not "
        "part of its 533 failed attempts. The terminal FAILED field is separately recorded; "
        "no status is inferred for these retries.",
        ["ecmwf_coordinator_db", "ecmwf_coordinator_code"]))
    ignored = _sqlite_rows(loaded["ecmwf_pre_repair_db"],
                           "select count(*), min(updated_at), max(updated_at) from messages "
                           "where status='FAILED' and error='ECMWF_HTTP_STATUS_OR_RANGE_IGNORED'")[0]
    if ignored[0] != 459:
        raise LineageError("ECMWF_PRE_REPAIR_FACT")
    events.append(_event(
        "ecmwf-s3-status-unretained-20260929-pre-repair", "ECMWF", ECMWF_S3, "UNRETAINED:459 field Range GETs",
        epoch_to_utc(ignored[1]), f"AGGREGATE_FIRST_OF_SPAN_TO_{epoch_to_utc(ignored[2])}", None,
        "NON_SUCCESS_STATUS_NOT_RETAINED_NOT_401_403_404_410", 459, {}, None, None, "NOT_RETAINED", None,
        "Targeted repair runs r1/r2 then re-requested failed fields; final ledger reached 55,488 DONE.",
        ["ecmwf_pre_repair_db", "ecmwf_backfill_code"]))
    timeouts = _sqlite_rows(loaded["ecmwf_pre_repair_db"],
                            "select count(*), min(updated_at), max(updated_at) from messages "
                            "where status='FAILED' and error='ConnectTimeout'")[0]
    if timeouts != (3, 1790683298.3630497, 1790712246.5758994):
        raise LineageError("ECMWF_PRE_REPAIR_TIMEOUT_FACT")
    events.append(_event(
        "ecmwf-s3-connect-timeouts-20260929-pre-repair", "ECMWF", ECMWF_S3,
        "UNRETAINED:3 pre-repair field requests ending in ConnectTimeout",
        epoch_to_utc(timeouts[1]), f"AGGREGATE_FIRST_OF_SPAN_TO_{epoch_to_utc(timeouts[2])}", None,
        "TRANSPORT_CONNECT_TIMEOUT_NO_HTTP_STATUS_OBSERVED", timeouts[0], {}, None, None,
        "NOT_RETAINED", None,
        "Three terminal transport failures are separate from the 459 HTTP_STATUS_OR_RANGE_IGNORED rows. "
        "They are not proven HTTP denials; later repair/final-ledger overlap is unresolved and counts "
        "must not be summed as unique provider responses.",
        ["ecmwf_pre_repair_db", "ecmwf_backfill_code"]))
    retried = _sqlite_rows(loaded["ecmwf_backfill_db"],
                           "select count(*), sum(attempts-1), min(updated_at), max(updated_at) from messages where attempts>1")[0]
    events.append(_event(
        "ecmwf-s3-first-attempts-unretained-20260929", "ECMWF", ECMWF_S3,
        f"UNRETAINED:{retried[0]} messages completed only after retry", epoch_to_utc(retried[2]),
        f"AGGREGATE_COMPLETION_TIMES_TO_{epoch_to_utc(retried[3])}", None,
        "FAILED_ATTEMPT_STATUS_NOT_RETAINED_MAY_OVERLAP_PRE_REPAIR", retried[1], {}, None, None,
        "NOT_RETAINED", None, "Each failed attempt was retried within the same run (2^n backoff).",
        ["ecmwf_backfill_db", "ecmwf_backfill_code"]))
    return events


def _noaa_events(loaded: Mapping) -> list:
    log = loaded["nomads_errors_prefix"]
    found = [(float(m[1]), m[2].decode(), int(m[3])) for m in NOMADS_302_RE.finditer(log)]
    if [f[0] for f in found] != list(NOMADS_302_EPOCHS) or {f[2] for f in found} != {742}:
        raise LineageError("NOMADS_302_FACT")
    _quote(loaded, "nomads_harvest_code", b"if r.status_code==302 and b'Over Rate Limit' in r.content:")
    _quote(loaded, "nomads_harvest_code", b"raise ProviderRateLimited('NOAA_AKAMAI_OVER_RATE_LIMIT')")
    post = ("Same-origin NOMADS harvest captures completed 2026-10-04T15:45:39Z-2026-10-05T17:56:32Z, Shadow "
            "NOMADS collection continued through 2026-10-07, and the other NOAA GEFS origin (noaa-gefs-pds S3) "
            "was contacted 2026-10-04T16:20Z (1,178 GETs, concurrency 6) after a harvest written at 16:08Z. "
            "The code version running during the 302s is not retained; its traceback shows a retry loop.")
    events = []
    for at, key, length in found:
        stamp = epoch_to_utc(at)
        events.append(_event(
            f"noaa-nomads-302-{stamp[:19].replace('-', '').replace(':', '')}Z-{key}", "NOAA_GEFS", NOMADS,
            "/cgi-bin/filter_gefs_atmos_0p50a.pl", stamp, "LOCAL_LOG_EPOCH_AT_TERMINAL_FAILURE", 302,
            "HTTP_302_BODY_UNRETAINED_LATER_CODED_AS_AKAMAI_OVER_RATE_LIMIT", 1, {}, None, length,
            "LENGTH_ONLY_IN_LOG", None, post,
            ["nomads_errors_prefix", "nomads_harvest_code", "nomads_harvest_code_1550", "nomads_check_redirect"]))
    retried = _sqlite_rows(loaded["gefs_s3_backfill_db"],
                           "select count(*), sum(attempts-1), min(updated_at), max(updated_at) from messages where attempts>1")[0]
    if retried[:2] != (264, 264):
        raise LineageError("GEFS_S3_RETRY_FACT")
    _quote(loaded, "gefs_s3_backfill_code", b'S3="https://noaa-gefs-pds.s3.amazonaws.com"')
    events.append(_event(
        "noaa-s3-first-attempts-unretained-20260929", "NOAA_GEFS", NOAA_S3,
        "UNRETAINED:264 idx+Range message fetches completed on a second attempt", epoch_to_utc(retried[2]),
        f"AGGREGATE_COMPLETION_TIMES_TO_{epoch_to_utc(retried[3])}", None,
        "FAILED_ATTEMPT_STATUS_NOT_RETAINED", 264, {}, None, None, "NOT_RETAINED", None,
        "Each failed attempt was retried after 2^n seconds within the same run; 33,759 messages completed.",
        ["gefs_s3_backfill_db", "gefs_s3_backfill_code"]))
    return events


def _policy(follow, trust, attempts, concurrency):
    return {"follow_redirects": follow, "trust_env": trust, "max_attempts": attempts, "concurrency": concurrency}


def _contact(cid, domain, origin, shape, purpose, rng, first, last, count, basis, statuses, policy, evidence):
    return {"contact_id": cid, "control_domain": domain, "origin": origin, "path_shape": shape,
            "method": "GET", "purpose": purpose, "range_used": rng, "first_utc": first, "last_utc": last,
            "request_count": count, "request_count_basis": basis, "status_counts": statuses,
            "client_policy": policy, "g3_ledger": False, "classification": OBSERVED, "evidence": evidence}


def _contacts(loaded: Mapping) -> list:
    export = strict_loads(loaded["ecmwf_public_export"])
    out = []
    for capture in export["captures"]:
        name = capture["capture"]
        by_origin: dict = {}
        for row in capture["indexes"]:
            if type(row["status"]) is not int:
                continue
            origin = "https://" + urlsplit(row["url"]).hostname
            by_origin.setdefault(origin, []).append(row)
        for origin, rows in sorted(by_origin.items()):
            counts: dict = {}
            for row in rows:
                counts[str(row["status"])] = counts.get(str(row["status"]), 0) + 1
            out.append(_contact(
                f"ecmwf-{name}-{urlsplit(origin).hostname.split('.')[0]}", "ECMWF", origin,
                "/forecasts/<run>/<cycle>z/<model>/0p25/<stream>/<run>-<step>h-<stream>-<type>.index"
                if origin == ECMWF_PORTAL else "/<run>/<cycle>z/<model>/0p25/<stream>/<run>-<step>h-<stream>-<type>.index",
                "INDEX_EXPLORATION", False, iso_to_utc(min(r["received_at"] for r in rows)),
                iso_to_utc(max(r["received_at"] for r in rows)), len(rows),
                f"Itemized index rows in the public export; capture total requests={capture['requests']} "
                "includes unitemized field/listing requests.", dict(sorted(counts.items())),
                _policy("UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"), ["ecmwf_public_export", "a5a6_audit"]))
    _quote(loaded, "r09_transcript", b"httpx.Client(trust_env=False,follow_redirects=False,timeout=20)")
    for origin, at, status in ((ECMWF_S3, "07:46:43", "503"), (ECMWF_PORTAL, "07:46:43", "200"),
                               (ECMWF_CDN, "07:46:44", "503")):
        out.append(_contact(
            f"ecmwf-r09-origin-probe-{urlsplit(origin).hostname.split('.')[0]}", "ECMWF", origin,
            ("/forecasts" if origin == ECMWF_PORTAL else "") + PROBE_PATH, "ORIGIN_PROBE_AFTER_S3_503", False,
            f"2026-09-30T{at}Z", f"2026-09-30T{at}Z", 1,
            "One printed GET per origin after four S3 503s; time from the response Date header (portal time "
            "taken from the adjacent S3 leg).", {status: 1}, _policy(False, False, 1, 1), ["r09_transcript"]))
    rows = _sqlite_rows(loaded["ecmwf_backfill_db"],
                        "select count(*), sum(attempts), min(updated_at), max(updated_at), sum(byte_length) from messages where status='DONE'")[0]
    out.append(_contact(
        "ecmwf-s3-backfill-20260929", "ECMWF", ECMWF_S3,
        "/<run>/<cycle>z/{ifs,aifs-ens}/0p25/enfo/<run>-<step>h-enfo-{cf,pf,ef}.grib2 (+ .index)",
        "HISTORICAL_FIELD_BACKFILL", True, epoch_to_utc(rows[2]), epoch_to_utc(rows[3]), None,
        f"{rows[0]} DONE messages, {rows[1]} message attempts, {rows[4]} field bytes; index GETs per message not itemized.",
        {"DONE_MESSAGES": rows[0]}, _policy(False, False, 3, 4), ["ecmwf_backfill_db", "ecmwf_backfill_code"]))
    rows = _sqlite_rows(loaded["gefs_s3_backfill_db"],
                        "select count(*), sum(attempts), min(updated_at), max(updated_at), sum(bytes) from messages where status='DONE'")[0]
    out.append(_contact(
        "noaa-s3-backfill-20260929", "NOAA_GEFS", NOAA_S3, "/gefs.<run>/<cycle>/atmos/pgrb2ap5/<member>.t<cycle>z.pgrb2a.0p50.f<hour>[.idx]",
        "HISTORICAL_FIELD_BACKFILL", True, epoch_to_utc(rows[2]), epoch_to_utc(rows[3]), None,
        f"{rows[0]} DONE messages over {rows[1]} message attempts; each successful attempt is one idx GET plus one "
        f"Range GET; GETs made by failed attempts are not retained; {rows[4]} field bytes; final status after redirects only.",
        {"2XX_AFTER_REDIRECTS": rows[0] * 2}, _policy(True, False, 3, 6), ["gefs_s3_backfill_db", "gefs_s3_backfill_code"]))
    manifest = json.loads(loaded["nomads_capture_manifests"])
    if len(manifest) != 21:
        raise LineageError("NOMADS_MANIFEST_FACT")
    _quote(loaded, "nomads_harvest_code", b"async with httpx.AsyncClient(timeout=15.,follow_redirects=False,limits=limits,")
    out.append(_contact(
        "noaa-nomads-brain-universe-20261004", "NOAA_GEFS", NOMADS, "/cgi-bin/filter_gefs_atmos_0p50a.pl?<filter query>",
        "FORWARD_POINT_FIELD_HARVEST", False, "2026-10-04T15:45:39.533532Z", "2026-10-05T17:56:32.905958Z", None,
        "21 READY captures (19 x 310 + 2 x 279 = 6,448 fields); attempts, retries and resumed fields not itemized.",
        {"200_FIELDS_IN_READY_CAPTURES": 6448, "302_TERMINAL_FAILURES": 5},
        _policy(False, True, 5, 1), ["nomads_capture_manifests", "nomads_errors_prefix", "nomads_harvest_code"]))
    rows = _sqlite_rows(loaded["s3_harvest_db"],
                        "select count(*), sum(attempts), min(updated_at), max(updated_at), sum(bytes) from messages where status='DONE'")[0]
    if rows[:2] != (589, 589):
        raise LineageError("S3_HARVEST_FACT")
    _quote(loaded, "s3_harvest_code", b"trust_env=False,follow_redirects=True")
    out.append(_contact(
        "noaa-s3-brain-universe-20261004", "NOAA_GEFS", NOAA_S3, "/gefs.20261004/00/atmos/pgrb2ap5/<member>.t00z.pgrb2a.0p50.f<hour>[.idx]",
        "FORWARD_FIELD_HARVEST", True, epoch_to_utc(rows[2]), epoch_to_utc(rows[3]), rows[1] * 2,
        f"589 DONE messages, one attempt each, idx GET + Range GET; {rows[4]} field bytes; completion times only.",
        {"2XX_AFTER_REDIRECTS": rows[1] * 2}, _policy(True, False, 3, 6), ["s3_harvest_db", "s3_harvest_code"]))
    _quote(loaded, "forward_harvest_doc", b"prefix `model-get` 3,743 / 4,635")
    _quote(loaded, "forward_harvest_doc", "2026-10-04 10:55:39 → 2026-10-07 03:59:58".encode())
    out.append(_contact(
        "noaa-nomads-shadow-collector-20261006-ledger", "NOAA_GEFS", NOMADS, "/cgi-bin/filter_gefs_atmos_0p50a.pl?<filter query>",
        "SHADOW_MODEL_FIELD_COLLECTION", False, "2026-10-04T10:55:39Z", "2026-10-07T03:59:58Z", 4635,
        "model-get records in the Oct 6 daily Shadow ledger (recorded span as reported); the Oct 5 ledger's 3,743 "
        "may overlap and is not added. Each is one GET with one attempt.",
        {"200": 4635}, _policy(False, True, 1, 1), ["forward_harvest_doc", "collector_code", "continuous_day_manager"]))
    return out


TRANSPORT_FINDINGS = (
    ("T1-ambient-environment-trusted", "Shadow and Brain NOMADS clients",
     "continuous_day_manager, perpetual_brain_preparer and the NOMADS universe harvest build httpx.AsyncClient "
     "without trust_env=False, and PublicCollector's anonymity check does not refuse an environment-trusting "
     "client. Process environments at contact time are not retained; the 2026-09-24 off-host probe environment "
     "had a socks5h proxy and its result file is no longer retained.",
     "Historical NOMADS contacts cannot prove absence of ambient proxy/CA configuration. They are not "
     "evidence for network.anonymous_no_retry_credential_policy.",
     ("continuous_day_manager", "perpetual_brain_preparer", "nomads_harvest_code", "collector_code", "gefs_source_doc")),
    ("T2-retries-after-restriction", "All historical bulk clients",
     "The ECMWF coordinator retried a field three times ending in HTTP 503; ECMWF and NOAA S3 backfills retry up "
     "to three times with 2^n backoff; the NOMADS harvest retries up to five times; PublicCollector retries "
     "non-GEFS 5xx within a cycle. GEFS through PublicCollector uses one attempt.",
     "Retained retry behaviour is incompatible with the zero-retry Gate-3 envelope and expands the denial "
     "history with unretained statuses.",
     ("ecmwf_coordinator_db", "ecmwf_backfill_code", "gefs_s3_backfill_code", "nomads_harvest_code", "collector_code")),
    ("T3-redirects-followed", "NOAA S3 backfill and S3 harvest",
     "Both noaa-gefs-pds clients set follow_redirects=True; redirect hops and final hosts are not retained.",
     "Their 2xx outcomes do not bind the answering origin; no redirect-free S3 access is evidenced.",
     ("gefs_s3_backfill_code", "s3_harvest_code")),
    ("T4-concurrency-and-volume", "Sep 29 bulk backfills and Oct 4 S3 harvest",
     "ECMWF S3 backfill moved about 35.6 GB at concurrency 4 on 2026-09-29; NOAA S3 backfill about 5.2 GB at "
     "concurrency 6; the Oct 4 S3 harvest ran at concurrency 6. S3 SlowDown 503s followed on 2026-09-30.",
     "Historical volume and parallelism exceed every Gate-3 budget and are part of the control-domain history "
     "a resumption review must consider.",
     ("ecmwf_backfill_db", "gefs_s3_backfill_db", "s3_harvest_code")),
    ("T5-origin-switch-after-restriction", "ECMWF and NOAA GEFS",
     "After S3 SlowDown the 2026-09-30 work probed CloudFront and the portal and then queried the portal; after "
     "the 2026-10-04 NOMADS 302s a new noaa-gefs-pds S3 harvest was written and run 33 minutes later.",
     "Origin switching after a denial is failover. The protocol forbids inferring separate control domains from "
     "hostnames, so both NOAA origins and all three ECMWF origins share their domain's hold.",
     ("r09_transcript", "nomads_errors_prefix", "s3_harvest_code", "s3_harvest_db")),
    ("T6-no-credential-evidence", "All retained contacts",
     "No retained record or client carries Authorization, cookies, API keys or signed URLs; PublicCollector "
     "refuses auth/cookie/proxy-authorization headers and clears cookies; ECMWF and S3 clients set "
     "trust_env=False. No reviewed licence or access-term bytes are retained for any provider.",
     "Historical access was anonymous as far as retained code shows. It is OBSERVED access only and grants no "
     "permission.",
     ("collector_code", "ecmwf_backfill_code", "gefs_s3_backfill_code", "gefs_source_doc")),
    ("T7-custody-gaps", "Raw restriction evidence",
     "The P1 binding's raw ECMWF files under AlphaV11_R09Extrema/Alpha are absent. All three pinned denial "
     "bodies are recovered byte-exact and digest-verified (two from the transcript, one from retained headers). "
     "The 2026-09-24 NOAA probe result, NOMADS 302 bodies and many backfill failure statuses are not retained.",
     "Missing statuses remain UNKNOWN holds; recovery restores bodies, not original receipts or clocks.",
     ("p1_binding", "p1_restriction_history", "r09_transcript", "gefs_source_doc")),
)

# Identity dispositions. Evidence can only move an identity offline when
# its stated obligation is satisfiable from retained bytes plus review.
ASSESSMENT_RULES = {
    "operational_release_dossier": ("EXTERNAL_PROVIDER_DOCUMENT_OR_RIGHT", "Original operational release bytes and effective interval; none retained."),
    "release_document_retrieval": ("EXTERNAL_PROVIDER_DOCUMENT_OR_RIGHT", "Retrieval provenance of original documents; only links are retained and retrieval is a provider request."),
    "licence_anonymous_access": ("EXTERNAL_PROVIDER_DOCUMENT_OR_RIGHT", "No licence/access-term bytes retained; observed anonymous access is not permission; restriction adjudication is unresolved."),
    "control_perturbed_mapping": ("EXTERNAL_PROVIDER_DOCUMENT_OR_RIGHT", "Operational control/perturbed attestation; adapter grammar and observed headers are insufficient."),
    "grib_identity_decoder_build": ("FUTURE_IMPLEMENTATION_AND_REVIEW", "Qualified A2-A7 decoder/build plus independent pre-acquisition pins."),
    "purpose_endpoint_contracts": ("EXTERNAL_PROVIDER_DOCUMENT_OR_RIGHT", "Real-purpose contracts need reviewed rights and mappings; V4 deliberately refuses them."),
    "current_run_index_object_range": ("FORWARD_RUN_OBSERVATION", "Run-scoped readiness/index/object/range receipts at or after the selected run."),
    "publication_attestation_or_absence_reason": ("EXTERNAL_PROVIDER_DOCUMENT_OR_RIGHT", "Provider attestation, or an absence reason grounded in provider documentation not retained."),
}
NETWORK_ASSESSMENT = {
    "exact_origin_path_purpose_allowlist": ("EXTERNAL_PROVIDER_DOCUMENT_OR_RIGHT", "Reviewed origins/paths need reviewed permission; this package lists only unqualified candidates and observed shapes."),
    "dns_tls_build_peer_policy": ("FUTURE_IMPLEMENTATION_AND_REVIEW", "No concrete Gate-3 transport exists; historical clients evidence nothing about DNS/TLS/peer enforcement."),
    "anonymous_no_retry_credential_policy": ("FUTURE_IMPLEMENTATION_AND_REVIEW", "Envelope evaluator refuses credential/retry/redirect/proxy vectors offline, but actual adapter enforcement does not exist (finding T1-T3)."),
    "restriction_domain_lineage": ("OFFLINE_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW", "This package: every retained denial, unretained-status failure and later success across both domains, bound to pinned bytes, all HELD."),
    "ecmwf_503_429_expiry_resumption_review": ("OFFLINE_ADJUDICABLE_AS_HELD_ONLY", "Retained evidence carries no Retry-After or expiry; offline review can only adjudicate HELD with UNKNOWN expiry. Resumption needs provider-side evidence."),
    "preflight_approval_receipts_if_used": ("OWNER_DECISION_REQUIRED", "Owner no-request-before-G3-L rule; no approval or receipt may be invented."),
}


def _assessment() -> dict:
    out = {}
    for identity in IN_SCOPE_IDS:
        group, name = identity.split(".", 1)
        if group == "sources":
            item = name.split("_", 1)[1]
            disposition, basis = ASSESSMENT_RULES[item]
        else:
            disposition, basis = NETWORK_ASSESSMENT[name]
        if identity in RUN_SPECIFIC:
            disposition = "FORWARD_RUN_OBSERVATION"
        offline = disposition in OFFLINE_DISPOSITIONS
        remaining = {
            "OFFLINE_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW": "Independent exact-commit review of this package, then private sealing.",
            "OFFLINE_ADJUDICABLE_AS_HELD_ONLY": "Any resumption needs provider expiry/permission evidence obtained under an owner-approved route.",
            "EXTERNAL_PROVIDER_DOCUMENT_OR_RIGHT": "Authentic provider documents/rights acquired or supplied outside this offline lane.",
            "FORWARD_RUN_OBSERVATION": "Fresh selected-run observation after G3-L-compatible authorization.",
            "FUTURE_IMPLEMENTATION_AND_REVIEW": "New implementation plus independent exact-commit review.",
            "OWNER_DECISION_REQUIRED": "Owner/protocol decision.",
        }[disposition]
        out[identity] = {"disposition": disposition, "closable_offline_on_independent_review": offline,
                         "remaining_external_or_forward": remaining, "basis": basis}
    return out


# Contacts with at least one successful response for that provider's paths.
OBSERVED_BY_PROVIDER = {
    "GEFS": frozenset({"noaa-nomads-brain-universe-20261004", "noaa-nomads-shadow-collector-20261006-ledger",
                       "noaa-s3-backfill-20260929", "noaa-s3-brain-universe-20261004"}),
    "IFS": frozenset({"ecmwf-capture-v1-data", "ecmwf-capture-current-1c8836a-data",
                      "ecmwf-r09-origin-probe-data", "ecmwf-s3-backfill-20260929"}),
    "AIFS": frozenset({"ecmwf-capture-v1-data", "ecmwf-s3-backfill-20260929"}),
}


def build_lineage(loaded: Mapping) -> tuple[dict, dict]:
    """Return (lineage, recovered denial bodies) from verified sources."""
    bodies = recover_denial_bodies(loaded)
    events = sorted(_ecmwf_events(loaded, bodies) + _noaa_events(loaded),
                    key=lambda e: (e["control_domain"], e["received_at_utc"] or "", e["event_id"]))
    contacts = sorted(_contacts(loaded), key=lambda c: (c["control_domain"], c["first_utc"], c["contact_id"]))
    domains = {
        "ECMWF": {"providers": ["AIFS", "IFS"], "origins": sorted(REQUIRED_DOMAIN_ORIGINS["ECMWF"]),
                  "status": "HELD", "hold_basis": sorted(e["event_id"] for e in events if e["control_domain"] == "ECMWF"),
                  "scope_independence_review": None, "resumption_review": None,
                  "later_successes_clear_hold": False,
                  "note": "All S3 503s with bodies are SlowDown ('Please reduce your request rate'); CloudFront fronts the same bucket (server AmazonS3); the portal returned 429. No Retry-After anywhere."},
        "NOAA_GEFS": {"providers": ["GEFS"], "origins": sorted(REQUIRED_DOMAIN_ORIGINS["NOAA_GEFS"]),
                      "status": "HELD", "hold_basis": sorted(e["event_id"] for e in events if e["control_domain"] == "NOAA_GEFS"),
                      "scope_independence_review": None, "resumption_review": None,
                      "later_successes_clear_hold": False,
                      "note": "P1's proposed origin noaa-gefs-pds S3 was contacted 33 minutes after NOMADS 302s; no reviewed evidence separates NOMADS (Akamai) and the S3 bucket. P1 restriction-history.json omits all NOAA records."},
    }
    permissions = {}
    for provider in PROVIDERS:
        observed = sorted(OBSERVED_BY_PROVIDER[provider])
        if not set(observed) <= {c["contact_id"] for c in contacts}:
            raise LineageError("OBSERVED_CONTACT_MISSING")
        permissions[provider] = {
            "observed_public_anonymous_access": observed, "reviewed_permissions": [],
            "licence_document_bytes": None, "status": "NO_REVIEWED_PERMISSION",
            "basis": "No retained licence/access-term bytes; the only references are URLs. Successful historical responses are observations, not permission."}
    findings = [{"finding_id": f[0], "subject": f[1], "fact": f[2], "consequence": f[3], "evidence": list(f[4])}
                for f in TRANSPORT_FINDINGS]
    assessment = _assessment()
    offline = sorted(i for i, a in assessment.items() if a["disposition"] == "OFFLINE_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW")
    held = sorted(i for i, a in assessment.items() if a["disposition"] == "OFFLINE_ADJUDICABLE_AS_HELD_ONLY")
    sources = [{"source_id": s[0], "path": s[1], "binding": s[2], "byte_length": s[3], "sha256": s[4], "note": s[5]}
               for s in PINNED_SOURCES]
    sources += [{"source_id": f"recovered_body_{sha[:12]}", "path": f"{RECOVERED_DIR}/{sha}.body",
                 "binding": "WHOLE_FILE", "byte_length": len(body), "sha256": sha,
                 "note": "Recovered denial body; equals a retained digest or is transcript text as labelled on its event."}
                for sha, body in sorted(bodies.items())]
    for event in events:
        if event["body_sha256"] in bodies:
            event["evidence"] = sorted(set(event["evidence"]) | {f"recovered_body_{event['body_sha256'][:12]}"})
    lineage = {
        "schema": SCHEMA, "as_of_utc": AS_OF_UTC, "baseline_commit": BASELINE_COMMIT,
        "g3l": "NO_GO", "qualification_credit": 0, "execution_authority": False,
        "provider_requests_by_this_package": 0,
        "permission_rule": "Observed public anonymous access is never permission. A request needs a reviewed permission bound to licence/access-term bytes, an unheld domain with reviewed expiry for every restriction, and a reviewed origin/path/purpose spec.",
        "evidence_sources": sources,
        "custody_observations": [
            "2026-10-07: raw ECMWF files cited by the P1 binding are absent from their recorded paths; no copy found by hash under /home/alphaadmin.",
            "2026-10-07: the 2026-09-24 NOAA probe result (sha256 2e9eb774...fc1f) is not retained; only its prose summary survives.",
            "2026-10-07: read-only inventory found 15,510 NOMADS SOURCE_RESULT rows across Shadow ledgers, all SUCCESS with one attempt, and four NOMADS GET reservations without completion records (unfinished intents); spot-checked on daily-2026-10-06.sqlite (4,635 SUCCESS, one IN_PROGRESS reservation at 09:24:15Z). Live ledgers are not bound into this package.",
            "2026-10-07: Gamma (non-G3-L) retained one HTTP 429 on /markets/5270640 without time or headers; it is outside this package's control domains.",
        ],
        "control_domains": domains, "contacts": contacts, "restriction_events": events,
        "permissions": permissions,
        "request_envelope": {
            "methods": ["GET"], "reviewed_origin_path_specs": [],
            "candidate_origin_path_specs": [
                {"origin": NOAA_S3, "provider": "GEFS", "purpose": "INDEX",
                 "path_shape": "/gefs.<YYYYMMDD>/<HH>/atmos/pgrb2ap5/gec00.t<HH>z.pgrb2a.0p50.f<HHH>.idx",
                 "status": "UNQUALIFIED_PROPOSAL", "basis": "P1 proposal; mapping, rights and NOAA hold unresolved."},
                {"origin": ECMWF_PORTAL, "provider": "IFS", "purpose": "FIELD",
                 "path_shape": "/forecasts/<run>/<HH>z/ifs/0p25/<stream>/<run>-<step>h-<stream>-<type>.grib2",
                 "status": "UNQUALIFIED_PROPOSAL", "basis": "V4 adapter grammar only; ECMWF domain HELD."},
                {"origin": ECMWF_PORTAL, "provider": "AIFS", "purpose": "FIELD",
                 "path_shape": "/forecasts/<run>/<HH>z/aifs-ens/0p25/<stream>/<run>-<step>h-<stream>-<type>.grib2",
                 "status": "UNQUALIFIED_PROPOSAL", "basis": "V4 adapter grammar only; ECMWF domain HELD."},
            ],
            "campaign_limits": {"attempts": 8, "body_bytes": 33_554_432},
            "per_request_limits": {"attempts": 1, "deadline_seconds": ATTEMPT_DEADLINE_SECONDS,
                                   "header_bytes": 4096, "header_count": 32, "in_flight": 1,
                                   "index_body_bytes": INDEX_CAP,
                                   "field_body_bytes": {p: FIELD_LIMITS[p] for p in PROVIDERS}},
            "forbidden": sorted(["CREDENTIALS", "COOKIES", "NETRC", "PROXY", "AMBIENT_ENVIRONMENT",
                                 "CLIENT_CERTIFICATE", "SIGNED_URL", "REDIRECT", "RETRY", "FAILOVER",
                                 "REQUEST_BODY", "QUERY", "OPEN_OR_MULTI_RANGE", "CONCURRENCY",
                                 "NON_GET_METHOD"]),
        },
        "transport_findings": findings,
        "identity_assessment": assessment,
        "identity_impact": {
            "audit_missing_before": 77, "audit_missing_after": 77, "closed_by_this_package": 0,
            "qualification_credit": 0, "offline_candidates_on_independent_review": offline,
            "offline_adjudicable_as_held_only": held,
            "remaining_future_or_external": len(assessment) - len(offline) - len(held),
            "statement": "No identity is closed. One network identity becomes an offline candidate on independent review; one can only be adjudicated HELD; 28 remain external, forward or implementation-bound.",
        },
    }
    return lineage, bodies


# ---------------------------------------------------------------------------
# Closed-schema lineage checker
# ---------------------------------------------------------------------------

TOP_KEYS = frozenset({
    "schema", "as_of_utc", "baseline_commit", "g3l", "qualification_credit",
    "execution_authority", "provider_requests_by_this_package", "permission_rule",
    "evidence_sources", "custody_observations", "control_domains", "contacts",
    "restriction_events", "permissions", "request_envelope", "transport_findings",
    "identity_assessment", "identity_impact",
})
SOURCE_KEYS = frozenset({"source_id", "path", "binding", "byte_length", "sha256", "note"})
BINDINGS = frozenset({"WHOLE_FILE", "APPEND_ONLY_PREFIX", "RECORD_DIGEST_ONLY", "FILE_SET_MANIFEST"})
DOMAIN_KEYS = frozenset({
    "providers", "origins", "status", "hold_basis", "scope_independence_review",
    "resumption_review", "later_successes_clear_hold", "note",
})
CONTACT_KEYS = frozenset({
    "contact_id", "control_domain", "origin", "path_shape", "method", "purpose",
    "range_used", "first_utc", "last_utc", "request_count", "request_count_basis",
    "status_counts", "client_policy", "g3_ledger", "classification", "evidence",
})
CLIENT_POLICY_KEYS = frozenset({"follow_redirects", "trust_env", "max_attempts", "concurrency"})
EVENT_KEYS = frozenset({
    "event_id", "control_domain", "origin", "path", "received_at_utc", "time_basis",
    "status", "kind", "occurrences", "retry_after", "retry_not_before_utc",
    "headers_retained", "body_sha256", "body_bytes", "body_custody", "retained_record",
    "post_event_requests", "expiry_adjudication", "carry_forward", "evidence",
})
PERMISSION_KEYS = frozenset({
    "observed_public_anonymous_access", "reviewed_permissions",
    "licence_document_bytes", "status", "basis",
})
REVIEWED_PERMISSION_KEYS = frozenset({
    "permission_id", "origins", "purposes", "reviewed_at_utc", "valid_until_utc",
    "licence_document", "review_ref", "basis",
})
REF_KEYS = frozenset({"sha256", "byte_length", "path"})
ENVELOPE_KEYS = frozenset({
    "methods", "reviewed_origin_path_specs", "candidate_origin_path_specs",
    "campaign_limits", "per_request_limits", "forbidden",
})
SPEC_KEYS = frozenset({"origin", "provider", "purpose", "path_regex", "permission_id", "review_ref"})
CANDIDATE_SPEC_KEYS = frozenset({"origin", "provider", "purpose", "path_shape", "status", "basis"})
FINDING_KEYS = frozenset({"finding_id", "subject", "fact", "consequence", "evidence"})
ASSESSMENT_KEYS = frozenset({"disposition", "closable_offline_on_independent_review",
                             "remaining_external_or_forward", "basis"})
IMPACT_KEYS = frozenset({
    "audit_missing_before", "audit_missing_after", "closed_by_this_package",
    "qualification_credit", "offline_candidates_on_independent_review",
    "offline_adjudicable_as_held_only", "remaining_future_or_external", "statement",
})
DISPOSITIONS = frozenset({
    "OFFLINE_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW", "OFFLINE_ADJUDICABLE_AS_HELD_ONLY",
    "EXTERNAL_PROVIDER_DOCUMENT_OR_RIGHT", "FORWARD_RUN_OBSERVATION",
    "FUTURE_IMPLEMENTATION_AND_REVIEW", "OWNER_DECISION_REQUIRED",
})
OFFLINE_DISPOSITIONS = frozenset({
    "OFFLINE_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW", "OFFLINE_ADJUDICABLE_AS_HELD_ONLY",
})
DOMAIN_STATUSES = frozenset({"HELD", "RESUMED_BY_REVIEW"})
# Restriction events that must never disappear from any later lineage. The
# three ECMWF denials are additionally pinned by record digest.
PINNED_ECMWF_EVENTS = frozenset(
    (status, iso_to_utc(at), body) for status, at, body in RETAINED_DENIAL_DIGESTS)
REQUIRED_EVENT_IDS = frozenset({
    "ecmwf-s3-503-20260929T095724Z", "ecmwf-s3-coordinator-retries-status-unretained-20260929",
    "ecmwf-s3-status-unretained-20260929-pre-repair",
    "ecmwf-s3-connect-timeouts-20260929-pre-repair",
    "ecmwf-s3-first-attempts-unretained-20260929",
    "ecmwf-s3-503-20260930T074643Z", "ecmwf-cloudfront-503-20260930T074644Z",
    "ecmwf-s3-503-20260930T074813Z", "ecmwf-s3-503-20260930T075439Z",
    "ecmwf-portal-429-20260930T075515Z",
    "noaa-s3-first-attempts-unretained-20260929",
} | {"ecmwf-s3-503-untimed-" + p.rsplit("/", 1)[1].replace(".index", "") for p in UNTIMED_S3_503_PATHS}
  | {f"noaa-nomads-302-{epoch_to_utc(t)[:19].replace('-', '').replace(':', '')}Z-{k}"
     for t, k in zip(NOMADS_302_EPOCHS, ("KATL-2026-10-05", "KAUS-2026-10-05", "KDAL-2026-10-05",
                                         "KHOU-2026-10-05", "KLAX-2026-10-05"))})


def _historical_event_digest(event: Mapping) -> str:
    """Pin observation fields while allowing later reviewed expiry annotations."""
    immutable = {k: v for k, v in event.items()
                 if k not in {"expiry_adjudication", "retry_after", "retry_not_before_utc"}}
    return _sha(json.dumps(immutable, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False).encode())


HISTORICAL_EVENT_PINS = {
    "ecmwf-s3-503-untimed-20260823000000-3h-enfo-ef": "a6d92ebbc0796bad01240c32eb5bcfbaf0e0f23d2ba68798f4b072c9195c282a",
    "ecmwf-s3-503-untimed-20260823000000-3h-oper-fc": "9a8c3762099749f72735d83444d0cc4a4ea5ba1db366e290dda4a22ddc72fceb",
    "ecmwf-s3-503-untimed-20260823000000-6h-enfo-cf": "ddfcdd0aba097173ab69febf5a68fb80952622b51fec5cbef6f882afa0e91837",
    "ecmwf-s3-503-untimed-20260823000000-6h-enfo-pf": "7e13605fee530c18aa9ce32e88f4ef5142af4baef6982580fb916c941dbf1761",
    "ecmwf-s3-coordinator-retries-status-unretained-20260929": "263e2f0a3df6996d6803c9198307d18ea001a5b3a393e294a8a562db929c5a80",
    "ecmwf-s3-503-20260929T095724Z": "a28c2ba0e1c1b5303f21cab361edb5af63e85713d6a21cb76a0444d517a1a88c",
    "ecmwf-s3-first-attempts-unretained-20260929": "94ad21c38e0aedba1bc440c34212bb63c77217fc63a6e5f4434c5bdc57621854",
    "ecmwf-s3-status-unretained-20260929-pre-repair": "b8d9874fedac1df04467ad57b81cba5cc0a35bb5d7a56852e35838b1ab8c1c65",
    "ecmwf-s3-connect-timeouts-20260929-pre-repair": "31d59e3c7cf9a86aec5b5cfa0b548242ea6b21938c09075952d70d3f9c3cd10d",
    "ecmwf-s3-503-20260930T074643Z": "0efd38ae3c54371f89a6866f68297dfc812c893d0516ddb54409d7d1219c1907",
    "ecmwf-cloudfront-503-20260930T074644Z": "db96beb87d5c583ffecbc53c3981249aece998368448745e042546459fece6cf",
    "ecmwf-s3-503-20260930T074813Z": "04d1932bb92c3caa587322cbbfc477ac745f1c918a38bcc26c349987d28bc062",
    "ecmwf-s3-503-20260930T075439Z": "da2463eb49c9c3287167f36de64fc3f480822f899b1c09fe64acd024b6628917",
    "ecmwf-portal-429-20260930T075515Z": "7d4e5a1e11ac45d6018d7085b97fc50be4ad37e284b0da5a5080c6eaf9d0e1a4",
    "noaa-s3-first-attempts-unretained-20260929": "ce323563ec16248e16885dad8ca865709de07e9cbdfd400c848ffbb18dce0eea",
    "noaa-nomads-302-20261004T154451Z-KATL-2026-10-05": "edfe8bff5a0cb7afef4f98fa58a060210010c739208d21e595319facc8a82d41",
    "noaa-nomads-302-20261004T154515Z-KAUS-2026-10-05": "3a3a0df1fb1ba1a3577c51b69163dfab7ac245aef0f01c7ca6f8b2b3212e9c95",
    "noaa-nomads-302-20261004T154617Z-KDAL-2026-10-05": "42c40774ac99610ca8bcef112beda2cb8407e5a5da80fa2cef72d28ce25fda1f",
    "noaa-nomads-302-20261004T154640Z-KHOU-2026-10-05": "c049518512341cac268b4f008072ee8fd8de723e86ee7ccfa15f6d4eb072c0d7",
    "noaa-nomads-302-20261004T154703Z-KLAX-2026-10-05": "1ad84994a13135e321ad14f659c44ec1ae7d89241482ed5bca4a7c4c3a677422",
}


def _is_ref(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == REF_KEYS
            and type(value["sha256"]) is str and SHA256_RE.fullmatch(value["sha256"]) is not None
            and type(value["byte_length"]) is int and value["byte_length"] > 0
            and type(value["path"]) is str and 0 < len(value["path"]) <= 4096)


def _closed(obj: Any, keys: frozenset, label: str, out: list) -> bool:
    if not isinstance(obj, dict) or set(obj) != keys:
        out.append(f"SCHEMA:{label}")
        return False
    return True


def _event_time(event: Mapping) -> Optional[datetime]:
    return parse_utc(event.get("received_at_utc"))


def _check_lineage(doc: Any) -> list[str]:
    """Check structure and immutable historical facts without reading external files."""
    out: list[str] = []
    if not _closed(doc, TOP_KEYS, "top", out):
        return out
    if doc["schema"] != SCHEMA:
        out.append("SCHEMA_VERSION")
    if doc["g3l"] != "NO_GO" or doc["qualification_credit"] != 0 \
            or doc["execution_authority"] is not False \
            or doc["provider_requests_by_this_package"] != 0:
        out.append("FORBIDDEN_AUTHORITY_OR_CREDIT_PROMOTION")
    if parse_utc(doc["as_of_utc"]) is None:
        out.append("AS_OF_UTC")

    source_ids: set = set()
    source_by_id: dict = {}
    sources = doc["evidence_sources"] if isinstance(doc["evidence_sources"], list) else []
    if not sources:
        out.append("SCHEMA:evidence_sources")
    for src in sources:
        if not _closed(src, SOURCE_KEYS, "evidence_source", out):
            continue
        if type(src["source_id"]) is not str or not src["source_id"]:
            out.append("SOURCE_ID_INVALID")
            continue
        if src["source_id"] in source_ids:
            out.append(f"DUPLICATE_SOURCE:{src['source_id']}")
        source_ids.add(src["source_id"])
        source_by_id[src["source_id"]] = src
        if not (type(src["sha256"]) is str and SHA256_RE.fullmatch(src["sha256"])
                and type(src["byte_length"]) is int and src["byte_length"] > 0
                and type(src["path"]) is str and type(src["binding"]) is str
                and src["binding"] in BINDINGS):
            out.append(f"SOURCE_BINDING:{src['source_id']}")
    if not SOURCE_IDS <= source_ids:
        out.append("PINNED_SOURCE_DROPPED")
    expected_sources = {s[0]: s[1:5] for s in PINNED_SOURCES}
    expected_sources.update(RECOVERED_BODY_SOURCES)
    for source_id, expected in expected_sources.items():
        src = source_by_id.get(source_id)
        if src is None or tuple(src.get(k) for k in ("path", "binding", "byte_length", "sha256")) != expected:
            out.append(f"PINNED_SOURCE_BINDING:{source_id}")

    def refs_ok(ids: Any, label: str) -> None:
        if not isinstance(ids, list) or not ids or any(type(i) is not str or i not in source_ids for i in ids):
            out.append(f"UNBOUND_EVIDENCE:{label}")

    domains = doc["control_domains"]
    if not isinstance(domains, dict) or set(domains) != set(REQUIRED_DOMAIN_ORIGINS):
        out.append("CONTROL_DOMAIN_SET")
        domains = {}
    origin_owner: dict = {}
    for name, dom in domains.items():
        if not _closed(dom, DOMAIN_KEYS, f"domain:{name}", out):
            continue
        if not isinstance(dom["origins"], list) or not isinstance(dom["providers"], list) \
                or not REQUIRED_DOMAIN_ORIGINS[name] <= set(dom["origins"]) \
                or not REQUIRED_DOMAIN_PROVIDERS[name] <= set(dom["providers"]):
            out.append(f"DOMAIN_SCOPE_NARROWED:{name}")
            continue
        if dom["status"] not in DOMAIN_STATUSES:
            out.append(f"DOMAIN_STATUS:{name}")
        if dom["later_successes_clear_hold"] is not False:
            out.append(f"PERMISSION_INFERRED_FROM_SUCCESS:{name}")
        for key in ("scope_independence_review", "resumption_review"):
            if dom[key] is not None and not _is_ref(dom[key]):
                out.append(f"DOMAIN_REVIEW_REF:{name}:{key}")
        for origin in dom["origins"]:
            if origin in origin_owner:
                out.append(f"ORIGIN_IN_TWO_DOMAINS:{origin}")
            origin_owner[origin] = name

    contacts = doc["contacts"] if isinstance(doc["contacts"], list) else []
    contact_ids: set = set()
    for c in contacts:
        if not _closed(c, CONTACT_KEYS, "contact", out):
            continue
        contact_ids.add(c["contact_id"])
        if c["classification"] != OBSERVED:
            out.append(f"CONTACT_PROMOTED_TO_PERMISSION:{c['contact_id']}")
        if c["g3_ledger"] is not False:
            out.append(f"CONTACT_CLAIMS_G3_LEDGER:{c['contact_id']}")
        if origin_owner.get(c["origin"]) != c["control_domain"]:
            out.append(f"CONTACT_DOMAIN:{c['contact_id']}")
        _closed(c["client_policy"], CLIENT_POLICY_KEYS, f"client_policy:{c['contact_id']}", out)
        refs_ok(c["evidence"], f"contact:{c['contact_id']}")

    events = [e for e in doc["restriction_events"] if isinstance(e, dict)] \
        if isinstance(doc["restriction_events"], list) else []
    if not isinstance(doc["restriction_events"], list) or len(events) != len(doc["restriction_events"]):
        out.append("SCHEMA:restriction_events")
    by_domain: dict = {}
    seen_ids: set = set()
    for e in events:
        if not _closed(e, EVENT_KEYS, "restriction_event", out):
            continue
        if type(e["event_id"]) is not str or REQUEST_ID_RE.fullmatch(e["event_id"]) is None:
            out.append("EVENT_ID_INVALID")
            continue
        if e["event_id"] in seen_ids:
            out.append(f"DUPLICATE_RESTRICTION_EVENT:{e['event_id']}")
        seen_ids.add(e["event_id"])
        domain_name = e["control_domain"]
        if type(domain_name) is not str or domain_name not in REQUIRED_DOMAIN_ORIGINS:
            out.append(f"EVENT_DOMAIN:{e['event_id']}")
            continue
        if (type(e["origin"]) is not str or origin_owner.get(e["origin"]) != domain_name):
            out.append(f"EVENT_DOMAIN:{e['event_id']}")
            continue
        if type(e["path"]) is not str or not e["path"]:
            out.append(f"EVENT_PATH:{e['event_id']}")
        if type(e["time_basis"]) is not str or not e["time_basis"]:
            out.append(f"EVENT_TIME_BASIS:{e['event_id']}")
        if e["received_at_utc"] is None:
            if type(e["time_basis"]) is not str or not e["time_basis"].startswith("UNRETAINED"):
                out.append(f"EVENT_TIME:{e['event_id']}")
        elif _event_time(e) is None:
            out.append(f"EVENT_TIME:{e['event_id']}")
        if e["carry_forward"] != HOLD_CARRY:
            out.append(f"EVENT_NOT_CARRIED_FORWARD:{e['event_id']}")
        if type(e["occurrences"]) is not int or e["occurrences"] < 1:
            out.append(f"EVENT_OCCURRENCES:{e['event_id']}")
        if e["status"] is not None and (type(e["status"]) is not int or not 100 <= e["status"] <= 599):
            out.append(f"EVENT_STATUS:{e['event_id']}")
        if type(e["kind"]) is not str or not e["kind"]:
            out.append(f"EVENT_KIND:{e['event_id']}")
        if e["body_bytes"] is not None and (type(e["body_bytes"]) is not int or e["body_bytes"] < 0):
            out.append(f"EVENT_BODY_BYTES:{e['event_id']}")
        if e["body_sha256"] is not None and (type(e["body_sha256"]) is not str
                or SHA256_RE.fullmatch(e["body_sha256"]) is None):
            out.append(f"EVENT_BODY_SHA256:{e['event_id']}")
        elif type(e["body_sha256"]) is str:
            body_id = f"recovered_body_{e['body_sha256'][:12]}"
            if (e["body_sha256"] not in RECOVERED_BODY_PINS
                    or e["body_bytes"] != RECOVERED_BODY_PINS[e["body_sha256"]]
                    or type(e["evidence"]) is not list or body_id not in e["evidence"]):
                out.append(f"EVENT_BODY_BINDING:{e['event_id']}")
        if type(e["body_custody"]) is not str or not e["body_custody"]:
            out.append(f"EVENT_BODY_CUSTODY:{e['event_id']}")
        if (not isinstance(e["headers_retained"], dict)
                or any(type(k) is not str or type(v) is not str
                       for k, v in e["headers_retained"].items())):
            out.append(f"EVENT_HEADERS:{e['event_id']}")
        if e["retained_record"] is not None and not isinstance(e["retained_record"], dict):
            out.append(f"EVENT_RETAINED_RECORD:{e['event_id']}")
        if type(e["post_event_requests"]) is not str or not e["post_event_requests"]:
            out.append(f"EVENT_POST_REQUESTS:{e['event_id']}")
        if e["expiry_adjudication"] is not None and not _is_ref(e["expiry_adjudication"]):
            out.append(f"EVENT_EXPIRY_REF:{e['event_id']}")
        if e["retry_after"] is not None and type(e["retry_after"]) is not str:
            out.append(f"EVENT_RETRY_AFTER:{e['event_id']}")
        if e["retry_after"] is None and e["retry_not_before_utc"] is not None:
            out.append(f"INVENTED_RETRY_AFTER:{e['event_id']}")
        if e["retry_not_before_utc"] is not None and parse_utc(e["retry_not_before_utc"]) is None:
            out.append(f"RETRY_NOT_BEFORE:{e['event_id']}")
        refs_ok(e["evidence"], f"event:{e['event_id']}")
        by_domain.setdefault(domain_name, []).append(e)
        hold_basis = domains[domain_name].get("hold_basis") if isinstance(domains.get(domain_name), dict) else None
        if not isinstance(hold_basis, list) or e["event_id"] not in hold_basis:
            out.append(f"EVENT_NOT_IN_HOLD_BASIS:{e['event_id']}")
    missing = REQUIRED_EVENT_IDS - seen_ids
    for event_id in sorted(missing):
        out.append(f"MISSING_RETAINED_RESTRICTION:{event_id}")
    for event_id, digest in HISTORICAL_EVENT_PINS.items():
        matches = [e for e in events if e.get("event_id") == event_id]
        if len(matches) != 1 or _historical_event_digest(matches[0]) != digest:
            out.append(f"HISTORICAL_EVENT_CHANGED:{event_id}")
        elif matches[0]["retry_after"] is not None or matches[0]["retry_not_before_utc"] is not None:
            out.append(f"HISTORICAL_RETRY_FACT_CHANGED:{event_id}")
    for status, at, body in PINNED_ECMWF_EVENTS:
        match = [e for e in events if set(e) == EVENT_KEYS
                 and (e["status"], e["received_at_utc"], e["body_sha256"]) == (status, at, body)]
        if len(match) != 1:
            out.append(f"MISSING_RETAINED_DENIAL:{status}:{at}")
            continue
        record = match[0]["retained_record"]
        try:
            ok = (isinstance(record, dict) and set(record) == {"capture", "response"}
                  and _retained_record_digest(record) == RETAINED_DENIAL_DIGESTS[(status, at[:-1] + "+00:00", body)]
                  and match[0]["headers_retained"] == record["response"]["headers"])
        except (KeyError, TypeError, ValueError):
            ok = False
        if not ok:
            out.append(f"TAMPERED_RETAINED_DENIAL:{status}:{at}")

    for name, dom in domains.items():
        if not isinstance(dom, dict) or set(dom) != DOMAIN_KEYS:
            continue
        held = by_domain.get(name, [])
        if not isinstance(dom["hold_basis"], list) or \
                sorted(dom["hold_basis"]) != sorted(e["event_id"] for e in held):
            out.append(f"HOLD_BASIS_INCOMPLETE:{name}")
        if dom["status"] == "RESUMED_BY_REVIEW" and (
                not _is_ref(dom["resumption_review"])
                or any(not _is_ref(e["expiry_adjudication"]) for e in held)):
            out.append(f"RESUMPTION_WITHOUT_REVIEWED_EXPIRY:{name}")

    perms = doc["permissions"]
    if not isinstance(perms, dict) or set(perms) != set(PROVIDERS):
        out.append("SCHEMA:permissions")
        perms = {}
    permission_ids: dict = {}
    permission_records: dict = {}
    for provider, p in perms.items():
        if not _closed(p, PERMISSION_KEYS, f"permission:{provider}", out):
            continue
        if not isinstance(p["observed_public_anonymous_access"], list) or \
                any(cid not in contact_ids for cid in p["observed_public_anonymous_access"]):
            out.append(f"UNBOUND_OBSERVED_ACCESS:{provider}")
        reviewed = p["reviewed_permissions"]
        if not isinstance(reviewed, list):
            out.append(f"SCHEMA:reviewed_permissions:{provider}")
            continue
        for r in reviewed:
            if not _closed(r, REVIEWED_PERMISSION_KEYS, f"reviewed_permission:{provider}", out):
                continue
            permission_id = r["permission_id"]
            if type(permission_id) is not str or not 1 <= len(permission_id) <= 128:
                out.append(f"PERMISSION_ID_INVALID:{provider}")
                continue
            if permission_id in permission_ids:
                out.append(f"DUPLICATE_PERMISSION_ID:{permission_id}")
            permission_ids[permission_id] = provider
            permission_records[permission_id] = r
            if not (isinstance(r["origins"], list) and r["origins"]
                    and all(type(x) is str and x in REQUIRED_DOMAIN_ORIGINS["ECMWF"] | REQUIRED_DOMAIN_ORIGINS["NOAA_GEFS"]
                            for x in r["origins"])
                    and isinstance(r["purposes"], list) and r["purposes"]
                    and all(type(x) is str and x in ("INDEX", "FIELD") for x in r["purposes"])):
                out.append(f"PERMISSION_SCOPE_INVALID:{provider}")
            if r["basis"] != "LICENCE_AND_ACCESS_TERMS_INDEPENDENT_REVIEW" \
                    or not _is_ref(r["licence_document"]) or not _is_ref(r["review_ref"]) \
                    or r["licence_document"]["sha256"] == r["review_ref"]["sha256"]:
                out.append(f"PERMISSION_WITHOUT_REVIEWED_LICENCE:{provider}")
            start, end = parse_utc(r["reviewed_at_utc"]), parse_utc(r["valid_until_utc"])
            if start is None or end is None or end <= start:
                out.append(f"PERMISSION_VALIDITY_WINDOW:{provider}")
        expected = "REVIEWED_PERMISSION_RECORDED" if reviewed else "NO_REVIEWED_PERMISSION"
        if p["status"] != expected:
            out.append(f"PERMISSION_STATUS:{provider}")
        if not reviewed and p["licence_document_bytes"] is not None:
            out.append(f"UNREVIEWED_LICENCE_PROMOTED:{provider}")

    env = doc["request_envelope"]
    if _closed(env, ENVELOPE_KEYS, "request_envelope", out):
        if env["methods"] != ["GET"]:
            out.append("ENVELOPE_METHODS")
        seen_specs = set()
        for spec in env["reviewed_origin_path_specs"] if isinstance(env["reviewed_origin_path_specs"], list) else [None]:
            if not _closed(spec, SPEC_KEYS, "reviewed_spec", out):
                continue
            if (type(spec["permission_id"]) is not str or type(spec["provider"]) is not str
                    or type(spec["origin"]) is not str or type(spec["purpose"]) is not str
                    or _literal_path_spec(spec["path_regex"]) is None):
                out.append("REVIEWED_SPEC_INVALID")
                continue
            identity = (spec["origin"], spec["provider"], spec["purpose"],
                        _literal_path_spec(spec["path_regex"]))
            if identity in seen_specs:
                out.append("DUPLICATE_REVIEWED_SPEC")
            seen_specs.add(identity)
            if permission_ids.get(spec["permission_id"]) != spec["provider"] \
                    or not _is_ref(spec["review_ref"]):
                out.append("REVIEWED_SPEC_WITHOUT_PERMISSION")
            permission = permission_records.get(spec["permission_id"])
            if (permission is None or not isinstance(permission.get("origins"), list)
                    or spec["origin"] not in permission["origins"]
                    or not isinstance(permission.get("purposes"), list)
                    or spec["purpose"] not in permission["purposes"]):
                out.append("REVIEWED_SPEC_OUTSIDE_PERMISSION_SCOPE")
        for spec in env["candidate_origin_path_specs"] if isinstance(env["candidate_origin_path_specs"], list) else [None]:
            if _closed(spec, CANDIDATE_SPEC_KEYS, "candidate_spec", out) \
                    and spec["status"] != "UNQUALIFIED_PROPOSAL":
                out.append("CANDIDATE_SPEC_PROMOTED")
        limits = env["campaign_limits"]
        if not isinstance(limits, dict) or set(limits) != {"attempts", "body_bytes"} \
                or any(type(v) is not int or v <= 0 for v in limits.values()) \
                or limits["attempts"] > 8 or limits["body_bytes"] > 33_554_432:
            out.append("CAMPAIGN_LIMITS_EXPANDED")

    for f in doc["transport_findings"] if isinstance(doc["transport_findings"], list) else [None]:
        if _closed(f, FINDING_KEYS, "transport_finding", out):
            refs_ok(f["evidence"], f"finding:{f['finding_id']}")

    assessment = doc["identity_assessment"]
    if not isinstance(assessment, dict) or set(assessment) != set(IN_SCOPE_IDS):
        out.append("IDENTITY_SET_DRIFT")
        assessment = {}
    for identity, a in assessment.items():
        if not _closed(a, ASSESSMENT_KEYS, f"assessment:{identity}", out):
            continue
        if a["disposition"] not in DISPOSITIONS:
            out.append(f"DISPOSITION:{identity}")
        if a["closable_offline_on_independent_review"] is not (a["disposition"] in OFFLINE_DISPOSITIONS):
            out.append(f"CLOSABILITY_INCONSISTENT:{identity}")
        if identity in RUN_SPECIFIC and a["disposition"] != "FORWARD_RUN_OBSERVATION":
            out.append(f"RUN_IDENTITY_NOT_FORWARD:{identity}")
        if identity.endswith("_licence_anonymous_access") and a["disposition"] in OFFLINE_DISPOSITIONS:
            provider = identity.split(".")[1].split("_")[0].upper()
            if not (isinstance(perms.get(provider), dict) and perms[provider].get("reviewed_permissions")):
                out.append(f"PERMISSION_CLAIMED_WITHOUT_REVIEW:{identity}")
    impact = doc["identity_impact"]
    if _closed(impact, IMPACT_KEYS, "identity_impact", out):
        typed = {i: a for i, a in assessment.items() if isinstance(a, dict)}
        offline = sorted(i for i, a in typed.items() if a.get("disposition") == "OFFLINE_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW")
        held_only = sorted(i for i, a in typed.items() if a.get("disposition") == "OFFLINE_ADJUDICABLE_AS_HELD_ONLY")
        if impact["closed_by_this_package"] != 0 or impact["qualification_credit"] != 0 \
                or impact["audit_missing_after"] != impact["audit_missing_before"]:
            out.append("IDENTITY_CREDIT_CLAIMED")
        if impact["offline_candidates_on_independent_review"] != offline \
                or impact["offline_adjudicable_as_held_only"] != held_only \
                or impact["remaining_future_or_external"] != len(assessment) - len(offline) - len(held_only):
            out.append("IDENTITY_IMPACT_INCONSISTENT")
    return out


def check_lineage(doc: Any) -> list[str]:
    """Return bounded, deterministic diagnostics for ordinary malformed JSON."""
    try:
        return _check_lineage(doc)[:128]
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError, re.error):
        return ["MALFORMED_LINEAGE"]


# ---------------------------------------------------------------------------
# Offline reviewed-reference resolution. ``_is_ref`` above accepts a bare
# {sha256, byte_length, path} shape; it does not read a file or establish
# that the file is an independent review of the exact thing it is attached
# to. This section resolves every non-null expiry_adjudication,
# scope_independence_review, resumption_review, licence_document, and
# review_ref to bounded local bytes under one trusted root, and binds each
# resolved review record to the event/domain/origin/provider/purpose/path it
# must cover. It adds no default trusted root, no default acceptance, no
# provider request and no DNS. It never grants execution_authority and it
# does not change what ``check_lineage`` or ``evaluate_request`` return;
# call it only after ``check_lineage`` reports no findings.
# ---------------------------------------------------------------------------

REVIEW_SCHEMA = "ALPHA_V11_G3_REVIEWED_REFERENCE_V1"
REVIEW_OUTCOME_OK = "INDEPENDENTLY_CONFIRMED"
REVIEWER_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
REVIEW_RECORD_LIMIT = 65536
REVIEWED_DOCUMENT_LIMIT = 16 * 1024 * 1024
REVIEW_COMMON_KEYS = frozenset({
    "schema", "kind", "outcome", "reviewer_identity",
    "reviewer_is_self_interested", "decision_utc", "basis",
})
REVIEW_KIND_EXTRA_KEYS = {
    "EXPIRY_ADJUDICATION": frozenset({
        "event_id", "control_domain", "origin", "original_restriction_sha256", "valid_until_utc"}),
    "RESUMPTION": frozenset({
        "control_domain", "hold_basis_event_ids", "scope_independent",
        "permitted_resumption_basis", "requested_at_utc"}),
    "SCOPE_INDEPENDENCE": frozenset({"control_domain", "independent_origins"}),
    "LICENCE_PERMISSION": frozenset({
        "provider", "licence_document_sha256", "origins", "purposes", "revoked", "valid_until_utc"}),
    "PATH_SPEC": frozenset({"permission_id", "origin", "provider", "purpose", "path"}),
}


def _confine_to_root(path_str: Any, root_real: Path) -> Path:
    """Resolve a reviewed-reference path strictly inside ``root_real``.

    Rejects an absolute or traversing ref path, and any symlink at any
    path component between the root and the target, so a ref cannot be
    made to point outside the one place its bytes are trusted to live.
    """
    if type(path_str) is not str or not path_str or "\x00" in path_str:
        raise LineageError("REVIEWED_REF_PATH_INVALID")
    parts = PurePosixPath(path_str).parts
    if not parts or parts[0] == "/" or any(p in ("", ".", "..") for p in parts):
        raise LineageError("REVIEWED_REF_PATH_INVALID")
    current = root_real
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise LineageError("REVIEWED_REF_PATH_SYMLINK")
    try:
        current.relative_to(root_real)
    except ValueError:
        raise LineageError("REVIEWED_REF_PATH_ESCAPES_ROOT") from None
    return current


def _resolve_ref_bytes(ref: Any, root_real: Path, limit: int) -> bytes:
    """Read the exact bytes a shape-valid ref claims, under the trusted root."""
    if not _is_ref(ref):
        raise LineageError("REVIEWED_REF_SHAPE_INVALID")
    raw = read_regular(_confine_to_root(ref["path"], root_real), limit)
    if len(raw) != ref["byte_length"] or _sha(raw) != ref["sha256"]:
        raise LineageError("REVIEWED_REF_CONTENT_MISMATCH")
    return raw


def _resolve_review_record(ref: Any, root_real: Path, review_manifest: Mapping, kind: str) -> dict:
    """Resolve one review-record ref and check its own closed shape.

    A matching digest in ``review_manifest`` -- pinned by the caller,
    independently of ``lineage`` -- is required in addition to the record's
    own self-declared fields; a record's hash alone proves file identity,
    never that its reviewer or conclusion are genuine.
    """
    raw = _resolve_ref_bytes(ref, root_real, REVIEW_RECORD_LIMIT)
    record = strict_loads(raw)
    expected_keys = REVIEW_COMMON_KEYS | REVIEW_KIND_EXTRA_KEYS[kind]
    if not isinstance(record, dict) or set(record) != expected_keys:
        raise LineageError(f"REVIEW_RECORD_SCHEMA:{kind}")
    if record["schema"] != REVIEW_SCHEMA or record["kind"] != kind:
        raise LineageError(f"REVIEW_RECORD_KIND:{kind}")
    if record["outcome"] != REVIEW_OUTCOME_OK:
        raise LineageError(f"REVIEW_RECORD_OUTCOME:{kind}")
    if record["reviewer_is_self_interested"] is not False:
        raise LineageError(f"REVIEW_RECORD_SELF_AUTHORED:{kind}")
    if (type(record["reviewer_identity"]) is not str
            or REVIEWER_IDENTITY_RE.fullmatch(record["reviewer_identity"]) is None):
        raise LineageError(f"REVIEW_RECORD_REVIEWER_IDENTITY:{kind}")
    if parse_utc(record["decision_utc"]) is None:
        raise LineageError(f"REVIEW_RECORD_DECISION_TIME:{kind}")
    if type(record["basis"]) is not str or not record["basis"]:
        raise LineageError(f"REVIEW_RECORD_BASIS:{kind}")
    digest = _sha(raw)
    manifest_entry = review_manifest.get(digest) if isinstance(review_manifest, Mapping) else None
    if (not isinstance(manifest_entry, Mapping) or set(manifest_entry) != {"kind", "reviewer_identity"}
            or manifest_entry["kind"] != kind
            or manifest_entry["reviewer_identity"] != record["reviewer_identity"]):
        raise LineageError(f"REVIEW_RECORD_NOT_IN_MANIFEST:{kind}")
    return record


def verify_reviewed_refs(lineage: Mapping, trusted_evidence_root: Path,
                         review_manifest: Mapping) -> list[str]:
    """Resolve and semantically bind every reviewed-reference in ``lineage``.

    This is an additional offline prerequisite, not a replacement for
    ``check_lineage``, and it trusts the lineage's own shape; call it only
    after ``check_lineage`` returns no findings. ``trusted_evidence_root``
    is the one local directory a ref's relative ``path`` may resolve
    inside -- there is no default, and a missing, non-directory or
    symlinked root fails closed. ``review_manifest`` is a separate
    {sha256: {"kind", "reviewer_identity"}} input the caller must have
    pinned independently of ``lineage`` itself. This function never
    returns execution_authority and performs no provider request or DNS;
    it reports findings only, exactly like ``check_lineage``.
    """
    if not isinstance(trusted_evidence_root, Path) or not isinstance(review_manifest, Mapping):
        return ["REVIEWED_REFS_INPUT_INVALID"]
    try:
        root_real = Path(os.path.realpath(trusted_evidence_root, strict=True))
    except OSError as exc:
        return [f"TRUSTED_ROOT_UNAVAILABLE:{exc.strerror}"]
    if trusted_evidence_root.is_symlink() or not root_real.is_dir():
        return ["TRUSTED_ROOT_INVALID"]

    out: list[str] = []

    def check(label: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except LineageError as exc:
            out.append(f"{label}:{exc}")
        except (TypeError, ValueError, KeyError, AttributeError, OverflowError, re.error):
            out.append(f"{label}:MALFORMED_REVIEWED_REF")

    try:
        domains = lineage["control_domains"]
        events = lineage["restriction_events"]
        permissions = lineage["permissions"]
        specs = lineage["request_envelope"]["reviewed_origin_path_specs"]
    except (TypeError, KeyError):
        return ["MALFORMED_REVIEWED_REFS_INPUT"]

    for name, dom in domains.items() if isinstance(domains, dict) else []:
        if not isinstance(dom, dict):
            continue

        def _scope(name=name, dom=dom) -> None:
            record = _resolve_review_record(dom["scope_independence_review"], root_real,
                                            review_manifest, "SCOPE_INDEPENDENCE")
            if record["control_domain"] != name:
                raise LineageError("DOMAIN_MISMATCH")
            if not isinstance(dom.get("origins"), list) \
                    or not set(dom["origins"]) <= set(record["independent_origins"]):
                raise LineageError("SCOPE_NOT_BOUND")

        if dom.get("scope_independence_review") is not None:
            check(f"SCOPE_INDEPENDENCE_REF:{name}", _scope)

        def _resumption(name=name, dom=dom) -> None:
            record = _resolve_review_record(dom["resumption_review"], root_real,
                                            review_manifest, "RESUMPTION")
            if record["control_domain"] != name:
                raise LineageError("DOMAIN_MISMATCH")
            if not isinstance(dom.get("hold_basis"), list) \
                    or not isinstance(record["hold_basis_event_ids"], list) \
                    or sorted(record["hold_basis_event_ids"]) != sorted(dom["hold_basis"]):
                raise LineageError("HOLD_BASIS_NOT_BOUND")
            if record["scope_independent"] is not True:
                raise LineageError("SCOPE_NOT_INDEPENDENT")
            if type(record["permitted_resumption_basis"]) is not str or not record["permitted_resumption_basis"]:
                raise LineageError("RESUMPTION_BASIS_MISSING")
            requested_at = parse_utc(record["requested_at_utc"])
            decision_at = parse_utc(record["decision_utc"])
            if requested_at is None or decision_at is None or requested_at < decision_at:
                raise LineageError("RESUMPTION_TIME_INVALID")

        if dom.get("resumption_review") is not None:
            check(f"RESUMPTION_REF:{name}", _resumption)

    for e in events if isinstance(events, list) else []:
        if not isinstance(e, dict) or e.get("expiry_adjudication") is None:
            continue

        def _expiry(e=e) -> None:
            record = _resolve_review_record(e["expiry_adjudication"], root_real,
                                            review_manifest, "EXPIRY_ADJUDICATION")
            if record["event_id"] != e.get("event_id") or record["control_domain"] != e.get("control_domain") \
                    or record["origin"] != e.get("origin"):
                raise LineageError("EVENT_BINDING_MISMATCH")
            try:
                original = _historical_event_digest(e)
            except (TypeError, ValueError, KeyError):
                raise LineageError("EVENT_SHAPE_INVALID") from None
            if record["original_restriction_sha256"] != original:
                raise LineageError("ORIGINAL_RESTRICTION_UNBOUND")
            decision_at = parse_utc(record["decision_utc"])
            valid_until = parse_utc(record["valid_until_utc"])
            if decision_at is None or valid_until is None or valid_until <= decision_at:
                raise LineageError("EXPIRY_VALIDITY_INVALID")

        check(f"EXPIRY_REF:{e.get('event_id')}", _expiry)

    for provider, p in permissions.items() if isinstance(permissions, dict) else []:
        if not isinstance(p, dict):
            continue
        reviewed = p.get("reviewed_permissions")
        for r in reviewed if isinstance(reviewed, list) else []:
            if not isinstance(r, dict):
                continue

            def _permission(provider=provider, r=r) -> None:
                licence_raw = _resolve_ref_bytes(r.get("licence_document"), root_real, REVIEWED_DOCUMENT_LIMIT)
                record = _resolve_review_record(r.get("review_ref"), root_real,
                                                review_manifest, "LICENCE_PERMISSION")
                if record["provider"] != provider:
                    raise LineageError("PROVIDER_MISMATCH")
                if record["licence_document_sha256"] != _sha(licence_raw):
                    raise LineageError("LICENCE_DOCUMENT_UNBOUND")
                if not isinstance(r.get("origins"), list) or not isinstance(r.get("purposes"), list) \
                        or set(record["origins"]) != set(r["origins"]) \
                        or set(record["purposes"]) != set(r["purposes"]):
                    raise LineageError("PERMISSION_SCOPE_UNBOUND")
                if record["revoked"] is not False:
                    raise LineageError("PERMISSION_REVOKED")
                decision_at = parse_utc(record["decision_utc"])
                valid_until = parse_utc(record["valid_until_utc"])
                if decision_at is None or valid_until is None or valid_until <= decision_at:
                    raise LineageError("PERMISSION_VALIDITY_INVALID")

            check(f"PERMISSION_REF:{provider}:{r.get('permission_id')}", _permission)

    for spec in specs if isinstance(specs, list) else []:
        if not isinstance(spec, dict):
            continue

        def _spec(spec=spec) -> None:
            record = _resolve_review_record(spec.get("review_ref"), root_real,
                                            review_manifest, "PATH_SPEC")
            path = _literal_path_spec(spec.get("path_regex"))
            if (record["permission_id"] != spec.get("permission_id") or record["origin"] != spec.get("origin")
                    or record["provider"] != spec.get("provider") or record["purpose"] != spec.get("purpose")
                    or path is None or record["path"] != path):
                raise LineageError("PATH_SPEC_UNBOUND")

        check(f"PATH_SPEC_REF:{spec.get('permission_id')}", _spec)

    return out[:128]


# ---------------------------------------------------------------------------
# Request envelope: applies the lineage to one proposed request, offline.
# ---------------------------------------------------------------------------

REQUEST_KEYS = frozenset({
    "request_id", "provider", "purpose", "method", "url", "headers", "range",
    "body_bytes", "max_response_bytes", "deadline_seconds", "follow_redirects",
    "max_retries", "attempt_index", "trust_env", "proxy", "netrc", "cookies",
    "client_certificate", "credential_source", "concurrency", "planned_at_utc",
    "permission_id", "failover_from_origin",
})
ALLOWED_REQUEST_HEADERS = frozenset({
    "accept", "accept-encoding", "connection", "host", "user-agent", "range",
})
CREDENTIAL_HEADERS = frozenset({
    "authorization", "cookie", "proxy-authorization", "x-api-key",
})
SIGNED_QUERY_KEYS = frozenset({
    "x-amz-signature", "x-amz-credential", "x-amz-security-token",
    "signature", "awsaccesskeyid", "token", "key", "apikey", "api_key",
})
ATTEMPT_DEADLINE_SECONDS = 30
RANGE_RE = re.compile(r"bytes=([0-9]{1,15})-([0-9]{1,15})\Z")
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
HEADER_TOKEN_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def _origin(url: str) -> Optional[str]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or port not in (None, 443)
            or parts.netloc != parts.hostname):
        return None
    return "https://" + parts.hostname


def _literal_path_spec(spec: Any) -> Optional[str]:
    """Accept only anchored literal paths, with legacy escaped dots."""
    if type(spec) is not str or not 1 <= len(spec) <= 2048 or not spec.startswith("/"):
        return None
    path = spec.replace(r"\.", ".")
    if "\\" in path or re.fullmatch(r"/[A-Za-z0-9_./-]+", path) is None:
        return None
    return path


def _path_matches(spec: Any, path: str) -> bool:
    return _literal_path_spec(spec) == path


def evaluate_request(request: Any, lineage: Mapping, *, now_utc: str,
                     ledger: Optional[Mapping] = None) -> dict:
    """Return REFUSED or ENVELOPE_CONSISTENT_NOT_AUTHORIZED, never permission.

    ``ledger`` is the caller's cumulative campaign accounting
    ``{"attempts": int, "body_bytes": int, "request_ids": [...]}``; absent
    or malformed accounting is unknown and refuses.
    """
    reasons: list[str] = []

    def refuse(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    def result() -> dict:
        return {"outcome": ENVELOPE_OK if not reasons else REFUSED, "reasons": reasons,
                "execution_authority": False}

    findings = check_lineage(lineage)
    if findings:
        reasons.extend(["LINEAGE_INVALID"] + findings[:16])
        return result()
    now = parse_utc(now_utc)
    if now is None:
        refuse("NOW_UTC_INVALID")
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        refuse("REQUEST_SCHEMA_CLOSED_KEYS")
        return result()

    provider, purpose = request["provider"], request["purpose"]
    if type(provider) is not str or provider not in PROVIDERS:
        refuse("PROVIDER_UNKNOWN")
        provider = None
    if type(purpose) is not str or purpose not in ("INDEX", "FIELD"):
        refuse("PURPOSE_NOT_SUPPORTED")
    if request["method"] != "GET":
        refuse("METHOD_NOT_GET")
    url = request["url"]
    wire_url = (type(url) is str and 0 < len(url) <= 2048
                and all(33 <= ord(ch) <= 126 and ch != "\\" for ch in url))
    if not wire_url:
        refuse("URL_WIRE_INVALID")
    origin = _origin(url) if wire_url else None
    path = None
    if origin is None:
        refuse("URL_NOT_PUBLIC_HTTPS_ORIGIN")
    else:
        parts = urlsplit(url)
        if "#" in url:
            refuse("URL_FRAGMENT")
        if "?" in url:
            refuse("URL_QUERY_FORBIDDEN")
            if {k.split("=", 1)[0].lower() for k in parts.query.split("&")} & SIGNED_QUERY_KEYS:
                refuse("SIGNED_OR_CREDENTIAL_URL")
        path = parts.path
        if url != origin + path:
            refuse("URL_NOT_CANONICAL")
        segments = path.split("/")
        if (unquote(path) != path or not path.startswith("/") or "" in segments[1:]
                or "." in segments or ".." in segments):
            refuse("PATH_NOT_CANONICAL")

    # Credentials, ambient configuration and bypass vectors.
    headers = request["headers"]
    if not isinstance(headers, dict) or any(type(k) is not str or type(v) is not str
                                            for k, v in headers.items()):
        refuse("HEADERS_MALFORMED")
        headers = {}
    lowered = {}
    for key, value in headers.items():
        if HEADER_TOKEN_RE.fullmatch(key) is None or any(ord(ch) < 32 or ord(ch) > 126 for ch in value):
            refuse("HEADER_WIRE_INVALID")
        if key.lower() in lowered:
            refuse("HEADER_DUPLICATE")
        lowered[key.lower()] = value
    if set(lowered) & CREDENTIAL_HEADERS or any(k.startswith("x-amz-") for k in lowered):
        refuse("CREDENTIAL_HEADER")
    if set(lowered) - ALLOWED_REQUEST_HEADERS:
        refuse("HEADER_NOT_REVIEWED")
    if lowered.get("accept-encoding") != "identity":
        refuse("ACCEPT_ENCODING_NOT_IDENTITY")
    if origin is not None and lowered.get("host", urlsplit(url).hostname) != urlsplit(url).hostname:
        refuse("HOST_HEADER_MISMATCH")
    if len(headers) > 32 or 2 + sum(len(k.encode("ascii", "replace")) + 2
                                    + len(v.encode("ascii", "replace")) + 2
                                    for k, v in headers.items()) > 4096:
        refuse("HEADER_BOUND")
    for key, code in (("trust_env", "AMBIENT_ENVIRONMENT_TRUSTED"), ("netrc", "NETRC_ENABLED"),
                      ("follow_redirects", "REDIRECTS_ENABLED")):
        if request[key] is not False:
            refuse(code)
    for key, code in (("proxy", "PROXY_CONFIGURED"), ("client_certificate", "CLIENT_CERTIFICATE_PRESENT"),
                      ("credential_source", "CREDENTIAL_SOURCE_PRESENT"),
                      ("failover_from_origin", "FAILOVER_FORBIDDEN")):
        if request[key] is not None:
            refuse(code)
    if request["cookies"] != {} or type(request["cookies"]) is not dict:
        refuse("COOKIES_PRESENT")
    for key, expected, code in (("max_retries", 0, "RETRIES_ENABLED"), ("attempt_index", 0, "RETRY_ATTEMPT"),
                                ("concurrency", 1, "CONCURRENCY_NOT_SINGLE"), ("body_bytes", 0, "REQUEST_BODY_FORBIDDEN")):
        if type(request[key]) is not int or request[key] != expected:
            refuse(code)

    # Size, range and time limits.
    cap = INDEX_CAP if purpose == "INDEX" else FIELD_LIMITS.get(provider) if purpose == "FIELD" else None
    max_bytes = request["max_response_bytes"]
    if type(max_bytes) is not int or max_bytes <= 0 or cap is None or max_bytes > cap:
        refuse("RESPONSE_BYTES_OVER_LIMIT")
    deadline = request["deadline_seconds"]
    if type(deadline) not in (int, float) or not 0 < deadline <= ATTEMPT_DEADLINE_SECONDS:
        refuse("DEADLINE_OVER_LIMIT")
    if lowered.get("range") != request["range"]:
        refuse("RANGE_HEADER_MISMATCH")
    rng = request["range"]
    if purpose == "INDEX" and rng is not None:
        refuse("INDEX_RANGE_FORBIDDEN")
    if purpose == "FIELD":
        match = RANGE_RE.fullmatch(rng) if type(rng) is str else None
        if match is None:
            refuse("FIELD_RANGE_NOT_SINGLE_CLOSED")
        elif int(match[2]) < int(match[1]):
            refuse("FIELD_RANGE_INVERTED")
        elif type(max_bytes) is int and int(match[2]) - int(match[1]) + 1 != max_bytes:
            refuse("FIELD_RANGE_RESPONSE_BOUND_MISMATCH")

    # Accounting: unknown accounting is never treated as zero.
    budget = lineage["request_envelope"]["campaign_limits"]
    request_id = request["request_id"]
    if type(request_id) is not str or REQUEST_ID_RE.fullmatch(request_id) is None:
        refuse("REQUEST_ID_INVALID")
    if not isinstance(ledger, dict) or set(ledger) != {"attempts", "body_bytes", "request_ids"} \
            or type(ledger["attempts"]) is not int or type(ledger["body_bytes"]) is not int \
            or ledger["attempts"] < 0 or ledger["body_bytes"] < 0 \
            or not isinstance(ledger["request_ids"], list) \
            or len(ledger["request_ids"]) != ledger["attempts"] \
            or len(ledger["request_ids"]) > budget["attempts"] \
            or any(type(i) is not str or REQUEST_ID_RE.fullmatch(i) is None
                   for i in ledger["request_ids"]) \
            or len(set(ledger["request_ids"])) != len(ledger["request_ids"]):
        refuse("CAMPAIGN_ACCOUNTING_UNKNOWN")
    else:
        if ledger["attempts"] + 1 > budget["attempts"]:
            refuse("CAMPAIGN_ATTEMPTS_EXHAUSTED")
        if type(max_bytes) is int and ledger["body_bytes"] + max_bytes > budget["body_bytes"]:
            refuse("CAMPAIGN_BYTES_EXHAUSTED")
        if request_id in ledger["request_ids"]:
            refuse("REQUEST_ID_REPLAY")

    # Restriction lineage and permission.
    domain_name = next((n for n, d in lineage["control_domains"].items() if origin in d["origins"]), None)
    if origin is not None and domain_name is None:
        refuse("UNKNOWN_CONTROL_DOMAIN")
    events = [e for e in lineage["restriction_events"] if e["control_domain"] == domain_name]
    if domain_name is not None:
        domain = lineage["control_domains"][domain_name]
        if provider not in domain["providers"]:
            refuse("PROVIDER_ORIGIN_DOMAIN_MISMATCH")
        if domain["status"] != "RESUMED_BY_REVIEW":
            refuse("CONTROL_DOMAIN_HELD")
        if any(e["expiry_adjudication"] is None for e in events):
            refuse("UNRESOLVED_RESTRICTION_HISTORY")
        if any(e["status"] in (302, 429, 503, None) and e["expiry_adjudication"] is None for e in events):
            refuse("PRIOR_RATE_LIMIT_OR_UNKNOWN_DENIAL_UNADJUDICATED")
        for event in events:
            not_before = parse_utc(event["retry_not_before_utc"])
            if not_before is not None and (now is None or now < not_before):
                refuse("RETRY_AFTER_CARRIED_FORWARD")
    if not any(s["permission_id"] == request["permission_id"]
               and s["origin"] == origin and s["provider"] == provider and s["purpose"] == purpose
               and path is not None and _path_matches(s["path_regex"], path)
               for s in lineage["request_envelope"]["reviewed_origin_path_specs"]):
        refuse("ORIGIN_PATH_PURPOSE_NOT_REVIEWED")

    permission = None
    if provider is not None:
        permission = next((p for p in lineage["permissions"][provider]["reviewed_permissions"]
                           if p["permission_id"] == request["permission_id"]), None)
    if permission is None:
        refuse("NO_REVIEWED_PERMISSION")
    else:
        if origin not in permission["origins"] or purpose not in permission["purposes"]:
            refuse("PERMISSION_SCOPE_MISMATCH")
        reviewed_at = parse_utc(permission["reviewed_at_utc"])
        valid_until = parse_utc(permission["valid_until_utc"])
        if now is None or not reviewed_at <= now < valid_until:
            refuse("STALE_OR_FUTURE_PERMISSION")
        # A restriction observed at or after the review supersedes it; a
        # restriction whose time was not retained may be later and also does.
        if any(_event_time(e) is None or _event_time(e) >= reviewed_at for e in events):
            refuse("PERMISSION_SUPERSEDED_BY_RESTRICTION")
    planned = parse_utc(request["planned_at_utc"])
    if planned is None or now is None or planned < now:
        refuse("PLANNED_TIME_INVALID_OR_PAST")
    if permission is not None and planned is not None and reviewed_at is not None and valid_until is not None:
        if planned < reviewed_at or planned >= valid_until or (type(deadline) in (int, float)
                and 0 < deadline <= ATTEMPT_DEADLINE_SECONDS
                and planned.timestamp() + deadline >= valid_until.timestamp()):
            refuse("PLANNED_TIME_OUTSIDE_PERMISSION")
    return result()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def verify_recovered_bodies(lineage: Mapping, root: Path = ROOT) -> list[str]:
    """Check committed recovered bodies against their recorded digests."""
    out = []
    sources = lineage.get("evidence_sources") if isinstance(lineage, dict) else None
    source_map = {}
    if isinstance(sources, list):
        for src in sources:
            if isinstance(src, dict) and type(src.get("source_id")) is str:
                source_map[src["source_id"]] = src
    for source_id, (path, binding, length, digest) in RECOVERED_BODY_SOURCES.items():
        src = source_map.get(source_id)
        if src is None or tuple(src.get(k) for k in ("path", "binding", "byte_length", "sha256")) \
                != (path, binding, length, digest):
            out.append(f"RECOVERED_BODY_BINDING:{source_id}")
        try:
            raw = read_regular(root / path, 4096)
        except LineageError:
            out.append(f"RECOVERED_BODY_MISSING:{path}")
            continue
        if len(raw) != length or _sha(raw) != digest:
            out.append(f"RECOVERED_BODY_CHANGED:{path}")
    return out


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="rebuild from pinned retained sources")
    build.add_argument("--write", action="store_true", help="write the artifact and recovered bodies")
    sub.add_parser("check", help="check the committed artifact offline")
    args = parser.parse_args(argv)
    committed_path = ROOT / ARTIFACT
    if args.command == "check":
        raw = read_regular(committed_path, 4 * 1024 * 1024)
        doc = strict_loads(raw)
        problems = check_lineage(doc) + verify_recovered_bodies(doc)
        if canonical_bytes(doc) != raw:
            problems.append("ARTIFACT_NOT_CANONICAL")
        print(json.dumps({"artifact_sha256": _sha(raw), "problems": problems,
                          "qualification_credit": 0, "g3l": "NO_GO"}, sort_keys=True))
        return 1 if problems else 0
    lineage, bodies = build_lineage(load_sources())
    problems = check_lineage(lineage)
    if problems:
        print(json.dumps({"problems": problems}, sort_keys=True))
        return 1
    raw = canonical_bytes(lineage)
    if args.write:
        target = ROOT / RECOVERED_DIR
        target.mkdir(parents=True, exist_ok=True)
        for sha, body in bodies.items():
            (target / f"{sha}.body").write_bytes(body)
        committed_path.write_bytes(raw)
    matches = committed_path.exists() and committed_path.read_bytes() == raw
    print(json.dumps({"artifact_sha256": _sha(raw), "matches_committed": matches,
                      "qualification_credit": 0, "g3l": "NO_GO"}, sort_keys=True))
    return 0 if matches else 1


if __name__ == "__main__":
    sys.exit(main())
