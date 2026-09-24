"""One declared numeric contract for forecast inference, capture and research.

Member counts, identities, units and quantization are part of the immutable
schema. Constructing research artifacts here neither selects an active bundle
nor attests calibration, source truth or station eligibility.
"""
from dataclasses import asdict, dataclass
import json

from ..weather_only_contracts import DAILY_HIGH, DAILY_LOW
from .datasets import FeatureDefinition, FeatureSchema
from .evidence import EvidenceError, canonical, digest, identity
from .probability import MODEL_QUANTIZATION


@dataclass(frozen=True)
class ForecastFeatureContract:
    model_widths: tuple[tuple[str, int], ...]
    unit: str
    family: str
    quantization: str = MODEL_QUANTIZATION

    def __post_init__(self):
        if (type(self.model_widths) is not tuple or not 1 <= len(self.model_widths) <= 16
                or any(type(row) is not tuple or len(row) != 2 for row in self.model_widths)):
            raise EvidenceError('FORECAST_FEATURE_MODEL_SHAPE')
        for model, width in self.model_widths:
            identity(model)
            if type(width) is not int or not 1 <= width <= 126:
                raise EvidenceError('FORECAST_FEATURE_MEMBER_BOUND')
        if (len({m for m, _ in self.model_widths}) != len(self.model_widths)
                or sum(n for _, n in self.model_widths) + 2 > 128):
            raise EvidenceError('FORECAST_FEATURE_MODEL_SET_OR_BOUND')
        if self.unit not in {'C', 'F'} or self.family not in {DAILY_HIGH, DAILY_LOW}:
            raise EvidenceError('FORECAST_FEATURE_UNIT_OR_FAMILY')
        if self.quantization != MODEL_QUANTIZATION:
            raise EvidenceError('FORECAST_FEATURE_QUANTIZATION_UNSUPPORTED')
        object.__setattr__(self, 'model_widths', tuple(sorted(self.model_widths)))

    @property
    def mapping(self):
        return {model: [f'model_{i}_member_{j:03}' for j in range(width)]
                for i, (model, width) in enumerate(self.model_widths)}

    @property
    def schema(self):
        features = [FeatureDefinition(name, self.unit, 'forecast_members', -250., 250., False)
                    for names in self.mapping.values() for name in names]
        features.extend(FeatureDefinition(name, self.unit, 'contract_quantization', -251., 251., True)
                        for name in ('lower_cut', 'upper_cut'))
        # Retain the original v1 capture schema identity for identical inputs.
        version = 'forecast-cuts:' + digest([self.mapping, self.unit, self.family, self.quantization])
        return FeatureSchema(version, tuple(features))

    def require_bundle(self, pinned):
        from .model_artifacts import PinnedBundle
        if not isinstance(pinned, PinnedBundle):
            raise EvidenceError('FORECAST_FEATURE_PINNED_BUNDLE_REQUIRED')
        value = pinned.payload
        models = value['components']['PROBABILITY']['parameters']['models']
        if (value['bundle']['target'] != 'FINAL_CONTRACT_PAYOUT'
                or value['bundle']['feature_schema_sha256'] != self.schema.sha256
                or {m['model_id'] for m in models} != set(self.mapping)
                or value['components']['FEATURES']['parameters'] != json.loads(canonical(asdict(self.schema)))):
            raise EvidenceError('FORECAST_FEATURE_PARENT_CONTRACT_MISMATCH')
        return value


def build_initial_forecast_bundle(artifacts, *, contract, probability_parameters, provenance, quality_modifiers):
    """Write a new, explicit INITIAL_NO_FIT research bundle; never adapt a parent."""
    from .model_artifacts import ARTIFACT_VERSION, validate_artifact
    if not isinstance(contract, ForecastFeatureContract) or provenance.get('training_status') != 'INITIAL_NO_FIT':
        raise EvidenceError('FORECAST_INITIAL_RESEARCH_CONTRACT_REQUIRED')
    if provenance.get('parent_bundle_sha256') is not None:
        raise EvidenceError('FORECAST_INITIAL_BUNDLE_HAS_NO_PARENT')
    parameters = {
        'FEATURES': json.loads(canonical(asdict(contract.schema))),
        'PROBABILITY': probability_parameters,
        'EXECUTION_COST': {'method': 'NO_EMPIRICAL_EXECUTION_MODEL', 'additional_cost_per_share': None,
                           'evidence_class': 'UNKNOWN'},
        'STRATEGY_QUALITY': {'method': 'FIXED_REDUCTION_ONLY', 'modifiers': quality_modifiers},
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
    if {m['model_id'] for m in probability_parameters['models']} != set(contract.mapping):
        raise EvidenceError('FORECAST_FEATURE_PARENT_CONTRACT_MISMATCH')
    refs = {kind: artifacts.put_artifact(value) for kind, value in values.items()}
    return artifacts.put_bundle(artifacts=refs, target='FINAL_CONTRACT_PAYOUT',
                                feature_schema_sha256=contract.schema.sha256)
