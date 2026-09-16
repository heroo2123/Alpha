"""Explicit production configuration. Financial limits have no implicit defaults."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re


STRATEGIES = frozenset({"DIRECTIONAL", "SAME_DAY", "SOURCE_SHOCK", "STRUCTURAL", "MAKER", "RESULT_LAG"})
MODES = frozenset({"LIVE_SIGNALS", "LIVE_EXECUTION", "SIMULATION"})
FEE_POLICIES = frozenset({"ONCHAIN_BOUND", "EXCHANGE_PUBLISHED_SCHEDULE"})


class ConfigurationError(ValueError):
    """Codes contain field names only, never supplied values or credentials."""


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decimal(value: object, field: str, *, positive: bool = True) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ConfigurationError(f"INVALID_NUMBER:{field}")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ConfigurationError(f"INVALID_NUMBER:{field}") from None
    if not number.is_finite() or (number <= 0 if positive else number < 0):
        raise ConfigurationError(f"INVALID_NUMBER:{field}")
    return number


def required(raw: dict, key: str):
    if key not in raw or raw[key] in (None, ""):
        raise ConfigurationError(f"MISSING_SETTING:{key}")
    return raw[key]


def address(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise ConfigurationError(f"INVALID_ADDRESS:{field}")
    return value.lower()


@dataclass(frozen=True)
class RiskLimits:
    capital: Decimal
    per_order: Decimal
    per_station_day: Decimal
    max_loss: Decimal
    daily_loss: Decimal
    max_price: Decimal
    max_slippage: Decimal
    max_fee_per_share: Decimal
    legging_loss: Decimal
    max_open_orders: int
    max_positions: int
    max_maker_rest_seconds: int

    @classmethod
    def parse(cls, raw: dict) -> "RiskLimits":
        if not isinstance(raw, dict):
            raise ConfigurationError("MISSING_SETTING:risk")
        values = {key: decimal(required(raw, key), "risk." + key, positive=key not in {"max_slippage", "max_fee_per_share"})
                  for key in ("capital", "per_order", "per_station_day", "max_loss", "daily_loss", "max_price", "max_slippage", "max_fee_per_share", "legging_loss")}
        if not values["max_price"] < 1 or values["max_slippage"] >= 1 or values["max_fee_per_share"] >= 1:
            raise ConfigurationError("RISK_PRICE_OR_FEE_RANGE")
        if not values["per_order"] <= values["per_station_day"] <= values["capital"]:
            raise ConfigurationError("RISK_EXPOSURE_ORDERING")
        if any(values[k] > values["capital"] for k in ("max_loss", "daily_loss", "legging_loss")):
            raise ConfigurationError("RISK_LOSS_EXCEEDS_CAPITAL")
        for key, low, high in (("max_open_orders", 1, 100), ("max_positions", 1, 1000), ("max_maker_rest_seconds", 181, 3600)):
            value = required(raw, key)
            if type(value) is not int or not low <= value <= high:
                raise ConfigurationError("RISK_INTEGER_RANGE:" + key)
            values[key] = value
        return cls(**values)


@dataclass(frozen=True)
class ProductionConfig:
    mode: str
    signal_db: Path
    status_path: Path
    strategies: frozenset[str]
    min_model_gap: Decimal
    min_structural_edge: Decimal
    allow_uncalibrated: bool
    execution_db: Path | None
    credentials_file: Path | None
    wallet: str | None
    signer: str | None
    activation_file: Path | None
    stop_file: Path | None
    risk: RiskLimits | None
    config_sha256: str
    telegram_file: Path | None = None
    execution_status_path: Path | None = None
    rpc_url: str | None = None
    source_path: Path | None = None
    fee_policy: str | None = None

    @classmethod
    def parse(cls, raw: dict) -> "ProductionConfig":
        if not isinstance(raw, dict):
            raise ConfigurationError("CONFIG_OBJECT_REQUIRED")
        mode = required(raw, "mode")
        if not isinstance(mode, str) or mode not in MODES:
            raise ConfigurationError("INVALID_MODE")
        paths = {}
        names = ["signal_db", "status_path"]
        for key in ("telegram_file", "execution_status_path"):
            if key in raw:
                names.append(key)
        if mode == "LIVE_EXECUTION":
            names += ["execution_db", "credentials_file", "activation_file", "stop_file"]
        for key in names:
            value = required(raw, key)
            if not isinstance(value, str) or not Path(value).is_absolute() or ".." in Path(value).parts:
                raise ConfigurationError("ABSOLUTE_PATH_REQUIRED:" + key)
            paths[key] = Path(value).resolve()
        if len(set(paths.values())) != len(paths):
            raise ConfigurationError("CONFIG_PATHS_MUST_BE_DISTINCT")
        if paths["status_path"].parent != paths["signal_db"].parent:
            raise ConfigurationError("SIGNAL_STATUS_MUST_SHARE_SIGNAL_STATE_DIRECTORY")
        if mode == "LIVE_EXECUTION":
            if paths["execution_db"].parent == paths["signal_db"].parent:
                raise ConfigurationError("EXECUTION_AND_SIGNALS_REQUIRE_DISTINCT_STATE_DIRECTORIES")
            if "execution_status_path" in paths and paths["execution_status_path"].parent != paths["execution_db"].parent:
                raise ConfigurationError("EXECUTION_STATUS_MUST_SHARE_EXECUTION_STATE_DIRECTORY")
        selected = required(raw, "strategies")
        if not isinstance(selected, list) or not selected or any(not isinstance(s, str) or s not in STRATEGIES for s in selected):
            raise ConfigurationError("INVALID_STRATEGIES")
        allow = required(raw, "allow_uncalibrated")
        if type(allow) is not bool:
            raise ConfigurationError("INVALID_BOOLEAN:allow_uncalibrated")
        if set(selected) & {"DIRECTIONAL", "SAME_DAY", "SOURCE_SHOCK", "MAKER"} and not allow:
            raise ConfigurationError("UNCALIBRATED_STRATEGY_REQUIRES_EXPLICIT_ACKNOWLEDGEMENT")
        wallet = signer = risk = fee_policy = None
        if mode == "LIVE_EXECUTION":
            fee_policy = required(raw, "fee_policy")
            if not isinstance(fee_policy, str) or fee_policy not in FEE_POLICIES:
                raise ConfigurationError("INVALID_FEE_POLICY")
            wallet = address(required(raw, "wallet"), "wallet")
            signer = address(required(raw, "signer"), "signer")
            if signer != wallet:
                raise ConfigurationError("ONLY_EXPLICIT_EOA_ACCOUNT_SUPPORTED")
            risk = RiskLimits.parse(required(raw, "risk"))
            if "STRUCTURAL" in selected and raw.get("partial_basket_policy") != "SEQUENTIAL_FAK_FULL_RESERVATION_STOP_ON_KNOWN_FAILURE":
                raise ConfigurationError("STRUCTURAL_PARTIAL_FILL_POLICY_REQUIRED")
        rpc = raw.get("rpc_url")
        if mode == "LIVE_EXECUTION":
            from urllib.parse import urlsplit
            parts = urlsplit(str(required(raw, "rpc_url")))
            if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
                raise ConfigurationError("INVALID_RPC_URL")
            if "execution_status_path" not in paths:
                raise ConfigurationError("MISSING_SETTING:execution_status_path")
        for key in ("min_model_gap", "min_structural_edge"):
            if decimal(required(raw, key), key) >= 1:
                raise ConfigurationError("GAP_RANGE:" + key)
        return cls(mode, paths["signal_db"], paths["status_path"], frozenset(selected),
                   decimal(required(raw, "min_model_gap"), "min_model_gap"),
                   decimal(required(raw, "min_structural_edge"), "min_structural_edge"), allow,
                   paths.get("execution_db"), paths.get("credentials_file"), wallet, signer,
                   paths.get("activation_file"), paths.get("stop_file"), risk, digest(raw),
                   paths.get("telegram_file"), paths.get("execution_status_path"), rpc,
                   fee_policy=fee_policy)

    @classmethod
    def load(cls, path: Path) -> "ProductionConfig":
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            raise ConfigurationError("CONFIG_READ_FAILED") from None
        return replace(cls.parse(raw), source_path=path.resolve())

    def activation_requested(self) -> bool:
        if self.mode != "LIVE_EXECUTION" or self.activation_file is None or self.stop_file is None:
            return False
        if self.stop_file.exists():
            return False
        if self.source_path is not None:
            try:
                if digest(json.loads(self.source_path.read_text())) != self.config_sha256:
                    return False
            except (OSError, ValueError, TypeError):
                return False
        try:
            st = self.activation_file.lstat()
            if self.activation_file.is_symlink() or st.st_mode & 0o022 or st.st_uid not in {0, os.geteuid()}:
                return False
            activation = json.loads(self.activation_file.read_text())
        except (OSError, ValueError):
            return False
        return activation == {"action": "ACTIVATE_LIVE_EXECUTION", "wallet": self.wallet,
                              "config_sha256": self.config_sha256}
