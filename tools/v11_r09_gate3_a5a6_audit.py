"""Read-only historical A5/A6 inventory; no decoder, transport or authority.

Prints observations to stdout. Header hashes derived here are diagnostics,
never independently frozen launch pins. Does not read forecast values.
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
import subprocess


REPO = Path(__file__).resolve().parents[1]
ROOT = Path('/home/alphaadmin/AlphaV11_R09Extrema/Alpha/private-evidence/r09-extrema')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(path):
    raw = path.read_bytes()
    return dict(path=str(path), sha256=sha(raw), byte_length=len(raw))


def headers(raw):
    assert raw[:4] == b'GRIB' and raw[7] == 2 and raw[-4:] == b'7777'
    assert int.from_bytes(raw[8:16], 'big') == len(raw)
    sections = {}
    offset = 16
    while offset < len(raw) - 4:
        size = int.from_bytes(raw[offset:offset + 4], 'big')
        number = raw[offset + 4]
        assert size >= 5 and offset + size <= len(raw) - 4 and number not in sections
        sections[number] = raw[offset:offset + size]
        offset += size
    assert offset == len(raw) - 4
    assert list(sections) in ([1, 3, 4, 5, 6, 7], [1, 2, 3, 4, 5, 6, 7])
    ident, grid, product, packing = (sections[n] for n in (1, 3, 4, 5))
    u = lambda b, a, z: int.from_bytes(b[a:z], 'big')
    template = u(product, 7, 9)
    run = datetime(u(ident, 12, 14), *ident[14:19], tzinfo=timezone.utc)
    return dict(run_utc=run.isoformat(), centre=u(ident, 5, 7),
                subcentre=u(ident, 7, 9), tables=ident[9], local_tables=ident[10],
                status=ident[19], data_type=ident[20], product_template=template,
                generating_process_type=product[11], generating_process_id=product[13],
                parameter_category=product[9], parameter_number=product[10],
                level_type=product[22], level_scale=product[23], level_value=u(product, 24, 28),
                member=product[35] if template in (1, 11) else None,
                ensemble_type=product[34] if template in (1, 11) else None,
                ensemble_size=product[36] if template in (1, 11) else None,
                forecast_unit=product[17], forecast_hour=u(product, 18, 22),
                grid_template=u(grid, 12, 14), ni=u(grid, 30, 34), nj=u(grid, 34, 38),
                point_count=u(grid, 6, 10), packing_template=u(packing, 9, 11),
                bitmap_indicator=sections[6][5],
                diagnostic_section_hashes={str(n): sha(b) for n, b in sections.items() if n <= 6},
                independently_frozen_section_pins=None)


def main():
    public_path = REPO / 'config/v11/r09_ecmwf_extrema_public_evidence.json'
    public = json.loads(public_path.read_bytes())
    captures, restrictions = [], []
    for export in public['captures']:
        folder = ROOT / export['capture']
        raw = (folder / 'capture.json').read_bytes()
        assert sha(raw) == export['capture_sha256']
        capture = json.loads(raw)
        responses = [r['response'] for r in capture['indexes'] + capture['fields']]
        responses.append(capture['listing'])
        retained = [r for r in responses if r.get('sha256')]
        for r in retained:
            body = (folder / 'objects' / r['sha256']).read_bytes()
            assert sha(body) == r['sha256'] and len(body) == r['bytes']
        receipts = sorted((folder / 'receipts').glob('*.json'))
        for path in receipts:
            assert json.loads(path.read_bytes()) in retained
        fields = []
        for item in capture['fields']:
            r = item['response']
            field = (folder / 'objects' / r['sha256']).read_bytes()
            idx = (folder / 'objects' / item['index_sha256']).read_bytes()
            assert sha(idx) == item['index_sha256']
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', r['headers']['content-range'])
            assert match and r['status'] == 206
            start, end, total = map(int, match.groups())
            assert end - start + 1 == len(field) and end < total
            rows = [json.loads(line) for line in idx.splitlines()]
            selected = [row for row in rows if row['_offset'] == start and row['_length'] == len(field)]
            assert len(selected) == 1
            row = selected[0]
            req = item['request']
            assert row['param'] == req['param'] and int(row.get('number', 0)) == req['member']
            assert int(row['step']) == req['product']['step']
            fields.append(dict(raw_sha256=r['sha256'], byte_length=len(field),
                               index_sha256=item['index_sha256'], selected_row=row,
                               object_path=r['path'], object_bytes=total,
                               recorded_etag=r['headers'].get('etag'),
                               received_at=r['received_at'], request=req, headers=headers(field)))
        for r in retained:
            if r['status'] in (429, 503):
                restrictions.append(dict(capture=export['capture'], response=r,
                                         expiry_adjudication=None))
        captures.append(dict(capture=export['capture'], manifest=pin(folder / 'capture.json'),
                             verified_body_count=len(retained), verified_body_bytes=sum(r['bytes'] for r in retained),
                             retained_receipts=[pin(p) for p in receipts],
                             status_counts=export['index_status_counts'], fields=fields,
                             operational_release_reviewed=capture['operational_release_reviewed'],
                             release_binding=capture['release_binding']))
    loose = []
    initial = json.loads((ROOT / 'initial-metadata.json').read_bytes())
    for item in initial:
        path = ROOT / item['file']
        raw = path.read_bytes()
        assert sha(raw) == item['sha256']
        index_name = item['file'].rsplit('-', 2)[0] + '.index'
        index = (ROOT / index_name).read_bytes()
        assert sha(index) == item['index_sha256']
        assert item['row'] in [json.loads(line) for line in index.splitlines()]
        index_check = pin(ROOT / index_name)
        loose.append(dict(file=pin(path), selected_row=item['row'], index=index_check,
                          headers=headers(raw), response_receipt=None,
                          scope='HISTORICAL_LOOSE_EXPLORATORY_BYTES'))
    earlier = json.loads((ROOT / 'aws-retry.json').read_bytes())
    assert sha((ROOT / 'aws-retry-body').read_bytes()) == earlier['sha256']
    restrictions.insert(0, dict(capture='earlier-loose-aws-retry', response=earlier,
                               expiry_adjudication=None))
    inputs = [public_path, ROOT / 'initial-metadata.json', ROOT / 'aws-retry.json',
              ROOT / 'aws-retry-body', Path(__file__).resolve(),
              Path('/home/alphaadmin/AlphaV11_R09ExtremaReview/review.log')]
    for name in ('IDENTITY_ACCEPTANCE_20261001.md', 'V4_PROVIDER_MAPPING_REPAIR_REVIEW_23c11e0.md',
                 'V4_PROVIDER_MAPPING_REPAIR_REVIEW_23c11e0_terminal.json',
                 'LAUNCH_CONTRACT_ADJUDICATION.md', 'DECODER_SOURCE_OFFLINE_ASSESSMENT_20261001.md'):
        inputs.append(REPO / 'docs' / ('V11_R09_GATE3_' + name))
    inputs += [REPO / p for p in ('tools/v11_r09_gate3_launch_v4.py',
               'tools/v11_r09_gate3_g3l_prep.py', 'tools/v11_ecmwf_extrema.py',
               'polymarket_scanner/v11/ecmwf_sources.py', 'polymarket_scanner/v11/ecmwf_grib.py')]
    keys = ('operational_release_dossier', 'release_document_retrieval', 'licence_anonymous_access',
            'control_perturbed_mapping', 'grib_identity_decoder_build', 'purpose_endpoint_contracts',
            'current_run_index_object_range', 'publication_attestation_or_absence_reason')
    result = dict(schema='R09_GATE3_A5A6_OFFLINE_AUDIT_V1',
                  observed_utc=datetime.now(timezone.utc).isoformat(),
                  inspected_main_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
                  scope='HISTORICAL_OBSERVATION_ONLY_NOT_SOURCE_OR_LAUNCH_QUALIFICATION',
                  launchable=False, g3l='NO_GO', inputs=[pin(p) for p in inputs],
                  captures=captures, loose_fields=loose, restrictions=restrictions,
                  qualified_source_identities={f'sources.{p}_{k}': None for p in ('gefs', 'ifs', 'aifs') for k in keys},
                  current_run_readiness=None, independent_section_pins=None,
                  bootstrap='OWNER_NO_PROVIDER_REQUEST_BEFORE_G3L_AND_MISSING_PRELAUNCH_CURRENT_RUN_PINS',
                  network_requests=0, native_decode_calls=0)
    mapping_report = REPO / 'docs/V11_R09_GATE3_V4_PROVIDER_MAPPING_REPAIR_REVIEW_23c11e0.md'
    mapping_terminal = json.loads((REPO / 'docs/V11_R09_GATE3_V4_PROVIDER_MAPPING_REPAIR_REVIEW_23c11e0_terminal.json').read_bytes())
    mapping_source = 'tools/v11_r09_gate3_launch_v4.py'
    accepted_source = subprocess.check_output(['git', 'show', '23c11e0:' + mapping_source], cwd=REPO)
    assert mapping_terminal['review_report_sha256'] == sha(mapping_report.read_bytes())
    assert accepted_source == (REPO / mapping_source).read_bytes()
    result['reusable_mapping_review'] = dict(candidate=mapping_terminal['candidate_commit'],
        tree=mapping_terminal['candidate_tree'], scope=mapping_terminal['scope'],
        report_matches_terminal=True, current_validator_matches_reviewed_bytes=True,
        validator_sha256=sha(accepted_source), grants_provider_qualification=False)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
