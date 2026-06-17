"""Redact secret tokens from request paths before they reach access logs.

Three routes carry a single secret directly in the URL path:

* ``/reset-password/<token>``     — password-reset token (single use, short TTL)
* ``/share/<token>``              — public share link (long-lived, read access)
* ``/config/users/invite/<token>`` — user-invitation token (single use)

gunicorn's access log records the full request line, so without redaction the
token would be written to ``/data/logs/openhangar-access.log`` (or stdout) in
clear text — see security finding N-25 (CWE-532). :func:`redact_sensitive_path`
masks just the token segment, leaving the rest of the path intact so the access
log keeps its operational value (status, IP, latency, which endpoint was hit).
"""

import re

# Anchored at the start of the path so we only match the three token routes and
# never their siblings (e.g. ``/aircraft/<id>/share/create`` or
# ``/config/users/invite/<id>/revoke``).  The token segment runs up to the next
# ``/``, ``?`` or whitespace; the lookahead keeps any query string intact.
_SENSITIVE_PATH_RE = re.compile(
    r"^(/reset-password/|/share/|/config/users/invite/)[^/?\s]+(?=$|[?\s])"
)


def redact_sensitive_path(path: str) -> str:
    """Return ``path`` with a trailing secret-token segment replaced by
    ``[REDACTED]``.

    Paths that do not start with one of the sensitive token prefixes — or that
    have no token segment — are returned unchanged.
    """

    return _SENSITIVE_PATH_RE.sub(r"\1[REDACTED]", path)
