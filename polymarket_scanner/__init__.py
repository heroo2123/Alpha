from .safe_logging import install_secret_safe_logging

# Install before any submodule creates HTTP clients or emits access logs. This is
# deliberately package-wide defense in depth: even an accidental legacy entrypoint
# cannot log a Telegram Bot API URL with the credential embedded in the path.
install_secret_safe_logging()

__all__ = [
    "config",
    "models",
    "polymarket",
    "detectors",
    "weather",
    "store",
    "telegram",
]
