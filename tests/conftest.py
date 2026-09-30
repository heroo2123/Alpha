"""Repository tests are offline, including on workspaces with injected proxies."""
import os
import socket

import pytest


@pytest.fixture
def candidate_clock(monkeypatch):
    """Virtual scheduler time for offline candidate integration tests.

    Ready coroutines run normally; idle time jumps to the next asyncio timer.
    Synchronous fixture/SQLite cost therefore cannot consume the candidate's
    budget. Worker deadlines and evidence/health clocks remain unmodified.
    Only use with in-memory transports, never actual asynchronous I/O.
    """
    import asyncio
    from types import SimpleNamespace
    from polymarket_scanner.v11 import candidate_runner

    class ClockedLoop(asyncio.SelectorEventLoop):
        now = 0.

        def time(self):
            return self.now

        def _run_once(self):
            if not self._ready and self._scheduled:
                self.now = max(self.now, self._scheduled[0]._when)
            super()._run_once()

    with asyncio.Runner(loop_factory=ClockedLoop) as runner:
        # Replace the module binding, not the shared stdlib time module.
        monkeypatch.setattr(candidate_runner, 'time', SimpleNamespace(monotonic=runner.get_loop().time))
        yield runner


def pytest_configure(config):
    # Production authority/config checks reject group- or other-writable
    # files/directories (e.g. config.py:activation_requested,
    # host_trust/*/authority.py custody checks) as a real security
    # requirement. Tests that write fixtures without an explicit mode rely on
    # umask alone to produce safe permissions; a host umask looser than 022
    # (e.g. 002, common with user-private-group setups) silently makes those
    # fixtures group-writable and trips the checks with no code defect. Pin a
    # deterministic umask for the test process so results do not depend on
    # the invoking shell/session.
    os.umask(0o022)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(name, None)
    original_connect = socket.socket.connect

    def offline_connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            host = address[0]
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise RuntimeError("repository tests prohibit external network connections")
        return original_connect(self, address)

    socket.socket.connect = offline_connect
