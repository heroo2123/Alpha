"""Reviewer-only adversarial probes (outside any git worktree)."""
import json
import pytest
from test_v11_gate3_raw_decoder_binding import _attempt, _close, _read, BODY
from tools.v11_gate3_raw_decoder_binding import RawBindingRefusal

_MISSING = object()

@pytest.mark.parametrize('value', ['CONTROL', len(BODY) - 1, len(BODY) + 1, 0,
                                   float(len(BODY)), str(len(BODY)), True, None,
                                   _MISSING, 'EXTRA'])
def test_probe(tmp_path, value):
    acquired, runtime, request = _attempt(tmp_path)
    try:
        real_close = runtime.session.transport_closed
        def tamper(rid, **kwargs):
            ev = json.loads(kwargs['closure_evidence_raw'])
            if value == 'EXTRA':
                ev['unexpected_field'] = 1
            elif value is _MISSING:
                del ev['read_bytes']
            elif value != 'CONTROL':
                ev['read_bytes'] = value
            kwargs['closure_evidence_raw'] = json.dumps(
                ev, sort_keys=True, separators=(',', ':')).encode()
            return real_close(rid, **kwargs)
        runtime.session.transport_closed = tamper
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        if value == 'CONTROL':
            assert _read(runtime, request) == BODY
        else:
            with pytest.raises(RawBindingRefusal, match='BINDING_CLOSURE'):
                _read(runtime, request)
    finally:
        runtime.report_sink.close()
        _close(acquired)
