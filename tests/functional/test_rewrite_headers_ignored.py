"""The signed-in ZAP scan's "Bypassing 403" (40038) sent `GET /` with
`X-Original-URL: /aircraft/<id>/edit` (a page the pilot may not open) and
took the 200 for a bypass. The app never routes on those headers (only
ProxyFix's X-Forwarded-* are trusted), so that 200 was the plain home page.
"""

import pytest  # pyright: ignore[reportMissingImports]
from flask import request  # pyright: ignore[reportMissingImports]

REWRITE_HEADERS = ("X-Original-URL", "X-Rewrite-URL")


@pytest.mark.parametrize("header", REWRITE_HEADERS)
def test_rewrite_headers_do_not_change_the_page(app, header):
    with app.test_request_context("/", headers={header: "/config/users/"}):
        assert request.path == "/"
        assert request.url_rule is not None
        assert request.url_rule.endpoint == "index"
