"""Public-source weather decisions, independent of delivery and financial authority.

Evidence clients never receive account credentials. Every execution revalidation
reacquires the contract and sources; a stored signal is a thesis to check, not proof.
Ensemble fractions are explicitly uncalibrated, and quoted baskets are conditional
on all legs filling. Source finality is never inferred from a polling interval.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import time
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from ..weather_only_clob import (
    CLOB, WeatherCLOBClient, conservative_taker_fee_per_share, parse_book, parse_market_info,
)
from ..weather_only_conditioned_wrh import build_wrh_observed_extreme_asof
from ..weather_only_conditioned_wrh_preimage import extract_wrh_official_observation_preimages
from ..weather_only_contract_strict import compile_strict_temperature_event, strict_contract_identity
from ..weather_only_contracts import DAILY_HIGH
from ..weather_only_discovery import GAMMA, WeatherOnlyDiscovery
from ..weather_only_forecast import (
    CELL_SELECTION_POLICY, OPEN_METEO_ENSEMBLE, OPEN_METEO_GEFS_MODEL, EnsembleMappingPolicy,
    map_ensemble_to_contract_buckets, parse_open_meteo_gefs_daily_extreme,
)
from ..weather_only_live_paper_all_signals import _friend_capture_gate
from ..weather_only_live_paper_all_signals_v8 import _excluded, _new_extreme_transition
from ..weather_only_paper_corrective import final_token_payout_v4
from ..weather_only_result_lag import evaluate_wrh_official_result_lag
from ..weather_only_rules import compile_temperature_rule_authority
from ..weather_only_same_day_capture import assemble_same_day_capture
from ..weather_only_same_day_contract import build_same_day_contract_semantics
from ..weather_only_structural import binary_pair_underround, complete_bucket_underround
from ..weather_only_three_layer_guarded import (
    GuardedNWSNearTermGridClient, GuardedNWSWRHLiveClient, GuardedOpenMeteoGEFSHourlyClient,
    GuardedSameDayStationMetadataClient, _bounded_async_json, _haversine_km,
)

SOURCE_TTL = 120.0
QUOTE_TTL = 20.0
STRATEGIES = frozenset({"DIRECTIONAL", "SAME_DAY", "SOURCE_SHOCK", "STRUCTURAL", "MAKER", "RESULT_LAG"})
MAPPING_POLICY = EnsembleMappingPolicy("PRODUCTION_RAW_GEFS_NEAREST_WHOLE_V1", True)


class WeatherUnavailable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _sha(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _number(value, code: str, *, minimum=0.0, maximum=math.inf) -> float:
    if isinstance(value, bool):
        raise WeatherUnavailable(code)
    try:
        out = float(value)
    except (ValueError, TypeError, OverflowError):
        raise WeatherUnavailable(code) from None
    if not math.isfinite(out) or not minimum <= out <= maximum:
        raise WeatherUnavailable(code)
    return out


def _array(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


class PublicWeatherSources(WeatherCLOBClient):
    """Bounded, credential-free GET requests, plus exact read-only CLOB composition."""
    def __init__(self):
        self.http = httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            headers={"User-Agent": "Alpha-weather-production/1 (+https://github.com/heroo2123/Alpha)",
                     "Accept-Encoding": "identity"})

    async def _get(self, url, params=None):
        return await _bounded_async_json(self.http, url, params=params,
            max_bytes=8*1024*1024, total_deadline_seconds=20,
            redirect_code="PUBLIC_REDIRECT_REJECTED", status_code="PUBLIC_HTTP_ERROR",
            encoding_code="PUBLIC_ENCODING_REJECTED", size_code="PUBLIC_RESPONSE_CAP",
            json_code="PUBLIC_JSON_INVALID", timeout_code="PUBLIC_TIMEOUT", transport_code="PUBLIC_TRANSPORT")

    async def event(self, event_id):
        body, _ = await self._get(f"{GAMMA}/events/{quote(str(event_id), safe='')}")
        if str(body.get("id") or "") != str(event_id):
            raise WeatherUnavailable("GAMMA_EVENT_IDENTITY_MISMATCH")
        return body

    async def market(self, market_id):
        body, _ = await self._get(f"{GAMMA}/markets/{quote(str(market_id), safe='')}")
        if str(body.get("id") or "") != str(market_id):
            raise WeatherUnavailable("GAMMA_MARKET_IDENTITY_MISMATCH")
        return body

    async def neg_risk(self, token):
        body, _ = await self._get(f"{CLOB}/neg-risk", {"token_id": token})
        if type(body.get("neg_risk")) is not bool:
            raise WeatherUnavailable("NEG_RISK_METADATA_MISSING")
        return body["neg_risk"]

    async def market_info(self, condition_id):
        body, received = await self._get(f"{CLOB}/clob-markets/{quote(str(condition_id), safe='')}")
        return parse_market_info(condition_id, body, received_at=received)

    async def books(self, token_ids):
        if len(token_ids) > 2000:
            raise WeatherUnavailable("TOKEN_CAP")
        sem = asyncio.Semaphore(8)
        async def one(token):
            async with sem:
                body, received = await self._get(f"{CLOB}/book", {"token_id": token})
                return token, parse_book(token, body, received_at=received)
        return dict(await asyncio.gather(*(one(token) for token in dict.fromkeys(token_ids))))

    async def daily_extreme(self, compiled, metadata):
        variable = "temperature_2m_max" if compiled.family == DAILY_HIGH else "temperature_2m_min"
        body, received = await self._get(OPEN_METEO_ENSEMBLE, {
            "latitude": metadata.latitude, "longitude": metadata.longitude, "daily": variable,
            "models": OPEN_METEO_GEFS_MODEL, "temperature_unit": "fahrenheit" if compiled.unit == "F" else "celsius",
            "timezone": metadata.timezone, "start_date": compiled.target_date.isoformat(),
            "end_date": compiled.target_date.isoformat(), "cell_selection": CELL_SELECTION_POLICY})
        result = parse_open_meteo_gefs_daily_extreme(body, station=compiled.station_hint,
            target_date=compiled.target_date, family=compiled.family, unit=compiled.unit,
            timezone=metadata.timezone, requested_latitude=metadata.latitude,
            requested_longitude=metadata.longitude, received_at=received)
        if _haversine_km(metadata.latitude, metadata.longitude,
                         result.resolved_latitude, result.resolved_longitude) > 50:
            raise WeatherUnavailable("FORECAST_GRID_TOO_FAR")
        return result


class WeatherPipeline:
    def __init__(self, *, public=None, discovery=None, metadata=None, wrh=None,
                 near_term=None, hourly=None, clock=None):
        self.public = public if public is not None else PublicWeatherSources()
        self.discovery = discovery if discovery is not None else WeatherOnlyDiscovery()
        self.metadata = metadata if metadata is not None else GuardedSameDayStationMetadataClient()
        self.wrh = wrh if wrh is not None else GuardedNWSWRHLiveClient()
        self.near_term = near_term if near_term is not None else GuardedNWSNearTermGridClient()
        self.hourly = hourly if hourly is not None else GuardedOpenMeteoGEFSHourlyClient()
        self.clock = clock if clock is not None else time.time
        self.last_rejections: list[dict] = []
        self._previous_wrh = {}
        self._evaluation_lock = asyncio.Lock()

    async def close(self):
        for client in (self.public, self.discovery, self.metadata, self.wrh, self.near_term, self.hourly):
            result = client.close()
            if inspect.isawaitable(result):
                await result

    async def discover(self):
        result = await self.discovery.discover()
        status = {**result.summary(), **self.discovery.global_recall_status()}
        return {"events": list(result.events), "status": status}

    def _fresh(self, timestamp, ttl, code):
        age = self.clock() - _number(timestamp, code)
        if age < -2 or age > ttl:
            raise WeatherUnavailable(code)

    def _reject(self, event_id, strategy, exc):
        # Never include arbitrary exception text or HTTP request bodies in status.
        self.last_rejections.append({"event_id": str(event_id)[:100], "strategy": strategy,
                                     "code": str(getattr(exc, "code", type(exc).__name__))[:180]})
        self.last_rejections = self.last_rejections[-200:]

    async def evaluate(self, event, strategies, min_model_gap, min_structural_edge):
        if not set(strategies) <= STRATEGIES:
            raise WeatherUnavailable("STRATEGY_UNSUPPORTED")
        policy = {"min_model_gap": _number(min_model_gap, "MODEL_GAP_INVALID", maximum=1),
                  "min_structural_edge": _number(min_structural_edge, "STRUCTURAL_EDGE_INVALID", maximum=1)}
        event_id = str(event.get("id") or "")
        async with self._evaluation_lock:
            try:
                fresh = await self.public.event(event_id)
                return await self._evaluate(fresh, tuple(strategies), policy)
            except Exception as exc:
                self._reject(event_id, "CONTRACT", exc)
                return []

    async def _evaluate(self, event, strategies, policy):
        compiled = compile_strict_temperature_event(event)
        if event.get("closed") is True or event.get("active") is False or not all(b.trade_open for b in compiled.buckets):
            raise WeatherUnavailable("CONTRACT_NOT_OPEN")
        if len(compiled.buckets) > 100:
            raise WeatherUnavailable("CONTRACT_BUCKET_CAP")
        semantic = strict_contract_identity(event, compiled)
        # The semantic key also binds all token/condition identities and bounds.
        semantic_hash = _sha({"contract": semantic, "buckets": [
            [b.market_id, b.condition_id, b.yes_token, b.no_token, b.lower, b.upper] for b in compiled.buckets]})
        metadata = await self.metadata.station(compiled.station_hint)
        if metadata is None or metadata.station != compiled.station_hint:
            raise WeatherUnavailable("STATION_IDENTITY_MISMATCH")
        self._fresh(metadata.received_at, SOURCE_TTL, "STATION_METADATA_STALE")
        local = datetime.fromtimestamp(self.clock(), ZoneInfo(metadata.timezone))
        tomorrow = datetime.combine(local.date()+timedelta(days=1), datetime.min.time(), ZoneInfo(metadata.timezone)).timestamp()
        out = []
        for strategy in sorted(strategies):
            try:
                evidence, choices = await self._strategy(strategy, event, compiled, metadata, semantic_hash, local, policy)
                # Acquire all quotes after the weather walk, never before it.
                exact = await self.public.exact_event_snapshot(compiled)
                self._validate_quotes(compiled, exact)
                if strategy == "STRUCTURAL":
                    opportunities = binary_pair_underround(compiled, exact.books, exact.parameters,
                        min_profit_per_set=policy["min_structural_edge"])
                    basket = complete_bucket_underround(compiled, exact.books, exact.parameters,
                        min_profit_per_set=policy["min_structural_edge"])
                    if basket is not None:
                        opportunities.append(basket)
                    choices = [{"tokens": list(item.token_ids), "theoretical_payout": 1.0,
                                "structure": item.lane} for item in opportunities]
                for choice in choices:
                    try:
                        legs = [await self._leg(event, compiled, exact, token, maker=strategy=="MAKER")
                                for token in choice["tokens"]]
                        total = sum(leg["price"]+leg["fee"] for leg in legs)
                        if "model_frequency" in choice and choice["model_frequency"]-total < policy["min_model_gap"]:
                            continue
                        if strategy == "SAME_DAY" and not .90 <= legs[0]["price"] <= .975:
                            continue
                        if strategy in {"SAME_DAY", "SOURCE_SHOCK"} and 1-total < .015:
                            continue
                        if strategy in {"STRUCTURAL", "RESULT_LAG"} and choice["theoretical_payout"]-total <= policy["min_structural_edge"]:
                            continue
                        now = self.clock()
                        self._fresh(exact.started_at, QUOTE_TTL, "QUOTE_EXPIRED")
                        for stamp in evidence["source_receipts"]:
                            self._fresh(stamp, SOURCE_TTL, "WEATHER_SOURCE_EXPIRED")
                        expires = min(min(evidence["source_receipts"])+SOURCE_TTL,
                                      exact.started_at+QUOTE_TTL,
                                      tomorrow-5 if strategy in {"SAME_DAY", "SOURCE_SHOCK"} else math.inf)
                        if expires <= now:
                            raise WeatherUnavailable("DECISION_EVIDENCE_EXPIRED")
                        payload = {"strategy": strategy, "event_id": compiled.event_id,
                            "event_url": "https://polymarket.com/event/"+quote(str(event.get("slug") or compiled.event_id), safe=""),
                            "title": str(event.get("title") or "")[:400],
                            "station_day": f"{compiled.station_hint}:{compiled.target_date.isoformat()}",
                            "semantic_hash": semantic_hash, "evidence_hash": _sha(evidence), "evidence": evidence,
                            "created": now, "expires": expires, "legs": legs, "policy": policy,
                            "quote_received": exact.finished_at,
                            "uncalibrated": strategy not in {"STRUCTURAL", "RESULT_LAG"},
                            **{key: val for key, val in choice.items() if key != "tokens"}}
                        payload["key"] = _sha([strategy, semantic_hash, choice["tokens"]])
                        payload["id"] = _sha(payload)
                        out.append(payload)
                    except Exception as exc:
                        self._reject(compiled.event_id, strategy, exc)
                if not choices:
                    self._reject(compiled.event_id, strategy, WeatherUnavailable("NO_SUPPORTED_OPPORTUNITY"))
            except Exception as exc:
                self._reject(compiled.event_id, strategy, exc)
        return out

    async def _wrh_snapshot(self, compiled, metadata):
        result = await asyncio.to_thread(self.wrh.fetch_snapshot, station=compiled.station_hint, target_date=compiled.target_date)
        self._fresh(result.snapshot.received_at, SOURCE_TTL, "WRH_SOURCE_STALE")
        if result.snapshot.station != compiled.station_hint or result.snapshot.target_date != compiled.target_date or result.snapshot.timezone != metadata.timezone:
            raise WeatherUnavailable("WRH_CONTRACT_IDENTITY_MISMATCH")
        return result.snapshot

    async def _strategy(self, strategy, event, compiled, metadata, semantic_hash, local, policy):
        evidence = {"station": metadata.as_dict(), "source_receipts": [metadata.received_at],
                    "semantic_hash": semantic_hash, "calibrated_probability": False}
        if strategy == "STRUCTURAL":
            evidence["claim"] = "Quoted complete set; payout requires all legs filled and final resolution."
            return evidence, []
        if strategy in {"DIRECTIONAL", "MAKER"}:
            horizon = (compiled.target_date-local.date()).days
            if not 1 <= horizon <= 3:
                raise WeatherUnavailable("FORECAST_NOT_FUTURE_LOCAL_DAY_WITHIN_3_DAYS")
            distribution = await self.public.daily_extreme(compiled, metadata)
            self._fresh(distribution.received_at, SOURCE_TTL, "FORECAST_SOURCE_STALE")
            mapped = map_ensemble_to_contract_buckets(compiled, distribution, MAPPING_POLICY)
            evidence.update({"distribution": distribution.as_dict(), "mapping": mapped.as_dict(),
                             "provider_run_age_known": False})
            evidence["source_receipts"].append(distribution.received_at)
            return evidence, [{"tokens": [token], "model_frequency": frequency, "theoretical_payout": 1.0}
                for row in mapped.bucket_frequencies
                for token, frequency in ((row.yes_token, row.raw_member_frequency), (row.no_token, row.raw_no_frequency))]
        if strategy in {"SAME_DAY", "SOURCE_SHOCK"} and compiled.target_date != local.date():
            raise WeatherUnavailable("NOT_TARGET_LOCAL_DAY")
        snapshot = await self._wrh_snapshot(compiled, metadata)
        evidence["source_receipts"].append(snapshot.received_at)
        if strategy == "RESULT_LAG":
            previous = self._previous_wrh.get(semantic_hash)
            self._previous_wrh[semantic_hash] = snapshot
            if len(self._previous_wrh) > 512:
                self._previous_wrh.pop(next(iter(self._previous_wrh)))
            if previous is None:
                raise WeatherUnavailable("RESULT_LAG_BASELINE_REQUIRED")
            candidate, _ = await evaluate_wrh_official_result_lag(event, previous, snapshot, clob=self.public)
            # The current WRH adapter cannot prove exact first-publication state.
            # Its real finality evaluator rejects that limitation; never override it.
            if candidate is None:
                raise WeatherUnavailable("RESULT_LAG_NO_CONFIRMED_OPPORTUNITY")
            evidence["finality"] = candidate.as_dict()
            return evidence, [{"tokens": [candidate.token_id], "theoretical_payout": candidate.payout_per_share}]
        as_of = self.clock()
        if strategy == "SOURCE_SHOCK":
            layer = build_wrh_observed_extreme_asof(snapshot, compiled, as_of=as_of,
                                                   max_snapshot_age_seconds=SOURCE_TTL)
            observations = extract_wrh_official_observation_preimages(snapshot, compiled, as_of=as_of, evidence=layer)
            capture = {"as_of": as_of, "official_observations": [row.as_dict() for row in observations],
                       "observed_state": layer.observed_state.as_dict()}
            transition = _new_extreme_transition(capture, compiled.family)
            evidence.update({"wrh": snapshot.as_dict(), "observation": capture, "revision_sensitive": True})
            if transition is None:
                raise WeatherUnavailable("NO_NEW_OFFICIAL_EXTREME")
            previous, current, observed_at = transition
            evidence["transition"] = [previous, current, observed_at]
            return evidence, [{"tokens": [bucket.no_token], "theoretical_payout": 1.0,
                               "revision_sensitive": True} for bucket in compiled.buckets
                              if not _excluded(bucket, previous, compiled.family) and _excluded(bucket, current, compiled.family)]
        near, hourly = await asyncio.gather(
            self.near_term.fetch_snapshot(station=compiled.station_hint, latitude=metadata.latitude, longitude=metadata.longitude),
            self.hourly.target_day(station=compiled.station_hint, latitude=metadata.latitude, longitude=metadata.longitude,
                target_date=compiled.target_date, unit=compiled.unit, timezone=metadata.timezone))
        evidence["source_receipts"].extend((near.received_at, hourly.received_at))
        semantics = build_same_day_contract_semantics(compiled, compile_temperature_rule_authority(event, compiled))
        capture = assemble_same_day_capture(compiled=compiled, contract_semantics=semantics, station_metadata=metadata,
            wrh_snapshot=snapshot, near_term_raw_snapshot=near, hourly_gefs=hourly,
            as_of=self.clock(), population_alignment_certified=False).as_dict()
        evidence.update({"capture": capture, "population_alignment_certified": False,
                         "claim": "Heuristic agreement of WRH, NWS and GEFS; uncalibrated and revision-sensitive."})
        choices = []
        for bucket in compiled.buckets:
            gate = _friend_capture_gate(capture, compiled, bucket)
            if gate:
                choices.append({"tokens": [bucket.yes_token], "model_frequency": gate["raw_support"],
                                "theoretical_payout": 1.0, "same_day_gate": gate})
        return evidence, choices

    def _validate_quotes(self, compiled, exact):
        if exact.event_id != compiled.event_id or exact.exact_clob is not True:
            raise WeatherUnavailable("EXACT_QUOTE_IDENTITY_MISMATCH")
        self._fresh(exact.started_at, QUOTE_TTL, "QUOTE_EXPIRED")
        expected = {token for bucket in compiled.buckets for token in (bucket.yes_token, bucket.no_token)}
        if set(exact.books) != expected:
            raise WeatherUnavailable("EXACT_QUOTE_TOKEN_SET_MISMATCH")
        for bucket in compiled.buckets:
            info = exact.parameters.get(bucket.condition_id)
            if info is None or info.condition_id != bucket.condition_id:
                raise WeatherUnavailable("EXACT_QUOTE_CONDITION_MISMATCH")
            labels = {token: label.lower() for token, label in info.token_outcomes}
            if labels != {bucket.yes_token: "yes", bucket.no_token: "no"}:
                raise WeatherUnavailable("EXACT_QUOTE_OUTCOME_MISMATCH")
            self._fresh(info.received_at, QUOTE_TTL, "MARKET_PARAMETERS_STALE")
        for token, book in exact.books.items():
            if token != book.token_id or not book.book_hash:
                raise WeatherUnavailable("EXACT_BOOK_IDENTITY_MISSING")
            self._fresh(book.received_at, QUOTE_TTL, "BOOK_RECEIPT_STALE")
            provider = _number(book.timestamp, "BOOK_PROVIDER_TIMESTAMP_INVALID")
            if provider >= 100_000_000_000:
                provider /= 1000
            if abs(book.received_at-provider) > QUOTE_TTL:
                raise WeatherUnavailable("BOOK_PROVIDER_TIMESTAMP_STALE")

    async def _leg(self, event, compiled, exact, token, *, maker):
        bucket = next(b for b in compiled.buckets if token in (b.yes_token, b.no_token))
        book, params = exact.books[token], exact.parameters[bucket.condition_id]
        if params.fee_rate > 0 and params.taker_only is not True:
            raise WeatherUnavailable("FEE_SEMANTICS_UNSUPPORTED")
        price = book.best_bid if maker else book.best_ask
        price = _number(price, "PRICE_UNAVAILABLE", minimum=.000001, maximum=.999999)
        tick = _number(params.minimum_tick_size, "TICK_INVALID", minimum=.000001, maximum=1)
        if Decimal(str(price)) % Decimal(str(tick)) != 0:
            raise WeatherUnavailable("PRICE_OFF_TICK")
        if maker and (book.best_ask is None or price >= book.best_ask):
            raise WeatherUnavailable("MAKER_POST_ONLY_PRICE_UNAVAILABLE")
        available = _number(book.best_ask_size, "LIQUIDITY_UNAVAILABLE")
        minimum = _number(params.minimum_order_size, "MIN_SIZE_INVALID", minimum=.000001)
        if available < minimum:
            raise WeatherUnavailable("BELOW_MINIMUM_LIQUIDITY")
        raw = next(row for row in event["markets"] if str(row.get("id")) == bucket.market_id)
        tokens = [str(value) for value in _array(raw.get("clobTokenIds"))]
        labels = [str(value).lower() for value in _array(raw.get("outcomes"))]
        if len(tokens) != 2 or len(labels) != 2 or token not in tokens:
            raise WeatherUnavailable("GAMMA_OUTCOME_INDEX_UNPROVEN")
        index = tokens.index(token)
        side = "YES" if token == bucket.yes_token else "NO"
        if labels[index] != side.lower():
            raise WeatherUnavailable("GAMMA_OUTCOME_INDEX_MISMATCH")
        neg_risk = await self.public.neg_risk(token)
        if type(neg_risk) is not bool:
            raise WeatherUnavailable("NEG_RISK_METADATA_MISSING")
        if type(raw.get("negRisk")) is bool and raw["negRisk"] != neg_risk:
            raise WeatherUnavailable("NEG_RISK_IDENTITY_CONFLICT")
        fee = 0.0 if maker else conservative_taker_fee_per_share(price, params.fee_rate, params.fee_exponent)
        return {"token": token, "condition": bucket.condition_id, "market_id": bucket.market_id,
            "side": side, "price": price, "fee": fee, "available": available, "min_size": minimum,
            "tick": tick, "neg_risk": neg_risk, "outcome_index": index,
            "fee_rate": params.fee_rate, "fee_exponent": params.fee_exponent,
            "post_only": maker, "book_hash": book.book_hash}

    async def revalidate(self, candidate):
        if candidate.get("id") != _sha({key: value for key, value in candidate.items() if key != "id"}):
            raise WeatherUnavailable("CANDIDATE_IDENTITY_CHANGED")
        if self.clock() >= _number(candidate.get("expires"), "CANDIDATE_EXPIRY_INVALID"):
            raise WeatherUnavailable("CANDIDATE_EXPIRED")
        async with self._evaluation_lock:
            event = await self.public.event(candidate["event_id"])
            refreshed = await self._evaluate(event, (candidate["strategy"],), candidate["policy"])
        for row in refreshed:
            if row["key"] != candidate["key"]:
                continue
            if row["semantic_hash"] != candidate["semantic_hash"]:
                raise WeatherUnavailable("CONTRACT_SEMANTICS_CHANGED")
            identity = lambda leg: (leg["token"], leg["condition"], leg["market_id"], leg["side"], leg["neg_risk"], leg["outcome_index"])
            if [identity(leg) for leg in row["legs"]] != [identity(leg) for leg in candidate["legs"]]:
                raise WeatherUnavailable("EXECUTION_LEG_IDENTITY_CHANGED")
            if row.get("model_frequency", 0)+1e-12 < candidate.get("model_frequency", 0):
                raise WeatherUnavailable("WEATHER_SUPPORT_DETERIORATED")
            row["expires"] = min(row["expires"], candidate["expires"])
            if self.clock() >= row["expires"]:
                raise WeatherUnavailable("CANDIDATE_EXPIRED_DURING_REFRESH")
            row["id"] = _sha({key: value for key, value in row.items() if key != "id"})
            return row
        raise WeatherUnavailable("THESIS_NO_LONGER_SUPPORTED")

    async def outcome(self, candidate):
        legs, receipts = [], []
        for leg in candidate["legs"]:
            market = await self.public.market(leg["market_id"])
            payout = final_token_payout_v4(leg["token"], market,
                expected_condition_id=leg["condition"], expected_side=leg["side"])
            if payout is None:
                return None
            # The legacy helper permits absent labels. Production requires them.
            tokens = [str(value) for value in _array(market.get("clobTokenIds"))]
            labels = [str(value).lower() for value in _array(market.get("outcomes"))]
            index = leg["outcome_index"]
            if len(labels) != 2 or index not in (0, 1) or tokens[index] != leg["token"] or labels[index] != leg["side"].lower():
                raise WeatherUnavailable("SETTLEMENT_OUTCOME_IDENTITY_CHANGED")
            legs.append({key: leg[key] for key in ("token", "condition", "market_id", "side")}|{"payout": payout})
            receipts.append({"market": leg["market_id"], "condition": leg["condition"],
                             "tokens": tokens, "labels": labels, "payouts": _array(market.get("outcomePrices")),
                             "resolution": market.get("umaResolutionStatus") or market.get("uma_resolution_status")})
        return {"payout_per_unit": sum(leg["payout"] for leg in legs), "legs": legs,
                "observed_at": self.clock(), "evidence_hash": _sha(receipts), "source": "GAMMA_FINAL_RESOLUTION"}
