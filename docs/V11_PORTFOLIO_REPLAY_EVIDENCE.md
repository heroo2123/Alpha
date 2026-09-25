# Original prepared basket/exit valuation replay evidence

2026-09-25. Original master SHA-256
`a0e16d9bd7344c943a54a16a53c6757662363d93642f6e5cb7953cd047659b4a` unchanged.
Recovered clean published b351cdb584ddbc8e17af4e77eee96b3709849f57/tree
33a74215b090094f78c5b049a0a63af2968555b9; all 764 implementation inputs matched.
No running operation was retried. V10 untouched, maintenance deferred.

`basket_valuation.basket_details` is the shared computation without journal-return
shortcuts or writes. `position_management.payout_inputs` shares original payout/
conditioning inputs while actual runtime admission/protected inference stays in
`_predict`. `portfolio_replay` reconstructs original prepared baskets and exits,
retaining every prepared candidate including those rejected by allocation. It
uses retained original model history, exact original source/valuation cutoffs,
books and original account inventory snapshot/FIFO economics. Rejected preparation
and other valuation families remain conditional/unimplemented. This is numeric
comparison, not historical strategy selection, controls, executable attestation,
independent source truth, or renewed admission.

`replay_valuations=True` joins proofs to existing account-effect replay using the
same readonly snapshot and total deadline. Typed `AuditPolicy.account_valuation_replay`
requires account replay and adds separate complete-cohort valuation coverage.
False/default omits the option from the serialized configuration to preserve old
candidate/report identities. Missing evidence and unsupported values remain
visible; no command, account mutation or financial authority is issued by replay.
The implementation was saved/published as **583bc1e7258f7d9ee1ee86812efd83a6281d3bb5**,
tree **64e2b29d5b07e5d24bea82aec4448891b382c27c**. Candidate/failure coverage is
now complete below; affected/full integration checks on the next saved tree are due.

## Development verification

Interpreter: `/workspace/scratch/38af7099c566/alpha-v11-venv-20260924/bin/python`.
Working directory: `/workspace/scratch/38af7099c566/Alpha`.
Logs: `/workspace/scratch/38af7099c566/v11-test-evidence/`.

| Run | Exact pytest selection | Result / session / exit | Raw log SHA-256 |
|---|---|---|---|
| portfolio-refactor-20260925-01.log | tests/test_v11_basket_valuation.py tests/test_v11_position_management.py | 43 passed in 5.00s / 1592 / 0 | 867dcae171c1c5ae2e0915d313ca982043e66918ca4c87ee7c2b7116af91417d |
| portfolio-dev-20260925-01.log | tests/test_v11_portfolio_replay.py | 4 failed, 11 passed in 2.90s / 56745 / 1 | d63c07579bad692e7b523401ca07e6261aa7bfbc59547baa22571d1b75ca283e |
| portfolio-dev-20260925-02.log | tests/test_v11_portfolio_replay.py tests/test_v11_account_replay.py tests/test_v11_account_replay_integration.py | 51 passed in 5.80s / 67586 / 0 | f3a1fdd0fd50299dea9888f03e5b0cdfdaabec53e9ee92f9280dbb7488f302f5 |
| portfolio-dev-20260925-03.log | tests/test_v11_portfolio_replay.py | 1 failed, 20 passed in 4.63s / 72719 / 1 | a660e8861c46db82cb30eccf640e0a2a94569af6d69dc5ae023c7fdbf5dd05bd |
| portfolio-candidate-dev-20260925-01.log | tests/test_v11_portfolio_replay.py::test_typed_candidate_source_strategy_account_and_scheduled_valuation_audit --tb=short (development name) | 1 failed in 1.16s / 73260 / 1 | 65926d5cece37f450f2a1a913f2021b0f2f05df4dff117f697c190885cc3b99b |
| portfolio-dev-20260925-04.log | tests/test_v11_portfolio_replay.py | 1 failed, 21 passed in 8.20s / 98199 / 1 | 494efb1703691ef8595a5be4101d7257274beff66781ab8c735fe42137607d4b |
| portfolio-dev-20260925-05.log | tests/test_v11_portfolio_replay.py tests/test_v11_account_replay.py tests/test_v11_account_replay_integration.py tests/test_v11_basket_valuation.py tests/test_v11_position_management.py | 102 passed in 16.66s / 82485 / 0 | 037f1e47f12040428477bb68028a4ba888d2713fd98207279adb3398e8d6463b |

