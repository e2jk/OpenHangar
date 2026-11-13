"""An instance admin who belongs to no tenant is refused tenant pages cleanly.

Since logins are validated server-side, an instance admin can be signed in
without any tenant membership (e.g. after leaving the last organisation).
Tenant-scoped pages must then answer 403 (or send them elsewhere), never
fail with a 500 or act on another tenant's data.
"""

import pw_hash
import pytest
from models import User, db

ROUTES = [
    ("post", "/config/tenant-slug"),
    ("post", "/config/profile"),
    ("post", "/config/email/test"),
    ("get", "/config/renters/"),
    ("get", "/config/renters/add"),
    ("get", "/config/renters/1/edit"),
    ("post", "/config/renters/1/revoke"),
    ("get", "/config/renters/1/account"),
    ("post", "/config/renters/1/account/payment"),
    ("get", "/config/renters/1/account/statement.csv"),
    ("get", "/reservations/fleet/"),
    ("get", "/my/account"),
    ("get", "/my/account/statement.csv"),
    ("get", "/config/users/"),
    ("get", "/documents/reconcile"),
    ("post", "/aircraft/1/share/create"),
]


@pytest.fixture
def lone_admin(app, client):
    with app.app_context():
        admin = User(
            email="lone-admin@example.com",
            password_hash=pw_hash.hash("LoneAdminPassword123!"),
            is_instance_admin=True,
        )
        db.session.add(admin)
        db.session.commit()
    client.post(
        "/login",
        data={"email": "lone-admin@example.com", "password": "LoneAdminPassword123!"},
    )
    assert client.get("/profile").status_code == 200
    return client


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_tenant_pages_refuse_an_admin_without_a_tenant(lone_admin, method, path):
    response = getattr(lone_admin, method)(path, data={"slug": "x", "name": "x"})
    assert response.status_code in (302, 403, 404), (path, response.status_code)
