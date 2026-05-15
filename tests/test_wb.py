"""
Tests for Phase 20: Mass & Balance — config, entries, CG computation, envelope check.
"""

import bcrypt  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from datetime import date

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    Role,
    Tenant,
    TenantUser,
    User,
    WeightBalanceConfig,
    WeightBalanceEntry,
    WeightBalanceStation,
    FUEL_DENSITY,
    GAL_TO_L,
    db,
)
from aircraft.routes import _point_in_polygon  # pyright: ignore[reportMissingImports]


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


def _add_aircraft(app, tenant_id, registration="OO-TST", fuel_type="avgas"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id,
            registration=registration,
            make="Cessna",
            model="172S",
            fuel_type=fuel_type,
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_wb_config(app, aircraft_id):
    """Add a W&B config with 3 stations; return (config_id, station_ids)."""
    with app.app_context():
        cfg = WeightBalanceConfig(
            aircraft_id=aircraft_id,
            empty_weight=760.0,
            empty_cg_arm=1.003,
            max_takeoff_weight=1111.0,
            forward_cg_limit=0.889,
            aft_cg_limit=1.219,
        )
        db.session.add(cfg)
        db.session.flush()
        stations = [
            WeightBalanceStation(
                config_id=cfg.id,
                label="Front seats",
                arm=1.016,
                max_weight=190.0,
                is_fuel=False,
                position=0,
            ),
            WeightBalanceStation(
                config_id=cfg.id,
                label="Rear seats",
                arm=1.854,
                max_weight=190.0,
                is_fuel=False,
                position=1,
            ),
            WeightBalanceStation(
                config_id=cfg.id,
                label="Fuel",
                arm=1.219,
                capacity=262.5,
                is_fuel=True,
                position=2,
            ),
        ]
        for s in stations:
            db.session.add(s)
        db.session.commit()
        cfg_id = cfg.id
        st_ids = [s.id for s in stations]
        return cfg_id, st_ids


# ── FUEL_DENSITY and GAL_TO_L constants ──────────────────────────────────────


class TestFuelDensity:
    def test_avgas_density(self, app):
        assert FUEL_DENSITY["avgas"] == pytest.approx(0.72)

    def test_jet_a1_density(self, app):
        assert FUEL_DENSITY["jet_a1"] == pytest.approx(0.81)

    def test_gal_to_l_constant(self, app):
        assert GAL_TO_L == pytest.approx(3.78541)


# ── WeightBalanceConfig model ─────────────────────────────────────────────────


class TestWeightBalanceConfigModel:
    def test_config_created_and_linked(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        with app.app_context():
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            assert cfg.aircraft_id == ac_id
            assert float(cfg.empty_weight) == pytest.approx(760.0)

    def test_stations_ordered_by_position(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        with app.app_context():
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            positions = [s.position for s in cfg.stations]
            assert positions == sorted(positions)

    def test_cascade_delete_config_with_aircraft(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            db.session.delete(ac)
            db.session.commit()
            assert db.session.get(WeightBalanceConfig, cfg_id) is None
            for sid in st_ids:
                assert db.session.get(WeightBalanceStation, sid) is None

    def test_aircraft_wb_config_relationship(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.wb_config is not None
            assert ac.wb_config.id == cfg_id


# ── CG computation ────────────────────────────────────────────────────────────


class TestCGComputation:
    def _compute_cg(self, empty_w, empty_arm, station_ws, station_arms):
        total_m = empty_w * empty_arm + sum(
            w * a for w, a in zip(station_ws, station_arms)
        )
        total_w = empty_w + sum(station_ws)
        return round(total_m / total_w, 3), round(total_w, 1)

    def test_cg_within_envelope(self, app):
        cg, tw = self._compute_cg(
            760.0, 1.003, [80.0, 0.0, 94.5], [1.016, 1.854, 1.219]
        )
        assert 0.889 <= cg <= 1.219
        assert tw <= 1111.0

    def test_cg_stored_on_entry_creation(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        with app.app_context():
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            # station_weights stores volume (L) for fuel station, kg for non-fuel
            fuel_vol = 131.25  # L → 131.25 × 0.72 = 94.5 kg
            fuel_kg = fuel_vol * FUEL_DENSITY["avgas"]
            sw = {str(st_ids[0]): 80.0, str(st_ids[1]): 0.0, str(st_ids[2]): fuel_vol}
            total_m = 760.0 * 1.003 + 80.0 * 1.016 + 0.0 * 1.854 + fuel_kg * 1.219
            total_w = 760.0 + 80.0 + 0.0 + fuel_kg
            expected_cg = round(total_m / total_w, 2)
            entry = WeightBalanceEntry(
                config_id=cfg.id,
                date=date(2026, 1, 1),
                total_weight=round(total_w, 2),
                loaded_cg=expected_cg,
                is_in_envelope=True,
                station_weights=sw,
            )
            db.session.add(entry)
            db.session.commit()
            eid = entry.id
        with app.app_context():
            e = db.session.get(WeightBalanceEntry, eid)
            assert float(e.loaded_cg) == pytest.approx(expected_cg, abs=0.01)
            assert float(e.total_weight) == pytest.approx(total_w, abs=0.1)

    def test_out_of_envelope_aft(self, app):
        """Heavy rear load with no front occupants pushes CG past aft limit."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        with app.app_context():
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            # front=0, rear=280 kg, fuel=0  →  CG ≈ 1.232 > aft limit 1.219
            sw = {str(st_ids[0]): 0.0, str(st_ids[1]): 280.0, str(st_ids[2]): 0.0}
            total_m = 760.0 * 1.003 + 0.0 * 1.016 + 280.0 * 1.854 + 0.0 * 1.219
            total_w = 760.0 + 0.0 + 280.0 + 0.0
            cg = total_m / total_w
            in_env = total_w <= 1111.0 and 0.889 <= cg <= 1.219
            # sanity check: this configuration is indeed out of envelope
            assert not in_env, f"expected out-of-envelope, got CG={cg:.3f}"
            entry = WeightBalanceEntry(
                config_id=cfg.id,
                date=date(2026, 1, 1),
                total_weight=round(total_w, 2),
                loaded_cg=round(cg, 2),
                is_in_envelope=in_env,
                station_weights=sw,
            )
            db.session.add(entry)
            db.session.commit()
            eid = entry.id
        with app.app_context():
            e = db.session.get(WeightBalanceEntry, eid)
            assert e.is_in_envelope is False

    def test_overweight_is_out_of_envelope(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        with app.app_context():
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            total_w = 1200.0  # exceeds MTOW 1111
            cg = 1.050
            entry = WeightBalanceEntry(
                config_id=cfg.id,
                date=date(2026, 1, 1),
                total_weight=total_w,
                loaded_cg=cg,
                is_in_envelope=(total_w <= 1111.0 and 0.889 <= cg <= 1.219),
                station_weights={},
            )
            db.session.add(entry)
            db.session.commit()
            eid = entry.id
        with app.app_context():
            e = db.session.get(WeightBalanceEntry, eid)
            assert e.is_in_envelope is False


# ── W&B config route ──────────────────────────────────────────────────────────


class TestWBConfigRoute:
    def test_get_shows_form_no_config(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/wb/config")
        assert resp.status_code == 200
        assert b"Mass" in resp.data

    def test_get_shows_existing_config(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_wb_config(app, ac_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/wb/config")
        assert resp.status_code == 200
        assert b"760" in resp.data

    def test_post_creates_config(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "760.0",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "datum_note": "Firewall",
                "station_label[]": ["Pilot", "Fuel"],
                "station_arm[]": ["1.016", "1.219"],
                "station_limit[]": ["190", "262.5"],
                "station_is_fuel[]": ["1"],
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.wb_config is not None
            assert len(ac.wb_config.stations) == 2

    def test_post_updates_existing_config(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "770.0",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "station_label[]": ["Pilot"],
                "station_arm[]": ["1.016"],
                "station_limit[]": ["190"],
                "station_is_fuel[]": [],
            },
        )
        with app.app_context():
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            assert float(cfg.empty_weight) == pytest.approx(770.0)
            assert len(cfg.stations) == 1

    def test_redirects_when_not_logged_in(self, client, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        resp = client.get(f"/aircraft/{ac_id}/wb/config")
        assert resp.status_code == 302

    def test_404_for_wrong_tenant(self, app, client):
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac_id = _add_aircraft(app, t2)
        _login(app, client, "a@example.com")
        resp = client.get(f"/aircraft/{ac_id}/wb/config")
        assert resp.status_code == 404


# ── W&B entry list route ──────────────────────────────────────────────────────


class TestWBListRoute:
    def test_redirects_to_config_when_no_config(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/wb/", follow_redirects=True)
        assert b"Configure" in resp.data

    def test_shows_entries(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        with app.app_context():
            db.session.add(
                WeightBalanceEntry(
                    config_id=cfg_id,
                    date=date(2026, 3, 1),
                    label="Morning check",
                    total_weight=934.5,
                    loaded_cg=1.050,
                    is_in_envelope=True,
                    station_weights={},
                )
            )
            db.session.commit()
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/wb/")
        assert resp.status_code == 200
        assert b"Morning check" in resp.data

    def test_shows_ok_badge_for_in_envelope(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        with app.app_context():
            db.session.add(
                WeightBalanceEntry(
                    config_id=cfg_id,
                    date=date(2026, 1, 1),
                    total_weight=900.0,
                    loaded_cg=1.05,
                    is_in_envelope=True,
                    station_weights={},
                )
            )
            db.session.commit()
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/wb/")
        assert b"OK" in resp.data

    def test_shows_out_badge_when_outside_envelope(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        with app.app_context():
            db.session.add(
                WeightBalanceEntry(
                    config_id=cfg_id,
                    date=date(2026, 1, 1),
                    total_weight=1200.0,
                    loaded_cg=1.25,
                    is_in_envelope=False,
                    station_weights={},
                )
            )
            db.session.commit()
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/wb/")
        assert b"OUT" in resp.data


# ── W&B new/edit entry route ──────────────────────────────────────────────────


class TestWBEntryRoute:
    def test_get_new_shows_form(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_wb_config(app, ac_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/wb/new")
        assert resp.status_code == 200
        assert b"Front seats" in resp.data

    def test_post_creates_entry_and_computes_cg(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        _login(app, client)
        # fuel station uses volume (L); 131.25 L × 0.72 kg/L = 94.5 kg
        resp = client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-03-01",
                "label": "Test flight",
                f"weight_{st_ids[0]}": "80.0",
                f"weight_{st_ids[1]}": "0.0",
                f"volume_{st_ids[2]}": "131.25",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            entry = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert entry is not None
            assert float(entry.total_weight) == pytest.approx(
                760.0 + 80.0 + 94.5, abs=0.1
            )
            assert entry.is_in_envelope is True

    def test_post_invalid_date_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "not-a-date",
                f"weight_{st_ids[0]}": "80.0",
            },
        )
        assert resp.status_code == 200
        assert b"valid date" in resp.data.lower()

    def test_post_edit_updates_entry(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        with app.app_context():
            e = WeightBalanceEntry(
                config_id=cfg_id,
                date=date(2026, 1, 1),
                total_weight=900.0,
                loaded_cg=1.05,
                is_in_envelope=True,
                station_weights={},
            )
            db.session.add(e)
            db.session.commit()
            eid = e.id
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/{eid}/edit",
            data={
                "date": "2026-03-15",
                "label": "Updated",
                f"weight_{st_ids[0]}": "70.0",
                f"weight_{st_ids[1]}": "0.0",
                f"volume_{st_ids[2]}": "80.0",
            },
        )
        with app.app_context():
            e = db.session.get(WeightBalanceEntry, eid)
            assert e.label == "Updated"
            assert e.date == date(2026, 3, 15)

    def test_redirects_to_config_when_no_config(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/wb/new", follow_redirects=True)
        assert b"Configure" in resp.data

    def test_404_for_wrong_tenant(self, app, client):
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac_id = _add_aircraft(app, t2)
        _add_wb_config(app, ac_id)
        _login(app, client, "a@example.com")
        resp = client.get(f"/aircraft/{ac_id}/wb/new")
        assert resp.status_code == 404


# ── W&B delete entry route ────────────────────────────────────────────────────


class TestWBDeleteEntryRoute:
    def test_delete_removes_entry(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        with app.app_context():
            e = WeightBalanceEntry(
                config_id=cfg_id,
                date=date(2026, 1, 1),
                total_weight=900.0,
                loaded_cg=1.05,
                is_in_envelope=True,
                station_weights={},
            )
            db.session.add(e)
            db.session.commit()
            eid = e.id
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/wb/{eid}/delete", follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert db.session.get(WeightBalanceEntry, eid) is None

    def test_404_for_wrong_aircraft(self, app, client):
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac1 = _add_aircraft(app, t1, "OO-A")
        ac2 = _add_aircraft(app, t2, "OO-B")
        cfg_id, _ = _add_wb_config(app, ac2)
        with app.app_context():
            e = WeightBalanceEntry(
                config_id=cfg_id,
                date=date(2026, 1, 1),
                total_weight=900.0,
                loaded_cg=1.05,
                is_in_envelope=True,
                station_weights={},
            )
            db.session.add(e)
            db.session.commit()
            eid = e.id
        _login(app, client, "a@example.com")
        resp = client.post(f"/aircraft/{ac1}/wb/{eid}/delete")
        assert resp.status_code == 404


# ── Aircraft detail page shows W&B section ────────────────────────────────────


class TestAircraftDetailWBSection:
    def test_detail_shows_configure_wb_when_no_config(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}")
        assert resp.status_code == 200
        assert b"Mass" in resp.data

    def test_detail_shows_last_entry_when_config_present(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        with app.app_context():
            db.session.add(
                WeightBalanceEntry(
                    config_id=cfg_id,
                    date=date(2026, 3, 1),
                    label="Pre-flight",
                    total_weight=934.5,
                    loaded_cg=1.05,
                    is_in_envelope=True,
                    station_weights={},
                )
            )
            db.session.commit()
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}")
        assert b"Pre-flight" in resp.data

    def test_detail_shows_new_calculation_button_when_config_present(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _add_wb_config(app, ac_id)
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}")
        assert b"wb/new" in resp.data


# ── Fuel type field on aircraft form ─────────────────────────────────────────


class TestFuelTypeField:
    def test_aircraft_default_fuel_type_is_avgas(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.fuel_type == "avgas"

    def test_edit_aircraft_saves_jet_a1(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/edit",
            data={
                "registration": "OO-TST",
                "make": "Robin",
                "model": "DR-401",
                "fuel_type": "jet_a1",
                "has_flight_counter": "on",
                "flight_counter_offset": "0.3",
                "regime": "EASA",
            },
        )
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.fuel_type == "jet_a1"

    def test_invalid_fuel_type_defaults_to_avgas(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/edit",
            data={
                "registration": "OO-TST",
                "make": "Cessna",
                "model": "172S",
                "fuel_type": "diesel",
                "has_flight_counter": "on",
                "flight_counter_offset": "0.3",
                "regime": "EASA",
            },
        )
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.fuel_type == "avgas"


# ── Additional coverage: wb_config POST error paths ───────────────────────────


class TestWBConfigPostErrors:
    def test_invalid_numeric_field_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "not-a-number",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "station_label[]": ["Pilot"],
                "station_arm[]": ["1.016"],
                "station_limit[]": [""],
                "station_is_fuel[]": [],
            },
        )
        assert resp.status_code == 200
        assert b"empty_weight" in resp.data.lower() or b"positive" in resp.data.lower()

    def test_negative_value_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "-5.0",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "station_label[]": ["Pilot"],
                "station_arm[]": ["1.016"],
                "station_limit[]": [""],
                "station_is_fuel[]": [],
            },
        )
        assert resp.status_code == 200
        assert b"positive" in resp.data.lower()

    def test_no_stations_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "760.0",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "station_label[]": [""],
                "station_arm[]": [""],
                "station_limit[]": [""],
                "station_is_fuel[]": [],
            },
        )
        assert resp.status_code == 200
        assert b"station" in resp.data.lower()

    def test_empty_label_station_skipped(self, app, client):
        """A station with a blank label is silently skipped."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "760.0",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "station_label[]": ["Valid", ""],
                "station_arm[]": ["1.016", "1.500"],
                "station_limit[]": ["", ""],
                "station_is_fuel[]": [],
            },
        )
        with app.app_context():
            from models import WeightBalanceConfig

            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            assert len(cfg.stations) == 1

    def test_invalid_arm_station_skipped(self, app, client):
        """A station with a non-numeric arm is silently skipped."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "760.0",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "station_label[]": ["Good", "Bad"],
                "station_arm[]": ["1.016", "not-a-number"],
                "station_limit[]": ["", ""],
                "station_is_fuel[]": [],
            },
        )
        with app.app_context():
            from models import WeightBalanceConfig

            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            assert len(cfg.stations) == 1
            assert cfg.stations[0].label == "Good"

    def test_capacity_field_persisted_for_fuel_station(self, app, client):
        """A numeric limit on a fuel station is stored as capacity (not max_weight)."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "760.0",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "station_label[]": ["Fuel tank"],
                "station_arm[]": ["1.219"],
                "station_limit[]": ["262.5"],
                "station_is_fuel[]": ["0"],
            },
        )
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            st = ac.wb_config.stations[0]
            assert st.is_fuel is True
            assert st.capacity == pytest.approx(262.5)
            assert st.max_weight is None

    def test_max_weight_field_persisted_for_non_fuel_station(self, app, client):
        """A numeric limit on a non-fuel station is stored as max_weight."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "760.0",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "station_label[]": ["Baggage"],
                "station_arm[]": ["2.540"],
                "station_limit[]": ["54.4"],
                "station_is_fuel[]": [],
            },
        )
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            st = ac.wb_config.stations[0]
            assert st.is_fuel is False
            assert float(st.max_weight) == pytest.approx(54.4)
            assert st.capacity is None


# ── Additional coverage: wb_entry edit mode (entry_id lookup) ─────────────────


class TestWBEntryEditMode:
    def test_get_edit_shows_existing_values(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        with app.app_context():
            e = WeightBalanceEntry(
                config_id=cfg_id,
                date=date(2026, 4, 1),
                label="Pre-flight",
                total_weight=900.0,
                loaded_cg=1.05,
                is_in_envelope=True,
                station_weights={str(st_ids[0]): 80.0},
            )
            db.session.add(e)
            db.session.commit()
            eid = e.id
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/wb/{eid}/edit")
        assert resp.status_code == 200
        assert b"Pre-flight" in resp.data

    def test_404_for_entry_belonging_to_other_config(self, app, client):
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac1 = _add_aircraft(app, t1, "OO-A")
        ac2 = _add_aircraft(app, t2, "OO-B")
        cfg1_id, _ = _add_wb_config(app, ac1)
        cfg2_id, _ = _add_wb_config(app, ac2)
        with app.app_context():
            e = WeightBalanceEntry(
                config_id=cfg2_id,
                date=date(2026, 1, 1),
                total_weight=900.0,
                loaded_cg=1.05,
                is_in_envelope=True,
                station_weights={},
            )
            db.session.add(e)
            db.session.commit()
            eid = e.id
        _login(app, client, "a@example.com")
        resp = client.get(f"/aircraft/{ac1}/wb/{eid}/edit")
        assert resp.status_code == 404


# ── Additional coverage: wb_entry POST — negative weight + flight link ────────


class TestWBEntryPostEdgeCases:
    def test_negative_weight_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-03-01",
                f"weight_{st_ids[0]}": "-10.0",
                f"weight_{st_ids[1]}": "0.0",
                f"volume_{st_ids[2]}": "0.0",
            },
        )
        assert resp.status_code == 200
        assert b"non-negative" in resp.data.lower() or b"positive" in resp.data.lower()


# ── Additional coverage: wb_entry_delete when no config ──────────────────────


class TestWBEntryDeleteEdgeCases:
    def test_delete_404_when_no_wb_config(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/wb/999/delete")
        assert resp.status_code == 404


# ── wb_entry mode on aircraft list page ──────────────────────────────────────


class TestAircraftListWBEntryMode:
    def test_wb_entry_mode_shows_only_configured_aircraft(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac1 = _add_aircraft(app, tenant_id, "OO-CFG")
        _add_aircraft(app, tenant_id, "OO-NOCFG")
        _add_wb_config(app, ac1)
        _login(app, client)
        resp = client.get("/aircraft/?next=wb_entry")
        assert resp.status_code == 200
        assert b"OO-CFG" in resp.data
        assert b"OO-NOCFG" not in resp.data

    def test_wb_entry_mode_subtitle(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/aircraft/?next=wb_entry")
        assert resp.status_code == 200
        assert b"W&B" in resp.data

    def test_wb_entry_mode_hides_add_aircraft_button(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/aircraft/?next=wb_entry")
        # url_for('aircraft.new_aircraft') resolves to /aircraft/new
        assert b"/aircraft/new" not in resp.data

    def test_log_flight_mode_hides_add_aircraft_button(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/aircraft/?next=log_flight")
        assert b"/aircraft/new" not in resp.data

    def test_normal_mode_shows_add_aircraft_button(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        _login(app, client)
        resp = client.get("/aircraft/")
        assert b"/aircraft/new" in resp.data


# ── Remaining coverage: max_weight ValueError + delete entry config mismatch ──


class TestWBRemainingCoverage:
    def test_invalid_limit_silently_ignored(self, app, client):
        """Non-numeric station_limit is caught and the station is still created with no capacity."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "760.0",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "station_label[]": ["Fuel"],
                "station_arm[]": ["1.219"],
                "station_limit[]": ["not-a-number"],
                "station_is_fuel[]": ["0"],
            },
        )
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.wb_config is not None
            st = ac.wb_config.stations[0]
            assert st.label == "Fuel"
            assert st.capacity is None
            assert st.max_weight is None

    def test_delete_entry_404_when_entry_belongs_to_other_config(self, app, client):
        """Entry exists but its config_id doesn't match this aircraft's wb_config."""
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac1 = _add_aircraft(app, t1, "OO-A")
        ac2 = _add_aircraft(app, t2, "OO-B")
        cfg1_id, _ = _add_wb_config(app, ac1)
        cfg2_id, _ = _add_wb_config(app, ac2)
        with app.app_context():
            e = WeightBalanceEntry(
                config_id=cfg2_id,
                date=date(2026, 1, 1),
                total_weight=900.0,
                loaded_cg=1.05,
                is_in_envelope=True,
                station_weights={},
            )
            db.session.add(e)
            db.session.commit()
            eid = e.id
        # User a owns ac1; directly craft a delete URL for ac1 but with entry from ac2's config
        _login(app, client, "a@example.com")
        # ac1 has a wb_config, but the entry belongs to ac2's config → 404 at line 553
        resp = client.post(f"/aircraft/{ac1}/wb/{eid}/delete")
        assert resp.status_code == 404


