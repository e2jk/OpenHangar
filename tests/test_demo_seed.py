"""Smoke test for demo_seed.seed() — runs the real seeding logic end to
end so schema/model drift (e.g. a seed helper still referencing a
relationship a refactor removed) is caught locally by
run-tests-with-coverage.sh, instead of only surfacing in CI's
"Validate Docker image" smoke-test job. _seed_helpers.py/demo_seed.py
are excluded from the coverage requirement (.coveragerc), so nothing
else in the suite calls this function to completion.
"""

from demo_seed import seed  # pyright: ignore[reportMissingImports]
from models import DemoSlot  # pyright: ignore[reportMissingImports]


class TestDemoSeedEndToEnd:
    def test_seed_runs_without_error(self, app, monkeypatch):
        monkeypatch.setenv("OPENHANGAR_ENV", "demo")
        # Full seed() creates OPENHANGAR_DEMO_SLOT_COUNT slots (default 20),
        # each with its own fleet + sole-pilot + sole-operator + shared-
        # ownership sub-tenants — one slot already exercises every code
        # path (including the sole-operator fleet seeding that broke), so
        # keep this fast.
        monkeypatch.setenv("OPENHANGAR_DEMO_SLOT_COUNT", "1")
        with app.app_context():
            seed()
            assert DemoSlot.query.count() == 1

    def test_seed_can_be_run_twice(self, app, monkeypatch):
        """Re-running seed() wipes and recreates every slot, including the
        shared-ownership sub-tenant (3 co-owner users, tracked by tenant id
        rather than a single user id column) — exercises that cleanup path,
        which a single run never reaches."""
        monkeypatch.setenv("OPENHANGAR_ENV", "demo")
        monkeypatch.setenv("OPENHANGAR_DEMO_SLOT_COUNT", "1")
        with app.app_context():
            seed()
            seed()
            assert DemoSlot.query.count() == 1
