"""Research-only: attempt a live-GEFS-schema rebind of the frozen all-market fit.

Background
----------
The deployed run-bound source (``polymarket_scanner/v11/gefs_sources.py``) emits a
31-member ``temperature_input`` under ``MODEL_ID = NOAA_GEFS_0P50_LINEAR_DAY_V1``.
The existing historical all-market research
(``v11_gefs_all_market_research_result_20260929.json``) fit ``bias_c``/
``kernel_sigma_c`` against a ``ForecastFeatureContract`` keyed on the label
``"gefs31"`` instead. Because ``ForecastFeatureContract.mapping`` and the resulting
``feature_schema_sha256`` are keyed by model_id string, a bundle built under
``"gefs31"`` cannot accept live components built under
``NOAA_GEFS_0P50_LINEAR_DAY_V1`` -- ``ForecastFeatureContract.require_bundle``
(``forecast_features.py``) fails closed on the model-id mismatch. That refusal is
correct and must not be silently patched over by renaming the label.

This tool decides -- and proves, rather than assumes -- whether that rename is a
pure identity operation (same 31 ordered member semantics, only the label
differs) or whether it would also paper over a genuine difference in how the
member values themselves were computed. It only produces a rebind bundle in the
first case; in the second, it fails closed and writes a manifest explaining why.

What was actually found for the current evidence set
------------------------------------------------------
The frozen research dataset itself declares (top-level ``feature_method`` in
``v11_gefs_all_market_dataset_20260929.json.gz``):
``GEFS_3H_SNAPSHOT_MEMBER_DAILY_MAX_OR_MIN`` -- each member's daily extreme is the
max/min of its raw 3-hour snapshot values.

The live source computes a materially different quantity. ``gefs_sources.py``'s
``assemble_path`` calls ``linear_extreme`` (recorded as
``coverage['method'] == 'PIECEWISE_LINEAR_POINT_TEMPERATURE_PATH'``), which
*interpolates* the piecewise-linear forecast path at the exact local-day boundary
instants and only includes raw snapshots strictly *inside* the window -- it does
not include the raw snapshot at the window's edges. Whenever the station's local
midnight does not land exactly on the model's 3-hour synoptic grid (the normal
case: e.g. US Eastern Daylight Time is UTC-4, not a multiple of 3 hours), the two
methods sample different points and can produce different extrema. This is not
hypothetical: ``tests/test_v11_gefs_sources.py::
test_full_31_member_path_reaches_model_input_with_original_run_and_unknown_publication``
already asserts the live pipeline interpolates a non-grid-aligned boundary
("Interpolate the 04:00 UTC boundary, excluding the 30h sample outside the day"),
and this tool's own tests construct a concrete counterexample where the two
methods disagree.

Because the two methods differ, this tool -- run against the real evidence with
no override -- refuses to build a bundle. See ``attempt_rebind`` /
``prove_member_semantics_equivalence``.

Safety scope
------------
This module never imports anything from ``host_trust``, never references
``/var/lib``, ``/etc``, credentials, wallets, orders, or execution. It writes only
to a caller-supplied private (mode 0700) output root, never a champion pointer,
approval, or protected-state path. A produced bundle is always
FITTED_NOT_CALIBRATED / VACUOUS_BOUNDS / financial_authority=False, and every
result this tool emits carries ``no_promotion=True``, ``not_host_approved=True``,
``historical_research_only=True``.
"""
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest, finite, identity, sha
from polymarket_scanner.v11.forecast_features import ForecastFeatureContract
from polymarket_scanner.v11.gefs_sources import MODEL_ID as LIVE_MODEL_ID
from polymarket_scanner.v11.model_artifacts import ARTIFACT_VERSION, ArtifactStore, validate_artifact
from polymarket_scanner.weather_only_contracts import DAILY_HIGH, DAILY_LOW


TOOL_VERSION = 'alpha_v11_gefs_schema_rebind_v1'
SOURCE_MODEL_ID = 'gefs31'
MEMBER_COUNT = 31
FAMILIES = (DAILY_HIGH, DAILY_LOW)

