"""Tests for Phase 18: Pilot Currency & Legality Checks."""

from datetime import date, timedelta
from types import SimpleNamespace

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Flight,
    PilotProfile,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from pilots.currency import (  # pyright: ignore[reportMissingImports]
    STATUS_EXPIRED,
    STATUS_OK,
    STATUS_UNKNOWN,
    STATUS_WARNING,
    currency_summary,
    medical_status,
    night_currency,
    passenger_currency,
    per_class_currency,
    recent_aircraft,
    sep_status,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

TODAY = date.today()


def _entry(landings_day=0, landings_night=0, days_ago=0):
    """Create a SimpleNamespace that duck-types Flight."""
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
            password_hash=_pw_hash.hash("pw"),
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
            "per_class",
            "recent_aircraft",
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
                Flight(
                    pic_user_id=uid,
                    date=TODAY - timedelta(days=10),
                    single_pilot_se=1.5,
                    function_pic=1.5,
                    landings_day=3,
                    other_aircraft_type_icao="C152",
                )
            )
            db.session.commit()
        _login(app, client)
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Pilot Currency" in resp.data
        assert b"Expiring soon" in resp.data  # medical < 90 days
        # The recency strip names the class the C152 counts under.
        assert b"By class:" in resp.data and b"SEP (land)" in resp.data

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
                    Flight(
                        pic_user_id=uid,
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
    """Duck-type a Flight row with ICAO and landing fields."""
    return SimpleNamespace(
        date=TODAY - timedelta(days=days_ago),
        id=days_ago,
        display_aircraft_type_icao=icao,
        display_aircraft_type=aircraft_type or icao,
        landings_day=landings_day or None,
        landings_night=landings_night or None,
    )


def _row(result, key):
    return next(r for r in result["by_class"] if r["key"] == key)


class TestPerClassCurrency:
    """FCL.060(b): "in an aircraft of the same type or class" — piston
    aeroplanes by class, other aircraft by type."""

    def test_empty_entries(self):
        result = per_class_currency([], TODAY)
        assert result == {"by_class": [], "unresolved_count": 0}

    def test_default_today_no_arg(self):
        assert per_class_currency([])["by_class"] == []

    def test_sep_types_share_the_class(self):
        """A DR40, a C152 and a PA-28 are all SEP (land): their landings add
        up, and each type's share is listed."""
        entries = [
            _typed_entry("DR40", days_ago=5, landings_day=1),
            _typed_entry("C152", days_ago=15, landings_day=1),
            _typed_entry("P28A", days_ago=25, landings_day=1, landings_night=1),
        ]
        result = per_class_currency(entries, TODAY)
        assert [r["key"] for r in result["by_class"]] == ["sep_land"]
        row = _row(result, "sep_land")
        assert row["class"] == "sep_land" and row["type"] is None
        assert row["passenger"]["count"] == 4
        assert row["passenger"]["status"] == STATUS_OK
        assert row["night"]["status"] == STATUS_OK
        assert row["types"] == {"C152": 1, "DR40": 1, "P28A": 2}
        assert row["last_flown"] == TODAY - timedelta(days=5)

    def test_classes_kept_apart_and_other_aircraft_by_type(self):
        entries = [
            _typed_entry("C172", days_ago=d, landings_day=1) for d in (5, 15, 25)
        ] + [
            _typed_entry("PA44", days_ago=10, landings_day=1),
            _typed_entry("C208", days_ago=12, landings_day=3),
        ]
        result = per_class_currency(entries, TODAY)
        assert {r["key"] for r in result["by_class"]} == {
            "sep_land",
            "mep_land",
            "type:C208",
        }
        assert _row(result, "mep_land")["status"] == STATUS_EXPIRED  # 1 of 3
        c208 = _row(result, "type:C208")
        assert c208["class"] is None and c208["type"] == "C208"
        assert c208["passenger"]["status"] == STATUS_OK
        # Current first.
        assert result["by_class"][-1]["key"] == "mep_land"

    def test_sea_classes(self):
        from utils import (  # pyright: ignore[reportMissingImports]
            AIRCRAFT_CLASS_MEP_SEA,
            AIRCRAFT_CLASS_SEP_SEA,
            _load_aircraft_classes,
        )

        sea = {v for v in _load_aircraft_classes().values()}
        assert AIRCRAFT_CLASS_SEP_SEA in sea and AIRCRAFT_CLASS_MEP_SEA in sea

    def test_only_the_last_90_days_count(self):
        entries = [
            _typed_entry("C172", days_ago=d, landings_day=1) for d in (5, 15, 100)
        ]
        row = _row(per_class_currency(entries, TODAY), "sep_land")
        assert row["passenger"]["count"] == 2
        assert row["types"] == {"C172": 2}

    def test_history_adds_classes_not_flown_lately(self):
        entries = [
            _typed_entry("C172", days_ago=d, landings_day=1) for d in (5, 15, 25)
        ]
        history = [
            ("C172", TODAY - timedelta(days=5)),
            ("PA44", date(2019, 5, 1)),
            (None, date(2018, 1, 1)),  # an unresolved type: nothing to show
        ]
        result = per_class_currency(entries, TODAY, history)
        mep = _row(result, "mep_land")
        assert mep["passenger"]["status"] == STATUS_UNKNOWN
        assert mep["last_flown"] == date(2019, 5, 1)
        assert mep["types"] == {}
        assert len(result["by_class"]) == 2

    def test_no_icao_resolved_via_aircraft_type(self):
        """Entries without display_aircraft_type_icao fall back to
        resolve_aircraft_type_icao — e.g. a managed aircraft's make and model."""
        entry = SimpleNamespace(
            date=TODAY - timedelta(days=5),
            id=1,
            display_aircraft_type_icao=None,
            display_aircraft_type="ROBIN DR-401 155CDI",
            landings_day=3,
            landings_night=None,
        )
        result = per_class_currency([entry], TODAY)
        assert _row(result, "sep_land")["types"] == {"DR40": 3}
        assert result["unresolved_count"] == 0

    def test_unresolvable_type_counted(self):
        entry = SimpleNamespace(
            date=TODAY - timedelta(days=5),
            id=1,
            display_aircraft_type_icao=None,
            # Not in the ICAO type data. (Not "Jodel DR-1050 Ambassadeur",
            # used here before: it is — Centre Est DR-1050, DR10 — and is
            # resolved since words are matched one by one.)
            display_aircraft_type="Homebuilt Skylark",
            landings_day=2,
            landings_night=None,
        )
        result = per_class_currency([entry], TODAY)
        assert result["unresolved_count"] == 1
        assert result["by_class"] == []

    def test_simulator_sessions_are_skipped(self):
        """An FSTD session lands on no aircraft: neither counted under its
        device's name nor reported as an unresolved type."""
        entry = SimpleNamespace(
            date=TODAY - timedelta(days=5),
            id=1,
            entry_type="fstd",
            display_aircraft_type_icao=None,
            display_aircraft_type="ALSIM AL-250",
            landings_day=None,
            landings_night=None,
        )
        result = per_class_currency([entry], TODAY)
        assert result == {"by_class": [], "unresolved_count": 0}
        assert recent_aircraft([entry], TODAY) == []

    def test_warning_and_unknown_statuses(self):
        # Anchor 65 days ago → expires in 25 days → WARNING, not EXPIRED
        entries = [
            _typed_entry("C172", days_ago=d, landings_day=1) for d in (60, 63, 65)
        ]
        row = _row(per_class_currency(entries, TODAY), "sep_land")
        assert row["passenger"]["status"] == STATUS_WARNING
        assert row["night"]["status"] == STATUS_UNKNOWN  # no night landing
        assert row["status"] == STATUS_WARNING
        none = [_typed_entry("C172", days_ago=5)]
        row = _row(per_class_currency(none, TODAY), "sep_land")
        assert row["passenger"]["status"] == STATUS_UNKNOWN
        night_only = [_typed_entry("C172", days_ago=5, landings_night=3)]
        assert _row(per_class_currency(night_only, TODAY), "sep_land")["status"] == (
            STATUS_OK
        )
        warn_night = [
            _typed_entry("C172", days_ago=5, landings_day=3),
            _typed_entry("C172", days_ago=70, landings_night=1),
        ]
        assert _row(per_class_currency(warn_night, TODAY), "sep_land")["status"] == (
            STATUS_WARNING
        )

    def test_currency_summary_includes_classes_and_aircraft(self):
        entries = [
            _typed_entry("C172", days_ago=d, landings_day=1) for d in (5, 15, 25)
        ]
        summary = currency_summary(_profile(), entries, TODAY)
        assert [r["key"] for r in summary["per_class"]["by_class"]] == ["sep_land"]
        assert summary["recent_aircraft"][0]["icao"] == "C172"


class TestRecentAircraft:
    def test_per_aircraft_over_six_months(self):
        def flown(reg, days_ago, landings=1, aircraft_type="ROBIN DR-401 155CDI"):
            return SimpleNamespace(
                date=TODAY - timedelta(days=days_ago),
                id=days_ago,
                display_registration=reg,
                display_aircraft_type=aircraft_type,
                display_aircraft_type_icao=None,
                landings_day=landings,
                landings_night=None,
            )

        entries = [
            flown("OO-LKN", 5),
            flown("OO-LKN", 120, landings=2),
            flown("OO-ABC", 30, aircraft_type="C152"),
            flown("OO-OLD", 200),  # more than 6 months ago: left out
        ]
        rows = recent_aircraft(entries, TODAY)
        assert [r["registration"] for r in rows] == ["OO-LKN", "OO-ABC"]
        lkn = rows[0]
        assert lkn["icao"] == "DR40" and lkn["class"] == "sep_land"
        assert lkn["flights"] == 2
        assert lkn["landings_90"] == 1  # the 120-day-old landings aren't recent
        assert lkn["last_flown"] == TODAY - timedelta(days=5)
        assert recent_aircraft([]) == []


class TestRecencyQueries:
    def test_recent_flights_and_flown_history(self, app):
        from models import Aircraft  # pyright: ignore[reportMissingImports]
        from pilots.currency import (  # pyright: ignore[reportMissingImports]
            flown_history,
            recent_flights,
        )

        uid = _create_user(app, "hist@example.com")
        with app.app_context():
            tid = TenantUser.query.filter_by(user_id=uid).one().tenant_id
            ac = Aircraft(
                tenant_id=tid,
                registration="OO-LKN",
                make="ROBIN",
                model="DR-401 155CDI",
            )
            db.session.add(ac)
            db.session.flush()
            db.session.add_all(
                [
                    Flight(aircraft_id=ac.id, pic_user_id=uid, date=TODAY),
                    Flight(
                        aircraft_id=ac.id,
                        pic_user_id=uid,
                        date=TODAY - timedelta(days=400),
                    ),
                    Flight(
                        second_crew_user_id=uid,
                        other_aircraft_type="PA44",
                        date=date(2019, 5, 1),
                    ),
                    Flight(
                        pic_user_id=uid,
                        other_aircraft_type_icao="C152",
                        date=date(2018, 6, 1),
                    ),
                    Flight(
                        pic_user_id=uid,
                        entry_type="fstd",
                        other_aircraft_type="ALSIM AL-250",
                        date=TODAY,
                    ),
                ]
            )
            db.session.commit()
            assert len(recent_flights(uid)) == 2  # the flight and the session
            history = sorted(flown_history(uid), key=lambda h: h[1])
            assert history == [
                ("C152", date(2018, 6, 1)),
                ("PA44", date(2019, 5, 1)),
                ("DR40", TODAY),
            ]


def test_aircraft_classes_without_the_type_data(app):
    from unittest.mock import patch

    from utils import _load_aircraft_classes  # pyright: ignore[reportMissingImports]

    with patch("builtins.open", side_effect=OSError):
        _load_aircraft_classes.cache_clear()
        result = _load_aircraft_classes()
    _load_aircraft_classes.cache_clear()
    assert result == {}
