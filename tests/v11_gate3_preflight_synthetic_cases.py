"""Explicitly generated synthetic fixtures for the Gate 3 offline attempt
model (``tools/v11_gate3_preflight_attempt_model.py``).

Every path/identity here is ``synthetic://``-prefixed and every byte string
is fabricated inline in this module. No private package, private evidence or
real provider file is opened. The three immutable public denial descriptors
below are copied verbatim (as literal Python dicts, never loaded at test
time) from the committed public record
``docs/V11_R09_GATE3_A5A6_OFFLINE_AUDIT_20261001.json``; nothing here is a
new verification of private contents and confers no real execution
authority, provider right or G3-L credit.
"""

from __future__ import annotations

import copy
import hashlib
import json

from tools.v11_gate3_evidence_preflight_checker import (
    BINDING_SCHEMA_NAME, ClockObservation, FROZEN_BINDING_SCALARS,
    FROZEN_BINDING_VERDICTS, FROZEN_DISALLOWED, FROZEN_LIMITS,
    FROZEN_REQUEST, FROZEN_REQUEST_HEADERS, FROZEN_RESPONSE_CONTRACT,
    FROZEN_SCALARS, FROZEN_WINDOW, LedgerEntry, PACKAGE_SCHEMA_NAME,
    PREREQ_KEYS, ResourceObservation, RESTRICTIONS_SCHEMA_NAME, StateLedger,
)
from tools.v11_gate3_preflight_attempt_model import (
    Checkpoint, SyntheticInputs, admit_synthetic, step,
)

HEAD0 = "0" * 64

GOOD_CLOCK = ClockObservation(
    measured_utc="2026-10-02T10:05:00Z",
    uncertainty_seconds=0.3,
    calibration_age_seconds=10.0,
    monotonic_consistent=True,
)
GOOD_RESOURCES = ResourceObservation(
    free_disk_bytes_after_reservation=3_300_000_000,
    mem_available_bytes_after_reservation=600_000_000,
    physically_reserved_bytes=67_108_864,
)
GOOD_REVIEW_TERMINAL = {
    "exit_code": 0,
    "error": None,
    "initial_clean": True,
    "verdict": "EXECUTABLE_PREFLIGHT_PASS",
}

# The three immutable public September 30 denial identities, copied verbatim
# from the committed public audit. Their (status, received_at, sha256) tuple
# and full record bytes are fixed by the checker's RETAINED_DENIAL_DIGESTS
# table; any mutation here must be refused by the checker (exercised by P07).
RETAINED_DENIAL_RECORDS = [
    {
        "capture": "earlier-loose-aws-retry",
        "expiry_adjudication": None,
        "response": {
            "headers": {
                "connection": "close",
                "content-type": "application/xml",
                "date": "Wed, 30 Sep 2026 07:48:13 GMT",
                "server": "AmazonS3",
                "transfer-encoding": "chunked",
                "x-amz-id-2": "PFu5qXG/MgsE4nX1QIOndte50H568EIlXRL7CU+DYnCmpQZqIsLjms9f4KCnOqVsbEprD2az+udyoKM0KpePlgbWuruMlWWD",
                "x-amz-request-id": "F69ZYP2JCF0639HX",
            },
            "received_at": "2026-09-30T07:48:13.707996+00:00",
            "sha256": "7c21325b9a8c5d3b7f06bed411ae11e6fa6dcb490320671bd8e1d49a64956a28",
            "status": 503,
            "url": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/20260823/00z/ifs/0p25/oper/20260823000000-3h-oper-fc.index",
        },
    },
    {
        "capture": "capture-v1",
        "expiry_adjudication": None,
        "response": {
            "bytes": 278,
            "evidence_class": "PUBLIC_OBSERVED",
            "headers": {"date": "Wed, 30 Sep 2026 07:54:39 GMT"},
            "path": "/20260823/00z/ifs/0p25/oper/20260823000000-3h-oper-fc.index",
            "received_at": "2026-09-30T07:54:39.435641+00:00",
            "sha256": "0986be0818f5c4e80bddac64bcd37d8f77da4c43acb380aaa7e4bcb677a51460",
            "source": "aws",
            "status": 503,
        },
    },
    {
        "capture": "capture-v1",
        "expiry_adjudication": None,
        "response": {
            "bytes": 17,
            "evidence_class": "PUBLIC_OBSERVED",
            "headers": {},
            "path": "/20260928/00z/ifs/0p25/enfo/20260928000000-12h-enfo-ef.index",
            "received_at": "2026-09-30T07:55:15.376499+00:00",
            "sha256": "3850dfdbf4489250268b5f0740240a9f4445e7c5c29e1d03aa0c5446808d7507",
            "source": "portal",
            "status": 429,
        },
    },
]

