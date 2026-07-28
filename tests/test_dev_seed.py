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
