#!/usr/bin/env python3
"""Sign a seeded user in to a disposable OpenHangar and print its session cookie.

Used by the authenticated ZAP scan (.github/workflows/zap-full-scan.yml) to
give the scanner a real login: CSRF-aware, with the TOTP step when the user
has one. Standard library only; cookies are handled by hand because the
target runs production settings (Secure cookies) over plain HTTP in CI.

  zap_auth_session.py login  BASE_URL EMAIL PASSWORD [TOTP_SECRET]
      prints "session=<value>" once /profile answers 200 with it
  zap_auth_session.py verify BASE_URL "session=<value>"
      exits 1 unless /profile still answers 200 (the scan kept the session)

Only ever point this at a throwaway instance seeded with fake data.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _request(
    url: str, cookie: str, data: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """(status, body, session cookie after the response)."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body)
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        resp = _opener.open(req, timeout=30)
    except urllib.error.HTTPError as err:
        resp = err
    for header in resp.headers.get_all("Set-Cookie") or []:
        match = re.match(r"(session=[^;]*)", header)
        if match:
            cookie = match.group(1)
    return resp.status, resp.read().decode("utf-8", "replace"), cookie


def _csrf(html: str) -> str:
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    if not match:
        raise SystemExit("no CSRF token on the page")
    return match.group(1)


def _totp(secret: str) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(
        key, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1
    ).digest()
    offset = digest[-1] & 0x0F
    code = (
        struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    ) % 1_000_000
    return f"{code:06d}"


def login(base: str, email: str, password: str, totp_secret: str = "") -> str:
    status, html, cookie = _request(f"{base}/login", "")
    status, _, cookie = _request(
        f"{base}/login",
        cookie,
        {"email": email, "password": password, "csrf_token": _csrf(html)},
    )
    if status != 302:
        raise SystemExit(f"credentials step answered {status}")
    if totp_secret:
        _, html, cookie = _request(f"{base}/login?step=totp", cookie)
        status, _, cookie = _request(
            f"{base}/login",
            cookie,
            {
                "step": "totp",
                "totp_code": _totp(totp_secret),
                "csrf_token": _csrf(html),
            },
        )
        if status != 302:
            raise SystemExit(f"TOTP step answered {status}")
    if not verify(base, cookie):
        raise SystemExit("signed in, but /profile does not answer 200")
    return cookie


def verify(base: str, cookie: str) -> bool:
    status, html, _ = _request(f"{base}/profile", cookie)
    return status == 200 and 'id="oh-logout-link"' in html


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "login":
        print(login(*argv[1:5]))
        return 0
    if len(argv) == 3 and argv[0] == "verify":
        ok = verify(argv[1], argv[2])
        print(
            "session still valid"
            if ok
            else "SESSION LOST: /profile no longer answers 200"
        )
        return 0 if ok else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
