"""
Configuration blueprint — backup management, email settings, and future config sections.
"""

import contextlib
import hashlib
import io
import json
import logging
import os
import subprocess  # nosec B404
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)  # pyright: ignore[reportMissingImports]
from flask.typing import ResponseReturnValue  # pyright: ignore[reportMissingImports]
from flask_babel import gettext as _, ngettext  # pyright: ignore[reportMissingImports]

from models import AppSetting, BackupRecord, db  # pyright: ignore[reportMissingImports]
from utils import login_required, require_instance_admin  # pyright: ignore[reportMissingImports]

config_bp = Blueprint("config", __name__, url_prefix="/config")
log = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────


def _derive_key(passphrase: str) -> bytes:
    """Derive a 32-byte AES key from a passphrase using HKDF-SHA256."""
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # pyright: ignore[reportMissingImports]
    from cryptography.hazmat.primitives import hashes  # pyright: ignore[reportMissingImports]

    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"openhangar-backup-kdf-salt-v1",
        info=b"openhangar-backup-v1",
    ).derive(passphrase.encode())


def _encrypt_bytes(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt *plaintext* with AES-256-GCM, prepending the 12-byte nonce."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # pyright: ignore[reportMissingImports]
    import os as _os

    nonce = _os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ct


def _get_alembic_head() -> str | None:
    """Return current Alembic revision from the DB, or None if unavailable."""
    try:
        from sqlalchemy import text  # pyright: ignore[reportMissingImports]

        return db.session.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        ).scalar()
    except Exception:
        return None


