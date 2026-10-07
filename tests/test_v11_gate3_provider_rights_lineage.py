"""Offline adversarial tests for the Gate 3 provider-rights/restriction lineage.

No test creates a socket, imports an HTTP/provider client, or performs DNS;
a guard fixture makes any attempt fail loudly. The committed lineage is real
retained evidence and must refuse every real request. Positive-path cases use
a clearly synthetic derivative (``synthetic://`` refs, invented permission)
that confers no real permission, right or G3-L credit.
"""

import ast
import copy
import hashlib
import json
import os
import signal
import socket
from pathlib import Path

import pytest

from tools import v11_gate3_provider_rights_lineage as lineage_mod
from tools.v11_gate3_provider_rights_lineage import (
    ARTIFACT, ENVELOPE_OK, IN_SCOPE_IDS, NOAA_S3, NOMADS, ECMWF_PORTAL, ECMWF_CDN,
    OBSERVED, PINNED_SOURCES, RECOVERED_DIR, REFUSED, REQUIRED_EVENT_IDS,
    LineageError, canonical_bytes, check_lineage, evaluate_request, load_sources,
    strict_loads, verify_recovered_bodies,
)
from tools.v11_r09_gate3_g3l_prep import INDEX_CAP, REQUIRED, RUN_SPECIFIC

ROOT = Path(__file__).resolve().parents[1]
P1_PATH = "/gefs.20261008/00/atmos/pgrb2ap5/gec00.t00z.pgrb2a.0p50.f024.idx"
NOW = "2026-10-08T10:00:00Z"
PLANNED = "2026-10-08T10:05:00Z"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_a, **_k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def committed() -> dict:
    return strict_loads((ROOT / ARTIFACT).read_bytes())


def _ref(tag: str) -> dict:
    return {"sha256": hashlib.sha256(tag.encode()).hexdigest(), "byte_length": 64,
            "path": f"synthetic://offline-fixture/{tag}"}


def synthetic_resumed() -> dict:
    """Synthetic derivative: every hold adjudicated, GEFS permission invented."""
    doc = committed()
    for name, domain in doc["control_domains"].items():
        domain["status"] = "RESUMED_BY_REVIEW"
        domain["resumption_review"] = _ref(f"resumption-{name}")
    for event in doc["restriction_events"]:
        event["expiry_adjudication"] = _ref("expiry-" + event["event_id"])
    doc["permissions"]["GEFS"]["reviewed_permissions"] = [{
        "permission_id": "synthetic-gefs-permission",
        "origins": [NOAA_S3], "purposes": ["INDEX"],
        "reviewed_at_utc": "2026-10-08T00:00:00Z", "valid_until_utc": "2026-10-09T00:00:00Z",
        "licence_document": _ref("licence"), "review_ref": _ref("licence-review"),
        "basis": "LICENCE_AND_ACCESS_TERMS_INDEPENDENT_REVIEW",
    }]
    doc["permissions"]["GEFS"]["status"] = "REVIEWED_PERMISSION_RECORDED"
    doc["request_envelope"]["reviewed_origin_path_specs"] = [{
        "origin": NOAA_S3, "provider": "GEFS", "purpose": "INDEX",
        "path_regex": r"/gefs\.20261008/00/atmos/pgrb2ap5/gec00\.t00z\.pgrb2a\.0p50\.f024\.idx",
        "permission_id": "synthetic-gefs-permission", "review_ref": _ref("spec-review"),
    }]
    assert check_lineage(doc) == []
    return doc


def good_request(**changes) -> dict:
    request = {
        "request_id": "synthetic-p1-index", "provider": "GEFS", "purpose": "INDEX", "method": "GET",
        "url": NOAA_S3 + P1_PATH,
        "headers": {"Accept": "text/plain", "Accept-Encoding": "identity", "Connection": "close",
                    "Host": "noaa-gefs-pds.s3.amazonaws.com", "User-Agent": "AlphaV11-EvidencePreflight/1"},
        "range": None, "body_bytes": 0, "max_response_bytes": 3_145_728, "deadline_seconds": 30,
        "follow_redirects": False, "max_retries": 0, "attempt_index": 0, "trust_env": False,
        "proxy": None, "netrc": False, "cookies": {}, "client_certificate": None,
        "credential_source": None, "concurrency": 1, "planned_at_utc": PLANNED,
        "permission_id": "synthetic-gefs-permission", "failover_from_origin": None,
    }
    request.update(changes)
    return request


LEDGER = {"attempts": 0, "body_bytes": 0, "request_ids": []}


def run(request, doc=None, now=NOW, ledger=LEDGER):
    return evaluate_request(request, doc if doc is not None else synthetic_resumed(),
                            now_utc=now, ledger=copy.deepcopy(ledger))


def refused_with(result, code):
    assert result["outcome"] == REFUSED and result["execution_authority"] is False, result
    assert code in result["reasons"], result


# -- Committed artifact and retained custody --------------------------------

def test_committed_artifact_is_canonical_and_consistent():
    raw = (ROOT / ARTIFACT).read_bytes()
    doc = strict_loads(raw)
    assert canonical_bytes(doc) == raw
    assert check_lineage(doc) == []
    assert verify_recovered_bodies(doc) == []
    assert doc["g3l"] == "NO_GO" and doc["qualification_credit"] == 0
    assert doc["execution_authority"] is False and doc["provider_requests_by_this_package"] == 0