# ── Fuel capacity and volume→kg CG conversion ────────────────────────────────


def _add_wb_config_with_fuel_unit(app, aircraft_id, fuel_unit="L"):
    """Config with one non-fuel and one fuel station; fuel station has capacity."""
    with app.app_context():
        cfg = WeightBalanceConfig(
            aircraft_id=aircraft_id,
            empty_weight=760.0,
            empty_cg_arm=1.003,
            max_takeoff_weight=1111.0,
            forward_cg_limit=0.889,
            aft_cg_limit=1.219,
            fuel_unit=fuel_unit,
        )
        db.session.add(cfg)
        db.session.flush()
        st_pax = WeightBalanceStation(
            config_id=cfg.id,
            label="Pilot",
            arm=1.016,
            max_weight=190.0,
            is_fuel=False,
            position=0,
        )
        st_fuel = WeightBalanceStation(
            config_id=cfg.id,
            label="Fuel tank",
            arm=1.219,
            capacity=200.0,
            is_fuel=True,
            position=1,
        )
        db.session.add(st_pax)
        db.session.add(st_fuel)
        db.session.commit()
        return cfg.id, st_pax.id, st_fuel.id


class TestFuelCapacity:
    def test_config_fuel_unit_defaults_to_L(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        with app.app_context():
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            assert cfg.fuel_unit == "L"

    def test_fuel_unit_gal_stored(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "760.0",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "fuel_unit": "gal",
                "station_label[]": ["Fuel"],
                "station_arm[]": ["1.219"],
                "station_limit[]": ["55"],
                "station_is_fuel[]": ["0"],
            },
        )
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.wb_config.fuel_unit == "gal"

    def test_volume_stored_for_fuel_station(self, app, client):
        """station_weights stores volume (not kg) for fuel stations."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_pax_id, st_fuel_id = _add_wb_config_with_fuel_unit(app, ac_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-04-01",
                f"weight_{st_pax_id}": "80.0",
                f"volume_{st_fuel_id}": "100.0",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert entry is not None
            assert entry.station_weights[str(st_fuel_id)] == pytest.approx(100.0)

    def test_fuel_volume_converted_to_kg_for_cg(self, app, client):
        """100 L avgas × 0.72 kg/L = 72 kg; total = 760 + 80 + 72 = 912."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)  # fuel_type="avgas"
        cfg_id, st_pax_id, st_fuel_id = _add_wb_config_with_fuel_unit(app, ac_id, "L")
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-04-01",
                f"weight_{st_pax_id}": "80.0",
                f"volume_{st_fuel_id}": "100.0",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert entry is not None
            expected_total = 760.0 + 80.0 + 100.0 * FUEL_DENSITY["avgas"]
            assert float(entry.total_weight) == pytest.approx(expected_total, abs=0.1)

    def test_gallons_mode_cg_uses_gal_to_l(self, app, client):
        """10 gal avgas × 3.78541 L/gal × 0.72 kg/L ≈ 27.255 kg added."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)  # fuel_type="avgas"
        cfg_id, st_pax_id, st_fuel_id = _add_wb_config_with_fuel_unit(app, ac_id, "gal")
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-04-01",
                f"weight_{st_pax_id}": "0.0",
                f"volume_{st_fuel_id}": "10.0",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert entry is not None
            fuel_kg = 10.0 * GAL_TO_L * FUEL_DENSITY["avgas"]
            expected_total = 760.0 + 0.0 + fuel_kg
            assert float(entry.total_weight) == pytest.approx(expected_total, abs=0.1)

    def test_jet_a1_density_used_in_cg_computation(self, app, client):
        """100 L Jet-A1 × 0.81 kg/L = 81 kg — different total than avgas (72 kg)."""
        _, tenant_id = _create_user_and_tenant(app, "jet@example.com")
        ac_id = _add_aircraft(app, tenant_id, fuel_type="jet_a1")
        cfg_id, st_pax_id, st_fuel_id = _add_wb_config_with_fuel_unit(app, ac_id, "L")
        _login(app, client, "jet@example.com")
        client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-04-01",
                f"weight_{st_pax_id}": "0.0",
                f"volume_{st_fuel_id}": "100.0",
            },
            follow_redirects=True,
        )
        with app.app_context():
            entry = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert entry is not None
            expected_total = 760.0 + 0.0 + 100.0 * FUEL_DENSITY["jet_a1"]
            assert float(entry.total_weight) == pytest.approx(expected_total, abs=0.1)
            # Sanity: Jet-A1 result differs from avgas result
            avgas_total = 760.0 + 100.0 * FUEL_DENSITY["avgas"]
            assert float(entry.total_weight) != pytest.approx(avgas_total, abs=0.1)

    def test_capacity_exceeded_shows_error(self, app, client):
        """Posting a volume above the station capacity triggers a validation error."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_pax_id, st_fuel_id = _add_wb_config_with_fuel_unit(app, ac_id)
        _login(app, client)
        # capacity is 200.0 L; post 250.0 L
        resp = client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-04-01",
                f"weight_{st_pax_id}": "80.0",
                f"volume_{st_fuel_id}": "250.0",
            },
        )
        assert resp.status_code == 200
        assert b"capacity" in resp.data.lower() or b"exceeds" in resp.data.lower()

    def test_negative_volume_shows_error(self, app, client):
        """Posting a negative volume for a fuel station triggers an error."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_pax_id, st_fuel_id = _add_wb_config_with_fuel_unit(app, ac_id)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-04-01",
                f"weight_{st_pax_id}": "80.0",
                f"volume_{st_fuel_id}": "-5.0",
            },
        )
        assert resp.status_code == 200
        assert b"non-negative" in resp.data.lower() or b"positive" in resp.data.lower()

    def test_invalid_fuel_unit_defaults_to_L(self, app, client):
        """An invalid fuel_unit value is silently coerced to 'L'."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "760.0",
                "empty_cg_arm": "1.003",
                "max_takeoff_weight": "1111.0",
                "forward_cg_limit": "0.889",
                "aft_cg_limit": "1.219",
                "fuel_unit": "kg",  # invalid
                "station_label[]": ["Pilot"],
                "station_arm[]": ["1.016"],
                "station_limit[]": [""],
                "station_is_fuel[]": [],
            },
        )
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.wb_config.fuel_unit == "L"


