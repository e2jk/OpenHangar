"""Fuzz the URL redirect validator (_safe_next) in reservations/routes.py."""
import sys
import atheris
from urllib.parse import urlparse


def _safe_next(next_url: str, fallback: str) -> str:
    """Mirrors app/reservations/routes.py:_safe_next exactly."""
    next_url = next_url.replace("\\", "")
    parsed = urlparse(next_url)
    if next_url and not parsed.scheme and not parsed.netloc:
        return next_url
    return fallback


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    url = fdp.ConsumeUnicodeNoSurrogates(512)
    result = _safe_next(url, "/")

    # Invariant: result is always either the fallback or a scheme-less,
    # netloc-less string — never an absolute URL that could redirect offsite.
    parsed = urlparse(result)
    assert not parsed.scheme, f"scheme leaked: {result!r}"
    assert not parsed.netloc, f"netloc leaked: {result!r}"


atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
