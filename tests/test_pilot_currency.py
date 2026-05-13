"""Tests for Phase 18: Pilot Currency & Legality Checks."""
import bcrypt  # pyright: ignore[reportMissingImports]
from datetime import date, timedelta
from types import SimpleNamespace

from pilots.currency import (  # pyright: ignore[reportMissingImports]
    STATUS_EXPIRED, STATUS_OK, STATUS_UNKNOWN, STATUS_WARNING,
    currency_summary, medical_status, night_currency, passenger_currency, sep_status,
)
from models import (  # pyright: ignore[reportMissingImports]
    PilotLogbookEntry, PilotProfile, Role, Tenant, TenantUser, User, db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

TODAY = date(2026, 5, 9)


def _entry(landings_day=0, landings_night=0, days_ago=0):
    """Create a SimpleNamespace that duck-types PilotLogbookEntry."""
    return SimpleNamespace(
        date=TODAY - timedelta(days=days_ago),
        id=days_ago,
        landings_day=landings_day or None,
        landings_night=landings_night or None,
    )


def _profile(medical_days=200, sep_days=200):
    return SimpleNamespace(
        medical_expiry=TODAY + timedelta(days=medical_days),
        sep_expiry=TODAY + timedelta(days=sep_days),
    )


def _create_user(app, email="curr@example.com"):
    with app.app_context():
        tenant = Tenant(name="Curr Test")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER))
        db.session.commit()
        return user.id


