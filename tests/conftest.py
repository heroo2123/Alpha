"""Repository tests are offline, including on workspaces with injected proxies."""
import os
import socket


def pytest_configure(config):
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