def test_recovered_denial_bodies_match_retained_digests():
    names = {p.name for p in (ROOT / RECOVERED_DIR).iterdir()}
    for path in (ROOT / RECOVERED_DIR).iterdir():
        assert hashlib.sha256(path.read_bytes()).hexdigest() + ".body" == path.name
    pinned = {"7c21325b9a8c5d3b7f06bed411ae11e6fa6dcb490320671bd8e1d49a64956a28",
              "0986be0818f5c4e80bddac64bcd37d8f77da4c43acb380aaa7e4bcb677a51460",
              "3850dfdbf4489250268b5f0740240a9f4445e7c5c29e1d03aa0c5446808d7507"}
    assert {p + ".body" for p in pinned} <= names
    first = (ROOT / RECOVERED_DIR / ("7c21325b9a8c5d3b7f06bed411ae11e6fa6dcb490320671bd8e1d49a64956a28.body")).read_bytes()
    assert b"<Code>SlowDown</Code>" in first and b"F69ZYP2JCF0639HX" in first
    assert (ROOT / RECOVERED_DIR / "3850dfdbf4489250268b5f0740240a9f4445e7c5c29e1d03aa0c5446808d7507.body").read_bytes() == b"Too Many Requests"


def test_changed_recovered_body_is_detected(tmp_path):
    doc = committed()
    (tmp_path / RECOVERED_DIR).mkdir(parents=True)
    for path in (ROOT / RECOVERED_DIR).iterdir():
        (tmp_path / RECOVERED_DIR / path.name).write_bytes(path.read_bytes())
    victim = next((tmp_path / RECOVERED_DIR).iterdir())
    victim.write_bytes(victim.read_bytes()[:-1] + b"X")
    assert any(p.startswith("RECOVERED_BODY_CHANGED") for p in verify_recovered_bodies(doc, tmp_path))


def _private_sources_present() -> bool:
    return all(Path(p).exists() for _i, p, b, *_ in PINNED_SOURCES
               if p.startswith("/") and b != "RECORD_DIGEST_ONLY")


@pytest.mark.skipif(not _private_sources_present(), reason="retained private sources not on this host")
def test_rebuild_from_retained_sources_is_byte_identical():
    doc, bodies = lineage_mod.build_lineage(load_sources())
    assert canonical_bytes(doc) == (ROOT / ARTIFACT).read_bytes()
    assert all(hashlib.sha256(b).hexdigest() == s for s, b in bodies.items())


def test_changed_or_truncated_pinned_source_refuses_build():
    real = {s[0]: s for s in PINNED_SOURCES}

    def reader_changing(target):
        def reader(source_id, path):
            raw = (ROOT / path).read_bytes() if not path.startswith("/") else b""
            if source_id == target:
                return raw + b" "
            if path.startswith("/"):
                raise LineageError("SOURCE_UNAVAILABLE:test")
            return raw
        return reader

    with pytest.raises(LineageError) as changed:
        load_sources(reader_changing("ecmwf_public_export"))
    assert str(changed.value) == "SOURCE_CHANGED:ecmwf_public_export"
    seen = []

    def short_reader(source_id, path):
        seen.append(source_id)
        if real[source_id][2] == "APPEND_ONLY_PREFIX":
            return b"x" * (real[source_id][3] - 1)
        raw = (ROOT / path).read_bytes() if not path.startswith("/") else None
        if raw is None:
            # Return exactly-pinned-length bytes with the wrong content.
            return b"\0" * real[source_id][3]
        return raw

    with pytest.raises(LineageError) as short:
        load_sources(short_reader)
    assert str(short.value).startswith(("SOURCE_CHANGED:", "SOURCE_TRUNCATED:"))
    assert "ecmwf_raw_aws_retry_json" not in seen and "ecmwf_raw_capture_v1" not in seen


# -- Real evidence refuses every real request --------------------------------

@pytest.mark.parametrize("url,provider,purpose", [
    (NOAA_S3 + P1_PATH, "GEFS", "INDEX"),
    (NOMADS + "/cgi-bin/filter_gefs_atmos_0p50a.pl", "GEFS", "INDEX"),
    (ECMWF_PORTAL + "/forecasts/20261008/00z/ifs/0p25/enfo/20261008000000-24h-enfo-ef.index", "IFS", "INDEX"),
    (ECMWF_CDN + "/20261008/00z/aifs-ens/0p25/enfo/20261008000000-24h-enfo-cf.index", "AIFS", "INDEX"),
])
def test_real_lineage_refuses_every_real_request(url, provider, purpose):
    result = run(good_request(url=url, provider=provider, purpose=purpose, permission_id=None,
                              headers={"Accept-Encoding": "identity"}), doc=committed())
    for code in ("CONTROL_DOMAIN_HELD", "UNRESOLVED_RESTRICTION_HISTORY",
                 "PRIOR_RATE_LIMIT_OR_UNKNOWN_DENIAL_UNADJUDICATED",
                 "ORIGIN_PATH_PURPOSE_NOT_REVIEWED", "NO_REVIEWED_PERMISSION"):
        refused_with(result, code)


