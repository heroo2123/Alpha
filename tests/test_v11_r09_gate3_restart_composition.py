"""Composed offline accounting contract: real budget + exchange + decoder + v1 store.

Synthetic fixtures only; no provider I/O, network adapter, clock installation or
launch entrypoint. This does not certify capture success, historical feature
eligibility, or any budget/store admission decision.
"""
import contextlib
import hashlib
import struct

import pytest

from polymarket_scanner.v11.ecmwf_grib import sections
from tools.v11_r09_gate3_launch import DurableBudget, LaunchContractError
from tools.v11_r09_gate3_offline_io import (ClockEvidence, MeasuredClock,
    ObjectProvenance, OfflineResponse, SyntheticExchange,
    VersionedImmutableObjectStore, consume_synthetic_response,
    decode_full_grid_station)


def _u(n, size):
    return n.to_bytes(size, 'big')


def _section(number, length):
    data = bytearray(length)
    data[:5] = _u(length, 4) + bytes([number])
    return data


def _grib(*, template=0, bitmap=255, count=4, values=(290, 291, 292, 293),
          di=500000, dj=500000, scan=64):
    ident = _section(1, 21)
    grid = _section(3, 72)
    grid[6:10] = _u(count, 4)
    grid[14] = 6
    grid[30:38] = _u(2, 4) + _u(2, 4)
    grid[42:46] = b'\xff' * 4
    grid[46:54] = _u(33000000, 4) + _u(276000000, 4)
    xsign, ysign = (-1 if scan & 128 else 1), (1 if scan & 64 else -1)
    grid[55:63] = _u(33000000 + ysign * dj, 4) + _u(276000000 + xsign * di, 4)
    grid[63:72] = _u(di, 4) + _u(dj, 4) + bytes([scan])
    product = _section(4, 37)
    packing = _section(5, 21 if template == 0 else 12)
    packing[5:11] = _u(count, 4) + _u(template, 2)
    if template == 0:
        packing[11:15] = struct.pack('>f', 280.)
        packing[19] = 8
        packed = bytes(int(v - 280) for v in values)
    else:
        packing[11] = 1
        packed = struct.pack('>' + 'f' * len(values), *values)
    mask = _section(6, 6)
    mask[5] = bitmap
    data = _section(7, 5 + len(packed))
    data[5:] = packed
    body = b''.join((ident, grid, product, packing, mask, data)) + b'7777'
    return b'GRIB\0\0\0\x02' + _u(16 + len(body), 8) + body


def _pins(raw):
    return {n: hashlib.sha256(s).hexdigest() for n, s in sections(raw).items() if n != 7}


def _root(tmp_path, name='private'):
    root = tmp_path / name
    root.mkdir(mode=0o700)
    (root / 'objects').mkdir(mode=0o700)
    return root


def _journal(tmp_path, name='journal'):
    directory = tmp_path / name
    directory.mkdir(mode=0o700)
    return directory


def _ctx(**changes):
    kwargs = dict(manifest_sha256='a' * 64, policy_sha256='b' * 64,
                  build_id='composition-fixture', clock_method='synthetic',
                  max_clock_age_seconds=10, host_id='fixture-host',
                  boot_id='boot-a')
    kwargs.update(changes)
    return kwargs


PINNED = {}


def _open(root, **changes):
    """Reopen with the previously observed descriptor pin, mirroring a real caller
    that must retain ``descriptor_sha256`` independently across every reopen."""
    kwargs = _ctx(**changes)
    if root in PINNED and 'expected_descriptor_sha256' not in changes:
        kwargs['expected_descriptor_sha256'] = PINNED[root]
    store = VersionedImmutableObjectStore(root, **kwargs)
    if store.report.classification == 'VALID':
        PINNED.setdefault(root, store.descriptor_sha256)
    return store


def _clock(phase, utc, mono, boot='boot-a'):
    raw = f'{phase}:{utc}:{mono}'.encode()
    return ClockEvidence(phase, MeasuredClock(utc, mono, 0.1, mono, boot,
        hashlib.sha256(raw).hexdigest()), raw, 'synthetic')


