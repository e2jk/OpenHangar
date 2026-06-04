"""
Tests for app/sync_watcher.py — background Syncthing import logic.

_scan_once is called directly with a test app whose UPLOAD_FOLDER points at a
tmp directory we control.  All tests use an in-memory SQLite DB (via the
session-scoped `app` fixture) so the watcher never starts a thread here.
"""

from datetime import date

import bcrypt  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    DocCategory,
    Document,
    PendingReconcile,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from sync_watcher import _scan_once  # pyright: ignore[reportMissingImports]


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def upload_dir(tmp_path):
    return tmp_path


@pytest.fixture()
def tenant_and_aircraft(app, upload_dir):
    """Create a tenant (slug='test-hangar') with one aircraft (OO-TST)."""
    app.config["UPLOAD_FOLDER"] = str(upload_dir)
    with app.app_context():
        tenant = Tenant(name="Test Hangar", slug="test-hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email="sync_test@example.com",
            password_hash=bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode(),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.ADMIN)
        )
        ac = Aircraft(
            tenant_id=tenant.id,
            registration="OO-TST",
            make="Cessna",
            model="172S",
        )
        db.session.add(ac)
        db.session.commit()
        yield tenant.id, ac.id

    with app.app_context():
        Document.query.filter(Document.filename.like("test-hangar/%")).delete()
        PendingReconcile.query.filter(
            PendingReconcile.filepath.like("test-hangar/%")
        ).delete()
        Aircraft.query.filter_by(registration="OO-TST").delete()
        TenantUser.query.filter(
            TenantUser.user_id
            == User.query.filter_by(email="sync_test@example.com")
            .with_entities(User.id)
            .scalar_subquery()
        ).delete(synchronize_session=False)
        User.query.filter_by(email="sync_test@example.com").delete()
        Tenant.query.filter_by(slug="test-hangar").delete()
        db.session.commit()


def _make_file(upload_dir, relpath: str, content: bytes = b"data") -> None:
    full = upload_dir / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)


# ── Auto-import tests ─────────────────────────────────────────────────────────


class TestAutoImport:
    def test_canonical_file_creates_document(
        self, app, upload_dir, tenant_and_aircraft
    ):
        tid, acid = tenant_and_aircraft
        _make_file(
            upload_dir,
            "test-hangar/OO-TST/maintenance/2024-03-15 - Annual inspection.pdf",
        )

        _scan_once(app)

        with app.app_context():
            doc = Document.query.filter(
                Document.filename.like("test-hangar/OO-TST/maintenance/%")
            ).first()
            assert doc is not None
            assert doc.title == "Annual inspection"
            assert doc.category == DocCategory.MAINTENANCE
            assert doc.aircraft_id == acid

    def test_canonical_file_is_idempotent(self, app, upload_dir, tenant_and_aircraft):
        _make_file(
            upload_dir,
            "test-hangar/OO-TST/insurance/2025-01-01 - Hull insurance.pdf",
        )

        _scan_once(app)
        _scan_once(app)

        with app.app_context():
            count = Document.query.filter(
                Document.filename.like("test-hangar/OO-TST/insurance/%")
            ).count()
            assert count == 1

    def test_registration_normalisation(self, app, upload_dir, tenant_and_aircraft):
        """Folder named 'oo-tst' (lowercase, hyphenated) matches OO-TST."""
        _make_file(
            upload_dir,
            "test-hangar/oo-tst/poh/2023-06-01 - POH rev5.pdf",
        )

        _scan_once(app)

        with app.app_context():
            doc = Document.query.filter(
                Document.filename.like("test-hangar/oo-tst/poh/%")
            ).first()
            assert doc is not None
            assert doc.category == DocCategory.POH

    def test_mime_and_size_populated(self, app, upload_dir, tenant_and_aircraft):
        payload = b"fake pdf content"
        _make_file(
            upload_dir,
            "test-hangar/OO-TST/logbook/2024-01-01 - Logbook.pdf",
            content=payload,
        )

        _scan_once(app)

        with app.app_context():
            doc = Document.query.filter(
                Document.filename.like("test-hangar/OO-TST/logbook/%")
            ).first()
            assert doc is not None
            assert doc.mime_type == "application/pdf"
            assert doc.size_bytes == len(payload)

    def test_title_extracted_from_canonical_filename(
        self, app, upload_dir, tenant_and_aircraft
    ):
        _make_file(
            upload_dir,
            "test-hangar/OO-TST/invoice/2023-11-30 - Parts receipt.pdf",
        )
        _scan_once(app)
        with app.app_context():
            doc = Document.query.filter(
                Document.filename.like("test-hangar/OO-TST/invoice/%")
            ).first()
            assert doc is not None
            assert doc.title == "Parts receipt"

    def test_title_from_stem_when_no_date_prefix(
        self, app, upload_dir, tenant_and_aircraft
    ):
        _make_file(
            upload_dir,
            "test-hangar/OO-TST/other/insurance certificate.pdf",
        )
        _scan_once(app)
        with app.app_context():
            doc = Document.query.filter(
                Document.filename.like("test-hangar/OO-TST/other/%")
            ).first()
            assert doc is not None
            assert doc.title == "insurance certificate"


