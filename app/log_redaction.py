"""Redact secret tokens from request paths before they reach access logs.

These routes carry a secret directly in the URL path:

* ``/reset-password/<token>``     — password-reset token (single use, short TTL)
* ``/config/users/invite/<token>`` — user-invitation token (single use)
* ``/share/<token>``, ``/share/<registration>/<token>`` — public share link
  (long-lived, read access; the QR code and the aircraft page hand out the
  form with the registration)
* ``/showcase/<token>``, ``/showcase/<registration>/<token>`` and
  ``/showcase/<token>/photos/<id>/img`` — public showcase link
* ``/notifications/snooze/<token>`` — reminder snooze link from e-mails,
  usable without signing in
* ``/passports/join/<token>…`` and ``/passports/public/<token>…`` — passport
  admission and public-view tokens

gunicorn's access log records the full request line, so without redaction the
token would be written to ``/data/logs/openhangar-access.log`` (or stdout) in
clear text — see security finding N-25 (CWE-532). :func:`redact_sensitive_path`
masks the token, leaving the route prefix and query string intact so the access
log keeps its operational value (status, IP, latency, which endpoint was hit).
"""

import re

# All anchored at the start of the path so a prefix appearing deeper in another
# route (e.g. ``/aircraft/<id>/share/create``) never matches. A segment runs up
# to the next ``/``, ``?`` or whitespace.
_SEGMENT = r"[^/?\s]+"

# Reset and invite links: the token is the last segment, so siblings such as
# ``/config/users/invite/<id>/revoke`` keep their (non-secret) id.
_LAST_SEGMENT_RE = re.compile(
    rf"^(/reset-password/|/config/users/invite/){_SEGMENT}(?=$|[?\s])"
)

# Share, showcase and snooze links: every segment after the prefix is masked.
# Which one holds the token depends on the form (one or two segments, photo
# sub-paths), and none of them is worth logging.
_WHOLE_TAIL_RE = re.compile(r"^(/share/|/showcase/|/notifications/snooze/)[^?\s]+")

_PASSPORT_TOKEN_RE = re.compile(
    rf"^(/passports/(?:join|public)/){_SEGMENT}(?=$|[/?\s])"
)


def redact_sensitive_path(path: str) -> str:
    """Return ``path`` with its secret-token part replaced by ``[REDACTED]``.

    Paths that do not start with one of the sensitive token prefixes — or that
    have no token segment — are returned unchanged.
    """

    for pattern in (_LAST_SEGMENT_RE, _WHOLE_TAIL_RE, _PASSPORT_TOKEN_RE):
        path = pattern.sub(r"\1[REDACTED]", path)
    return path
