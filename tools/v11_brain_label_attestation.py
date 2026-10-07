from __future__ import annotations
from pathlib import Path
import json, os, re, time
from polymarket_scanner.v11.evidence import EvidenceStore
from polymarket_scanner.v11.label_attestation import attest_resolved_day

BASE = Path('/home/alphaadmin/AlphaV11_BrainForward')
START_DAY = '2026-10-05'
NAMESPACE = 'CHALLENGER:katl-shadow'


def captured_days(base):
    return tuple(sorted(
        p.name for p in base.iterdir()
        if p.is_dir()
        and re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.name)
        and p.name >= START_DAY
        and (p / 'capture-status.json').exists()
    ))


def resolved_days(base):
    return tuple(d for d in captured_days(base) if (base / d / 'labels-status.json').exists())


def attest_day(base, day, *, now):
    root = base / day
    store = EvidenceStore(root / 'source.sqlite', NAMESPACE)
    cs = json.loads((root / 'capture-status.json').read_text())
    ls = json.loads((root / 'labels-status.json').read_text())
    capture_record = store.get(cs['capture_id'])
    event_id = capture_record['event_id']
    rule_state = store.latest(kind='RULE_STATE', event_id=event_id)
    if rule_state is None:
        return {'day': day, 'event_id': event_id, 'state': 'ATTESTATION_BLOCKED_RULE_FINGERPRINT_MISSING'}
    details = rule_state['body']['details']
    if details.get('quarantined'):
        return {'day': day, 'event_id': event_id, 'state': 'ATTESTATION_BLOCKED_RULE_QUARANTINED'}
    rule_fingerprint_payload = details['preimage']
    label_records = {mid: store.get(lid) for mid, lid in ls['label_ids'].items()}
    station = rule_fingerprint_payload['station']
    official_records = store.records(kind='OFFICIAL_OBSERVATION', event_id='station:' + station)
    result = attest_resolved_day(rule_fingerprint_payload=rule_fingerprint_payload,
                                 label_records=label_records,
                                 official_observation_records=official_records, now=now)
    return {'day': day, 'event_id': event_id, **result}


def run(base=BASE, *, now=None):
    now = time.time() if now is None else now
    days = resolved_days(base)
    if not days:
        raise SystemExit('WAITING_FORWARD_LABELS')
    out = {'version': 'alpha_v11_brain_label_attestation_v1',
           'days': [attest_day(base, day, now=now) for day in days],
           'financial_authority': False, 'automatic_promotion': False}
    (base / 'label-attestation-status.json').write_text(json.dumps(out, indent=2, sort_keys=True) + '\n')
    os.chmod(base / 'label-attestation-status.json', 0o600)
    print(json.dumps(out, sort_keys=True))
    return out


if __name__ == '__main__':
    run(BASE)
