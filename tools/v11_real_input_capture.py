"""Bounded, source-only commissioning. Default preflight opens no network/store.

Config: rule, official, quality, books, collateral_asset, valid_until,
official_max_age_seconds, review: exact serialized RealInputPlan fields.
Collect requires an independently reviewed config digest and a dedicated 0700
state directory. Repeat with a new cycle ID on a >=300-second user timer; the
persistent provider schedule still applies across restarts. Never reset a held
ledger to retry a provider. There is deliberately no unattended hold-clear flag.
"""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_scanner.v11.book_inputs import BookPolicy
from polymarket_scanner.v11.certification import StationMetadata
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, digest
from polymarket_scanner.v11.pws_quality import PWSPolicy
from polymarket_scanner.v11.real_input_capture import InputReview, RealInputPlan, RealInputCapture
from polymarket_scanner.v11.rules import RuleFingerprint


def load_plan(path):
    from polymarket_scanner.v11.model_artifacts import parse_data
    with Path(path).open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise EvidenceError('REAL_INPUT_CONFIG_BOUND')
    value = parse_data(raw)
    official = dict(value.pop('official'))
    for name in ('observation_providers', 'forecast_providers'):
        official[name] = tuple(official[name])
    quality = dict(value.pop('quality'))
    quality['distance_bands'] = tuple(tuple(b) for b in quality['distance_bands'])
    return RealInputPlan(rule=RuleFingerprint(**value.pop('rule')), official=StationMetadata(**official),
                         quality=PWSPolicy(**quality), books=BookPolicy(**value.pop('books')),
                         review=InputReview(**value.pop('review')), **value)


async def collect(plan, store_path, cycle):
    import httpx
    from polymarket_scanner.v11.collection import PublicCollector
    from polymarket_scanner.v11.observation_runtime import ScheduledCollector
    from polymarket_scanner.v11.runtime_health import RuntimeHealth, HealthPolicy, SourceNeed
    store = EvidenceStore(Path(store_path), 'CHALLENGER:real-input-capture')
    event = plan.rule.payload['event_id']
    health = RuntimeHealth(store, HealthPolicy('real-input-v1', 30., 30., 2., 2, .05, ('capture',)),
        account_id='source-only-no-account', scopes={event: ('PWS_OBSERVATION_LEAD',)},
        sources=(SourceNeed(event, 'PWS_OBSERVATION_LEAD', 'PWS_OBSERVATION',
                            'ALPHA_PWS_QC', plan.official.station, plan.quality.fresh_seconds),))
    health.sample('input-bootstrap:'+digest(cycle))
    await asyncio.sleep(.06)
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        worker = RealInputCapture(ScheduledCollector(PublicCollector(store, client, attempts=1)), health, plan)
        return await worker.step(cycle)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--reviewed-config-sha256')
    parser.add_argument('--store')
    parser.add_argument('--cycle')
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.config)
        result = plan.preflight(time.time())
        if args.collect:
            if (args.reviewed_config_sha256 != digest(asdict(plan)) or not args.store or not args.cycle):
                raise EvidenceError('REAL_INPUT_REVIEWED_CONFIG_STORE_CYCLE_REQUIRED')
            row = asyncio.run(collect(plan, args.store, args.cycle))
            result = dict(row['body']['details'], record_id=row['id'], recorded_at=row['body']['recorded_at'])
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get('outcome') in (None, 'FRESH_SOURCE_EVIDENCE_ONLY') else 2
    except (EvidenceError, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps(dict(outcome='GATED', reason=str(exc), financial_authority=False,
                              acceptance_granted=False), sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