def _prefix(base_utc=100, boot='boot-a'):
    phases = ('request_start', 'body_receipt', 'decode_complete')
    return tuple(_clock(p, base_utc + i, 1 + i, boot) for i, p in enumerate(phases))


def _final(utc=103, boot='boot-a'):
    return _clock('durable_seal', utc, 4, boot)


@contextlib.contextmanager
def _acquire(journal, manifest_sha256, root, *, max_bytes, **store_changes):
    """One invocation's ordering contract: budget first, store second, reverse close."""
    budget = DurableBudget(journal, manifest_sha256, max_bytes=max_bytes)
    try:
        store = _open(root, **store_changes)
    except BaseException:
        budget.close()
        raise
    try:
        yield budget, store
    finally:
        store.close()
        budget.close()


def test_ordered_acquire_and_reverse_close_releases_both_locks(tmp_path):
    journal = _journal(tmp_path)
    root = _root(tmp_path)
    with _acquire(journal, 'a' * 64, root, max_bytes=64) as (budget, store):
        assert budget.received == 0
        assert store.report.classification == 'VALID'
    assert budget.fd is None and budget.lock_fd is None and budget.dir_fd is None
    assert store.root_fd is None and store.dir_fd is None and store.journal_fd is None
    # Reverse close left neither lock held: a fresh invocation reacquires both.
    with _acquire(journal, 'a' * 64, root, max_bytes=64) as (reopened_budget, reopened_store):
        assert reopened_store.report.classification == 'VALID'


def test_failed_second_acquisition_releases_only_this_invocation(tmp_path):
    journal_a, root_a = _journal(tmp_path, 'journal_a'), _root(tmp_path, 'private_a')
    journal_b = _journal(tmp_path, 'journal_b')
    with DurableBudget(journal_a, 'a' * 64, max_bytes=64) as budget_a, \
         _open(root_a) as store_a:
        assert store_a.report.classification == 'VALID'
        with pytest.raises(BlockingIOError):
            with _acquire(journal_b, 'b' * 64, root_a, max_bytes=64):
                pass  # root_a is exclusively held by store_a: the store step fails.
        # Invocation B's own budget was released by its failed acquisition...
        with DurableBudget(journal_b, 'b' * 64, max_bytes=64) as fresh_b:
            assert fresh_b.received == 0
        # ...while invocation A's independently held budget is untouched.
        with pytest.raises(LaunchContractError, match='JOURNAL_CONCURRENT_WRITER'):
            DurableBudget(journal_a, 'a' * 64, max_bytes=64)
        assert store_a.report.classification == 'VALID'


