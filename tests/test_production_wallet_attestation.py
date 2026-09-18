"""Pinned public runtime/source fixtures; no fake code hashes or network IO."""
import json

import pytest

from polymarket_scanner.production.chain import ChainReader, ExchangeError, calldata
from polymarket_scanner.production.wallet_attestation import (
    attest_wallet, FACTORY, FACTORY_IMPLEMENTATION, BEACON, IMPLEMENTATION,
    FORWARDER, IMPLEMENTATION_SLOT, BEACON_SLOT, ZERO,
)
from test_production_deposit_session import SessionChain, OWNER, SESSION, DEPOSIT, session_client
from test_production_exchange import NOW, Wire, context, prepare


def attest(chain, **kwargs):
    return attest_wallet(chain, chain.wallet, OWNER, SESSION, clock=lambda: NOW,
        expected_proxy_type='NATIVE_BEACON' if chain.proxy == 'native_beacon' else 'UUPS_BEACON_FORWARDER', **kwargs)


@pytest.mark.parametrize('proxy', ['native_beacon', 'legacy_erc1967'])
def test_supported_proxy_and_real_runtime_control(proxy):
    chain = SessionChain(); chain.proxy = proxy
    if proxy == 'legacy_erc1967': chain.wallet = '0x29b58e89eb61dfa0497f562ada227c808ccbd61c'
    result = attest(chain)
    assert result['implementation'] == IMPLEMENTATION
    assert result['onchain_valid_until'] == NOW+10000


@pytest.mark.parametrize('target', [FACTORY, FACTORY_IMPLEMENTATION, BEACON, IMPLEMENTATION, FORWARDER])
@pytest.mark.parametrize('change', ['empty', 'drift'])
def test_actual_code_identity_is_required(target, change):
    chain = SessionChain(); chain.proxy = 'legacy_erc1967'
    original = chain.code
    chain.code = lambda address, **kw: (b'' if change == 'empty' else b'\xff') if address == target else original(address, **kw)
    with pytest.raises(ExchangeError, match='CODE_IDENTITY_DRIFT'):
        attest(chain)


@pytest.mark.parametrize('field,value,code', [
    ('pinned_impl', '0x'+'44'*20, 'BEACON_IMPLEMENTATION_DRIFT'),
    ('beacon_implementation', '0x'+'44'*20, 'BEACON_IMPLEMENTATION_DRIFT'),
    ('owner', '0x'+'44'*20, 'CURRENT_OWNER_MISMATCH'),
    ('pending_owner', OWNER, 'OWNERSHIP_HANDOVER_PENDING'),
    ('factory_impl', '0x'+'44'*20, 'FACTORY_IMPLEMENTATION_DRIFT'),
])
def test_proxy_owner_and_per_wallet_pin_negatives(field, value, code):
    chain = SessionChain(); setattr(chain, field, value)
    with pytest.raises(ExchangeError, match=code): attest(chain)


def test_pinned_same_reviewed_implementation_is_supported():
    chain = SessionChain(); chain.pinned_impl = IMPLEMENTATION
    assert attest(chain)['implementation'] == IMPLEMENTATION


@pytest.mark.parametrize('target,signature', [(FACTORY,'BEACON()'), (BEACON,'implementation()'), (DEPOSIT,'owner()')])
@pytest.mark.parametrize('malformed', ['padding','length'])
def test_strict_address_return(target, signature, malformed):
    chain = SessionChain(); original = chain.call
    def call(to, sig, types, values, **kwargs):
        raw = original(to, sig, types, values, **kwargs)
        if (to,sig) == (target,signature):
            return b'\x01'+raw[1:] if malformed == 'padding' else raw[1:]
        return raw
    chain.call = call
    with pytest.raises(ExchangeError, match='ADDRESS_EVIDENCE_MALFORMED'): attest(chain)


