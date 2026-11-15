"""Finding from the signed-in ZAP scan (2026-11-14), "Integer Overflow Error"
(30003): a number far too large for any id, year, page or period made pages
fail with a server error instead of being ignored or answering "not found".

PostgreSQL rejects an id beyond its INTEGER range ("integer out of range")
rather than finding nothing, and the test database (SQLite) rejects one
beyond 64 bits; dates and offsets have their own limits. The same goes
for a decimal beyond its NUMERIC(precision, scale) column ("numeric field
overflow"), which SQLite stores anyway: models.fits_column refuses both
before the flush, so the outcome is the same on both databases.
"""

import re
from datetime import date
from decimal import Decimal

import pytest  # pyright: ignore[reportMissingImports]
from flights.form_parsing import (  # pyright: ignore[reportMissingImports]
    parse_flight_fields,
)
from models import (  # pyright: ignore[reportMissingImports]
    Expense,
    Flight,
    NumberTooLarge,
    db,
    fits_column,
)
from pilots.form_parsing import (  # pyright: ignore[reportMissingImports]
    parse_pilot_fields,
)
from sqlalchemy import (  # pyright: ignore[reportMissingImports]
    BigInteger,
    Column,
    Float,
    Integer,
    Numeric,
    SmallInteger,
    String,
)
from utils import DB_INT_MAX, int_in  # pyright: ignore[reportMissingImports]
from werkzeug.exceptions import NotFound

HUGE = "5" * 44
NUMBER_CONVERTERS = ("DbIdConverter", "AircraftRefConverter")


def _numbered_get_paths(app):
    """Every GET route with a number in its path, each number set to HUGE."""
    for rule in app.url_map.iter_rules():
        converters = rule._converters
        if "GET" not in rule.methods or not any(
            type(c).__name__ in NUMBER_CONVERTERS for c in converters.values()
        ):
            continue
        path = rule.rule
        for name, conv in converters.items():
            value = HUGE if type(conv).__name__ in NUMBER_CONVERTERS else "x"
            path = re.sub(r"<[^>]*\b" + name + ">", value, path)
        yield path


def test_every_numbered_page_survives_an_oversized_number(owner_env, app):
    paths = list(_numbered_get_paths(app))
    assert len(paths) > 50  # the sweep really covers the app's id routes
    failures = []
    for path in paths:
        status = owner_env.client.get(path).status_code
        if status >= 500:
            failures.append(f"{path}: {status}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize(
    "path, found",
    [
        (f"/flights/{DB_INT_MAX}/edit", True),
        (f"/flights/{DB_INT_MAX + 1}/edit", False),
        (f"/aircraft/{DB_INT_MAX}/flights", True),
        (f"/aircraft/{DB_INT_MAX + 1}/flights", False),
    ],
)
def test_ids_beyond_the_database_range_match_no_route(app, path, found):
    adapter = app.url_map.bind("localhost")
    with app.app_context():
        if found:
            adapter.match(path)
        else:
            with pytest.raises(NotFound):
                adapter.match(path)


@pytest.mark.parametrize(
    "page, param",
    [
        ("/", "cal_year"),
        ("/", "cal_month"),
        ("/pilot/logbook", "page"),
        ("/aircraft/{ac}/flights", "page"),
        ("/aircraft/{ac}/documents", "page"),
        ("/aircraft/{ac}/reservations/", "year"),
        ("/aircraft/{ac}/reservations/", "month"),
        ("/aircraft/{ac}/expenses", "period"),
        ("/aircraft/{ac}/costs", "period"),
        ("/aircraft/{ac}/refuels", "period"),
        ("/aircraft/{ac}/reports/utilization", "period"),
        ("/my/account", "period"),
        ("/pilot/documents/upload", "replace"),
        ("/aircraft/{ac}/documents/upload", "replace"),
        ("/flights/new", "aircraft_id"),
        ("/flights/new", "gps_review_return"),
        ("/flights/new", "gps_seg"),
    ],
)
def test_oversized_query_numbers_are_ignored(owner_env, page, param):
    url = page.format(ac=owner_env.aircraft_id)
    resp = owner_env.client.get(url, query_string={param: HUGE})
    assert resp.status_code < 500, (url, param)