def test_synthetic_positive_path_is_never_permission():
    result = run(good_request())
    assert result == {"outcome": ENVELOPE_OK, "reasons": [], "execution_authority": False}


def test_every_retained_success_is_observation_only():
    doc = committed()
    assert doc["contacts"] and all(c["classification"] == OBSERVED for c in doc["contacts"])
    for provider, perm in doc["permissions"].items():
        assert perm["status"] == "NO_REVIEWED_PERMISSION" and perm["reviewed_permissions"] == []
        assert perm["licence_document_bytes"] is None and perm["observed_public_anonymous_access"]
    assert all(d["status"] == "HELD" for d in doc["control_domains"].values())


# -- Permission is never inferred from success -------------------------------

def test_contact_promoted_to_permission_is_rejected():
    doc = committed()
    doc["contacts"][0]["classification"] = "REVIEWED_PERMISSION"
    assert any(f.startswith("CONTACT_PROMOTED_TO_PERMISSION") for f in check_lineage(doc))


def test_later_success_cannot_clear_hold():
    doc = committed()
    doc["control_domains"]["NOAA_GEFS"]["later_successes_clear_hold"] = True
    assert "PERMISSION_INFERRED_FROM_SUCCESS:NOAA_GEFS" in check_lineage(doc)


@pytest.mark.parametrize("domain", ["ECMWF", "NOAA_GEFS"])
def test_resumption_without_reviewed_expiry_is_rejected(domain):
    doc = committed()
    doc["control_domains"][domain]["status"] = "RESUMED_BY_REVIEW"
    assert f"RESUMPTION_WITHOUT_REVIEWED_EXPIRY:{domain}" in check_lineage(doc)
    doc["control_domains"][domain]["resumption_review"] = _ref("resume")
    assert f"RESUMPTION_WITHOUT_REVIEWED_EXPIRY:{domain}" in check_lineage(doc)


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(basis="OBSERVED_PUBLIC_ACCESS"),
    lambda p: p.update(review_ref=dict(p["licence_document"])),
    lambda p: p.update(licence_document=None),
])
def test_permission_without_reviewed_licence_is_rejected(mutate):
    doc = synthetic_resumed()
    mutate(doc["permissions"]["GEFS"]["reviewed_permissions"][0])
    assert "PERMISSION_WITHOUT_REVIEWED_LICENCE:GEFS" in check_lineage(doc)
    assert run(good_request(), doc=doc)["reasons"][0] == "LINEAGE_INVALID"


def test_licence_identity_cannot_be_offline_without_reviewed_permission():
    doc = committed()
    item = doc["identity_assessment"]["sources.gefs_licence_anonymous_access"]
    item.update(disposition="OFFLINE_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW",
                closable_offline_on_independent_review=True)
    assert "PERMISSION_CLAIMED_WITHOUT_REVIEW:sources.gefs_licence_anonymous_access" in check_lineage(doc)


# -- Stale permission ---------------------------------------------------------

@pytest.mark.parametrize("now", ["2026-10-09T00:00:00Z", "2026-10-07T23:59:59Z", "2027-01-01T00:00:00Z"])
def test_stale_or_future_permission_refuses(now):
    refused_with(run(good_request(planned_at_utc="2027-02-01T00:00:00Z"), now=now), "STALE_OR_FUTURE_PERMISSION")


def test_restriction_after_permission_review_supersedes_it():
    doc = synthetic_resumed()
    event = next(e for e in doc["restriction_events"] if e["control_domain"] == "NOAA_GEFS")
    late = copy.deepcopy(event)
    late.update(event_id="synthetic-late-503", received_at_utc="2026-10-08T06:00:00Z", status=503)
    doc["restriction_events"].append(late)
    doc["control_domains"]["NOAA_GEFS"]["hold_basis"].append("synthetic-late-503")
    assert check_lineage(doc) == []
    refused_with(run(good_request(), doc=doc), "PERMISSION_SUPERSEDED_BY_RESTRICTION")


def test_untimed_restriction_supersedes_permission():
    doc = synthetic_resumed()
    event = next(e for e in doc["restriction_events"] if e["control_domain"] == "NOAA_GEFS")
    event = copy.deepcopy(event)
    event.update(event_id="synthetic-untimed", received_at_utc=None, time_basis="UNRETAINED_SYNTHETIC")
    doc["restriction_events"].append(event)
    doc["control_domains"]["NOAA_GEFS"]["hold_basis"].append("synthetic-untimed")
    assert check_lineage(doc) == []
    refused_with(run(good_request(), doc=doc), "PERMISSION_SUPERSEDED_BY_RESTRICTION")


def test_invalid_now_refuses():
    refused_with(run(good_request(), now="2026-10-08 10:00:00"), "NOW_UTC_INVALID")


# -- Changed origin/path ------------------------------------------------------

