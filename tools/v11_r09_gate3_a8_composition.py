"""Offline A8 composition boundary. A successful check is not launch authority.

This module deliberately has no runner, network client, credential, or manifest
builder. In particular, current V4 cannot validate a real three-provider plan:
its unsupported-purpose refusal must be repaired and independently reviewed
before this boundary can succeed with real inputs.
"""
from __future__ import annotations

import hashlib
import inspect
import math
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from types import CodeType

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_g3l_prep import check_inventory
from tools.v11_r09_gate3_launch import (
    LaunchContractError, _git, check, digest, exact, parse_canonical,
)
from tools.v11_r09_gate3_launch_v4 import PURPOSES, validate_manifest_v4
from tools.v11_r09_gate3_runtime import (
    Clock, FakeClock, FakeResourceProbe, FrozenPlan, ResourceProbe,
    ResourceSnapshot, SyntheticTransport, Transport,
)
from tools.v11_r09_gate3_offline_io import ClockEvidence
from tools.v11_r09_gate3_store_v1 import VersionedImmutableObjectStore

SCHEMA = 'R09_GATE3_A8_COMPOSITION_PREPARATION_V1'
REVIEW_SCHEMA = 'R09_GATE3_A8_COMPOSITION_REVIEW_V1'
PURPOSE_SCHEMA = 'R09_GATE3_REAL_PURPOSE_CONTRACT_V1'
GATES = tuple(f'A{i}' for i in range(1, 8))
ROLES = ('transport', 'clock', 'storage', 'resources', 'decoder')
COMPONENTS = ROLES + tuple(f'{provider}:{purpose}'
                          for provider in ('GEFS', 'IFS', 'AIFS')
                          for purpose in PURPOSES)
