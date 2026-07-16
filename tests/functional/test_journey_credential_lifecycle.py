"""J14 — Credential lifecycle with CSRF on (docs/functional_test_plan.md).

Intent: the full account lifecycle works as a user experiences it, with
the real CSRF machinery engaged: invite -> accept -> login -> enable
TOTP -> logout -> login with TOTP -> change password -> old password
rejected -> password reset.

Own module-local `app`/`client` fixtures (function-scoped, mirroring
tests/test_csrf.py's `csrf_app`/`csrf_client`), not the shared
session-scoped `app` from tests/conftest.py: that fixture is reused by
every other test file in the same worker with WTF_CSRF_ENABLED=False
baked in at creation time, so flipping it here would corrupt every
other test sharing it. tests/test_backup.py's own module-scoped `app`
override for a similar reason is the established precedent for this.

Documented deviations (the plan's own "deviate only with a documented
reason" rule) -- two places where the plan's text doesn't match current
code:

1. "an old session cookie is no longer authenticated" after a password
   change does not happen in current code. There is no session-version
   or token column on User, login_required only checks
   session.get("user_id") is truthy, and _profile_change_password never
   touches any other client's session -- Flask's signed cookie session
   is stateless with no server-side revocation list. This test asserts
   the real behaviour instead: a second client's pre-existing session
   cookie *remains* authenticated after the first client changes its
   password.

2. There is no self-service "forgot password" route, and no email is
   ever sent for password resets (grepping the whole app/ tree: the only
   send_email call sites are the invite-email path and notification
   dispatch). The only place a PasswordResetToken is created is
   `POST /config/tenants/<id>/reset-password`, gated by
   @require_instance_admin (a global User.is_instance_admin flag,
   distinct from tenant Role.OWNER/ADMIN) -- an instance admin generates
   the link and hands it to the tenant owner out-of-band; the reset
   link is rendered directly on the confirmation page, not emailed. This
   test drives that real flow instead of a nonexistent emailed one.
   Resetting also silently clears the user's TOTP secret (verified in
   app/auth/routes.py), asserted here too since it's a real, notable
   side effect of this path.

Existing: pieces across test_multi_user.py, test_require_totp.py,
test_csrf.py; the single chained flow with real CSRF tokens is new.
"""

import re
import tempfile
import time as _time
from urllib.parse import urlsplit

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
import pyotp  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from init import create_app  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    PasswordResetToken,
    TenantUser,
    User,
    UserInvitation,
    db,
)
from sqlalchemy.pool import StaticPool  # pyright: ignore[reportMissingImports]

_PASSWORD = "OwnerSecret123!"
_NEW_PASSWORD = "OwnerNewSecret456!"
_RESET_PASSWORD = "OwnerResetSecret789!"


@pytest.fixture()
def app():
    upload_dir = tempfile.mkdtemp()
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = True
    app.config["WTF_CSRF_TIME_LIMIT"] = None
    app.config["RATELIMIT_ENABLED"] = False
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    app.config["UPLOAD_FOLDER"] = upload_dir
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.drop_all()
        db.engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()


def _token(resp_data: bytes) -> str:
    match = re.search(rb'<meta name="csrf-token" content="([^"]+)"', resp_data)
    assert match, "csrf-token meta tag not found in response"
    return match.group(1).decode()


def _post(client, get_url, post_url, data):
    """GET `get_url` for a fresh token, POST it to `post_url`, follow
    redirects, and assert no error flash -- csrf-aware sibling of
    tests/functional/conftest.py's submit(), needed since that helper
    never adds a token.
    """
    token = _token(client.get(get_url).data)
    data = dict(data, csrf_token=token)
    resp = client.post(post_url, data=data, follow_redirects=True)
    assert resp.status_code == 200, f"POST {post_url} -> {resp.status_code}"
    assert b"text-bg-danger" not in resp.data, (
        f"POST {post_url}: unexpected error flash:\n"
        f"{resp.data.decode('utf-8', 'replace')[:2000]}"
    )
    return resp