# The 22 retained refusal reasons from the accepted public verdict (handoff
# "retained 22 reasons" paragraph): 12 null prerequisites plus 10 others.
NULLABLE_PREREQS = sorted(PREREQ_KEYS - {"owner_directive_original_record"})
RETAINED_22_REASONS = frozenset(
    {f"NULL_PREREQUISITE:{k}" for k in NULLABLE_PREREQS}
    | {
        "GEFS_LINEAGE_UNRESOLVED", "GEFS_STATUS_NOT_ADMISSIBLE",
        "UNRESOLVED_GEFS_SCOPE", "NULL_COMPLETE_LINEAGE_REVIEW",
        "NULL_SHARED_HISTORY_HEAD", "NULL_UNRESOLVED_ATTEMPT_RECONCILIATION",
        "MISSING_EXECUTION_REVIEW", "MISSING_LIVE_LEDGER",
        "MISSING_STORAGE_PERSISTENCE_REVIEW", "NO_PHYSICAL_STORAGE_RESERVATION",
    }
)
assert len(RETAINED_22_REASONS) == 22


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ref(tag: str, data: bytes = b"synthetic") -> dict:
    return {
        "sha256": _sha(f"{tag}:{data!r}".encode()),
        "byte_length": len(data),
        "path": f"synthetic://offline-fixture/{tag}",
    }


def good_package_dict() -> dict:
    """A structurally complete synthetic package satisfying every checker
    requirement. Still frozen to NO_GO/False/0 throughout."""

    pkg = dict(FROZEN_SCALARS)
    pkg.update({
        "author_model": "synthetic-test-fixture",
        "blocking_reasons": [],
        "directories": [
            {"device": 1, "inode": 1, "mode": "0700", "path": "synthetic://root",
             "qualification": "OBSERVATION_ONLY", "uid": 1000},
        ],
        "disallowed": sorted(FROZEN_DISALLOWED),
        "independent_execution_review": {
            "binding": "DETACHED_ENVELOPE_OF_FINAL_PACKAGE_AND_RUNTIME",
            "present": True,
        },
        "input_refs": [dict(_ref("input-0"), repository_path="docs/synthetic-input-0.md")],
        "limits": dict(FROZEN_LIMITS),
        "prerequisites": {
            **{key: _ref(f"prereq-{key}") for key in NULLABLE_PREREQS},
            "owner_directive_original_record": {
                "byte_length": 1441, "path": "synthetic://owner-directive",
                "qualification": "OWNER_INSTRUCTION_ONLY_NOT_PROVIDER_RIGHTS",
                "sha256": _sha(b"owner-directive"),
            },
        },
        "requests": [{
            **{k: v for k, v in FROZEN_REQUEST.items()},
            "request_headers": dict(FROZEN_REQUEST_HEADERS),
            "restriction_status": "SCOPE_INDEPENDENCE_CONFIRMED",
        }],
        "response_contract": dict(FROZEN_RESPONSE_CONTRACT),
        "restrictions_ref": None,  # filled in once restrictions bytes are known
        "schema": PACKAGE_SCHEMA_NAME,
        "storage_qualification": {
            "live_ledger_created": True,
            "persistence_review": _ref("prereq-persistence"),
            "physically_reserved_bytes": 67_108_864,
        },
        "unknown_outputs_not_required_as_inputs": [
            "index_body_sha256", "index_etag", "selected_index_row",
            "proposed_field_range", "actual_phase_clocks",
        ],
        "window": dict(FROZEN_WINDOW),
    })
    return pkg


def good_restrictions_dict() -> dict:
    return {
        "complete_lineage_review": _ref("lineage-review"),
        "execution_authority": False,
        "inventory_audit_ref": _ref("inventory-audit"),
        "known_control_domains": {
            "ECMWF": {
                "models": ["IFS", "AIFS"],
                "origins": [
                    "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
                    "https://data.ecmwf.int",
                ],
                "resumption_review": None,
                "status": "HELD",
            },
            "GEFS": {
                "origins": ["https://noaa-gefs-pds.s3.amazonaws.com"],
                "scope_independence_review": _ref("gefs-scope-review"),
                "status": "SCOPE_INDEPENDENCE_CONFIRMED",
            },
        },
        "raw_source_refs": [_ref("raw-source-0")],
        "records": copy.deepcopy(RETAINED_DENIAL_RECORDS),
        "schema": RESTRICTIONS_SCHEMA_NAME,
        "shared_history_head": _sha(b"synthetic-history-head"),
        "status": "RECONCILED_SYNTHETIC_FIXTURE",
        "unresolved_attempt_reconciliation": _ref("unresolved-reconciliation"),
    }