@pytest.mark.parametrize("page", ["/", "/aircraft/{ac}/reservations/"])
@pytest.mark.parametrize("year, month", [("2", "0"), ("9998", "13")])
def test_calendars_reach_the_ends_of_their_range(owner_env, page, year, month):
    url = page.format(ac=owner_env.aircraft_id)
    keys = ("cal_year", "cal_month") if page == "/" else ("year", "month")
    resp = owner_env.client.get(url, query_string=dict(zip(keys, (year, month))))
    assert resp.status_code == 200, (url, year, month)


def test_int_in_accepts_its_bounds_and_nothing_beyond():
    parse = int_in(1, 3)
    assert [parse("1"), parse("3")] == [1, 3]
    for bad in ("0", "4", HUGE, "", "x", "1.5"):
        with pytest.raises(ValueError):
            parse(bad)


TOO_LARGE = b"A number entered is far too large, so nothing was saved."


@pytest.mark.parametrize(
    "col_type, value, fits",
    [
        (Numeric(4, 1), 999.9, True),
        (Numeric(4, 1), -999.9, True),
        (Numeric(4, 1), 999.94, True),
        (Numeric(4, 1), 999.95, False),  # the database rounds it to 1000.0
        (Numeric(4, 1), 1000, False),
        (Numeric(4, 1), Decimal("1e50"), False),  # too big to round
        (Numeric(4, 1), float("inf"), False),
        (Numeric(4, 1), float("nan"), False),
        (Numeric(10, 2), 99_999_999.99, True),
        (Numeric(10, 2), 1e12, False),
        (Numeric(3), 999, True),  # no scale
        (Numeric(3), 1000, False),
        (Numeric(), 10**40, True),  # unbounded
        (Float(), 1e300, True),
        (Integer(), DB_INT_MAX, True),
        (Integer(), -DB_INT_MAX - 1, True),
        (Integer(), DB_INT_MAX + 1, False),
        (SmallInteger(), 2**15, False),
        (BigInteger(), 2**40, True),
        (BigInteger(), 2**63, False),
        (Integer(), True, True),  # a bool is not a number here
        (Numeric(4, 1), "12345", True),  # left to the column type
        (String(5), 10**9, True),
    ],
)
def test_fits_column(col_type, value, fits):
    assert fits_column(Column("x", col_type), value) is fits


def test_saving_a_number_too_large_is_refused(owner_env, app):
    with app.app_context():
        flight = Flight(date=date(2026, 1, 1), night_time=1000)
        db.session.add(flight)
        with pytest.raises(NumberTooLarge, match="flights.night_time"):
            db.session.flush()
        db.session.rollback()


def test_a_form_without_its_own_check_says_why_nothing_was_saved(owner_env, app):
    form_page = f"/aircraft/{owner_env.aircraft_id}/expenses/add"
    resp = owner_env.client.post(
        form_page,
        data={
            "date": "2026-01-15",
            "expense_type": "other",
            "expense_category": "fixed",
            "amount": "1" + "0" * 12,  # NUMERIC(10, 2): at most 99,999,999.99
            "currency": "EUR",
        },
        headers={"Referer": f"http://localhost{form_page}"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == form_page
    assert TOO_LARGE in owner_env.client.get(form_page).data
    with app.app_context():
        assert Expense.query.count() == 0


def test_pilot_logbook_hours_fields_say_which_number_is_too_large():
    _values, errors = parse_pilot_fields(
        {"date": "2026-01-01", "night_time": "999.9", "instrument_time": "1000"}
    )
    assert errors == ["Instrument time: number too large."]


def test_pilot_logbook_hours_fields_share_one_column_type():
    # pilots.form_parsing bounds them all by night_time's column type.
    columns = Flight.__table__.c
    hours = [
        "night_time",
        "instrument_time",
        "single_pilot_se",
        "single_pilot_me",
        "multi_pilot",
        "function_pic",
        "function_copilot",
        "function_dual",
        "function_instructor",
        "fstd_duration",
    ]
    assert {repr(columns[name].type) for name in hours} == {
        "Numeric(precision=4, scale=1)"
    }


def test_flight_form_refuses_a_number_too_large_for_its_column():
    form = {"date": "2026-01-01", "departure_icao": "EBAW", "arrival_icao": "EBKT"}
    _values, errors = parse_flight_fields(form, None)
    assert errors == []
    _values, errors = parse_flight_fields({**form, "oil_added_after_l": "100"}, None)
    assert errors == [TOO_LARGE.decode() + " Please check the values and try again."]
