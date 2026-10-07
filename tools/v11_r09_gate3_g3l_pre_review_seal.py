"""Seal already-retained, independently-reviewed G3-L material into the
PRE_REVIEW evidence schema `check_inventory` understands.

This grants no qualification and no launch credit. It only repackages
identities the unmodified identity audit (`v11_r09_gate3_g3l_identity_audit`)
already classifies as RETAINED_REVIEWED_LOCAL_SCOPE or
DETERMINISTIC_OFFLINE_RECONCILIATION -- scope that already passed an
independent review (027fd7a) or is an exact recovered PASS terminal -- into
`check_inventory`'s `ref`/`review_ref` object-store schema, with real,
hash-verified byte copies placed under a private object root. A sealed slot
no longer appearing in `check_inventory`'s findings is a PRE_REVIEW packaging
fact only: the identity audit's own `qualification_credit` stays 0, and the
disposition text for every sealed identity still says independent
package-entry review remains pending. This module never touches a
FUTURE/INVALID identity, never touches a run- or window-scoped identity (no
selected acquisition window exists yet to bind to), and performs no network
access and no write outside the caller-supplied object root.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from tools.v11_r09_gate3_g3l_identity_audit import OFFLINE, RETAINED_SCOPED, _evidence_bytes, audit
from tools.v11_r09_gate3_g3l_prep import (
    ALL_IDS, PRE_REVIEW_IDS, RUN_SPECIFIC, SCHEMA, WINDOW_SPECIFIC, check_inventory,
)

# For each identity: the two already-verified `source_refs` paths (from the
# unmodified identity audit's own output) that become `ref` and `review_ref`.
# Chosen so every pair is itself a real, checkable link -- a JSON field in one
# file whose value is the other file's sha256, or (where the reconciliation's
# own already-PASS_IN_SCOPE-reviewed scoping draws a document and its review
# together as one identity's sole evidence) the two files that scope names.
# Never includes a run- or window-scoped identity: no selected acquisition
# window exists yet, so nothing here may claim window/run freshness.
SEAL_MAP: dict[str, tuple[str, str]] = {
    # terminal["review_report_sha256"] == the review document's own sha256.
    "code.mapping_exact_commit_review": (
        "docs/V11_R09_GATE3_V4_PROVIDER_MAPPING_REPAIR_REVIEW_23c11e0.md",
        "docs/V11_R09_GATE3_V4_PROVIDER_MAPPING_REPAIR_REVIEW_23c11e0_terminal.json",
    ),
    # terminal["artifacts_sha256"] records the review document's own sha256.
    "protocol.g3i_composition_review_terminal": (
        "docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d.md",
        "docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d_terminal.json",
    ),
    # This identity's own reconciliation scope is exactly {document, review};
    # no terminal is in its scope (the terminal belongs to the sibling
    # review_terminal identity below), so the reviewed document and its
    # review report are this identity's only two retained artifacts.
    "protocol.g3p_addendum_commit_tree_document": (
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_ADJUDICATION.md",
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413.md",
    ),
    # terminal["report_sha256"] == the review document's own sha256.
    "protocol.g3p_addendum_review_terminal": (
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413.md",
        "docs/V11_R09_GATE3_LAUNCH_CONTRACT_REVIEW_14c2413_terminal.json",
    ),
    # This identity's own reconciliation scope is exactly {document, review};
    # no terminal is in its scope (recovered separately for the sibling
    # g3p_original_review_terminal identity below). The review document's own
    # prose names this exact document ("Reviewed document:
    # docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md").
    "protocol.g3p_original_commit_tree_document": (
        "docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md",
        "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md",
    ),
    # terminal["report_sha256"] == the review document's own sha256; the
    # identity audit separately re-verifies exit==0, marker, and error==None.
    "protocol.g3p_original_review_terminal": (
        "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a.md",
        "docs/V11_R09_GATE3_PROTOCOL_REVIEW_117830a_terminal.json",
    ),
    # terminal["reviewed_document_sha256"] == the design document's own
    # sha256 directly (the strongest binding of any row here).
    "protocol.transport_design_review_terminal": (
        "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md",
        "docs/V11_R09_GATE3_TRANSPORT_RUNTIME_REVIEW_7e132a0_terminal.json",
    ),
}

_MEDIA_TYPES = {".md": "text/markdown", ".json": "application/json"}


def _media_type(path: str) -> str:
    suffix = Path(path).suffix
    if suffix not in _MEDIA_TYPES:
        raise ValueError(f"unsupported sealed media type: {path}")
    return _MEDIA_TYPES[suffix]


def _verified(ref: dict) -> bool:
    """True only if the identity audit itself already cryptographically
    confirmed these exact current bytes against a named historical baseline
    -- either a reviewed-bytes comparison (`current_matches_reviewed_bytes`)
    or a named-commit Git blob comparison (`matches_named_git_bytes`). Absent
    both fields, the ref is unverified and must never be sealed."""
    return ref.get("current_matches_reviewed_bytes") is True or ref.get("matches_named_git_bytes") is True


def _seal_object(object_root: Path, data: bytes) -> dict:
    """Create or verify `object_root/<sha256>` without ever following a
    symlink and without an exists-then-write race: creation uses
    O_CREAT|O_EXCL|O_NOFOLLOW, which atomically fails if that path already
    names anything at all (file, symlink, dangling or not), so an attacker
    who pre-plants a symlink there can never cause a write through it. If
    the path already exists, it is reopened with O_NOFOLLOW and confirmed to
    be a regular file via the open file descriptor before its bytes are
    trusted -- never via a separate, racy stat/exists call on the path."""
    sha256 = hashlib.sha256(data).hexdigest()
    object_root.mkdir(parents=True, exist_ok=True)
    destination = object_root / sha256
    create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(destination, create_flags, 0o644)
    except FileExistsError:
        fd = None
    if fd is not None:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        return sha256
    try:
        existing_fd = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError(f"sealed object path is not a safe regular file: {sha256}") from exc
    try:
        if not stat.S_ISREG(os.fstat(existing_fd).st_mode):
            raise ValueError(f"sealed object path is not a regular file: {sha256}")
        chunks = []
        while chunk := os.read(existing_fd, 1024 * 1024):
            chunks.append(chunk)
        existing = b"".join(chunks)
    finally:
        os.close(existing_fd)
    if existing != data:
        raise ValueError(f"object root digest collision: {sha256}")
    return sha256


def sealed_evidence(repo: Path, *, target_date: str, now_utc: int,
                    free_disk_bytes: int, available_memory_bytes: int,
                    object_root: Path, seal_map: dict[str, tuple[str, str]] = SEAL_MAP) -> dict:
    """Return (evidence, sealed, skipped). Never raises for ordinary category
    drift (a once-retained identity whose current bytes no longer match its
    reviewed baseline is skipped, not sealed) -- only for a `seal_map` entry
    that cannot be resolved against the audit's own verified output at all,
    which means the map itself, not the live repo, is wrong.
    """
    repo = repo.resolve()
    object_root = object_root.resolve()
    result = audit(repo, target_date=target_date, now_utc=now_utc,
                  free_disk_bytes=free_disk_bytes, available_memory_bytes=available_memory_bytes)
    evidence = {identity: None for identity in ALL_IDS}
    sealed: list[str] = []
    skipped: list[dict] = []
    cache: dict[str, bytes] = {}
    for identity, (ref_path, review_path) in seal_map.items():
        if identity not in ALL_IDS:
            raise ValueError(f"seal_map references unknown identity: {identity}")
        if identity in RUN_SPECIFIC or identity in WINDOW_SPECIFIC:
            raise ValueError(f"refusing to seal a run/window-scoped identity: {identity}")
        if ref_path == review_path:
            raise ValueError(f"seal_map ref and review_ref must differ: {identity}")
        row = result["identities"][identity]
        if row["category"] not in (RETAINED_SCOPED, OFFLINE):
            skipped.append({"id": identity, "reason": f"category is {row['category']}, not retained"})
            continue
        refs_by_path: dict[str, dict] = {}
        for ref in row["source_refs"]:
            existing = refs_by_path.get(ref["path"])
            if existing is None or (not _verified(existing) and _verified(ref)):
                refs_by_path[ref["path"]] = ref
        if ref_path not in refs_by_path or review_path not in refs_by_path:
            raise ValueError(f"seal_map path not in this identity's own verified source_refs: {identity}")
        ref_entry, review_entry = refs_by_path[ref_path], refs_by_path[review_path]
        if not _verified(ref_entry) or not _verified(review_entry):
            skipped.append({"id": identity, "reason": "current bytes have drifted from the reviewed baseline"})
            continue
        if ref_entry["sha256"] == review_entry["sha256"]:
            raise ValueError(f"ref and review_ref resolved to identical bytes: {identity}")
        ref_data = _evidence_bytes(repo, ref_path, cache)
        review_data = _evidence_bytes(repo, review_path, cache)
        if (hashlib.sha256(ref_data).hexdigest() != ref_entry["sha256"]
                or hashlib.sha256(review_data).hexdigest() != review_entry["sha256"]):
            raise ValueError(f"live bytes no longer match the audit's own verified hashes: {identity}")
        ref_sha = _seal_object(object_root, ref_data)
        review_sha = _seal_object(object_root, review_data)
        evidence[identity] = {
            "ref": {"sha256": ref_sha, "byte_length": len(ref_data),
                    "media_type": _media_type(ref_path), "path": ref_sha},
            "review_ref": {"sha256": review_sha, "byte_length": len(review_data),
                          "media_type": _media_type(review_path), "path": review_sha},
            "observed_utc": now_utc,
            "scope": f"retained_reviewed_local_scope:{identity}",
        }
        sealed.append(identity)
    return evidence, sealed, skipped


def build_pre_review_inventory(repo: Path, *, target_date: str, now_utc: int,
                               free_disk_bytes: int, available_memory_bytes: int,
                               object_root: Path) -> dict:
    evidence, sealed, skipped = sealed_evidence(
        repo, target_date=target_date, now_utc=now_utc, free_disk_bytes=free_disk_bytes,
        available_memory_bytes=available_memory_bytes, object_root=object_root)
    inventory = {"schema": SCHEMA, "launchable": False, "target_date": target_date,
                "evidence": evidence}
    findings = check_inventory(inventory, target_date=target_date, now_utc=now_utc,
                               stage="PRE_REVIEW", object_root=object_root)
    missing_after = {f["id"] for f in findings if f["state"] == "MISSING"}
    sealed_set = set(sealed)
    consumer_states = {f["id"]: f["state"] for f in findings if f["id"] in sealed_set}
    if consumer_states:
        bad = ", ".join(f"{item_id}={state}" for item_id, state in sorted(consumer_states.items()))
        raise ValueError(f"a sealed identity is reported by the consumer as not clean: {bad}")
    return {
        "schema": "R09_GATE3_G3L_PRE_REVIEW_SEAL_V1", "launchable": False,
        "qualification_credit": 0,
        "sealed_identity_count": len(sealed), "sealed_identities": sorted(sealed),
        "skipped_identities": skipped,
        "missing_before": len(PRE_REVIEW_IDS),
        "missing_after": len(missing_after),
        "findings": findings,
        "honesty_note": ("Sealing a slot here is a PRE_REVIEW packaging fact only; it is "
                         "not launch permission, not G3-L qualification, and the sealed "
                         "identity still requires its own independent package-entry review "
                         "before any later stage may trust it."),
    }