# The frozen research dataset's own declared per-member daily-extreme algorithm
# (top-level "feature_method" in v11_gefs_all_market_dataset_20260929.json.gz).
# Used only as a fallback label when the caller does not supply the dataset
# bytes; verify_source_evidence() always prefers the value read from the file.
SOURCE_FEATURE_METHOD_FALLBACK = 'GEFS_3H_SNAPSHOT_MEMBER_DAILY_MAX_OR_MIN'

# The live run-bound source's algorithm identifier, exactly as
# polymarket_scanner/v11/gefs_sources.py's assemble_path() records it in
# payload['coverage']['method']. Kept in sync by
# test_gefs_schema_rebind.py::test_live_feature_method_constant_matches_gefs_sources.
LIVE_FEATURE_METHOD = 'PIECEWISE_LINEAR_POINT_TEMPERATURE_PATH'

_FORBIDDEN_OUTPUT_PREFIXES = ('/var/lib', '/etc', '/var/run', '/run')


class RebindRefused(EvidenceError):
    """Raised internally and always turned into a NO_BUNDLE_CREATED result."""


def _forbid_protected_output_root(root: Path) -> None:
    resolved = root.resolve()
    parts = resolved.parts
    if (any(str(resolved) == p or str(resolved).startswith(p + '/') for p in _FORBIDDEN_OUTPUT_PREFIXES)
            or 'host_trust' in parts or 'alpha-v11' in parts):
        raise EvidenceError('REBIND_OUTPUT_ROOT_FORBIDDEN')


def contract_pair(unit: str, family: str) -> tuple[ForecastFeatureContract, ForecastFeatureContract]:
    source = ForecastFeatureContract(((SOURCE_MODEL_ID, MEMBER_COUNT),), unit, family)
    live = ForecastFeatureContract(((LIVE_MODEL_ID, MEMBER_COUNT),), unit, family)
    return source, live


def prove_contract_shape_equivalence(unit: str, family: str) -> tuple[ForecastFeatureContract, ForecastFeatureContract]:
    """Proves only that renaming model_id alone preserves width/unit/family/
    quantization shape. This says nothing about whether the member VALUES
    upstream were computed the same way -- see prove_member_semantics_equivalence.
    """
    source, live = contract_pair(unit, family)
    if (source.model_widths[0][1] != live.model_widths[0][1] or source.unit != live.unit
            or source.family != live.family or source.quantization != live.quantization):
        raise RebindRefused('GEFS31_LIVE_CONTRACT_SHAPE_MISMATCH')
    return source, live


def prove_member_semantics_equivalence(*, source_feature_method: str, live_feature_method: str = LIVE_FEATURE_METHOD) -> None:
    """Fails closed unless the historical and live per-member daily-extreme
    construction algorithms are the identical declared computation. See the
    module docstring: for the real evidence these are provably different
    (raw snapshot max/min vs piecewise-linear boundary interpolation), so this
    raises for the real invocation and the tool refuses to build a bundle.
    """
    identity(source_feature_method)
    identity(live_feature_method)
    if source_feature_method != live_feature_method:
        raise RebindRefused('GEFS31_LIVE_MEMBER_SEMANTICS_METHOD_MISMATCH')


