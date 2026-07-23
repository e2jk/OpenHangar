"""Fuzz the access-log secret-token redactor (log_redaction.py).

redact_sensitive_path() masks the token segment of the three URL paths that
carry a secret directly in the path (password-reset, public share links,
user-invitation) before gunicorn's access logger ever sees them (CWE-532).
It runs on the raw WSGI PATH_INFO of every request, fully attacker-
controlled. The main invariant worth fuzzing: a path that does NOT start
with one of the three sensitive prefixes must come back completely
unchanged — over-redacting (or under-redacting) is the only way this
function can meaningfully fail, since a regex .sub() never raises for a
valid str input.
"""

import sys
from pathlib import Path

import atheris

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

with atheris.instrument_imports(include=["log_redaction"]):
    from log_redaction import redact_sensitive_path  # noqa: E402

_SENSITIVE_PREFIXES = ("/reset-password/", "/share/", "/config/users/invite/")


@atheris.instrument_func
def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    path = fdp.ConsumeUnicodeNoSurrogates(160)

    result = redact_sensitive_path(path)
    assert isinstance(result, str)

    if not path.startswith(_SENSITIVE_PREFIXES):
        assert result == path, (
            f"path changed despite no sensitive prefix: {path!r} -> {result!r}"
        )


if __name__ == "__main__":
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()