def _login(app, client, email="curr@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


# ── Passenger currency ────────────────────────────────────────────────────────

class TestPassengerCurrency:
    def test_ok_exactly_three_landings(self):
        entries = [_entry(landings_day=1, days_ago=10),
                   _entry(landings_day=1, days_ago=20),
                   _entry(landings_day=1, days_ago=30)]
        result = passenger_currency(entries, TODAY)
        assert result["status"] == STATUS_OK
        assert result["count"] == 3
        assert result["shortfall"] == 0

    def test_ok_more_than_three(self):
        entries = [_entry(landings_day=3, days_ago=5)]
        result = passenger_currency(entries, TODAY)
        assert result["status"] == STATUS_OK
        assert result["count"] == 3

    def test_expired_fewer_than_three(self):
        entries = [_entry(landings_day=2, days_ago=10)]
        result = passenger_currency(entries, TODAY)
        assert result["status"] == STATUS_EXPIRED
        assert result["count"] == 2
        assert result["shortfall"] == 1

    def test_expired_zero_landings_in_window(self):
        entries = [_entry(landings_day=5, days_ago=95)]  # outside window
        result = passenger_currency(entries, TODAY)
        assert result["status"] == STATUS_UNKNOWN
        assert result["count"] == 0
        assert result["shortfall"] == 3

    def test_unknown_no_entries(self):
        result = passenger_currency([], TODAY)
        assert result["status"] == STATUS_UNKNOWN

    def test_boundary_exactly_90_days_ago(self):
        # Anchor 90 days ago → expires today (0 days left) → warning, but still current
        entries = [_entry(landings_day=3, days_ago=90)]
        result = passenger_currency(entries, TODAY)
        assert result["status"] == STATUS_WARNING
        assert result["days_left"] == 0

    def test_boundary_91_days_ago_excluded(self):
        entries = [_entry(landings_day=3, days_ago=91)]
        result = passenger_currency(entries, TODAY)
        assert result["status"] == STATUS_UNKNOWN

    def test_expires_on_computed_correctly(self):
        # anchor entry 30 days ago → expires 90-30=60 days from now
        entries = [_entry(landings_day=1, days_ago=5),
                   _entry(landings_day=1, days_ago=15),
                   _entry(landings_day=1, days_ago=30)]
        result = passenger_currency(entries, TODAY)
        assert result["expires_on"] == TODAY - timedelta(days=30) + timedelta(days=90)
        assert result["days_left"] == 60

    def test_warning_when_expires_within_30_days(self):
        # anchor 65 days ago → expires in 25 days → warning
        entries = [_entry(landings_day=1, days_ago=60),
                   _entry(landings_day=1, days_ago=63),
                   _entry(landings_day=1, days_ago=65)]
        result = passenger_currency(entries, TODAY)
        assert result["status"] == STATUS_WARNING
        assert result["days_left"] == 25

    def test_single_entry_multiple_landings_anchor(self):
        # 1 entry with 5 landings, 70 days ago → anchor=70 days ago, expires in 20 days → warning
        entries = [_entry(landings_day=5, days_ago=70)]
        result = passenger_currency(entries, TODAY)
        assert result["status"] == STATUS_WARNING
        assert result["expires_on"] == TODAY - timedelta(days=70) + timedelta(days=90)


# ── Night currency ────────────────────────────────────────────────────────────

class TestNightCurrency:
    def test_ok_three_night_landings(self):
        entries = [_entry(landings_night=3, days_ago=20)]
        result = night_currency(entries, TODAY)
        assert result["status"] == STATUS_OK
        assert result["count"] == 3

    def test_expired_two_night_landings(self):
        entries = [_entry(landings_night=2, days_ago=10)]
        result = night_currency(entries, TODAY)
        assert result["status"] == STATUS_EXPIRED
        assert result["shortfall"] == 1

    def test_day_landings_do_not_count_for_night(self):
        entries = [_entry(landings_day=5, days_ago=10)]
        result = night_currency(entries, TODAY)
        assert result["status"] == STATUS_UNKNOWN

    def test_unknown_no_night_entries(self):
        result = night_currency([], TODAY)
        assert result["status"] == STATUS_UNKNOWN

    def test_warning_when_expires_within_30_days(self):
        entries = [_entry(landings_night=1, days_ago=62),
                   _entry(landings_night=1, days_ago=65),
                   _entry(landings_night=1, days_ago=68)]
        result = night_currency(entries, TODAY)
        assert result["status"] == STATUS_WARNING


# ── Medical status ────────────────────────────────────────────────────────────

class TestMedicalStatus:
    def test_ok_when_more_than_90_days(self):
        result = medical_status(_profile(medical_days=100), TODAY)
        assert result["status"] == STATUS_OK
        assert result["days_remaining"] == 100

    def test_warning_when_exactly_90_days(self):
        result = medical_status(_profile(medical_days=90), TODAY)
        assert result["status"] == STATUS_WARNING

    def test_warning_when_less_than_90_days(self):
        result = medical_status(_profile(medical_days=45), TODAY)
        assert result["status"] == STATUS_WARNING
        assert result["days_remaining"] == 45

    def test_expired_when_past(self):
        result = medical_status(_profile(medical_days=-1), TODAY)
        assert result["status"] == STATUS_EXPIRED
        assert result["days_remaining"] < 0

    def test_unknown_when_no_expiry_set(self):
        p = SimpleNamespace(medical_expiry=None, sep_expiry=None)
        result = medical_status(p, TODAY)
        assert result["status"] == STATUS_UNKNOWN

    def test_expiry_date_returned(self):
        expiry = TODAY + timedelta(days=150)
        p = SimpleNamespace(medical_expiry=expiry, sep_expiry=None)
        result = medical_status(p, TODAY)
        assert result["expiry"] == expiry


# ── SEP status ────────────────────────────────────────────────────────────────

class TestSepStatus:
    def test_ok_when_more_than_90_days(self):
        result = sep_status(_profile(sep_days=120), TODAY)
        assert result["status"] == STATUS_OK

    def test_warning_within_90_days(self):
        result = sep_status(_profile(sep_days=30), TODAY)
        assert result["status"] == STATUS_WARNING

    def test_expired(self):
        result = sep_status(_profile(sep_days=-5), TODAY)
        assert result["status"] == STATUS_EXPIRED

    def test_unknown_when_none(self):
        p = SimpleNamespace(medical_expiry=None, sep_expiry=None)
        result = sep_status(p, TODAY)
        assert result["status"] == STATUS_UNKNOWN


# ── currency_summary ──────────────────────────────────────────────────────────

class TestCurrencySummary:
    def test_returns_none_when_no_profile(self):
        assert currency_summary(None, [], TODAY) is None

    def test_overall_ok_when_all_ok(self):
        entries = [_entry(landings_day=3, days_ago=10),
                   _entry(landings_night=3, days_ago=10)]
        summary = currency_summary(_profile(medical_days=200, sep_days=200), entries, TODAY)
        assert summary["overall"] == STATUS_OK

    def test_overall_warning_when_one_warns(self):
        entries = [_entry(landings_day=3, days_ago=10),
                   _entry(landings_night=3, days_ago=10)]
        summary = currency_summary(_profile(medical_days=45, sep_days=200), entries, TODAY)
        assert summary["overall"] == STATUS_WARNING

    def test_overall_expired_when_one_expired(self):
        entries = [_entry(landings_day=3, days_ago=10)]
        summary = currency_summary(_profile(medical_days=-1, sep_days=200), entries, TODAY)
        assert summary["overall"] == STATUS_EXPIRED

    def test_summary_contains_all_keys(self):
        summary = currency_summary(_profile(), [], TODAY)
        assert set(summary.keys()) == {"passenger", "night", "medical", "sep", "overall"}

    def test_default_today_fallback(self):
        # Calls without explicit today exercise the _date.today() branches (lines 66, 73, 79, 87, 100)
        assert passenger_currency([]) is not None
        assert night_currency([]) is not None
        p = _profile()
        assert medical_status(p) is not None
        assert sep_status(p) is not None
        assert currency_summary(p, []) is not None

    def test_unknown_treated_as_warning_in_overall(self):
        # No entries → passenger & night unknown → overall at least warning
        summary = currency_summary(_profile(medical_days=200, sep_days=200), [], TODAY)
        assert summary["overall"] in (STATUS_WARNING, STATUS_EXPIRED)

    def test_expired_takes_precedence_over_warning(self):
        entries = [_entry(landings_day=3, days_ago=65)]  # warning (25 days left)
        summary = currency_summary(_profile(medical_days=-1, sep_days=200), entries, TODAY)
        assert summary["overall"] == STATUS_EXPIRED


# ── Dashboard integration ─────────────────────────────────────────────────────

class TestDashboardCurrencyIntegration:
    def test_dashboard_shows_currency_card_with_profile(self, app, client):
        uid = _create_user(app)
        with app.app_context():
            db.session.add(PilotProfile(
                user_id=uid,
                license_number="BE.PPL.TEST",
                medical_expiry=TODAY + timedelta(days=45),
                sep_expiry=TODAY + timedelta(days=200),
            ))
            db.session.add(PilotLogbookEntry(
                pilot_user_id=uid,
                date=TODAY - timedelta(days=10),
                single_pilot_se=1.5,
                function_pic=1.5,
                landings_day=3,
            ))
            db.session.commit()
        _login(app, client)
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Pilot Currency" in resp.data
        assert b"Expiring soon" in resp.data  # medical < 90 days

    def test_dashboard_hides_currency_card_without_profile(self, app, client):
        _create_user(app, "noprofile@example.com")
        _login(app, client, "noprofile@example.com")
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Pilot Currency" not in resp.data

    def test_dashboard_shows_not_current_when_no_landings(self, app, client):
        uid = _create_user(app, "nocurrency@example.com")
        with app.app_context():
            db.session.add(PilotProfile(
                user_id=uid,
                medical_expiry=TODAY + timedelta(days=200),
                sep_expiry=TODAY + timedelta(days=200),
            ))
            db.session.commit()
        _login(app, client, "nocurrency@example.com")
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Pilot Currency" in resp.data
        assert b"No data" in resp.data

    def test_dashboard_shows_current_badge_when_fully_current(self, app, client):
        uid = _create_user(app, "fullcurrent@example.com")
        with app.app_context():
            db.session.add(PilotProfile(
                user_id=uid,
                medical_expiry=TODAY + timedelta(days=200),
                sep_expiry=TODAY + timedelta(days=200),
            ))
            for days_ago in (5, 15, 25):
                db.session.add(PilotLogbookEntry(
                    pilot_user_id=uid,
                    date=TODAY - timedelta(days=days_ago),
                    single_pilot_se=1.0,
                    function_pic=1.0,
                    landings_day=1,
                    landings_night=1,
                ))
            db.session.commit()
        _login(app, client, "fullcurrent@example.com")
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Current" in resp.data
