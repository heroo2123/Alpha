"""Check reviewed runtime wheel hashes, including both supported Python ABIs."""
from pathlib import Path
import argparse
import re


def normalized_spec(spec):
    if spec.count('==')!=1:
        raise ValueError('INVALID_HASH_INVENTORY')
    name,version=spec.split('==')
    return re.sub(r'[-_.]+','-',name.lower())+'=='+version


def rows(path):
    result={}
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        spec,*hashes=line.split()
        spec=normalized_spec(spec)
        if spec in result or not hashes or any(not h.startswith('--hash=sha256:') or len(h)!=78 for h in hashes):
            raise ValueError('INVALID_HASH_INVENTORY')
        result[spec]=set(hashes)
    return result


def verify(root, generated=None):
    root=Path(root)
    runtime=rows(root/'requirements-runtime-hashed.txt')
    execution=rows(root/'requirements-execution-hashed.txt')
    specifications={normalized_spec(r.strip()) for r in (root/'requirements.txt').read_text().splitlines() if r.strip() and not r.lstrip().startswith('#')}
    if set(runtime)!=specifications:
        raise ValueError('RUNTIME_PIN_INVENTORY_MISMATCH')
    # The execution lock already pins reviewed 3.11/3.12 wheels. Require exact
    # hash sets for only the runtime's original packages, not its signing extras.
    if any(execution.get(spec)!=hashes for spec,hashes in runtime.items()):
        raise ValueError('RUNTIME_REVIEWED_HASH_SET_MISMATCH')
    if generated:
        observed=rows(generated)
        if set(observed)!=set(runtime) or any(not hashes<=runtime[spec] for spec,hashes in observed.items()):
            raise ValueError('DOWNLOADED_RUNTIME_WHEEL_NOT_APPROVED')
    return len(runtime)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generated',type=Path)
    args=parser.parse_args()
    print(f'{verify(Path(__file__).resolve().parents[1],args.generated)} exact runtime pins and reviewed cross-version hash sets verified.')
