"""
Tests for Phase 37c: Renter authorization — model validity matrix, config
CRUD routes, the reservation create/confirm guard, and the expiry
notification digest. See docs/phase37_rental_spec.md § 37c.
"""

from datetime import date, timedelta
from unittest.mock import patch

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Document,
    NotificationType,
    RenterAuthorization,
    Reservation,
    ReservationStatus,
    Role,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    UserAircraftAccess,
    db,
)


def _make_user(app, email, role=Role.PILOT):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(email=email, password_hash=_pw_hash.hash("pw"), is_active=True)
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _make_aircraft(app, tenant_id, reg="OO-TST"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=reg, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _grant_access(app, user_id, aircraft_id):
    with app.app_context():
        db.session.add(UserAircraftAccess(user_id=user_id, aircraft_id=aircraft_id))
        db.session.commit()


def _login(app, client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _add_authorization(app, tenant_id, renter_user_id, **kwargs):
    with app.app_context():
        kwargs.setdefault("granted_on", date(2026, 1, 1))
        auth = RenterAuthorization(
            tenant_id=tenant_id, renter_user_id=renter_user_id, **kwargs
        )
        db.session.add(auth)
        db.session.commit()
        return auth.id


# ── Model: is_valid / valid_for ─────────────────────────────────────────────────


class TestIsValid:
    def test_valid_with_no_expiry(self, app):
        uid, tid = _make_user(app, "renter1@ex.com")
        aid = _add_authorization(app, tid, uid)
        with app.app_context():
            auth = db.session.get(RenterAuthorization, aid)
            assert auth.is_valid is True

    def test_revoked_is_invalid(self, app):
        from datetime import datetime, timezone

        uid, tid = _make_user(app, "renter2@ex.com")
        aid = _add_authorization(app, tid, uid)
        with app.app_context():
            auth = db.session.get(RenterAuthorization, aid)
            auth.revoked_at = datetime.now(timezone.utc)
            db.session.commit()
            assert auth.is_valid is False

    def test_date_expired_is_invalid(self, app):
        uid, tid = _make_user(app, "renter3@ex.com")
        aid = _add_authorization(
            app, tid, uid, expires_on=date.today() - timedelta(days=1)
        )
        with app.app_context():
            auth = db.session.get(RenterAuthorization, aid)
            assert auth.is_valid is False

    def test_future_expiry_is_valid(self, app):
        uid, tid = _make_user(app, "renter4@ex.com")
        aid = _add_authorization(
            app, tid, uid, expires_on=date.today() + timedelta(days=30)
        )
        with app.app_context():
            auth = db.session.get(RenterAuthorization, aid)
            assert auth.is_valid is True

    def test_medical_expired_is_invalid(self, app):
        uid, tid = _make_user(app, "renter5@ex.com")
        aid = _add_authorization(
            app, tid, uid, medical_valid_until=date.today() - timedelta(days=1)
        )
        with app.app_context():
            auth = db.session.get(RenterAuthorization, aid)
            assert auth.is_valid is False


class TestValidFor:
    def test_fleet_wide_covers_any_aircraft(self, app):
        uid, tid = _make_user(app, "renter6@ex.com")
        ac1 = _make_aircraft(app, tid, "OO-AA1")
        ac2 = _make_aircraft(app, tid, "OO-AA2")
        _add_authorization(app, tid, uid, aircraft_id=None)
        with app.app_context():
            assert RenterAuthorization.valid_for(uid, ac1) is not None
            assert RenterAuthorization.valid_for(uid, ac2) is not None

    def test_per_aircraft_does_not_cover_other_aircraft(self, app):
        uid, tid = _make_user(app, "renter7@ex.com")
        ac1 = _make_aircraft(app, tid, "OO-BB1")
        ac2 = _make_aircraft(app, tid, "OO-BB2")
        _add_authorization(app, tid, uid, aircraft_id=ac1)
        with app.app_context():
            assert RenterAuthorization.valid_for(uid, ac1) is not None
            assert RenterAuthorization.valid_for(uid, ac2) is None

    def test_no_authorization_returns_none(self, app):
        uid, tid = _make_user(app, "renter8@ex.com")
        ac1 = _make_aircraft(app, tid, "OO-CC1")
        with app.app_context():
            assert RenterAuthorization.valid_for(uid, ac1) is None

    def test_only_invalid_rows_returns_none(self, app):
        uid, tid = _make_user(app, "renter9@ex.com")
        ac1 = _make_aircraft(app, tid, "OO-DD1")
        _add_authorization(
            app, tid, uid, aircraft_id=ac1, expires_on=date.today() - timedelta(days=1)
        )
        with app.app_context():
            assert RenterAuthorization.valid_for(uid, ac1) is None


# ── Config: renters list ─────────────────────────────────────────────────────────


class TestRentersListRoute:
    def test_owner_can_view(self, app, client):
        uid, tid = _make_user(app, "owner1@ex.com", role=Role.OWNER)
        _login(app, client, uid)
        r = client.get("/config/renters/")
        assert r.status_code == 200

    def test_pilot_cannot_view(self, app, client):
        uid, tid = _make_user(app, "pilot1@ex.com", role=Role.PILOT)
        _login(app, client, uid)
        r = client.get("/config/renters/")
        assert r.status_code == 403

    def test_lists_only_this_tenant(self, app, client):
        uid, tid = _make_user(app, "owner2@ex.com", role=Role.OWNER)
        other_uid, other_tid = _make_user(app, "renter_other@ex.com", role=Role.PILOT)
        renter_uid, _ = _make_user(app, "renter_mine@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _add_authorization(app, tid, renter_uid)
        _add_authorization(app, other_tid, other_uid)
        _login(app, client, uid)
        r = client.get("/config/renters/")
        assert r.status_code == 200
        assert b"renter_mine" in r.data
        assert b"renter_other" not in r.data

    def test_status_badges(self, app, client):
        uid, tid = _make_user(app, "owner3@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_badges@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _add_authorization(app, tid, renter_uid)  # valid
        _login(app, client, uid)
        r = client.get("/config/renters/")
        assert b"Valid" in r.data

    def test_expired_status_badge(self, app, client):
        uid, tid = _make_user(app, "owner3b@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_expired@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _add_authorization(
            app, tid, renter_uid, expires_on=date.today() - timedelta(days=1)
        )
        _login(app, client, uid)
        r = client.get("/config/renters/")
        assert b"Expired" in r.data

    def test_expiring_soon_status_badge(self, app, client):
        uid, tid = _make_user(app, "owner3c@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_expiring@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _add_authorization(
            app, tid, renter_uid, expires_on=date.today() + timedelta(days=10)
        )
        _login(app, client, uid)
        r = client.get("/config/renters/")
        assert b"Expiring soon" in r.data

    def test_revoked_status_badge(self, app, client):
        from datetime import datetime, timezone

        uid, tid = _make_user(app, "owner3d@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_revoked_badge@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        aid = _add_authorization(app, tid, renter_uid)
        with app.app_context():
            auth = db.session.get(RenterAuthorization, aid)
            auth.revoked_at = datetime.now(timezone.utc)
            db.session.commit()
        _login(app, client, uid)
        r = client.get("/config/renters/")
        assert b"Revoked" in r.data


# ── Config: add / edit ────────────────────────────────────────────────────────────


class TestRenterAddEdit:
    def test_get_add_form(self, app, client):
        uid, tid = _make_user(app, "owner4@ex.com", role=Role.OWNER)
        _login(app, client, uid)
        r = client.get("/config/renters/add")
        assert r.status_code == 200

    def test_add_creates_fleet_wide_authorization(self, app, client):
        uid, tid = _make_user(app, "owner5@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_add1@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={
                "renter_user_id": str(renter_uid),
                "granted_on": "2026-06-01",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            auth = RenterAuthorization.query.filter_by(
                tenant_id=tid, renter_user_id=renter_uid
            ).first()
            assert auth is not None
            assert auth.aircraft_id is None
            assert auth.authorized_by_id == uid

    def test_add_with_specific_aircraft(self, app, client):
        uid, tid = _make_user(app, "owner6@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_add2@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid, "OO-EE1")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={
                "renter_user_id": str(renter_uid),
                "aircraft_id": str(ac_id),
                "granted_on": "2026-06-01",
                "expires_on": "2027-06-01",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            auth = RenterAuthorization.query.filter_by(
                tenant_id=tid, renter_user_id=renter_uid
            ).first()
            assert auth.aircraft_id == ac_id
            assert auth.expires_on == date(2027, 6, 1)

    def test_add_requires_valid_renter(self, app, client):
        uid, tid = _make_user(app, "owner7@ex.com", role=Role.OWNER)
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={"renter_user_id": "999999", "granted_on": "2026-06-01"},
        )
        assert r.status_code == 200
        with app.app_context():
            assert RenterAuthorization.query.filter_by(tenant_id=tid).count() == 0

    def test_add_rejects_non_numeric_renter_id(self, app, client):
        uid, tid = _make_user(app, "owner7b@ex.com", role=Role.OWNER)
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={"renter_user_id": "not-a-number", "granted_on": "2026-06-01"},
        )
        assert r.status_code == 200
        with app.app_context():
            assert RenterAuthorization.query.filter_by(tenant_id=tid).count() == 0

    def test_add_rejects_malformed_optional_date(self, app, client):
        uid, tid = _make_user(app, "owner7c@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_baddate@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={
                "renter_user_id": str(renter_uid),
                "granted_on": "2026-06-01",
                "medical_valid_until": "not-a-date",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            auth = RenterAuthorization.query.filter_by(
                tenant_id=tid, renter_user_id=renter_uid
            ).one()
            # A malformed optional date is treated as absent, not an error.
            assert auth.medical_valid_until is None

    def test_agreement_field_present_but_empty_is_ignored(self, app, client):
        from io import BytesIO

        uid, tid = _make_user(app, "owner7d@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_emptyfile@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={
                "renter_user_id": str(renter_uid),
                "granted_on": "2026-06-01",
                "agreement": (BytesIO(b""), ""),
            },
            content_type="multipart/form-data",
        )
        assert r.status_code == 302
        with app.app_context():
            auth = RenterAuthorization.query.filter_by(
                tenant_id=tid, renter_user_id=renter_uid
            ).one()
            assert Document.query.filter_by(renter_authorization_id=auth.id).count() == 0

    def test_add_rejects_renter_from_other_tenant(self, app, client):
        uid, tid = _make_user(app, "owner8@ex.com", role=Role.OWNER)
        other_uid, _ = _make_user(app, "renter_foreign@ex.com", role=Role.PILOT)
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={"renter_user_id": str(other_uid), "granted_on": "2026-06-01"},
        )
        assert r.status_code == 200
        with app.app_context():
            assert RenterAuthorization.query.filter_by(tenant_id=tid).count() == 0

    def test_add_rejects_invalid_aircraft_selection(self, app, client):
        uid, tid = _make_user(app, "owner9@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_add3@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={
                "renter_user_id": str(renter_uid),
                "aircraft_id": "not-a-number",
                "granted_on": "2026-06-01",
            },
        )
        assert r.status_code == 200

    def test_add_rejects_aircraft_from_other_tenant(self, app, client):
        uid, tid = _make_user(app, "owner10@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_add4@ex.com", role=Role.PILOT)
        _other_uid, other_tid = _make_user(app, "otherowner@ex.com", role=Role.OWNER)
        other_ac_id = _make_aircraft(app, other_tid, "OO-FOREIGN")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={
                "renter_user_id": str(renter_uid),
                "aircraft_id": str(other_ac_id),
                "granted_on": "2026-06-01",
            },
        )
        assert r.status_code == 200
        with app.app_context():
            assert RenterAuthorization.query.filter_by(tenant_id=tid).count() == 0

    def test_add_requires_granted_on(self, app, client):
        uid, tid = _make_user(app, "owner11@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_add5@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _login(app, client, uid)
        r = client.post("/config/renters/add", data={"renter_user_id": str(renter_uid)})
        assert r.status_code == 200

    def test_expiry_before_granted_rejected(self, app, client):
        uid, tid = _make_user(app, "owner12@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_add6@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={
                "renter_user_id": str(renter_uid),
                "granted_on": "2026-06-01",
                "expires_on": "2026-01-01",
            },
        )
        assert r.status_code == 200
        with app.app_context():
            assert RenterAuthorization.query.filter_by(tenant_id=tid).count() == 0

    def test_agreement_upload_rejects_bad_extension(self, app, client):
        from io import BytesIO

        uid, tid = _make_user(app, "owner13@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_add7@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={
                "renter_user_id": str(renter_uid),
                "granted_on": "2026-06-01",
                "agreement": (BytesIO(b"bad"), "agreement.exe"),
            },
            content_type="multipart/form-data",
        )
        assert r.status_code == 200
        with app.app_context():
            assert RenterAuthorization.query.filter_by(tenant_id=tid).count() == 0

    def test_agreement_upload_creates_document(self, app, client, tmp_path):
        from io import BytesIO

        uid, tid = _make_user(app, "owner14@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_add8@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        app.config["UPLOAD_FOLDER"] = str(tmp_path)
        _login(app, client, uid)
        r = client.post(
            "/config/renters/add",
            data={
                "renter_user_id": str(renter_uid),
                "granted_on": "2026-06-01",
                "agreement": (BytesIO(b"%PDF-1.4 fake"), "agreement.pdf"),
            },
            content_type="multipart/form-data",
        )
        assert r.status_code == 302
        with app.app_context():
            auth = RenterAuthorization.query.filter_by(
                tenant_id=tid, renter_user_id=renter_uid
            ).one()
            doc = Document.query.filter_by(renter_authorization_id=auth.id).first()
            assert doc is not None
            assert doc.aircraft_id is None
            assert doc.original_filename == "agreement.pdf"

    def test_edit_updates_existing(self, app, client):
        uid, tid = _make_user(app, "owner15@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_edit1@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        aid = _add_authorization(app, tid, renter_uid, notes="old note")
        _login(app, client, uid)
        r = client.get(f"/config/renters/{aid}/edit")
        assert r.status_code == 200
        r = client.post(
            f"/config/renters/{aid}/edit",
            data={
                "renter_user_id": str(renter_uid),
                "granted_on": "2026-06-01",
                "notes": "updated note",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            auth = db.session.get(RenterAuthorization, aid)
            assert auth.notes == "updated note"

    def test_edit_cross_tenant_404(self, app, client):
        uid, tid = _make_user(app, "owner16@ex.com", role=Role.OWNER)
        other_uid, other_tid = _make_user(app, "renter_edit2@ex.com", role=Role.PILOT)
        aid = _add_authorization(app, other_tid, other_uid)
        _login(app, client, uid)
        r = client.get(f"/config/renters/{aid}/edit")
        assert r.status_code == 404


class TestRenterRevoke:
    def test_revoke_sets_revoked_at(self, app, client):
        uid, tid = _make_user(app, "owner17@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_rev1@ex.com", role=Role.PILOT)
        aid = _add_authorization(app, tid, renter_uid)
        _login(app, client, uid)
        r = client.post(f"/config/renters/{aid}/revoke")
        assert r.status_code == 302
        with app.app_context():
            auth = db.session.get(RenterAuthorization, aid)
            assert auth.revoked_at is not None
            assert auth.is_valid is False

    def test_revoke_cross_tenant_404(self, app, client):
        uid, tid = _make_user(app, "owner18@ex.com", role=Role.OWNER)
        other_uid, other_tid = _make_user(app, "renter_rev2@ex.com", role=Role.PILOT)
        aid = _add_authorization(app, other_tid, other_uid)
        _login(app, client, uid)
        r = client.post(f"/config/renters/{aid}/revoke")
        assert r.status_code == 404


# ── Reservation guard ──────────────────────────────────────────────────────────


class TestReservationAuthorizationGuard:
    def _set_policy(self, app, tenant_id, policy):
        with app.app_context():
            profile = TenantProfile.query.filter_by(tenant_id=tenant_id).first()
            if profile is None:
                profile = TenantProfile(tenant_id=tenant_id, setup_complete=True)
                db.session.add(profile)
            profile.rental_authorization_policy = policy
            db.session.commit()

    def test_policy_off_allows_unauthorized_renter(self, app, client):
        uid, tid = _make_user(app, "renter_off@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid, "OO-OFF1")
        _grant_access(app, uid, ac_id)
        self._set_policy(app, tid, "off")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{ac_id}/reservations/new",
            data={"start_dt": "2026-06-20T09:00", "end_dt": "2026-06-20T11:00"},
        )
        assert r.status_code == 302
        with app.app_context():
            assert Reservation.query.filter_by(aircraft_id=ac_id).count() == 1

    def test_policy_warn_allows_with_flash(self, app, client):
        uid, tid = _make_user(app, "renter_warn@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid, "OO-WRN1")
        _grant_access(app, uid, ac_id)
        self._set_policy(app, tid, "warn")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{ac_id}/reservations/new",
            data={"start_dt": "2026-06-20T09:00", "end_dt": "2026-06-20T11:00"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"valid rental authorization" in r.data
        with app.app_context():
            assert Reservation.query.filter_by(aircraft_id=ac_id).count() == 1

    def test_policy_block_refuses(self, app, client):
        uid, tid = _make_user(app, "renter_block@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid, "OO-BLK1")
        _grant_access(app, uid, ac_id)
        self._set_policy(app, tid, "block")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{ac_id}/reservations/new",
            data={"start_dt": "2026-06-20T09:00", "end_dt": "2026-06-20T11:00"},
        )
        assert r.status_code == 200  # re-rendered form, not redirected
        with app.app_context():
            assert Reservation.query.filter_by(aircraft_id=ac_id).count() == 0

    def test_block_get_form_shows_explanation(self, app, client):
        uid, tid = _make_user(app, "renter_block2@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid, "OO-BLK2")
        _grant_access(app, uid, ac_id)
        self._set_policy(app, tid, "block")
        _login(app, client, uid)
        r = client.get(f"/aircraft/{ac_id}/reservations/new")
        assert r.status_code == 200
        assert b"valid rental authorization" in r.data

    def test_valid_authorization_passes_under_block(self, app, client):
        uid, tid = _make_user(app, "renter_valid@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid, "OO-VLD1")
        _grant_access(app, uid, ac_id)
        self._set_policy(app, tid, "block")
        _add_authorization(app, tid, uid)
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{ac_id}/reservations/new",
            data={"start_dt": "2026-06-20T09:00", "end_dt": "2026-06-20T11:00"},
        )
        assert r.status_code == 302
        with app.app_context():
            assert Reservation.query.filter_by(aircraft_id=ac_id).count() == 1

    def test_owner_exempt_under_block(self, app, client):
        uid, tid = _make_user(app, "owner_exempt@ex.com", role=Role.OWNER)
        ac_id = _make_aircraft(app, tid, "OO-EXM1")
        self._set_policy(app, tid, "block")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{ac_id}/reservations/new",
            data={"start_dt": "2026-06-20T09:00", "end_dt": "2026-06-20T11:00"},
        )
        assert r.status_code == 302
        with app.app_context():
            assert Reservation.query.filter_by(aircraft_id=ac_id).count() == 1

    def test_confirm_blocked_when_pilot_unauthorized(self, app, client):
        uid, tid = _make_user(app, "owner_confirm@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_confirm@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid, "OO-CNF1")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _grant_access(app, renter_uid, ac_id)
        # Created while policy was "warn" so it exists as pending...
        self._set_policy(app, tid, "warn")
        _login(app, client, renter_uid)
        client.post(
            f"/aircraft/{ac_id}/reservations/new",
            data={"start_dt": "2026-06-20T09:00", "end_dt": "2026-06-20T11:00"},
        )
        with app.app_context():
            res = Reservation.query.filter_by(aircraft_id=ac_id).one()
            res_id = res.id
        # ...then the owner tightens the policy to "block" before confirming.
        self._set_policy(app, tid, "block")
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{ac_id}/reservations/{res_id}/confirm", follow_redirects=True
        )
        assert r.status_code == 200
        assert b"does not have a valid rental" in r.data
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.status == ReservationStatus.PENDING

    def test_confirm_succeeds_when_authorized(self, app, client):
        uid, tid = _make_user(app, "owner_confirm2@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_confirm2@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid, "OO-CNF2")
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _grant_access(app, renter_uid, ac_id)
        _add_authorization(app, tid, renter_uid)
        self._set_policy(app, tid, "block")
        _login(app, client, renter_uid)
        client.post(
            f"/aircraft/{ac_id}/reservations/new",
            data={"start_dt": "2026-06-20T09:00", "end_dt": "2026-06-20T11:00"},
        )
        with app.app_context():
            res_id = Reservation.query.filter_by(aircraft_id=ac_id).one().id
        _login(app, client, uid)
        r = client.post(f"/aircraft/{ac_id}/reservations/{res_id}/confirm")
        assert r.status_code == 302
        with app.app_context():
            res = db.session.get(Reservation, res_id)
            assert res.status == ReservationStatus.CONFIRMED

    def test_no_tenant_profile_defaults_to_warn(self, app, client):
        """No TenantProfile row at all (never visited usage-profile settings)
        still applies the 'warn' default, not a crash."""
        uid, tid = _make_user(app, "renter_noprofile@ex.com", role=Role.PILOT)
        ac_id = _make_aircraft(app, tid, "OO-NOP1")
        _grant_access(app, uid, ac_id)
        _login(app, client, uid)
        r = client.post(
            f"/aircraft/{ac_id}/reservations/new",
            data={"start_dt": "2026-06-20T09:00", "end_dt": "2026-06-20T11:00"},
            follow_redirects=True,
        )
        assert r.status_code == 200
        assert b"valid rental authorization" in r.data


# ── Usage-profile policy setting ──────────────────────────────────────────────


class TestUpdateProfilePolicy:
    def test_saves_valid_policy(self, app, client):
        uid, tid = _make_user(app, "owner_policy1@ex.com", role=Role.OWNER)
        _login(app, client, uid)
        r = client.post(
            "/config/profile",
            data={
                "operating_model": "sole_operator",
                "rental_authorization_policy": "block",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            profile = TenantProfile.query.filter_by(tenant_id=tid).first()
            assert profile.rental_authorization_policy == "block"

    def test_invalid_policy_ignored(self, app, client):
        uid, tid = _make_user(app, "owner_policy2@ex.com", role=Role.OWNER)
        _login(app, client, uid)
        r = client.post(
            "/config/profile",
            data={
                "operating_model": "sole_operator",
                "rental_authorization_policy": "not-a-policy",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            profile = TenantProfile.query.filter_by(tenant_id=tid).first()
            assert profile.rental_authorization_policy == "warn"


# ── Notification: expiry digest ────────────────────────────────────────────────


class TestRenterAuthorizationNotification:
    def test_fires_within_threshold(self, app):
        uid, tid = _make_user(app, "owner_notif1@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_notif1@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _add_authorization(
            app, tid, renter_uid, expires_on=date.today() + timedelta(days=10)
        )
        with app.app_context():
            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_renter_authorizations  # pyright: ignore[reportMissingImports]

                _check_renter_authorizations(app)
                types_dispatched = [c.args[0] for c in mock_dispatch.call_args_list]
                assert NotificationType.RENTER_AUTHORIZATION_EXPIRY in types_dispatched

    def test_medical_expiry_also_triggers(self, app):
        uid, tid = _make_user(app, "owner_notif2@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_notif2@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _add_authorization(
            app,
            tid,
            renter_uid,
            medical_valid_until=date.today() + timedelta(days=5),
        )
        with app.app_context():
            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_renter_authorizations  # pyright: ignore[reportMissingImports]

                _check_renter_authorizations(app)
                assert mock_dispatch.called
                _args, kwargs_or_ctx = (
                    mock_dispatch.call_args_list[0].args,
                    mock_dispatch.call_args_list[0].args[2],
                )
                details = kwargs_or_ctx["details"]
                assert any("medical" in v for _label, v in details)

    def test_no_content_no_dispatch(self, app):
        uid, tid = _make_user(app, "owner_notif3@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_notif3@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        _add_authorization(
            app, tid, renter_uid, expires_on=date.today() + timedelta(days=90)
        )
        with app.app_context():
            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_renter_authorizations  # pyright: ignore[reportMissingImports]

                _check_renter_authorizations(app)
                assert not mock_dispatch.called

    def test_revoked_authorization_excluded(self, app):
        uid, tid = _make_user(app, "owner_notif4@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_notif4@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.commit()
        aid = _add_authorization(
            app, tid, renter_uid, expires_on=date.today() + timedelta(days=5)
        )
        with app.app_context():
            from datetime import datetime, timezone

            auth = db.session.get(RenterAuthorization, aid)
            auth.revoked_at = datetime.now(timezone.utc)
            db.session.commit()

            with patch("services.notification_service.dispatch") as mock_dispatch:
                from services.notification_service import _check_renter_authorizations  # pyright: ignore[reportMissingImports]

                _check_renter_authorizations(app)
                assert not mock_dispatch.called

    def test_respects_disabled_preference(self, app):
        """When the owner disables this notification type, no email is sent
        (exercised through the real dispatch(), not the mocked shortcut)."""
        from models import NotificationPreference  # pyright: ignore[reportMissingImports]

        uid, tid = _make_user(app, "owner_notif5@ex.com", role=Role.OWNER)
        renter_uid, _ = _make_user(app, "renter_notif5@ex.com", role=Role.PILOT)
        with app.app_context():
            db.session.add(
                TenantUser(user_id=renter_uid, tenant_id=tid, role=Role.PILOT)
            )
            db.session.add(
                NotificationPreference(
                    user_id=uid,
                    tenant_id=tid,
                    notification_type=NotificationType.RENTER_AUTHORIZATION_EXPIRY,
                    enabled=False,
                )
            )
            db.session.commit()
        _add_authorization(
            app, tid, renter_uid, expires_on=date.today() + timedelta(days=5)
        )
        with app.app_context():
            with patch("services.email_service.send_email") as mock_send:
                from services.notification_service import _check_renter_authorizations  # pyright: ignore[reportMissingImports]

                _check_renter_authorizations(app)
                assert not mock_send.called

    def test_run_daily_checks_includes_renter_authorizations(self, app):
        with app.app_context():
            with (
                patch(
                    "services.notification_service._check_renter_authorizations"
                ) as mock_check,
                patch("services.notification_service._check_maintenance"),
                patch("services.notification_service._check_insurance"),
                patch("services.notification_service._check_medical_and_sep"),
                patch("services.notification_service._check_documents"),
                patch("services.notification_service._check_airworthiness_reviews"),
                patch(
                    "services.recurring_expense_service.materialize_recurring_expenses"
                ),
            ):
                from services.notification_service import run_daily_checks  # pyright: ignore[reportMissingImports]

                run_daily_checks(app)
                assert mock_check.called
