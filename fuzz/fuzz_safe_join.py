"""Fuzz the path-traversal guard used for uploaded-document storage paths."""

import os
import sys
from pathlib import Path

import atheris
from werkzeug.exceptions import BadRequest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

# include= scopes instrumentation to just this module — see
# fuzz_flight_form_parsing.py for the measured setup-time win. Retrofitted
# here from Phase 1's original plain @instrument_func-only form.
with atheris.instrument_imports(include=["documents.routes"]):
    from documents.routes import _safe_join  # noqa: E402

_ROOT = "/tmp/oh-fuzz-uploads"


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    part = fdp.ConsumeUnicodeNoSurrogates(256)

    try:
        joined = _safe_join(_ROOT, part)
    except BadRequest:
        return  # correctly rejected a path that would escape the root
    except ValueError:
        return  # e.g. an embedded NUL byte rejected by the OS path syscalls

    root = os.path.realpath(_ROOT)
    assert joined == root or joined.startswith(root + os.sep), (
        f"path escaped root: {joined!r}"
    )


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
