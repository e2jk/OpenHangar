"""
Tests for Phase 12: Snag List routes, model, and grounding propagation.
"""
import bcrypt  # pyright: ignore[reportMissingImports]
from datetime import datetime, timezone

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft, Role, Snag, Tenant, TenantUser, User, db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_user_and_tenant(app, email="pilot@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(b"testpassword123", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN))
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id, registration="OO-TST"):
    with app.app_context():
        ac = Aircraft(tenant_id=tenant_id, registration=registration,
                      make="Cessna", model="172S")
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_snag(app, aircraft_id, title="Cracked cowling fastener",
              is_grounding=False, reporter=None, resolved=False):
    with app.app_context():
        s = Snag(
            aircraft_id=aircraft_id,
            title=title,
            description="Observed during pre-flight.",
            reporter=reporter,
            is_grounding=is_grounding,
        )
        if resolved:
            s.resolved_at = datetime.now(timezone.utc)
            s.resolution_note = "Replaced fastener."
        db.session.add(s)
        db.session.commit()
        return s.id


def _login_orphan_user(app, client):
    with app.app_context():
        user = User(
            email="orphan@example.com",
            password_hash=bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as sess:
        sess["user_id"] = uid


# ── Snag model ────────────────────────────────────────────────────────────────

class TestSnagModel:
    def test_is_open_true_when_not_resolved(self, app):
        with app.app_context():
            s = Snag(aircraft_id=1, title="Test", is_grounding=False)
            assert s.is_open is True

    def test_is_open_false_when_resolved(self, app):
        with app.app_context():
            s = Snag(aircraft_id=1, title="Test", is_grounding=False,
                     resolved_at=datetime.now(timezone.utc))
            assert s.is_open is False

    def test_aircraft_is_grounded_with_open_grounding_snag(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, is_grounding=True)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.is_grounded is True

    def test_aircraft_not_grounded_with_non_grounding_snag(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, is_grounding=False)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.is_grounded is False

    def test_aircraft_not_grounded_when_grounding_snag_resolved(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, is_grounding=True, resolved=True)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.is_grounded is False

    def test_aircraft_not_grounded_with_no_snags(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.is_grounded is False

    def test_cascade_delete_snags_with_aircraft(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            db.session.delete(ac)
            db.session.commit()
            assert db.session.get(Snag, snag_id) is None


# ── List snags ────────────────────────────────────────────────────────────────

class TestListSnags:
    def test_redirects_when_not_logged_in(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/snags")
        assert resp.status_code == 302

    def test_404_for_wrong_tenant(self, app, client):
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac_id = _add_aircraft(app, t2)
        _login(app, client, "a@example.com")
        resp = client.get(f"/aircraft/{ac_id}/snags")
        assert resp.status_code == 404

    def test_shows_open_snags(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, title="Left door seal worn")
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/snags")
        assert resp.status_code == 200
        assert b"Left door seal worn" in resp.data

    def test_shows_closed_snags_section(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, title="Old issue", resolved=True)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/snags")
        assert resp.status_code == 200
        assert b"Old issue" in resp.data

    def test_shows_grounded_banner_when_grounding_snag(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, is_grounding=True)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/snags")
        assert b"grounded" in resp.data.lower()

    def test_403_when_user_has_no_tenant(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login_orphan_user(app, client)
        resp = client.get(f"/aircraft/{ac_id}/snags")
        assert resp.status_code == 403


# ── New snag ──────────────────────────────────────────────────────────────────

class TestNewSnag:
    def test_get_shows_form(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/snags/new")
        assert resp.status_code == 200
        assert b"Log Snag" in resp.data

    def test_post_creates_snag(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/snags/new", data={
            "title": "Fuel cap missing",
            "description": "Left wing fuel cap not found after flight.",
            "reporter": "J. Smith",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Fuel cap missing" in resp.data

    def test_post_creates_grounding_snag(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(f"/aircraft/{ac_id}/snags/new", data={
            "title": "Main gear collapse",
            "is_grounding": "on",
        })
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.is_grounded is True

    def test_post_empty_title_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/snags/new", data={"title": ""})
        assert resp.status_code == 200
        assert b"Title is required" in resp.data

    def test_redirects_when_not_logged_in(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.post(f"/aircraft/{ac_id}/snags/new", data={"title": "x"})
        assert resp.status_code == 302


# ── Edit snag ─────────────────────────────────────────────────────────────────

class TestEditSnag:
    def test_get_shows_form(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/snags/{snag_id}/edit")
        assert resp.status_code == 200
        assert b"Edit Snag" in resp.data

    def test_post_updates_snag(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id, title="Old title")
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/snags/{snag_id}/edit", data={
            "title": "Updated title",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Updated title" in resp.data

    def test_cannot_edit_closed_snag(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id, resolved=True)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/snags/{snag_id}/edit",
                          follow_redirects=True)
        assert b"cannot be edited" in resp.data

    def test_404_wrong_aircraft(self, app, client):
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac1 = _add_aircraft(app, t1, "OO-A")
        ac2 = _add_aircraft(app, t2, "OO-B")
        snag_id = _add_snag(app, ac2)
        _login(app, client, "a@example.com")
        resp = client.get(f"/aircraft/{ac1}/snags/{snag_id}/edit")
        assert resp.status_code == 404

    def test_empty_title_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/snags/{snag_id}/edit",
                           data={"title": ""})
        assert b"Title is required" in resp.data


# ── Resolve snag ──────────────────────────────────────────────────────────────

class TestResolveSnag:
    def test_get_shows_form(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/snags/{snag_id}/resolve")
        assert resp.status_code == 200
        assert b"Resolve Snag" in resp.data

    def test_post_closes_snag(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/snags/{snag_id}/resolve",
                           data={"resolution_note": "Fixed by mechanic."}, follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            s = db.session.get(Snag, snag_id)
            assert s.is_open is False
            assert s.resolution_note == "Fixed by mechanic."

    def test_resolving_grounding_snag_ungrounds_aircraft(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id, is_grounding=True)
        _login(app, client)
        with app.app_context():
            assert db.session.get(Aircraft, ac_id).is_grounded is True
        client.post(f"/aircraft/{ac_id}/snags/{snag_id}/resolve",
                    data={"resolution_note": "Gear door repaired."})
        with app.app_context():
            assert db.session.get(Aircraft, ac_id).is_grounded is False

    def test_empty_note_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/snags/{snag_id}/resolve",
                           data={"resolution_note": ""})
        assert b"resolution note is required" in resp.data.lower()

    def test_already_closed_snag_redirects(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id, resolved=True)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/snags/{snag_id}/resolve",
                          follow_redirects=True)
        assert b"already closed" in resp.data.lower()

    def test_post_already_closed_redirects(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id, resolved=True)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/snags/{snag_id}/resolve",
                           data={"resolution_note": "Already done."},
                           follow_redirects=True)
        assert b"already closed" in resp.data.lower()


# ── Delete snag ───────────────────────────────────────────────────────────────

class TestDeleteSnag:
    def test_delete_removes_snag(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        snag_id = _add_snag(app, ac_id, title="To be deleted")
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/snags/{snag_id}/delete",
                           follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert db.session.get(Snag, snag_id) is None

    def test_404_wrong_aircraft(self, app, client):
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac1 = _add_aircraft(app, t1, "OO-A")
        ac2 = _add_aircraft(app, t2, "OO-B")
        snag_id = _add_snag(app, ac2)
        _login(app, client, "a@example.com")
        resp = client.post(f"/aircraft/{ac1}/snags/{snag_id}/delete")
        assert resp.status_code == 404


# ── Grounding status in aircraft list / dashboard ─────────────────────────────

class TestGroundingStatus:
    def test_compute_statuses_returns_grounded(self, app):
        from utils import compute_aircraft_statuses  # pyright: ignore[reportMissingImports]
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, is_grounding=True)
        with app.app_context():
            aircraft = Aircraft.query.filter_by(tenant_id=tenant_id).all()
            statuses = compute_aircraft_statuses(aircraft, [], {})
            assert statuses[ac_id] == "grounded"

    def test_compute_statuses_ok_when_no_grounding_snag(self, app):
        from utils import compute_aircraft_statuses  # pyright: ignore[reportMissingImports]
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, is_grounding=False)
        with app.app_context():
            aircraft = Aircraft.query.filter_by(tenant_id=tenant_id).all()
            statuses = compute_aircraft_statuses(aircraft, [], {})
            assert statuses[ac_id] == "ok"

    def test_aircraft_list_shows_grounded_badge(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, is_grounding=True)
        _login(app, client)
        resp = client.get("/aircraft/")
        assert b"GROUNDED" in resp.data

    def test_dashboard_shows_grounded_in_fleet(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, is_grounding=True)
        _login(app, client)
        resp = client.get("/")
        assert b"GROUNDED" in resp.data

    def test_dashboard_shows_grounding_snag_in_alerts(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, title="Gear door unsafe", is_grounding=True)
        _login(app, client)
        resp = client.get("/")
        assert b"Gear door unsafe" in resp.data

    def test_aircraft_detail_shows_grounded_banner(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_snag(app, ac_id, is_grounding=True)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}")
        assert b"grounded" in resp.data.lower()