Initial failures were fixture expectations, corrected without weakening production
gates: both basket variants have synthetic roots without raw derivation edges;
missing model evidence already gates the whole original account command; inventory
comparison binds the original account snapshot rather than an unused older fill
command. The corrected missing-inventory case removes the required snapshot.
New output explicitly records input evidence classes and derivation availability.
Sources/markets/clocks/model approval fixtures are synthetic. No real economic
edge, fill or actual provider availability is inferred.

The candidate test initially assumed synthetic cheap quotes alone would permit
new reservations. Actual derived risk correctly retained
`CROSS_BUCKET_MOTION_UNKNOWN`, `DEPTH_LOSS_UNKNOWN`, `EXECUTION_HEALTH_UNKNOWN`,
`PRICE_VELOCITY_UNKNOWN`, `SETTLEMENT_WINDOW_UNKNOWN_OR_CLOSED`, and stream
synchronization gates. The final test proves that the typed candidate schedules
the historical audit while these gates remain. The separate existing mock-census
strategy integration declares synthetic healthy risk inputs and proves normalized
books/raw receipt lineage, protected prediction and all prepared account values;
it emits multiple coordinate commands, all checked instead of assuming one.
Missing required raw evidence gates the comparison. No production gate was changed
to satisfy either fixture. All 102 final cases pass, including numerical defect
detection, model promotion/missing history, later inventory/book/day changes,
unsupported values staying in the denominator, a shared whole-audit deadline,
and recovery after report publication without recomputation or another command.

**83/200 (~42%); formal 1/50 (2%)**, unchanged. Full original preparation/control
and executable replay, actual/calibration evidence, independent review, isolated
deployment and unfunded acceptance remain open. **NOT_READY_TO_FUND**.

## Retained development transcripts

Display copies trim line-ending spaces; hashes above identify the exact raw logs.

### portfolio-refactor-20260925-01.log

```text
...........................................                              [100%]
43 passed in 5.00s
```

### portfolio-dev-20260925-01.log

