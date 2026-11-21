"""Joining or registering through an invitation never follows a redirect
target from the request: every destination is built server-side."""

from urllib.parse import urlsplit

from flask import Flask
from flask.testing import FlaskClient

from tests.test_passport_builder import signin
from tests.test_passport_groups import consent_values
from tests.test_passport_invitation_routes import friend, join_url
from tests.test_passport_invitations import invited
from tests.test_passport_management import owner

__all__ = ["friend", "invited", "owner"]

HOSTILE = {"next": "https://evil.example/", "return_to": "//evil.example/x"}


def _local(location: str | None) -> bool:
    parts = urlsplit(location or "")
    return not parts.netloc and (location or "").startswith("/")


def test_join_ignores_redirect_targets_in_the_request(
    app: Flask, client: FlaskClient, friend: dict[str, int]
) -> None:
    url = join_url(app, friend)
    # Signed out: the preview and the sign-in hand-off stay on this site.
    preview = client.get(url, query_string=HOSTILE)
    assert preview.status_code in (200, 302)
    assert preview.status_code == 200 or _local(preview.location)
    signin(client, friend["other"])
    joined = client.post(
        url, query_string=HOSTILE, data=consent_values("Friend") | HOSTILE
    )
    assert joined.status_code == 302 and _local(joined.location)
    assert f"/passports/{friend['passport']}/" in joined.location
    again = client.get(url, query_string=HOSTILE)
    assert _local(again.location)
