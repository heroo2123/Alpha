"""Canonical production CLI. Signing dependencies load only in execution commands."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import time

from .config import ConfigurationError, ProductionConfig
from .io import atomic_json, clean_startup, lease
from .signals import SignalError


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("component", choices=("signals", "execution", "preflight", "recover", "record-redemption", "export", "activation-request", "resume-openings", "import-legacy"))
    result.add_argument("--config", required=True, type=Path)
    result.add_argument("--once", action="store_true")
    result.add_argument("--expected-fault")
    result.add_argument("--expected-stop-generation")
    result.add_argument("--transaction")
    result.add_argument("--condition")
    result.add_argument("--output", type=Path)
    result.add_argument("--legacy-db", type=Path)
    result.add_argument("--legacy-chat-id")
    result.add_argument("--legacy-bot-id")
    return result


def review_output(args, config):
    """Exports/review requests must never overwrite an operational input or DB."""
    if args.output is None or not args.output.is_absolute():
        raise ConfigurationError("ABSOLUTE_REVIEW_OUTPUT_REQUIRED")
    output = args.output.resolve()
    protected = {args.config.resolve()}
    for key in ("signal_db", "execution_db", "status_path", "execution_status_path", "credentials_file",
                "activation_file", "stop_file", "telegram_file"):
        path = getattr(config, key)
        if path:
            protected.update({path.resolve(), Path(str(path) + "-wal"), Path(str(path) + "-shm"), Path(str(path) + ".writer.lock")})
    protected.add(config.signal_db.parent / "weather-paper-runtime.lock")
    if output in protected or any(output.exists() and path.exists() and output.samefile(path) for path in protected):
        raise ConfigurationError("REVIEW_OUTPUT_ALIASES_OPERATIONAL_FILE")
    # Explicitly refuse all existing destinations, including link races at the
    # operator review boundary; the CLI never needs in-place export replacement.
    if args.output.is_symlink() or output.exists():
        raise ConfigurationError("REVIEW_OUTPUT_ALREADY_EXISTS")
    return output


async def run(args):
    clean_startup()
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    logging.getLogger("httpcore").setLevel(logging.CRITICAL)
    config = ProductionConfig.load(args.config)
    if args.component == "import-legacy":
        if not config.telegram_file or not args.legacy_db or not args.legacy_db.is_absolute() or not args.legacy_chat_id or not args.legacy_bot_id:
            raise ConfigurationError("LEGACY_IMPORT_REQUIRES_ABSOLUTE_DB_AND_ORIGINAL_CHAT_AND_BOT_IDS")
        from .legacy import import_legacy
        from .signals import SignalStore
        from .telegram import Telegram
        with lease(config.signal_db.parent / "weather-paper-runtime.lock"):
            telegram = Telegram(config.telegram_file)
            store = SignalStore(config.signal_db)
            try:
                result = import_legacy(store, args.legacy_db, identity=telegram.delivery_identity,
                    expected_identity={"bot_id": args.legacy_bot_id, "chat_id": args.legacy_chat_id})
                print(json.dumps(result, sort_keys=True))
            finally:
                store.close()
                await telegram.close()
        return
    if args.component == "resume-openings":
        if not config.activation_requested():
            raise ConfigurationError("CURRENT_CONFIGURATION_ACTIVATION_REQUIRED")
        if not args.expected_stop_generation:
            raise ConfigurationError("EXPECTED_STOP_GENERATION_REQUIRED")
        try:
            status = json.loads(config.execution_status_path.read_text())
            if (not 0 <= time.time() - status["updated_at"] < 30 or status.get("reconciled") is not True
                or status.get("config_sha256") != config.config_sha256 or status["account"]["wallet"] != config.wallet
                or status["account"].get("reconciliation_fault")):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            raise ConfigurationError("FRESH_RECONCILED_EXECUTION_STATUS_REQUIRED") from None
        from .signals import SignalStore
        store = SignalStore(config.signal_db)
        with store.transaction() as db:
            row = db.execute("SELECT value FROM live_state WHERE key='stop_generation'").fetchone()
            if (row[0] if row else "0") != args.expected_stop_generation:
                raise ConfigurationError("STOP_GENERATION_CHANGED")
            db.execute("DELETE FROM live_state WHERE key IN ('stop_opening','cancel_open')")
            store.audit(db, "LOCAL_OPERATOR_RESUME_REQUEST", config.wallet, {"config_sha256": config.config_sha256})
        return
    if config.mode == "SIMULATION":
        raise ConfigurationError("USE_DOCUMENTED_SEPARATE_SIMULATION_ENTRYPOINT")
    if args.component == "activation-request":
        if config.mode != "LIVE_EXECUTION":
            raise ConfigurationError("ACTIVATION_REVIEW_OUTPUT_REQUIRED_DISTINCT_FROM_ACTIVE_FILE")
        atomic_json(review_output(args, config), {"action": "ACTIVATE_LIVE_EXECUTION", "wallet": config.wallet, "config_sha256": config.config_sha256}, exclusive=True)
        return
    if args.component == "export":
        output = review_output(args, config)
        from .signals import SignalReader
        reader = SignalReader(config.signal_db)
        data = {"signals": reader._query("SELECT id,evidence,status,delivery,outcome FROM live_signals"), "account_performance": "NOT_INFERRED_FROM_SIGNALS"}
        data["excluded_legacy_research"] = reader._query("SELECT * FROM live_legacy_research")
        if config.execution_db:
            import sqlite3
            with sqlite3.connect(config.execution_db.as_uri() + "?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                # Never export signed requests or authentication material.
                data["actual"] = {table: [dict(r) for r in db.execute("SELECT * FROM " + table)] for table in ("execution_orders", "execution_fills", "execution_settlements", "execution_redemptions", "execution_burns", "execution_native_gas")}
        atomic_json(output, data, exclusive=True)
        return
    from .weather import WeatherPipeline
    weather = WeatherPipeline()
    if args.component == "signals":
        if not config.telegram_file:
            raise ConfigurationError("MISSING_SETTING:telegram_file")
        from .signals import SignalStore
        from .telegram import Telegram
        from .service import SignalService
        with lease(config.signal_db.parent / "weather-paper-runtime.lock"):
            telegram = Telegram(config.telegram_file)
            store = SignalStore(config.signal_db)
            try:
                await SignalService(config, store, telegram, weather).run(once=args.once)
            finally:
                store.close()
                await telegram.close()
                await weather.close()
        return
    if config.mode != "LIVE_EXECUTION":
        await weather.close()
        raise ConfigurationError("EXECUTION_MODE_REQUIRED")
    from .exchange import ExchangeEOA
    from .engine import ExecutionEngine
    from .ledger import ExecutionLedger
    from .signals import SignalReader
    with lease(Path(str(config.execution_db) + ".writer.lock")):
        exchange = ExchangeEOA.from_credentials_file(config.credentials_file, wallet=config.wallet, signer=config.signer, rpc_url=config.rpc_url)
        try:
            ledger = ExecutionLedger(config.execution_db, config.wallet)
            ledger.recover_after_restart()
            engine = ExecutionEngine(config, ledger, SignalReader(config.signal_db), exchange, weather)
            if args.component == "record-redemption":
                if not args.transaction or not args.condition:
                    raise ConfigurationError("REDEMPTION_TRANSACTION_AND_CONDITION_REQUIRED")
                records = await engine.call(exchange.redemption_receipt, args.transaction, args.condition)
                if not records:
                    raise ConfigurationError("REDEMPTION_NOT_CONFIRMED")
                for record in records:
                    ledger.record_redemption(record)
                await engine.reconcile()
            elif args.component == "recover":
                if not args.expected_fault:
                    raise ConfigurationError("EXPECTED_FAULT_REQUIRED")
                await engine.reconcile(ignore_sticky_fault=True)
                if not engine.reconciled:
                    raise ConfigurationError("RECOVERY_RECONCILIATION_INCOMPLETE")
                ledger.clear_fault(args.expected_fault)
            elif args.component == "preflight":
                await engine.reconcile()
                if not engine.reconciled:
                    raise ConfigurationError("PREFLIGHT_RECONCILIATION_INCOMPLETE")
            else:
                while True:
                    atomic_json(config.execution_status_path, await engine.tick(), mode=0o640)
                    if args.once:
                        break
                    await asyncio.sleep(5)
            atomic_json(config.execution_status_path, engine.status(), mode=0o640)
        finally:
            exchange.close()
            await weather.close()


def main():
    try:
        asyncio.run(run(parser().parse_args()))
    except KeyboardInterrupt:
        return
    except (ConfigurationError, SignalError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        # Untrusted API payloads/credential exceptions never reach process logs.
        print("PRODUCTION_COMPONENT_FAILED_CLOSED", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
