"""Offline negative contract check; no installed package/artifact is changed."""
import contextlib
import io
import json
import runpy
from types import SimpleNamespace

path='/tmp/alpha-v11-decoder-review-20a42f7/docs/V11_R09_GATE3_DECODER_BUILD_OBSERVATION_20261001_probe.py'
n=runpy.run_path(path,run_name='review_import')
g=n['main'].__globals__
g['resource'].setrlimit=lambda *args: None
g['subprocess'].run=lambda *args,**kwargs: SimpleNamespace(stdout='mocked-nonempty')
g['EXPECTED_VENV']='/tmp/reviewer-intentionally-wrong-venv/'
g['_check_repo_sources']=lambda:[{'matches_committed_HEAD':False}]
g['_check_dist_info']=lambda *args:{'mismatches':['injected-drift'], 'missing':['injected-missing']}
g['_run_bounded_synthetic_ccsds_decode']=lambda:{'injected_wrong_result':289.0}
g['_loaded_native_libraries']=lambda:[]
buffer=io.StringIO()
with contextlib.redirect_stdout(buffer):
    result=n['main']()
report=json.loads(buffer.getvalue())
assert result==0 and not report['venv_matches_expected']
assert report['decoder_sources'][0]['matches_committed_HEAD'] is False
print(json.dumps({'main_return_code':result,'wrong_venv_detected_but_not_rejected':not report['venv_matches_expected'],'dirty_repo_not_rejected':not report['repo_worktree_clean'],'source_drift_not_rejected':True,'record_mismatch_and_missing_not_rejected':True,'companion_manifest_never_read':True},indent=2))
