"""Process custody and secret-safe atomic output shared by production components."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

from .config import ConfigurationError, canonical


@contextmanager
def lease(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigurationError("WORKER_ALREADY_RUNNING") from None
        yield
    finally:
        os.close(fd)


def atomic_json(path: Path, value: dict, *, mode=0o600, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".status-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(canonical(value) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        if exclusive:
            try:
                os.link(name, path)
            except FileExistsError:
                raise ConfigurationError("REVIEW_OUTPUT_ALREADY_EXISTS") from None
        else:
            os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def private_json(path: Path):
    try:
        st = path.lstat()
        if path.is_symlink() or st.st_mode & 0o077 or st.st_uid not in {0, os.geteuid()}:
            raise ConfigurationError("PRIVATE_FILE_CUSTODY_INVALID")
        if st.st_size > 65536:
            raise ConfigurationError("PRIVATE_FILE_TOO_LARGE")
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError
        return raw
    except (OSError, ValueError):
        raise ConfigurationError("PRIVATE_FILE_READ_FAILED") from None


def clean_startup():
    forbidden = {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP", "LD_PRELOAD", "LD_LIBRARY_PATH", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
    if any(key.upper() in forbidden for key in os.environ):
        raise ConfigurationError("CODE_LOADING_OR_TRANSPORT_ENVIRONMENT_FORBIDDEN")
    os.environ["ALPHA_DISABLE_DOTENV"] = "1"
    os.umask(0o077)
