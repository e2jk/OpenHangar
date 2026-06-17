"""Gunicorn configuration.

Installs an access logger that masks secret tokens in the request line/path so
they are never written to the access log in clear text (security finding N-25).
The redaction logic lives in :mod:`log_redaction` (unit-tested); this file is
only the gunicorn glue and is excluded from coverage.
"""

import os
import sys
from typing import Any

# Ensure sibling modules (``log_redaction``, ``wsgi``) are importable when
# gunicorn execs this config file, regardless of the launch directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gunicorn.glogging import Logger  # noqa: E402

from log_redaction import redact_sensitive_path  # noqa: E402


class RedactingLogger(Logger):  # type: ignore[misc]  # gunicorn ships no stubs
    """Access logger that masks secret tokens in the request path/line."""

    def atoms(
        self,
        resp: Any,
        req: Any,
        environ: dict[str, Any],
        request_time: Any,
    ) -> dict[str, Any]:
        atoms: dict[str, Any] = super().atoms(resp, req, environ, request_time)
        path = environ.get("PATH_INFO", "") or ""
        redacted = redact_sensitive_path(path)
        if redacted != path:
            atoms["U"] = redacted
            # The raw path appears verbatim inside the request-line atom ("r");
            # replace just that occurrence so the method/protocol stay intact.
            if atoms.get("r"):
                atoms["r"] = atoms["r"].replace(path, redacted, 1)
        return atoms


logger_class = RedactingLogger
