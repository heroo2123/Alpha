from __future__ import annotations

"""Frozen uncalibrated value-reference policy for weather maker PAPER research.

Raw GEFS member frequency is not a calibrated probability and this module does not
turn it into one.  The lower value used by the passive-bid simulator is only a fixed,
versioned research stress: remove three supporting members from an exact 31-member
mapped ensemble.  This is deliberately simple and prospectively testable.  It is not
a confidence interval, a statistical lower bound, settlement authority, or financial
advice.

The purpose of the policy is to let the PAPER bot collect honest prospective maker
outcomes without inventing a hidden tuning rule.  Any later calibration must use a new
version and must not rewrite historical signals.
"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass

from .weather_only_forecast import GEFS_TOTAL_MEMBERS
from .weather_only_maker import FairValueBand
from .weather_only_maker_shadow import MakerShadowPolicy


MAKER_RESEARCH_POLICY_VERSION = "weather_maker_gefs31_minus3_uncalibrated_v1"
MAKER_EXPECTED_MEMBERS = GEFS_TOTAL_MEMBERS
MAKER_STRESS_MEMBER_VOTES = 3
MAKER_MINIMUM_EDGE_PER_SHARE = 0.05
MAKER_MAX_FAIR_AGE_SECONDS = 900.0
MAKER_MAX_ORDER_AGE_SECONDS = 300.0
MAKER_MAX_BOOK_AGE_SECONDS = 10.0
MAKER_MAX_EVENTS_PER_CYCLE = 2
MAKER_MAX_ACTIVE_ORDERS = 8
MAKER_TRADE_FEED_PAGE_SIZE = 200
MAKER_TRADE_FEED_MAX_PAGES_PER_ORDER_CYCLE = 2


class MakerResearchPolicyError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise MakerResearchPolicyError("MAKER_RESEARCH_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MakerResearchPolicy:
    version: str = MAKER_RESEARCH_POLICY_VERSION
    expected_members: int = MAKER_EXPECTED_MEMBERS
    stress_member_votes: int = MAKER_STRESS_MEMBER_VOTES
    minimum_edge_per_share: float = MAKER_MINIMUM_EDGE_PER_SHARE
    max_fair_age_seconds: float = MAKER_MAX_FAIR_AGE_SECONDS
    max_order_age_seconds: float = MAKER_MAX_ORDER_AGE_SECONDS
    max_book_age_seconds: float = MAKER_MAX_BOOK_AGE_SECONDS
    max_events_per_cycle: int = MAKER_MAX_EVENTS_PER_CYCLE
    max_active_orders: int = MAKER_MAX_ACTIVE_ORDERS
    trade_feed_page_size: int = MAKER_TRADE_FEED_PAGE_SIZE
    trade_feed_max_pages_per_order_cycle: int = MAKER_TRADE_FEED_MAX_PAGES_PER_ORDER_CYCLE

    def __post_init__(self) -> None:
        expected = {
            "version": MAKER_RESEARCH_POLICY_VERSION,
            "expected_members": MAKER_EXPECTED_MEMBERS,
            "stress_member_votes": MAKER_STRESS_MEMBER_VOTES,
            "minimum_edge_per_share": MAKER_MINIMUM_EDGE_PER_SHARE,
            "max_fair_age_seconds": MAKER_MAX_FAIR_AGE_SECONDS,
            "max_order_age_seconds": MAKER_MAX_ORDER_AGE_SECONDS,
            "max_book_age_seconds": MAKER_MAX_BOOK_AGE_SECONDS,
            "max_events_per_cycle": MAKER_MAX_EVENTS_PER_CYCLE,
            "max_active_orders": MAKER_MAX_ACTIVE_ORDERS,
            "trade_feed_page_size": MAKER_TRADE_FEED_PAGE_SIZE,
            "trade_feed_max_pages_per_order_cycle": MAKER_TRADE_FEED_MAX_PAGES_PER_ORDER_CYCLE,
        }
        if asdict(self) != expected:
            raise MakerResearchPolicyError("MAKER_RESEARCH_POLICY_DRIFT")
        if self.expected_members != 31 or not 0 < self.stress_member_votes < self.expected_members:
            raise MakerResearchPolicyError("MAKER_RESEARCH_POLICY_INVALID")
        if not 0.0 < self.minimum_edge_per_share < 1.0:
            raise MakerResearchPolicyError("MAKER_RESEARCH_POLICY_INVALID")
        if min(self.max_fair_age_seconds, self.max_order_age_seconds, self.max_book_age_seconds) <= 0.0:
            raise MakerResearchPolicyError("MAKER_RESEARCH_POLICY_INVALID")
        if min(
            self.max_events_per_cycle,
            self.max_active_orders,
            self.trade_feed_page_size,
            self.trade_feed_max_pages_per_order_cycle,
        ) <= 0:
            raise MakerResearchPolicyError("MAKER_RESEARCH_POLICY_INVALID")

    @property
    def policy_sha256(self) -> str:
        return _sha(asdict(self))

    def shadow_policy(self) -> MakerShadowPolicy:
        return MakerShadowPolicy(
            policy_id=self.version,
            minimum_edge_per_share=self.minimum_edge_per_share,
            max_fair_age_seconds=self.max_fair_age_seconds,
            max_order_age_seconds=self.max_order_age_seconds,
            max_book_age_seconds=self.max_book_age_seconds,
        )


def build_uncalibrated_gefs_fair_band(
    *,
    token_id: str,
    member_hits: int,
    member_count: int,
    as_of: float,
    source_evidence_sha256: str,
    policy: MakerResearchPolicy | None = None,
) -> FairValueBand:
    frozen = policy or MakerResearchPolicy()
    if not isinstance(frozen, MakerResearchPolicy):
        raise MakerResearchPolicyError("MAKER_RESEARCH_POLICY_TYPE_INVALID")
    token = str(token_id or "").strip()
    if not token:
        raise MakerResearchPolicyError("MAKER_RESEARCH_TOKEN_MISSING")
    if isinstance(member_hits, bool) or not isinstance(member_hits, int):
        raise MakerResearchPolicyError("MAKER_RESEARCH_MEMBER_HITS_INVALID")
    if isinstance(member_count, bool) or not isinstance(member_count, int):
        raise MakerResearchPolicyError("MAKER_RESEARCH_MEMBER_COUNT_INVALID")
    if member_count != frozen.expected_members:
        raise MakerResearchPolicyError("MAKER_RESEARCH_MEMBER_COUNT_MISMATCH")
    if not 0 <= member_hits <= member_count:
        raise MakerResearchPolicyError("MAKER_RESEARCH_MEMBER_HITS_INVALID")
    if isinstance(as_of, bool) or not isinstance(as_of, (int, float)):
        raise MakerResearchPolicyError("MAKER_RESEARCH_AS_OF_INVALID")
    cutoff = float(as_of)
    if not math.isfinite(cutoff) or cutoff < 0.0:
        raise MakerResearchPolicyError("MAKER_RESEARCH_AS_OF_INVALID")
    evidence = str(source_evidence_sha256 or "").strip().lower()
    if len(evidence) != 64 or any(ch not in "0123456789abcdef" for ch in evidence):
        raise MakerResearchPolicyError("MAKER_RESEARCH_SOURCE_EVIDENCE_INVALID")

    lower_hits = max(0, member_hits - frozen.stress_member_votes)
    upper_hits = min(member_count, member_hits + frozen.stress_member_votes)
    lower = lower_hits / member_count
    point = member_hits / member_count
    upper = upper_hits / member_count
    model_version = (
        f"{frozen.version}|source={evidence}|stress={frozen.stress_member_votes}_of_{member_count}"
    )
    fair = FairValueBand(
        token_id=token,
        lower=lower,
        point=point,
        upper=upper,
        model_version=model_version,
        as_of=cutoff,
        calibrated=False,
    )
    if fair.calibrated:
        raise MakerResearchPolicyError("MAKER_RESEARCH_CALIBRATION_AUTHORITY_BROKEN")
    return fair


def fair_band_research_metadata(fair: FairValueBand, policy: MakerResearchPolicy) -> dict:
    if not isinstance(fair, FairValueBand) or not isinstance(policy, MakerResearchPolicy):
        raise MakerResearchPolicyError("MAKER_RESEARCH_METADATA_INPUT_INVALID")
    return {
        "policy_version": policy.version,
        "policy_sha256": policy.policy_sha256,
        "member_count": policy.expected_members,
        "stress_member_votes": policy.stress_member_votes,
        "fair_lower_reference": float(fair.lower),
        "raw_member_frequency": float(fair.point),
        "fair_upper_reference": float(fair.upper),
        "calibrated_probability": False,
        "confidence_interval": False,
        "statistical_lower_bound": False,
        "settlement_authority": False,
        "financial_authority": False,
    }