@pytest.mark.parametrize('part', ['immutable_owner','extra_byte','proxy_prefix'])
def test_wallet_exact_runtime_and_immutable_args(part):
    chain = SessionChain(); original = chain.code
    def code(to, **kw):
        raw = original(to, **kw)
        if to == DEPOSIT:
            return raw[:-1]+b'\xff' if part == 'immutable_owner' else raw+b'\x00' if part == 'extra_byte' else b'\xff'+raw[1:]
        return raw
    chain.code = code
    with pytest.raises(ExchangeError, match='RUNTIME_OR_IMMUTABLE_IDENTITY_MISMATCH'): attest(chain)


@pytest.mark.parametrize('target,slot,value,code', [
    (FACTORY,BEACON_SLOT,BEACON,'FACTORY_PROXY_UNSUPPORTED'),
    (DEPOSIT,IMPLEMENTATION_SLOT,IMPLEMENTATION,'NATIVE_PROXY_DRIFT'),
    (DEPOSIT,BEACON_SLOT,ZERO,'NATIVE_PROXY_DRIFT'),
])
def test_storage_proxy_semantics(target, slot, value, code):
    chain = SessionChain(); original = chain.storage
    chain.storage = lambda to, key, **kw: bytes(12)+bytes.fromhex(value[2:]) if (to,key)==(target,slot) else original(to,key,**kw)
    with pytest.raises(ExchangeError, match=code): attest(chain)


def test_legacy_unmigrated_implementation_is_explicitly_unsupported():
    chain=SessionChain(); chain.proxy='legacy_erc1967'
    chain.forwarder='0x58ca52ebe0dadfdf531cde7062e76746de4db1eb'
    with pytest.raises(ExchangeError, match='LEGACY_IMPLEMENTATION_UNSUPPORTED'): attest(chain)


@pytest.mark.parametrize('signature,value,code', [
    ('paused()',1,'WALLET_PAUSED'),
    ('isOperator(address)',1,'OPERATOR_ROLE_FORBIDDEN'),
    ('isOperator(address)',2,'OPERATOR_ROLE_FORBIDDEN'),
])
def test_pause_and_executor_operator_role(signature,value,code):
    chain=SessionChain(); original=chain.call_uint
    chain.call_uint=lambda to,sig,*a,**kw: value if sig==signature else original(to,sig,*a,**kw)
    with pytest.raises(ExchangeError,match=code): attest(chain)


def test_proxy_runtime_must_match_same_derived_wallet_form():
    with pytest.raises(ExchangeError,match='PROXY_DERIVATION_MISMATCH'):
        attest_wallet(SessionChain(),DEPOSIT,OWNER,SESSION,clock=lambda:NOW,expected_proxy_type='UUPS_BEACON_FORWARDER')


def test_attestation_reorg_and_latency_fail_closed():
    chain=SessionChain()
    chain.confirm_block=lambda block: (_ for _ in ()).throw(ExchangeError('CHAIN_ATTESTATION_BLOCK_CHANGED'))
    with pytest.raises(ExchangeError,match='BLOCK_CHANGED'): attest(chain)
    tick=[NOW]; chain=SessionChain(); chain.confirm_block=lambda block: tick.__setitem__(0,NOW+16)
    with pytest.raises(ExchangeError,match='ATTESTATION_TOO_SLOW'):
        attest_wallet(chain,DEPOSIT,OWNER,SESSION,clock=lambda:tick[0],expected_proxy_type='NATIVE_BEACON')


def test_impl_upgrade_after_preparation_prevents_post_and_is_known_unsubmitted():
    from polymarket_scanner.production.chain import SubmissionNotAttempted
    chain=SessionChain(); wire=Wire(context()); exchange=session_client(wire,chain=chain)
    prepared=prepare(exchange); chain.pinned_impl='0x'+'44'*20
    with pytest.raises(SubmissionNotAttempted,match='IMPLEMENTATION_DRIFT'): exchange.submit(prepared)
    assert all(method=='GET' for method,*_ in wire.calls)