def _build_fitted_forecast_bundle(artifacts: ArtifactStore, *, contract: ForecastFeatureContract,
                                   bias: float, kernel_sigma: float, provenance: dict) -> tuple[str, dict]:
    """A FITTED_NOT_CALIBRATED sibling of forecast_features.build_initial_forecast_bundle.

    Kept local to this research tool (not added to production forecast_features.py)
    to keep the reviewable surface small: this writes only ordinary, independently
    validated ArtifactStore objects through public APIs.
    """
    if provenance.get('training_status') != 'FITTED_NOT_CALIBRATED':
        raise EvidenceError('REBIND_FITTED_PROVENANCE_REQUIRED')
    if provenance.get('parent_bundle_sha256') is not None:
        raise EvidenceError('REBIND_BUNDLE_HAS_NO_PARENT')
    model_id = contract.model_widths[0][0]
    probability_parameters = {'family': 'GAUSSIAN_MEMBER_MIXTURE',
        'models': [{'model_id': model_id, 'dependence_group': model_id, 'bias': bias,
                    'kernel_sigma': kernel_sigma, 'within_group_weight': 1.}],
        'group_weights': {model_id: 1.}}
    parameters = {
        'FEATURES': json.loads(canonical(asdict(contract.schema))),
        'PROBABILITY': probability_parameters,
        'EXECUTION_COST': {'method': 'NO_EMPIRICAL_EXECUTION_MODEL', 'additional_cost_per_share': None,
                           'evidence_class': 'UNKNOWN'},
        'STRATEGY_QUALITY': {'method': 'FIXED_REDUCTION_ONLY', 'modifiers': {}},
    }
    values = {kind: dict(version=ARTIFACT_VERSION, kind=kind, target='FINAL_CONTRACT_PAYOUT',
                        feature_schema_sha256=contract.schema.sha256, parameters=params, provenance=provenance)
              for kind, params in parameters.items()}
    values['CALIBRATION'] = dict(version=ARTIFACT_VERSION, kind='CALIBRATION', target='FINAL_CONTRACT_PAYOUT',
        feature_schema_sha256=contract.schema.sha256, provenance=provenance,
        parameters=dict(method='VACUOUS_BOUNDS', status='UNCALIBRATED',
                        probability_artifact_sha256=digest(values['PROBABILITY'])))
    for value in values.values():
        validate_artifact(value)
    refs = {kind: artifacts.put_artifact(value) for kind, value in values.items()}
    bundle_sha256 = artifacts.put_bundle(artifacts=refs, target='FINAL_CONTRACT_PAYOUT',
                                         feature_schema_sha256=contract.schema.sha256)
    return bundle_sha256, refs


def attempt_rebind(*, unit: str, family: str, bias_c: float, kernel_sigma_c: float,
                    source_dataset_sha256: str, source_parameter_sha256: str, preregistration_sha256: str,
                    training_metrics_sha256: str, code_commit: str, code_tree: str, created_at: float,
                    causal_watermark: float, seed: int, run_id: str, output_root: Path | None,
                    source_feature_method: str, live_feature_method: str = LIVE_FEATURE_METHOD) -> dict:
    """Attempt one live-schema bundle. Never raises for the expected fail-closed
    path; a caller always gets a structured result dict with an explicit status.
    """
    common = {'family': family, 'unit': unit, 'source_model_id': SOURCE_MODEL_ID, 'live_model_id': LIVE_MODEL_ID,
              'source_feature_method': source_feature_method, 'live_feature_method': live_feature_method,
              'financial_authority': False, 'promotion_authority': False,
              'no_promotion': True, 'not_host_approved': True, 'historical_research_only': True}
    try:
        prove_contract_shape_equivalence(unit, family)
        prove_member_semantics_equivalence(source_feature_method=source_feature_method,
                                            live_feature_method=live_feature_method)
    except RebindRefused as exc:
        return {**common, 'status': 'NO_BUNDLE_CREATED', 'reason': str(exc), 'bundle_sha256': None,
                'component_sha256': None}

    if output_root is None:
        raise EvidenceError('REBIND_OUTPUT_ROOT_REQUIRED')
    _forbid_protected_output_root(output_root)
    objects = output_root / 'objects'
    objects.mkdir(mode=0o700, parents=True, exist_ok=True)
    objects.chmod(0o700)
    artifacts = ArtifactStore(objects)
    live_contract = ForecastFeatureContract(((LIVE_MODEL_ID, MEMBER_COUNT),), unit, family)
    provenance = {
        'code_commit': sha(code_commit, 40), 'code_tree': sha(code_tree, 40),
        'config_sha256': digest({'tool': TOOL_VERSION, 'unit': unit, 'family': family}),
        'dataset_sha256': sha(source_dataset_sha256), 'causal_watermark': finite(causal_watermark),
        'dependency_sha256': digest({'source_feature_method': source_feature_method,
                                     'live_feature_method': live_feature_method}),
        'runtime_sha256': digest({'contract': asdict(live_contract)}),
        'seed': seed, 'created_at': finite(created_at), 'parent_bundle_sha256': None,
        'run_id': identity(run_id, maximum=100), 'model_family': 'GAUSSIAN_MEMBER_MIXTURE',
        'model_version': 'gefs_schema_rebind_v1', 'training_metrics_sha256': sha(training_metrics_sha256),
        'holdout_metrics_sha256': None, 'comparison_policy_sha256': sha(preregistration_sha256),
        'training_status': 'FITTED_NOT_CALIBRATED',
    }
    bundle_sha256, refs = _build_fitted_forecast_bundle(artifacts, contract=live_contract,
        bias=bias_c, kernel_sigma=kernel_sigma_c, provenance=provenance)
    return {**common, 'status': 'BUNDLE_CREATED', 'reason': 'MEMBER_SEMANTICS_PROVEN_IDENTICAL',
            'bundle_sha256': bundle_sha256, 'component_sha256': refs, 'provenance': provenance,
            'source_parameter_sha256': source_parameter_sha256}


