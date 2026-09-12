from .safe_logging import install_secret_safe_logging

# Install before any submodule creates HTTP clients or emits access logs. This is
# deliberately package-wide defense in depth: even an accidental legacy entrypoint
# cannot log a Telegram Bot API URL with the credential embedded in the path.
install_secret_safe_logging()

# Weather-only CLOB reads are idempotent and carry no order methods.  Install a
# bounded retry adapter before weather submodules import WeatherCLOBClient so one
# transient timeout/transport error cannot poison an entire five-minute runtime
# cycle or several W7 samples.  Semantic/status/identity failures remain fail-closed.
from .weather_only_clob_resilience import install_weather_clob_transport_resilience

install_weather_clob_transport_resilience()

__all__ = [
    "config",
    "models",
    "polymarket",
    "detectors",
    "weather",
    "store",
    "telegram",
]