MAX_ARTIFACT = 32 * 1024 * 1024


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sealed(root: Path, ref: dict) -> bytes:
    """Read a reviewed object through a stable regular-file descriptor."""
    exact(ref, ('sha256', 'byte_length', 'media_type', 'path'), 'A8_ARTIFACT_SCHEMA')
    digest(ref['sha256'], 'A8_ARTIFACT_DIGEST')
    check(type(ref['byte_length']) is int and 0 < ref['byte_length'] <= MAX_ARTIFACT
          and type(ref['media_type']) is str and '/' in ref['media_type']
          and type(ref['path']) is str, 'A8_ARTIFACT_SCHEMA')
    rel = Path(ref['path'])
    check(not rel.is_absolute() and rel.parts and
          all(part not in ('', '.', '..') for part in rel.parts), 'A8_ARTIFACT_PATH')
    root = Path(root).resolve(strict=True)
    path = root / rel
    check(all(not (root.joinpath(*rel.parts[:n])).is_symlink()
              for n in range(1, len(rel.parts) + 1)), 'A8_ARTIFACT_SYMLINK')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            check(stat.S_ISREG(before.st_mode) and before.st_size == ref['byte_length'],
                  'A8_ARTIFACT_TYPE_OR_SIZE')
            raw = bytearray()
            while len(raw) <= MAX_ARTIFACT:
                chunk = os.read(fd, min(1024 * 1024, MAX_ARTIFACT + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(fd)
            current = os.stat(path, follow_symlinks=False)
            check(len(raw) == ref['byte_length'] and
                  (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                  (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) and
                  (before.st_dev, before.st_ino) == (current.st_dev, current.st_ino) and
                  _sha(raw) == ref['sha256'], 'A8_ARTIFACT_CHANGED')
            return bytes(raw)
        finally:
            os.close(fd)
    except OSError as exc:
        raise LaunchContractError('A8_ARTIFACT_UNAVAILABLE') from exc


def _component(repo: Path, component: object, pin: dict,
               method: str | None = None) -> tuple:
    exact(pin, ('path', 'sha256', 'qualname'), 'A8_COMPONENT_SCHEMA')
    digest(pin['sha256'], 'A8_COMPONENT_DIGEST')
    check(type(pin['path']) is str and type(pin['qualname']) is str,
          'A8_COMPONENT_SCHEMA')
    subject = component if inspect.isfunction(component) else type(component)
    check(subject.__qualname__ == pin['qualname'], 'A8_COMPONENT_IDENTITY')
    try:
        original = Path(inspect.getfile(subject))
        check(not original.is_symlink(), 'A8_COMPONENT_PATH')
        source = original.resolve(strict=True)
        relative = source.relative_to(Path(repo).resolve(strict=True))
        declared = Path(pin['path'])
        check(not declared.is_absolute() and '..' not in declared.parts and
              relative == declared and relative.parts[0] == 'tools' and
              not any(part.startswith('test') for part in relative.parts) and
              not any(word in pin['qualname'].lower() for word in ('fake', 'synthetic')),
              'A8_COMPONENT_PATH')
        before = source.stat()
        raw = source.read_bytes()
        after = source.stat()
        check(stat.S_ISREG(before.st_mode) and
              (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
              (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) and
              _sha(raw) == pin['sha256'], 'A8_COMPONENT_CHANGED')
    except LaunchContractError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise LaunchContractError('A8_COMPONENT_UNAVAILABLE') from exc
    # Compile the exact reviewed bytes without executing them. A matching class
    # source file alone says nothing about a method assigned after import.
    if method is not None:
        check(method not in getattr(component, '__dict__', {}) and
              inspect.isfunction(subject.__dict__.get(method)),
              'A8_COMPONENT_METHOD_REPLACED')
        loaded = subject.__dict__[method]
        qualname = pin['qualname'] + '.' + method
    else:
        loaded = subject
        qualname = pin['qualname']
    module = sys.modules.get(subject.__module__)
    namespace = vars(module) if module is not None else loaded.__globals__
    check(type(namespace.get('__file__')) is str and
          Path(namespace['__file__']).resolve() == source and
          namespace.get(subject.__name__) is subject,
          'A8_COMPONENT_METHOD_REPLACED')
    check(loaded.__module__ == subject.__module__ and
          loaded.__qualname__ == qualname and
          loaded.__globals__ is namespace,
          'A8_COMPONENT_METHOD_REPLACED')
    try:
        compiled = compile(raw, str(source), 'exec', dont_inherit=True)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise LaunchContractError('A8_COMPONENT_SOURCE_INVALID') from exc
    def matching_codes(code: CodeType):
        for value in code.co_consts:
            if isinstance(value, CodeType):
                if value.co_qualname == qualname:
                    yield value
                yield from matching_codes(value)
    matches = tuple(matching_codes(compiled))
    check(len(matches) == 1 and loaded.__code__ == matches[0],
          'A8_COMPONENT_METHOD_REPLACED')
    return (id(component), id(subject), id(loaded), id(loaded.__code__), before.st_dev,
            before.st_ino, before.st_mtime_ns, pin['sha256'])


def _adapters(repo: Path, components: dict, pins: dict) -> tuple:
    exact(components, ROLES, 'A8_ADAPTER_SET')
    exact(pins, ROLES, 'A8_ADAPTER_PINS')
    check(isinstance(components['transport'], Transport) and
          not isinstance(components['transport'], SyntheticTransport) and
          isinstance(components['clock'], Clock) and
          not isinstance(components['clock'], FakeClock) and
          isinstance(components['resources'], ResourceProbe) and
          not isinstance(components['resources'], FakeResourceProbe) and
          type(components['storage']) is VersionedImmutableObjectStore and
          inspect.isfunction(components['decoder']), 'A8_REAL_ADAPTERS_REQUIRED')
    # Storage health is checked through two directly invoked methods: the
    # directory check and the `_usable` wrapper that gates it on `_failed`/
    # recovery classification. Both must be bound to reviewed source, or a
    # replaced `_usable` can skip the directory check entirely.
    methods = {'transport': 'dispatch', 'clock': 'evidence',
               'storage': ('_check_dirs', '_usable'), 'resources': 'snapshot',
               'decoder': None}
    def identity(role):
        spec = methods[role]
        if isinstance(spec, tuple):
            return tuple(_component(repo, components[role], pins[role], method)
                        for method in spec)
        return _component(repo, components[role], pins[role], spec)
    return tuple(identity(role) for role in ROLES)


def _purpose_contracts(payload: dict, root: Path) -> None:
    """Every endpoint must resolve to typed, reviewed real contract bytes."""
    endpoints = payload['network']['endpoints']
    check(len(endpoints) == 3 * len(PURPOSES), 'A8_PURPOSE_COVERAGE')
    for endpoint in endpoints:
        source = payload['sources'][endpoint['provider']]
        contract_ref = source['purpose_mappings'][endpoint['purpose']]['response_contract']
        raw = _sealed(root, {**contract_ref, 'path': 'objects/' + contract_ref['sha256']})
        contract = parse_canonical(raw)
        exact(contract, ('schema', 'provider', 'purpose', 'origin', 'method',
                         'path_spec', 'source_dossier_sha256', 'parser_sha256'),
              'A8_PURPOSE_CONTRACT_SCHEMA')
        check(contract == {
            'schema': PURPOSE_SCHEMA, 'provider': endpoint['provider'],
            'purpose': endpoint['purpose'], 'origin': endpoint['origin'],
            'method': 'GET', 'path_spec': endpoint['path_spec'],
            'source_dossier_sha256': source['dossier']['sha256'],
            'parser_sha256': endpoint['parser_identity']['sha256'],
        }, 'A8_PURPOSE_CONTRACT_MISMATCH')


def _prerequisite_reviews(refs_by_gate: dict, root: Path) -> dict:
    exact(refs_by_gate, GATES, 'A8_PREREQUISITE_SET')
    review_hashes = {}
    for gate, refs in refs_by_gate.items():
        exact(refs, ('evidence', 'review'), 'A8_PREREQUISITE_SCHEMA')
        evidence = _sealed(root, refs['evidence'])
        reviewed = _sealed(root, refs['review'])
        check(refs['evidence']['sha256'] != refs['review']['sha256'],
              'A8_SELF_REVIEW_FORBIDDEN')
        record = parse_canonical(reviewed)
        exact(record, ('gate', 'status', 'evidence_sha256', 'commit_oid', 'tree_oid'),
              'A8_PREREQUISITE_REVIEW_SCHEMA')
        check(record['gate'] == gate and record['status'] == 'ACCEPTED' and
              record['evidence_sha256'] == _sha(evidence) and
              type(record['commit_oid']) is str and
              type(record['tree_oid']) is str and
              re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', record['commit_oid']) and
              re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', record['tree_oid']) and
              len(record['tree_oid']) == len(record['commit_oid']),
              'A8_PREREQUISITE_NOT_ACCEPTED')
        review_hashes[gate] = refs['review']['sha256']
    check(len(set(review_hashes.values())) == len(GATES),
          'A8_PREREQUISITE_REVIEW_REUSED')
    return review_hashes


def _component_reviews(refs_by_component: dict, payload: dict, source_pins: dict,
                       root: Path) -> dict:
    """Require distinct accepted records for five adapters and 15 contracts."""
    exact(refs_by_component, COMPONENTS, 'A8_COMPONENT_REVIEW_SET')
    expected = {role: source_pins[role]['sha256'] for role in ROLES}
    for provider in ('GEFS', 'IFS', 'AIFS'):
        for purpose in PURPOSES:
            expected[f'{provider}:{purpose}'] = payload['sources'][provider][
                'purpose_mappings'][purpose]['response_contract']['sha256']
    review_hashes = {}
    for component, refs in refs_by_component.items():
        exact(refs, ('evidence', 'review'), 'A8_COMPONENT_REVIEW_SCHEMA')
        evidence = _sealed(root, refs['evidence'])
        reviewed = _sealed(root, refs['review'])
        check(refs['evidence']['sha256'] != refs['review']['sha256'],
              'A8_SELF_REVIEW_FORBIDDEN')
        record = parse_canonical(reviewed)
        exact(record, ('component', 'status', 'evidence_sha256', 'identity_sha256',
                       'commit_oid', 'tree_oid'), 'A8_COMPONENT_REVIEW_SCHEMA')
        check(record['component'] == component and record['status'] == 'ACCEPTED' and
              record['evidence_sha256'] == _sha(evidence) and
              record['identity_sha256'] == expected[component] and
              type(record['commit_oid']) is str and
              type(record['tree_oid']) is str and
              re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', record['commit_oid']) and
              re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', record['tree_oid']) and
              len(record['tree_oid']) == len(record['commit_oid']),
              'A8_COMPONENT_NOT_ACCEPTED')
        review_hashes[component] = refs['review']['sha256']
    check(len(set(review_hashes.values())) == len(COMPONENTS),
          'A8_COMPONENT_REVIEW_REUSED')
    return review_hashes


def _fresh_context(payload: dict, plan: FrozenPlan, components: dict,
                   *, now_utc: int, repo: Path, storage_pin: dict) -> tuple:
    clock = components['clock']
    resources = components['resources']
    storage = components['storage']
    reviewed = parse_canonical(plan.runtime_context_raw)
    check(type(reviewed) is dict and type(storage.context) is dict and
          'clock_method' in reviewed and 'boot_id' in reviewed,
          'A8_STORAGE_CONTEXT')
    reading = clock.evidence('composition_pre_use')
    snapshot = resources.snapshot()
    check(type(reading) is ClockEvidence and
          reading.phase == 'composition_pre_use' and
          type(reading.raw) is bytes and
          type(reading.method) is str and reading.method != 'synthetic' and
          reading.method == reviewed['clock_method'] ==
          storage.context.get('clock_method') and
          _sha(reading.raw) == reading.reading.evidence_sha256 and
          type(snapshot) is ResourceSnapshot, 'A8_LIVE_EVIDENCE_REQUIRED')
    measured = reading.reading
    vals = (measured.utc_seconds, measured.monotonic_seconds,
            measured.measured_monotonic_seconds, measured.uncertainty_seconds)
    check(all(type(v) in (int, float) and math.isfinite(v) for v in vals) and
          0 <= measured.uncertainty_seconds <= payload['time']['uncertainty_seconds'] and
          0 <= measured.measured_monotonic_seconds <= measured.monotonic_seconds and
          measured.monotonic_seconds - measured.measured_monotonic_seconds <=
          payload['clocks_and_receipts']['preregistration']['max_measurement_age_seconds'] and
          abs(measured.utc_seconds - now_utc) <=
          1 + measured.uncertainty_seconds and
          measured.boot_id == reviewed['boot_id'] ==
          storage.boot_id,
          'A8_CLOCK_STALE_OR_DIFFERENT_HOST')
    need = payload['runtime']['resource_bounds']['local_storage_quota_bytes']
    limits = payload['limits']
    check(type(snapshot.free_disk_bytes) is int and
          type(snapshot.available_memory_bytes) is int and
          snapshot.free_disk_bytes >= limits['min_free_disk_bytes'] + need and
          snapshot.available_memory_bytes >= limits['min_available_memory_bytes'] +
          limits['decoded'], 'A8_CAPACITY_STALE_OR_INSUFFICIENT')
    check(type(storage) is VersionedImmutableObjectStore and
          storage.root_fd is not None, 'A8_STORAGE_CONTEXT')
    exact(storage.context, ('manifest', 'policy', 'build', 'clock_method',
                            'max_clock_age'), 'A8_STORAGE_CONTEXT')
    check(storage.context['manifest'] == plan.manifest_sha256 and
          storage.context['policy'] == reviewed.get('store_policy') ==
          payload['runtime']['policy']['sha256'] and
          storage.descriptor_sha256 == reviewed.get('store_descriptor') and
          _sha(canonical(storage.descriptor)) == storage.descriptor_sha256 and
          all(storage.descriptor.get(key) == value for key, value in
              storage.context.items()), 'A8_STORAGE_CONTEXT')
    # `_usable` is the directly invoked store health call; it gates `_check_dirs`
    # on `_failed`/recovery classification. Recheck both against reviewed source
    # immediately before invocation so an instance, class, or in-place
    # replacement made after `_adapters` ran cannot skip either check.
    _component(repo, storage, storage_pin, '_check_dirs')
    _component(repo, storage, storage_pin, '_usable')
    storage._usable()
    st = os.fstat(storage.root_fd)
    check((st.st_dev, st.st_ino) == storage.root_identity,
          'A8_STORAGE_SUBSTITUTED')
    return (measured.boot_id, st.st_dev, st.st_ino,
            storage.descriptor_sha256, storage.context['policy'],
            reading.method, reading.reading.evidence_sha256)


@dataclass(frozen=True)
class PreparedComposition:
    """Ephemeral check result, deliberately without a run/dispatch method."""
    package_sha256: str
    review_sha256: str
    component_identities: tuple
    host_identity: tuple


def validate_a8_composition(package_raw: bytes, *, manifest_raw: bytes,
                            inventory_raw: bytes, review_raw: bytes,
                            expected_review_sha256: str, plan: FrozenPlan,
                            components: dict, repo: Path, object_root: Path,
                            now_utc: int) -> PreparedComposition:
    """Check exact independently pinned inputs; never issue launch permission.

    The expected review hash must come from outside the candidate package.
    A reviewer must separately authenticate the A1-A7 and A8 verdicts; this
    function checks their byte binding, not the reviewer's authority.
    """
    package = parse_canonical(package_raw)
    exact(package, ('schema', 'manifest_sha256', 'inventory_sha256', 'plan_sha256',
                    'runtime_context_sha256', 'commit_oid', 'tree_oid',
                    'reviewed_utc',
                    'adapter_sources', 'prerequisite_reviews',
                    'component_reviews'), 'A8_PACKAGE_SCHEMA')
    check(package['schema'] == SCHEMA, 'A8_PACKAGE_SCHEMA')
    for key in ('manifest_sha256', 'inventory_sha256', 'plan_sha256',
                'runtime_context_sha256'):
        digest(package[key], 'A8_PACKAGE_DIGEST')
    check(type(manifest_raw) is bytes and type(inventory_raw) is bytes and
          type(review_raw) is bytes and type(now_utc) is int and
          _sha(manifest_raw) == package['manifest_sha256'] and
          _sha(inventory_raw) == package['inventory_sha256'], 'A8_PACKAGE_BYTES')
    digest(expected_review_sha256, 'A8_EXTERNAL_REVIEW_PIN')
    check(_sha(review_raw) == expected_review_sha256,
          'A8_EXTERNAL_REVIEW_MISMATCH')
    manifest = parse_canonical(manifest_raw)
    check(type(manifest) is dict and type(manifest.get('identity')) is dict and
          type(manifest['identity'].get('pilot_id')) is str,
          'A8_MANIFEST_IDENTITY_REQUIRED')
    check(manifest['identity']['mapping_scope'] == 'PROVIDER_REVIEW_REQUIRED' and
          not manifest['identity']['pilot_id'].startswith('synthetic_'),
          'A8_SYNTHETIC_MANIFEST_FORBIDDEN')
    check(type(plan) is FrozenPlan and not plan.synthetic_fixture and
          plan.manifest_raw == manifest_raw and
          plan.manifest_sha256 == package['manifest_sha256'] and
          plan.sha256 == package['plan_sha256'] and
          type(plan.runtime_context_raw) is bytes and
          _sha(plan.runtime_context_raw) == package['runtime_context_sha256'],
          'A8_EXACT_VALIDATED_PLAN_REQUIRED')
    inventory = parse_canonical(inventory_raw)
    check(type(inventory) is dict and type(inventory.get('target_date')) is str,
          'A8_INVENTORY_REQUIRED')
    check(type(package['reviewed_utc']) is int and
          package['reviewed_utc'] <= now_utc, 'A8_REVIEW_TIME')
    findings = check_inventory(inventory, target_date=inventory['target_date'],
                               now_utc=package['reviewed_utc'], stage='FINAL',
                               object_root=object_root)
    check(not findings, 'A8_EVIDENCE_INCOMPLETE_OR_STALE')
    evidence = inventory['evidence']
    check(_sealed(object_root,
                  evidence['review.private_v4_manifest_canonical_bytes']['ref']) ==
          manifest_raw and
          _sealed(object_root,
                  evidence['review.private_v4_manifest_digest']['ref']) ==
          package['manifest_sha256'].encode('ascii'),
          'A8_INVENTORY_MANIFEST_SUBSTITUTION')
    g3l_terminal_ref = evidence['review.detached_g3l_completed_terminal']['ref']
    g3l_terminal = parse_canonical(_sealed(object_root, g3l_terminal_ref))
    exact(g3l_terminal, ('schema', 'status', 'manifest_sha256', 'commit_oid',
                         'tree_oid'), 'A8_G3L_TERMINAL_SCHEMA')
    check(g3l_terminal == {
        'schema': 'R09_GATE3_G3L_REVIEW_TERMINAL_V1', 'status': 'PASS',
        'manifest_sha256': package['manifest_sha256'],
        'commit_oid': package['commit_oid'], 'tree_oid': package['tree_oid'],
    }, 'A8_G3L_NOT_ACCEPTED')
    check(validate_manifest_v4(manifest_raw, repo=repo, object_root=object_root,
                               now_utc=now_utc) == package['manifest_sha256'] and
          plan.verify_validated_projection() == manifest,
          'A8_V4_VALIDATION_MISMATCH')
    _purpose_contracts(manifest, object_root)
    check(package['commit_oid'] == _git(repo, 'rev-parse', 'HEAD') and
          package['tree_oid'] == _git(repo, 'rev-parse', 'HEAD^{tree}'),
          'A8_REVIEWED_TREE_MISMATCH')
    review_hashes = _prerequisite_reviews(package['prerequisite_reviews'],
                                          object_root)
    identities = _adapters(repo, components, package['adapter_sources'])
    component_review_hashes = _component_reviews(
        package['component_reviews'], manifest, package['adapter_sources'],
        object_root)
    host = _fresh_context(manifest, plan, components, now_utc=now_utc,
                          repo=repo, storage_pin=package['adapter_sources']['storage'])
    review = parse_canonical(review_raw)
    exact(review, ('schema', 'status', 'package_sha256', 'manifest_sha256',
                   'inventory_sha256', 'plan_sha256', 'commit_oid', 'tree_oid',
                   'reviewed_utc', 'g3l_terminal_sha256',
                   'prerequisite_review_sha256', 'component_review_sha256'),
          'A8_REVIEW_SCHEMA')
    check(review == {'schema': REVIEW_SCHEMA, 'status': 'ACCEPTED',
                     'package_sha256': _sha(package_raw),
                     'manifest_sha256': package['manifest_sha256'],
                     'inventory_sha256': package['inventory_sha256'],
                     'plan_sha256': package['plan_sha256'],
                     'commit_oid': package['commit_oid'],
                     'tree_oid': package['tree_oid'],
                     'reviewed_utc': package['reviewed_utc'],
                     'g3l_terminal_sha256': g3l_terminal_ref['sha256'],
                     'prerequisite_review_sha256': review_hashes,
                     'component_review_sha256': component_review_hashes},
          'A8_REVIEW_NOT_BOUND_TO_INPUTS')
    return PreparedComposition(_sha(package_raw), expected_review_sha256,
                               identities, host)


def recheck_a8_composition(prepared: PreparedComposition, package_raw: bytes,
                           **kwargs) -> PreparedComposition:
    """Repeat all checks at point of use, including actual adapter object IDs."""
    check(type(prepared) is PreparedComposition and
          _sha(package_raw) == prepared.package_sha256 and
          kwargs.get('expected_review_sha256') == prepared.review_sha256,
          'A8_REVIEW_TO_USE_SUBSTITUTION')
    plan = kwargs.get('plan')
    now = kwargs.get('now_utc')
    check(type(plan) is FrozenPlan and type(now) is int and
          plan.window.start_utc <= now < plan.window.acquisition_end_utc,
          'A8_USE_OUTSIDE_WINDOW')
    current = validate_a8_composition(package_raw, **kwargs)
    check(current.component_identities == prepared.component_identities and
          current.host_identity[:-1] == prepared.host_identity[:-1],
          'A8_REVIEW_TO_USE_SUBSTITUTION')
    return current
