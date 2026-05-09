"""
Configuration blueprint — backup management, email settings, and future config sections.
"""
import hashlib
import io
import logging
import os
import subprocess
import zipfile
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, session, url_for  # pyright: ignore[reportMissingImports]
from flask_babel import gettext as _  # pyright: ignore[reportMissingImports]

from models import BackupRecord, db  # pyright: ignore[reportMissingImports]

config_bp = Blueprint("config", __name__, url_prefix="/config")
log = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _derive_key(password: str) -> bytes:
    """Derive a 32-byte AES key from a passphrase using SHA-256."""
    return hashlib.sha256(password.encode()).digest()


def _encrypt_bytes(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt *plaintext* with AES-256-GCM, prepending the 12-byte nonce."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]
    import os as _os
    nonce = _os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ct


def run_backup() -> BackupRecord:
    """
    Produce an encrypted ZIP backup of the PostgreSQL database and uploaded
    documents.

    The ZIP contains:
      - ``openhangar.sql``        — full pg_dump output
      - ``uploads/<filename>``   — every file from the uploads folder

    The ZIP is then AES-256-GCM encrypted and written to the backup folder.
    A ``BackupRecord`` row is committed and returned.

    Raises ``RuntimeError`` on failure; the record is still committed with
    ``status='failed'`` so operators can see the attempt.
    """
    from flask import current_app  # pyright: ignore[reportMissingImports]

    backup_folder = current_app.config.get("BACKUP_FOLDER", "/data/backups")
    upload_folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
    encryption_key_raw = os.environ.get("BACKUP_ENCRYPTION_KEY", "")
    database_url = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")

    os.makedirs(backup_folder, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"openhangar_backup_{ts}.zip.enc"
    path = os.path.join(backup_folder, filename)

    record = BackupRecord(filename=filename, path=path, status="failed")
    db.session.add(record)
    db.session.flush()  # get an id without committing

    try:
        sql_bytes = _pg_dump(database_url)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("openhangar.sql", sql_bytes)
            _add_uploads_to_zip(zf, upload_folder)
        zip_bytes = buf.getvalue()

        if encryption_key_raw:
            key = _derive_key(encryption_key_raw)
            payload = _encrypt_bytes(zip_bytes, key)
        else:
            payload = zip_bytes
            log.warning("BACKUP_ENCRYPTION_KEY not set — backup is unencrypted")

        with open(path, "wb") as fh:
            fh.write(payload)

        sha256 = hashlib.sha256(payload).hexdigest()
        record.size_bytes = len(payload)
        record.sha256 = sha256
        record.status = "ok"
    except Exception as exc:
        log.error("Backup failed: %s", exc)
        db.session.commit()
        raise RuntimeError(str(exc)) from exc

    db.session.commit()
    return record


def _add_uploads_to_zip(zf: zipfile.ZipFile, upload_folder: str) -> None:
    """Add every file in *upload_folder* into the ZIP under ``uploads/``."""
    if not os.path.isdir(upload_folder):
        return
    for entry in os.scandir(upload_folder):
        if entry.is_file():
            zf.write(entry.path, arcname=f"uploads/{entry.name}")


def _pg_dump(database_url: str) -> bytes:
    """Run pg_dump against *database_url* and return the SQL as bytes."""
    env = os.environ.copy()
    if database_url.startswith("postgresql"):
        env["DATABASE_URL"] = database_url
        # pg_dump reads PGPASSWORD / connection string
        cmd = ["pg_dump", "--no-password", database_url]
    else:
        raise RuntimeError(f"Unsupported database URL scheme: {database_url!r}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        env=env,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout


# ── views ─────────────────────────────────────────────────────────────────────

def _demo_guard():
    """Abort with 403 if running in demo mode."""
    if os.environ.get("FLASK_ENV") == "demo":
        abort(403)


@config_bp.route("/")
def index():
    _demo_guard()
    if not session.get("user_id"):
        return redirect(url_for("auth.login"))
    from services.email_service import get_smtp_status  # pyright: ignore[reportMissingImports]
    records = (
        BackupRecord.query
        .order_by(BackupRecord.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template("config/list.html", records=records,
                           smtp_status=get_smtp_status())


@config_bp.route("/run", methods=["POST"])
def run_backup_now():
    _demo_guard()
    if not session.get("user_id"):
        abort(403)
    try:
        record = run_backup()
        flash(_("Backup completed: %(filename)s", filename=record.filename), "success")
    except RuntimeError as exc:
        flash(_("Backup failed: %(error)s", error=exc), "danger")
    return redirect(url_for("config.index"))


@config_bp.route("/email/test", methods=["POST"])
def test_email():
    _demo_guard()
    if not session.get("user_id"):
        abort(403)
    from models import User  # pyright: ignore[reportMissingImports]
    from services.email_service import EmailNotConfiguredError, EmailSendError, send_email  # pyright: ignore[reportMissingImports]
    user = db.session.get(User, session["user_id"])
    if not user:
        abort(403)
    try:
        send_email(
            to=user.email,
            subject="OpenHangar — test email",
            text_body=(
                "This is a test email from your OpenHangar instance.\n\n"
                "If you received this, your SMTP configuration is working correctly."
            ),
        )
        flash(_("Test email sent to %(email)s.", email=user.email), "success")
    except EmailNotConfiguredError as exc:
        flash(_("Email not configured: %(error)s", error=exc), "warning")
    except EmailSendError as exc:
        flash(_("Email send failed: %(error)s", error=exc), "danger")
    return redirect(url_for("config.index"))