```text
FF....F..F.....                                                          [100%]
=================================== FAILURES ===================================
_ test_basket_original_numeric_engine_without_journal_or_current_admission[CROSS_TEMP_RELATIVE_VALUE] _

rig = {'admission_kw': {'binding': ReleaseBinding(code_commit='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', code_tree='bbbbbbb...maximum_units='20'), maximum_book_skew_seconds=2.0, maximum_rule_age_seconds=120.0, minimum_total_ev='.01'), ...}, ...}
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7ff615864fb0>

    @pytest.mark.parametrize('rig', ['CROSS_TEMP_RELATIVE_VALUE','STRUCTURAL'], indirect=True)
    def test_basket_original_numeric_engine_without_journal_or_current_admission(rig,monkeypatch):
        reserve(rig); c=coordinator(rig); head=c._head()
        def forbidden(*a,**kw):pytest.fail('Historical numerical valuation issued a write or current approval')
        monkeypatch.setattr(ActiveModelRegistry,'pin',forbidden)
        monkeypatch.setattr(ActiveModelRegistry,'revalidate',forbidden)
        monkeypatch.setattr('polymarket_scanner.v11.strategy_admission.StrategyAdmission.revalidate',forbidden)
        monkeypatch.setattr('polymarket_scanner.v11.evidence.EvidenceStore.audit',forbidden)
        result=replay(rig); row=proof_row(result)
        assert row['status']=='ECONOMICS_REPRODUCED',row
        assert row['original_prediction_sha256']==row['recomputed_prediction_sha256']
        assert row['original_valuation_sha256']==row['recomputed_valuation_sha256']
>       assert row['source_derivation_sha256'] and row['book_derivation']['source_derivation_sha256']
E       assert (None)

tests/test_v11_portfolio_replay.py:43: AssertionError
_ test_basket_original_numeric_engine_without_journal_or_current_admission[STRUCTURAL] _

rig = {'admission_kw': {'binding': ReleaseBinding(code_commit='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', code_tree='bbbbbbb...maximum_units='20'), maximum_book_skew_seconds=2.0, maximum_rule_age_seconds=120.0, minimum_total_ev='.01'), ...}, ...}
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7ff615702db0>

    @pytest.mark.parametrize('rig', ['CROSS_TEMP_RELATIVE_VALUE','STRUCTURAL'], indirect=True)
    def test_basket_original_numeric_engine_without_journal_or_current_admission(rig,monkeypatch):
        reserve(rig); c=coordinator(rig); head=c._head()
        def forbidden(*a,**kw):pytest.fail('Historical numerical valuation issued a write or current approval')
        monkeypatch.setattr(ActiveModelRegistry,'pin',forbidden)
        monkeypatch.setattr(ActiveModelRegistry,'revalidate',forbidden)
        monkeypatch.setattr('polymarket_scanner.v11.strategy_admission.StrategyAdmission.revalidate',forbidden)
        monkeypatch.setattr('polymarket_scanner.v11.evidence.EvidenceStore.audit',forbidden)
        result=replay(rig); row=proof_row(result)
        assert row['status']=='ECONOMICS_REPRODUCED',row
        assert row['original_prediction_sha256']==row['recomputed_prediction_sha256']
        assert row['original_valuation_sha256']==row['recomputed_valuation_sha256']
>       assert row['source_derivation_sha256'] and row['book_derivation']['source_derivation_sha256']
E       assert (None)

tests/test_v11_portfolio_replay.py:43: AssertionError
_ test_missing_original_receipt_gates_valuation_without_using_current_state[model2] _

rig = {'admission_kw': {'binding': ReleaseBinding(code_commit='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', code_tree='bbbbbbb...maximum_units='20'), maximum_book_skew_seconds=2.0, maximum_rule_age_seconds=120.0, minimum_total_ev='.01'), ...}, ...}
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7ff6157e2810>
missing = 'model2'

    @pytest.mark.parametrize('missing', ['pin','model2','basket-book0','exit:start','record-fill-0'])
    def test_missing_original_receipt_gates_valuation_without_using_current_state(rig,monkeypatch,missing):
        from polymarket_scanner.v11.learning_sources import LearningSourceView
        inventory(rig);reserved_exit(rig);get=LearningSourceView.get
        def absent(self,key):
            if key==missing:raise EvidenceError('EVIDENCE_MISSING')
            return get(self,key)
        monkeypatch.setattr(LearningSourceView,'get',absent)
>       row=proof_row(replay(rig,'reserve-exit'))

tests/test_v11_portfolio_replay.py:87:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

result = {'action': 'COORDINATE', 'admission_authority': False, 'command_id': 'reserve-exit', 'command_recorded_at': 1789250420.02, ...}

    def proof_row(result):
>       assert result['status']=='EFFECTS_REPRODUCED', result
E       AssertionError: {'action': 'COORDINATE', 'admission_authority': False, 'command_id': 'reserve-exit', 'command_recorded_at': 1789250420.02, ...}
E       assert 'GATED' == 'EFFECTS_REPRODUCED'
E
E         - EFFECTS_REPRODUCED
E         + GATED

tests/test_v11_portfolio_replay.py:25: AssertionError
_ test_missing_original_receipt_gates_valuation_without_using_current_state[record-fill-0] _

rig = {'admission_kw': {'binding': ReleaseBinding(code_commit='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', code_tree='bbbbbbb...maximum_units='20'), maximum_book_skew_seconds=2.0, maximum_rule_age_seconds=120.0, minimum_total_ev='.01'), ...}, ...}
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7ff6157e3aa0>
missing = 'record-fill-0'

    @pytest.mark.parametrize('missing', ['pin','model2','basket-book0','exit:start','record-fill-0'])
    def test_missing_original_receipt_gates_valuation_without_using_current_state(rig,monkeypatch,missing):
        from polymarket_scanner.v11.learning_sources import LearningSourceView
        inventory(rig);reserved_exit(rig);get=LearningSourceView.get
        def absent(self,key):
            if key==missing:raise EvidenceError('EVIDENCE_MISSING')
            return get(self,key)
        monkeypatch.setattr(LearningSourceView,'get',absent)
        row=proof_row(replay(rig,'reserve-exit'))
>       assert row['status']=='GATED' and not row['economic_match'],row
E       AssertionError: {'admission_ref': {'id': 'pin', 'seq': 26, 'sha256': 'ae4f3f45bfcadb3b1444dea73389457a21b6a3c2a8e0e0da712704f52fd7a85f...': True, 'prediction': True, 'reasons': True, 'valuation': True}, 'cutoff': 1789250420.02, 'economic_match': True, ...}
E       assert ('ECONOMICS_REPRODUCED' == 'GATED'
E
E         - GATED
E         + ECONOMICS_REPRODUCED)

tests/test_v11_portfolio_replay.py:88: AssertionError
=========================== short test summary info ============================
FAILED tests/test_v11_portfolio_replay.py::test_basket_original_numeric_engine_without_journal_or_current_admission[CROSS_TEMP_RELATIVE_VALUE]
FAILED tests/test_v11_portfolio_replay.py::test_basket_original_numeric_engine_without_journal_or_current_admission[STRUCTURAL]
FAILED tests/test_v11_portfolio_replay.py::test_missing_original_receipt_gates_valuation_without_using_current_state[model2]
FAILED tests/test_v11_portfolio_replay.py::test_missing_original_receipt_gates_valuation_without_using_current_state[record-fill-0]
4 failed, 11 passed in 2.90s
```