# ── Pending-queue tests ───────────────────────────────────────────────────────


class TestPendingQueue:
    def test_unknown_aircraft_queued(self, app, upload_dir, tenant_and_aircraft):
        _make_file(
            upload_dir,
            "test-hangar/XX-UNK/maintenance/2024-05-01 - Some doc.pdf",
        )

        _scan_once(app)

        with app.app_context():
            pr = PendingReconcile.query.filter_by(
                filepath="test-hangar/XX-UNK/maintenance/2024-05-01 - Some doc.pdf"
            ).first()
            assert pr is not None
            assert pr.aircraft_id is None
            assert pr.category == DocCategory.MAINTENANCE
            assert pr.title_hint == "Some doc"
            assert pr.date_hint == date(2024, 5, 1)

    def test_unknown_category_queued(self, app, upload_dir, tenant_and_aircraft):
        _make_file(
            upload_dir,
            "test-hangar/OO-TST/noncategory/2024-05-01 - Something.pdf",
        )

        _scan_once(app)

        with app.app_context():
            pr = PendingReconcile.query.filter_by(
                filepath="test-hangar/OO-TST/noncategory/2024-05-01 - Something.pdf"
            ).first()
            assert pr is not None
            assert pr.category is None

    def test_shallow_path_queued(self, app, upload_dir, tenant_and_aircraft):
        """File directly in tenant folder (< 4 path parts) goes to review queue."""
        _make_file(upload_dir, "test-hangar/orphan.pdf")

        _scan_once(app)

        with app.app_context():
            pr = PendingReconcile.query.filter_by(
                filepath="test-hangar/orphan.pdf"
            ).first()
            assert pr is not None

    def test_pending_file_not_duplicated(self, app, upload_dir, tenant_and_aircraft):
        _make_file(
            upload_dir,
            "test-hangar/XX-UNK/maintenance/2024-06-01 - Dup doc.pdf",
        )

        _scan_once(app)
        _scan_once(app)

        with app.app_context():
            count = PendingReconcile.query.filter_by(
                filepath="test-hangar/XX-UNK/maintenance/2024-06-01 - Dup doc.pdf"
            ).count()
            assert count == 1


# ── Skip rules ────────────────────────────────────────────────────────────────


class TestSkipRules:
    def test_dotfile_skipped(self, app, upload_dir, tenant_and_aircraft):
        _make_file(
            upload_dir,
            "test-hangar/OO-TST/maintenance/.syncthing.tmp",
        )

        _scan_once(app)

        with app.app_context():
            assert (
                Document.query.filter(
                    Document.filename.like("test-hangar/OO-TST/maintenance/.%")
                ).count()
                == 0
            )
            assert (
                PendingReconcile.query.filter(
                    PendingReconcile.filepath.like("test-hangar/OO-TST/maintenance/.%")
                ).count()
                == 0
            )

    def test_underscore_file_skipped(self, app, upload_dir, tenant_and_aircraft):
        _make_file(
            upload_dir,
            "test-hangar/OO-TST/maintenance/_conflict.pdf",
        )

        _scan_once(app)

        with app.app_context():
            assert (
                PendingReconcile.query.filter(
                    PendingReconcile.filepath.like("test-hangar/OO-TST/maintenance/_%")
                ).count()
                == 0
            )

    def test_already_tracked_document_skipped(
        self, app, upload_dir, tenant_and_aircraft
    ):
        tid, acid = tenant_and_aircraft
        relpath = "test-hangar/OO-TST/maintenance/2024-01-01 - Already.pdf"
        _make_file(upload_dir, relpath)

        with app.app_context():
            existing = Document(
                aircraft_id=acid,
                filename=relpath,
                original_filename="Already.pdf",
                title="Already",
                category=DocCategory.MAINTENANCE,
            )
            db.session.add(existing)
            db.session.commit()

        _scan_once(app)

        with app.app_context():
            count = Document.query.filter_by(filename=relpath).count()
            assert count == 1
