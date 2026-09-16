from pathlib import Path
import pytest
from tools.verify_runtime_hash_inventory import verify


ROOT=Path(__file__).resolve().parents[1]


def test_runtime_contains_exact_reviewed_cross_version_wheels_without_signing_packages():
    assert verify(ROOT)>0
    text=(ROOT/'requirements-runtime-hashed.txt').read_text()
    assert 'eth-account' not in text and 'eth-abi' not in text


def test_extra_unreviewed_runtime_hash_is_rejected(tmp_path):
    for name in ('requirements.txt','requirements-runtime-hashed.txt','requirements-execution-hashed.txt'):
        (tmp_path/name).write_text((ROOT/name).read_text())
    path=tmp_path/'requirements-runtime-hashed.txt'; rows=path.read_text().splitlines()
    rows[0]+=' --hash=sha256:'+'f'*64
    path.write_text('\n'.join(rows)+'\n')
    with pytest.raises(ValueError,match='REVIEWED_HASH_SET'): verify(tmp_path)


@pytest.mark.parametrize('spelling',['PyYAML','PYYAML','typing_extensions','Typing.Extensions'])
def test_case_and_pep503_duplicate_cannot_hide_unreviewed_hash(tmp_path,spelling):
    for name in ('requirements.txt','requirements-runtime-hashed.txt','requirements-execution-hashed.txt'):
        (tmp_path/name).write_text((ROOT/name).read_text())
    path=tmp_path/'requirements-runtime-hashed.txt'
    version='6.0.3' if spelling.lower()=='pyyaml' else '4.16.0'
    path.write_text(spelling+'=='+version+' --hash=sha256:'+'f'*64+'\n'+path.read_text())
    with pytest.raises(ValueError,match='INVALID_HASH_INVENTORY'): verify(tmp_path)