@pytest.mark.parametrize("url,code", [
    (NOMADS + P1_PATH, "ORIGIN_PATH_PURPOSE_NOT_REVIEWED"),
    (NOAA_S3 + P1_PATH.replace("f024", "f030"), "ORIGIN_PATH_PURPOSE_NOT_REVIEWED"),
    (NOAA_S3 + P1_PATH.replace("20261008", "20261009"), "ORIGIN_PATH_PURPOSE_NOT_REVIEWED"),
    (NOAA_S3 + "/gefs.20261008/00/atmos/../atmos/pgrb2ap5/gec00.t00z.pgrb2a.0p50.f024.idx", "PATH_NOT_CANONICAL"),
    (NOAA_S3 + "/gefs.20261008//00/atmos/pgrb2ap5/gec00.t00z.pgrb2a.0p50.f024.idx", "PATH_NOT_CANONICAL"),
    (NOAA_S3 + P1_PATH.replace(".idx", "%2Eidx"), "PATH_NOT_CANONICAL"),
    ("http://noaa-gefs-pds.s3.amazonaws.com" + P1_PATH, "URL_NOT_PUBLIC_HTTPS_ORIGIN"),
    ("https://noaa-gefs-pds.s3.amazonaws.com:8443" + P1_PATH, "URL_NOT_PUBLIC_HTTPS_ORIGIN"),
    ("https://user:pw@noaa-gefs-pds.s3.amazonaws.com" + P1_PATH, "URL_NOT_PUBLIC_HTTPS_ORIGIN"),
    ("https://NOAA-GEFS-PDS.s3.amazonaws.com" + P1_PATH, "URL_NOT_PUBLIC_HTTPS_ORIGIN"),
    ("https://noaa-gefs-pds.s3.amazonaws.com:443" + P1_PATH, "URL_NOT_PUBLIC_HTTPS_ORIGIN"),
    ("https://noaa-gefs-pds.s3.us-east-1.amazonaws.com" + P1_PATH, "UNKNOWN_CONTROL_DOMAIN"),
    (NOAA_S3 + P1_PATH + "#frag", "URL_FRAGMENT"),
    (NOAA_S3 + P1_PATH + "?x=1", "URL_QUERY_FORBIDDEN"),
])
def test_changed_origin_or_path_refuses(url, code):
    refused_with(run(good_request(url=url)), code)


def test_failover_to_another_origin_refuses():
    refused_with(run(good_request(failover_from_origin=NOMADS)), "FAILOVER_FORBIDDEN")


def test_provider_origin_mismatch_refuses():
    refused_with(run(good_request(provider="IFS")), "PROVIDER_ORIGIN_DOMAIN_MISMATCH")


@pytest.mark.parametrize("origin", [NOAA_S3, NOMADS])
def test_narrowing_noaa_domain_to_escape_hold_is_rejected(origin):
    doc = committed()
    doc["control_domains"]["NOAA_GEFS"]["origins"].remove(origin)
    assert "DOMAIN_SCOPE_NARROWED:NOAA_GEFS" in check_lineage(doc)


def test_origin_cannot_belong_to_two_domains():
    doc = committed()
    doc["control_domains"]["ECMWF"]["origins"].append(NOAA_S3)
    assert f"ORIGIN_IN_TWO_DOMAINS:{NOAA_S3}" in check_lineage(doc)


# -- Missing restriction history, 429/503 history -----------------------------

@pytest.mark.parametrize("event_id", sorted(REQUIRED_EVENT_IDS))
def test_dropping_any_retained_restriction_is_rejected(event_id):
    doc = committed()
    doc["restriction_events"] = [e for e in doc["restriction_events"] if e["event_id"] != event_id]
    findings = check_lineage(doc)
    assert f"MISSING_RETAINED_RESTRICTION:{event_id}" in findings
    assert any(f.startswith("HOLD_BASIS_INCOMPLETE") for f in findings)


@pytest.mark.parametrize("mutate", [
    lambda e: e["headers_retained"].update({"retry-after": "0"}),
    lambda e: e["retained_record"]["response"].update(status=200),
    lambda e: e["retained_record"]["response"]["headers"].update({"retry-after": "0"}),
    lambda e: e.update(retained_record=None),
])
def test_tampering_with_pinned_denial_is_rejected(mutate):
    doc = committed()
    event = next(e for e in doc["restriction_events"] if e["event_id"] == "ecmwf-s3-503-20260930T074813Z")
    mutate(event)
    assert any(f.startswith("TAMPERED_RETAINED_DENIAL") for f in check_lineage(doc))


def test_hold_must_carry_forward():
    doc = committed()
    doc["restriction_events"][0]["carry_forward"] = "EXPIRED_BY_ELAPSED_TIME"
    assert any(f.startswith("EVENT_NOT_CARRIED_FORWARD") for f in check_lineage(doc))


def test_invented_retry_after_is_rejected():
    doc = committed()
    doc["restriction_events"][0]["retry_not_before_utc"] = "2026-09-30T08:00:00Z"
    assert any(f.startswith("INVENTED_RETRY_AFTER") for f in check_lineage(doc))
    doc["restriction_events"][0]["retry_after"] = "86400"
    assert any(f.startswith("HISTORICAL_RETRY_FACT_CHANGED") for f in check_lineage(doc))