# ── Polygon envelope ──────────────────────────────────────────────────────────
#
# Example from user (converted to metric — 1 in = 0.0254 m, 1 lb = 0.453592 kg):
#   (11.5 in, 1000 lb) → (0.2921 m, 453.6 kg)
#   (11.5 in, 1200 lb) → (0.2921 m, 544.3 kg)
#   (14   in, 1500 lb) → (0.3556 m, 680.4 kg)
#   (21   in, 1500 lb) → (0.5334 m, 680.4 kg)
#   (21   in, 1000 lb) → (0.5334 m, 453.6 kg)
#
# The polygon is a pentagon: rectangular on the right and aft sides, with the
# forward limit slanting inward (more restrictive) as weight increases.

# Polygon vertices
_POLY = [
    [0.2921, 453.6],
    [0.2921, 544.3],
    [0.3556, 680.4],
    [0.5334, 680.4],
    [0.5334, 453.6],
]


# Arm at which the forward boundary lies at a given weight along the angled edge
# (interpolated between vertices [0.2921, 544.3] and [0.3556, 680.4])
def _fwd_boundary(w_kg):
    return 0.2921 + (0.3556 - 0.2921) * (w_kg - 544.3) / (680.4 - 544.3)


def _add_wb_config_polygon(app, aircraft_id):
    """Config with the user's non-rectangular polygon envelope."""
    with app.app_context():
        cfg = WeightBalanceConfig(
            aircraft_id=aircraft_id,
            empty_weight=350.0,
            empty_cg_arm=0.38,
            max_takeoff_weight=680.4,
            forward_cg_limit=0.2921,
            aft_cg_limit=0.5334,
            envelope_points=_POLY,
        )
        db.session.add(cfg)
        db.session.flush()
        st = WeightBalanceStation(
            config_id=cfg.id,
            label="Occupants",
            arm=0.40,
            max_weight=200.0,
            is_fuel=False,
            position=0,
        )
        db.session.add(st)
        db.session.commit()
        return cfg.id, st.id


