"""
Tests for Phase 20: Mass & Balance — config, entries, CG computation, envelope check.
"""
import bcrypt  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]
from datetime import date

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft, FlightEntry, FlightCrew, CrewRole, Role,
    Tenant, TenantUser, User,
    WeightBalanceConfig, WeightBalanceEntry, WeightBalanceStation,
    FUEL_DENSITY, db,
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


def _add_aircraft(app, tenant_id, registration="OO-TST", fuel_type="avgas"):
    with app.app_context():
        ac = Aircraft(tenant_id=tenant_id, registration=registration,
                      make="Cessna", model="172S", fuel_type=fuel_type)
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
            WeightBalanceStation(config_id=cfg.id, label="Front seats",
                                 arm=1.016, max_weight=190.0, is_fuel=False, position=0),
            WeightBalanceStation(config_id=cfg.id, label="Rear seats",
                                 arm=1.854, max_weight=190.0, is_fuel=False, position=1),
            WeightBalanceStation(config_id=cfg.id, label="Fuel",
                                 arm=1.219, max_weight=189.0, is_fuel=True, position=2),
        ]
        for s in stations:
            db.session.add(s)
        db.session.commit()
        cfg_id = cfg.id
        st_ids = [s.id for s in stations]
        return cfg_id, st_ids


# ── FUEL_DENSITY constant ─────────────────────────────────────────────────────

