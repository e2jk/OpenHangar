"""Portable participant-owned data; group ownership never expands the export."""

import json
import tempfile
import zipfile
from datetime import UTC, datetime
from typing import Any, BinaryIO, cast

from models import (
    Passport,
    PassportAirfield,
    PassportAirfieldComment,
    PassportAirfieldIdentity,
    PassportArrivalExclusion,
    PassportParticipant,
    PassportStampPhoto,
    PassportVisit,
    PassportVoucherAllocation,
    PassportVoucherRedemption,
    db,
)

from passports import catalogue
from passports.catalogue_access import visible_identity_ids
from passports.eligibility import progress_many
from passports.evidence import photo_path


def _values(row: Any, names: str) -> dict[str, Any]:
    return {name: getattr(row, name) for name in names.split()}


def account_archive(
    tenant_id: int, user_id: int, visible_passports: list[Passport]
) -> BinaryIO:
    """Export this tenant's own records, including retained withdrawn evidence.

    Current group metadata/completion is included only for currently accessible
    passports. Retained evidence does not grant access to a group's later edits.
    Photos spill to a temporary file rather than accumulating in memory.
    """
    participants = (
        PassportParticipant.query.join(Passport)
        .filter(Passport.tenant_id == tenant_id, PassportParticipant.user_id == user_id)
        .order_by(PassportParticipant.id)
        .all()
    )
    participant_ids = [p.id for p in participants]
    visible_passports = [p for p in visible_passports if p.tenant_id == tenant_id]
    visible_ids = {p.id for p in visible_passports}
    visits = (
        PassportVisit.query.filter(PassportVisit.participant_id.in_(participant_ids))
        .order_by(PassportVisit.id)
        .all()
    )
    allocations = (
        PassportVoucherAllocation.query.join(Passport)
        .filter(
            Passport.tenant_id == tenant_id,
            PassportVoucherAllocation.personal_user_id == user_id,
        )
        .order_by(PassportVoucherAllocation.id)
        .all()
    )
    events = (
        PassportVoucherRedemption.query.filter(
            PassportVoucherRedemption.participant_id.in_(participant_ids)
        )
        .order_by(PassportVoucherRedemption.id)
        .all()
    )
    photos = (
        PassportStampPhoto.query.filter(
            PassportStampPhoto.participant_id.in_(participant_ids),
            PassportStampPhoto.uploader_user_id == user_id,
        )
        .order_by(PassportStampPhoto.id)
        .all()
    )
    # A withdrawn participant gets only target references from their own retained
    # records, never a group's current target list or its new private metadata.
    event_allocations = PassportVoucherAllocation.query.filter(
        PassportVoucherAllocation.id.in_([e.allocation_id for e in events])
    ).all()
    retained_targets = {
        row.target_id for row in [*visits, *allocations, *event_allocations, *photos]
    }
    targets = (
        PassportAirfield.query.filter(
            PassportAirfield.tenant_id == tenant_id,
            db.or_(
                PassportAirfield.passport_id.in_(visible_ids),
                PassportAirfield.id.in_(retained_targets),
            ),
        )
        .order_by(PassportAirfield.id)
        .all()
    )
    identities = {
        row.id: row
        for row in PassportAirfieldIdentity.query.filter(
            PassportAirfieldIdentity.id.in_([t.airfield_id for t in targets])
        ).all()
    }
    private = visible_identity_ids(tenant_id, user_id, private_metadata=True)
    private_ids = set(db.session.scalars(private)) if private is not None else None
    fields = []
    for target in targets:
        identity = identities[target.airfield_id]
        can_private = (
            target.passport_id in visible_ids
            and (private_ids is None or identity.id in private_ids)
            and not target.public_catalogue
        )
        data = (
            catalogue.effective_airfield(identity)
            if can_private
            else catalogue.public_airfield(identity)
            if identity.source == "ourairports"
            else {}
        )
        data = {
            key: value
            for key, value in data.items()
            if key in catalogue.OVERRIDE_FIELDS or key == "source_id"
        }
        if target.passport_id in visible_ids:
            data.update(target.classification_overrides or {})
        fields.append(
            {
                **data,
                "target_id": target.id,
                "passport_id": target.passport_id,
                "airfield_id": identity.id,
            }
        )
    summaries = progress_many(visible_passports, user_id)
    document: dict[str, Any] = {
        "format": "openhangar-passport-data",
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "passports": [
            _values(p, "id title edition_label start_date end_date archived_at")
            for p in visible_passports
        ],
        "participations": [
            _values(
                p,
                "id passport_id display_name accepted_at is_active share_visits share_history public_shared_at public_frequency_at public_name",
            )
            for p in participants
        ],
        "targets": fields,
        "visits": [
            _values(
                v,
                "id passport_id participant_id target_id visit_date note flight_id evidence_kind created_at updated_at",
            )
            for v in visits
        ],
        "arrival_exclusions": [
            _values(e, "participant_id flight_id")
            for e in PassportArrivalExclusion.query.filter(
                PassportArrivalExclusion.participant_id.in_(participant_ids)
            )
            .order_by(PassportArrivalExclusion.id)
            .all()
        ],
        "voucher_allocations": [
            _values(a, "id passport_id target_id quantity valid_from valid_until")
            for a in allocations
        ],
        "voucher_events": [
            _values(
                e,
                "id passport_id allocation_id participant_id redemption_date quantity flight_id saving_amount currency note replaces_id voided_at created_at",
            )
            for e in events
        ],
        "current_completion": [
            {
                "passport_id": p.id,
                "airfields": [
                    {
                        "target_id": row["target_id"],
                        "visited": row["visited"],
                        "count": row["count"],
                        "events": [
                            {
                                key: event.get(key)
                                for key in (
                                    "kind",
                                    "id",
                                    "date",
                                    "time",
                                    "flight_id",
                                    "note",
                                    "excluded",
                                )
                            }
                            for event in row["events"]
                        ],
                    }
                    for row in summaries[p.id]["rows"]
                ],
            }
            for p in visible_passports
        ],
        "comments": [
            _values(
                comment,
                "id series_id airfield_id author_display_name category body experience_date created_at edited_at",
            )
            for comment in PassportAirfieldComment.query.filter_by(
                tenant_id=tenant_id, author_user_id=user_id
            )
            .order_by(PassportAirfieldComment.id)
            .all()
        ],
        "photos": [],
    }
    # The download response owns this file and closes it after transmission.
    output = tempfile.SpooledTemporaryFile(  # noqa: SIM115
        max_size=4 * 1024 * 1024, mode="w+b"
    )
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for photo in photos:
                entry = _values(
                    photo,
                    "id passport_id participant_id target_id public_consent public_shared_at group_shared_at created_at",
                )
                entry["file"] = "photos/" + str(photo.id) + ".jpg"
                try:
                    archive.write(
                        photo_path(photo.filename),
                        entry["file"],
                        compress_type=zipfile.ZIP_STORED,
                    )
                    entry["included"] = True
                except FileNotFoundError:
                    # Lost/concurrently removed photos must not prevent recovering
                    # the participant's other records.
                    entry["included"] = False
                document["photos"].append(entry)
            archive.writestr(
                "passport-data.json",
                json.dumps(document, ensure_ascii=False, indent=2, default=str),
            )
        output.seek(0)
        return cast(BinaryIO, output)
    except Exception:
        output.close()
        raise
