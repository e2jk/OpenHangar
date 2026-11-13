"""Imports refuse what would expand too far, before expanding it (audit S08).

All files here are small and built on the fly: budgets are lowered with
monkeypatch where a realistic limit would need a big file, and the highly
compressed entry is streamed into its archive, never held in memory.
"""

import io
import zipfile

import openpyxl
import pytest
from services import spreadsheet_limits

from tests.test_amp_import_routes import _add_aircraft, _create_user_and_tenant
from tests.test_amp_import_routes import _login as _amp_login
from tests.test_logbook_import import _login, _make_user

HEADER = ["Date", "Departure place", "Arrival place", "Aircraft type"]


def _xlsx(rows, *, extra=None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Logbook"
    for row in rows:
        ws.append(row)
    if extra:
        extra(ws)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _logbook(n=3):
    return [HEADER] + [
        [f"2024-01-{i + 1:02d}", "EBAW", "EBKT", "C172"] for i in range(n)
    ]


def _with_bomb(data: bytes, size: int) -> bytes:
    """`data` plus an entry of `size` zero bytes, written in 1 MB chunks."""
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for entry in source.infolist():
            target.writestr(entry, source.read(entry.filename))
        with target.open("xl/media/padding.bin", "w") as fh:
            chunk = b"\0" * (1024 * 1024)
            for _ in range(size // len(chunk)):
                fh.write(chunk)
    return out.getvalue()


@pytest.fixture
def no_openpyxl(monkeypatch):
    """Fail the test if a refused file still reached openpyxl."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("openpyxl was asked to load a refused file")

    monkeypatch.setattr(openpyxl, "load_workbook", refuse)


class TestArchiveBudget:
    def test_highly_compressed_entry_is_refused_unopened(self, app, no_openpyxl):
        data = _with_bomb(_xlsx(_logbook()), 8 * 1024 * 1024)
        assert len(data) < 200 * 1024  # a small upload...
        with app.test_request_context(), pytest.raises(ValueError, match="too large"):
            spreadsheet_limits.check_xlsx_archive(data)

    def test_total_expanded_size_is_bounded(self, app):
        data = _xlsx(_logbook())
        with app.test_request_context(), pytest.raises(ValueError, match="too large"):
            spreadsheet_limits.check_xlsx_archive(data, max_expanded=1000)

    def test_too_many_entries_are_refused(self, app, monkeypatch):
        monkeypatch.setattr(spreadsheet_limits, "MAX_ARCHIVE_ENTRIES", 5)
        with app.test_request_context(), pytest.raises(ValueError, match="too large"):
            spreadsheet_limits.check_xlsx_archive(_xlsx(_logbook()))

    def test_ordinary_workbooks_and_non_zip_data_pass(self, app):
        with app.test_request_context():
            spreadsheet_limits.check_xlsx_archive(_xlsx(_logbook(200)))
            spreadsheet_limits.check_xlsx_archive(b"not a zip: openpyxl reports it")


class TestBoundedRows:
    def test_too_many_rows_are_refused(self, app, monkeypatch):
        from pilots.logbook_import import parse_file

        monkeypatch.setattr(spreadsheet_limits, "MAX_ROWS", 50)
        with app.test_request_context(), pytest.raises(ValueError, match="too large"):
            parse_file(_xlsx(_logbook(60)), "big.xlsx")

    def test_formatting_far_below_the_data_costs_nothing(self, app, monkeypatch):
        from pilots.logbook_import import parse_file

        monkeypatch.setattr(spreadsheet_limits, "MAX_ROWS", 50)

        def styled_far_below(ws):
            ws.cell(row=5000, column=2).number_format = "0.0"

        with app.test_request_context():
            parsed = parse_file(_xlsx(_logbook(5), extra=styled_far_below), "f.xlsx")
        assert len(parsed.data_rows) == 5

    def test_empty_rows_between_data_are_kept_as_read(self):
        rows = [[1, 2], [None, None], [None], [3, 4], [None, None]]
        assert spreadsheet_limits.bounded_rows(rows) == [
            [1, 2],
            [None, None],
            [None],
            [3, 4],
        ]

    def test_data_after_too_many_empty_rows_is_refused(self, app, monkeypatch):
        monkeypatch.setattr(spreadsheet_limits, "MAX_ROWS", 3)
        rows = [[1]] + [[None]] * 5 + [[2]]
        with app.test_request_context(), pytest.raises(ValueError, match="too large"):
            spreadsheet_limits.bounded_rows(rows)

    def test_columns_beyond_the_budget_are_not_read(self, app, monkeypatch):
        from pilots.logbook_import import parse_file

        monkeypatch.setattr("pilots.logbook_import.MAX_COLUMNS", 6)

        def far_right(ws):
            ws.cell(row=2, column=16000, value="far away")

        with app.test_request_context():
            parsed = parse_file(_xlsx(_logbook(3), extra=far_right), "f.xlsx")
        assert all(len(row) <= 6 for row in parsed.data_rows)

    def test_a_merged_range_over_the_whole_sheet_is_clipped(self, app):
        """Declared in the sheet XML directly: building it with openpyxl's
        writer would create an object per merged cell."""
        from pilots.logbook_import import parse_file

        source = zipfile.ZipFile(io.BytesIO(_xlsx([["LOGBOOK"], *_logbook(3)])))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for entry in source.infolist():
                content = source.read(entry.filename)
                if entry.filename == "xl/worksheets/sheet1.xml":
                    content = content.replace(
                        b"</sheetData>",
                        b'</sheetData><mergeCells count="1">'
                        b'<mergeCell ref="A1:XFD1048576"/></mergeCells>',
                    )
                target.writestr(entry, content)
        with app.test_request_context():
            parsed = parse_file(out.getvalue(), "f.xlsx")
        assert parsed.norm_cols

    def test_csv_rows_and_wide_rows_are_bounded(self, app, monkeypatch):
        from pilots.logbook_import import parse_file

        monkeypatch.setattr(spreadsheet_limits, "MAX_ROWS", 10)
        many = (
            "Date,Departure place,Arrival place,Aircraft type\n"
            + "2024-01-01,EBAW,EBKT,C172\n" * 20
        ).encode()
        wide = (
            "Date,Departure place,Arrival place,Aircraft type\n" + "x," * 200 + "\n"
        ).encode()
        trailing = (
            "Date,Departure place,Arrival place,Aircraft type\n"
            "2024-01-01,EBAW,EBKT,C172" + "," * 200 + "\n"
        ).encode()
        with app.test_request_context():
            for data in (many, wide):
                with pytest.raises(ValueError, match="too large"):
                    parse_file(data, "f.csv")
            assert parse_file(trailing, "f.csv").data_rows


class TestParseCache:
    def test_large_workbooks_are_not_cached(self, app, monkeypatch):
        from pilots import logbook_import

        monkeypatch.setattr(logbook_import, "_EXCEL_PARSE_CACHE_MAX_CELLS", 10)
        logbook_import._excel_parse_cache.clear()
        with app.test_request_context():
            logbook_import.parse_file(_xlsx(_logbook(20)), "big.xlsx")
            assert not logbook_import._excel_parse_cache
            logbook_import.parse_file(_xlsx(_logbook(1)), "small.xlsx")
            assert len(logbook_import._excel_parse_cache) == 1
        logbook_import._excel_parse_cache.clear()


class TestGpsPointBudget:
    @pytest.mark.parametrize("kind", ["gpx", "kml", "csv"])
    def test_files_with_too_many_points_are_refused(self, app, monkeypatch, kind):
        from aircraft import gps_import

        monkeypatch.setattr(gps_import, "_MAX_POINTS_PER_FILE", 5)
        times = [f"2024-05-01T10:00:{i:02d}Z" for i in range(10)]
        if kind == "gpx":
            points = "".join(
                f'<trkpt lat="50.{i}" lon="4.{i}"><time>{t}</time></trkpt>'
                for i, t in enumerate(times)
            )
            data = (
                '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
                f"{points}</trkseg></trk></gpx>"
            ).encode()
            name = "track.gpx"
        elif kind == "kml":
            whens = "".join(f"<when>{t}</when>" for t in times)
            coords = "".join(
                f"<gx:coord>4.{i} 50.{i} 100</gx:coord>" for i in range(10)
            )
            data = (
                '<kml xmlns="http://www.opengis.net/kml/2.2" '
                'xmlns:gx="http://www.google.com/kml/ext/2.2"><Document><Placemark>'
                f"<gx:Track>{whens}{coords}</gx:Track></Placemark></Document></kml>"
            ).encode()
            name = "track.kml"
        else:
            rows = "".join(
                f"2024-05-01,10:00:{i:02d},+00:00,50.{i},4.{i},100,90,3D\n"
                for i in range(10)
            )
            data = (
                '#airframe_info, system_id="X"\nunits\n'
                "Lcl Date,Lcl Time,UTCOfst,Latitude,Longitude,AltMSL,GndSpd,GPSfix\n"
                + rows
            ).encode()
            name = "log_240501_100000_EBAW.csv"
        with (
            app.test_request_context(),
            pytest.raises(ValueError, match="too many points"),
        ):
            gps_import.parse_gps_file(data, name)


class TestRoutes:
    def test_pilot_import_refuses_a_bomb_with_a_message(self, app, client, no_openpyxl):
        with app.app_context():
            uid = _make_user()
        _login(client, uid)
        data = _with_bomb(_xlsx(_logbook()), 8 * 1024 * 1024)
        rv = client.post(
            "/pilot/logbook/import",
            data={"logbook_file": (io.BytesIO(data), "log.xlsx")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 422
        assert b"too large to import" in rv.data
        with client.session_transaction() as sess:
            assert not any("import" in key for key in sess)

    def test_amp_import_refuses_a_bomb_with_a_message(self, app, client, no_openpyxl):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _amp_login(app, client)
        data = _with_bomb(_xlsx([["Task", "Interval"]]), 8 * 1024 * 1024)
        rv = client.post(
            f"/aircraft/{acid}/maintenance/import",
            data={"amp_file": (io.BytesIO(data), "amp.xlsx")},
            content_type="multipart/form-data",
        )
        assert rv.status_code == 422
        assert b"too large to import" in rv.data


def test_merged_ranges_starting_outside_the_cells_read_are_ignored():
    from pilots.logbook_import import _merge_label_map

    rows = [["GROUP", None], ["a", "b"]]
    ranges = [(1, 1, 2, 1), (1, 5, 3, 9), (4, 2, 6, 2)]  # (min_col, min_row, ...)
    assert _merge_label_map(ranges, rows) == {(0, 0): "GROUP", (0, 1): "GROUP"}