def test_healthy_receive_decode_raw_and_feature_seal_roundtrip(tmp_path):
    journal, root = _journal(tmp_path), _root(tmp_path)
    raw_bytes = _grib()
    request = {'request_id': 'field-1', 'purpose': 'FIELD', 'provider': 'GEFS',
               'origin': 'https://weather.example.invalid', 'path': '/fixed/message',
               'range_start': 0, 'range_end': len(raw_bytes) - 1,
               'reservation_bytes': len(raw_bytes)}
    response = OfflineResponse(206,
        (('Content-Length', str(len(raw_bytes))),
         ('Content-Range', f'bytes 0-{len(raw_bytes) - 1}/{len(raw_bytes)}'),
         ('ETag', '"pinned-object"')),
        (raw_bytes[:70], raw_bytes[70:]), '8.8.8.8', True)
    with _acquire(journal, 'a' * 64, root, max_bytes=len(raw_bytes)) as (budget, store):
        received = consume_synthetic_response(request, SyntheticExchange({'field-1': response}),
            budget, started_monotonic=1, allowed_peer_ips=('8.8.8.8',),
            expected_etag='"pinned-object"', expected_object_bytes=len(raw_bytes))
        assert received == raw_bytes and budget.received == len(raw_bytes)
        point = decode_full_grid_station(received, provider='GEFS',
            section_sha256=_pins(raw_bytes), latitude=33.5, longitude=-83.5)
        assert point.kelvin == 293

        raw_provenance = ObjectProvenance('RAW', request['request_id'], 'attempt-1',
            'c' * 64, 'd' * 64, 'e' * 64)
        raw_receipt = store.seal_with_provenance(received, raw_provenance, _prefix(),
            lambda: _final())
        assert raw_receipt.object_seal_witnessed and not raw_receipt.historical_feature_eligible

        manifest_bytes = struct.pack('>d', point.kelvin)
        manifest_provenance = ObjectProvenance('FEATURE_MANIFEST', 'feature-1',
            'attempt-1', 'c' * 64, 'd' * 64, 'e' * 64, (raw_receipt.commit_hash,))
        manifest_receipt = store.seal_with_provenance(manifest_bytes, manifest_provenance,
            _prefix(200), lambda: _final(203))
        assert not manifest_receipt.historical_feature_eligible
        assert manifest_receipt.object_sha256 != raw_receipt.object_sha256
        assert manifest_receipt.provenance.dependencies == (raw_receipt.commit_hash,)

        head = (5, store.report.journal_head)

    with _open(root, expected_head=head) as recovered:
        assert recovered.report.classification == 'VALID'
        assert recovered.report.rollback_assurance == 'EXTERNAL_HEAD_MATCH'
        old_raw = recovered.receipts[raw_receipt.object_sha256]
        old_manifest = recovered.receipts[manifest_receipt.object_sha256]
        assert old_raw.acknowledgement == 'UNKNOWN' and old_manifest.acknowledgement == 'UNKNOWN'
        assert not old_raw.historical_feature_eligible and not old_manifest.historical_feature_eligible
        assert old_raw.clocks == raw_receipt.clocks
        assert recovered.read_receipt(old_raw) == raw_bytes
        assert recovered.read_receipt(old_manifest) == manifest_bytes
        # Synthetic clock fixtures are not a measured-attestation or availability claim.
        assert not hasattr(old_raw, 'measured_attestation')
        assert not hasattr(old_raw, 'historically_available')


def test_feature_manifest_rejects_missing_dependency_even_after_decode(tmp_path):
    journal, root = _journal(tmp_path), _root(tmp_path)
    raw_bytes = _grib()
    with _acquire(journal, 'a' * 64, root, max_bytes=len(raw_bytes)) as (_budget, store):
        decode_full_grid_station(raw_bytes, provider='GEFS', section_sha256=_pins(raw_bytes),
            latitude=33.5, longitude=-83.5)
        manifest_provenance = ObjectProvenance('FEATURE_MANIFEST', 'feature-1',
            'attempt-1', 'c' * 64, 'd' * 64, 'e' * 64, ('f' * 64,))
        with pytest.raises(LaunchContractError, match='STORE_DEPENDENCY_MISSING'):
            store.seal_with_provenance(b'orphan manifest', manifest_provenance,
                _prefix(), lambda: _final())


def test_incomplete_budget_beside_committed_object_neither_admits_the_other(tmp_path):
    journal, root = _journal(tmp_path), _root(tmp_path)
    with _acquire(journal, 'a' * 64, root, max_bytes=8) as (budget, store):
        budget.reserve('held', 4, started_monotonic=1)
        budget.consume('held', b'AB')
        raw_provenance = ObjectProvenance('RAW', 'other-request', 'attempt-1',
            'c' * 64, 'd' * 64, 'e' * 64)
        receipt = store.seal_with_provenance(b'independent object', raw_provenance,
            _prefix(), lambda: _final())
        assert receipt.acknowledgement == 'ACKNOWLEDGED_THIS_SESSION'
        assert not hasattr(receipt, 'capture_success')

    with DurableBudget(journal, 'a' * 64, max_bytes=8) as reopened_budget:
        assert reopened_budget.received == 2 and reopened_budget.in_flight == 'held'
        assert reopened_budget.reserved == 4
        with pytest.raises(LaunchContractError, match='UNCERTAIN_REQUEST_HELD'):
            reopened_budget.reserve('next', 1, started_monotonic=5)
    with _open(root) as reopened_store:
        assert reopened_store.report.classification == 'VALID'
        assert reopened_store.receipts[receipt.object_sha256].acknowledgement == 'UNKNOWN'