@pytest.mark.parametrize("status", [429, 503, 302, None])
def test_new_unadjudicated_429_503_event_reopens_hold(status):
    doc = synthetic_resumed()
    event = copy.deepcopy(next(e for e in doc["restriction_events"] if e["control_domain"] == "NOAA_GEFS"))
    event.update(event_id="synthetic-new-denial", status=status, expiry_adjudication=None,
                 received_at_utc="2026-10-07T00:00:00Z")
    doc["restriction_events"].append(event)
    doc["control_domains"]["NOAA_GEFS"]["hold_basis"].append("synthetic-new-denial")
    assert "RESUMPTION_WITHOUT_REVIEWED_EXPIRY:NOAA_GEFS" in check_lineage(doc)
    refused_with(run(good_request(), doc=doc), "LINEAGE_INVALID")
    doc["control_domains"]["NOAA_GEFS"].update(status="HELD")
    assert check_lineage(doc) == []
    result = run(good_request(), doc=doc)
    for code in ("CONTROL_DOMAIN_HELD", "UNRESOLVED_RESTRICTION_HISTORY",
                 "PRIOR_RATE_LIMIT_OR_UNKNOWN_DENIAL_UNADJUDICATED"):
        refused_with(result, code)


def test_retry_after_is_carried_forward_even_after_resumption():
    doc = synthetic_resumed()
    event = copy.deepcopy(next(e for e in doc["restriction_events"] if e["control_domain"] == "NOAA_GEFS"))
    event.update(event_id="synthetic-retry-after", retry_after="86400",
                 retry_not_before_utc="2026-10-08T12:00:00Z")
    doc["restriction_events"].append(event)
    doc["control_domains"]["NOAA_GEFS"]["hold_basis"].append("synthetic-retry-after")
    assert check_lineage(doc) == []
    refused_with(run(good_request(), doc=doc), "RETRY_AFTER_CARRIED_FORWARD")
    assert run(good_request(planned_at_utc="2026-10-08T12:30:00Z"), doc=doc,
               now="2026-10-08T12:00:01Z")["outcome"] == ENVELOPE_OK


# -- Credential introduction / bypass ----------------------------------------

@pytest.mark.parametrize("header", [
    "Authorization", "AUTHORIZATION", "Cookie", "Proxy-Authorization", "X-Api-Key",
    "X-Amz-Security-Token", "x-amz-date", "x-amz-content-sha256",
])
def test_credential_header_refuses(header):
    headers = dict(good_request()["headers"], **{header: "synthetic"})
    refused_with(run(good_request(headers=headers)), "CREDENTIAL_HEADER")


@pytest.mark.parametrize("query", ["X-Amz-Signature=abc", "X-Amz-Credential=a%2Fb", "AWSAccessKeyId=x", "token=t"])
def test_signed_url_refuses(query):
    refused_with(run(good_request(url=NOAA_S3 + P1_PATH + "?" + query)), "SIGNED_OR_CREDENTIAL_URL")


@pytest.mark.parametrize("key,value,code", [
    ("trust_env", True, "AMBIENT_ENVIRONMENT_TRUSTED"),
    ("trust_env", None, "AMBIENT_ENVIRONMENT_TRUSTED"),
    ("proxy", "socks5h://127.0.0.1:1080", "PROXY_CONFIGURED"),
    ("netrc", True, "NETRC_ENABLED"),
    ("cookies", {"session": "x"}, "COOKIES_PRESENT"),
    ("cookies", None, "COOKIES_PRESENT"),
    ("client_certificate", "/synthetic/cert.pem", "CLIENT_CERTIFICATE_PRESENT"),
    ("credential_source", "aws-default-profile", "CREDENTIAL_SOURCE_PRESENT"),
    ("follow_redirects", True, "REDIRECTS_ENABLED"),
    ("max_retries", 1, "RETRIES_ENABLED"),
    ("max_retries", False, "RETRIES_ENABLED"),
    ("attempt_index", 1, "RETRY_ATTEMPT"),
    ("concurrency", 6, "CONCURRENCY_NOT_SINGLE"),
    ("concurrency", True, "CONCURRENCY_NOT_SINGLE"),
    ("method", "HEAD", "METHOD_NOT_GET"),
    ("method", "POST", "METHOD_NOT_GET"),
])
def test_bypass_vector_refuses(key, value, code):
    refused_with(run(good_request(**{key: value})), code)


def test_unreviewed_header_and_host_mismatch_refuse():
    headers = dict(good_request()["headers"], **{"X-Forwarded-For": "1.2.3.4"})
    refused_with(run(good_request(headers=headers)), "HEADER_NOT_REVIEWED")
    headers = dict(good_request()["headers"], Host="nomads.ncep.noaa.gov")
    refused_with(run(good_request(headers=headers)), "HOST_HEADER_MISMATCH")
    headers = dict(good_request()["headers"], **{"accept-encoding": "identity"})
    refused_with(run(good_request(headers=headers)), "HEADER_DUPLICATE")
    headers = dict(good_request()["headers"], **{"Accept-Encoding": "gzip"})
    refused_with(run(good_request(headers=headers)), "ACCEPT_ENCODING_NOT_IDENTITY")


# -- Oversized request/body/time limits -------------------------------------

