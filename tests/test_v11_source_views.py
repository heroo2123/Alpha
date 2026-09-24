import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.gefs_sources import PROVIDER
from test_v11_gefs_sources import gefs,field,raw


def view(r,rows,**kw):
    return r['store'].source_batch(**dict(dict(kind='MODEL',event_id=r['plan'].event_id,provider=PROVIDER,
        record_ids=tuple(row['id'] for row in rows),source_identities=tuple(row['body']['source_identity'] for row in rows)),**kw))


def test_exact_source_view_retains_revision_order_and_raw_identity_without_mutation(gefs):
    r=gefs;a=field(r,0,r['plan'].hours[0]);new=raw(r,label='newer-receipt');pin=r['store'].pin_read_view()
    snapshot=view(r,(a,),raw_lineage=True)
    assert snapshot['head']==new and snapshot['records'][a['id']]==a
    assert snapshot['channels'][a['body']['source_identity']]==new
    assert snapshot['records'][a['body']['payload']['raw_evidence_id']]['sha256']==a['body']['payload']['raw_evidence_sha256']
    assert r['store'].pin_read_view()==pin


@pytest.mark.parametrize('changes',[dict(record_ids=()),dict(record_ids=('x',)*901),dict(source_identities=()),
    dict(source_identities=('x',)*513),dict(raw_lineage=1),dict(kind='OPERATOR_EVENT')])
def test_batch_read_bounds_do_not_relax_existing_decision_limits(gefs,changes):
    r=gefs;a=field(r,0,r['plan'].hours[0]);pin=r['store'].pin_read_view()
    with pytest.raises(EvidenceError,match='SOURCE_VIEW_BOUND'):view(r,(a,),**changes)
    assert pin==r['store'].pin_read_view() and r['store'].limits.max_evidence_per_decision==64


def test_missing_or_expired_source_view_fails_closed(gefs):
    r=gefs;a=field(r,0,r['plan'].hours[0]);pin=r['store'].pin_read_view()
    with pytest.raises(EvidenceError,match='TIME_BOUND'):view(r,(a,),deadline=0.)
    with pytest.raises(EvidenceError,match='EVIDENCE_MISSING'):view(r,(a,),record_ids=('missing',))
    assert pin==r['store'].pin_read_view()
