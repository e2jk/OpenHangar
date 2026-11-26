"""A browser that signed in to an account before is not stopped by that
account's lock, and a successful sign-in keeps the address's failures."""

import pw_hash as _pw  # pyright: ignore[reportMissingImports]
from models import Role, Tenant, TenantUser, User, db  # pyright: ignore[reportMissingImports]
from services import login_throttle  # pyright: ignore[reportMissingImports]

EMAIL = "device@test.com"
PASSWORD = "TestPassword1!"


def _make_user(app):
    with app.app_context():
        tenant = Tenant(name="Device Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(email=EMAIL, password_hash=_pw.hash(PASSWORD), is_active=True)
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.PILOT)
        )
        db.session.commit()


def _lock_account(client):
    """Ten wrong passwords from ten other addresses lock the account."""
    for i in range(10):
        client.post(
            "/login",
            data={"email": EMAIL, "password": "wrong"},
            environ_base={"REMOTE_ADDR": f"192.0.2.{i + 1}"},
        )


def _sign_in(client, password=PASSWORD):
    return client.post("/login", data={"email": EMAIL, "password": password})


def test_a_known_browser_signs_in_while_the_account_is_locked(app, client):
    _make_user(app)
    assert _sign_in(client).status_code == 302
    assert client.get_cookie("oh_login_device") is not None
    client.get("/logout")
    _lock_account(client)
    resp = _sign_in(client)
    assert resp.status_code == 302 and "/login" not in resp.headers["Location"]


def test_a_new_browser_stays_locked_out(app, client):
    _make_user(app)
    _lock_account(client)
    assert _sign_in(client).status_code == 429


def test_a_forged_device_cookie_does_not_bypass_the_lock(app, client):
    _make_user(app)
    _lock_account(client)
    client.set_cookie("oh_login_device", "forged.value")
    assert _sign_in(client).status_code == 429


def test_another_accounts_device_cookie_does_not_bypass_the_lock(app, client):
    from auth.routes import _device_serializer  # pyright: ignore[reportMissingImports]

    _make_user(app)
    _lock_account(client)
    with app.test_request_context():
        cookie = _device_serializer().dumps("acct:someone-else")
    client.set_cookie("oh_login_device", cookie)
    assert _sign_in(client).status_code == 429


def test_signing_in_keeps_the_addresss_failures(app, client):
    from auth.routes import _ip_key  # pyright: ignore[reportMissingImports]

    _make_user(app)
    _sign_in(client, password="wrong-password")
    _sign_in(client)
    with app.app_context():
        assert login_throttle.record_failure(_ip_key("127.0.0.1"), 3600) == 2
