"""Address search through a Nominatim-compatible geocoder (services.geocoding):
server-side only, one request per second for the instance, cached, and the
search text never logged."""

import fcntl
import io
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask
from services import geocoding
from services.upload_layout import GEOCODER_LOCKS_DIRNAME, NOT_BACKED_UP_DIRNAMES

ROWS = [
    {
        "lat": "50.4592",
        "lon": "4.4538",
        "display_name": "Charleroi, Hainaut, Wallonia, Belgium",
        "name": "Charleroi",
        "osm_type": "relation",
        "osm_id": 59579,
        "category": "boundary",
    },
    # No name: falls back to the first part of the address.
    {
        "lat": "50.5",
        "lon": "4.4",
        "display_name": "Rue du Lac 3, Charleroi",
        "osm_type": "way",
        "osm_id": "77",
    },
    {"lat": "not a number", "lon": "4"},
    {"lat": "95", "lon": "4"},
    "not an object",
]


class _Response(io.BytesIO):
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


@pytest.fixture(autouse=True)
def _fresh(app: Flask, tmp_path: Path) -> Iterator[None]:
    geocoding._cache.clear()
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    app.config["GEOCODER_URL"] = geocoding.DEFAULT_URL
    yield
    geocoding._cache.clear()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[urllib.request.Request]:
    seen: list[urllib.request.Request] = []

    def fake(request: urllib.request.Request, timeout: float) -> _Response:
        seen.append(request)
        assert timeout == geocoding.REQUEST_SECONDS
        return _Response(json.dumps(ROWS).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(geocoding.time, "sleep", lambda _s: None)
    return seen


def test_enabled_by_default_and_disabled_by_empty_or_bad_url(app: Flask) -> None:
    with app.app_context():
        assert geocoding.enabled()
        for url in ("", "  ", "ftp://geo.example", "nominatim.example"):
            app.config["GEOCODER_URL"] = url
            assert not geocoding.enabled()
            with pytest.raises(geocoding.GeocoderError) as error:
                geocoding.search("Charleroi")
            assert error.value.reason == "disabled"
        app.config["GEOCODER_URL"] = "https://geo.example/nominatim/"
        assert geocoding.base_url() == "https://geo.example/nominatim"


def test_search_sends_one_identified_request_and_parses_results(
    app: Flask, calls: list[urllib.request.Request]
) -> None:
    with app.test_request_context():
        results = geocoding.search("  Charleroi   airport ", language="fr", limit=99)
    assert [r.name for r in results] == ["Charleroi", "Rue du Lac 3"]
    first = results[0]
    assert (first.latitude, first.longitude) == (50.4592, 4.4538)
    assert first.osm_reference == "R59579" and first.category == "boundary"
    assert results[1].osm_reference == "W77"
    (request,) = calls
    parts = urlsplit(request.full_url)
    assert f"{parts.scheme}://{parts.netloc}" == geocoding.DEFAULT_URL
    assert parts.path == "/search"
    params = parse_qs(parts.query)
    assert params["q"] == ["Charleroi airport"]
    assert params["format"] == ["jsonv2"] and params["accept-language"] == ["fr"]
    assert params["limit"] == [str(geocoding.MAX_RESULTS)]
    assert "OpenHangar" in request.get_header("User-agent")


def test_repeated_searches_come_from_the_cache(
    app: Flask, calls: list[urllib.request.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    with app.test_request_context():
        geocoding.search("Charleroi")
        geocoding.search("charleroi ")  # same search, other spacing and case
        assert len(calls) == 1
        geocoding.search("Charleroi", language="nl")
        assert len(calls) == 2
        # An expired entry is fetched again.
        monkeypatch.setattr(geocoding, "CACHE_SECONDS", -1)
        geocoding.search("Charleroi")
        assert len(calls) == 3
        # The cache keeps only the most recent searches.
        monkeypatch.setattr(geocoding, "CACHE_SECONDS", 3600)
        monkeypatch.setattr(geocoding, "CACHE_ENTRIES", 1)
        geocoding.search("Namur")
        geocoding.search("Charleroi")
        assert len(calls) == 5


@pytest.mark.parametrize(
    "query", ["", " a ", "x" * 201, "Char\x00leroi", "Char\x7fleroi"]
)
def test_invalid_search_text_never_reaches_the_geocoder(
    app: Flask, calls: list[urllib.request.Request], query: str
) -> None:
    with app.test_request_context(), pytest.raises(geocoding.GeocoderError) as error:
        geocoding.search(query)
    assert error.value.reason == "invalid" and calls == []


def _failing(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    def fake(request: urllib.request.Request, timeout: float) -> Any:
        if isinstance(outcome, BaseException):
            raise outcome
        return _Response(outcome)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(geocoding.time, "sleep", lambda _s: None)


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (urllib.error.HTTPError("u", 429, "slow down", {}, None), "busy"),  # type: ignore[arg-type]
        (urllib.error.HTTPError("u", 503, "down", {}, None), "unavailable"),  # type: ignore[arg-type]
        (OSError("timed out"), "unavailable"),
        (b"{not json", "unavailable"),
        (b"\xff\xfe", "unavailable"),
        (json.dumps({"error": "x"}).encode(), "unavailable"),
        (b"[" + b" " * geocoding.MAX_RESPONSE_BYTES + b"]", "unavailable"),
    ],
)
def test_failures_are_reported_without_logging_the_search_text(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    outcome: Any,
    reason: str,
) -> None:
    _failing(monkeypatch, outcome)
    secret = "12 Secret Street, Hometown"
    with (
        app.test_request_context(),
        caplog.at_level(logging.DEBUG),
        pytest.raises(geocoding.GeocoderError) as error,
    ):
        geocoding.search(secret)
    assert error.value.reason == reason
    assert "Secret" not in caplog.text and "Hometown" not in caplog.text
    with app.test_request_context():
        assert error.value.message  # a translated, user-facing sentence


def test_messages_and_attribution(app: Flask) -> None:
    with app.test_request_context():
        texts = {
            reason: geocoding.GeocoderError(reason).message
            for reason in ("disabled", "invalid", "busy", "unavailable", "other")
        }
        assert texts["other"] == texts["unavailable"]
        assert len(set(texts.values())) == 4
        assert "OpenStreetMap" in geocoding.attribution()


def test_one_request_per_second_for_the_whole_instance(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    waits: list[float] = []
    clock = [1_000_000.0]
    monkeypatch.setattr(geocoding.time, "time", lambda: clock[0])
    monkeypatch.setattr(geocoding.time, "sleep", waits.append)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout: _Response(b"[]"),
    )
    with app.test_request_context():
        geocoding.search("Charleroi")
        assert waits == []
        clock[0] += 0.25
        geocoding.search("Namur")  # 0.25 s later: waits out the rest of the second
        assert waits == [pytest.approx(0.75)]
        clock[0] += 5
        geocoding.search("Liège")
        assert waits == [pytest.approx(0.75)]
        # A recorded time in the future (clock change) never waits over a second.
        lock = tmp_path / GEOCODER_LOCKS_DIRNAME / "requests.lock"
        lock.write_text(str(clock[0] + 3600))
        geocoding.search("Spa")
        assert waits[-1] == pytest.approx(geocoding.MIN_INTERVAL_SECONDS)
        lock.write_text("garbage")
        geocoding.search("Mons")


def test_a_held_slot_makes_other_searches_busy(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(geocoding, "SLOT_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout: _Response(b"[]")
    )
    folder = tmp_path / GEOCODER_LOCKS_DIRNAME
    folder.mkdir()
    with (folder / "requests.lock").open("a+") as other_worker:
        fcntl.flock(other_worker, fcntl.LOCK_EX)
        with (
            app.test_request_context(),
            pytest.raises(geocoding.GeocoderError) as error,
        ):
            geocoding.search("Charleroi")
        assert error.value.reason == "busy"
        fcntl.flock(other_worker, fcntl.LOCK_UN)


def test_unusable_lock_folder_is_unavailable(app: Flask, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("")
    app.config["UPLOAD_FOLDER"] = str(blocker)
    with app.test_request_context(), pytest.raises(geocoding.GeocoderError) as error:
        geocoding.search("Charleroi")
    assert error.value.reason == "unavailable"


def test_lock_folder_is_left_out_of_backups() -> None:
    assert GEOCODER_LOCKS_DIRNAME in NOT_BACKED_UP_DIRNAMES