def test_credential_lifecycle_with_csrf_enabled(app, client):
    email = "owner@example.com"

    # /setup wizard (sole_operator, one aircraft) -- same shape as
    # tests/functional/conftest.py's owner_env, but CSRF-aware.
    _post(
        client,
        "/setup",
        "/setup",
        {"step": "account", "email": email, "password": _PASSWORD, "name": "Owner"},
    )
    _post(client, "/setup", "/setup", {"step": "totp", "action": "skip"})
    _post(
        client,
        "/setup",
        "/setup",
        {"step": "operating_model", "operating_model": "sole_operator"},
    )
    _post(client, "/setup", "/setup", {"step": "aircraft_count", "aircraft_count": "1"})

    # Admin invites a second user.
    invite_email = "invitee@example.com"
    _post(
        client,
        "/config/users/",
        "/config/users/invite",
        {
            "email": [invite_email],
            "display_name": ["Invitee"],
            "role": ["pilot"],
            "aircraft_ids": [""],
        },
    )
    with app.app_context():
        invitation = UserInvitation.query.filter_by(email=invite_email).first()
        assert invitation is not None
        invite_token = invitation.token

    # Invitee accepts, sets their own password, on a fresh client (their
    # own session cookie jar).
    invitee = app.test_client()
    accept_url = f"/config/users/invite/{invite_token}"
    invitee_password = "InviteeSecret123!"
    _post(
        invitee,
        accept_url,
        accept_url,
        {
            "email": invite_email,
            "password": invitee_password,
            "password2": invitee_password,
        },
    )
    _post(
        invitee,
        "/login",
        "/login",
        {"email": invite_email, "password": invitee_password},
    )
    assert invitee.get("/").status_code == 200

    # Owner enables TOTP via the real profile flow. profile()'s GET branch
    # pops profile_totp_secret from the session (a deliberate one-shot
    # reveal of the QR/secret), so the confirm step must reuse the token
    # from setup_totp's own POST response rather than _post()'s usual
    # get-a-fresh-token-first pattern -- an intervening GET /profile would
    # silently consume the secret before confirm_totp ever saw it.
    setup_resp = _post(client, "/profile", "/profile", {"action": "setup_totp"})
    with client.session_transaction() as sess:
        totp_secret = sess["profile_totp_secret"]
    confirm_resp = client.post(
        "/profile",
        data={
            "action": "confirm_totp",
            "totp_code": pyotp.TOTP(totp_secret).now(),
            "csrf_token": _token(setup_resp.data),
        },
        follow_redirects=True,
    )
    assert b"text-bg-danger" not in confirm_resp.data, confirm_resp.data[:2000]

    # Successive logins for the same user need distinct TOTP codes: the
    # login route caches "already used" codes per user for 90s
    # (auth.totp.replay guard), so calling pyotp.TOTP(secret).now() twice
    # in quick succession would collide on the same 30s-window code.
    def _next_code(offset_seconds):
        return pyotp.TOTP(totp_secret).at(_time.time() + offset_seconds)

    # A second, independent client logs in as the owner too, *before* the
    # password change below -- its session must still work afterwards
    # (see module docstring, deviation 1).
    other_session_client = app.test_client()
    _post(
        other_session_client,
        "/login",
        "/login",
        {"email": email, "password": _PASSWORD},
    )
    _post(
        other_session_client,
        "/login?step=totp",
        "/login",
        {"step": "totp", "totp_code": _next_code(0)},
    )
    assert other_session_client.get("/profile").status_code == 200

    client.get("/logout")

    # Log back in, now gated by TOTP.
    _post(client, "/login", "/login", {"email": email, "password": _PASSWORD})
    login_resp = client.get("/login?step=totp")
    assert login_resp.status_code == 200
    _post(
        client,
        "/login?step=totp",
        "/login",
        {"step": "totp", "totp_code": _next_code(30)},
    )
    assert client.get("/").status_code == 200

    # Change password.
    _post(
        client,
        "/profile",
        "/profile",
        {
            "action": "change_password",
            "current_password": _PASSWORD,
            "new_password": _NEW_PASSWORD,
            "confirm_password": _NEW_PASSWORD,
        },
    )

    # Old password now fails. A fresh, logged-out client is needed for
    # this check -- `client` is still authenticated post-password-change
    # (changing your own password doesn't log you out), so GET /login on
    # it would just redirect straight back to "/".
    old_password_client = app.test_client()
    login_page = old_password_client.get("/login")
    fail_resp = old_password_client.post(
        "/login",
        data={
            "email": email,
            "password": _PASSWORD,
            "csrf_token": _token(login_page.data),
        },
        follow_redirects=True,
    )
    assert b"Invalid" in fail_resp.data

    # Deviation 1: the OTHER client's session, established before the
    # password change, is still authenticated -- current code has no
    # session-invalidation-on-password-change mechanism (see docstring).
    assert other_session_client.get("/profile").status_code == 200

    # Instance-admin-generated reset link (deviation 2: not an emailed
    # self-service flow). is_instance_admin is a global flag with no UI
    # path to grant it -- a direct write is the only way to create one,
    # matching this suite's "things the UI cannot create" convention.
    with app.app_context():
        admin_user = User(
            email="instance-admin@example.com",
            password_hash=_pw_hash.hash("InstanceAdminPass1!"),
            is_instance_admin=True,
        )
        db.session.add(admin_user)
        db.session.commit()
        owner_user = User.query.filter_by(email=email).first()
        tenant_id = TenantUser.query.filter_by(user_id=owner_user.id).first().tenant_id
        owner_user_id = owner_user.id

    admin_client = app.test_client()
    _post(
        admin_client,
        "/login",
        "/login",
        {"email": "instance-admin@example.com", "password": "InstanceAdminPass1!"},
    )
    reset_resp = _post(
        admin_client,
        "/",  # reset-password is POST-only, no GET to source a token from
        f"/config/tenants/{tenant_id}/reset-password",
        {"owner_user_id": str(owner_user_id)},
    )
    match = re.search(rb'id="reset-url"[^>]*value="([^"]+)"', reset_resp.data)
    assert match, "reset link input not found on tenant_reset_token.html"
    reset_path = urlsplit(match.group(1).decode()).path

    with app.app_context():
        assert PasswordResetToken.query.filter_by(user_id=owner_user_id).count() == 1

    reset_client = app.test_client()
    _post(
        reset_client,
        reset_path,
        reset_path,
        {"new_password": _RESET_PASSWORD, "confirm_password": _RESET_PASSWORD},
    )

    # New password from the reset works; the changed one from before does not.
    login_page = reset_client.get("/login")
    ok_resp = reset_client.post(
        "/login",
        data={
            "email": email,
            "password": _RESET_PASSWORD,
            "csrf_token": _token(login_page.data),
        },
        follow_redirects=True,
    )
    assert b"text-bg-danger" not in ok_resp.data
    # No TOTP step this time: reset silently clears the secret too.
    assert ok_resp.request.path == "/"

    with app.app_context():
        assert User.query.filter_by(email=email).first().totp_secret is None