def _parse_gatus_env() -> tuple[str, str, str | None] | None:
    """Return (base_url, endpoint_key, auth_header_or_None) from env vars, or None if not configured."""
    endpoint_url = os.environ.get("OPENHANGAR_GATUS_ENDPOINT_URL", "").rstrip("/")
    if not endpoint_url or "/endpoints/" not in endpoint_url:
        return None
    base_url, _, endpoint_key = endpoint_url.rpartition("/endpoints/")
    if not base_url or not endpoint_key:
        return None
    auth_header = os.environ.get("OPENHANGAR_GATUS_AUTH_HEADER") or None
    return base_url, endpoint_key, auth_header


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
    encryption_key_raw = os.environ.get("OPENHANGAR_BACKUP_ENCRYPTION_KEY", "")
    database_url = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")

    os.makedirs(backup_folder, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    app_version = os.environ.get("OPENHANGAR_VERSION", "development")
    filename = f"openhangar_backup_{ts}_{app_version}.zip.enc"
    path = os.path.join(backup_folder, filename)
    alembic_head = _get_alembic_head()
    metadata = {
        "app_version": app_version,
        "alembic_head": alembic_head,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    record = BackupRecord(
        filename=filename,
        path=path,
        status="failed",
        app_version=app_version,
        alembic_head=alembic_head,
    )
    db.session.add(record)
    db.session.flush()  # get an id without committing

    try:
        sql_bytes = _pg_dump(database_url)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("openhangar.sql", sql_bytes)
            zf.writestr("metadata.json", json.dumps(metadata, indent=2))
            _add_uploads_to_zip(zf, upload_folder)
        zip_bytes = buf.getvalue()

        if encryption_key_raw:
            key = _derive_key(encryption_key_raw)
            payload = _encrypt_bytes(zip_bytes, key)
        else:
            payload = zip_bytes
            log.warning(
                "OPENHANGAR_BACKUP_ENCRYPTION_KEY not set — backup is unencrypted"
            )

        with open(path, "wb") as fh:
            fh.write(payload)

        meta_path = path.replace(".zip.enc", ".meta")
        with open(meta_path, "w") as fh:
            json.dump(metadata, fh, indent=2)

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
        cmd = [
            "pg_dump",
            "--no-password",
            "--no-owner",   # omit ALTER … OWNER TO: role names are environment-specific
            "--no-acl",     # omit GRANT/REVOKE: privileges are managed by the app, not the DB
            database_url,
        ]
    else:
        raise RuntimeError(f"Unsupported database URL scheme: {database_url!r}")

    result = subprocess.run(  # nosec B603  # fixed list, no shell, DB URL from server config
        cmd,
        capture_output=True,
        env=env,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout


# ── views ─────────────────────────────────────────────────────────────────────


@config_bp.before_request
def _block_in_demo() -> None:
    if os.environ.get("OPENHANGAR_ENV") == "demo":
        abort(403)
    if session.get("user_id"):
        # All logged-in users may manage their own notification preferences
        if request.endpoint == "config.notification_preferences":
            return
        from models import Role, User  # pyright: ignore[reportMissingImports]
        from utils import current_user_role  # pyright: ignore[reportMissingImports]

        user = db.session.get(User, session["user_id"])
        # Instance admins always pass — they may not have a tenant role
        if user and user.is_instance_admin:
            return
        if current_user_role() not in (Role.ADMIN, Role.OWNER):
            abort(403)


@config_bp.route("/")
def index() -> ResponseReturnValue:
    if not session.get("user_id"):
        return redirect(url_for("auth.login"))
    from services.email_service import get_email_health, get_smtp_status  # pyright: ignore[reportMissingImports]

    _BACKUP_DISPLAY_LIMIT = 10
    total_backups = BackupRecord.query.count()
    records = (
        BackupRecord.query.order_by(BackupRecord.created_at.desc())
        .limit(_BACKUP_DISPLAY_LIMIT)
        .all()
    )
    backup_extra = max(0, total_backups - _BACKUP_DISPLAY_LIMIT)
    from sqlalchemy import func  # pyright: ignore[reportMissingImports]
    from models import Role, TenantUser, User, UserInvitation  # pyright: ignore[reportMissingImports]

    _role_labels = {
        Role.ADMIN: "Admin",
        Role.OWNER: "Owner",
        Role.PILOT: "Pilot / Renter",
        Role.MAINTENANCE: "Maintenance",
        Role.VIEWER: "Viewer",
    }
    tu_self = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    tid = tu_self.tenant_id if tu_self else None
    user_counts = []
    open_invitations = 0
    if tid:
        results = (
            db.session.query(TenantUser.role, func.count(TenantUser.user_id))
            .join(User, TenantUser.user_id == User.id)
            .filter(TenantUser.tenant_id == tid, User.is_active.is_(True))
            .group_by(TenantUser.role)
            .all()
        )
        counts_by_role = dict(results)
        user_counts = [
            (_role_labels[r], counts_by_role[r])
            for r in Role
            if counts_by_role.get(r, 0) > 0
        ]
        open_invitations = (
            UserInvitation.query.filter_by(tenant_id=tid)
            .filter(UserInvitation.accepted_at.is_(None))
            .count()
        )
    current_version = os.environ.get("OPENHANGAR_VERSION", "development")
    latest_setting = db.session.get(AppSetting, "latest_version")
    latest_version = latest_setting.value if latest_setting else None
    from utils import check_update_available  # pyright: ignore[reportMissingImports]

    update_available = check_update_available()
    versions_behind: int | None = None
    try:
        import json as _json

        _all_v_setting = db.session.get(AppSetting, "all_versions")
        if _all_v_setting and current_version != "development":
            _all_versions = _json.loads(_all_v_setting.value)
            if isinstance(_all_versions, list) and current_version in _all_versions:
                _idx = _all_versions.index(current_version)
                if _idx > 0:
                    versions_behind = _idx
    except Exception as exc:
        log.debug("Could not compute versions-behind count: %s", exc)
    db_size: str | None = None
    try:
        from sqlalchemy import text as _text  # pyright: ignore[reportMissingImports]

        _res = db.session.execute(
            _text("SELECT pg_size_pretty(pg_database_size(current_database()))")
        ).scalar()
        db_size = str(_res) if _res is not None else None
    except Exception as exc:
        log.debug("Could not retrieve DB size: %s", exc)
    upload_size_bytes: int | None = None
    try:
        _upload_folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
        if os.path.isdir(_upload_folder):
            upload_size_bytes = sum(
                int(e.stat().st_size) for e in os.scandir(_upload_folder) if e.is_file()
            )
    except Exception as exc:
        log.debug("Could not retrieve upload folder size: %s", exc)
    from models import Tenant, User  # pyright: ignore[reportMissingImports]

    current_user = db.session.get(User, session["user_id"])
    tenant_count = Tenant.query.count()
    _tenant = db.session.get(Tenant, tid) if tid else None
    upgrade_dir = os.environ.get("OPENHANGAR_UPGRADE_DIR", "")
    upgrade_dir_enabled = bool(upgrade_dir)
    upgrade_active = False
    if upgrade_dir:
        upgrade_active = os.path.exists(
            os.path.join(upgrade_dir, "trigger")
        ) or os.path.exists(os.path.join(upgrade_dir, "trigger.running"))
    return render_template(
        "config/settings.html",
        records=records,
        backup_extra=backup_extra,
        backup_encryption_key_set=bool(
            os.environ.get("OPENHANGAR_BACKUP_ENCRYPTION_KEY")
        ),
        backup_folder=current_app.config.get("BACKUP_FOLDER", "/data/backups"),
        smtp_status=get_smtp_status(),
        email_health=get_email_health(),
        user_counts=user_counts,
        open_invitations=open_invitations,
        current_version=current_version,
        latest_version=latest_version,
        update_available=update_available,
        versions_behind=versions_behind,
        db_size=db_size,
        upload_size_bytes=upload_size_bytes,
        current_user=current_user,
        tenant_count=tenant_count,
        tenant=_tenant,
        openaip_api_key=(
            db.session.get(AppSetting, "openaip_api_key")
            or type("_", (), {"value": None})()
        ).value,
        gatus_configured=_parse_gatus_env() is not None,
        upgrade_dir_enabled=upgrade_dir_enabled,
        upgrade_active=upgrade_active,
    )


@config_bp.route("/tenant-slug", methods=["POST"])
@login_required
def update_tenant_slug() -> ResponseReturnValue:
    import re as _re
    import shutil
    from models import AircraftPhoto, Document, PendingReconcile, Tenant, TenantUser  # pyright: ignore[reportMissingImports]

    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)  # pragma: no cover
    tenant = db.session.get(Tenant, tu.tenant_id)
    if not tenant:
        abort(403)  # pragma: no cover

    raw = request.form.get("slug", "").strip().lower()
    if not raw:
        flash(_("Hangar ID cannot be empty."), "danger")
        return redirect(url_for("config.index"))

    slug = _re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:64]
    if not slug:
        flash(_("Hangar ID must contain at least one letter or digit."), "danger")
        return redirect(url_for("config.index"))

    existing = Tenant.query.filter(Tenant.slug == slug, Tenant.id != tenant.id).first()
    if existing:
        flash(_("That Hangar ID is already in use. Please choose another."), "danger")
        return redirect(url_for("config.index"))

    old_slug = tenant.slug
    tenant.slug = slug

    if old_slug and old_slug != slug:
        from documents.routes import _safe_join  # pyright: ignore[reportMissingImports]

        folder = current_app.config.get("UPLOAD_FOLDER", "/data/uploads")
        old_dir = _safe_join(folder, old_slug)
        new_dir = _safe_join(folder, slug)
        if os.path.isdir(old_dir):
            if os.path.isdir(new_dir):
                # Destination already exists — merge file-by-file
                for dirpath, _dirs, filenames in os.walk(old_dir):
                    rel = os.path.relpath(dirpath, old_dir)
                    dest_dir = os.path.join(new_dir, rel)
                    os.makedirs(dest_dir, exist_ok=True)
                    for fname in filenames:
                        shutil.move(
                            os.path.join(dirpath, fname),
                            os.path.join(dest_dir, fname),
                        )
                shutil.rmtree(old_dir, ignore_errors=True)
            else:
                os.rename(old_dir, new_dir)

        # Rewrite stored paths in the database
        prefix_old = old_slug + "/"
        prefix_new = slug + "/"
        for doc in Document.query.filter(Document.filename.like(old_slug + "/%")).all():
            doc.filename = prefix_new + doc.filename[len(prefix_old) :]
        for pr in PendingReconcile.query.filter(
            PendingReconcile.filepath.like(old_slug + "/%")
        ).all():
            pr.filepath = prefix_new + pr.filepath[len(prefix_old) :]
        for photo in AircraftPhoto.query.filter(
            AircraftPhoto.filename.like(old_slug + "/%")
        ).all():
            photo.filename = prefix_new + photo.filename[len(prefix_old) :]

    db.session.commit()
    flash(_("Hangar ID saved."), "success")
    return redirect(url_for("config.index"))


@config_bp.route("/map-tiles", methods=["POST"])
@login_required
def update_map_tiles() -> ResponseReturnValue:
    # ADMIN/OWNER enforcement is handled by config_bp.before_request.
    key = request.form.get("openaip_api_key", "").strip()
    setting = db.session.get(AppSetting, "openaip_api_key")
    if key:
        if setting:
            setting.value = key
        else:
            db.session.add(AppSetting(key="openaip_api_key", value=key))
        db.session.commit()
        flash(_("OpenAIP API key saved."), "success")
    else:
        if setting:
            db.session.delete(setting)
            db.session.commit()
        flash(_("OpenAIP API key removed."), "success")
    return redirect(url_for("config.index"))


@config_bp.route("/run", methods=["POST"])
def run_backup_now() -> ResponseReturnValue:
    if not session.get("user_id"):
        abort(403)
    try:
        record = run_backup()
        flash(_("Backup completed: %(filename)s", filename=record.filename), "success")
    except RuntimeError as exc:
        flash(_("Backup failed: %(error)s", error=exc), "danger")
    return redirect(url_for("config.index"))


@config_bp.route("/profile", methods=["POST"])
def update_profile() -> ResponseReturnValue:
    if not session.get("user_id"):
        abort(403)
    from models import OperatingModel, Tenant, TenantProfile, TenantUser  # pyright: ignore[reportMissingImports]

    tu = TenantUser.query.filter_by(user_id=session["user_id"]).first()
    if not tu:
        abort(403)  # pragma: no cover

    profile = TenantProfile.query.filter_by(tenant_id=tu.tenant_id).first()
    if not profile:
        profile = TenantProfile(tenant_id=tu.tenant_id, setup_complete=True)
        db.session.add(profile)

    model_str = request.form.get("operating_model", "sole_operator")
    try:
        profile.operating_model = OperatingModel(model_str)
    except ValueError:
        flash(_("Invalid operating model."), "danger")
        return redirect(url_for("config.index"))
    if model_str == "sole_pilot":
        profile.planned_aircraft_count = 0
        profile.allows_rental = False
    else:
        try:
            count = max(1, int(request.form.get("planned_aircraft_count") or 1))
        except (ValueError, TypeError):
            count = 1
        profile.planned_aircraft_count = count
        profile.allows_rental = bool(request.form.get("allows_rental"))

    tenant = db.session.get(Tenant, tu.tenant_id)
    if tenant:
        tenant.require_totp = bool(request.form.get("require_totp"))

    db.session.commit()
    flash(_("Usage profile updated."), "success")
    return redirect(url_for("config.index"))


@config_bp.route("/email/test", methods=["POST"])
def test_email() -> ResponseReturnValue:
    if not session.get("user_id"):
        abort(403)
    from models import User  # pyright: ignore[reportMissingImports]
    from services.email_service import (
        EmailNotConfiguredError,
        EmailSendError,
        send_email,
    )  # pyright: ignore[reportMissingImports]

    user = db.session.get(User, session["user_id"])
    if not user:
        abort(403)  # pragma: no cover
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


@config_bp.route("/check-version", methods=["POST"])
def check_version() -> ResponseReturnValue:
    if not session.get("user_id"):
        abort(403)
    import json as _json
    from datetime import datetime, timezone
    from services.version_service import fetch_versions, upsert_app_setting  # pyright: ignore[reportMissingImports]

    versions = fetch_versions()
    upsert_app_setting(
        db.session,
        "version_last_checked_at",
        datetime.now(timezone.utc).isoformat(),
    )
    if versions:
        upsert_app_setting(db.session, "latest_version", versions[0])
        upsert_app_setting(db.session, "all_versions", _json.dumps(versions))
        from services.version_service import _persist_update_flag  # pyright: ignore[reportMissingImports]

        _persist_update_flag(
            db.session, os.environ.get("OPENHANGAR_VERSION", "development"), versions[0]
        )
    db.session.commit()
    flash(_("Version check refreshed."), "success")
    return redirect(url_for("config.index"))


# ── One-click upgrade ─────────────────────────────────────────────────────────


@config_bp.route("/trigger-upgrade", methods=["POST"])
def trigger_upgrade() -> ResponseReturnValue:
    if not session.get("user_id"):
        abort(403)
    upgrade_dir = os.environ.get("OPENHANGAR_UPGRADE_DIR", "")
    if not upgrade_dir:
        abort(404)
    os.makedirs(upgrade_dir, exist_ok=True)
    running_path = os.path.join(upgrade_dir, "trigger.running")
    trigger_path = os.path.join(upgrade_dir, "trigger")
    if os.path.exists(running_path):
        flash(_("An upgrade is already in progress."), "warning")
        return redirect(url_for("config.index"))
    if os.path.exists(trigger_path):
        flash(_("Upgrade already triggered."), "info")
        return redirect(url_for("config.index"))
    from models import User  # pyright: ignore[reportMissingImports]

    user = db.session.get(User, session["user_id"])
    trigger_data = {
        "triggered_by": user.email if user else "unknown",
        "triggered_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(trigger_path, "w") as fh:
        json.dump(trigger_data, fh)
    flash(_("Upgrade triggered. The service will restart shortly."), "info")
    return redirect(url_for("config.index"))


@config_bp.route("/upgrade-status")
def upgrade_status() -> ResponseReturnValue:
    if not session.get("user_id"):
        abort(403)
    upgrade_dir = os.environ.get("OPENHANGAR_UPGRADE_DIR", "")
    if not upgrade_dir:
        return abort(404)
    done_path = os.path.join(upgrade_dir, "trigger.done")
    failed_path = os.path.join(upgrade_dir, "trigger.failed")
    running_path = os.path.join(upgrade_dir, "trigger.running")
    trigger_path = os.path.join(upgrade_dir, "trigger")
    if os.path.exists(done_path):
        with contextlib.suppress(OSError):
            os.remove(done_path)
        return jsonify({"status": "done"})
    if os.path.exists(failed_path):
        msg = ""
        with contextlib.suppress(OSError):
            with open(failed_path) as fh:
                msg = fh.read().strip()
            os.remove(failed_path)
        return jsonify({"status": "failed", "message": msg})
    if os.path.exists(running_path):
        return jsonify({"status": "in-progress"})
    if os.path.exists(trigger_path):
        return jsonify({"status": "triggered"})
    return jsonify({"status": "idle"})


# ── Phase 29: Tenant management (instance admin only) ─────────────────────────


@config_bp.route("/tenants")
@require_instance_admin
def tenant_list() -> ResponseReturnValue:
    from models import Aircraft, Role, Tenant, TenantUser  # pyright: ignore[reportMissingImports]

    tenants = Tenant.query.order_by(Tenant.created_at).all()
    stats = []
    for t in tenants:
        user_count = TenantUser.query.filter_by(tenant_id=t.id).count()
        aircraft_count = Aircraft.query.filter_by(tenant_id=t.id).count()
        owners = (
            TenantUser.query.filter_by(tenant_id=t.id)
            .filter(TenantUser.role.in_([Role.OWNER, Role.ADMIN]))
            .all()
        )
        stats.append(
            {
                "tenant": t,
                "user_count": user_count,
                "aircraft_count": aircraft_count,
                "owners": owners,
            }
        )

    return render_template("config/tenant_list.html", stats=stats)


@config_bp.route("/tenants/create", methods=["GET", "POST"])
@require_instance_admin
def tenant_create() -> ResponseReturnValue:
    from datetime import timedelta

    from models import OperatingModel, Role, Tenant, TenantProfile, User, UserInvitation  # pyright: ignore[reportMissingImports]

    user = db.session.get(User, session["user_id"])
    assert user is not None  # guaranteed by @require_instance_admin

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        admin_email = request.form.get("admin_email", "").strip().lower()
        model_str = request.form.get("operating_model", "sole_operator")

        if not name:
            flash(_("Tenant name is required."), "danger")
            return render_template("config/tenant_create.html")
        if not admin_email:
            flash(_("Admin email is required."), "danger")
            return render_template("config/tenant_create.html")

        tenant = Tenant(name=name, is_active=True)
        db.session.add(tenant)
        db.session.flush()

        try:
            op_model: OperatingModel | None = OperatingModel(model_str)
        except ValueError:
            op_model = None

        profile = TenantProfile(
            tenant_id=tenant.id,
            operating_model=op_model,
            setup_complete=False,
        )
        db.session.add(profile)

        invitation = UserInvitation(
            tenant_id=tenant.id,
            invited_by_user_id=user.id,
            email=admin_email,
            role=Role.OWNER,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.session.add(invitation)
        db.session.commit()

        flash(
            _(
                "Tenant '%(name)s' created. Share this invite link with the owner: %(url)s",
                name=name,
                url=url_for(
                    "users.accept_invite", token=invitation.token, _external=True
                ),
            ),
            "success",
        )
        return redirect(url_for("config.tenant_list"))

    return render_template("config/tenant_create.html")


@config_bp.route("/tenants/<int:tenant_id>/toggle", methods=["POST"])
@require_instance_admin
def tenant_toggle_active(tenant_id: int) -> ResponseReturnValue:
    from models import Tenant  # pyright: ignore[reportMissingImports]

    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        abort(404)

    tenant.is_active = not tenant.is_active
    db.session.commit()

    if tenant.is_active:
        flash(_("Tenant '%(name)s' reactivated.", name=tenant.name), "success")
    else:
        flash(_("Tenant '%(name)s' deactivated.", name=tenant.name), "warning")

    return redirect(url_for("config.tenant_list"))


@config_bp.route("/tenants/<int:tenant_id>/reset-password", methods=["POST"])
@require_instance_admin
def tenant_reset_owner_password(tenant_id: int) -> ResponseReturnValue:
    from datetime import timedelta

    from models import PasswordResetToken, Role, Tenant, TenantUser, User  # pyright: ignore[reportMissingImports]

    admin = db.session.get(User, session["user_id"])
    assert admin is not None  # guaranteed by @require_instance_admin
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        abort(404)

    owner_user_id = request.form.get("owner_user_id", type=int)
    if not owner_user_id:
        flash(_("No user selected."), "danger")
        return redirect(url_for("config.tenant_list"))

    tu = TenantUser.query.filter_by(tenant_id=tenant_id, user_id=owner_user_id).first()
    if not tu or tu.role not in (Role.OWNER, Role.ADMIN):
        abort(403)

    token = PasswordResetToken(
        user_id=owner_user_id,
        generated_by_user_id=admin.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.session.add(token)
    db.session.commit()

    reset_url = url_for("auth.reset_password", token=token.token, _external=True)
    return render_template(
        "config/tenant_reset_token.html",
        tenant=tenant,
        reset_url=reset_url,
        expires_at=token.expires_at,
    )


@config_bp.route("/notifications/", methods=["GET", "POST"])
@login_required
def notification_preferences() -> ResponseReturnValue:
    """Manage per-user notification preferences. Accessible to all logged-in users."""
    from models import (  # pyright: ignore[reportMissingImports]
        NotificationPreference,
        NotificationType,
        Role,
        TenantNotificationDefault,
        TenantProfile,
        TenantUser,
        User,
    )
    from utils import current_user_role  # pyright: ignore[reportMissingImports]

    user = db.session.get(User, session["user_id"])
    if not user:
        abort(403)

    tu = TenantUser.query.filter_by(user_id=user.id).first()
    tenant_id = tu.tenant_id if tu else None
    role = current_user_role()

    is_owner = role in (Role.ADMIN, Role.OWNER)
    is_pilot = (
        role in (Role.ADMIN, Role.OWNER, Role.PILOT, Role.INSTRUCTOR) or user.is_pilot
    )
    is_maint = (
        role in (Role.ADMIN, Role.OWNER, Role.MAINTENANCE, Role.INSTRUCTOR)
        or user.is_maintenance
    )

    def _user_has_cap(caps: list[str]) -> bool:
        return (
            ("is_owner" in caps and is_owner)
            or ("is_pilot" in caps and is_pilot)
            or ("is_maint" in caps and is_maint)
        )

    visible_types = [
        t
        for t in NotificationType.ALL
        if _user_has_cap(NotificationType.REQUIRED_CAPS.get(t, []))
    ]

    if request.method == "POST":
        if tenant_id is None:
            flash(_("Cannot save: no tenant associated."), "danger")
            return redirect(url_for("config.notification_preferences"))

        for notif_type in visible_types:
            enabled = bool(request.form.get(f"enabled_{notif_type}"))
            threshold_raw = request.form.get(f"threshold_{notif_type}", "").strip()
            threshold_days: int | None = None
            if notif_type in NotificationType.HAS_THRESHOLD and threshold_raw:
                try:
                    threshold_days = max(1, int(threshold_raw))
                except ValueError:
                    threshold_days = None

            existing = NotificationPreference.query.filter_by(
                user_id=user.id, tenant_id=tenant_id, notification_type=notif_type
            ).first()
            # Only save if the user's preference differs from the effective default
            system_default = NotificationType.SYSTEM_DEFAULTS.get(notif_type, {})
            same_as_default = enabled == system_default.get(
                "enabled", False
            ) and threshold_days == system_default.get("threshold_days")
            if same_as_default and existing:
                db.session.delete(existing)
            elif not same_as_default:
                if existing:
                    existing.enabled = enabled
                    existing.threshold_days = threshold_days
                else:
                    db.session.add(
                        NotificationPreference(
                            user_id=user.id,
                            tenant_id=tenant_id,
                            notification_type=notif_type,
                            enabled=enabled,
                            threshold_days=threshold_days,
                        )
                    )
        db.session.commit()
        flash(_("Notification preferences saved."), "success")
        return redirect(url_for("config.notification_preferences"))

    # Build current effective preferences for display
    prefs: dict[str, dict[str, object]] = {}
    for notif_type in visible_types:
        if tenant_id:
            from services.notification_service import get_effective_preference  # pyright: ignore[reportMissingImports]

            prefs[notif_type] = get_effective_preference(user.id, tenant_id, notif_type)
        else:
            prefs[notif_type] = dict(
                NotificationType.SYSTEM_DEFAULTS.get(
                    notif_type, {"enabled": False, "threshold_days": None}
                )
            )

    # Tenant defaults visible only to admins/owners
    tenant_defaults: dict[str, dict[str, object]] | None = None
    if is_owner and tenant_id:
        tenant_defaults = {}
        for notif_type in NotificationType.ALL:
            td = TenantNotificationDefault.query.filter_by(
                tenant_id=tenant_id, notification_type=notif_type
            ).first()
            if td:
                tenant_defaults[notif_type] = {
                    "enabled": td.enabled,
                    "threshold_days": td.threshold_days,
                }
            else:
                tenant_defaults[notif_type] = dict(
                    NotificationType.SYSTEM_DEFAULTS.get(
                        notif_type, {"enabled": False, "threshold_days": None}
                    )
                )

    profile = (
        TenantProfile.query.filter_by(tenant_id=tenant_id).first()
        if tenant_id
        else None
    )

    return render_template(
        "config/notifications.html",
        visible_types=visible_types,
        prefs=prefs,
        has_threshold=NotificationType.HAS_THRESHOLD,
        system_defaults=NotificationType.SYSTEM_DEFAULTS,
        tenant_defaults=tenant_defaults,
        is_owner=is_owner,
        profile=profile,
    )


@config_bp.route("/backfill/aircraft-type-icao", methods=["POST"])
@require_instance_admin
def backfill_aircraft_type_icao() -> ResponseReturnValue:
    """Resolve aircraft_type_icao for all logbook entries that have aircraft_type but no icao designator."""
    from models import PilotLogbookEntry  # pyright: ignore[reportMissingImports]
    from utils import resolve_aircraft_type_icao  # pyright: ignore[reportMissingImports]

    rows = PilotLogbookEntry.query.filter(
        PilotLogbookEntry.aircraft_type.isnot(None),
        PilotLogbookEntry.aircraft_type_icao.is_(None),
    ).all()

    updated = 0
    for entry in rows:
        resolved = resolve_aircraft_type_icao(entry.aircraft_type)
        if resolved:
            entry.aircraft_type_icao = resolved
            updated += 1

    db.session.commit()
    flash(
        ngettext(
            "Back-fill complete: one of %(total)d entry resolved.",
            "Back-fill complete: %(updated)d of %(total)d entries resolved.",
            updated,
            updated=updated,
            total=len(rows),
        ),
        "success",
    )
    return redirect(url_for("config.index"))


_ALLOWED_BADGE_PATHS: dict[str, str] = {
    "uptimes/1h/badge.svg": "uptimes/1h/badge.svg",
    "uptimes/24h/badge.svg": "uptimes/24h/badge.svg",
    "uptimes/7d/badge.svg": "uptimes/7d/badge.svg",
    "uptimes/30d/badge.svg": "uptimes/30d/badge.svg",
    "response-times/1h/badge.svg": "response-times/1h/badge.svg",
    "response-times/24h/badge.svg": "response-times/24h/badge.svg",
    "response-times/7d/badge.svg": "response-times/7d/badge.svg",
    "response-times/30d/badge.svg": "response-times/30d/badge.svg",
}


@config_bp.route("/gatus-badge/<path:badge_path>")
@login_required
def gatus_badge(badge_path: str) -> ResponseReturnValue:
    safe_path = _ALLOWED_BADGE_PATHS.get(badge_path)
    if safe_path is None:
        return abort(404)
    gatus = _parse_gatus_env()
    if gatus is None:
        return abort(404)
    base_url, endpoint_key, auth_header = gatus
    badge_url = f"{base_url}/api/v1/endpoints/{endpoint_key}/{safe_path}"
    req = urllib.request.Request(badge_url)
    if auth_header:
        req.add_header("Authorization", f"Basic {auth_header}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310
            content = resp.read()
            content_type = resp.headers.get("Content-Type", "image/svg+xml")
        return current_app.response_class(content, mimetype=content_type)
    except urllib.error.URLError as exc:
        log.warning("Gatus badge fetch failed (%s): %s", badge_url, repr(exc))
        return abort(503)
