"""The browser receives storage namespaces, never a reusable login credential."""

from flask import session
from models import Role, Tenant, TenantUser, User, db
from services.offline_identity import identity


def test_no_identity_outside_request_or_without_authenticated_login(app):
    with app.app_context():
        assert identity() == ("", "")
    with app.test_request_context():
        assert identity() == ("", "")
        session["user_id"] = 123
        assert identity() == ("", "")


def test_owner_is_stable_but_login_epoch_changes_and_is_not_a_bearer(app):
    with app.app_context():
        tenant = Tenant(name="Offline account")
        user = User(email="offline@example.com", password_hash="unused")
        db.session.add_all([tenant, user])
        db.session.flush()
        uid, tid = user.id, tenant.id
        db.session.add(TenantUser(user_id=uid, tenant_id=tid, role=Role.PILOT))
        db.session.commit()
    with app.test_request_context():
        session.update(user_id=uid, auth_session="first-bearer-token")
        first = identity()
        session["auth_session"] = "second-bearer-token"
        second = identity()
        assert first[0] == second[0]
        assert first[1] != second[1]
        assert all(len(value) == 64 for value in first + second)
        assert "bearer-token" not in repr((first, second))
    with app.app_context():
        db.session.delete(db.session.get(TenantUser, (uid, tid)))
        db.session.commit()
    with app.test_request_context():
        session.update(user_id=uid, auth_session="second-bearer-token")
        no_tenant = identity()
        assert no_tenant[0] != first[0]
        session["user_id"] = uid + 1
        assert identity()[0] != no_tenant[0]


def test_identity_headers_are_empty_for_anonymous_responses(client):
    response = client.get("/health")
    assert response.headers["X-OH-Offline-Owner"] == ""
    assert response.headers["X-OH-Offline-Session"] == ""


def test_assertions_bind_reads_and_writes_without_logging_out_another_account(
    app, client
):
    import pw_hash

    with app.app_context():
        tenant = Tenant(name="Identity assertions")
        user = User(
            email="assertions@example.com",
            password_hash=pw_hash.hash("AssertionPassword123!"),
        )
        db.session.add_all([tenant, user])
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
    client.post(
        "/login",
        data={"email": "assertions@example.com", "password": "AssertionPassword123!"},
    )
    response = client.get("/profile")
    headers = {
        name: response.headers[name]
        for name in ("X-OH-Offline-Owner", "X-OH-Offline-Session")
    }
    assert all(headers.values())
    assert client.get("/api/offline/identity", headers=headers).status_code == 204
    assert (
        client.get(
            "/api/offline/identity",
            headers={"X-OH-Offline-Owner": headers["X-OH-Offline-Owner"]},
        ).status_code
        == 409
    )
    wrong = {**headers, "X-OH-Offline-Owner": "retired-owner"}
    assert (
        client.post(
            "/profile",
            headers=wrong,
            data={"action": "update_name", "name": "Rejected"},
        ).status_code
        == 409
    )
    assert client.get("/profile").status_code == 200
    with app.app_context():
        assert User.query.filter_by(email="assertions@example.com").one().name is None


def test_credential_renewal_uses_fresh_navigation_for_htmx(app, client):
    import pw_hash

    with app.app_context():
        tenant = Tenant(name="Credential renewal")
        user = User(
            email="renew@example.com", password_hash=pw_hash.hash("InitialPassword123!")
        )
        db.session.add_all([tenant, user])
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
    client.post(
        "/login", data={"email": "renew@example.com", "password": "InitialPassword123!"}
    )
    before = client.get("/profile")
    headers = {
        name: before.headers[name]
        for name in ("X-OH-Offline-Owner", "X-OH-Offline-Session")
    }
    response = client.post(
        "/profile",
        headers={**headers, "HX-Request": "true"},
        data={
            "action": "change_password",
            "current_password": "InitialPassword123!",
            "new_password": "ReplacementPassword123!",
            "confirm_password": "ReplacementPassword123!",
        },
    )
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/profile"
    assert response.headers["X-OH-Offline-Owner"] == headers["X-OH-Offline-Owner"]
    assert response.headers["X-OH-Offline-Session"] != headers["X-OH-Offline-Session"]
    assert client.get("/profile").status_code == 200


def _signed_in(app, client, email):
    import pw_hash

    with app.app_context():
        tenant = Tenant(name=f"Lifetime {email}")
        user = User(email=email, password_hash=pw_hash.hash("LifetimePassword123!"))
        db.session.add_all([tenant, user])
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        uid = user.id
    login = client.post(
        "/login", data={"email": email, "password": "LifetimePassword123!"}
    )
    return uid, login


def test_signed_in_responses_tell_the_browser_how_long_the_login_lasts(app, client):
    """The worker drops its saved pages once the login has ended, even offline."""
    import time

    from models import AuthSession

    assert "X-OH-Login-Expires-In" not in client.get("/health").headers
    uid, login = _signed_in(app, client, "lifetime@example.com")
    lifetime = int(app.permanent_session_lifetime.total_seconds())
    # The sign-in answer itself already carries it (the login was just made).
    assert lifetime - 5 <= int(login.headers["X-OH-Login-Expires-In"]) <= lifetime
    assert login.headers["X-OH-Instant-Pages"] == "1"
    with app.app_context():
        AuthSession.query.filter_by(user_id=uid).update(
            {"expires_at": int(time.time()) + 600}, synchronize_session=False
        )
        db.session.commit()
    response = client.get("/profile")
    assert 590 <= int(response.headers["X-OH-Login-Expires-In"]) <= 600
    assert response.headers["X-OH-Instant-Pages"] == "1"
    signed_out = client.get("/logout")
    assert "X-OH-Login-Expires-In" not in signed_out.headers
    assert "X-OH-Instant-Pages" not in signed_out.headers


def test_instant_pages_off_is_sent_and_shown_on_the_settings_page(app, client):
    _signed_in(app, client, "instant-off@example.com")
    page = client.get("/config/")
    assert page.status_code == 200
    assert b"OPENHANGAR_INSTANT_PAGES" in page.data
    assert b'id="instant-pages-status"' in page.data
    app.config["INSTANT_PAGES"] = False
    try:
        response = client.get("/config/")
        assert "X-OH-Instant-Pages" not in response.headers
        assert "X-OH-Login-Expires-In" in response.headers
        html = response.data.decode()
        status = html.split('id="instant-pages-status"', 1)[1].split("</div>", 1)[0]
        assert "Disabled" in status
    finally:
        app.config["INSTANT_PAGES"] = True


def test_instant_pages_variable_is_on_unless_explicitly_off(monkeypatch):
    from services.offline_identity import instant_pages_enabled

    monkeypatch.delenv("OPENHANGAR_INSTANT_PAGES", raising=False)
    assert instant_pages_enabled()
    for value in ("false", "0", "no", " FALSE "):
        monkeypatch.setenv("OPENHANGAR_INSTANT_PAGES", value)
        assert not instant_pages_enabled()
    for value in ("true", "1", "yes", ""):
        monkeypatch.setenv("OPENHANGAR_INSTANT_PAGES", value)
        assert instant_pages_enabled()


def test_seconds_left_is_none_without_a_validated_login(app):
    from services.auth_sessions import seconds_left

    with app.test_request_context():
        assert seconds_left() is None
