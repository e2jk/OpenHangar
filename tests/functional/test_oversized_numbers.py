"""Finding from the signed-in ZAP scan (2026-11-14), "Integer Overflow Error"
(30003): a number far too large for any id, year, page or period made pages
fail with a server error instead of being ignored or answering "not found".

PostgreSQL rejects an id beyond its INTEGER range ("integer out of range")
rather than finding nothing, and the test database (SQLite) rejects one
beyond 64 bits; dates and offsets have their own limits.
"""

import re

import pytest  # pyright: ignore[reportMissingImports]
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
