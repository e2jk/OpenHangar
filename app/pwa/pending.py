"""Files shared to OpenHangar from another app, kept until a form uses them.

A share (the installed app's share target, POST /pwa/shared) stores its files
under UPLOAD_FOLDER/share_tmp/<token>/, with random names; the session only
holds the token and what each file is. The user then picks a destination:
an aircraft document is saved at once, while an expense receipt, a flight
photo or a maintenance file is offered on that form, pre-attached, and used
when the form is saved. Discarded once used, when another share replaces it,
or by the daily temporary-files cleanup (services.temp_cleanup) after a day.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import shutil
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

from flask import current_app, request, session
from services.upload_layout import SHARE_TMP_DIRNAME
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

SESSION_KEY = "share_pending"
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_SAVED_RE = re.compile(r"^[0-9a-f]{32}(\.[a-z0-9]{1,5})?$")

# Static MIME type → file extension. The stored name and the extension handed
# to the destination form come from the content type, never from the name the
# sharing app sent (which may carry no extension at all).
MIME_TO_EXT: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/plain": ".txt",
}


@dataclass(frozen=True)
class SharedFile:
    original: str  # the name the sharing app gave, for display only
    saved: str  # random name inside the share's folder
    mime: str

    @property
    def upload_name(self) -> str:
        """A safe file name with the extension of its content type."""
        stem = os.path.splitext(secure_filename(self.original))[0][:100] or "shared"
        return f"{stem}{MIME_TO_EXT.get(self.mime, '')}"


@dataclass
class PendingShare:
    token: str
    user_id: int
    title: str = ""
    files: list[SharedFile] = field(default_factory=list)
    destination: str = ""
    aircraft_id: int | None = None

    @property
    def folder(self) -> str:
        return os.path.join(_root(), self.token)

    @property
    def mimetypes(self) -> list[str]:
        return [f.mime for f in self.files]

    def path(self, shared: SharedFile) -> str:
        return os.path.join(self.folder, shared.saved)

    @contextlib.contextmanager
    def upload(self, index: int = 0) -> Iterator[FileStorage]:
        """The shared file as an uploaded file, for the destination's own
        upload handling (its checks and storage layout apply unchanged)."""
        shared = self.files[index]
        with open(self.path(shared), "rb") as stream:
            yield FileStorage(
                stream=stream,
                filename=shared.upload_name,
                content_type=shared.mime,
                name="shared",
            )


def _root() -> str:
    return os.path.join(
        current_app.config.get("UPLOAD_FOLDER", "/data/uploads"), SHARE_TMP_DIRNAME
    )


def _save(share: PendingShare) -> None:
    data = asdict(share)
    session[SESSION_KEY] = data
    session.modified = True


def start(uploads: list[FileStorage], title: str, user_id: int) -> PendingShare:
    """Keep *uploads* for the signed-in user, replacing any earlier share."""
    discard()
    share = PendingShare(token=secrets.token_hex(16), user_id=user_id, title=title)
    os.makedirs(share.folder, mode=0o700, exist_ok=True)
    for upload in uploads:
        original = os.path.basename(upload.filename or "") or "shared"
        mime = (upload.mimetype or "application/octet-stream").lower()
        saved = f"{secrets.token_hex(16)}{MIME_TO_EXT.get(mime, '')}"
        upload.save(os.path.join(share.folder, saved))
        share.files.append(SharedFile(original=original[:255], saved=saved, mime=mime))
    _save(share)
    return share


def _parse(data: Any) -> PendingShare | None:
    if not isinstance(data, dict):
        return None
    token = data.get("token")
    if not isinstance(token, str) or not _TOKEN_RE.match(token):
        return None
    try:
        files = [
            SharedFile(
                original=str(f["original"]), saved=f["saved"], mime=str(f["mime"])
            )
            for f in data.get("files") or []
        ]
        aircraft_id = data.get("aircraft_id")
        share = PendingShare(
            token=token,
            user_id=int(data["user_id"]),
            title=str(data.get("title") or ""),
            files=files,
            destination=str(data.get("destination") or ""),
            aircraft_id=int(aircraft_id) if aircraft_id is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not files or not all(
        isinstance(f.saved, str) and _SAVED_RE.match(f.saved) for f in files
    ):
        return None
    return share


def current(
    destination: str | None = None, aircraft_id: int | None = None
) -> PendingShare | None:
    """The signed-in user's pending share, if its files are still there —
    and, when given, only if it was sent to *destination* / *aircraft_id*."""
    share = _parse(session.get(SESSION_KEY))
    if share is None or share.user_id != session.get("user_id"):
        return None
    if not all(os.path.isfile(share.path(f)) for f in share.files):
        return None
    if destination is not None and share.destination != destination:
        return None
    if aircraft_id is not None and share.aircraft_id != aircraft_id:
        return None
    return share


def submitted(destination: str, aircraft_id: int | None = None) -> PendingShare | None:
    """The pending share the form being saved kept attached: offered on it
    (same destination and aircraft), and its "use the shared file" box still
    ticked — with this share's token, not an older one's."""
    share = current(destination, aircraft_id)
    if share is None or request.form.get("use_shared_file") != share.token:
        return None
    return share


def choose(share: PendingShare, destination: str, aircraft_id: int | None) -> None:
    """Remember where the user sent the share; its form picks it up."""
    share.destination = destination
    share.aircraft_id = aircraft_id
    _save(share)


def discard() -> None:
    """Forget the pending share and delete its files."""
    share = _parse(session.pop(SESSION_KEY, None))
    if share is not None:
        shutil.rmtree(share.folder, ignore_errors=True)
