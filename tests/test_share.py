"""
Tests for Phase 11: Read-only Share Links.
"""
from datetime import datetime, timezone

import bcrypt  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft, FlightEntry, MaintenanceTrigger, Role, ShareToken,
    Tenant, TenantUser, TriggerType, User, db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _setup(app):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="pilot@share.test",
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN))
        ac = Aircraft(tenant_id=tenant.id, registration="OO-TST", make="Cessna", model="172S")
        db.session.add(ac)
        db.session.commit()
        return user.id, tenant.id, ac.id


def _login(app, client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def _add_token(app, aircraft_id, token="tsttoken", access_level="summary", revoked=False):
    with app.app_context():
        st = ShareToken(aircraft_id=aircraft_id, token=token, access_level=access_level)
        if revoked:
            st.revoked_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.session.add(st)
        db.session.commit()
        return st.id


# ── Model tests ───────────────────────────────────────────────────────────────

class TestShareTokenModel:
    def test_is_active_true_when_not_revoked(self, app):
        with app.app_context():
            st = ShareToken(aircraft_id=1, token="abc12345", access_level="summary")
            assert st.is_active is True

    def test_is_active_false_when_revoked(self, app):
        with app.app_context():
            st = ShareToken(
                aircraft_id=1, token="abc12345", access_level="summary",
                revoked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            assert st.is_active is False

    def test_default_access_level(self, app):
        uid, tid, acid = _setup(app)
        with app.app_context():
            st = ShareToken(aircraft_id=acid, token="tok12345")
            db.session.add(st)
            db.session.commit()
            assert st.access_level == "summary"

    def test_persists_all_fields(self, app):
        uid, tid, acid = _setup(app)
        with app.app_context():
            st = ShareToken(aircraft_id=acid, token="pers1234", access_level="full")
            db.session.add(st)
            db.session.commit()
            fetched = db.session.get(ShareToken, st.id)
            assert fetched.token == "pers1234"
            assert fetched.access_level == "full"
            assert fetched.revoked_at is None
            assert fetched.created_at is not None

    def test_cascade_delete_with_aircraft(self, app):
        uid, tid, acid = _setup(app)
        _add_token(app, acid, "del12345")
        with app.app_context():
            ac = db.session.get(Aircraft, acid)
            db.session.delete(ac)
            db.session.commit()
            assert ShareToken.query.filter_by(token="del12345").first() is None


# ── _generate_token ───────────────────────────────────────────────────────────

class TestGenerateToken:
    def test_token_is_8_chars(self, app):
        from share.routes import _generate_token  # pyright: ignore[reportMissingImports]
        uid, tid, acid = _setup(app)
        with app.app_context():
            token = _generate_token()
            assert len(token) == 8

    def test_tokens_are_unique(self, app):
        from share.routes import _generate_token  # pyright: ignore[reportMissingImports]
        uid, tid, acid = _setup(app)
        with app.app_context():
            tokens = {_generate_token() for _ in range(20)}
            assert len(tokens) == 20


# ── create_token view ─────────────────────────────────────────────────────────

class TestCreateToken:
    def test_redirect_if_not_logged_in(self, client, app):
        uid, tid, acid = _setup(app)
        resp = client.post(f"/aircraft/{acid}/share/create")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_creates_summary_token(self, app, client):
        uid, tid, acid = _setup(app)
        _login(app, client, uid)
        resp = client.post(f"/aircraft/{acid}/share/create", data={"access_level": "summary"})
        assert resp.status_code == 302
        with app.app_context():
            st = ShareToken.query.filter_by(aircraft_id=acid).first()
            assert st is not None
            assert st.access_level == "summary"
            assert len(st.token) == 8

    def test_creates_full_token(self, app, client):
        uid, tid, acid = _setup(app)
        _login(app, client, uid)
        client.post(f"/aircraft/{acid}/share/create", data={"access_level": "full"})
        with app.app_context():
            st = ShareToken.query.filter_by(aircraft_id=acid).first()
            assert st.access_level == "full"

    def test_invalid_access_level_defaults_to_summary(self, app, client):
        uid, tid, acid = _setup(app)
        _login(app, client, uid)
        client.post(f"/aircraft/{acid}/share/create", data={"access_level": "admin"})
        with app.app_context():
            st = ShareToken.query.filter_by(aircraft_id=acid).first()
            assert st.access_level == "summary"

    def test_404_for_other_tenants_aircraft(self, app, client):
        uid, tid, acid = _setup(app)
        _login(app, client, uid)
        with app.app_context():
            other_tenant = Tenant(name="Other")
            db.session.add(other_tenant)
            db.session.flush()
            other_ac = Aircraft(tenant_id=other_tenant.id, registration="OO-OTH", make="X", model="Y")
            db.session.add(other_ac)
            db.session.commit()
            other_acid = other_ac.id
        resp = client.post(f"/aircraft/{other_acid}/share/create", data={"access_level": "summary"})
        assert resp.status_code == 404

    def test_403_when_user_has_no_tenant(self, app, client):
        uid, tid, acid = _setup(app)
        # Create a user with no TenantUser association
        with app.app_context():
            orphan = User(
                email="orphan@share.test",
                password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
                is_active=True,
            )
            db.session.add(orphan)
            db.session.commit()
            orphan_id = orphan.id
        _login(app, client, orphan_id)
        resp = client.post(f"/aircraft/{acid}/share/create", data={"access_level": "summary"})
        assert resp.status_code == 403


# ── revoke_token view ─────────────────────────────────────────────────────────

class TestRevokeToken:
    def test_revoke_sets_revoked_at(self, app, client):
        uid, tid, acid = _setup(app)
        _login(app, client, uid)
        token_id = _add_token(app, acid, "rev12345")
        resp = client.post(f"/aircraft/{acid}/share/{token_id}/revoke")
        assert resp.status_code == 302
        with app.app_context():
            st = db.session.get(ShareToken, token_id)
            assert st.revoked_at is not None
            assert not st.is_active

    def test_404_for_wrong_aircraft(self, app, client):
        uid, tid, acid = _setup(app)
        _login(app, client, uid)
        with app.app_context():
            other_ac = Aircraft(tenant_id=tid, registration="OO-OT2", make="X", model="Y")
            db.session.add(other_ac)
            db.session.commit()
            other_acid = other_ac.id
        token_id = _add_token(app, other_acid, "oth12345")
        resp = client.post(f"/aircraft/{acid}/share/{token_id}/revoke")
        assert resp.status_code == 404

    def test_redirect_if_not_logged_in(self, app, client):
        uid, tid, acid = _setup(app)
        token_id = _add_token(app, acid, "nli12345")
        resp = client.post(f"/aircraft/{acid}/share/{token_id}/revoke")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ── public_view ───────────────────────────────────────────────────────────────

class TestPublicView:
    def test_404_for_unknown_token(self, client):
        resp = client.get("/share/zzzzzzzz")
        assert resp.status_code == 404

    def test_404_for_revoked_token(self, app, client):
        uid, tid, acid = _setup(app)
        _add_token(app, acid, "rvk12345", revoked=True)
        resp = client.get("/share/rvk12345")
        assert resp.status_code == 404

    def test_200_for_valid_token(self, app, client):
        uid, tid, acid = _setup(app)
        _add_token(app, acid, "ok123456")
        resp = client.get("/share/ok123456")
        assert resp.status_code == 200
        assert b"OO-TST" in resp.data

    def test_noindex_header_set(self, app, client):
        uid, tid, acid = _setup(app)
        _add_token(app, acid, "hdr12345")
        resp = client.get("/share/hdr12345")
        assert "noindex" in resp.headers.get("X-Robots-Tag", "")
        assert "nofollow" in resp.headers.get("X-Robots-Tag", "")

    def test_noindex_meta_tag_in_html(self, app, client):
        uid, tid, acid = _setup(app)
        _add_token(app, acid, "met12345")
        resp = client.get("/share/met12345")
        assert b'content="noindex, nofollow"' in resp.data

    def test_summary_hides_hobbs(self, app, client):
        uid, tid, acid = _setup(app)
        with app.app_context():
            db.session.add(FlightEntry(
                aircraft_id=acid, date=datetime(2026, 1, 1).date(),
                departure_icao="EBOS", arrival_icao="EBBR",
                flight_time_counter_start=100.0, flight_time_counter_end=101.5,
            ))
            db.session.commit()
        _add_token(app, acid, "sum12345", access_level="summary")
        resp = client.get("/share/sum12345")
        assert b"101" not in resp.data  # hobbs value not shown in summary

    def test_full_shows_hobbs(self, app, client):
        uid, tid, acid = _setup(app)
        with app.app_context():
            db.session.add(FlightEntry(
                aircraft_id=acid, date=datetime(2026, 1, 1).date(),
                departure_icao="EBOS", arrival_icao="EBBR",
                flight_time_counter_start=100.0, flight_time_counter_end=101.5,
            ))
            db.session.commit()
        _add_token(app, acid, "ful12345", access_level="full")
        resp = client.get("/share/ful12345")
        assert b"101.5" in resp.data

    def test_full_shows_recent_flights(self, app, client):
        uid, tid, acid = _setup(app)
        with app.app_context():
            db.session.add(FlightEntry(
                aircraft_id=acid, date=datetime(2026, 3, 1).date(),
                departure_icao="EBOS", arrival_icao="ELLX",
                flight_time_counter_start=200.0, flight_time_counter_end=201.2,
            ))
            db.session.commit()
        _add_token(app, acid, "flt12345", access_level="full")
        resp = client.get("/share/flt12345")
        assert b"EBOS" in resp.data
        assert b"ELLX" in resp.data

    def test_summary_hides_recent_flights(self, app, client):
        uid, tid, acid = _setup(app)
        with app.app_context():
            db.session.add(FlightEntry(
                aircraft_id=acid, date=datetime(2026, 3, 1).date(),
                departure_icao="EBOS", arrival_icao="ELLX",
                flight_time_counter_start=200.0, flight_time_counter_end=201.2,
            ))
            db.session.commit()
        _add_token(app, acid, "nfl12345", access_level="summary")
        resp = client.get("/share/nfl12345")
        assert b"ELLX" not in resp.data

    def test_maintenance_items_shown(self, app, client):
        uid, tid, acid = _setup(app)
        with app.app_context():
            db.session.add(MaintenanceTrigger(
                aircraft_id=acid, name="Annual inspection",
                trigger_type=TriggerType.CALENDAR,
                due_date=datetime(2027, 1, 1).date(), interval_days=365,
            ))
            db.session.commit()
        _add_token(app, acid, "mnt12345")
        resp = client.get("/share/mnt12345")
        assert b"Annual inspection" in resp.data

    def test_full_shows_due_date_in_maintenance(self, app, client):
        uid, tid, acid = _setup(app)
        with app.app_context():
            db.session.add(MaintenanceTrigger(
                aircraft_id=acid, name="ARC",
                trigger_type=TriggerType.CALENDAR,
                due_date=datetime(2027, 6, 15).date(), interval_days=365,
            ))
            db.session.commit()
        _add_token(app, acid, "ddt12345", access_level="full")
        resp = client.get("/share/ddt12345")
        assert b"2027-06-15" in resp.data

    def test_summary_hides_due_date(self, app, client):
        uid, tid, acid = _setup(app)
        with app.app_context():
            db.session.add(MaintenanceTrigger(
                aircraft_id=acid, name="ARC",
                trigger_type=TriggerType.CALENDAR,
                due_date=datetime(2027, 6, 15).date(), interval_days=365,
            ))
            db.session.commit()
        _add_token(app, acid, "ndd12345", access_level="summary")
        resp = client.get("/share/ndd12345")
        assert b"2027-06-15" not in resp.data

    def test_no_login_required(self, app, client):
        uid, tid, acid = _setup(app)
        _add_token(app, acid, "pub12345")
        # Deliberately do NOT call _login()
        resp = client.get("/share/pub12345")
        assert resp.status_code == 200


# ── token_qr view ─────────────────────────────────────────────────────────────

class TestTokenQr:
    def test_redirect_if_not_logged_in(self, app, client):
        uid, tid, acid = _setup(app)
        token_id = _add_token(app, acid, "qrn12345")
        resp = client.get(f"/aircraft/{acid}/share/{token_id}/qr")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_returns_png(self, app, client):
        uid, tid, acid = _setup(app)
        _login(app, client, uid)
        token_id = _add_token(app, acid, "qrp12345")
        resp = client.get(f"/aircraft/{acid}/share/{token_id}/qr")
        assert resp.status_code == 200
        assert resp.content_type == "image/png"
        assert resp.data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes

    def test_404_for_revoked_token(self, app, client):
        uid, tid, acid = _setup(app)
        _login(app, client, uid)
        token_id = _add_token(app, acid, "qrr12345", revoked=True)
        resp = client.get(f"/aircraft/{acid}/share/{token_id}/qr")
        assert resp.status_code == 404

    def test_404_for_wrong_aircraft(self, app, client):
        uid, tid, acid = _setup(app)
        _login(app, client, uid)
        with app.app_context():
            other_ac = Aircraft(tenant_id=tid, registration="OO-QR2", make="X", model="Y")
            db.session.add(other_ac)
            db.session.commit()
            other_acid = other_ac.id
        token_id = _add_token(app, other_acid, "qrw12345")
        resp = client.get(f"/aircraft/{acid}/share/{token_id}/qr")
        assert resp.status_code == 404

    def test_content_disposition_filename(self, app, client):
        uid, tid, acid = _setup(app)
        _login(app, client, uid)
        token_id = _add_token(app, acid, "qrfn1234")
        resp = client.get(f"/aircraft/{acid}/share/{token_id}/qr")
        assert b"qrfn1234" in resp.headers["Content-Disposition"].encode()
