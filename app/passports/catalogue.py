"""Offline catalogue and tenant-scoped, stable identities for selected airfields.

Only referenced fields are materialised. Refreshes never infer closure from a
missing row, overwrite manual overrides, or merge identities by code/proximity.
Callers commit the transaction; no function here performs network access.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from models import PassportAirfieldAlias, PassportAirfieldIdentity, db

CATALOGUE_PATH = Path(__file__).resolve().parent.parent / "data" / "airports.csv"
OVERRIDE_FIELDS = frozenset(
    {
        "name",
        "ident",
        "icao_code",
        "latitude",
        "longitude",
        "country",
        "region",
        "airfield_type",
        "is_closed",
        "military",
        "ulm",
    }
)


class AirfieldConflict(ValueError):
    """Identity/code reconciliation needs explicit operator review."""


@dataclass(frozen=True)
class CatalogueAirfield:
    source_id: str
    ident: str
    icao_code: str | None
    name: str
    latitude: float | None
    longitude: float | None
    country: str | None
    region: str | None
    airfield_type: str
    is_closed: bool


def _coordinates(latitude: Any, longitude: Any) -> tuple[float | None, float | None]:
    if latitude in (None, "") and longitude in (None, ""):
        return None, None
    lat, lon = float(latitude), float(longitude)
    if (
        not math.isfinite(lat)
        or not math.isfinite(lon)
        or not (-90 <= lat <= 90 and -180 <= lon <= 180)
    ):
        raise ValueError("Invalid airfield coordinates")
    return lat, lon


@lru_cache(maxsize=1)
def load_catalogue() -> tuple[CatalogueAirfield, ...]:
    """Retain non-ICAO, closed and coordinate-less entries for builder filters."""
    result = []
    with CATALOGUE_PATH.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if not row.get("id") or not row.get("ident") or not row.get("name"):
                continue
            try:
                lat, lon = _coordinates(
                    row.get("latitude_deg"), row.get("longitude_deg")
                )
            except (TypeError, ValueError):
                lat, lon = None, None
            result.append(
                CatalogueAirfield(
                    source_id=row["id"].strip(),
                    ident=row["ident"].strip().upper(),
                    icao_code=row.get("icao_code", "").strip().upper() or None,
                    name=row["name"].strip(),
                    latitude=lat,
                    longitude=lon,
                    country=row.get("iso_country", "").strip() or None,
                    region=row.get("iso_region", "").strip() or None,
                    airfield_type=row.get("type", "").strip(),
                    is_closed=row.get("type") == "closed",
                )
            )
    return tuple(result)


def catalogue_choices() -> dict[str, list[str]]:
    rows = load_catalogue()
    return {
        key: sorted({str(getattr(row, key)) for row in rows if getattr(row, key)})
        for key in ("country", "region", "airfield_type")
    }


def find_airfields(
    query: str = "",
    *,
    country: str | None = None,
    region: str | None = None,
    types: set[str] | None = None,
    include_closed: bool = False,
) -> list[CatalogueAirfield]:
    """Catalogue selection does not insert any database rows."""
    words = query.casefold().split()
    return [
        row
        for row in load_catalogue()
        if (include_closed or not row.is_closed)
        and (country is None or row.country == country)
        and (region is None or row.region == region)
        and (types is None or row.airfield_type in types)
        and all(
            word in f"{row.ident} {row.icao_code or ''} {row.name}".casefold()
            for word in words
        )
    ]


def resolve_code(
    tenant_id: int,
    code: str,
    on_date: date | None = None,
    *,
    airfield_ids: Any = None,
    private_airfield_ids: Any = None,
) -> PassportAirfieldIdentity | None:
    """Resolve only explicit aliases; ambiguous/reused codes are never guessed."""
    aliases = db.select(PassportAirfieldAlias.airfield_id).where(
        PassportAirfieldAlias.code == code.strip().upper()
    )
    if private_airfield_ids is not None:
        aliases = aliases.where(
            db.or_(
                PassportAirfieldAlias.airfield_id.in_(private_airfield_ids),
                PassportAirfieldAlias.catalogue_source_id.is_not(None),
            )
        )
    if on_date is not None:
        aliases = aliases.where(
            db.or_(
                PassportAirfieldAlias.valid_from.is_(None),
                PassportAirfieldAlias.valid_from <= on_date,
            ),
            db.or_(
                PassportAirfieldAlias.valid_until.is_(None),
                PassportAirfieldAlias.valid_until >= on_date,
            ),
        )
    # Select identities by ID: DISTINCT over the entire row would compare its
    # JSON overrides, for which PostgreSQL deliberately has no equality operator.
    query = PassportAirfieldIdentity.query.filter(
        PassportAirfieldIdentity.tenant_id == tenant_id,
        PassportAirfieldIdentity.id.in_(aliases),
    )
    if airfield_ids is not None:
        query = query.filter(PassportAirfieldIdentity.id.in_(airfield_ids))
    matches = query.all()
    if len(matches) > 1:
        raise AirfieldConflict("Ambiguous airfield code; review its dated aliases")
    return matches[0] if matches else None


def add_alias(
    airfield: PassportAirfieldIdentity,
    code: str,
    *,
    valid_from: date | None = None,
    valid_until: date | None = None,
    catalogue_source_id: str | None = None,
) -> PassportAirfieldAlias:
    """Explicit dated reconciliation may retain a reused code on two identities."""
    code = code.strip().upper()
    if (
        not code
        or len(code) > 64
        or (valid_from and valid_until and valid_from > valid_until)
    ):
        raise ValueError("Invalid airfield alias")
    alias = PassportAirfieldAlias(
        airfield_id=airfield.id,
        code=code,
        catalogue_source_id=catalogue_source_id,
        valid_from=valid_from,
        valid_until=valid_until,
    )
    db.session.add(alias)
    return alias


def materialise(tenant_id: int, row: CatalogueAirfield) -> PassportAirfieldIdentity:
    field: PassportAirfieldIdentity | None = PassportAirfieldIdentity.query.filter_by(
        tenant_id=tenant_id, source="ourairports", source_id=row.source_id
    ).first()
    # Validate before mutation so rejected refreshes cannot partly replace data.
    codes = {code for code in (row.ident, row.icao_code) if code}
    for code in codes:
        other = resolve_code(tenant_id, code)
        if other is not None and (field is None or other.id != field.id):
            raise AirfieldConflict(
                "Code belongs to another identity; review source replacement or reuse"
            )
    if field is None:
        field = PassportAirfieldIdentity(
            tenant_id=tenant_id, source="ourairports", source_id=row.source_id
        )
        db.session.add(field)
    for key, value in asdict(row).items():
        setattr(field, key, value)
    field.source_url = "https://ourairports.com/airports/" + row.ident + "/"
    field.updated_at = datetime.now(UTC)
    db.session.flush()
    aliases = PassportAirfieldAlias.query.filter_by(airfield_id=field.id).all()
    existing = {alias.code for alias in aliases}
    for alias in aliases:
        if alias.code in codes:
            alias.catalogue_source_id = row.source_id
    for code in sorted(codes - existing):
        add_alias(field, code, catalogue_source_id=row.source_id)
    db.session.flush()
    return field


def refresh_referenced(tenant_id: int) -> list[str]:
    """Return source IDs needing review; leave missing rows and conflicts intact."""
    fields = PassportAirfieldIdentity.query.filter_by(
        tenant_id=tenant_id, source="ourairports"
    ).all()
    rows = {row.source_id: row for row in load_catalogue()}
    conflicts = []
    for field in fields:
        row = rows.get(field.source_id)
        if row is not None:
            try:
                materialise(tenant_id, row)
            except AirfieldConflict:
                conflicts.append(field.source_id)
    return conflicts


def public_airfield(field: PassportAirfieldIdentity) -> dict[str, Any]:
    """The retained public source values, without tenant corrections/provenance."""
    return {
        **{key: getattr(field, key, None) for key in OVERRIDE_FIELDS},
        "source_id": field.source_id,
    }


def public_candidate(field: PassportAirfieldIdentity) -> CatalogueAirfield:
    """A saved public reference, keeping source values across catalogue refreshes."""
    return CatalogueAirfield(
        source_id=field.source_id or "",
        ident=field.ident or "",
        icao_code=field.icao_code,
        name=field.name,
        latitude=float(field.latitude) if field.latitude is not None else None,
        longitude=float(field.longitude) if field.longitude is not None else None,
        country=field.country,
        region=field.region,
        airfield_type=field.airfield_type or "",
        is_closed=field.is_closed,
    )


def effective_airfield(field: PassportAirfieldIdentity) -> dict[str, Any]:
    """Source values plus explicit corrections; absent military/ULM is unknown."""
    result = {key: getattr(field, key, None) for key in OVERRIDE_FIELDS}
    result.update(field.overrides or {})
    return result


def correct_airfield(field: PassportAirfieldIdentity, values: dict[str, Any]) -> None:
    """Apply only explicit manual corrections; source metadata remains intact."""
    if values.keys() - OVERRIDE_FIELDS:
        raise ValueError("Unknown airfield correction")
    merged = {**(field.overrides or {}), **values}
    current = {**effective_airfield(field), **values}
    _coordinates(current["latitude"], current["longitude"])
    if (
        not isinstance(current["name"], str)
        or not current["name"].strip()
        or len(current["name"]) > 255
    ):
        raise ValueError("Invalid airfield name")
    # JSON must not contain Decimal objects from the source Numeric columns.
    for key in ("latitude", "longitude"):
        if key in merged and merged[key] is not None:
            merged[key] = float(merged[key])
    field.overrides = merged


def create_custom(
    tenant_id: int,
    name: str,
    *,
    code: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> PassportAirfieldIdentity:
    lat, lon = _coordinates(latitude, longitude)
    if not name.strip() or len(name.strip()) > 255:
        raise ValueError("Invalid airfield name")
    normalised = code.strip().upper() if code else None
    if normalised and (
        len(normalised) > 64 or resolve_code(tenant_id, normalised) is not None
    ):
        raise AirfieldConflict("Code is already assigned or too long")
    field = PassportAirfieldIdentity(
        tenant_id=tenant_id,
        source="manual",
        name=name.strip(),
        ident=normalised,
        latitude=lat,
        longitude=lon,
    )
    db.session.add(field)
    db.session.flush()
    if normalised:
        add_alias(field, normalised)
    return field


def reconcile_custom(
    field: PassportAirfieldIdentity, row: CatalogueAirfield
) -> PassportAirfieldIdentity:
    """Explicitly adopt a source identity, preserving custom values as overrides."""
    if field.source != "manual":
        raise AirfieldConflict("Only a custom identity can adopt a catalogue row")
    existing = PassportAirfieldIdentity.query.filter_by(
        tenant_id=field.tenant_id, source="ourairports", source_id=row.source_id
    ).first()
    if existing is not None:
        raise AirfieldConflict(
            "Source identity already exists; review a merge explicitly"
        )
    for code in {value for value in (row.ident, row.icao_code) if value}:
        other = resolve_code(field.tenant_id, code)
        if other is not None and other.id != field.id:
            raise AirfieldConflict("Source code belongs to another airfield")
    # All conflicts checked before changing the original identity.
    field.overrides = {
        **{
            key: value
            for key, value in effective_airfield(field).items()
            if value is not None
        },
        **(field.overrides or {}),
    }
    field.overrides = {
        key: float(value) if key in {"latitude", "longitude"} else value
        for key, value in field.overrides.items()
    }
    field.source = "ourairports"
    field.source_id = row.source_id
    return materialise(field.tenant_id, row)
