import pytest

from polymarket_scanner.v11.evidence import EvidenceStore
from polymarket_scanner.v11.event_risk import SafetyReductions
from polymarket_scanner.v11.operator_safety_router import (
    OperatorSafetyError, OperatorSafetyPolicy, OperatorSafetyRouter,
)


@pytest.fixture
def rig(tmp_path):
    tmp_path.chmod(0o700)
    now = [1000.]
    store = EvidenceStore(tmp_path/'evidence.sqlite', 'V11_PAPER', clock=lambda: now[0])
    policy = OperatorSafetyPolicy(account_id='account', operators=(111, 222),
        allowed_scopes=('ACCOUNT', 'CITY', 'STATION', 'EVENT'),
        allowed_actions=('CANCEL_AND_HALT', 'NO_NEW_ORDERS', 'QUARANTINE_STATION'))
    router = OperatorSafetyRouter(store, policy, clock=lambda: now[0])
    return store, now, router


def command(**overrides):
    base = dict(actor=111, scope='ACCOUNT', scope_id='account', action='CANCEL_AND_HALT',
                reason='operator_stop', created=1000., expires=1010.)
    base.update(overrides)
    return base


def no_reduction_recorded(store):
    return not store.records(kind='OPERATOR_EVENT', limit=1)


def test_policy_rejects_malformed_allowlists_and_ceilings():
    with pytest.raises(OperatorSafetyError, match='OPERATOR_ALLOWLIST_INVALID'):
        OperatorSafetyPolicy(account_id='a', operators=(1, 1), allowed_scopes=('ACCOUNT',),
                              allowed_actions=('CANCEL_AND_HALT',))
    with pytest.raises(OperatorSafetyError, match='ALLOWED_SCOPES_INVALID'):
        OperatorSafetyPolicy(account_id='a', operators=(1,), allowed_scopes=('PORTFOLIO',),
                              allowed_actions=('CANCEL_AND_HALT',))
    with pytest.raises(OperatorSafetyError, match='ALLOWED_ACTIONS_INVALID'):
        OperatorSafetyPolicy(account_id='a', operators=(1,), allowed_scopes=('ACCOUNT',),
                              allowed_actions=('RESTORE_NORMAL',))


def test_unauthorized_actor_is_rejected_and_creates_no_reduction(rig):
    store, now, router = rig
    with pytest.raises(OperatorSafetyError, match='OPERATOR_NOT_AUTHORIZED'):
        router.route('cmd1', **command(actor=999))
    assert no_reduction_recorded(store)


def test_scope_not_preauthorized_is_rejected(rig):
    store, now, _ = rig
    policy = OperatorSafetyPolicy(account_id='account', operators=(111,), allowed_scopes=('ACCOUNT',),
                                   allowed_actions=('CANCEL_AND_HALT',))
    router = OperatorSafetyRouter(store, policy, clock=lambda: now[0])
    with pytest.raises(OperatorSafetyError, match='SCOPE_NOT_PREAUTHORIZED'):
        router.route('cmd1', **command(scope='EVENT', scope_id='event', action='CANCEL_AND_HALT'))
    assert no_reduction_recorded(store)


def test_action_not_preauthorized_is_rejected(rig):
    store, now, router = rig
    with pytest.raises(OperatorSafetyError, match='ACTION_NOT_PREAUTHORIZED'):
        router.route('cmd1', **command(action='DISABLE_INVENTORY_OPERATIONS'))
    assert no_reduction_recorded(store)


def test_account_scope_must_match_configured_account(rig):
    store, now, router = rig
    with pytest.raises(OperatorSafetyError, match='ACCOUNT_SCOPE_MISMATCH'):
        router.route('cmd1', **command(scope_id='someone-elses-account'))
    assert no_reduction_recorded(store)


@pytest.mark.parametrize('overrides', [
    dict(created=1001., expires=1010.),  # created after now
    dict(created=1000., expires=999.),  # expires before now
    dict(created=1000., expires=1200.),  # window exceeds ceiling
])
def test_stale_backdated_or_overlong_command_windows_are_rejected(rig, overrides):
    store, now, router = rig
    with pytest.raises(OperatorSafetyError, match='COMMAND_EXPIRED_OR_BACKDATED'):
        router.route('cmd1', **command(**overrides))
    assert no_reduction_recorded(store)


def test_authorized_command_applies_reduction_and_is_idempotent_on_replay(rig):
    store, now, router = rig
    first = router.route('cmd1', **command())
    assert first['body']['details']['flags']['no_new_orders']
    assert first['body']['details']['cancellation_status'] == 'REQUESTED_NOT_CONFIRMED'
    now[0] += 1
    replay = router.route('cmd1', **command())
    assert replay == first
    assert len(store.records(kind='OPERATOR_EVENT', limit=10)) == 1


def test_replaying_command_id_with_different_parameters_conflicts(rig):
    store, now, router = rig
    router.route('cmd1', **command())
    with pytest.raises(Exception, match='REPLAY_REQUEST_CONFLICT'):
        router.route('cmd1', **command(action='NO_NEW_ORDERS'))


def test_station_scope_reduction_is_visible_to_the_existing_safety_view(rig):
    store, now, router = rig
    router.route('cmd-station', **command(scope='STATION', scope_id='KJFK', action='QUARANTINE_STATION'))
    from polymarket_scanner.v11.event_risk import EventContext
    context = EventContext('account', 'city', 'KJFK', 'event')
    flags = SafetyReductions(store).view(context)['flags']
    assert flags['quarantined'] and flags['manual_review']
