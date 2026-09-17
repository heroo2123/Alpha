"""Wallet adapters fail closed; Deposit Wallet support is CLOB-session-only."""
import pytest
from eth_account import Account
from test_production_lifecycle import config
from polymarket_scanner.production.config import ConfigurationError

SESSION = Account.from_key((2).to_bytes(32, "big")).address
OWNER = Account.from_key((3).to_bytes(32, "big")).address
DEPOSIT = "0x" + "33" * 20


def deposit_config(tmp_path, **overrides):
    values = dict(wallet=DEPOSIT, signer=SESSION, deposit_owner=OWNER, wallet_type="DEPOSIT_WALLET",
        signature_type=3, session_scopes=["CLOB"], session_valid_until=4_000_000_000,
        session_exclusive_until=3_900_000_000)
    values.update(overrides)
    return config(tmp_path, **values)


@pytest.mark.parametrize('overrides',[
    {'wallet_type':'SAFE'}, {'wallet_type':'PROXY'}, {'wallet_type':None},
    {'signature_type':3}, {'signature_type':False}, {'session_signer':'0x'+'34'*20},
    {'session_key':'fixture-only-not-a-key'}, {'session_scopes':['CLOB']},
    {'withdrawal_disabled':True}, {'deposit_owner':OWNER},
])
def test_unsupported_or_misplaced_wallet_request_fails_closed(tmp_path, overrides):
    with pytest.raises(ConfigurationError):
        config(tmp_path, **overrides)


def test_deposit_wallet_requires_explicit_clob_only_session(tmp_path):
    cfg = deposit_config(tmp_path)
    assert cfg.wallet == DEPOSIT.lower()
    assert cfg.signer == SESSION.lower()
    assert cfg.wallet_type == "DEPOSIT_WALLET"
    assert cfg.deposit_owner == OWNER.lower()
    assert cfg.signature_type == 3
    assert cfg.session_scopes == ("CLOB",)
    assert cfg.session_exclusive_until < cfg.session_valid_until


@pytest.mark.parametrize('overrides',[
    {'session_scopes':['ALL']}, {'session_scopes':['CLOB','COMBOSRFQ']},
    {'session_valid_until':0}, {'session_exclusive_until':4_100_000_000},
    {'signature_type':0}, {'signer':DEPOSIT}, {'deposit_owner':DEPOSIT}, {'deposit_owner':SESSION},
])
def test_deposit_wallet_rejects_broader_or_inconsistent_session(tmp_path, overrides):
    with pytest.raises(ConfigurationError):
        deposit_config(tmp_path, **overrides)


@pytest.mark.parametrize("field", ["owner_private_key", "builder_api_key", "builder_api_secret",
                                    "builder_api_passphrase", "mnemonic", "seed_phrase", "private_key"])
def test_live_execution_rejects_unknown_secret_like_top_level_fields(tmp_path, field):
    with pytest.raises(ConfigurationError, match="UNKNOWN_LIVE_EXECUTION_SETTING"):
        deposit_config(tmp_path, **{field:"must-not-be-stored-here"})


def test_explicit_eoa_remains_supported_without_false_restriction_claim(tmp_path):
    cfg=config(tmp_path,wallet_type='EOA',signature_type=0)
    assert cfg.wallet==cfg.signer
    assert cfg.wallet_type == 'EOA' and cfg.session_scopes == ()
    assert not cfg.activation_requested()


@pytest.mark.parametrize('overrides',[
    {'session_valid_until':float('inf')}, {'session_exclusive_until':float('inf')},
    {'session_valid_until':float('nan')}, {'session_exclusive_until':float('nan')},
    {'session_valid_until':True}, {'session_exclusive_until':False},
])
def test_deposit_wallet_rejects_nonfinite_or_boolean_expiry_metadata(tmp_path, overrides):
    with pytest.raises(ConfigurationError, match="DEPOSIT_SESSION_EXPIRY_INVALID"):
        deposit_config(tmp_path, **overrides)
