"""Keeping a rendered tracks GIF (services.tracks_gif_cache).

- the same inputs give the kept GIF without drawing it again;
- other inputs (a new track, another OpenAIP key) draw it again and replace
  that variant's file, while other variants stay;
- a GIF older than MAX_AGE_DAYS is drawn again; a disk that can't keep it
  still gets the GIF; the daily job deletes old ones;
- both download routes go through it; the folder is never backed up.
"""

import os
import time
from datetime import date

import pytest
from models import Flight, GpsTrack, db  # pyright: ignore[reportMissingImports]
from services import tracks_gif_cache  # pyright: ignore[reportMissingImports]
from services.upload_layout import (  # pyright: ignore[reportMissingImports]
    NOT_BACKED_UP_DIRNAMES,
    TRACKS_GIF_CACHE_DIRNAME,
)

from tests.test_gps_import import (  # pyright: ignore[reportMissingImports]
    _login,
    _make_user_and_aircraft,
)


class Renderer:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> bytes:
        self.calls += 1
        return b"GIF89a-%d" % self.calls


def _kept(folder: str, owner: str) -> list[str]:
    path = os.path.join(folder, TRACKS_GIF_CACHE_DIRNAME, owner)
    return sorted(os.listdir(path)) if os.path.isdir(path) else []


def _age(path: str, days: float) -> None:
    then = time.time() - days * 86400
    os.utime(path, (then, then))


def test_the_same_inputs_give_the_kept_gif(tmp_path) -> None:
    render = Renderer()
    args = (str(tmp_path), "aircraft-1", "landscape-std")
    first = tracks_gif_cache.cached_gif(*args, {"tracks": [1]}, render)
    again = tracks_gif_cache.cached_gif(*args, {"tracks": [1]}, render)
    assert first == again == b"GIF89a-1" and render.calls == 1


def test_other_inputs_replace_only_that_variant(tmp_path) -> None:
    render = Renderer()
    folder = str(tmp_path)
    tracks_gif_cache.cached_gif(folder, "pilot-3", "landscape-std", [1], render)
    tracks_gif_cache.cached_gif(folder, "pilot-3", "portrait-hires", [1], render)
    newer = tracks_gif_cache.cached_gif(
        folder, "pilot-3", "landscape-std", [1, 2], render
    )
    assert newer == b"GIF89a-3"
    kept = _kept(folder, "pilot-3")
    assert len(kept) == 2
    assert {name.split("-")[0] for name in kept} == {"landscape", "portrait"}


def test_an_old_gif_is_drawn_again(tmp_path) -> None:
    render = Renderer()
    args = (str(tmp_path), "aircraft-1", "landscape-std", [1])
    tracks_gif_cache.cached_gif(*args, render)
    (name,) = _kept(str(tmp_path), "aircraft-1")
    _age(os.path.join(tmp_path, TRACKS_GIF_CACHE_DIRNAME, "aircraft-1", name), 31)
    assert tracks_gif_cache.cached_gif(*args, render) == b"GIF89a-2"


def test_a_disk_that_cannot_keep_it_still_gets_the_gif(tmp_path) -> None:
    (tmp_path / TRACKS_GIF_CACHE_DIRNAME).write_text("not a folder")
    render = Renderer()
    args = (str(tmp_path), "aircraft-1", "landscape-std", [1])
    assert tracks_gif_cache.cached_gif(*args, render) == b"GIF89a-1"
    assert tracks_gif_cache.cached_gif(*args, render) == b"GIF89a-2"


def test_the_daily_job_deletes_old_gifs(tmp_path, monkeypatch) -> None:
    folder = str(tmp_path)
    render = Renderer()
    tracks_gif_cache.cached_gif(folder, "aircraft-1", "landscape-std", [1], render)
    tracks_gif_cache.cached_gif(folder, "aircraft-2", "landscape-std", [1], render)
    (old,) = _kept(folder, "aircraft-1")
    _age(os.path.join(folder, TRACKS_GIF_CACHE_DIRNAME, "aircraft-1", old), 31)
    assert tracks_gif_cache.cleanup_stale_gifs(folder) == 1
    assert not os.path.isdir(
        os.path.join(folder, TRACKS_GIF_CACHE_DIRNAME, "aircraft-1")
    )
    assert len(_kept(folder, "aircraft-2")) == 1
    assert tracks_gif_cache.cleanup_stale_gifs(str(tmp_path / "none")) == 0

    # A file replaced while the job runs is skipped.
    real_getmtime = os.path.getmtime

    def vanished(path):
        if path.endswith(".gif"):
            raise FileNotFoundError(path)
        return real_getmtime(path)

    monkeypatch.setattr(tracks_gif_cache.os.path, "getmtime", vanished)
    assert tracks_gif_cache.cleanup_stale_gifs(folder) == 0


def test_the_cache_is_never_backed_up() -> None:
    assert TRACKS_GIF_CACHE_DIRNAME in NOT_BACKED_UP_DIRNAMES


def _flight_with_a_track(app, *, aircraft_id=None, pic_user_id=None) -> None:
    with app.app_context():
        track = GpsTrack(
            source_filename="t.gpx",
            geojson={
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[4.0, 51.0], [4.5, 51.3], [5.0, 51.0]],
                },
                "properties": {},
            },
        )
        db.session.add(track)
        db.session.flush()
        db.session.add(
            Flight(
                aircraft_id=aircraft_id,
                pic_user_id=pic_user_id,
                date=date(2024, 6, 1),
                departure_icao="EBNM",
                arrival_icao="EBAW",
                gps_track_id=track.id,
            )
        )
        db.session.commit()


@pytest.mark.parametrize("pilot", [False, True])
def test_a_second_download_is_not_drawn_again(client, app, monkeypatch, pilot) -> None:
    import utils  # pyright: ignore[reportMissingImports]

    uid, _tid, ac_id = _make_user_and_aircraft(app)
    _login(client, uid)
    _flight_with_a_track(
        app, aircraft_id=None if pilot else ac_id, pic_user_id=uid if pilot else None
    )
    calls = []
    real = utils.generate_tracks_gif

    def counting(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(utils, "generate_tracks_gif", counting)
    url = (
        "/pilot/tracks/animation.gif"
        if pilot
        else f"/aircraft/{ac_id}/tracks/animation.gif"
    )
    first = client.get(url)
    second = client.get(url)
    assert first.data[:3] == b"GIF" and second.data == first.data
    assert len(calls) == 1
    client.get(url + "?orientation=portrait")
    assert len(calls) == 2
    owner = f"pilot-{uid}" if pilot else f"aircraft-{ac_id}"
    assert len(_kept(app.config["UPLOAD_FOLDER"], owner)) == 2
