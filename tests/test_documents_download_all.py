"""Download all of an aircraft's documents as one ZIP."""

import io
import zipfile

from models import Document, db  # pyright: ignore[reportMissingImports]

from tests.test_documents import _add_aircraft, _create_user_and_tenant, _login


def test_every_document_gets_its_own_entry(app, client, tmp_path):
    """Two documents with the same name both reach the ZIP, and a name
    carrying a Windows path keeps only its file name."""
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    _uid, tid = _create_user_and_tenant(app)
    acid = _add_aircraft(app, tid, "OO-ZIP")
    with app.app_context():
        for i, original in enumerate(["report.pdf", "report.pdf", r"C:\scans\arc.pdf"]):
            stored = f"doc{i}.pdf"
            (tmp_path / stored).write_bytes(f"content {i}".encode())
            db.session.add(
                Document(
                    aircraft_id=acid,
                    filename=stored,
                    original_filename=original,
                    mime_type="application/pdf",
                    title=f"Doc {i}",
                )
            )
        db.session.commit()
    _login(app, client)
    resp = client.get(f"/aircraft/{acid}/documents/download-all")
    assert resp.status_code == 200
    assert "aircraft-OO-ZIP-documents.zip" in resp.headers["Content-Disposition"]
    names = zipfile.ZipFile(io.BytesIO(resp.data)).namelist()
    assert sorted(names) == ["arc.pdf", "manifest.txt", "report (2).pdf", "report.pdf"]


def test_a_name_without_extension_is_numbered_too():
    from documents.routes import _unique_arcname

    used: set[str] = set()
    assert _unique_arcname("README", used) == "README"
    assert _unique_arcname("readme", used) == "readme (2)"
    assert _unique_arcname("", used) == "document"


def test_attachment_disposition_survives_odd_registrations():
    """A registration with a quote, a backslash or a non-Latin-1 letter
    gives a valid header: an ASCII fallback plus the exact UTF-8 name."""
    from utils import attachment_disposition  # pyright: ignore[reportMissingImports]

    header = attachment_disposition('Ö"Z\\x_flights.csv')
    assert header.startswith('attachment; filename="Zx_flights.csv";')
    assert "filename*=UTF-8''%C3%96%22Z%5Cx_flights.csv" in header
    assert attachment_disposition("ÖÄ").startswith('attachment; filename="download"')
