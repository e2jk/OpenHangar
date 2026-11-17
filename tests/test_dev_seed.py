"""Smoke test for dev_seed.seed() — runs the real seeding logic end to
end so schema/model drift (e.g. a seed helper still referencing a
relationship a refactor removed) is caught locally by
run-tests-with-coverage.sh. Mirrors test_demo_seed.py's rationale;
_seed_helpers.py/dev_seed.py are excluded from the coverage requirement
(.coveragerc), so nothing else in the suite calls this function to
completion.
"""

from dev_seed import seed  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AircraftOwner,
    Tenant,
    User,
)


class TestDevSeedEndToEnd:
    def test_seed_runs_without_error(self, app, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ENV", "development")
        with app.app_context():
            seed()
            assert Tenant.query.filter_by(slug="dev-hangar").first() is not None
            assert (
                User.query.filter_by(email="admin@openhangar.dev").first() is not None
            )

    def test_shared_ownership_seeded_on_robin(self, app, monkeypatch):
        """Phase 39: the Robin (OO-GRN) gets 3 co-owners via the legacy
        escape-hatch, without a TenantProfile on the main dev tenant."""
        monkeypatch.setenv("OPENHANGAR_ENV", "development")
        with app.app_context():
            seed()
            robin = Aircraft.query.filter_by(registration="OO-GRN").first()
            assert robin is not None
            owners = AircraftOwner.query.filter_by(aircraft_id=robin.id).all()
            assert len(owners) == 3
            assert sum(o.share_pct for o in owners) == 100

    def test_shared_ownership_dedicated_tenant_seeded(self, app, monkeypatch):
        """The dedicated OO-SH1 shared-ownership tenant (separate from Dev
        Hangar, mirroring demo_seed's per-slot sub-tenant) is seeded with
        its 3 co-owners."""
        monkeypatch.setenv("OPENHANGAR_ENV", "development")
        with app.app_context():
            seed()
            assert Tenant.query.filter_by(slug="dev-hangar-sho").first() is not None
            sh1 = Aircraft.query.filter_by(registration="OO-SH1").first()
            assert sh1 is not None
            owners = AircraftOwner.query.filter_by(aircraft_id=sh1.id).all()
            assert len(owners) == 3
            assert sum(o.share_pct for o in owners) == 100
            for email in (
                "alice@openhangar.dev",
                "bob@openhangar.dev",
                "carol@openhangar.dev",
            ):
                assert User.query.filter_by(email=email).first() is not None

    def test_billing_dashboard_renders_for_seeded_robin(self, app, client, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ENV", "development")
        with app.app_context():
            seed()
            admin = User.query.filter_by(email="admin@openhangar.dev").first()
            robin = Aircraft.query.filter_by(registration="OO-GRN").first()
            uid = admin.id
            acid = robin.id
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        r = client.get(f"/aircraft/{acid}/owners/billing")
        assert r.status_code == 200

    def test_passports_seeded_with_contrasting_progress(self, app, client, monkeypatch):
        """Phase 41: three passports for the admin, stamped live from the
        seeded logbook (partly visited, dated edition, complete), plus home
        airfields for "Where next?"; every passport page renders."""
        from models import Passport, PilotProfile  # pyright: ignore[reportMissingImports]
        from passports.eligibility import (  # pyright: ignore[reportMissingImports]
            passport_progress,
        )

        monkeypatch.setenv("OPENHANGAR_ENV", "development")
        with app.app_context():
            seed()
            admin = User.query.filter_by(email="admin@openhangar.dev").first()
            uid = admin.id
            passports = {
                p.title.split(" 2")[0]: p
                for p in Passport.query.filter_by(owner_user_id=uid).all()
            }
            assert set(passports) == {
                "Belgian airfields",
                "Landing voucher booklet",
                "Training airfields",
            }
            lifetime = passport_progress(passports["Belgian airfields"], uid)
            booklet = passport_progress(passports["Landing voucher booklet"], uid)
            training = passport_progress(passports["Training airfields"], uid)
            assert 0 < lifetime["visited"] < lifetime["total"]
            assert lifetime["excluded_closed"] == 1
            assert any(row.get("latitude") is None for row in lifetime["rows"])
            visited = {row["ident"] for row in booklet["rows"] if row["visited"]}
            assert {"EBKT", "EBSP", "EBSH", "EBSG"} <= visited
            assert training["percent"] == 100
            # The set-aside first EBCI arrival moves its stamp to the next one.
            ebci = next(r for r in lifetime["rows"] if r["ident"] == "EBCI")
            assert any(e["excluded"] for e in ebci["events"])
            assert ebci["first"]["date"] > min(e["date"] for e in ebci["events"])
            homes = {
                ac.registration: ac.home_airfield.ident
                for ac in Aircraft.query.filter(Aircraft.home_airfield_id.isnot(None))
            }
            assert homes == {
                "OO-PNH": "EBCI",
                "OO-ABC": "EBAW",
                "OO-GRN": "EBNM",
                "OO-TCH": "EBST",
            }
            profile = PilotProfile.query.filter_by(user_id=uid).one()
            assert profile.home_airfield.ident == "EBCI"
            ids = [p.id for p in passports.values()]
            lifetime_id = passports["Belgian airfields"].id
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        assert client.get("/passports/").status_code == 200
        for pid in ids:
            assert client.get(f"/passports/{pid}/").status_code == 200
            assert client.get(f"/passports/{pid}/where-next").status_code == 200
        assert client.get(f"/passports/{lifetime_id}/?frequency=1").status_code == 200


def test_passport_booklet_year_looks_back_60_days():
    """Early in January the seeded booklet is last year's edition (which
    already holds visits); from March on it is the current year's."""
    from datetime import date

    from _seed_helpers import (  # pyright: ignore[reportMissingImports]
        passport_booklet_year,
    )

    assert passport_booklet_year(date(2027, 1, 5)) == 2026
    assert passport_booklet_year(date(2027, 3, 1)) == 2026
    assert passport_booklet_year(date(2027, 3, 2)) == 2027
    assert passport_booklet_year(date(2026, 11, 17)) == 2026