class RPCFixture:
    def __init__(self, fault=None): self.calls=[]; self.fixture=SessionChain(); self.fault=fault
    def request(self, method, url, **kw):
        req=json.loads(kw['body']); self.calls.append(req); params=req['params']; rpc=req['method']
        assert method=='POST' and url=='https://polygon.example.invalid'
        if rpc=='eth_chainId': result='0x1' if self.fault=='chain' else '0x89'
        elif rpc=='eth_getBlockByNumber':
            result={'number':'0x64','hash':'0x'+'89'*32,'timestamp':hex(NOW-181 if self.fault=='stale' else NOW)}
            if params[0]=='0x64' and self.fault=='reorg': result['hash']='0x'+'88'*32
        elif rpc=='eth_getCode':
            result='0x'+self.fixture.code(params[0],block=params[1]).hex()
            if params[0]==DEPOSIT and self.fault=='code': result='0x'
        elif rpc=='eth_getStorageAt': result='0x'+self.fixture.storage(params[0],params[1],block=params[2]).hex()
        else:
            assert rpc=='eth_call'; call,block=params; sigdata=call['data']; target=call['to']
            for signature in ['BEACON()','defaultImplementation()','implementation()','pinnedImplementation(address)',
                              'owner()','factory()','id()','walletInterfaceId()','pendingOwner()',
                              'paused()','isOperator(address)','sessionSignerAuthorizedUntil(address)']:
                types=['address'] if '(address)' in signature else []
                values=[DEPOSIT if signature=='pinnedImplementation(address)' else SESSION] if types else []
                if calldata(signature,types,values)!=sigdata: continue
                kwargs={'block':block}
                if 'from' in call: kwargs['sender']=call['from']
                if signature in ('paused()','isOperator(address)','sessionSignerAuthorizedUntil(address)'):
                    raw=self.fixture.call_uint(target,signature,types,values,**kwargs).to_bytes(32,'big')
                else: raw=self.fixture.call(target,signature,types,values,**kwargs)
                result='0x'+raw.hex(); break
            else: raise AssertionError(sigdata)
        return 200,{'jsonrpc':'2.0','id':req['id'],'result':result}


@pytest.mark.parametrize('fault,expected', [(None,None),('chain','CHAIN_MISMATCH'),('stale','STALE_CHAIN_BLOCK'),('code','CODE_MISSING'),('reorg','BLOCK_CHANGED')])
def test_actual_rpc_reader_attests_only_at_pinned_block_and_never_mutates(fault,expected):
    wire=RPCFixture(fault); chain=ChainReader(transport=wire,rpc_url='https://polygon.example.invalid',clock=lambda:NOW)
    if expected:
        with pytest.raises(ExchangeError,match=expected):
            attest_wallet(chain,DEPOSIT,OWNER,SESSION,expected_proxy_type='NATIVE_BEACON',clock=lambda:NOW)
    else:
        result=attest_wallet(chain,DEPOSIT,OWNER,SESSION,expected_proxy_type='NATIVE_BEACON',clock=lambda:NOW)
        assert result['proxy_type']=='NATIVE_BEACON'
        calls=[r for r in wire.calls if r['method']=='eth_call']
        implementation=[r for r in calls if r['params'][0]['data']==calldata('implementation()',[],[])]
        assert implementation[0]['params'][0]['from']==DEPOSIT
        assert all(r['params'][-1]=='0x64' for r in wire.calls if r['method'] in ('eth_call','eth_getCode','eth_getStorageAt'))
    assert {r['method'] for r in wire.calls}<={'eth_chainId','eth_getBlockByNumber','eth_getCode','eth_getStorageAt','eth_call'}
    for mutation in ('eth_sendRawTransaction','eth_sendTransaction','personal_sign'):
        with pytest.raises(ExchangeError,match='MUTATION_FORBIDDEN'): chain.rpc(mutation,[])
