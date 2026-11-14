"""Maps of many flights: track overviews and the map tile cache.

- services.track_overview: a track's light line (simplified, capped,
  rounded, no altitude), stored with it and refreshed when it changes;
- utils: map tiles kept on disk between requests (fresh / stale / not
  writable), and left out when they can't be had;
- backups leave the tile cache out.
"""

import os
import time
import zipfile
from unittest.mock import patch

from models import GpsTrack, db  # pyright: ignore[reportMissingImports]
from services.track_overview import (  # pyright: ignore[reportMissingImports]
    MAX_POINTS,
    overview_coords,
    overview_geojson,
)


def _feature(coords, kind="LineString"):
    return {"type": "Feature", "geometry": {"type": kind, "coordinates": coords}}


class TestOverview:
    def test_straight_line_keeps_its_ends_rounded_without_altitude(self):
        line = [[4.0 + i * 0.001, 51.0 + i * 0.001, 300.0] for i in range(50)]
        line[10] = [4.0123456789, 51.0101, 300.0]  # a real bend
        got = overview_coords(_feature(line))
        assert got[0] == [4.0, 51.0] and got[-1] == [4.049, 51.049]
        assert [4.01235, 51.0101] in got  # the bend, rounded to ~1 m
        assert len(got) <= 6 and all(len(p) == 2 for p in got)  # no altitude

    def test_long_wiggly_track_is_capped(self):
        line = [[4.0 + i * 0.001, 51.0 + (0.01 if i % 2 else 0.0)] for i in range(2000)]
        assert 2 <= len(overview_coords(_feature(line))) <= MAX_POINTS

    def test_shapes_it_reads(self):
        multi = _feature(
            [[[4.0, 51.0], [4.1, 51.1]], [[4.2, 51.2]], None], "MultiLineString"
        )
        assert overview_coords(multi) == [[4.0, 51.0], [4.2, 51.2]]
        # A bare geometry, a loop back to the start (zero-length baseline).
        loop = {"type": "LineString", "coordinates": [[4, 51], [4.5, 51.5], [4, 51]]}
        assert overview_coords(loop) == [[4, 51], [4.5, 51.5], [4, 51]]
        for nothing in (
            None,
            "x",
            {"type": "Feature", "geometry": "x"},
            _feature([4, 51], "Point"),
            _feature([[4, 51], "junk", [5]]),
        ):
            assert overview_coords(nothing) is None

    def test_stored_and_refreshed_with_the_track(self, app):
        with app.app_context():
            track = GpsTrack(geojson=_feature([[4, 51], [5, 52]]))
            db.session.add(track)
            db.session.commit()
            assert track.overview == [[4, 51], [5, 52]]
            track.geojson = _feature([[6, 50], [7, 49]])
            db.session.commit()
            assert track.overview == [[6, 50], [7, 49]]
            track.source_filename = "x.gpx"  # not the line: kept as is
            db.session.commit()
            assert track.overview == [[6, 50], [7, 49]]

            assert overview_geojson(None) is None
            assert overview_geojson(track)["geometry"]["coordinates"] == [
                [6, 50],
                [7, 49],
            ]
            track.overview = None  # e.g. not saved since overviews exist
            assert overview_geojson(track)["type"] == "Feature"
            track.geojson = None
            assert overview_geojson(track) is None


class TestTileCache:
    def test_disk_cache_fresh_stale_and_unwritable(self, app, tmp_path):
        import utils

        calls = []

        class _Resp:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self):
                return self.data

        def fake_urlopen(req, timeout):
            calls.append(req.full_url)
            return _Resp(b"tile-%d" % len(calls))

        cache_dir = str(tmp_path)
        with patch("urllib.request.urlopen", fake_urlopen):
            first = utils._fetch_tile_bytes(
                "osm", 3, 1, 2, "https://t/3/1/2.png", cache_dir
            )
            again = utils._fetch_tile_bytes(
                "osm", 3, 1, 2, "https://t/3/1/2.png", cache_dir
            )
            assert first == again == b"tile-1" and len(calls) == 1
            path = os.path.join(cache_dir, "osm", "3", "1", "2.png")
            old = time.time() - utils._TILE_MAX_AGE_S["osm"] - 10
            os.utime(path, (old, old))
            assert (
                utils._fetch_tile_bytes(
                    "osm", 3, 1, 2, "https://t/3/1/2.png", cache_dir
                )
                == b"tile-2"
            )
            # Nowhere to keep it: still served.
            with patch("os.makedirs", side_effect=OSError("read-only")):
                assert (
                    utils._fetch_tile_bytes(
                        "osm", 4, 1, 2, "https://t/4/1/2.png", cache_dir
                    )
                    == b"tile-3"
                )
            assert (
                utils._fetch_tile_bytes("osm", 4, 1, 2, "https://t/4/1/2.png", None)
                == b"tile-4"
            )

    def test_cache_dir_only_inside_the_app(self, app):
        from concurrent.futures import ThreadPoolExecutor

        import utils

        # Checked in a fresh thread: Flask contexts do not carry over to it, so
        # an app context another test left pushed on this worker cannot leak in.
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(utils._tile_cache_dir).result() is None
        with app.app_context():
            assert utils._tile_cache_dir().endswith("tile_cache")

    def test_backup_leaves_the_tile_cache_out(self, app, tmp_path):
        from config.routes import (  # pyright: ignore[reportMissingImports]
            _add_uploads_to_zip,
        )

        up = tmp_path / "uploads"
        (up / "tile_cache" / "osm").mkdir(parents=True)
        (up / "tile_cache" / "osm" / "1.png").write_bytes(b"x")
        (up / "t").mkdir()
        (up / "t" / "doc.pdf").write_bytes(b"x")
        out = tmp_path / "b.zip"
        with zipfile.ZipFile(out, "w") as zf:
            _add_uploads_to_zip(zf, str(up))
        with zipfile.ZipFile(out) as zf:
            assert zf.namelist() == ["uploads/t/doc.pdf"]


def test_flights_in_the_order_they_were_flown():
    """Same-day flights by start time — GPS block-off, else logged departure
    (both UTC) — so an outbound flight is drawn before its return."""
    from datetime import UTC, date, datetime, time
    from types import SimpleNamespace

    from services.track_overview import (  # pyright: ignore[reportMissingImports]
        in_flight_order,
    )

    def f(fid, day, block_off=None, dep=None, takeoff=None):
        return SimpleNamespace(
            id=fid,
            date=day,
            block_off_utc=block_off,
            departure_time=dep,
            takeoff_time=takeoff,
        )

    d = date(2026, 6, 26)
    back = f(1, d, block_off=datetime(2026, 6, 26, 13, 34, tzinfo=UTC))
    out = f(2, d, block_off=datetime(2026, 6, 26, 6, 10))  # naive, as SQLite
    logged_only = f(3, d, dep=time(9, 0))
    takeoff_only = f(4, d, takeoff=time(15, 0))
    no_time = f(5, d)
    # A track whose GPS date is the day after its logged date (a logbook
    # date typo) stays on its logged day, after that day's flights.
    typo = f(6, date(2026, 6, 25), block_off=datetime(2026, 6, 26, 14, 8, tzinfo=UTC))
    earlier_day = f(7, date(2026, 6, 25), dep=time(16, 0))
    flights = [back, typo, takeoff_only, no_time, logged_only, out, earlier_day]
    assert [x.id for x in in_flight_order(flights)] == [7, 6, 5, 2, 3, 1, 4]
