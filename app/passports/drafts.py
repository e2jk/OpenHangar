"""Expiring, actor-scoped drafts; no selection lists in browser cookies."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from typing import Any

from flask_babel import gettext as _
from models import Passport, PassportAirfield, PassportDraft, db

from passports.management import (
    METADATA_FIELDS,
    BuilderError,
    fingerprint,
    owned_passport,
    require_actor,
)

DRAFT_TTL = 7200
MAX_DRAFTS = 10
MAX_TARGETS = 5000
MAX_PAYLOAD_BYTES = 2_000_000


def new_draft(
    tenant_id: int, user_id: int, passport: Passport | None = None
) -> tuple[str, PassportDraft]:
    require_actor(tenant_id, user_id, creating=passport is None)
    if passport is not None:
        owned_passport(tenant_id, user_id, passport.id)
    now = int(time.time())
    # Only this actor's drafts are pruned, including when the per-user cap is hit.
    existing = (
        PassportDraft.query.filter_by(tenant_id=tenant_id, user_id=user_id)
        .order_by(PassportDraft.expires_at.desc(), PassportDraft.token_hash)
        .all()
    )
    for i, draft in enumerate(existing):
        if draft.expires_at <= now or i >= MAX_DRAFTS - 1:
            db.session.delete(draft)
    token = secrets.token_urlsafe(24)
    metadata = {
        key: str(getattr(passport, key) or "") if passport is not None else ""
        for key in METADATA_FIELDS
    }
    selected = (
        [
            f"field:{row.airfield_id}"
            for row in PassportAirfield.query.filter_by(passport_id=passport.id)
            .order_by(PassportAirfield.display_order, PassportAirfield.id)
            .all()
        ]
        if passport is not None
        else []
    )
    draft = PassportDraft(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        tenant_id=tenant_id,
        user_id=user_id,
        passport_id=passport.id if passport else None,
        base_fingerprint=fingerprint(passport),
        expires_at=now + DRAFT_TTL,
        payload={
            "metadata": metadata,
            "source": {
                "url": passport.source_url,
                "retrieved_at": passport.source_retrieved_at.isoformat(),
            }
            if passport is not None
            and passport.source_url
            and passport.source_retrieved_at
            else None,
            "import_preview": None,
            "selected": selected,
            "custom": {},
            "classifications": {},
            "visible": [],
            "filters": {
                "countries": [],
                "regions": [],
                "types": ["small_airport", "medium_airport", "large_airport"],
                "closed": False,
                "query": "",
                "military": "any",
                "ulm": "any",
            },
            "codes": "",
            "unresolved": [],
            "review": None,
        },
    )
    db.session.add(draft)
    db.session.flush()
    return token, draft


def load_draft(
    token: str,
    tenant_id: int,
    user_id: int,
    passport_id: int | None,
    revision: int | None = None,
) -> PassportDraft:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32}", token):
        raise BuilderError(_("This passport draft has expired. Start again."), 404)
    draft: PassportDraft | None = (
        PassportDraft.query.filter_by(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            tenant_id=tenant_id,
            user_id=user_id,
            passport_id=passport_id,
        )
        .with_for_update()
        .first()
    )
    if draft is None or draft.expires_at <= int(time.time()):
        raise BuilderError(_("This passport draft has expired. Start again."), 404)
    if revision is not None and revision != draft.revision:
        raise BuilderError(
            _("This draft changed in another tab. Reopen the builder."), 409
        )
    return draft


def save_draft(draft: PassportDraft, payload: dict[str, Any]) -> None:
    if (
        len(payload["selected"]) > MAX_TARGETS
        or len(json.dumps(payload).encode()) > MAX_PAYLOAD_BYTES
    ):
        raise BuilderError(
            _("This passport selection is too large. Narrow your selection.")
        )
    draft.payload = payload
    draft.revision += 1