class TestFuelDensity:
    def test_avgas_density(self, app):
        assert FUEL_DENSITY["avgas"] == pytest.approx(0.72)

    def test_jet_a1_density(self, app):
        assert FUEL_DENSITY["jet_a1"] == pytest.approx(0.81)


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
        total_m = empty_w * empty_arm + sum(w * a for w, a in zip(station_ws, station_arms))
        total_w = empty_w + sum(station_ws)
        return round(total_m / total_w, 3), round(total_w, 1)

    def test_cg_within_envelope(self, app):
        cg, tw = self._compute_cg(760.0, 1.003, [80.0, 0.0, 94.5], [1.016, 1.854, 1.219])
        assert 0.889 <= cg <= 1.219
        assert tw <= 1111.0

    def test_cg_stored_on_entry_creation(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        with app.app_context():
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            sw = {str(st_ids[0]): 80.0, str(st_ids[1]): 0.0, str(st_ids[2]): 94.5}
            total_m = 760.0 * 1.003 + 80.0 * 1.016 + 0.0 * 1.854 + 94.5 * 1.219
            total_w = 760.0 + 80.0 + 0.0 + 94.5
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
        resp = client.post(f"/aircraft/{ac_id}/wb/config", data={
            "empty_weight": "760.0",
            "empty_cg_arm": "1.003",
            "max_takeoff_weight": "1111.0",
            "forward_cg_limit": "0.889",
            "aft_cg_limit": "1.219",
            "datum_note": "Firewall",
            "station_label[]": ["Pilot", "Fuel"],
            "station_arm[]": ["1.016", "1.219"],
            "station_max_weight[]": ["190", "189"],
            "station_is_fuel[]": ["1"],
        }, follow_redirects=True)
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
        client.post(f"/aircraft/{ac_id}/wb/config", data={
            "empty_weight": "770.0",
            "empty_cg_arm": "1.003",
            "max_takeoff_weight": "1111.0",
            "forward_cg_limit": "0.889",
            "aft_cg_limit": "1.219",
            "station_label[]": ["Pilot"],
            "station_arm[]": ["1.016"],
            "station_max_weight[]": ["190"],
            "station_is_fuel[]": [],
        })
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
            db.session.add(WeightBalanceEntry(
                config_id=cfg_id,
                date=date(2026, 3, 1),
                label="Morning check",
                total_weight=934.5,
                loaded_cg=1.050,
                is_in_envelope=True,
                station_weights={},
            ))
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
            db.session.add(WeightBalanceEntry(
                config_id=cfg_id, date=date(2026, 1, 1),
                total_weight=900.0, loaded_cg=1.05,
                is_in_envelope=True, station_weights={},
            ))
            db.session.commit()
        _login(app, client)
        resp = client.get(f"/aircraft/{ac_id}/wb/")
        assert b"OK" in resp.data

    def test_shows_out_badge_when_outside_envelope(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        with app.app_context():
            db.session.add(WeightBalanceEntry(
                config_id=cfg_id, date=date(2026, 1, 1),
                total_weight=1200.0, loaded_cg=1.25,
                is_in_envelope=False, station_weights={},
            ))
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
        resp = client.post(f"/aircraft/{ac_id}/wb/new", data={
            "date": "2026-03-01",
            "label": "Test flight",
            f"weight_{st_ids[0]}": "80.0",
            f"weight_{st_ids[1]}": "0.0",
            f"weight_{st_ids[2]}": "94.5",
        }, follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            entry = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert entry is not None
            assert float(entry.total_weight) == pytest.approx(760.0 + 80.0 + 94.5, abs=0.1)
            assert entry.is_in_envelope is True

    def test_post_invalid_date_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/wb/new", data={
            "date": "not-a-date",
            f"weight_{st_ids[0]}": "80.0",
        })
        assert resp.status_code == 200
        assert b"valid date" in resp.data.lower()

    def test_post_edit_updates_entry(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        with app.app_context():
            e = WeightBalanceEntry(
                config_id=cfg_id, date=date(2026, 1, 1),
                total_weight=900.0, loaded_cg=1.05,
                is_in_envelope=True, station_weights={},
            )
            db.session.add(e)
            db.session.commit()
            eid = e.id
        _login(app, client)
        client.post(f"/aircraft/{ac_id}/wb/{eid}/edit", data={
            "date": "2026-03-15",
            "label": "Updated",
            f"weight_{st_ids[0]}": "70.0",
            f"weight_{st_ids[1]}": "0.0",
            f"weight_{st_ids[2]}": "80.0",
        })
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
                config_id=cfg_id, date=date(2026, 1, 1),
                total_weight=900.0, loaded_cg=1.05,
                is_in_envelope=True, station_weights={},
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
                config_id=cfg_id, date=date(2026, 1, 1),
                total_weight=900.0, loaded_cg=1.05,
                is_in_envelope=True, station_weights={},
            )
            db.session.add(e)
            db.session.commit()
            eid = e.id
        _login(app, client, "a@example.com")
        resp = client.post(f"/aircraft/{ac1}/wb/{eid}/delete")
        assert resp.status_code == 404


# ── Flight link ───────────────────────────────────────────────────────────────

class TestWBFlightLink:
    def test_entry_links_to_flight(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=ac_id,
                date=date(2026, 3, 1),
                departure_icao="EBOS",
                arrival_icao="EHRD",
                engine_time_counter_start=100.0,
                engine_time_counter_end=101.5,
                flight_time=1.5,
                landing_count=1,
            )
            db.session.add(fe)
            db.session.flush()
            db.session.add(FlightCrew(flight_id=fe.id, name="J. Klein",
                                      role=CrewRole.PIC, sort_order=0))
            db.session.commit()
            fid = fe.id
        _login(app, client)
        client.post(f"/aircraft/{ac_id}/wb/new", data={
            "date": "2026-03-01",
            f"weight_{st_ids[0]}": "80.0",
            f"weight_{st_ids[1]}": "0.0",
            f"weight_{st_ids[2]}": "94.5",
            "flight_entry_id": str(fid),
        })
        with app.app_context():
            entry = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert entry is not None
            assert entry.flight_entry_id == fid

    def test_flight_link_cleared_on_flight_delete(self, app):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=ac_id,
                date=date(2026, 3, 1),
                departure_icao="EBOS",
                arrival_icao="EHRD",
                engine_time_counter_start=100.0,
                engine_time_counter_end=101.5,
                flight_time=1.5,
                landing_count=1,
            )
            db.session.add(fe)
            db.session.flush()
            db.session.add(FlightCrew(flight_id=fe.id, name="J. Klein",
                                      role=CrewRole.PIC, sort_order=0))
            e = WeightBalanceEntry(
                config_id=cfg_id,
                date=date(2026, 3, 1),
                total_weight=934.5,
                loaded_cg=1.05,
                is_in_envelope=True,
                station_weights={},
                flight_entry_id=fe.id,
            )
            db.session.add(e)
            db.session.commit()
            eid, fid = e.id, fe.id

        with app.app_context():
            fe = db.session.get(FlightEntry, fid)
            db.session.delete(fe)
            db.session.commit()
            e = db.session.get(WeightBalanceEntry, eid)
            assert e is not None
            assert e.flight_entry_id is None


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
            db.session.add(WeightBalanceEntry(
                config_id=cfg_id, date=date(2026, 3, 1),
                label="Pre-flight",
                total_weight=934.5, loaded_cg=1.05,
                is_in_envelope=True, station_weights={},
            ))
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
        client.post(f"/aircraft/{ac_id}/edit", data={
            "registration": "OO-TST",
            "make": "Robin",
            "model": "DR-401",
            "fuel_type": "jet_a1",
            "has_flight_counter": "on",
            "flight_counter_offset": "0.3",
            "regime": "EASA",
        })
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.fuel_type == "jet_a1"

    def test_invalid_fuel_type_defaults_to_avgas(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(f"/aircraft/{ac_id}/edit", data={
            "registration": "OO-TST",
            "make": "Cessna",
            "model": "172S",
            "fuel_type": "diesel",
            "has_flight_counter": "on",
            "flight_counter_offset": "0.3",
            "regime": "EASA",
        })
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.fuel_type == "avgas"




# ── Additional coverage: wb_config POST error paths ───────────────────────────

class TestWBConfigPostErrors:
    def test_invalid_numeric_field_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/wb/config", data={
            "empty_weight": "not-a-number",
            "empty_cg_arm": "1.003",
            "max_takeoff_weight": "1111.0",
            "forward_cg_limit": "0.889",
            "aft_cg_limit": "1.219",
            "station_label[]": ["Pilot"],
            "station_arm[]": ["1.016"],
            "station_max_weight[]": [""],
            "station_is_fuel[]": [],
        })
        assert resp.status_code == 200
        assert b"empty_weight" in resp.data.lower() or b"positive" in resp.data.lower()

    def test_negative_value_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/wb/config", data={
            "empty_weight": "-5.0",
            "empty_cg_arm": "1.003",
            "max_takeoff_weight": "1111.0",
            "forward_cg_limit": "0.889",
            "aft_cg_limit": "1.219",
            "station_label[]": ["Pilot"],
            "station_arm[]": ["1.016"],
            "station_max_weight[]": [""],
            "station_is_fuel[]": [],
        })
        assert resp.status_code == 200
        assert b"positive" in resp.data.lower()

    def test_no_stations_shows_error(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/wb/config", data={
            "empty_weight": "760.0",
            "empty_cg_arm": "1.003",
            "max_takeoff_weight": "1111.0",
            "forward_cg_limit": "0.889",
            "aft_cg_limit": "1.219",
            "station_label[]": [""],
            "station_arm[]": [""],
            "station_max_weight[]": [""],
            "station_is_fuel[]": [],
        })
        assert resp.status_code == 200
        assert b"station" in resp.data.lower()

    def test_empty_label_station_skipped(self, app, client):
        """A station with a blank label is silently skipped."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, _ = _add_wb_config(app, ac_id)
        _login(app, client)
        client.post(f"/aircraft/{ac_id}/wb/config", data={
            "empty_weight": "760.0",
            "empty_cg_arm": "1.003",
            "max_takeoff_weight": "1111.0",
            "forward_cg_limit": "0.889",
            "aft_cg_limit": "1.219",
            "station_label[]": ["Valid", ""],
            "station_arm[]": ["1.016", "1.500"],
            "station_max_weight[]": ["", ""],
            "station_is_fuel[]": [],
        })
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
        client.post(f"/aircraft/{ac_id}/wb/config", data={
            "empty_weight": "760.0",
            "empty_cg_arm": "1.003",
            "max_takeoff_weight": "1111.0",
            "forward_cg_limit": "0.889",
            "aft_cg_limit": "1.219",
            "station_label[]": ["Good", "Bad"],
            "station_arm[]": ["1.016", "not-a-number"],
            "station_max_weight[]": ["", ""],
            "station_is_fuel[]": [],
        })
        with app.app_context():
            from models import WeightBalanceConfig
            cfg = db.session.get(WeightBalanceConfig, cfg_id)
            assert len(cfg.stations) == 1
            assert cfg.stations[0].label == "Good"

    def test_max_weight_field_persisted(self, app, client):
        """A numeric max_weight on a station is stored."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(f"/aircraft/{ac_id}/wb/config", data={
            "empty_weight": "760.0",
            "empty_cg_arm": "1.003",
            "max_takeoff_weight": "1111.0",
            "forward_cg_limit": "0.889",
            "aft_cg_limit": "1.219",
            "station_label[]": ["Fuel"],
            "station_arm[]": ["1.219"],
            "station_max_weight[]": ["189.0"],
            "station_is_fuel[]": ["0"],
        })
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            st = ac.wb_config.stations[0]
            assert float(st.max_weight) == pytest.approx(189.0)