### portfolio-dev-20260925-02.log

```text
...................................................                      [100%]
51 passed in 5.80s
```

### portfolio-dev-20260925-03.log

```text
....................F                                                    [100%]
=================================== FAILURES ===================================
__ test_typed_candidate_source_strategy_account_and_scheduled_valuation_audit __

rig = {'admission_kw': {'binding': ReleaseBinding(code_commit='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', code_tree='bbbbbbb...aaaaaa'], 'context': EventContext(account_id='account', city_id='Atlanta', station_id='KATL', event_id='event-1'), ...}
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7fc33a3ba8a0>

    def test_typed_candidate_source_strategy_account_and_scheduled_valuation_audit(rig,monkeypatch):
        import asyncio
        import httpx
        from polymarket_scanner.v11 import candidate_assembly as app
        from test_v11_candidate_assembly import plan,synthetic_clock
        from test_v11_book_inputs import response
        from test_v11_runtime_health import ready,advance
        cfg=plan(rig);cfg=replace(cfg,audits=replace(cfg.audits,records_per_step=256,
            account_replay=ReplayPolicy('candidate-portfolio',5.),account_valuation_replay=True))
        synthetic_clock(rig,monkeypatch);calls=[]
        def transport(req):
            calls.append(req)
            if req.url.host=='gamma-api.polymarket.com':return httpx.Response(200,json=dict(events=[],next_cursor=None))
            if req.url.host=='aviationweather.gov':return httpx.Response(200,json=[dict(icaoId=rig['context'].station_id,obsTime=rig['now'][0]-1,temp=25)])
            book=response(rig,req.url.params['token_id'])
            book.update(asks=[dict(price='.2',size='20')],bids=[dict(price='.19',size='20')],min_order_size='1')
            return httpx.Response(200,json=book)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                candidate=app.assemble_candidate(rig['store'],client,cfg,generation='portfolio-candidate')
                ready(rig,candidate.runtime.health)
                first=await candidate.run('portfolio-source-decision')
                state=candidate.runtime.coordinator._state(candidate.runtime.coordinator._head())
                assert len(state['intents'])==3,first['body']['details']
                advance(rig,(int(rig['now'][0]//86400)+1)*86400+1-rig['now'][0])
                for i in range(12):
                    await candidate.run('portfolio-scheduled-audit-'+str(i))
                    report=rig['store'].latest(kind='RUNTIME_STATUS',event_id='v11-audit-report:DAILY')
                    if report and report['body']['details']['account_replay']['retained_command_count']:
                        return candidate,report['body']['details']
                pytest.fail('Candidate did not finish scheduled portfolio audit')
>       candidate,report=asyncio.run(run());d=report['account_replay'];v=d['prepared_valuation_coverage']

tests/test_v11_portfolio_replay.py:221:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/runners.py:195: in run
    return runner.run(main)
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/runners.py:118: in run
    return self._loop.run_until_complete(task)
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/base_events.py:691: in run_until_complete
    return future.result()
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            candidate=app.assemble_candidate(rig['store'],client,cfg,generation='portfolio-candidate')
            ready(rig,candidate.runtime.health)
            first=await candidate.run('portfolio-source-decision')
            state=candidate.runtime.coordinator._state(candidate.runtime.coordinator._head())
>           assert len(state['intents'])==3,first['body']['details']
E           AssertionError: {'active_command_requires_recovery': False, 'all_async_jobs_drained': True, 'clock_healthy_at_finish': True, 'config_sha256': '940c7bc047db6a03647e28d7ef18896a14f1fa794c2e578d43c12650660c4161', ...}
E           assert 0 == 3
E            +  where 0 = len({})

tests/test_v11_portfolio_replay.py:213: AssertionError
=========================== short test summary info ============================
FAILED tests/test_v11_portfolio_replay.py::test_typed_candidate_source_strategy_account_and_scheduled_valuation_audit
1 failed, 20 passed in 4.63s
```