class TestPointInPolygon:
    """Unit tests for the ray-casting helper, independent of routes/DB."""

    def test_center_is_inside(self, app):
        assert _point_in_polygon(0.42, 567.0, _POLY) is True

    def test_inside_near_left_edge_low_weight(self, app):
        # At w=500 kg, forward boundary = 0.2921 (vertical edge) → arm 0.31 is inside
        assert _point_in_polygon(0.31, 500.0, _POLY) is True

    def test_outside_forward_high_weight(self, app):
        # At w=640 kg, forward boundary ≈ 0.337; arm 0.30 is forward of it
        fwd = _fwd_boundary(640.0)
        assert 0.30 < fwd  # sanity
        assert _point_in_polygon(0.30, 640.0, _POLY) is False

    def test_outside_aft(self, app):
        # Aft limit is 0.5334; arm 0.55 is beyond it
        assert _point_in_polygon(0.55, 560.0, _POLY) is False

    def test_outside_forward_low_weight(self, app):
        # At low weight, forward limit = 0.2921; arm 0.28 is forward
        assert _point_in_polygon(0.28, 500.0, _POLY) is False

    def test_outside_above_max_weight(self, app):
        # Max weight in polygon is 680.4 kg; 700 kg is above it
        assert _point_in_polygon(0.42, 700.0, _POLY) is False

    def test_outside_below_min_weight(self, app):
        # Bottom edge is at 453.6 kg; 450 kg is below the polygon
        assert _point_in_polygon(0.42, 450.0, _POLY) is False

    def test_rectangular_envelope_center_is_inside(self, app):
        rect = [[0.889, 0.0], [1.219, 0.0], [1.219, 1111.0], [0.889, 1111.0]]
        assert _point_in_polygon(1.05, 900.0, rect) is True

    def test_rectangular_envelope_outside(self, app):
        rect = [[0.889, 0.0], [1.219, 0.0], [1.219, 1111.0], [0.889, 1111.0]]
        assert _point_in_polygon(1.30, 900.0, rect) is False