def good_binding_dict(package_raw: bytes, restrictions_raw: bytes, protocol_raw: bytes) -> dict:
    binding = dict(FROZEN_BINDING_SCALARS)
    binding.update({
        "allowed_review_verdicts": sorted(FROZEN_BINDING_VERDICTS),
        "missing_prerequisites": [],
        "native_decode_calls": 0,
        "owner_instruction_record": _ref("owner-instruction"),
        "prepared_at_utc": "2026-10-02T09:25:30.001040+00:00",
        "private_package": {"byte_length": len(package_raw), "path": "synthetic://package",
                             "sha256": _sha(package_raw)},
        "private_restrictions": {"byte_length": len(restrictions_raw), "path": "synthetic://restrictions",
                                  "sha256": _sha(restrictions_raw)},
        "private_root": "synthetic://root",
        "protocol": {"byte_length": len(protocol_raw), "path": "synthetic://protocol",
                     "sha256": _sha(protocol_raw)},
        "provider_requests": 0,
        "qualification_credit": 0,
        "raw_restriction_sources": [],
        "schema": BINDING_SCHEMA_NAME,
        "source_inputs": [],
    })
    return binding


def protocol_raw_bytes() -> bytes:
    return b"synthetic offline protocol fixture bytes, not the real protocol"


def encode_raws(pkg: dict, restrictions: dict, protocol_raw: bytes | None = None):
    """Encode a (package, restrictions) dict pair into mutually consistent
    exact raw bytes, computing the restrictions_ref/binding byte references
    fresh from the actual encoded bytes every time (never a stale/cached
    reference)."""

    protocol_raw = protocol_raw if protocol_raw is not None else protocol_raw_bytes()
    restrictions_raw = json.dumps(restrictions).encode()
    pkg = copy.deepcopy(pkg)
    pkg["restrictions_ref"] = {
        "byte_length": len(restrictions_raw), "path": "synthetic://restrictions",
        "sha256": _sha(restrictions_raw),
    }
    package_raw = json.dumps(pkg).encode()
    binding_raw = json.dumps(good_binding_dict(package_raw, restrictions_raw, protocol_raw)).encode()
    return package_raw, restrictions_raw, protocol_raw, binding_raw


def good_raws():
    return encode_raws(good_package_dict(), good_restrictions_dict())


def good_inputs(**overrides) -> SyntheticInputs:
    package_raw, restrictions_raw, protocol_raw, binding_raw = overrides.pop(
        "raws", None) or good_raws()
    kwargs = dict(
        package_raw=package_raw, restrictions_raw=restrictions_raw,
        protocol_raw=protocol_raw, binding_raw=binding_raw,
        clock=GOOD_CLOCK, resources=GOOD_RESOURCES, ledger=StateLedger(()),
        review_terminal=dict(GOOD_REVIEW_TERMINAL), mode="SYNTHETIC_ONLY",
    )
    kwargs.update(overrides)
    return SyntheticInputs(**kwargs)


def genesis_checkpoint(**overrides) -> Checkpoint:
    kwargs = dict(
        campaign_id="synthetic://campaign/alpha-v11-evidence-preflight-20261002",
        pilot_id="synthetic://pilot/p01",
        expected_history_head=HEAD0,
        external_history_head=HEAD0,
        owner="synthetic://owner/worker-1",
        used_attempts=0, outstanding_attempts=0,
        used_body_bytes=0, outstanding_body_bytes=0,
        used_time_us=0, outstanding_time_us=0,
        pilot_used_attempts=0, pilot_outstanding_attempts=0,
        pilot_used_body_bytes=0, pilot_outstanding_body_bytes=0,
        holds=(), unfinished_intents=(), last_start_us=None,
        synthetic_genesis=True,
    )
    kwargs.update(overrides)
    return Checkpoint(**kwargs)


def all_blocked_package_and_restrictions():
    """Reproduce exactly the retained 22-reason public verdict blocker set
    on top of the otherwise-good synthetic fixture (handoff P02)."""

    pkg = good_package_dict()
    pkg["prerequisites"] = {
        **{key: None for key in NULLABLE_PREREQS},
        "owner_directive_original_record": pkg["prerequisites"]["owner_directive_original_record"],
    }
    pkg["independent_execution_review"]["present"] = False
    pkg["storage_qualification"]["live_ledger_created"] = False
    pkg["storage_qualification"]["persistence_review"] = None
    pkg["storage_qualification"]["physically_reserved_bytes"] = 0

    restrictions = good_restrictions_dict()
    restrictions["complete_lineage_review"] = None
    restrictions["shared_history_head"] = None
    restrictions["unresolved_attempt_reconciliation"] = None
    restrictions["known_control_domains"]["GEFS"]["status"] = "BLOCKED_UNKNOWN_LINEAGE_AND_SCOPE"
    restrictions["known_control_domains"]["GEFS"]["scope_independence_review"] = None
    # Keep the request's mirrored restriction_status consistent with the
    # now-blocked GEFS status so this fixture triggers exactly the 22
    # retained reasons, not an extra INCONSISTENT_REQUEST_RESTRICTION_STATUS.
    pkg["requests"][0]["restriction_status"] = "BLOCKED_UNKNOWN_LINEAGE_AND_SCOPE"
    return pkg, restrictions


