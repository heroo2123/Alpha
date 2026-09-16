"""Explicit unsupported wallet requests must never fall back to an owner key."""
import pytest
from test_production_lifecycle import config
from polymarket_scanner.production.config import ConfigurationError

@pytest.mark.parametrize('overrides',[
    {'wallet_type':'DEPOSIT_WALLET'}, {'wallet_type':'SAFE'}, {'wallet_type':'PROXY'},
    {'wallet_type':None}, {'signature_type':3}, {'signature_type':False}, {'session_signer':'0x'+'34'*20},
    {'session_key':'fixture-only-not-a-key'}, {'session_scopes':['CLOB']},
    {'withdrawal_disabled':True},
])
def test_unsupported_request_fails_before_credentials_or_network(tmp_path,overrides):
    with pytest.raises(ConfigurationError,match='UNSUPPORTED|NOT_IMPLEMENTED|NOT_WITHDRAWAL_RESTRICTED'):
        config(tmp_path,**overrides)

def test_explicit_eoa_does_not_claim_withdrawal_restriction(tmp_path):
    cfg=config(tmp_path,wallet_type='EOA',signature_type=0)
    assert cfg.wallet==cfg.signer
    assert not cfg.activation_requested()
