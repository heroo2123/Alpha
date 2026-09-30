"""Deterministic v2 rebuild of the exact-day/live-schema GEFS research candidates.

Background
----------
A prior, never-committed one-shot script
(``/home/alphaadmin/AlphaV11_BrainWork/build_exact_day_live_schema_bundles_20260930.py``,
SHA-256 ``38e3650e65508a7889255ba5ef5737f4c3a0be9695cfe85d271bbc459b6b5336``) produced
four draft HIGH/LOW x C/F research bundles under the exact live model id
``NOAA_GEFS_0P50_LINEAR_DAY_V1``. See
``docs/V11_R47_EXACT_DAY_LIVE_SCHEMA_REBUILD.md`` for why that correction (using
``gefs_sources.linear_extreme`` instead of 3-hour bracket snapshot max/min) was
required in the first place.

That v1 generator had two provenance defects, both fixed here and nowhere else:

1. ``created_at``/``causal_watermark`` used ``time.time()`` (wall-clock, different
   every run), so re-running it could never reproduce the same bundle hashes.
2. ``run_id`` used ``family_name[-4:]`` as a "family suffix", which is the
   identical string ``"ture"`` for both ``daily_high_temperature`` and
   ``daily_low_temperature`` -- HIGH and LOW parent/fit run identities collided.

Neither defect changed any predicted probability (both families still select
bias 0.0 C / sigma 0.5 C on the frozen research grid), but both weaken exact
reproducibility and unique identity, which a protected model-authority review
requires before any install step.

This v2 generator:
 * verifies generator and package source bytes against the exact supplied commit/tree;
 * derives ``created_at`` from that commit's committer timestamp (lineage time,
   not a claim of physical execution time), never before dataset completion;
 * pins the complete source dataset-manifest snapshot and uses its completion
   time as the evidence watermark, without claiming historical availability;
 * uses an explicit ``HIGH``/``LOW`` token (never a family-name slice) in every
   run_id, so HIGH and LOW identities can never collide for any family name;
 * independently recomputes (never merely trusts) every source hash it depends
   on: the plan/catalog/preregistration files' own self-hashes, their declared
   cross-links, and the raw GEFS SQLite database's content hash, using the
   exact same table/column/order specification as the original backfill
   pipeline's ``database_content_sha256`` (see ``DB_CONTENT_SPECS``);
 * uses the exact live model id and 31-member width, the exact deployed
   ``linear_extreme`` boundary-clipping semantics, and never installs or
   approves anything -- every bundle is ``FITTED_NOT_CALIBRATED`` /
   ``NO_PROMOTION`` / ``financial_authority=False`` / ``host_approved=False``.

Given identical input files, two runs of this generator produce byte-identical
``rows``/dataset digests, byte-identical manifests, and byte-identical bundle
hashes -- see ``tests/test_gefs_exact_day_live_schema_bundle_v2.py``.

Safety scope
------------
This module never imports ``host_trust`` or ``production``, never references
``/var/lib``, ``/etc``, credentials, wallets, orders, or execution, and refuses
to write to any protected-looking output root (see ``_forbid_protected_output_root``).
It writes only to a caller-supplied private (mode 0700) output root. A validated
hash is still not a promotion review.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest, finite, identity, sha
from polymarket_scanner.v11.forecast_features import ForecastFeatureContract, build_initial_forecast_bundle
from polymarket_scanner.v11.gefs_sources import MODEL_ID, linear_extreme
from polymarket_scanner.v11.model_artifacts import ArtifactStore
from polymarket_scanner.v11.offline_learning import LearningEnvelope, _Budget, _predict, _scores, _comparison, _metric


GENERATOR_VERSION = 'alpha_v11_gefs_exact_day_live_schema_bundle_v2'
V1_GENERATOR_SHA256 = '38e3650e65508a7889255ba5ef5737f4c3a0be9695cfe85d271bbc459b6b5336'
FAMILIES = ('daily_high_temperature', 'daily_low_temperature')
FAMILY_TOKEN = {'daily_high_temperature': 'HIGH', 'daily_low_temperature': 'LOW'}
SPLITS = ('TRAIN', 'DEVELOPMENT', 'HISTORICAL_CONFIRMATION')
UNITS = ('C', 'F')
FEATURE_METHOD = 'PIECEWISE_LINEAR_POINT_TEMPERATURE_PATH'
FROZEN_TIMESTAMP_SOURCE = 'VERIFIED_CODE_COMMIT_COMMITTER_TIME'
WATERMARK_SOURCE = 'PINNED_COMPLETED_DATASET_MANIFEST_CREATED_UTC'
REPO_ROOT = Path(__file__).resolve().parents[1]

# Exact copy of post_gefs_pipeline.py::database_content_sha256's table/column/order
# specification, so this tool independently recomputes -- not trusts -- the
# recorded gefs_database_content_sha256 for the actual current database content.
DB_CONTENT_SPECS = {
    'messages': 'SELECT message_key,run_date,cycle,hour,member,status,attempts,idx_sha256,grib_sha256,bytes,error '
                'FROM messages ORDER BY message_key',
    'station_days': 'SELECT station_day,station,target_date,split,expected_values,status '
                     'FROM station_days ORDER BY station_day',
    'point_values': 'SELECT message_key,station_day,member,hour,value_k,distance_km,nearest_lat,nearest_lon '
                     'FROM point_values ORDER BY station_day,member,hour,message_key',
}

_FORBIDDEN_OUTPUT_PREFIXES = ('/var/lib', '/etc', '/var/run', '/run')


def _forbid_protected_output_root(root: Path) -> None:
    resolved = root.resolve()
    parts = resolved.parts
    if (any(str(resolved) == p or str(resolved).startswith(p + '/') for p in _FORBIDDEN_OUTPUT_PREFIXES)
            or 'host_trust' in parts or 'alpha-v11' in parts):
        raise EvidenceError('V2_OUTPUT_ROOT_FORBIDDEN')


def _git(args: list[str]) -> str:
    return subprocess.run(['git', *args], cwd=REPO_ROOT,
                          capture_output=True, text=True, check=True).stdout.strip()


def verify_code_identity(code_commit: str, code_tree: str) -> dict:
    """Reject parent-only provenance, changed generator/dependencies, and bad trees.

    Package Python files are checked as a complete set, including untracked files;
    documentation may differ so an evidence-only commit can follow the code commit.
    """
    sha(code_commit, 40)
    sha(code_tree, 40)
    try:
        if _git(['rev-parse', code_commit + '^{commit}']) != code_commit:
            raise EvidenceError('V2_CODE_COMMIT_MISMATCH')
        if _git(['rev-parse', code_commit + '^{tree}']) != code_tree:
            raise EvidenceError('V2_CODE_TREE_MISMATCH')
        entries = {}
        for line in _git(['ls-tree', '-r', code_commit]).splitlines():
            metadata, name = line.split('\t', 1)
            if (name.startswith('polymarket_scanner/') and name.endswith('.py')
                    or name in ('tools/gefs_exact_day_live_schema_bundle_v2.py', 'requirements-dev.txt')):
                entries[name] = metadata.split()[2]
        generator = 'tools/gefs_exact_day_live_schema_bundle_v2.py'
        if generator not in entries or 'requirements-dev.txt' not in entries:
            raise EvidenceError('V2_CODE_GENERATOR_NOT_COMMITTED')
        local = {str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / 'polymarket_scanner').rglob('*.py')}
        if local != {p for p in entries if p.startswith('polymarket_scanner/')}:
            raise EvidenceError('V2_CODE_SOURCE_SET_MISMATCH')
        sources = {}
        for name, blob in entries.items():
            raw = (REPO_ROOT / name).read_bytes()
            actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if actual != blob:
                raise EvidenceError('V2_CODE_BYTES_MISMATCH:' + name)
            sources[name] = hashlib.sha256(raw).hexdigest()
        # Verify the actually executing module as well as the repository copy.
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != sources[generator]:
            raise EvidenceError('V2_EXECUTABLE_BYTES_MISMATCH')
        created = finite(float(_git(['show', '-s', '--format=%ct', code_commit])))
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        raise EvidenceError('V2_CODE_IDENTITY_UNVERIFIABLE') from exc
    return {'created_at': created, 'generator_sha256': sources[generator],
            'executable_sources_sha256': digest(sources)}


def _utc_timestamp(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError('explicit UTC required')
        return finite(parsed.timestamp())
    except (TypeError, ValueError) as exc:
        raise EvidenceError('V2_DATASET_COMPLETION_TIMESTAMP_INVALID') from exc


def database_content_sha256(db) -> str:
    h = hashlib.sha256()
    for table, sql in DB_CONTENT_SPECS.items():
        h.update((table + '\n').encode())
        for row in db.execute(sql):
            h.update(canonical(list(row)).encode())
            h.update(b'\n')
    return h.hexdigest()


def _verify_self_hash(value: dict, label: str) -> None:
    if digest({k: v for k, v in value.items() if k != 'artifact_sha256'}) != value.get('artifact_sha256'):
        raise EvidenceError('V2_SOURCE_SELF_HASH_MISMATCH:' + label)


def verify_source_evidence(*, plan: dict, catalog: dict, preregistration: dict,
                           old_dataset_manifest: dict, dataset_manifest_sha256: str, db) -> dict:
    """Recomputes and cross-checks every source hash this generator depends on.

    Never trusts a file's own declared hash fields without recomputing them
    from the actual content on disk / in the database.
    """
    if digest(old_dataset_manifest) != sha(dataset_manifest_sha256):
        raise EvidenceError('V2_DATASET_MANIFEST_SNAPSHOT_MISMATCH')
    completed = _utc_timestamp(old_dataset_manifest['created_utc'])
    _verify_self_hash(plan, 'plan')
    _verify_self_hash(catalog, 'catalog')
    _verify_self_hash(preregistration, 'preregistration')

    if plan['catalog_sha256'] != catalog['artifact_sha256']:
        raise EvidenceError('V2_PLAN_CATALOG_LINK_MISMATCH')
    if (preregistration['brain_plan_sha256'] != plan['artifact_sha256']
            or preregistration['catalog_sha256'] != catalog['artifact_sha256']):
        raise EvidenceError('V2_PREREGISTRATION_LINK_MISMATCH')
    if (old_dataset_manifest['plan_sha256'] != plan['artifact_sha256']
            or old_dataset_manifest['catalog_sha256'] != catalog['artifact_sha256']):
        raise EvidenceError('V2_OLD_DATASET_MANIFEST_LINK_MISMATCH')

    recomputed_db_sha256 = database_content_sha256(db)
    if recomputed_db_sha256 != old_dataset_manifest['gefs_database_content_sha256']:
        raise EvidenceError('V2_GEFS_DB_CONTENT_HASH_MISMATCH')

    return {'gefs_database_content_sha256': recomputed_db_sha256, 'completed_dataset_watermark': completed}


def _to_c(x: float, unit: str) -> float:
    return x if unit == 'C' else (x - 32) * 5 / 9


def _cut_c(x, unit: str, delta: float):
    return None if x is None else _to_c(float(x) + delta, unit)


def build_corrected_dataset(plan: dict, catalog: dict, db) -> tuple[dict, dict]:
    """Exact-local-day, deployed-``linear_extreme``-semantics historical dataset.

    Ports the (private, never-committed) v1 generator's dataset-construction
    loop verbatim in behavior; only provenance/run-id wrapping downstream of
    this function changed for v2.
    """
    events = {(e['station'], e['target_date'], e['family']): e for e in catalog['events']
              if e.get('complete_final_vector') and e.get('source_family') == 'NWS_WRH_TIMESERIES'}
    contracts_c = {f: ForecastFeatureContract(((MODEL_ID, 31),), 'C', f) for f in FAMILIES}
    rows = {f: {s: [] for s in SPLITS} for f in FAMILIES}
    correction = {f: [] for f in FAMILIES}

    for sd in plan['station_days']:
        sid = sd['station'] + '|' + sd['target_date']
        hours = tuple(map(int, sd['forecast_hours']))
        run = datetime.fromisoformat(sd['run_utc']).timestamp()
        day = date.fromisoformat(sd['target_date'])
        tz = ZoneInfo(sd['timezone'])
        start = datetime.combine(day, dtime(), tz).timestamp()
        end = datetime.combine(day + timedelta(days=1), dtime(), tz).timestamp()
        points = [run + h * 3600 for h in hours]
        if not points[0] <= start <= end <= points[-1]:
            raise EvidenceError('V2_DAY_NOT_BRACKETED:' + sid)
        rr = list(db.execute('SELECT member,hour,value_k FROM point_values WHERE station_day=? ORDER BY member,hour', (sid,)))
        by = {m: [] for m in range(31)}
        for r in rr:
            by[int(r['member'])].append((int(r['hour']), float(r['value_k'])))
        for m in range(31):
            if tuple(h for h, _ in by[m]) != hours:
                raise EvidenceError('V2_MEMBER_HOURS_MISMATCH:' + sid + ':' + str(m))
        for fam in FAMILIES:
            high = fam == 'daily_high_temperature'
            live, legacy = [], []
            for m in range(31):
                vals = [v for _, v in by[m]]
                live.append(linear_extreme(points, vals, ((start, end),), high=high) - 273.15)
                legacy.append((max(vals) if high else min(vals)) - 273.15)
            correction[fam].extend(abs(a - b) for a, b in zip(live, legacy))
            ev = events[(sd['station'], sd['target_date'], fam)]
            names = contracts_c[fam].mapping[MODEL_ID]
            base = dict(zip(names, live))
            for b in ev['buckets']:
                target = {'market_id': str(b['market_id']), 'condition_id': str(b['condition_id']),
                          'token_id': str(b['yes_token']), 'side': 'YES'}
                vals = dict(base)
                vals['lower_cut'] = _cut_c(b['lower'], ev['unit'], -.5)
                vals['upper_cut'] = _cut_c(b['upper'], ev['unit'], .5)
                rows[fam][sd['split']].append({
                    'event_id': str(ev['event_id']), 'station': ev['station'],
                    'city_day': ev['station'] + ':' + ev['target_date'],
                    'horizon': '00Z_DAILY_EXTREME',
                    'season': 'LATE_SUMMER' if int(ev['target_date'][5:7]) in (8, 9) else 'OTHER',
                    'strategy': 'GEFS_EXACT_DAY_HISTORICAL', 'selection': 'ALL_SUPPORTED_PREDICTIONS',
                    'target_identity': target, 'target_identity_sha256': digest(target),
                    'label_value': float(b['yes_payout']), 'values': vals})
    return rows, correction


def dataset_digest(rows: dict, *, plan_sha256: str, catalog_sha256: str, gefs_db_content_sha256: str) -> str:
    body = {'version': 'v11_gefs_exact_local_day_historical_v1', 'source_plan_sha256': plan_sha256,
            'source_catalog_sha256': catalog_sha256, 'source_gefs_db_content_sha256': gefs_db_content_sha256,
            'model_id': MODEL_ID, 'feature_method': FEATURE_METHOD, 'normalization_unit': 'C', 'rows': rows}
    return digest(body)


def envelope_for(family: str, contracts_c: dict, preregistration: dict) -> LearningEnvelope:
    s = preregistration['search']
    c = contracts_c[family]
    return LearningEnvelope(MODEL_ID, tuple(c.mapping[MODEL_ID]), 'lower_cut', 'upper_cut',
        tuple(map(float, s['bias_grid_c'])), tuple(map(float, s['sigma_grid_c'])), s['primary_metric'],
        int(s['minimum_train_city_days']), int(s['minimum_evaluation_city_days']), int(s['minimum_evaluation_stations']),
        float(s['required_improvement']), float(s['maximum_slice_regression']), float(s['max_wall_seconds']),
        int(s['max_kernel_evaluations']), int(s['bootstrap_resamples']), int(s['seed']))


def fit_family(family: str, rows: dict, envelope: LearningEnvelope, preregistration: dict) -> dict:
    budget = _Budget(envelope, time.monotonic)
    trials = []
    for bias in sorted(envelope.bias_grid):
        for sigma in sorted(envelope.sigma_grid):
            metrics = _scores(rows[family]['TRAIN'], _predict(rows[family]['TRAIN'], envelope, bias, sigma, budget))
            trials.append({'bias_c': bias, 'kernel_sigma_c': sigma, 'training_metrics': metrics})
    best = min(trials, key=lambda r: (_metric(r['training_metrics'], envelope.primary_metric), r['bias_c'], r['kernel_sigma_c']))
    comparisons = {}
    baseline = preregistration['baseline']
    for split in ('DEVELOPMENT', 'HISTORICAL_CONFIRMATION'):
        rr = rows[family][split]
        base_pred = _predict(rr, envelope, float(baseline['bias_c']), float(baseline['kernel_sigma_c']), budget)
        cand_pred = _predict(rr, envelope, best['bias_c'], best['kernel_sigma_c'], budget)
        comparisons[split] = _comparison(rr, base_pred, cand_pred, envelope, budget)
    return {'selected': best, 'comparisons': comparisons, 'policy_sha256': envelope.sha256}


def build_family_unit_bundle(*, artifacts: ArtifactStore, family: str, unit: str, fit_result: dict,
                              preregistration: dict, dataset_sha256: str, code_commit: str, code_tree: str,
                              dependency_sha256: str, runtime_sha256: str, frozen_created_at: float, causal_watermark: float) -> dict:
    if family not in FAMILIES or unit not in UNITS:
        raise EvidenceError('V2_BUNDLE_FAMILY_OR_UNIT_UNSUPPORTED')
    scale = 1.0 if unit == 'C' else 1.8
    contract = ForecastFeatureContract(((MODEL_ID, 31),), unit, family)
    token = FAMILY_TOKEN[family]
    best = fit_result['selected']
    seed = int(preregistration['search']['seed'])
    frozen = finite(frozen_created_at)

    initial_prov = {
        'code_commit': sha(code_commit, 40), 'code_tree': sha(code_tree, 40),
        'config_sha256': sha(preregistration['artifact_sha256']), 'dataset_sha256': None, 'causal_watermark': None,
        'dependency_sha256': sha(dependency_sha256), 'runtime_sha256': sha(runtime_sha256), 'seed': seed,
        'created_at': frozen, 'parent_bundle_sha256': None,
        'run_id': identity(f'gefs-exact-day-v2-parent-{token}-{unit}', maximum=100),
        'model_family': 'GEFS31_EXACT_DAY_KERNEL', 'model_version': 'v11-exact-day-baseline-v2',
        'training_metrics_sha256': None, 'holdout_metrics_sha256': None,
        'comparison_policy_sha256': sha(fit_result['policy_sha256']), 'training_status': 'INITIAL_NO_FIT'}
    params = {'family': 'GAUSSIAN_MEMBER_MIXTURE',
        'models': [{'model_id': MODEL_ID, 'dependence_group': 'NCEP_GEFS',
                    'bias': float(preregistration['baseline']['bias_c']) * scale,
                    'kernel_sigma': float(preregistration['baseline']['kernel_sigma_c']) * scale,
                    'within_group_weight': 1.0}],
        'group_weights': {'NCEP_GEFS': 1.0}}
    parent = build_initial_forecast_bundle(artifacts, contract=contract, probability_parameters=params,
                                            provenance=initial_prov, quality_modifiers={})
    pinned_parent = artifacts.pin(parent).payload

    fitted_prov = {**initial_prov, 'dataset_sha256': sha(dataset_sha256), 'causal_watermark': finite(causal_watermark),
        'parent_bundle_sha256': parent, 'run_id': identity(f'gefs-exact-day-v2-fit-{token}-{unit}', maximum=100),
        'model_version': 'v11-exact-day-historical-v2',
        'training_metrics_sha256': digest(best['training_metrics']),
        'holdout_metrics_sha256': digest(fit_result['comparisons']), 'training_status': 'FITTED_NOT_CALIBRATED'}

    prob = copy.deepcopy(pinned_parent['components']['PROBABILITY'])
    prob['parameters']['models'][0]['bias'] = float(best['bias_c']) * scale
    prob['parameters']['models'][0]['kernel_sigma'] = float(best['kernel_sigma_c']) * scale
    prob['provenance'] = fitted_prov
    refs = dict(pinned_parent['bundle']['artifacts'])
    refs['PROBABILITY'] = artifacts.put_artifact(prob)

    cal = copy.deepcopy(pinned_parent['components']['CALIBRATION'])
    cal['parameters']['probability_artifact_sha256'] = refs['PROBABILITY']
    cal['provenance'] = fitted_prov
    refs['CALIBRATION'] = artifacts.put_artifact(cal)

    candidate = artifacts.put_bundle(artifacts=refs, target='FINAL_CONTRACT_PAYOUT',
                                      feature_schema_sha256=contract.schema.sha256)
    artifacts.pin(candidate)  # re-validate full bundle/component integrity before returning
    return {'family': family, 'unit': unit, 'parent_bundle_sha256': parent, 'candidate_bundle_sha256': candidate,
            'feature_schema_sha256': contract.schema.sha256, 'bias': float(best['bias_c']) * scale,
            'kernel_sigma': float(best['kernel_sigma_c']) * scale, 'component_sha256': refs,
            'parent_run_id': initial_prov['run_id'], 'fit_run_id': fitted_prov['run_id']}


def build_manifest(*, bundles: list, fit: dict, correction: dict, plan_sha256: str, catalog_sha256: str,
                    gefs_db_content_sha256: str, dataset_sha256: str, preregistration_sha256: str,
                    code_commit: str, code_tree: str, frozen_created_at: float,
                    code_identity: dict, dataset_binding: dict, source_dataset_manifest: dict,
                    rejected_lineage: dict | None = None, v1_bundle_sha256: dict | None = None) -> dict:
    created_iso = datetime.fromtimestamp(frozen_created_at, tz=timezone.utc).isoformat()
    manifest = {
        'artifact_type': 'V11_GEFS_EXACT_DAY_LIVE_SCHEMA_RESEARCH_BUNDLES_V2', 'generator_version': GENERATOR_VERSION,
        'created_utc': created_iso, 'provenance_timestamp_source': FROZEN_TIMESTAMP_SOURCE,
        'code_commit': code_commit, 'code_tree': code_tree,
        'code_identity': code_identity,
        'created_at_semantics': 'Deterministic committed generator lineage time; not physical execution time.',
        'source_dataset_manifest_snapshot': source_dataset_manifest,
        'source_dataset_manifest_sha256': digest(source_dataset_manifest),
        'dataset_binding': dataset_binding, 'training_dataset_provenance_sha256': digest(dataset_binding),
        'causal_watermark': dataset_binding['completed_dataset_watermark'],
        'causal_watermark_source': WATERMARK_SOURCE,
        'historical_availability_established': False, 'forward_proof_established': False,
        'review_rejected_v2_lineage': rejected_lineage,
        'source_preregistration_sha256': preregistration_sha256, 'source_plan_sha256': plan_sha256,
        'source_catalog_sha256': catalog_sha256, 'source_gefs_db_content_sha256': gefs_db_content_sha256,
        'corrected_dataset_sha256': dataset_sha256, 'live_model_id': MODEL_ID, 'feature_method': FEATURE_METHOD,
        'fit': {f: {'selected_parameters': {'bias_c': fit[f]['selected']['bias_c'],
                                            'kernel_sigma_c': fit[f]['selected']['kernel_sigma_c']},
                    'training_metrics_sha256': digest(fit[f]['selected']['training_metrics']),
                    'comparisons': fit[f]['comparisons'], 'comparison_policy_sha256': fit[f]['policy_sha256'],
                    'boundary_correction': {'member_paths': len(correction[f]),
                                            'changed': sum(x > 1e-12 for x in correction[f]),
                                            'max_abs_c': max(correction[f]), 'mean_abs_c': sum(correction[f]) / len(correction[f])}}
               for f in FAMILIES},
        'bundles': bundles, 'status': 'NO_PROMOTION', 'calibration_status': 'FITTED_NOT_CALIBRATED',
        'historical_confirmation_is_forward_holdout': False,
        'financial_authority': False, 'promotion_authority': False, 'order_authority': False, 'host_approved': False,
        'supersedes': {
            'draft_generator_path': '/home/alphaadmin/AlphaV11_BrainWork/build_exact_day_live_schema_bundles_20260930.py',
            'draft_generator_sha256': V1_GENERATOR_SHA256,
            'draft_defects': ['WALL_CLOCK_PROVENANCE_TIMESTAMP', 'AMBIGUOUS_IDENTICAL_FAMILY_RUN_ID_SUFFIX'],
            'draft_bundle_sha256': v1_bundle_sha256 or {},
            'note': 'Draft v1 bundle hashes are retained here only as a superseded-lineage pointer; '
                    'they are never reused, aliased, or installed by this generator.'},
        'limitations': [
            'Completed-dataset watermark records frozen evidence completion, not historical availability or forward proof.',
            'Historical confirmation was already inspected and is not a forward untouched holdout.',
            'Execution cost evidence remains UNKNOWN.',
            'Calibration remains UNCALIBRATED/VACUOUS_BOUNDS.',
            'Host model-authority review/installation and forward shadow evidence remain required.'],
    }
    raw = canonical(manifest).encode()
    manifest['artifact_sha256'] = hashlib.sha256(raw).hexdigest()
    return manifest


def generate(*, plan: dict, catalog: dict, preregistration: dict, old_dataset_manifest: dict, db,
             output_root: Path, code_commit: str, code_tree: str, dependency_sha256: str, runtime_sha256: str,
             dataset_manifest_sha256: str, rejected_lineage: dict | None = None, v1_bundle_sha256: dict | None = None) -> dict:
    """The single entry point both the CLI and tests use. Pure given identical
    (plan, catalog, preregistration, old_dataset_manifest, db content, code
    identity, dependency/runtime hashes) -- see the determinism tests.
    """
    _forbid_protected_output_root(output_root)
    if output_root.exists():
        raise EvidenceError('V2_OUTPUT_ROOT_MUST_NOT_EXIST')
    code_identity = verify_code_identity(code_commit, code_tree)
    if dependency_sha256 != hashlib.sha256((REPO_ROOT / 'requirements-dev.txt').read_bytes()).hexdigest():
        raise EvidenceError('V2_DEPENDENCY_IDENTITY_MISMATCH')
    if runtime_sha256 != hashlib.sha256((sys.version + '|' + sys.executable).encode()).hexdigest():
        raise EvidenceError('V2_RUNTIME_IDENTITY_MISMATCH')
    evidence = verify_source_evidence(plan=plan, catalog=catalog, preregistration=preregistration,
        old_dataset_manifest=old_dataset_manifest, dataset_manifest_sha256=dataset_manifest_sha256, db=db)
    if code_identity['created_at'] < evidence['completed_dataset_watermark']:
        raise EvidenceError('V2_CODE_LINEAGE_PREDATES_COMPLETED_DATASET')
    output_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    objects = output_root / 'objects'
    objects.mkdir(mode=0o700)
    artifacts = ArtifactStore(objects)
    rows, correction = build_corrected_dataset(plan, catalog, db)
    dataset_sha256 = dataset_digest(rows, plan_sha256=plan['artifact_sha256'], catalog_sha256=catalog['artifact_sha256'],
                                    gefs_db_content_sha256=evidence['gefs_database_content_sha256'])

    dataset_binding = {
        'corrected_dataset_sha256': dataset_sha256,
        'source_dataset_manifest_sha256': dataset_manifest_sha256,
        'completed_dataset_watermark': evidence['completed_dataset_watermark'],
        'watermark_source': WATERMARK_SOURCE,
        'historical_availability_established': False, 'forward_proof_established': False}
    contracts_c = {f: ForecastFeatureContract(((MODEL_ID, 31),), 'C', f) for f in FAMILIES}
    fit = {f: fit_family(f, rows, envelope_for(f, contracts_c, preregistration), preregistration) for f in FAMILIES}

    bundles = [build_family_unit_bundle(artifacts=artifacts, family=family, unit=unit, fit_result=fit[family],
        preregistration=preregistration, dataset_sha256=digest(dataset_binding), code_commit=code_commit, code_tree=code_tree,
        dependency_sha256=dependency_sha256, runtime_sha256=runtime_sha256,
        frozen_created_at=code_identity['created_at'], causal_watermark=evidence['completed_dataset_watermark'])
        for family in FAMILIES for unit in UNITS]

    candidate_hashes = {b['candidate_bundle_sha256'] for b in bundles}
    old_hashes = set((v1_bundle_sha256 or {}).values())
    old_hashes.update((rejected_lineage or {}).get('candidate_bundle_sha256', []))
    if len(candidate_hashes) != 4 or not candidate_hashes.isdisjoint(old_hashes):
        raise EvidenceError('V2_CANDIDATE_HASH_REUSED_OR_COLLIDING')

    manifest = build_manifest(bundles=bundles, fit=fit, correction=correction, plan_sha256=plan['artifact_sha256'],
        catalog_sha256=catalog['artifact_sha256'], gefs_db_content_sha256=evidence['gefs_database_content_sha256'],
        dataset_sha256=dataset_sha256, preregistration_sha256=preregistration['artifact_sha256'],
        code_commit=code_commit, code_tree=code_tree, frozen_created_at=code_identity['created_at'],
        code_identity=code_identity, dataset_binding=dataset_binding, source_dataset_manifest=old_dataset_manifest,
        rejected_lineage=rejected_lineage,
        v1_bundle_sha256=v1_bundle_sha256)

    manifest_path = output_root / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    manifest_path.chmod(0o600)
    return manifest


def main(argv: list[str] | None = None) -> int:
    import sqlite3

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--catalog', required=True, type=Path)
    parser.add_argument('--preregistration', required=True, type=Path)
    parser.add_argument('--old-dataset-manifest', required=True, type=Path)
    parser.add_argument('--dataset-manifest-sha256', required=True,
                        help='Pinned canonical digest of the entire completed dataset-manifest snapshot.')
    parser.add_argument('--rejected-v2-manifest', type=Path)
    parser.add_argument('--gefs-db', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--code-commit', default=None)
    parser.add_argument('--code-tree', default=None)
    parser.add_argument('--v1-manifest', type=Path, default=None,
                        help='Optional draft v1 manifest, quoted only as a superseded-lineage pointer.')
    args = parser.parse_args(argv)

    output_root = args.output_root.resolve()
    _forbid_protected_output_root(output_root)
    if output_root.exists():
        raise EvidenceError('V2_OUTPUT_ROOT_MUST_NOT_EXIST')

    plan = json.loads(args.plan.read_bytes())
    catalog = json.loads(args.catalog.read_bytes())
    preregistration = json.loads(args.preregistration.read_bytes())
    old_dataset_manifest = json.loads(args.old_dataset_manifest.read_bytes())
    v1_bundle_sha256 = None
    if args.v1_manifest is not None:
        v1_manifest = json.loads(args.v1_manifest.read_bytes())
        v1_bundle_sha256 = {b['family'] + ':' + b['unit']: b['candidate_bundle_sha256'] for b in v1_manifest['bundles']}

    rejected_lineage = None
    if args.rejected_v2_manifest is not None:
        raw = args.rejected_v2_manifest.read_bytes()
        rejected = json.loads(raw)
        _verify_self_hash(rejected, 'review_rejected_v2')
        rejected_lineage = {
            'manifest_file_sha256': hashlib.sha256(raw).hexdigest(),
            'artifact_sha256': rejected['artifact_sha256'],
            'code_commit': rejected['code_commit'], 'code_tree': rejected['code_tree'],
            'candidate_bundle_sha256': [b['candidate_bundle_sha256'] for b in rejected['bundles']],
            'status': 'REVIEW_REJECTED_PRESERVED_NOT_REUSED',
            'defects': ['CODE_IDENTITY_PREDATES_GENERATOR', 'CREATION_PREDATES_COMPLETED_DATASET']}

    code_commit = args.code_commit or _git(['rev-parse', 'HEAD'])
    code_tree = args.code_tree or _git(['rev-parse', code_commit + '^{tree}'])
    repo_root = Path(__file__).resolve().parents[1]
    dependency_sha256 = hashlib.sha256((repo_root / 'requirements-dev.txt').read_bytes()).hexdigest()
    runtime_sha256 = hashlib.sha256((sys.version + '|' + sys.executable).encode()).hexdigest()

    db = sqlite3.connect('file:' + str(args.gefs_db) + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    try:
        manifest = generate(plan=plan, catalog=catalog, preregistration=preregistration,
            old_dataset_manifest=old_dataset_manifest, db=db, output_root=output_root,
            code_commit=code_commit, code_tree=code_tree, dependency_sha256=dependency_sha256,
            runtime_sha256=runtime_sha256, dataset_manifest_sha256=args.dataset_manifest_sha256,
            rejected_lineage=rejected_lineage, v1_bundle_sha256=v1_bundle_sha256)
    finally:
        db.close()

    print(output_root / 'manifest.json')
    print('dataset', manifest['corrected_dataset_sha256'])
    print('manifest', manifest['artifact_sha256'])
    for family in FAMILIES:
        f = manifest['fit'][family]
        print(family, 'selected', f['selected_parameters']['bias_c'], f['selected_parameters']['kernel_sigma_c'])
    for b in manifest['bundles']:
        print(b['family'], b['unit'], b['candidate_bundle_sha256'], 'sigma', b['kernel_sigma'],
              'parent_run_id', b['parent_run_id'], 'fit_run_id', b['fit_run_id'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