class TestPolygonEnvelopeRoute:
    """Integration tests: polygon envelope is used in the wb_entry route."""

    def test_post_in_polygon_saved_as_in_envelope(self, app, client):
        _, tenant_id = _create_user_and_tenant(app, "poly1@example.com")
        ac_id = _add_aircraft(app, tenant_id, "OO-PLY")
        cfg_id, st_id = _add_wb_config_polygon(app, ac_id)
        _login(app, client, "poly1@example.com")
        # Point (CG ≈ 0.42, weight ≈ 567) should be inside the polygon
        client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-05-01",
                f"weight_{st_id}": "217.0",  # 350 + 217 = 567 kg total
            },
            follow_redirects=True,
        )
        with app.app_context():
            e = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert e is not None
            assert e.is_in_envelope is True

    def test_post_outside_polygon_aft_saved_as_out(self, app, client):
        _, tenant_id = _create_user_and_tenant(app, "poly2@example.com")
        ac_id = _add_aircraft(app, tenant_id, "OO-PLZ")
        cfg_id, st_id = _add_wb_config_polygon(app, ac_id)
        _login(app, client, "poly2@example.com")
        with app.app_context():
            # Override station arm to push CG past aft limit (0.5334 m)
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            for s in cfg.stations:
                s.arm = 0.60  # far aft
            db.session.commit()
        client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-05-01",
                f"weight_{st_id}": "100.0",
            },
            follow_redirects=True,
        )
        with app.app_context():
            e = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert e is not None
            assert e.is_in_envelope is False

    def test_post_outside_polygon_overweight_saved_as_out(self, app, client):
        _, tenant_id = _create_user_and_tenant(app, "poly3@example.com")
        ac_id = _add_aircraft(app, tenant_id, "OO-PLW")
        cfg_id, st_id = _add_wb_config_polygon(app, ac_id)
        _login(app, client, "poly3@example.com")
        # 350 (empty) + 400 = 750 kg > polygon max 680.4 kg
        client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-05-01",
                f"weight_{st_id}": "400.0",
            },
            follow_redirects=True,
        )
        with app.app_context():
            e = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert e is not None
            assert e.is_in_envelope is False

    def test_polygon_config_saved_via_route(self, app, client):
        _, tenant_id = _create_user_and_tenant(app, "poly4@example.com")
        ac_id = _add_aircraft(app, tenant_id, "OO-PLV")
        _login(app, client, "poly4@example.com")
        client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "350.0",
                "empty_cg_arm": "0.38",
                "max_takeoff_weight": "680.4",
                "forward_cg_limit": "0.2921",
                "aft_cg_limit": "0.5334",
                "env_arm[]": ["0.2921", "0.2921", "0.3556", "0.5334", "0.5334"],
                "env_weight[]": ["453.6", "544.3", "680.4", "680.4", "453.6"],
                "station_label[]": ["Occupants"],
                "station_arm[]": ["0.40"],
                "station_limit[]": ["200"],
                "station_is_fuel[]": [],
            },
            follow_redirects=True,
        )
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            pts = ac.wb_config.envelope_points
            assert pts is not None
            assert len(pts) == 5
            assert pts[0][0] == pytest.approx(0.2921, abs=0.0001)

    def test_fewer_than_3_polygon_points_not_stored(self, app, client):
        _, tenant_id = _create_user_and_tenant(app, "poly5@example.com")
        ac_id = _add_aircraft(app, tenant_id, "OO-PLU")
        _login(app, client, "poly5@example.com")
        client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "350.0",
                "empty_cg_arm": "0.38",
                "max_takeoff_weight": "680.4",
                "forward_cg_limit": "0.2921",
                "aft_cg_limit": "0.5334",
                "env_arm[]": ["0.30", "0.50"],
                "env_weight[]": ["500", "500"],
                "station_label[]": ["Pax"],
                "station_arm[]": ["0.40"],
                "station_limit[]": [""],
                "station_is_fuel[]": [],
            },
        )
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.wb_config.envelope_points is None

    def test_invalid_envelope_point_silently_skipped(self, app, client):
        """Non-numeric env_arm/env_weight values are skipped; valid remainder is stored."""
        _, tenant_id = _create_user_and_tenant(app, "polyinv@example.com")
        ac_id = _add_aircraft(app, tenant_id, "OO-INV")
        _login(app, client, "polyinv@example.com")
        client.post(
            f"/aircraft/{ac_id}/wb/config",
            data={
                "empty_weight": "350.0",
                "empty_cg_arm": "0.38",
                "max_takeoff_weight": "680.4",
                "forward_cg_limit": "0.2921",
                "aft_cg_limit": "0.5334",
                # 5 pairs, one arm is non-numeric → 4 valid points stored
                "env_arm[]": ["0.2921", "bad", "0.3556", "0.5334", "0.5334"],
                "env_weight[]": ["453.6", "544.3", "680.4", "680.4", "453.6"],
                "station_label[]": ["Pax"],
                "station_arm[]": ["0.40"],
                "station_limit[]": ["200"],
                "station_is_fuel[]": [],
            },
            follow_redirects=True,
        )
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            pts = ac.wb_config.envelope_points
            assert pts is not None
            assert len(pts) == 4

    def test_rectangular_fallback_still_works(self, app, client):
        """A config without envelope_points still uses the scalar fwd/aft/MTOW check."""
        _, tenant_id = _create_user_and_tenant(app, "rect@example.com")
        ac_id = _add_aircraft(app, tenant_id, "OO-RCT")
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        _login(app, client, "rect@example.com")
        with app.app_context():
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            assert cfg.envelope_points is None
        client.post(
            f"/aircraft/{ac_id}/wb/new",
            data={
                "date": "2026-05-01",
                f"weight_{st_ids[0]}": "80.0",
                f"weight_{st_ids[1]}": "0.0",
                f"volume_{st_ids[2]}": "131.25",
            },
            follow_redirects=True,
        )
        with app.app_context():
            e = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert e is not None
            assert e.is_in_envelope is True
