"""The passport-only allowlists name real endpoints, so a renamed or mistyped
entry cannot silently deny a page those accounts should reach."""

from flask import Flask
from flask.testing import FlaskClient
from models import Role, Tenant, TenantUser, User, db
from services.account_scope import (
    ACCOUNT_ENDPOINTS,
    LOGBOOK_ENDPOINTS,
    PERSONAL_ENDPOINTS,
)

from tests.test_passport_builder import signin


def test_every_allowlisted_endpoint_exists(app: Flask) -> None:
    registered = {rule.endpoint for rule in app.url_map.iter_rules()}
    allowlisted = PERSONAL_ENDPOINTS | ACCOUNT_ENDPOINTS | LOGBOOK_ENDPOINTS
    assert sorted(allowlisted - registered) == []


def test_passport_only_account_can_read_robots_txt(
    app: Flask, client: FlaskClient
) -> None:
    with app.app_context():
        tenant = Tenant(name="Robots club")
        owner = User(email="robots-owner@example.com", password_hash="unused")
        friend = User(email="robots-friend@example.com", password_hash="unused")
        db.session.add_all([tenant, owner, friend])
        db.session.flush()
        db.session.add_all(
            [
                TenantUser(user_id=owner.id, tenant_id=tenant.id, role=Role.OWNER),
                TenantUser(
                    user_id=friend.id,
                    tenant_id=tenant.id,
                    role=None,
                    access_scope="personal",
                ),
            ]
        )
        db.session.commit()
        friend_id = friend.id
    signin(client, friend_id)
    assert client.get("/robots.txt").status_code == 200


def test_passport_only_account_can_open_public_links(
    app: Flask, client: FlaskClient
) -> None:
    from models import Aircraft, ShareToken

    with app.app_context():
        tenant = Tenant(name="Links club")
        owner = User(email="links-owner@example.com", password_hash="unused")
        friend = User(email="links-friend@example.com", password_hash="unused")
        db.session.add_all([tenant, owner, friend])
        db.session.flush()
        ac = Aircraft(tenant_id=tenant.id, registration="OO-LNK", make="X", model="Y")
        db.session.add_all(
            [
                ac,
                TenantUser(user_id=owner.id, tenant_id=tenant.id, role=Role.OWNER),
                TenantUser(
                    user_id=friend.id,
                    tenant_id=tenant.id,
                    role=None,
                    access_scope="personal",
                ),
            ]
        )
        db.session.flush()
        db.session.add(
            ShareToken(aircraft_id=ac.id, token="lnk12345", access_level="summary")
        )
        db.session.commit()
        friend_id = friend.id
    signin(client, friend_id)
    assert client.get("/share/lnk12345").status_code == 200
    assert client.get("/.well-known/security.txt").status_code == 200
