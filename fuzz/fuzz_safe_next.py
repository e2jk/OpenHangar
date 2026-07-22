"""Fuzz the open-redirect guard in reservations/routes.py."""

import sys
from pathlib import Path
from urllib.parse import urlparse

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from reservations.routes import _safe_next  # noqa: E402

_FALLBACK = "/fallback"


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    url = fdp.ConsumeUnicodeNoSurrogates(512)
    result = _safe_next(url, _FALLBACK)

    parsed = urlparse(result)
    assert not parsed.scheme, f"scheme leaked: {result!r}"
    assert not parsed.netloc, f"netloc leaked: {result!r}"
    assert result == _FALLBACK or result.startswith("/"), (
        f"non-relative result: {result!r}"
    )


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
