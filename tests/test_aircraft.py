"""
Tests for Phase 2: Aircraft management routes (CRUD + auth guard).
"""

import bcrypt  # pyright: ignore[reportMissingImports]
from models import (
    Aircraft,
    Component,
    ComponentType,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]


def _login_orphan_user(app, client):
    """Create a User with no TenantUser and inject into session."""
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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_user_and_tenant(app, email="pilot@example.com", password="testpassword123"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()

        user = User(
            email=email,
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
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

    def test_placeholder_flag_saved(self, app, client):
        _create_user_and_tenant(app)
        _login(app, client)
        client.post(
            "/aircraft/new",
            data={
                "registration": "OO-PNH",
                "make": "Cessna",
                "model": "172S",
                "is_placeholder": "on",
            },
        )
        with app.app_context():
            assert (
                Aircraft.query.filter_by(registration="OO-PNH").first().is_placeholder
                is True
            )


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
        assert f"/aircraft/{ac_id}" in response.headers["Location"]


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
