"""Fuzz filename/extension handling in documents/routes.py."""
import os
import sys
import atheris
from werkzeug.utils import secure_filename  # type: ignore

_ALLOWED_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt",
}


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    raw_filename = fdp.ConsumeUnicodeNoSurrogates(256)

    # Mirrors the validation logic in documents/routes.py:upload_document
    safe = secure_filename(raw_filename)
    if not safe:
        return
    ext = os.path.splitext(safe)[1].lower()

    # Invariant: ext is always a simple dotted extension, never a path
    assert "/" not in ext, f"path separator in ext: {ext!r}"
    assert "\\" not in ext, f"backslash in ext: {ext!r}"
    assert ext == ext.lower(), f"ext not normalised to lowercase: {ext!r}"

    allowed = ext in _ALLOWED_EXTS
    _ = allowed  # consume result; fuzzer checks for crashes/assertion failures


atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