# -- Event-script driver: builds the exact closed-schema event dicts for the
# happy-path tag sequence, chaining each event's seq/head to the state
# actually returned by the previous step (never a precomputed guess). -------

HAPPY_TAGS = ("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START", "STATUS",
              "HEADERS", "BODY", "CLOSE_ACK", "ACCOUNT_ACK", "SEAL_ACK")


def event_fields(tag: str, cfg: dict) -> dict:
    start_mono = cfg.get("start_mono", 10_000_000)
    body = cfg.get("body", b"abc")
    clock = cfg.get("clock", GOOD_CLOCK)
    resources = cfg.get("resources", GOOD_RESOURCES)
    host = cfg.get("host", "synthetic://host/h1")
    boot = cfg.get("boot", "synthetic://boot/b1")
    body_mono = cfg.get("body_mono", start_mono + 100_000)
    close_mono = cfg.get("close_mono", start_mono + 200_000)
    seal_mono = cfg.get("seal_mono", start_mono + 300_000)

    if tag == "LOCKS":
        return {"locks": cfg.get("locks", ("shared", "stage", "session", "budget", "store"))}
    if tag == "INTENT_ACK":
        return {"ref": cfg.get("intent_ref", "synthetic://intent/p01")}
    if tag == "RESERVE_ACK":
        return {
            "ref": cfg.get("reservation_ref", "synthetic://reservation/p01"),
            "attempts": cfg.get("attempts", 1),
            "body_bytes": cfg.get("body_bytes", 3_145_728),
            "time_us": cfg.get("time_us", 60_000_000),
            "report_bytes": cfg.get("report_bytes", 16_777_216),
        }
    if tag == "START":
        return {"clock": clock, "resources": resources, "monotonic_us": start_mono,
                "host": host, "boot": boot}
    if tag == "STATUS":
        return {"status": cfg.get("status", 200), "explicit_denial": cfg.get("explicit_denial", False)}
    if tag == "HEADERS":
        content_length = cfg.get("content_length", len(body))
        headers = [
            (b"Content-Type", cfg.get("content_type", b"text/plain")),
            (b"Content-Length", str(content_length).encode()),
        ]
        headers.extend(cfg.get("extra_headers", ()))
        return {"headers": tuple(headers)}
    if tag == "BODY":
        return {"data": cfg.get("body_chunk", body), "clock": cfg.get("body_clock", clock),
                "monotonic_us": body_mono, "host": host, "boot": boot}
    if tag == "CLOSE_ACK":
        return {"ref": cfg.get("closure_ref", "synthetic://closure/p01"),
                "clock": cfg.get("close_clock", clock), "monotonic_us": close_mono,
                "host": host, "boot": boot}
    if tag == "ACCOUNT_ACK":
        return {"ref": cfg.get("accounting_ref", "synthetic://accounting/p01")}
    if tag == "SEAL_ACK":
        return {"ref": cfg.get("seal_ref", "synthetic://seal/p01"),
                "clock": cfg.get("seal_clock", clock), "monotonic_us": seal_mono,
                "host": host, "boot": boot}
    if tag == "FAULT":
        return {"kind": cfg.get("fault_kind", "CRASH")}
    raise ValueError(f"unknown tag: {tag}")


def next_event(state, owner: str, tag: str, cfg: dict | None = None) -> dict:
    return {"tag": tag, "seq": state.sequence + 1, "owner": owner, "head": state.head,
            **event_fields(tag, cfg or {})}


def drive(inputs, checkpoint, tags=HAPPY_TAGS, cfg=None, owner: str | None = None):
    """Admit then step through ``tags`` in order, chaining seq/head from the
    actual prior transition. Stops (without raising) at the first
    non-accepted transition or admission refusal. Returns
    ``(final_state, [Transition, ...])``; the transitions list is empty if
    admission itself refused."""

    cfg = cfg or {}
    owner = owner if owner is not None else checkpoint.owner
    state = admit_synthetic(inputs, checkpoint)
    transitions = []
    if state.phase != "ADMITTED":
        return state, transitions
    for tag in tags:
        event = next_event(state, owner, tag, cfg)
        transition = step(state, event)
        transitions.append(transition)
        state = transition.state
        if not transition.accepted:
            break
    return state, transitions


def drive_happy_path(inputs=None, checkpoint=None, cfg=None):
    inputs = inputs if inputs is not None else good_inputs()
    checkpoint = checkpoint if checkpoint is not None else genesis_checkpoint()
    return drive(inputs, checkpoint, HAPPY_TAGS, cfg)