def verify_source_evidence(*, research_result_path: Path, dataset_manifest_path: Path,
                           preregistration_path: Path, dataset_gz_path: Path | None) -> dict:
    """Recomputes every hash this tool relies on instead of trusting file fields."""
    research = json.loads(research_result_path.read_bytes())
    manifest = json.loads(dataset_manifest_path.read_bytes())
    prereg = json.loads(preregistration_path.read_bytes())

    if digest({k: v for k, v in research.items() if k != 'artifact_sha256'}) != research['artifact_sha256']:
        raise EvidenceError('REBIND_RESEARCH_RESULT_SELF_HASH_MISMATCH')
    if digest({k: v for k, v in prereg.items() if k != 'artifact_sha256'}) != prereg['artifact_sha256']:
        raise EvidenceError('REBIND_PREREGISTRATION_SELF_HASH_MISMATCH')
    if prereg['artifact_sha256'] != research['preregistration_sha256']:
        raise EvidenceError('REBIND_PREREGISTRATION_LINK_MISMATCH')
    if manifest['dataset_sha256'] != research['dataset_sha256']:
        raise EvidenceError('REBIND_DATASET_LINK_MISMATCH')

    source_feature_method = SOURCE_FEATURE_METHOD_FALLBACK
    if dataset_gz_path is not None:
        raw = dataset_gz_path.read_bytes()
        if digest(json.loads(gzip.decompress(raw))) != manifest['dataset_sha256']:
            raise EvidenceError('REBIND_DATASET_CONTENT_HASH_MISMATCH')
        import hashlib
        if hashlib.sha256(raw).hexdigest() != manifest['compressed_file_sha256']:
            raise EvidenceError('REBIND_DATASET_COMPRESSED_HASH_MISMATCH')
        source_feature_method = json.loads(gzip.decompress(raw))['feature_method']

    results = {}
    for entry in research['results']:
        fp = entry['frozen_parameters']
        if digest(fp) != entry['candidate_parameter_sha256']:
            raise EvidenceError('REBIND_FROZEN_PARAMETER_HASH_MISMATCH')
        if (fp['model_id'] != SOURCE_MODEL_ID or fp['unit'] != 'C' or fp['family'] not in FAMILIES
                or fp['selection_partition'] != 'TRAIN_ONLY'
                or fp['dataset_sha256'] != research['dataset_sha256']
                or fp['preregistration_sha256'] != research['preregistration_sha256']
                or entry['calibration_status'] != 'FITTED_NOT_CALIBRATED'):
            raise EvidenceError('REBIND_FROZEN_PARAMETER_SHAPE_INVALID')
        best_trial = next((t for t in entry['training_trials']
                           if t['bias_c'] == fp['bias_c'] and t['kernel_sigma_c'] == fp['kernel_sigma_c']), None)
        if best_trial is None:
            raise EvidenceError('REBIND_SELECTED_TRIAL_NOT_FOUND')
        results[fp['family']] = {
            'bias_c': fp['bias_c'], 'kernel_sigma_c': fp['kernel_sigma_c'],
            'dataset_sha256': fp['dataset_sha256'], 'candidate_parameter_sha256': entry['candidate_parameter_sha256'],
            'training_metrics_sha256': digest(best_trial['training_metrics']),
        }
    if set(results) != set(FAMILIES):
        raise EvidenceError('REBIND_BOTH_FAMILIES_REQUIRED')
    created_at = datetime.fromisoformat(research['created_utc']).timestamp()
    return {'results': results, 'preregistration_sha256': research['preregistration_sha256'],
            'dataset_sha256': research['dataset_sha256'], 'source_feature_method': source_feature_method,
            'causal_watermark': created_at, 'seed': prereg['search']['seed']}