### portfolio-candidate-dev-20260925-01.log

```text
F                                                                        [100%]
=================================== FAILURES ===================================
__ test_typed_candidate_source_strategy_account_and_scheduled_valuation_audit __
tests/test_v11_portfolio_replay.py:224: in test_typed_candidate_source_strategy_account_and_scheduled_valuation_audit
    candidate,report=asyncio.run(run());d=report['account_replay'];v=d['prepared_valuation_coverage']
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/runners.py:195: in run
    return runner.run(main)
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/runners.py:118: in run
    return self._loop.run_until_complete(task)
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/base_events.py:691: in run_until_complete
    return future.result()
tests/test_v11_portfolio_replay.py:213: in run
    assert len(state['intents'])==3,[(r['id'],r['body'].get('details',{}).get('outcome'),
E   AssertionError: [('risk-input:2785acc88e6b3aacee6346b90a0c03627ee3b955e618c0b2609ed793c84af614:book:0', 'MEASURED_RESEARCH_FEATURES', ...609ed793c84af614:book:5', 'MEASURED_RESEARCH_FEATURES', 'OBSERVED_INPUTS_NOT_EXECUTION_QUALITY_VALIDATION', None), ...]
E   assert 0 == 3
E    +  where 0 = len({})
=========================== short test summary info ============================
FAILED tests/test_v11_portfolio_replay.py::test_typed_candidate_source_strategy_account_and_scheduled_valuation_audit
1 failed in 1.16s
```

### portfolio-dev-20260925-04.log

```text
.....................F                                                   [100%]
=================================== FAILURES ===================================
_ test_mock_census_books_protected_strategy_and_common_account_replay_raw_lineage _

rig = {'admission_kw': {'binding': ReleaseBinding(code_commit='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', code_tree='bbbbbbb...aaaaaa'], 'context': EventContext(account_id='account', city_id='Atlanta', station_id='KATL', event_id='event-1'), ...}
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x7f780f022cf0>

    def test_mock_census_books_protected_strategy_and_common_account_replay_raw_lineage(rig,monkeypatch):
        from test_v11_strategy_runtime import joined,run_candidate
        # This existing integration explicitly declares synthetic healthy event
        # metrics. It is not evidence that the fully derived candidate is eligible.
        rt=joined(rig,monkeypatch);run_candidate(rig,rt)
        commands=[r for r in rig['store'].records(kind='COORDINATOR_EVENT',event_id=ACCOUNT_KEY)
            if r['body']['details']['request']['action']=='COORDINATE']
>       assert len(commands)==1
E       AssertionError: assert 2 == 1
E        +  where 2 = len([{'body': {'available_at': 1789250421.01, 'details': {'economic_attribution_is_not_multiple_fills': True, 'effect_inpu...tick:719e54fa2707980e50287dd8e2ae7f9cc666f87630d2e36da2b272e62cfc05cd:coordinate:0', 'kind': 'COORDINATOR_EVENT', ...}])

tests/test_v11_portfolio_replay.py:241: AssertionError
=========================== short test summary info ============================
FAILED tests/test_v11_portfolio_replay.py::test_mock_census_books_protected_strategy_and_common_account_replay_raw_lineage
1 failed, 21 passed in 8.20s
```

### portfolio-dev-20260925-05.log

```text
........................................................................ [ 70%]
..............................                                           [100%]
102 passed in 16.66s
```