def test_completed_budget_beside_held_store_neither_admits_the_other(tmp_path, monkeypatch):
    import os
    journal, root = _journal(tmp_path), _root(tmp_path)
    with _acquire(journal, 'a' * 64, root, max_bytes=8) as (budget, store):
        budget.reserve('complete-me', 4, started_monotonic=1)
        budget.consume('complete-me', b'ABCD')
        budget.complete('complete-me')
        assert budget.in_flight is None and budget.received == 4

        real = os.fsync
        journal_calls = 0
        def uncertain(fd):
            nonlocal journal_calls
            if fd == store.journal_fd:
                journal_calls += 1
                if journal_calls == 1:
                    raise OSError('synthetic uncertain journal fsync')
            return real(fd)
        with monkeypatch.context() as patch:
            patch.setattr(os, 'fsync', uncertain)
            raw_provenance = ObjectProvenance('RAW', 'held-request', 'attempt-1',
                'c' * 64, 'd' * 64, 'e' * 64)
            with pytest.raises(OSError, match='uncertain journal'):
                store.seal_with_provenance(b'held object', raw_provenance, _prefix(),
                    lambda: _final())

    with DurableBudget(journal, 'a' * 64, max_bytes=8) as reopened_budget:
        assert reopened_budget.in_flight is None and reopened_budget.received == 4
        reopened_budget.reserve('after-completion', 1, started_monotonic=99)
    with _open(root) as reopened_store:
        assert reopened_store.report.classification == 'UNRESOLVED_PREPARE'
        assert list((root / 'objects').iterdir()) == []


def test_repeated_reopen_and_cross_boot_store_reads_while_budget_resume_refused(tmp_path):
    journal, root = _journal(tmp_path), _root(tmp_path)
    with DurableBudget(journal, 'a' * 64, max_bytes=8, boot_id='boot-a') as budget:
        budget.reserve('violator', 4, started_monotonic=1)
        with pytest.raises(LaunchContractError, match='STREAM_ABORT_AT_ALLOWANCE'):
            budget.consume('violator', b'123456789')
        assert budget.violated and budget.in_flight == 'violator'

    with _open(root) as store:
        raw_provenance = ObjectProvenance('RAW', 'unrelated-request', 'attempt-1',
            'c' * 64, 'd' * 64, 'e' * 64)
        raw_receipt = store.seal_with_provenance(b'historical object', raw_provenance,
            _prefix(), lambda: _final())

    for _ in range(2):
        with DurableBudget(journal, 'a' * 64, max_bytes=8, boot_id='boot-a') as reopened:
            assert reopened.violated and reopened.in_flight == 'violator'
            with pytest.raises(LaunchContractError, match='STREAM_VIOLATION_HELD'):
                reopened.reserve('resume-attempt', 1, started_monotonic=99)

    with pytest.raises(LaunchContractError, match='JOURNAL_IDENTITY_MISMATCH'):
        DurableBudget(journal, 'a' * 64, max_bytes=8, boot_id='boot-b')

    with _open(root, boot_id='boot-b') as historical:
        assert historical.report.classification == 'VALID'
        got = historical.receipts[raw_receipt.object_sha256]
        assert historical.read_receipt(got) == b'historical object'
        with pytest.raises(LaunchContractError, match='STORE_CROSS_BOOT_ACQUISITION_HELD'):
            store_provenance = ObjectProvenance('RAW', 'forged-request', 'attempt-1',
                'c' * 64, 'd' * 64, 'e' * 64)
            historical.seal_with_provenance(b'forged', store_provenance, _prefix(300, 'boot-b'),
                lambda: _final(303, 'boot-b'))

    with _open(root) as same_boot:
        assert same_boot.report.classification == 'VALID'
        assert same_boot.receipts[raw_receipt.object_sha256].clocks == raw_receipt.clocks