@pytest.mark.parametrize("changes,code", [
    ({"max_response_bytes": INDEX_CAP + 1}, "RESPONSE_BYTES_OVER_LIMIT"),
    ({"max_response_bytes": 0}, "RESPONSE_BYTES_OVER_LIMIT"),
    ({"body_bytes": 1}, "REQUEST_BODY_FORBIDDEN"),
    ({"deadline_seconds": 31}, "DEADLINE_OVER_LIMIT"),
    ({"deadline_seconds": float("nan")}, "DEADLINE_OVER_LIMIT"),
    ({"deadline_seconds": float("inf")}, "DEADLINE_OVER_LIMIT"),
    ({"deadline_seconds": 0}, "DEADLINE_OVER_LIMIT"),
    ({"range": "bytes=0-99"}, "INDEX_RANGE_FORBIDDEN"),
    ({"planned_at_utc": "2026-10-08T09:00:00Z"}, "PLANNED_TIME_INVALID_OR_PAST"),
])
def test_size_time_limits_refuse(changes, code):
    request = good_request(**changes)
    if request["range"] is not None:
        request["headers"] = dict(request["headers"], Range=request["range"])
    refused_with(run(request), code)


def _field_doc():
    doc = synthetic_resumed()
    perm = doc["permissions"]["GEFS"]["reviewed_permissions"][0]
    perm["purposes"] = ["FIELD"]
    doc["request_envelope"]["reviewed_origin_path_specs"][0].update(
        purpose="FIELD", path_regex=r"/gefs\.20261008/00/atmos/pgrb2ap5/gec00\.t00z\.pgrb2a\.0p50\.f024")
    assert check_lineage(doc) == []
    return doc


def _field_request(rng, size):
    request = good_request(purpose="FIELD", url=NOAA_S3 + P1_PATH[:-4], range=rng, max_response_bytes=size)
    request["headers"] = dict(request["headers"], Range=rng) if rng is not None else request["headers"]
    return request


def test_field_range_positive_and_oversized():
    doc = _field_doc()
    assert run(_field_request("bytes=100-1099", 1000), doc=doc)["outcome"] == ENVELOPE_OK
    limit = 2 * 1024 ** 2
    refused_with(run(_field_request(f"bytes=0-{limit}", limit + 1), doc=doc), "RESPONSE_BYTES_OVER_LIMIT")


@pytest.mark.parametrize("rng,size,code", [
    ("bytes=0-", 10, "FIELD_RANGE_NOT_SINGLE_CLOSED"),
    ("bytes=0-1,5-6", 2, "FIELD_RANGE_NOT_SINGLE_CLOSED"),
    ("bytes=-500", 500, "FIELD_RANGE_NOT_SINGLE_CLOSED"),
    (None, 10, "FIELD_RANGE_NOT_SINGLE_CLOSED"),
    ("bytes=10-5", 6, "FIELD_RANGE_INVERTED"),
    ("bytes=0-99", 200, "FIELD_RANGE_RESPONSE_BOUND_MISMATCH"),
])
def test_field_range_malformed_refuses(rng, size, code):
    refused_with(run(_field_request(rng, size), doc=_field_doc()), code)


def test_range_header_must_match_declared_range():
    request = _field_request("bytes=100-1099", 1000)
    request["headers"]["Range"] = "bytes=0-9999999"
    refused_with(run(request, doc=_field_doc()), "RANGE_HEADER_MISMATCH")


def test_header_bounds_refuse():
    headers = dict(good_request()["headers"], **{"User-Agent": "x" * 5000})
    refused_with(run(good_request(headers=headers)), "HEADER_BOUND")


@pytest.mark.parametrize("ledger,code", [
    (None, "CAMPAIGN_ACCOUNTING_UNKNOWN"),
    ({"attempts": 0, "body_bytes": 0}, "CAMPAIGN_ACCOUNTING_UNKNOWN"),
    ({"attempts": -1, "body_bytes": 0, "request_ids": []}, "CAMPAIGN_ACCOUNTING_UNKNOWN"),
    ({"attempts": 8, "body_bytes": 0, "request_ids": [f"prior-{i}" for i in range(8)]}, "CAMPAIGN_ATTEMPTS_EXHAUSTED"),
    ({"attempts": 1, "body_bytes": 33_554_432 - 3_145_727, "request_ids": ["prior-0"]}, "CAMPAIGN_BYTES_EXHAUSTED"),
    ({"attempts": 1, "body_bytes": 0, "request_ids": ["synthetic-p1-index"]}, "REQUEST_ID_REPLAY"),
])
def test_campaign_accounting_refuses(ledger, code):
    refused_with(evaluate_request(good_request(), synthetic_resumed(), now_utc=NOW, ledger=ledger), code)


def test_campaign_limits_cannot_be_expanded():
    doc = committed()
    doc["request_envelope"]["campaign_limits"]["attempts"] = 9
    assert "CAMPAIGN_LIMITS_EXPANDED" in check_lineage(doc)


def test_request_schema_is_closed():
    request = good_request()
    request["extra"] = 1
    refused_with(run(request), "REQUEST_SCHEMA_CLOSED_KEYS")
    del request["extra"], request["proxy"]
    refused_with(run(request), "REQUEST_SCHEMA_CLOSED_KEYS")


# -- Identity impact ----------------------------------------------------------

