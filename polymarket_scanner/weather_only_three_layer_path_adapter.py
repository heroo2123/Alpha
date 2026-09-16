from __future__ import annotations

"""Path-aware entrypoint for the same-day three-layer research assembler.

The core assembler accepts the deliberately small ``VerifiedNearTermCoverage`` type.
This adapter requires the richer timestamped Layer-2 path, verifies it, reduces its
sampled model path under the frozen sampling hypothesis, and only then invokes the
core assembler.  This gives live/replay code one canonical route that cannot replace a
future segment with a single current PWS reading.

The output remains research-only and same-day delivery remains disabled.
"""

from .weather_only_conditioned_extremes import ObservedExtremeState
from .weather_only_conditioned_paths import VerifiedRemainingHoursPath
from .weather_only_contracts import CompiledWeatherEvent
from .weather_only_forecast import EnsembleMappingPolicy
from .weather_only_near_term_path import (
    VerifiedNearTermPath,
    reduce_sample_path_to_research_coverage,
    verify_near_term_path_integrity,
)
from .weather_only_three_layer import (
    ThreeLayerResearchDecision,
    build_three_layer_research_decision,
)
from .weather_only_unresolved_coverage import UnresolvedCoveragePlan


class ThreeLayerPathAdapterError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def build_three_layer_from_near_term_path(
    compiled: CompiledWeatherEvent,
    observed: ObservedExtremeState,
    coverage: UnresolvedCoveragePlan,
    near_term_path: VerifiedNearTermPath,
    ensemble_path: VerifiedRemainingHoursPath,
    mapping_policy: EnsembleMappingPolicy,
    *,
    as_of: float,
) -> ThreeLayerResearchDecision:
    try:
        path = verify_near_term_path_integrity(near_term_path)
        reduced = reduce_sample_path_to_research_coverage(path)
        return build_three_layer_research_decision(
            compiled,
            observed,
            coverage,
            reduced,
            ensemble_path,
            mapping_policy,
            as_of=as_of,
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise ThreeLayerPathAdapterError(f"THREE_LAYER_PATH_ADAPTER:{code}") from exc
