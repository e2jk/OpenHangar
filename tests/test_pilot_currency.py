"""Tests for Phase 18: Pilot Currency & Legality Checks."""

import bcrypt  # pyright: ignore[reportMissingImports]
from datetime import date, timedelta
from types import SimpleNamespace

from pilots.currency import (  # pyright: ignore[reportMissingImports]
    STATUS_EXPIRED,
    STATUS_OK,
    STATUS_UNKNOWN,
    STATUS_WARNING,
    currency_summary,
    medical_status,
    night_currency,
    passenger_currency,
    per_type_currency,
    sep_status,
)
from models import (  # pyright: ignore[reportMissingImports]
    PilotLogbookEntry,
    PilotProfile,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

TODAY = date.today()


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
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
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
        entries = [
            _entry(landings_day=1, days_ago=10),
            _entry(landings_day=1, days_ago=20),
            _entry(landings_day=1, days_ago=30),
        ]
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
        entries = [
            _entry(landings_day=1, days_ago=5),
            _entry(landings_day=1, days_ago=15),
            _entry(landings_day=1, days_ago=30),
        ]
        result = passenger_currency(entries, TODAY)
        assert result["expires_on"] == TODAY - timedelta(days=30) + timedelta(days=90)
        assert result["days_left"] == 60

    def test_warning_when_expires_within_30_days(self):
        # anchor 65 days ago → expires in 25 days → warning
        entries = [
            _entry(landings_day=1, days_ago=60),
            _entry(landings_day=1, days_ago=63),
            _entry(landings_day=1, days_ago=65),
        ]
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
        # 2 night landings ≥ 1 required → now STATUS_OK (rule: 1 night landing)
        entries = [_entry(landings_night=2, days_ago=10)]
        result = night_currency(entries, TODAY)
        assert result["status"] == STATUS_OK

    def test_expired_zero_night_landings(self):
        # No night landings at all → expired
        entries = [_entry(landings_day=5, days_ago=10)]
        result = night_currency(entries, TODAY)
        assert result["status"] == STATUS_UNKNOWN

    def test_one_night_landing_is_sufficient(self):
        entries = [_entry(landings_night=1, days_ago=10)]
        result = night_currency(entries, TODAY)
        assert result["status"] == STATUS_OK
        assert result["shortfall"] == 0

    def test_day_landings_do_not_count_for_night(self):
        entries = [_entry(landings_day=5, days_ago=10)]
        result = night_currency(entries, TODAY)
        assert result["status"] == STATUS_UNKNOWN

    def test_unknown_no_night_entries(self):
        result = night_currency([], TODAY)
        assert result["status"] == STATUS_UNKNOWN

    def test_warning_when_expires_within_30_days(self):
        entries = [
            _entry(landings_night=1, days_ago=62),
            _entry(landings_night=1, days_ago=65),
            _entry(landings_night=1, days_ago=68),
        ]
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
        entries = [
            _entry(landings_day=3, days_ago=10),
            _entry(landings_night=3, days_ago=10),
        ]
        summary = currency_summary(
            _profile(medical_days=200, sep_days=200), entries, TODAY
        )
        assert summary["overall"] == STATUS_OK

    def test_overall_warning_when_one_warns(self):
        entries = [
            _entry(landings_day=3, days_ago=10),
            _entry(landings_night=3, days_ago=10),
        ]
        summary = currency_summary(
            _profile(medical_days=45, sep_days=200), entries, TODAY
        )
        assert summary["overall"] == STATUS_WARNING

    def test_overall_expired_when_one_expired(self):
        entries = [_entry(landings_day=3, days_ago=10)]
        summary = currency_summary(
            _profile(medical_days=-1, sep_days=200), entries, TODAY
        )
        assert summary["overall"] == STATUS_EXPIRED

    def test_summary_contains_all_keys(self):
        summary = currency_summary(_profile(), [], TODAY)
        assert set(summary.keys()) == {
            "passenger",
            "night",
            "medical",
            "sep",
            "per_type",
            "overall",
        }

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
        summary = currency_summary(
            _profile(medical_days=-1, sep_days=200), entries, TODAY
        )
        assert summary["overall"] == STATUS_EXPIRED


# ── Dashboard integration ─────────────────────────────────────────────────────


class TestDashboardCurrencyIntegration:
    def test_dashboard_shows_currency_card_with_profile(self, app, client):
        uid = _create_user(app)
        with app.app_context():
            db.session.add(
                PilotProfile(
                    user_id=uid,
                    license_number="BE.PPL.TEST",
                    medical_expiry=TODAY + timedelta(days=45),
                    sep_expiry=TODAY + timedelta(days=200),
                )
            )
            db.session.add(
                PilotLogbookEntry(
                    pilot_user_id=uid,
                    date=TODAY - timedelta(days=10),
                    single_pilot_se=1.5,
                    function_pic=1.5,
                    landings_day=3,
                )
            )
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
            db.session.add(
                PilotProfile(
                    user_id=uid,
                    medical_expiry=TODAY + timedelta(days=200),
                    sep_expiry=TODAY + timedelta(days=200),
                )
            )
            db.session.commit()
        _login(app, client, "nocurrency@example.com")
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Pilot Currency" in resp.data
        assert b"No data" in resp.data

    def test_dashboard_shows_current_badge_when_fully_current(self, app, client):
        uid = _create_user(app, "fullcurrent@example.com")
        with app.app_context():
            db.session.add(
                PilotProfile(
                    user_id=uid,
                    medical_expiry=TODAY + timedelta(days=200),
                    sep_expiry=TODAY + timedelta(days=200),
                )
            )
            for days_ago in (5, 15, 25):
                db.session.add(
                    PilotLogbookEntry(
                        pilot_user_id=uid,
                        date=TODAY - timedelta(days=days_ago),
                        single_pilot_se=1.0,
                        function_pic=1.0,
                        landings_day=1,
                        landings_night=1,
                    )
                )
            db.session.commit()
        _login(app, client, "fullcurrent@example.com")
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Valid" in resp.data


# ── per_type_currency ─────────────────────────────────────────────────────────


def _typed_entry(
    icao, days_ago=0, landings_day=0, landings_night=0, aircraft_type=None
):
    """Duck-type PilotLogbookEntry with ICAO and landing fields."""
    return SimpleNamespace(
        date=TODAY - timedelta(days=days_ago),
        id=days_ago,
        aircraft_type_icao=icao,
        aircraft_type=aircraft_type or icao,
        landings_day=landings_day or None,
        landings_night=landings_night or None,
    )


class TestPerTypeCurrency:
    def test_empty_entries(self):
        result = per_type_currency([], TODAY)
        assert result["by_type"] == {}
        assert result["unresolved_count"] == 0

    def test_single_type_current(self):
        entries = [
            _typed_entry("C172", days_ago=d, landings_day=1) for d in (5, 15, 25)
        ]
        result = per_type_currency(entries, TODAY)
        assert "C172" in result["by_type"]
        assert result["by_type"]["C172"]["passenger"]["status"] == STATUS_OK
        assert result["by_type"]["C172"]["passenger"]["count"] == 3
        assert result["unresolved_count"] == 0

    def test_two_types_kept_separate(self):
        entries = [
            _typed_entry("C172", days_ago=d, landings_day=1) for d in (5, 15, 25)
        ] + [_typed_entry("P28A", days_ago=d, landings_day=1) for d in (10, 20, 30)]
        result = per_type_currency(entries, TODAY)
        assert set(result["by_type"]) == {"C172", "P28A"}
        assert result["by_type"]["C172"]["passenger"]["count"] == 3
        assert result["by_type"]["P28A"]["passenger"]["count"] == 3

    def test_variants_share_icao_bucket(self):
        """PA-28-161 Warrior II and PA-28-181 Archer III both map to P28A."""
        entries = [
            _typed_entry(
                "P28A", days_ago=5, landings_day=2, aircraft_type="PA-28-161 Warrior II"
            ),
            _typed_entry(
                "P28A",
                days_ago=10,
                landings_day=1,
                aircraft_type="PA-28-181 Archer III",
            ),
        ]
        result = per_type_currency(entries, TODAY)
        assert set(result["by_type"]) == {"P28A"}
        assert result["by_type"]["P28A"]["passenger"]["count"] == 3

    def test_no_icao_resolved_via_aircraft_type(self):
        """Entries without aircraft_type_icao fall back to resolve_aircraft_type_icao."""
        entry = SimpleNamespace(
            date=TODAY - timedelta(days=5),
            id=1,
            aircraft_type_icao=None,
            aircraft_type="C172",  # resolvable: exact ICAO code
            landings_day=3,
            landings_night=None,
        )
        result = per_type_currency([entry], TODAY)
        assert "C172" in result["by_type"]
        assert result["unresolved_count"] == 0

    def test_unresolvable_type_counted(self):
        entry = SimpleNamespace(
            date=TODAY - timedelta(days=5),
            id=1,
            aircraft_type_icao=None,
            aircraft_type="Jodel DR-1050 Ambassadeur",  # not in ICAO data
            landings_day=2,
            landings_night=None,
        )
        result = per_type_currency([entry], TODAY)
        assert result["unresolved_count"] == 1
        assert result["by_type"] == {}

    def test_night_currency_tracked_separately(self):
        entries = [
            _typed_entry("C172", days_ago=5, landings_day=3, landings_night=1),
        ]
        result = per_type_currency(entries, TODAY)
        tc = result["by_type"]["C172"]
        assert tc["passenger"]["status"] == STATUS_OK  # 3 landings (day) ≥ 3
        assert tc["night"]["status"] == STATUS_OK  # 1 night landing ≥ 1
        assert tc["night"]["count"] == 1

    def test_expired_day_sets_type_status_expired(self):
        entries = [_typed_entry("P44A", days_ago=5, landings_day=1)]  # only 1, need 3
        result = per_type_currency(entries, TODAY)
        assert result["by_type"]["P44A"]["status"] == STATUS_EXPIRED

    def test_unknown_when_no_landings(self):
        entry = _typed_entry("C172", days_ago=5, landings_day=0, landings_night=0)
        result = per_type_currency([entry], TODAY)
        assert result["by_type"]["C172"]["passenger"]["status"] == STATUS_UNKNOWN
        assert result["by_type"]["C172"]["night"]["status"] == STATUS_UNKNOWN

    def test_currency_summary_includes_per_type(self):
        entries = [
            _typed_entry("C172", days_ago=d, landings_day=1) for d in (5, 15, 25)
        ]
        profile = _profile()
        summary = currency_summary(profile, entries, TODAY)
        assert "per_type" in summary
        assert "C172" in summary["per_type"]["by_type"]

    def test_sorted_by_icao_code(self):
        entries = [
            _typed_entry("P28A", days_ago=d, landings_day=1) for d in (5, 15, 25)
        ] + [_typed_entry("C172", days_ago=d, landings_day=1) for d in (5, 15, 25)]
        result = per_type_currency(entries, TODAY)
        keys = list(result["by_type"])
        assert keys == sorted(keys)

    def test_warning_status_when_currency_expiring_soon(self):
        # Anchor 65 days ago → expires in 25 days → WARNING, not EXPIRED
        entries = [
            _typed_entry("C172", days_ago=d, landings_day=1) for d in (60, 63, 65)
        ]
        result = per_type_currency(entries, TODAY)
        assert result["by_type"]["C172"]["passenger"]["status"] == STATUS_WARNING
        assert result["by_type"]["C172"]["status"] == STATUS_WARNING

    def test_default_today_no_arg(self):
        result = per_type_currency([])
        assert result["by_type"] == {}
        assert result["unresolved_count"] == 0