def test_identity_assessment_covers_exact_sources_and_network_set():
    doc = committed()
    expected = {f"sources.{n}" for n in REQUIRED["sources"]} | {f"network.{n}" for n in REQUIRED["network"]}
    assert set(doc["identity_assessment"]) == expected == set(IN_SCOPE_IDS) and len(expected) == 30
    impact = doc["identity_impact"]
    assert impact["closed_by_this_package"] == 0 and impact["qualification_credit"] == 0
    assert impact["audit_missing_before"] == impact["audit_missing_after"] == 77
    assert impact["offline_candidates_on_independent_review"] == ["network.restriction_domain_lineage"]
    assert impact["offline_adjudicable_as_held_only"] == ["network.ecmwf_503_429_expiry_resumption_review"]
    assert impact["remaining_future_or_external"] == 28
    for identity in RUN_SPECIFIC & expected:
        assert doc["identity_assessment"][identity]["disposition"] == "FORWARD_RUN_OBSERVATION"


def test_identity_credit_claim_is_rejected():
    doc = committed()
    doc["identity_impact"]["closed_by_this_package"] = 1
    assert "IDENTITY_CREDIT_CLAIMED" in check_lineage(doc)
    doc = committed()
    doc["identity_assessment"]["network.dns_tls_build_peer_policy"]["closable_offline_on_independent_review"] = True
    assert "CLOSABILITY_INCONSISTENT:network.dns_tls_build_peer_policy" in check_lineage(doc)


@pytest.mark.parametrize("key,value", [
    ("g3l", "PASS"), ("qualification_credit", 1), ("execution_authority", True),
    ("provider_requests_by_this_package", 1),
])
def test_authority_promotion_is_rejected(key, value):
    doc = committed()
    doc[key] = value
    assert "FORBIDDEN_AUTHORITY_OR_CREDIT_PROMOTION" in check_lineage(doc)


def test_p1_restriction_history_is_incomplete_relative_to_lineage():
    doc = committed()
    noaa = [e for e in doc["restriction_events"] if e["control_domain"] == "NOAA_GEFS"]
    ecmwf = [e for e in doc["restriction_events"] if e["control_domain"] == "ECMWF"]
    assert len(noaa) == 6 and len(ecmwf) == 14
    assert {e["origin"] for e in ecmwf} == {lineage_mod.ECMWF_S3, ECMWF_PORTAL, ECMWF_CDN}


def test_lineage_invalid_short_circuits_with_no_authority():
    result = evaluate_request(good_request(), {"schema": "x"}, now_utc=NOW, ledger=LEDGER)
    assert result["outcome"] == REFUSED and result["reasons"][0] == "LINEAGE_INVALID"
    assert result["execution_authority"] is False


# -- No network, no subprocess -----------------------------------------------