def _git(args: list[str]) -> str:
    return subprocess.run(['git', *args], cwd=Path(__file__).resolve().parents[1],
                          capture_output=True, text=True, check=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--research-result', required=True, type=Path)
    parser.add_argument('--dataset-manifest', required=True, type=Path)
    parser.add_argument('--preregistration', required=True, type=Path)
    parser.add_argument('--dataset-gz', type=Path, default=None)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--code-commit', default=None)
    parser.add_argument('--code-tree', default=None)
    args = parser.parse_args(argv)

    output_root = args.output_root.resolve()
    _forbid_protected_output_root(output_root)
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_root.chmod(0o700)

    evidence = verify_source_evidence(research_result_path=args.research_result,
        dataset_manifest_path=args.dataset_manifest, preregistration_path=args.preregistration,
        dataset_gz_path=args.dataset_gz)
    code_commit = args.code_commit or _git(['rev-parse', 'HEAD'])
    code_tree = args.code_tree or _git(['rev-parse', 'HEAD^{tree}'])

    now = datetime.now(timezone.utc).timestamp()
    results = []
    for family, fp in sorted(evidence['results'].items()):
        result = attempt_rebind(unit='C', family=family, bias_c=fp['bias_c'], kernel_sigma_c=fp['kernel_sigma_c'],
            source_dataset_sha256=fp['dataset_sha256'], source_parameter_sha256=fp['candidate_parameter_sha256'],
            preregistration_sha256=evidence['preregistration_sha256'],
            training_metrics_sha256=fp['training_metrics_sha256'], code_commit=code_commit, code_tree=code_tree,
            created_at=now, causal_watermark=evidence['causal_watermark'],
            seed=evidence['seed'], run_id=f'gefs-schema-rebind:{family}', output_root=output_root,
            source_feature_method=evidence['source_feature_method'])
        results.append(result)

    manifest = {
        'artifact_type': 'V11_GEFS_LIVE_SCHEMA_REBIND_ATTEMPT', 'tool_version': TOOL_VERSION,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'code_commit': code_commit, 'code_tree': code_tree,
        'source_model_id': SOURCE_MODEL_ID, 'live_model_id': LIVE_MODEL_ID,
        'inputs': {'research_result_sha256': digest(json.loads(args.research_result.read_bytes())),
                   'dataset_manifest_sha256': digest(json.loads(args.dataset_manifest.read_bytes())),
                   'preregistration_sha256': evidence['preregistration_sha256'],
                   'dataset_sha256': evidence['dataset_sha256']},
        'results': results,
        'overall_status': 'NO_BUNDLE_CREATED' if any(r['status'] != 'BUNDLE_CREATED' for r in results) else 'BUNDLE_CREATED',
        'financial_authority': False, 'promotion_authority': False,
        'no_promotion': True, 'not_host_approved': True, 'historical_research_only': True,
    }
    out_path = output_root / f'v11_gefs_live_schema_rebind_manifest_{datetime.now(timezone.utc):%Y%m%d}.json'
    out_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    out_path.chmod(0o600)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
