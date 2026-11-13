"""The signed-in user's membership is looked up once per request (audit P02),
and never cached beyond it: role and grant changes apply on the next request."""

from flask import session
from models import Role, Tenant, TenantUser, User, db
from sqlalchemy import event


def _member(app, role=Role.ADMIN, email="member@example.com"):
    with app.app_context():
        tenant = Tenant(name="Membership cache")
        user = User(email=email, password_hash="unused", is_active=True)
        db.session.add_all([tenant, user])
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _membership_queries(app, fn):
    seen = []

    def count(_conn, _cursor, statement, *_args):
        if "FROM tenant_users" in " ".join(statement.split()):
            seen.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", count)
    return len(seen)


def test_one_lookup_per_request(app):
    from utils import current_membership, current_user_role

    uid, _ = _member(app)

    def checks():
        with app.test_request_context():
            session["user_id"] = uid
            assert current_user_role() == Role.ADMIN
            assert current_membership().user_id == uid
            assert current_user_role() == Role.ADMIN

    assert _membership_queries(app, checks) == 1


def test_a_role_change_applies_on_the_next_request(app):
    from utils import current_user_role

    uid, tid = _member(app)
    with app.test_request_context():
        session["user_id"] = uid
        assert current_user_role() == Role.ADMIN
    with app.app_context():
        db.session.get(TenantUser, (uid, tid)).role = Role.VIEWER
        db.session.commit()
    with app.test_request_context():
        session["user_id"] = uid
        assert current_user_role() == Role.VIEWER


def test_the_cache_is_per_user_within_a_request(app):
    from utils import current_membership

    first, _ = _member(app, email="first@example.com")
    second, _ = _member(app, Role.PILOT, email="second@example.com")
    with app.test_request_context():
        session["user_id"] = first
        assert current_membership().user_id == first
        session["user_id"] = second  # e.g. a sign-in during this request
        assert current_membership().user_id == second
        session.pop("user_id")
        assert current_membership() is None


def test_role_gated_pages_still_follow_role_changes(app, client):
    uid, tid = _member(app, Role.VIEWER)
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    assert client.get("/config/users/").status_code == 403
    with app.app_context():
        db.session.get(TenantUser, (uid, tid)).role = Role.ADMIN
        db.session.commit()
    assert client.get("/config/users/").status_code == 200
