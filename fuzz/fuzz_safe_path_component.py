"""Fuzz the filesystem-path-segment sanitizer used for document filenames."""

import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from documents.routes import _safe_path_component  # noqa: E402

_UNSAFE_CHARS = '<>:"/\\|?*'


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    s = fdp.ConsumeUnicodeNoSurrogates(256)
    result = _safe_path_component(s)

    for ch in _UNSAFE_CHARS:
        assert ch not in result, f"unsafe char {ch!r} leaked: {result!r}"
    assert not any(ord(c) < 0x20 for c in result), f"control char leaked: {result!r}"


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
