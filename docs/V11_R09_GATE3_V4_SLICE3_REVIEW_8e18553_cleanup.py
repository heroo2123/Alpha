"""Review-only post-test cleanup; never changes code during a test."""
import os
import shutil
from pathlib import Path
import pytest

@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    path = item.funcargs.get('tmp_path')
    yield
    if path is None:
        return
    path = Path(path).resolve()
    base = item.config._tmp_path_factory.getbasetemp().resolve()
    if base.name not in ('alpha-review-8e18553-family', 'alpha-review-8e18553-family2', 'alpha-review-8e18553-probes') or not path.is_relative_to(base):
        raise RuntimeError('review cleanup outside unique review base refused')
    # Candidate fixtures leave report reserve FDs open. Close only descriptors
    # into this test's own completed temporary directory, after all teardown.
    for entry in list(Path('/proc/self/fd').iterdir()):
        try:
            target = os.readlink(entry)
            if target.startswith(str(path) + '/'):
                os.close(int(entry.name))
        except (FileNotFoundError, OSError):
            pass
    # Preserve directory names and inodes: fixtures retain path/identity keyed
    # histories across tests, so deleting directories would cause reuse.
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in files:
            (Path(directory) / name).unlink()
        for name in dirs:
            target = Path(directory) / name
            if target.is_symlink():
                target.unlink()
