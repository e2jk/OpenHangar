"""
Tests for Phase 2: Aircraft management routes (CRUD + auth guard).
"""

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from unittest.mock import patch

from utils import _load_aircraft_type_engine_data, get_aircraft_type_engine_info  # pyright: ignore[reportMissingImports]
from models import (
    Aircraft,
    Component,
    ComponentType,
    Role,
    Tenant,
    TenantProfile,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]


def _login_orphan_user(app, client):
    """Create a User with no TenantUser and inject into session."""
    with app.app_context():
        user = User(
            email="orphan@example.com",
            password_hash=_pw_hash.hash("x"),
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        uid = user.id
    with client.session_transaction() as sess:
        sess["user_id"] = uid


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user_and_tenant(app, email="pilot@example.com", password="testpassword123"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()

        user = User(
            email=email,
            password_hash=_pw_hash.hash(password),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()

        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id, registration="OO-PNH", make="Cessna", model="172S"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make=make, model=model
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_component(
    app, aircraft_id, type_=ComponentType.ENGINE, make="Lycoming", model="IO-360"
):
    with app.app_context():
        comp = Component(aircraft_id=aircraft_id, type=type_, make=make, model=model)
        db.session.add(comp)
        db.session.commit()
        return comp.id


# ── Auth guard ────────────────────────────────────────────────────────────────


class TestAuthGuard:
    def test_aircraft_list_redirects_when_not_logged_in(self, client):
        response = client.get("/aircraft/")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_aircraft_detail_redirects_when_not_logged_in(self, client):
        response = client.get("/aircraft/1")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_new_aircraft_redirects_when_not_logged_in(self, client):
        response = client.get("/aircraft/new")
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_aircraft_list_accessible_when_logged_in(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        assert client.get("/aircraft/").status_code == 200


# ── Aircraft list ─────────────────────────────────────────────────────────────


class TestAircraftList:
    def test_empty_list_shows_empty_state(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        assert b"No aircraft" in client.get("/aircraft/").data

    def test_aircraft_appears_in_list(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-PNH")
        _login(app, client)
        assert b"OO-PNH" in client.get("/aircraft/").data

    def test_other_tenant_aircraft_not_shown(self, app, client):
        _create_user_and_tenant(app)
        _create_user_and_tenant(app, email="other@example.com")
        with app.app_context():
            other_tenant = Tenant.query.filter_by(name="Test Hangar").all()[-1]
            _add_aircraft(app, other_tenant.id, registration="OO-OTHER")
        _login(app, client)
        data = client.get("/aircraft/").data
        assert b"OO-OTHER" not in data

    def test_single_aircraft_mode_redirects_to_detail(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        with app.app_context():
            profile = TenantProfile(tenant_id=tid, planned_aircraft_count=1)
            db.session.add(profile)
            db.session.commit()
        _login(app, client)
        r = client.get("/aircraft/")
        assert r.status_code == 302
        assert "/aircraft/OO-PNH" in r.headers["Location"]

    def test_single_aircraft_mode_list_param_bypasses_redirect(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid)
        with app.app_context():
            profile = TenantProfile(tenant_id=tid, planned_aircraft_count=1)
            db.session.add(profile)
            db.session.commit()
        _login(app, client)
        assert client.get("/aircraft/?list=1").status_code == 200


# ── Add aircraft ──────────────────────────────────────────────────────────────


class TestAddAircraft:
    def test_get_shows_form(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        assert client.get("/aircraft/new").status_code == 200

    def test_valid_post_creates_aircraft(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={
                "registration": "OO-PNH",
                "make": "Cessna",
                "model": "172S",
                "year": "2004",
            },
        )
        assert response.status_code == 302
        with app.app_context():
            ac = Aircraft.query.filter_by(registration="OO-PNH").first()
            assert ac is not None
            assert ac.make == "Cessna"
            assert ac.year == 2004

    def test_registration_normalised_to_uppercase(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        client.post(
            "/aircraft/new",
            data={
                "registration": "oo-pnh",
                "make": "Cessna",
                "model": "172S",
            },
        )
        with app.app_context():
            assert Aircraft.query.filter_by(registration="OO-PNH").first() is not None

    def test_missing_registration_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        response = client.post(
            "/aircraft/new", data={"make": "Cessna", "model": "172S"}
        )
        assert response.status_code == 200
        assert b"Registration" in response.data

    def test_missing_make_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        response = client.post(
            "/aircraft/new", data={"registration": "OO-PNH", "model": "172S"}
        )
        assert response.status_code == 200
        assert b"Manufacturer" in response.data

    def test_invalid_year_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={
                "registration": "OO-PNH",
                "make": "Cessna",
                "model": "172S",
                "year": "abcd",
            },
        )
        assert response.status_code == 200
        assert b"Year" in response.data


# ── Aircraft detail ───────────────────────────────────────────────────────────


class TestAircraftDetail:
    def test_shows_registration(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        assert b"OO-PNH" in client.get(f"/aircraft/{ac_id}").data

    def test_shows_components(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _add_component(app, ac_id, make="Lycoming", model="IO-360")
        _login(app, client)
        assert b"IO-360" in client.get(f"/aircraft/{ac_id}").data

    def test_other_tenant_aircraft_returns_404(self, app, client):
        _create_user_and_tenant(app)
        _create_user_and_tenant(app, email="other@example.com")
        with app.app_context():
            other_tid = (
                TenantUser.query.filter_by(
                    user_id=User.query.filter_by(email="other@example.com").first().id
                )
                .first()
                .tenant_id
            )
        other_ac_id = _add_aircraft(app, other_tid, registration="OO-OTHER")
        _login(app, client)
        assert client.get(f"/aircraft/{other_ac_id}").status_code == 404


# ── Registration-based URLs (AircraftRefConverter) ─────────────────────────────


class TestAircraftRefConverter:
    def test_numeric_id_still_resolves(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, registration="OO-PNH")
        _login(app, client)
        assert client.get(f"/aircraft/{ac_id}").status_code == 200

    def test_registration_resolves_same_aircraft(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-PNH")
        _login(app, client)
        resp = client.get("/aircraft/OO-PNH")
        assert resp.status_code == 200
        assert b"OO-PNH" in resp.data

    def test_registration_is_case_insensitive(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-PNH")
        _login(app, client)
        assert client.get("/aircraft/oo-pnh").status_code == 200

    def test_unknown_registration_404s(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        assert client.get("/aircraft/ZZ-NOPE").status_code == 404

    def test_other_tenant_registration_404s(self, app, client):
        """Same tenant-scoping guarantee as the numeric-id path — the
        converter itself does an unscoped lookup, so this proves the view's
        own tenant check is what's actually protecting the data."""
        _create_user_and_tenant(app)
        _create_user_and_tenant(app, email="other2@example.com")
        with app.app_context():
            other_tid = (
                TenantUser.query.filter_by(
                    user_id=User.query.filter_by(email="other2@example.com").first().id
                )
                .first()
                .tenant_id
            )
        _add_aircraft(app, other_tid, registration="OO-CROSS")
        _login(app, client)
        assert client.get("/aircraft/OO-CROSS").status_code == 404

    def test_subpage_resolves_via_registration(self, app, client):
        """The converter applies to the whole /aircraft/<ref>/... tree, not
        just the detail page — flights.list_flights is one representative
        subpage."""
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-PNH")
        _login(app, client)
        assert client.get("/aircraft/OO-PNH/flights").status_code == 200

    def test_registration_with_slash_resolves_via_sanitized_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        with app.app_context():
            ac = Aircraft(tenant_id=tid, registration="OO/GRN", make="X", model="Y")
            db.session.add(ac)
            db.session.commit()
        _login(app, client)
        assert client.get("/aircraft/OO-GRN").status_code == 200

    def test_generated_links_use_registration(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-PNH")
        _login(app, client)
        resp = client.get("/aircraft/")
        assert b"/aircraft/OO-PNH" in resp.data

    def test_to_url_passes_through_explicit_registration_string(self, app):
        """A caller may explicitly pass a registration string (rather than
        the more common int id) to url_for — must render as-is, not be
        treated as a numeric id."""
        from utils import AircraftRefConverter  # pyright: ignore[reportMissingImports]

        with app.app_context(), app.test_request_context():
            converter = AircraftRefConverter(app.url_map)
            assert converter.to_url("OO-PNH") == "OO-PNH"

    def test_to_url_falls_back_to_numeric_for_unknown_id(self, app):
        """Defensive branch: to_url() is asked to render a link for an id
        with no matching row (shouldn't happen via a real FK, but must not
        crash link generation if it ever does)."""
        from utils import AircraftRefConverter  # pyright: ignore[reportMissingImports]

        with app.app_context(), app.test_request_context():
            converter = AircraftRefConverter(app.url_map)
            assert converter.to_url(999999) == "999999"


# ── Edit aircraft ─────────────────────────────────────────────────────────────


class TestEditAircraft:
    def test_get_shows_prefilled_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        data = client.get(f"/aircraft/{ac_id}/edit").data
        assert b"OO-PNH" in data

    def test_valid_post_updates_aircraft(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/edit",
            data={
                "registration": "OO-PNH",
                "make": "Cessna",
                "model": "172SP",
                "year": "2006",
            },
        )
        with app.app_context():
            assert db.session.get(Aircraft, ac_id).model == "172SP"

    def test_edit_other_tenant_aircraft_returns_404(self, app, client):
        _create_user_and_tenant(app)
        _create_user_and_tenant(app, email="other@example.com")
        with app.app_context():
            other_tid = (
                TenantUser.query.filter_by(
                    user_id=User.query.filter_by(email="other@example.com").first().id
                )
                .first()
                .tenant_id
            )
        other_ac_id = _add_aircraft(app, other_tid, registration="OO-OTHER")
        _login(app, client)
        assert client.get(f"/aircraft/{other_ac_id}/edit").status_code == 404


# ── Delete aircraft ───────────────────────────────────────────────────────────


class TestDeleteAircraft:
    def test_delete_removes_aircraft(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        response = client.post(f"/aircraft/{ac_id}/delete")
        assert response.status_code == 302
        with app.app_context():
            assert db.session.get(Aircraft, ac_id) is None

    def test_delete_cascades_to_components(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        comp_id = _add_component(app, ac_id)
        _login(app, client)
        client.post(f"/aircraft/{ac_id}/delete")
        with app.app_context():
            assert db.session.get(Component, comp_id) is None

    def test_delete_other_tenant_aircraft_returns_404(self, app, client):
        _create_user_and_tenant(app)
        _create_user_and_tenant(app, email="other@example.com")
        with app.app_context():
            other_tid = (
                TenantUser.query.filter_by(
                    user_id=User.query.filter_by(email="other@example.com").first().id
                )
                .first()
                .tenant_id
            )
        other_ac_id = _add_aircraft(app, other_tid, registration="OO-OTHER")
        _login(app, client)
        assert client.post(f"/aircraft/{other_ac_id}/delete").status_code == 404


# ── Add component ─────────────────────────────────────────────────────────────


class TestAddComponent:
    def test_get_shows_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        assert client.get(f"/aircraft/{ac_id}/components/new").status_code == 200

    def test_valid_post_creates_component(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        response = client.post(
            f"/aircraft/{ac_id}/components/new",
            data={
                "type": ComponentType.ENGINE,
                "make": "Lycoming",
                "model": "IO-360-L2A",
                "serial_number": "L-99999",
                "time_at_install": "312.5",
                "position": "left",
                "installed_at": "2020-01-15",
            },
        )
        assert response.status_code == 302
        with app.app_context():
            comp = Component.query.filter_by(aircraft_id=ac_id).first()
            assert comp is not None
            assert comp.model == "IO-360-L2A"
            assert float(comp.time_at_install) == 312.5
            assert comp.position == "left"

    def test_missing_type_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        response = client.post(
            f"/aircraft/{ac_id}/components/new",
            data={
                "make": "Lycoming",
                "model": "IO-360",
            },
        )
        assert response.status_code == 200
        assert b"required" in response.data.lower()

    def test_negative_time_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        response = client.post(
            f"/aircraft/{ac_id}/components/new",
            data={
                "type": ComponentType.ENGINE,
                "make": "Lycoming",
                "model": "IO-360",
                "time_at_install": "-5",
            },
        )
        assert response.status_code == 200
        assert b"positive" in response.data.lower()


# ── Edit component ────────────────────────────────────────────────────────────


class TestEditComponent:
    def test_get_shows_prefilled_form(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        comp_id = _add_component(app, ac_id, model="IO-360")
        _login(app, client)
        data = client.get(f"/aircraft/{ac_id}/components/{comp_id}/edit").data
        assert b"IO-360" in data

    def test_valid_post_updates_component(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        comp_id = _add_component(app, ac_id, model="IO-360")
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/components/{comp_id}/edit",
            data={
                "type": ComponentType.ENGINE,
                "make": "Lycoming",
                "model": "IO-360-L2A",
            },
        )
        with app.app_context():
            assert db.session.get(Component, comp_id).model == "IO-360-L2A"

    def test_component_not_on_aircraft_returns_404(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id1 = _add_aircraft(app, tid, registration="OO-PNH")
        ac_id2 = _add_aircraft(app, tid, registration="OO-ABC")
        comp_id = _add_component(app, ac_id2)
        _login(app, client)
        assert (
            client.get(f"/aircraft/{ac_id1}/components/{comp_id}/edit").status_code
            == 404
        )


# ── Delete component ──────────────────────────────────────────────────────────


class TestDeleteComponent:
    def test_delete_removes_component(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        comp_id = _add_component(app, ac_id)
        _login(app, client)
        response = client.post(f"/aircraft/{ac_id}/components/{comp_id}/delete")
        assert response.status_code == 302
        with app.app_context():
            assert db.session.get(Component, comp_id) is None

    def test_delete_redirects_to_aircraft_detail(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        comp_id = _add_component(app, ac_id)
        _login(app, client)
        response = client.post(f"/aircraft/{ac_id}/components/{comp_id}/delete")
        assert "/aircraft/OO-PNH" in response.headers["Location"]


# ── Coverage gap: no TenantUser → 403 ────────────────────────────────────────


class TestNoTenantUser:
    def test_aircraft_list_aborts_403_when_no_tenant_user(self, app, client):
        _login_orphan_user(app, client)
        response = client.get("/aircraft/")
        assert response.status_code == 403


# ── Coverage gap: _save_aircraft validation ───────────────────────────────────


class TestSaveAircraftValidation:
    def test_missing_model_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={
                "registration": "OO-PNH",
                "make": "Cessna",
                "model": "",
            },
        )
        assert response.status_code == 200
        assert b"Model" in response.data

    def test_year_out_of_range_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={
                "registration": "OO-PNH",
                "make": "Cessna",
                "model": "172S",
                "year": "1800",
            },
        )
        assert response.status_code == 200
        assert b"Year" in response.data

    def test_negative_fuel_flow_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={
                "registration": "OO-PNH",
                "make": "Cessna",
                "model": "172S",
                "fuel_flow": "-5",
            },
        )
        assert response.status_code == 200
        assert b"non-negative" in response.data

    def test_invalid_insurance_expiry_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={
                "registration": "OO-PNH",
                "make": "Cessna",
                "model": "172S",
                "insurance_expiry": "not-a-date",
            },
        )
        assert response.status_code == 200
        assert b"Insurance expiry" in response.data

    def test_valid_insurance_expiry_saves(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={
                "registration": "OO-PNH",
                "make": "Cessna",
                "model": "172S",
                "insurance_expiry": "2027-06-30",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

    def test_negative_reserve_hourly_rate_shows_error(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={
                "registration": "OO-PNH",
                "make": "Cessna",
                "model": "172S",
                "reserve_hourly_rate": "-15",
            },
        )
        assert response.status_code == 200
        assert b"non-negative" in response.data

    def test_valid_reserve_hourly_rate_saves(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        client.post(
            "/aircraft/new",
            data={
                "registration": "OO-PNH",
                "make": "Cessna",
                "model": "172S",
                "reserve_hourly_rate": "15.50",
            },
            follow_redirects=False,
        )
        with app.app_context():
            ac = Aircraft.query.filter_by(registration="OO-PNH").first()
            assert float(ac.reserve_hourly_rate) == 15.50


# ── Registration uniqueness (rejects a collision, incl. sanitized form) ────────


class TestRegistrationUniqueness:
    def test_create_exact_duplicate_in_same_tenant_rejected(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-PNH")
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={"registration": "OO-PNH", "make": "Piper", "model": "PA-28"},
        )
        assert response.status_code == 200
        assert b"already used by another aircraft" in response.data
        with app.app_context():
            assert Aircraft.query.filter_by(registration="OO-PNH").count() == 1

    def test_create_sanitized_collision_rejected(self, app, client):
        """A registration that only differs by '/' or a space from an
        existing one still collides, since AircraftRefConverter would
        otherwise route both to the same URL."""
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-GRN")
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={"registration": "OO/GRN", "make": "Piper", "model": "PA-28"},
        )
        assert response.status_code == 200
        assert b"already used by another aircraft" in response.data

    def test_create_case_insensitive_collision_rejected(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-PNH")
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={"registration": "oo-pnh", "make": "Piper", "model": "PA-28"},
        )
        assert response.status_code == 200
        assert b"already used by another aircraft" in response.data

    def test_create_same_registration_different_tenant_allowed(self, app, client):
        _create_user_and_tenant(app)
        _, other_tid = _create_user_and_tenant(app, email="other3@example.com")
        _add_aircraft(app, other_tid, registration="OO-PNH")
        _login(app, client)
        response = client.post(
            "/aircraft/new",
            data={"registration": "OO-PNH", "make": "Cessna", "model": "172S"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        with app.app_context():
            assert Aircraft.query.filter_by(registration="OO-PNH").count() == 2

    def test_edit_keeping_own_registration_not_rejected(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, registration="OO-PNH")
        _login(app, client)
        response = client.post(
            f"/aircraft/{ac_id}/edit",
            data={"registration": "OO-PNH", "make": "Cessna", "model": "172SP"},
            follow_redirects=False,
        )
        assert response.status_code == 302

    def test_edit_to_another_aircrafts_registration_rejected(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        _add_aircraft(app, tid, registration="OO-PNH")
        ac_id2 = _add_aircraft(app, tid, registration="OO-ABC")
        _login(app, client)
        response = client.post(
            f"/aircraft/{ac_id2}/edit",
            data={"registration": "OO-PNH", "make": "Piper", "model": "PA-28"},
        )
        assert response.status_code == 200
        assert b"already used by another aircraft" in response.data
        with app.app_context():
            assert db.session.get(Aircraft, ac_id2).registration == "OO-ABC"


# ── Coverage gap: _save_component validation ──────────────────────────────────


class TestSaveComponentValidation:
    def test_missing_make_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        response = client.post(
            f"/aircraft/{ac_id}/components/new",
            data={
                "type": ComponentType.ENGINE,
                "make": "",
                "model": "IO-360",
            },
        )
        assert response.status_code == 200
        assert b"Manufacturer" in response.data

    def test_missing_model_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        response = client.post(
            f"/aircraft/{ac_id}/components/new",
            data={
                "type": ComponentType.ENGINE,
                "make": "Lycoming",
                "model": "",
            },
        )
        assert response.status_code == 200
        assert b"Model" in response.data

    def test_invalid_installed_at_date_shows_error(self, app, client):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        _login(app, client)
        response = client.post(
            f"/aircraft/{ac_id}/components/new",
            data={
                "type": ComponentType.ENGINE,
                "make": "Lycoming",
                "model": "IO-360",
                "installed_at": "not-a-date",
            },
        )
        assert response.status_code == 200
        assert b"valid date" in response.data


# ── ICAO type engine info (utils) ─────────────────────────────────────────────


class TestGetAircraftTypeEngineInfo:
    def test_known_piston_type_returns_tuple(self):
        result = get_aircraft_type_engine_info("C172")
        assert result is not None
        ec, et = result
        assert isinstance(ec, int) and ec >= 1
        assert et == "Piston"

    def test_unknown_code_returns_none(self):
        assert get_aircraft_type_engine_info("ZZZNOTREAL") is None

    def test_case_insensitive(self):
        assert get_aircraft_type_engine_info("c172") == get_aircraft_type_engine_info(
            "C172"
        )

    def test_missing_csv_returns_empty_and_logs_warning(self):
        _load_aircraft_type_engine_data.cache_clear()
        try:
            with patch("builtins.open", side_effect=OSError("not found")):
                result = _load_aircraft_type_engine_data()
            assert result == {}
        finally:
            _load_aircraft_type_engine_data.cache_clear()


# ── Component suggestion + quick_add_components ───────────────────────────────


def _post_new_aircraft(client, icao_type="", **overrides):
    fields = {
        "registration": "OO-TST",
        "make": "Cessna",
        "model": "172S",
        "aircraft_type_icao": icao_type,
    }
    fields.update(overrides)
    return client.post("/aircraft/new", data=fields)


class TestComponentSuggestion:
    def test_piston_icao_sets_session_suggestion(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        resp = _post_new_aircraft(client, icao_type="C172")
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            keys = [k for k in sess if k.startswith("suggest_components_")]
            assert keys, "session suggestion not set for piston ICAO type"

    def test_no_icao_type_no_suggestion(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        _post_new_aircraft(client, icao_type="")
        with client.session_transaction() as sess:
            keys = [k for k in sess if k.startswith("suggest_components_")]
            assert not keys

    def test_detail_clears_suggestion_after_one_view(self, app, client):
        _create_user_and_tenant(app, "pilot2@example.com")
        _login(app, client, "pilot2@example.com")
        resp = _post_new_aircraft(client, icao_type="C172", registration="OO-TSU")
        # Redirect now targets /aircraft/<registration> (AircraftRefConverter)
        # rather than a numeric id — look the row up directly.
        with app.app_context():
            ac_id = Aircraft.query.filter_by(registration="OO-TSU").first().id
        client.get(resp.headers["Location"])
        with client.session_transaction() as sess:
            assert f"suggest_components_{ac_id}" not in sess


class TestQuickAddComponents:
    def test_single_engine_adds_engine_and_propeller(self, app, client):
        uid, tid = _create_user_and_tenant(app, "pilot3@example.com")
        _login(app, client, "pilot3@example.com")
        ac_id = _add_aircraft(app, tid, registration="OO-TSV")
        resp = client.post(
            f"/aircraft/{ac_id}/quick-add-components",
            data={"engine_count": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            from models import Component

            comps = Component.query.filter_by(aircraft_id=ac_id).all()
            assert len(comps) == 2
            types = {c.type for c in comps}
            assert types == {"engine", "propeller"}
            assert all(c.position is None for c in comps)

    def test_twin_engine_adds_two_of_each_with_position(self, app, client):
        uid, tid = _create_user_and_tenant(app, "pilot4@example.com")
        _login(app, client, "pilot4@example.com")
        ac_id = _add_aircraft(app, tid, registration="OO-TSW")
        client.post(
            f"/aircraft/{ac_id}/quick-add-components",
            data={"engine_count": "2"},
        )
        with app.app_context():
            from models import Component

            comps = Component.query.filter_by(aircraft_id=ac_id).all()
            assert len(comps) == 4
            positions = {c.position for c in comps}
            assert positions == {"1", "2"}

    def test_invalid_engine_count_defaults_to_one(self, app, client):
        uid, tid = _create_user_and_tenant(app, "pilot5@example.com")
        _login(app, client, "pilot5@example.com")
        ac_id = _add_aircraft(app, tid, registration="OO-TSX")
        client.post(
            f"/aircraft/{ac_id}/quick-add-components",
            data={"engine_count": "notanumber"},
        )
        with app.app_context():
            from models import Component

            assert Component.query.filter_by(aircraft_id=ac_id).count() == 2