def test_module_imports_no_network_or_subprocess():
    tree = ast.parse((ROOT / "tools/v11_gate3_provider_rights_lineage.py").read_text())
    banned = {"socket", "ssl", "http", "urllib.request", "httpx", "requests", "aiohttp",
              "subprocess", "boto3", "botocore", "asyncio"}
    for node in ast.walk(tree):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
            [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        for name in names:
            assert name not in banned and name.split(".")[0] not in {"socket", "ssl", "http", "httpx",
                                                                     "requests", "subprocess", "aiohttp"}, name


def test_cli_check_passes_offline(capsys):
    assert lineage_mod.main(["check"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["problems"] == [] and out["qualification_credit"] == 0 and out["g3l"] == "NO_GO"


def test_coordinator_unknown_retry_population_is_pinned():
    doc = committed()
    event = next(e for e in doc["restriction_events"] if e["event_id"] ==
                 "ecmwf-s3-coordinator-retries-status-unretained-20260929")
    assert event["status"] is None and event["occurrences"] == 21
    assert event["received_at_utc"] == "2026-09-29T09:56:18.859689Z"
    assert "2026-09-29T09:58:53.381961Z" in event["time_basis"]
    assert "ecmwf_coordinator_db" in event["evidence"]
    doc["restriction_events"].remove(event)
    doc["control_domains"]["ECMWF"]["hold_basis"].remove(event["event_id"])
    assert any(p.startswith("MISSING_RETAINED_RESTRICTION:") for p in check_lineage(doc))


def test_immutable_sources_bodies_and_event_facts_refuse_forgery(tmp_path):
    doc = committed()
    doc["evidence_sources"][0].update(path="/tmp/nonexistent", byte_length=1, sha256="0" * 64)
    assert any(p.startswith("PINNED_SOURCE_BINDING:") for p in check_lineage(doc))
    doc = committed()
    doc["evidence_sources"] = [s for s in doc["evidence_sources"]
                               if not s["source_id"].startswith("recovered_body_")]
    for event in doc["restriction_events"]:
        event["evidence"] = [s for s in event["evidence"] if not s.startswith("recovered_body_")]
    assert any(p.startswith("PINNED_SOURCE_BINDING:recovered_body_") for p in check_lineage(doc))
    assert any(p.startswith("RECOVERED_BODY_BINDING:") for p in verify_recovered_bodies(doc, tmp_path))
    doc = committed()
    event = next(e for e in doc["restriction_events"] if e["occurrences"] == 459)
    event["occurrences"] = 1
    assert f"HISTORICAL_EVENT_CHANGED:{event['event_id']}" in check_lineage(doc)


def test_permission_spec_identity_and_duplicate_refuse():
    doc = synthetic_resumed()
    second = copy.deepcopy(doc["permissions"]["GEFS"]["reviewed_permissions"][0])
    second["permission_id"] = "synthetic-second"
    doc["permissions"]["GEFS"]["reviewed_permissions"].append(second)
    refused_with(run(good_request(permission_id="synthetic-second"), doc=doc),
                 "ORIGIN_PATH_PURPOSE_NOT_REVIEWED")
    second["permission_id"] = "synthetic-gefs-permission"
    assert "DUPLICATE_PERMISSION_ID:synthetic-gefs-permission" in check_lineage(doc)
    refused_with(run(good_request(), doc=doc), "LINEAGE_INVALID")
    second["permission_id"] = "synthetic-second"
    doc["request_envelope"]["reviewed_origin_path_specs"].append(
        dict(doc["request_envelope"]["reviewed_origin_path_specs"][0], permission_id="synthetic-second"))
    assert "DUPLICATE_REVIEWED_SPEC" in check_lineage(doc)
    doc = synthetic_resumed()
    doc["request_envelope"]["reviewed_origin_path_specs"].append(
        copy.deepcopy(doc["request_envelope"]["reviewed_origin_path_specs"][0]))
    assert "DUPLICATE_REVIEWED_SPEC" in check_lineage(doc)
    doc = synthetic_resumed()
    doc["request_envelope"]["reviewed_origin_path_specs"][0]["origin"] = NOMADS
    assert "REVIEWED_SPEC_OUTSIDE_PERMISSION_SCOPE" in check_lineage(doc)


def test_planned_completion_must_fit_permission_window():
    refused_with(run(good_request(planned_at_utc="2027-01-01T00:00:00Z")),
                 "PLANNED_TIME_OUTSIDE_PERMISSION")


def test_oversized_numeric_deadline_refuses_cleanly():
    refused_with(run(good_request(deadline_seconds=10 ** 1000)), "DEADLINE_OVER_LIMIT")
    refused_with(run(good_request(planned_at_utc="2026-10-08T23:59:45Z")),
                 "PLANNED_TIME_OUTSIDE_PERMISSION")
    refused_with(run(good_request(planned_at_utc="2026-10-08T23:59:30Z")),
                 "PLANNED_TIME_OUTSIDE_PERMISSION")


@pytest.mark.parametrize("value", ["safe\r\nAuthorization: forged", "bad\x00value", "bad\x7fvalue", "caf\u00e9"])
def test_header_value_wire_bytes_refuse(value):
    headers = dict(good_request()["headers"], **{"User-Agent": value})
    refused_with(run(good_request(headers=headers)), "HEADER_WIRE_INVALID")


def test_header_name_wire_bytes_refuse():
    headers = dict(good_request()["headers"], **{"User Agent": "safe"})
    refused_with(run(good_request(headers=headers)), "HEADER_WIRE_INVALID")


def test_fifo_and_special_file_refuse_before_read(tmp_path):
    fifo = tmp_path / "input.fifo"
    os.mkfifo(fifo)
    old = signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("blocked open")))
    try:
        signal.alarm(2)
        with pytest.raises(LineageError, match="SOURCE_NOT_REGULAR"):
            lineage_mod.read_regular(fifo, 100)
        signal.alarm(0)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    with pytest.raises(LineageError, match="SOURCE_NOT_REGULAR"):
        lineage_mod.read_regular(Path("/dev/null"), 100)


def test_nested_regex_refuses_without_matching():
    doc = synthetic_resumed()
    doc["request_envelope"]["reviewed_origin_path_specs"][0]["path_regex"] = "/(a+)+$"
    assert "REVIEWED_SPEC_INVALID" in check_lineage(doc)
    refused_with(run(good_request(url=NOAA_S3 + "/" + "a" * 32 + "!"), doc=doc), "LINEAGE_INVALID")


@pytest.mark.parametrize("ledger", [
    {"attempts": 0, "body_bytes": 0, "request_ids": ["previous"] * 9},
    {"attempts": 2, "body_bytes": 0, "request_ids": ["previous", "previous"]},
    {"attempts": 1, "body_bytes": 0, "request_ids": [None]},
])
def test_contradictory_ledger_refuses(ledger):
    refused_with(run(good_request(), ledger=ledger), "CAMPAIGN_ACCOUNTING_UNKNOWN")


@pytest.mark.parametrize("request_id", [None, "", "bad\nidentity", "x" * 129])
def test_invalid_request_id_refuses(request_id):
    refused_with(run(good_request(request_id=request_id)), "REQUEST_ID_INVALID")


def test_malformed_json_nested_values_refuse_deterministically():
    doc = committed()
    doc["evidence_sources"][0]["source_id"] = []
    assert check_lineage(doc) == check_lineage(doc)
    refused_with(run(good_request(), doc=doc), "LINEAGE_INVALID")
    doc = synthetic_resumed()
    doc["request_envelope"]["reviewed_origin_path_specs"][0]["path_regex"] = None
    assert "REVIEWED_SPEC_INVALID" in check_lineage(doc)
    refused_with(run(good_request(), doc=doc), "LINEAGE_INVALID")
