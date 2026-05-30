from flask import current_app  # pyright: ignore[reportMissingImports]
from flask_caching import Cache  # pyright: ignore[reportMissingImports]
from flask_limiter import Limiter  # pyright: ignore[reportMissingImports]
from flask_limiter.util import get_remote_address  # pyright: ignore[reportMissingImports]


def _rate_limiting_disabled() -> bool:
    """True when RATELIMIT_ENABLED=False — used as exempt_when on route-level limits.

    Flask-limiter 4.x does not honour RATELIMIT_ENABLED for per-route @limiter.limit()
    decorators (only for the global default). This function makes the exemption explicit
    so that test fixtures that set RATELIMIT_ENABLED=False disable all limits correctly.
    It is a dict lookup and adds no measurable overhead per request.
    """
    return not current_app.config.get("RATELIMIT_ENABLED", True)


cache = Cache()

limiter = Limiter(
    get_remote_address, storage_uri="memory://", default_limits=["200 per minute"]
)
