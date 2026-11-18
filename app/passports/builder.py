"""Selection/review/commit workflow over bounded server-side passport drafts."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from flask_babel import gettext as _
from models import (
    Passport,
    PassportAirfield,
    PassportAirfieldAlias,
    PassportAirfieldIdentity,
    PassportDraft,
    db,
)
from werkzeug.datastructures import MultiDict

from passports import catalogue as cat
from passports import imports
from passports.drafts import MAX_TARGETS, save_draft
from passports.management import (
    METADATA_FIELDS,
    BuilderError,
    completion_preview,
    create_personal,
    fingerprint,
    metadata_values,
    owned_passport,
    protected_targets,
)

VISIBLE_LIMIT = 500


def _classification(value: str) -> bool | None:
    if value not in ("unknown", "yes", "no"):
        raise BuilderError(_("Choose yes, no or unknown for each classification."))
    return {"unknown": None, "yes": True, "no": False}[value]


def _field_descriptor(field: PassportAirfieldIdentity) -> dict[str, Any]:
    data = cat.effective_airfield(field)
    return {
        **data,
        "id": field.id,
        "key": f"field:{field.id}",
        "latitude": float(data["latitude"]) if data["latitude"] is not None else None,
        "longitude": float(data["longitude"])
        if data["longitude"] is not None
        else None,
    }


def available_fields(tenant_id: int) -> dict[str, dict[str, Any]]:
    materialised = PassportAirfieldIdentity.query.filter_by(tenant_id=tenant_id).all()
    by_source = {
        field.source_id: field
        for field in materialised
        if field.source == "ourairports"
    }
    result = {f"field:{field.id}": _field_descriptor(field) for field in materialised}
    for row in cat.load_catalogue():
        key = f"catalogue:{row.source_id}"
        result[key] = (
            result[f"field:{by_source[row.source_id].id}"]
            if row.source_id in by_source
            else {**vars(row), "key": key, "id": None, "military": None, "ulm": None}
        )
    return result


def selected_fields(
    tenant_id: int, state: dict[str, Any], *, allow_missing: bool = False
) -> list[dict[str, Any]]:
    if not state["selected"]:
        return []
    available = available_fields(tenant_id)
    selected = []
    seen = set()
    for key in state["selected"]:
        field: dict[str, Any]
        if key.startswith("custom:"):
            if key not in state["custom"]:
                raise BuilderError(
                    _("An airfield reference is invalid. Reopen the builder.")
                )
            field = {**state["custom"][key], "key": key, "id": None}
        else:
            candidate = available.get(key)
            if candidate is None:
                if not allow_missing:
                    raise BuilderError(
                        _("An airfield is no longer available. Review your selection."),
                        409,
                    )
                candidate = {
                    "key": key,
                    "id": None,
                    "name": key,
                    "ident": None,
                    "latitude": None,
                    "longitude": None,
                    "is_closed": False,
                    "military": None,
                    "ulm": None,
                }
            field = candidate
        if field["key"] not in seen:
            selected.append({**field, **state["classifications"].get(key, {})})
            seen.add(field["key"])
    return selected


def matching_fields(tenant_id: int, state: dict[str, Any]) -> list[dict[str, Any]]:
    filters = state["filters"]
    if not (
        filters["countries"] or filters["regions"] or len(filters["query"].strip()) >= 2
    ):
        return []
    matches = {}
    for field in available_fields(tenant_id).values():
        if field["key"] in matches:
            continue
        if (
            (filters["countries"] and field.get("country") not in filters["countries"])
            or (filters["regions"] and field.get("region") not in filters["regions"])
            or field.get("airfield_type") not in filters["types"]
            or (field.get("is_closed") and not filters["closed"])
        ):
            continue
        haystack = f"{field.get('ident') or ''} {field.get('icao_code') or ''} {field['name']}".casefold()
        if not all(word in haystack for word in filters["query"].casefold().split()):
            continue
        if any(
            filters[key] != "any" and field.get(key) != _classification(filters[key])
            for key in ("military", "ulm")
        ):
            continue
        matches[field["key"]] = field
    return sorted(
        matches.values(), key=lambda field: (field["name"].casefold(), field["key"])
    )


def update_form(state: dict[str, Any], form: MultiDict[str, str]) -> None:
    if state.get("edition_copy") and form.get("edition_copy_options") == "yes":
        state["edition_copy"]["copy_allocations"] = (
            form.get("copy_allocations") == "yes"
        )
    for key in METADATA_FIELDS:
        if key in form:
            if len(form[key]) > 10000:
                raise BuilderError(_("A passport field is too long."))
            state["metadata"][key] = form[key]
    visible = set(state["visible"])
    chosen = set(form.getlist("choices"))
    if not chosen <= visible:
        raise BuilderError(_("An airfield selection was not offered by this draft."))
    state["selected"] = sorted((set(state["selected"]) - visible) | chosen)
    for key in state["selected"]:
        for name in ("military", "ulm"):
            form_key = name + "_" + key
            if form_key in form:
                state["classifications"].setdefault(key, {})[name] = _classification(
                    form[form_key]
                )


def set_filters(state: dict[str, Any], form: MultiDict[str, str]) -> None:
    choices = cat.catalogue_choices()
    filters: dict[str, Any] = {}
    for key, choice in (
        ("countries", "country"),
        ("regions", "region"),
        ("types", "airfield_type"),
    ):
        values = form.getlist(key)
        if not set(values) <= set(choices[choice]):
            raise BuilderError(_("Choose valid catalogue filters."))
        filters[key] = values
    query = form.get("query", "").strip()
    if len(query) > 255:
        raise BuilderError(_("The airfield search is too long."))
    filters.update(query=query, closed="closed" in form)
    for key in ("military", "ulm"):
        filters[key] = form.get("filter_" + key, "any")
        if filters[key] != "any":
            _classification(filters[key])
    state["filters"] = filters


def add_codes(tenant_id: int, state: dict[str, Any], raw: str) -> None:
    if len(raw) > 50000:
        raise BuilderError(_("The pasted airfield list is too long."))
    by_code: dict[str, set[str]] = {}
    for field in available_fields(tenant_id).values():
        for code in (field.get("ident"), field.get("icao_code")):
            if code:
                by_code.setdefault(code.upper(), set()).add(field["key"])
    aliases: dict[str, set[str]] = {}
    for code, airfield_id in (
        db.session.query(PassportAirfieldAlias.code, PassportAirfieldAlias.airfield_id)
        .join(PassportAirfieldIdentity)
        .filter(PassportAirfieldIdentity.tenant_id == tenant_id)
        .all()
    ):
        aliases.setdefault(code, set()).add(f"field:{airfield_id}")
    selected, unresolved = set(state["selected"]), []
    for code in dict.fromkeys(re.split(r"[\s,;]+", raw.strip().upper())):
        if not code:
            continue
        matches = by_code.get(code, set())
        alias_matches = aliases.get(code, set())
        matches = matches | alias_matches if len(alias_matches) <= 1 else set()
        if len(matches) == 1 and code != "ZZZZ":
            selected.update(matches)
        else:
            unresolved.append(code)
    if len(selected) > MAX_TARGETS:
        raise BuilderError(
            _("This passport selection is too large. Narrow your selection.")
        )
    state["selected"] = sorted(selected)
    state["unresolved"] = unresolved
    state["codes"] = "\n".join(unresolved)


def add_custom(state: dict[str, Any], form: MultiDict[str, str]) -> None:
    name, code = (
        form.get("custom_name", "").strip(),
        form.get("custom_code", "").strip().upper(),
    )
    if not name or len(name) > 255 or len(code) > 64 or code == "ZZZZ":
        raise BuilderError(_("Enter a valid custom airfield name and optional code."))
    try:
        lat, lon = cat._coordinates(
            form.get("custom_latitude", ""), form.get("custom_longitude", "")
        )
    except (TypeError, ValueError) as exc:
        raise BuilderError(
            _("Enter both valid coordinates, or leave both empty.")
        ) from exc
    key = "custom:" + secrets.token_hex(8)
    state["custom"][key] = {
        "name": name,
        "ident": code or None,
        "icao_code": None,
        "latitude": lat,
        "longitude": lon,
        "country": None,
        "region": None,
        "airfield_type": "small_airport",
        "is_closed": False,
        "military": None,
        "ulm": None,
    }
    state["selected"].append(key)


def review(
    tenant_id: int,
    user_id: int,
    passport: Passport | None,
    draft: PassportDraft,
    state: dict[str, Any],
) -> dict[str, Any]:
    if fingerprint(passport) != draft.base_fingerprint:
        raise BuilderError(
            _("This passport changed while you were editing it. Reopen the builder."),
            409,
        )
    if state["unresolved"]:
        raise BuilderError(
            _("Correct the unknown or ambiguous codes, or explicitly drop them.")
        )
    if state.get("edition_copy"):
        from passports.editions import validate_copy

        validate_copy(tenant_id, user_id, state["edition_copy"])
    metadata = metadata_values(state["metadata"])
    if passport is not None:
        from passports.groups import validate_dates as validate_shared_dates
        from passports.vouchers import validate_dates

        validate_shared_dates(passport, metadata["start_date"], metadata["end_date"])
        validate_dates(passport, metadata["start_date"], metadata["end_date"])
    fields = selected_fields(tenant_id, state)
    old_targets = (
        PassportAirfield.query.filter_by(passport_id=passport.id).all()
        if passport
        else []
    )
    kept = {field["id"] for field in fields if field.get("id")}
    removed = [target for target in old_targets if target.airfield_id not in kept]
    protected = (
        protected_targets(passport.id, {target.id for target in removed})
        if passport
        else set()
    )
    old_fields = [
        _field_descriptor(field)
        for field in PassportAirfieldIdentity.query.filter(
            PassportAirfieldIdentity.id.in_(
                [target.airfield_id for target in old_targets]
            )
        ).all()
    ]
    if passport is not None:
        from passports.vouchers import protected_targets as voucher_targets

        if voucher_targets(passport.id, {target.id for target in removed}):
            raise BuilderError(
                _(
                    "Keep airfields with voucher allocations. Archive this edition or delete unused allocations first."
                ),
                409,
            )
    before = (
        completion_preview(
            passport,
            user_id,
            old_fields,
            {key: getattr(passport, key) for key in METADATA_FIELDS},
        )
        if passport
        else None
    )
    after = completion_preview(passport, user_id, fields, metadata, tenant_id=tenant_id)
    allocation_preview = []
    voucher_default = 0
    if state.get("edition_copy", {}).get("copy_allocations"):
        from passports.vouchers import copy_preview

        source = owned_passport(
            tenant_id, user_id, state["edition_copy"]["source_id"], archived_ok=True
        )
        voucher_default = source.default_voucher_quantity
        allocation_preview = copy_preview(
            source, [field["id"] for field in fields if field.get("id")]
        )
        names = {
            field["id"]: ((str(field["ident"]) + " — ") if field.get("ident") else "")
            + field["name"]
            for field in fields
            if field.get("id")
        }
        for row in allocation_preview:
            row["airfield_name"] = names[row["airfield_id"]]
    result = {
        "metadata": metadata,
        "edition_copy": state.get("edition_copy"),
        "voucher_allocations": allocation_preview,
        "voucher_default": voucher_default,
        "source": imports.source_values(state),
        "fields": fields,
        "removed": len(removed),
        "protected": len(protected),
        "before": before,
        "after": after,
    }
    signature = hashlib.sha256(
        json.dumps(result, default=str, sort_keys=True).encode()
    ).hexdigest()
    return {**result, "signature": signature}


def commit_selection(
    tenant_id: int,
    user_id: int,
    passport: Passport | None,
    draft: PassportDraft,
    state: dict[str, Any],
    confirmed_removal: bool,
) -> Passport:
    impact = review(tenant_id, user_id, passport, draft, state)
    if not state.get("review") or state["review"] != impact["signature"]:
        raise BuilderError(_("Review this selection again before saving."), 409)
    if impact["protected"] and not confirmed_removal:
        raise BuilderError(
            _("Confirm removal of airfields with saved visits or photos.")
        )
    if passport is None:
        passport = create_personal(tenant_id, user_id, impact["metadata"])
        if state.get("edition_copy"):
            from passports.editions import finish_copy

            finish_copy(passport, user_id, state["edition_copy"])
    else:
        for key, value in impact["metadata"].items():
            setattr(passport, key, value)
    for key, value in impact["source"].items():
        setattr(passport, key, value)
    old_targets = {
        target.airfield_id: target
        for target in PassportAirfield.query.filter_by(passport_id=passport.id).all()
    }
    kept: set[int] = set()
    source_rows = {row.source_id: row for row in cat.load_catalogue()}
    for field in impact["fields"]:
        key = field["key"]
        if field.get("id"):
            identity = db.session.get(PassportAirfieldIdentity, field["id"])
            if identity is None:
                raise BuilderError(
                    _("An airfield is no longer available. Review your selection."), 409
                )
        elif key.startswith("catalogue:"):
            identity = cat.materialise(tenant_id, source_rows[key[len("catalogue:") :]])
        else:
            identity = cat.create_custom(
                tenant_id,
                field["name"],
                code=field["ident"],
                latitude=field["latitude"],
                longitude=field["longitude"],
            )
        classifications = {name: field.get(name) for name in ("military", "ulm")}
        existing = cat.effective_airfield(identity)
        if any(existing.get(name) != classifications[name] for name in classifications):
            cat.correct_airfield(identity, classifications)
            identity.overrides = {
                **identity.overrides,
                "classification_provenance": {
                    "source": "manual",
                    "user_id": user_id,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            }
        if identity.id not in kept:
            target = old_targets.get(identity.id)
            if target is None:
                target = PassportAirfield(
                    tenant_id=tenant_id,
                    passport_id=passport.id,
                    airfield_id=identity.id,
                )
                db.session.add(target)
            target.display_order = len(kept)
            kept.add(identity.id)
    for identity_id, target in old_targets.items():
        if identity_id not in kept:
            db.session.delete(target)
    db.session.flush()
    if state.get("edition_copy", {}).get("copy_allocations"):
        from passports.vouchers import copy_allocations

        # finish_copy linked the source, so validate its owner rather than its
        # pre-link fingerprint again; review already locked and validated it.
        source = owned_passport(
            tenant_id, user_id, state["edition_copy"]["source_id"], archived_ok=True
        )
        copy_allocations(source, passport, user_id)
    from passports.vouchers import _provision_defaults

    _provision_defaults(passport)
    db.session.delete(draft)
    db.session.flush()
    return passport


def advance(
    tenant_id: int,
    user_id: int,
    passport: Passport | None,
    draft: PassportDraft,
    form: MultiDict[str, str],
    *,
    state: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Passport | None]:
    state = copy.deepcopy(draft.payload) if state is None else state
    update_form(state, form)
    action = form.get("action", "filter")
    if action == "save":
        return state, commit_selection(
            tenant_id, user_id, passport, draft, state, "confirm_removal" in form
        )
    state["review"] = None
    if action == "preview_import":
        imports.prepare(
            tenant_id,
            state,
            form.get("import_source", "generic"),
            form.get("import_url", ""),
        )
    elif action in {"import_merge", "import_replace"}:
        retained = set()
        if passport is not None and action == "import_replace":
            targets = PassportAirfield.query.filter_by(passport_id=passport.id).all()
            protected = protected_targets(
                passport.id, {target.id for target in targets}
            )
            retained = {
                f"field:{target.airfield_id}"
                for target in targets
                if target.id in protected
            }
        imports.apply(state, replace=action == "import_replace", retained=retained)
    elif action == "cancel_import":
        state["import_preview"] = None
    elif action == "filter":
        set_filters(state, form)
    elif action == "select_all":
        combined = set(state["selected"]) | {
            field["key"] for field in matching_fields(tenant_id, state)
        }
        if len(combined) > MAX_TARGETS:
            raise BuilderError(
                _("This passport selection is too large. Narrow your selection.")
            )
        state["selected"] = sorted(combined)
    elif action == "add_codes":
        add_codes(tenant_id, state, form.get("codes", ""))
    elif action == "drop_unknown":
        state["codes"], state["unresolved"] = "", []
    elif action == "add_custom":
        add_custom(state, form)
    elif action.startswith("remove:"):
        key = action[len("remove:") :]
        if key not in state["selected"]:
            raise BuilderError(
                _("An airfield reference is invalid. Reopen the builder.")
            )
        state["selected"].remove(key)
    elif action == "review":
        if form.get("codes", state["codes"]).strip():
            add_codes(tenant_id, state, form.get("codes", state["codes"]))
        state["review"] = review(tenant_id, user_id, passport, draft, state)[
            "signature"
        ]
    elif action not in ("back", "selection"):
        raise BuilderError(_("Unknown passport builder action."))
    if len(state["selected"]) > MAX_TARGETS:
        raise BuilderError(
            _("This passport selection is too large. Narrow your selection.")
        )
    state["visible"] = (
        []
        if state["review"] or state.get("import_preview")
        else [
            field["key"] for field in matching_fields(tenant_id, state)[:VISIBLE_LIMIT]
        ]
    )
    save_draft(draft, state)
    return state, None