# ── Additional coverage: wb_entry edit mode (entry_id lookup) ─────────────────

class TestWBEntryEditMode:
    def test_get_edit_shows_existing_values(self, app, client):
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        with app.app_context():
            e = WeightBalanceEntry(
                config_id=cfg_id, date=date(2026, 4, 1),
                label="Pre-flight",
                total_weight=900.0, loaded_cg=1.05,
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
                config_id=cfg2_id, date=date(2026, 1, 1),
                total_weight=900.0, loaded_cg=1.05,
                is_in_envelope=True, station_weights={},
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
        resp = client.post(f"/aircraft/{ac_id}/wb/new", data={
            "date": "2026-03-01",
            f"weight_{st_ids[0]}": "-10.0",
            f"weight_{st_ids[1]}": "0.0",
            f"weight_{st_ids[2]}": "0.0",
        })
        assert resp.status_code == 200
        assert b"non-negative" in resp.data.lower() or b"positive" in resp.data.lower()

    def test_flight_link_ignored_for_wrong_aircraft(self, app, client):
        """A flight_entry_id belonging to another aircraft must be silently ignored."""
        _, t1 = _create_user_and_tenant(app, "a@example.com")
        _, t2 = _create_user_and_tenant(app, "b@example.com")
        ac1 = _add_aircraft(app, t1, "OO-A")
        ac2 = _add_aircraft(app, t2, "OO-B")
        cfg_id, st_ids = _add_wb_config(app, ac1)
        with app.app_context():
            fe = FlightEntry(
                aircraft_id=ac2,
                date=date(2026, 3, 1),
                departure_icao="EBOS", arrival_icao="EHRD",
                engine_time_counter_start=100.0, engine_time_counter_end=101.5,
                flight_time=1.5, landing_count=1,
            )
            db.session.add(fe)
            db.session.flush()
            db.session.add(FlightCrew(flight_id=fe.id, name="J. Klein",
                                      role=CrewRole.PIC, sort_order=0))
            db.session.commit()
            fid = fe.id
        _login(app, client, "a@example.com")
        client.post(f"/aircraft/{ac1}/wb/new", data={
            "date": "2026-03-01",
            f"weight_{st_ids[0]}": "80.0",
            f"weight_{st_ids[1]}": "0.0",
            f"weight_{st_ids[2]}": "0.0",
            "flight_entry_id": str(fid),
        })
        with app.app_context():
            entry = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert entry is not None
            assert entry.flight_entry_id is None

    def test_invalid_flight_entry_id_ignored(self, app, client):
        """A non-integer flight_entry_id is silently discarded."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        cfg_id, st_ids = _add_wb_config(app, ac_id)
        _login(app, client)
        resp = client.post(f"/aircraft/{ac_id}/wb/new", data={
            "date": "2026-03-01",
            f"weight_{st_ids[0]}": "80.0",
            f"weight_{st_ids[1]}": "0.0",
            f"weight_{st_ids[2]}": "0.0",
            "flight_entry_id": "not-an-int",
        }, follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            entry = WeightBalanceEntry.query.filter_by(config_id=cfg_id).first()
            assert entry is not None
            assert entry.flight_entry_id is None


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
        ac2 = _add_aircraft(app, tenant_id, "OO-NOCFG")
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
    def test_invalid_max_weight_silently_ignored(self, app, client):
        """Non-numeric max_weight is caught and the station is still created."""
        _, tenant_id = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tenant_id)
        _login(app, client)
        client.post(f"/aircraft/{ac_id}/wb/config", data={
            "empty_weight": "760.0",
            "empty_cg_arm": "1.003",
            "max_takeoff_weight": "1111.0",
            "forward_cg_limit": "0.889",
            "aft_cg_limit": "1.219",
            "station_label[]": ["Fuel"],
            "station_arm[]": ["1.219"],
            "station_max_weight[]": ["not-a-number"],
            "station_is_fuel[]": ["0"],
        })
        with app.app_context():
            ac = db.session.get(Aircraft, ac_id)
            assert ac.wb_config is not None
            st = ac.wb_config.stations[0]
            assert st.label == "Fuel"
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
                config_id=cfg2_id, date=date(2026, 1, 1),
                total_weight=900.0, loaded_cg=1.05,
                is_in_envelope=True, station_weights={},
            )
            db.session.add(e)
            db.session.commit()
            eid = e.id
        # User a owns ac1; directly craft a delete URL for ac1 but with entry from ac2's config
        _login(app, client, "a@example.com")
        # ac1 has a wb_config, but the entry belongs to ac2's config → 404 at line 553
        resp = client.post(f"/aircraft/{ac1}/wb/{eid}/delete")
        assert resp.status_code == 404
