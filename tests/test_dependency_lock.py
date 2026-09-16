from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_EXPECTED = {
    "httpx",
    "python-dotenv",
    "pydantic",
    "pydantic-settings",
    "fastapi",
    "uvicorn",
    "websockets",
    "annotated-types",
    "anyio",
    "certifi",
    "click",
    "h11",
    "httpcore",
    "httptools",
    "idna",
    "pyyaml",
    "sniffio",
    "starlette",
    "typing-extensions",
    "uvloop",
    "watchfiles",
}
DEV_EXPECTED = {"pytest", "iniconfig", "packaging", "pluggy"}


def _requirement_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _name(line: str) -> str:
    left = line.split("==", 1)[0].strip().lower()
    return left.split("[", 1)[0]


def test_runtime_dependency_graph_is_fully_version_pinned():
    lines = _requirement_lines(ROOT / "requirements.txt")
    assert lines
    assert all("==" in line for line in lines)
    assert all(not any(op in line.split("==", 1)[1] for op in (">", "<", "~=")) for line in lines)
    names = {_name(line) for line in lines}
    assert RUNTIME_EXPECTED <= names


def test_dev_dependency_graph_includes_reviewed_execution_lock_and_hash_pins():
    lines = _requirement_lines(ROOT / "requirements-dev.txt")
    includes = [line for line in lines if line.startswith("-r ")]
    requirements = [line for line in lines if not line.startswith("-r ")]
    assert includes == ["-r requirements-execution-hashed.txt"]
    assert requirements
    assert all("==" in line for line in requirements)
    names = {_name(line) for line in requirements}
    assert DEV_EXPECTED <= names
    import re
    execution = _requirement_lines(ROOT / "requirements-execution-hashed.txt")
    for row in requirements + execution:
        assert re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+!-]+(?: --hash=sha256:[0-9a-f]{64})+", row), row
    assert RUNTIME_EXPECTED <= {_name(row) for row in execution}
    assert {"eth-account", "eth-abi", "eth-utils"} <= {_name(row) for row in execution}
