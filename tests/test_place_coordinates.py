"""Explicit coordinates work offline; short links and ambiguous text are refused."""

import pytest
from places.access import PlaceError
from places.coordinates import coordinate_pair, parse_coordinates


@pytest.mark.parametrize(
    "value, expected",
    [
        ("50.5, 4.2", (50.5, 4.2)),
        ("-50.5 ; -4.2", (-50.5, -4.2)),
        ("50.5 4.2", (50.5, 4.2)),
        ("50.5\t 4.2", (50.5, 4.2)),
        (" 50.5 ,  4.2 ", (50.5, 4.2)),
        ("N 50° 30' E 4° 12'", (50.5, 4.2)),
        ("90,180", (90, 180)),
        ("50°30′0″N, 4°12′0″E", (50.5, 4.2)),
        ("S50°30'0\" W4°12'0\"", (-50.5, -4.2)),
        ("4°12'E 50°30'N", (50.5, 4.2)),
        ("N50°30'N E4°12'E", (50.5, 4.2)),
        ("https://maps.google.com/?q=50.5,4.2", (50.5, 4.2)),
        ("https://www.google.com/maps/search/?api=1&query=50.5,4.2", (50.5, 4.2)),
        ("https://maps.apple.com/?ll=50.5,4.2", (50.5, 4.2)),
        ("https://www.google.com/maps/@50.5,4.2,12z", (50.5, 4.2)),
        ("https://www.google.com/maps/place/test/data=!3d50.5!4d4.2!5m1", (50.5, 4.2)),
        (
            "https://www.openstreetmap.org/?mlat=50.5&mlon=4.2#map=15/50.5/4.2",
            (50.5, 4.2),
        ),
        ("https://www.openstreetmap.org/#map=15/50.5/4.2", (50.5, 4.2)),
    ],
)
def test_explicit_coordinate_formats(value: str, expected: tuple[float, float]) -> None:
    assert parse_coordinates(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a museum near here",
        "50",
        "NaN,4.2",
        "91,4",
        "1,181",
        "50°61'N 4°12'E",
        "50°30'61\"N 4°12'E",
        "50°30' 4°12'",
        "50°30'N 4°12'N",
        "N50°30'S E4°12'E",
        "prefix 50°30'N 4°12'E",
        "x" * 2049,
        "https://maps.app.goo.gl/secret",
        "javascript:alert(1)",
        "file:///tmp/map?q=50,4",
        "https://maps.yandex.com/?ll=4.2,50.5",
        "https://[",
        "https://maps.test/?" + "q=x&" * 31,
        "https://maps.test/?q=91,4",
        "https://maps.test/?mlat=50&mlon=nan",
    ],
)
def test_ambiguous_invalid_and_network_only_inputs_fail(value: str) -> None:
    with pytest.raises(PlaceError):
        parse_coordinates(value)


@pytest.mark.parametrize(
    "latitude, longitude",
    [
        ("", "4"),
        ("NaN", "4"),
        ("1", "inf"),
        ("x" * 33, "4"),
        ("50", "4" * 33),
        ("-91", "4"),
    ],
)
def test_form_coordinates_refuse_non_finite_unbounded_or_missing_values(
    latitude: str, longitude: str
) -> None:
    with pytest.raises(PlaceError):
        coordinate_pair(latitude, longitude)


@pytest.mark.parametrize(
    "value",
    [
        "1" + " " * 2000 + "x",
        "1," + " " * 2000 + "x",
        "1°" + " " * 2000 + "x",
        "1" + "\t " * 1000 + "1x",
        "N 1° " * 400,
    ],
)
def test_long_space_runs_are_refused_without_backtracking(value: str) -> None:
    """Possessive space runs: these took up to half a second each when the
    patterns could backtrack into a run of spaces (now well under 10 ms)."""
    import time

    start = time.perf_counter()
    with pytest.raises(PlaceError):
        parse_coordinates(value)
    assert time.perf_counter() - start < 0.1
