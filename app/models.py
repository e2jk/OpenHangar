import enum
import secrets
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy  # pyright: ignore[reportMissingImports]

db = SQLAlchemy()


class Role(str, enum.Enum):
    ADMIN = "admin"
    OWNER = "owner"
    PILOT = "pilot"  # Pilot/Renter: log flights + view own; no config/cost edits
    MAINTENANCE = (
        "maintenance"  # Maintenance: view+update maintenance; no flights/aircraft edits
    )
    VIEWER = "viewer"  # Read-only across tenant
    STUDENT = "student"  # Student pilot — requires instructor sign-off on solo entries
    INSTRUCTOR = "instructor"  # Flight instructor — can countersign student entries


class OperatingModel(str, enum.Enum):
    SOLE_PILOT = "sole_pilot"
    SOLE_OPERATOR = "sole_operator"
    SHARED_OWNERSHIP = "shared_ownership"
    FLIGHT_CLUB = "flight_club"
    FLIGHT_SCHOOL = "flight_school"


class PermissionBit:
    """Bitmask constants for UserAircraftAccess.permissions_mask."""

    VIEW_AIRCRAFT = 0x01
    EDIT_AIRCRAFT = 0x02
    READ_MAINT_FULL = 0x04
    READ_MAINT_LIMITED = 0x08
    WRITE_MAINTENANCE = 0x10
    EDIT_COMPONENTS = 0x20
    WRITE_LOGBOOK = 0x40
    RESERVE_AIRCRAFT = 0x80
    ALL = 0xFF

    # Default masks per role (used when no explicit per-aircraft row exists)
    ROLE_DEFAULTS: "dict[str, int]" = {
        "admin": ALL,
        "owner": ALL,
        "pilot": VIEW_AIRCRAFT | READ_MAINT_LIMITED | WRITE_LOGBOOK | RESERVE_AIRCRAFT,
        "student": VIEW_AIRCRAFT | READ_MAINT_LIMITED,
        "instructor": VIEW_AIRCRAFT
        | READ_MAINT_FULL
        | WRITE_LOGBOOK
        | RESERVE_AIRCRAFT,
        "maintenance": VIEW_AIRCRAFT
        | EDIT_AIRCRAFT
        | READ_MAINT_FULL
        | WRITE_MAINTENANCE
        | EDIT_COMPONENTS,
        "viewer": VIEW_AIRCRAFT | READ_MAINT_FULL,
    }


class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    slug = db.Column(db.String(64), nullable=True, unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    require_totp = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    users = db.relationship(
        "TenantUser", back_populates="tenant", cascade="all, delete-orphan"
    )
    aircraft = db.relationship(
        "Aircraft", back_populates="tenant", cascade="all, delete-orphan"
    )


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    totp_secret = db.Column(db.String(64), nullable=True, default=None)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    name = db.Column(db.String(128), nullable=True)
    language = db.Column(db.String(8), nullable=True, default="en")
    theme = db.Column(db.String(8), nullable=True, default=None)
    # Phase 23: capability flags — orthogonal to role; allow cross-role flows
    is_pilot = db.Column(db.Boolean, nullable=False, default=False)
    is_maintenance = db.Column(db.Boolean, nullable=False, default=False)
    view_only = db.Column(db.Boolean, nullable=False, default=False)
    # Phase 29: instance-level super admin — set on the very first user created
    is_instance_admin = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    tenants = db.relationship(
        "TenantUser", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        return (self.name or "").strip() or self.email.split("@")[0]


class TenantUser(db.Model):
    __tablename__ = "tenant_users"

    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    role = db.Column(db.Enum(Role), nullable=False, default=Role.OWNER)

    user = db.relationship("User", back_populates="tenants")
    tenant = db.relationship("Tenant", back_populates="users")


class UserAircraftAccess(db.Model):
    """Grants a non-owner/admin user explicit access to a specific aircraft."""

    __tablename__ = "user_aircraft_access"

    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), primary_key=True
    )
    # Phase 23: optional override mask; when NULL the role's default mask applies
    permissions_mask = db.Column(db.Integer, nullable=True)


class UserAllAircraftAccess(db.Model):
    """Grants a user access to every aircraft in a tenant (past and future).

    Admin users bypass access checks entirely and never need this row.
    For non-admin users, this row grants access to every aircraft in the
    tenant using the supplied permissions_mask (or the role default when NULL).
    """

    __tablename__ = "user_all_aircraft_access"

    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    permissions_mask = db.Column(db.Integer, nullable=True)


class UserInvitation(db.Model):
    """Time-limited invitation for a new user to join a tenant."""

    __tablename__ = "user_invitations"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        default=lambda: secrets.token_urlsafe(32),
    )
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    invited_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    email = db.Column(db.String(255), nullable=True)
    display_name = db.Column(db.String(128), nullable=True)
    role = db.Column(db.Enum(Role), nullable=False, default=Role.PILOT)
    aircraft_ids = db.Column(db.JSON, nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    accepted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    tenant = db.relationship("Tenant")
    invited_by = db.relationship("User", foreign_keys=[invited_by_user_id])

    @property
    def is_expired(self) -> bool:
        exp = self.expires_at
        # SQLite returns naive datetimes; compare with naive UTC in that case
        if exp.tzinfo is None:
            return datetime.now(timezone.utc).replace(tzinfo=None) > exp
        return datetime.now(timezone.utc) > exp

    @property
    def is_accepted(self) -> bool:
        return self.accepted_at is not None


# ── Phase 29: Password Reset Token ───────────────────────────────────────────


class PasswordResetToken(db.Model):
    """One-time password reset token generated by the instance admin for a tenant owner."""

    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        default=lambda: secrets.token_urlsafe(32),
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    generated_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", foreign_keys=[user_id])
    generated_by = db.relationship("User", foreign_keys=[generated_by_user_id])

    @property
    def is_expired(self) -> bool:
        exp = self.expires_at
        if exp.tzinfo is None:
            return datetime.now(timezone.utc).replace(tzinfo=None) > exp
        return datetime.now(timezone.utc) > exp

    @property
    def is_used(self) -> bool:
        return self.used_at is not None


# ── Phase 26: Tenant Profile ─────────────────────────────────────────────────


class TenantProfile(db.Model):
    """Instance-level profile collected during the onboarding wizard."""

    __tablename__ = "tenant_profiles"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    operating_model = db.Column(db.Enum(OperatingModel), nullable=True)
    # 0 → logbook-only (no aircraft UI)
    # 1 → single-aircraft (hides fleet-level widgets)
    # N → show "Add aircraft" CTA until N aircraft exist
    planned_aircraft_count = db.Column(db.Integer, nullable=True)
    allows_rental = db.Column(db.Boolean, nullable=False, default=False)
    club_name = db.Column(db.String(128), nullable=True)
    school_name = db.Column(db.String(128), nullable=True)
    organisation_name = db.Column(db.String(128), nullable=True)
    setup_complete = db.Column(db.Boolean, nullable=False, default=False)
    # Phase 34: optional email subject prefix, e.g. "[MyClub]"
    email_subject_prefix = db.Column(db.String(64), nullable=True)
    # Phase 37c: "off" | "warn" | "block" — enforcement level when a renter
    # (non is_owner user) books an aircraft without a valid RenterAuthorization.
    rental_authorization_policy = db.Column(
        db.String(8), nullable=False, default="warn"
    )

    tenant = db.relationship("Tenant", backref=db.backref("profile", uselist=False))


# ── Phase 1: Aircraft & Component Models ──────────────────────────────────────


# Application-level component type constants.
# Stored as plain strings in the DB so new types never require a migration.
class ComponentType:
    AIRFRAME = "airframe"
    ENGINE = "engine"
    PROPELLER = "propeller"
    AVIONICS = "avionics"
    OTHER = "other"

    ALL = {AIRFRAME, ENGINE, PROPELLER, AVIONICS, OTHER}


class Aircraft(db.Model):
    __tablename__ = "aircraft"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    registration = db.Column(db.String(16), nullable=False)
    make = db.Column(db.String(64), nullable=False)
    model = db.Column(db.String(64), nullable=False)
    year = db.Column(db.Integer, nullable=True)
    has_flight_counter = db.Column(db.Boolean, nullable=False, default=True)
    flight_counter_offset = db.Column(db.Numeric(3, 1), nullable=False, default=0.3)
    fuel_flow = db.Column(
        db.Numeric(6, 2), nullable=True
    )  # typical fuel consumption in L/h
    fuel_type = db.Column(
        db.String(8), nullable=False, default="avgas"
    )  # "avgas" | "jet_a1"
    # Oil consumption warning threshold in L/h; null = no warning on the
    # cost dashboard.
    oil_warning_lph = db.Column(db.Numeric(4, 2), nullable=True)
    insurance_expiry = db.Column(db.Date, nullable=True)
    # Phase 30: GPS import time rounding preference
    logbook_time_precision = db.Column(
        db.String(16), nullable=False, default="tenth_hour"
    )  # "tenth_hour" | "minute"
    # Phase 36: optional engine-overhaul reserve accrual rate, surfaced as a
    # line item on the cost dashboard; null = not configured.
    reserve_hourly_rate = db.Column(db.Numeric(8, 2), nullable=True)
    # Archived (sold/retired) aircraft keep their full history but are hidden
    # from active-fleet views, reservations, and notification passes.
    archived_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    tenant = db.relationship("Tenant", back_populates="aircraft")
    components = db.relationship(
        "Component", back_populates="aircraft", cascade="all, delete-orphan"
    )
    flights = db.relationship(
        "FlightEntry", back_populates="aircraft", cascade="all, delete-orphan"
    )
    maintenance_triggers = db.relationship(
        "MaintenanceTrigger", back_populates="aircraft", cascade="all, delete-orphan"
    )
    expenses = db.relationship(
        "Expense", back_populates="aircraft", cascade="all, delete-orphan"
    )
    documents = db.relationship(
        "Document",
        back_populates="aircraft",
        cascade="all, delete-orphan",
        single_parent=True,
        foreign_keys="Document.aircraft_id",
        primaryjoin="Document.aircraft_id == Aircraft.id",
    )
    share_tokens = db.relationship(
        "ShareToken",
        back_populates="aircraft",
        cascade="all, delete-orphan",
    )
    snags = db.relationship(
        "Snag",
        back_populates="aircraft",
        cascade="all, delete-orphan",
    )
    wb_config = db.relationship(
        "WeightBalanceConfig",
        back_populates="aircraft",
        cascade="all, delete-orphan",
        uselist=False,
    )
    reservations = db.relationship(
        "Reservation",
        back_populates="aircraft",
        cascade="all, delete-orphan",
    )
    booking_settings = db.relationship(
        "AircraftBookingSettings",
        back_populates="aircraft",
        cascade="all, delete-orphan",
        uselist=False,
    )
    photos = db.relationship(
        "AircraftPhoto",
        back_populates="aircraft",
        cascade="all, delete-orphan",
        order_by="AircraftPhoto.sort_order",
    )
    airworthiness_statuses = db.relationship(
        "AirworthinessDocumentStatus",
        back_populates="aircraft",
        cascade="all, delete-orphan",
    )
    installed_stcs = db.relationship(
        "InstalledSTC",
        back_populates="aircraft",
        cascade="all, delete-orphan",
    )

    @property
    def cover_photo(self) -> "AircraftPhoto | None":
        return self.photos[0] if self.photos else None

    @property
    def total_engine_hours(self) -> "float | None":
        """Current engine hours — the highest engine_time_counter_end across all flight entries."""
        val = db.session.execute(
            db.select(db.func.max(FlightEntry.engine_time_counter_end)).where(
                FlightEntry.aircraft_id == self.id
            )
        ).scalar()
        return float(val) if val is not None else None

    @property
    def total_flight_hours(self) -> "float | None":
        """Current flight hours — the highest flight_time_counter_end across all flight entries."""
        val = db.session.execute(
            db.select(db.func.max(FlightEntry.flight_time_counter_end)).where(
                FlightEntry.aircraft_id == self.id
            )
        ).scalar()
        return float(val) if val is not None else None

    @staticmethod
    def engine_hours_by_id(aircraft_ids: "list[int]") -> "dict[int, float | None]":
        """Current engine hours for a whole fleet in one aggregate query.

        Returns an entry for every requested id (None when the aircraft has no
        flight entries or no engine counter values)."""
        totals: dict[int, float | None] = {aid: None for aid in aircraft_ids}
        if not aircraft_ids:
            return totals
        rows = db.session.execute(
            db.select(
                FlightEntry.aircraft_id,
                db.func.max(FlightEntry.engine_time_counter_end),
            )
            .where(FlightEntry.aircraft_id.in_(aircraft_ids))
            .group_by(FlightEntry.aircraft_id)
        ).all()
        for aid, max_end in rows:
            totals[aid] = float(max_end) if max_end is not None else None
        return totals

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def is_grounded(self) -> bool:
        """True when any unresolved grounding snag exists, or insurance has expired."""
        from datetime import date as _date

        if self.insurance_expiry is not None and self.insurance_expiry < _date.today():
            return True
        return any(s.is_grounding and s.is_open for s in self.snags)

    @property
    def insurance_status(self) -> str:
        """Return 'expired', 'expiring_soon' (≤30 days), or 'ok'."""
        from datetime import date as _date

        if self.insurance_expiry is None:
            return "ok"
        delta = (self.insurance_expiry - _date.today()).days
        if delta < 0:
            return "expired"
        if delta <= 30:
            return "expiring_soon"
        return "ok"


class AircraftPhoto(db.Model):
    __tablename__ = "aircraft_photos"
    __table_args__ = (db.Index("ix_aircraft_photos_aircraft_id", "aircraft_id"),)

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    filename = db.Column(db.String(512), nullable=False)
    original_filename = db.Column(db.String(256), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=1)
    uploaded_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    uploaded_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    aircraft = db.relationship("Aircraft", back_populates="photos")


class Component(db.Model):
    """
    A generic aircraft component (engine, propeller, avionics, …).

    Common fields live as columns; type-specific attributes go in `extras` (JSON).
    `removed_at = NULL` means the component is currently installed.
    `position` disambiguates multiple components of the same type, e.g. "left" / "right"
    for a twin-engine aircraft.
    """

    __tablename__ = "components"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    # Plain string, validated at application layer — no DB ENUM so new types
    # never require a schema migration.
    type = db.Column(db.String(32), nullable=False)
    # Optional slot label: "left", "right", "1", "2", "center", …
    position = db.Column(db.String(32), nullable=True)

    make = db.Column(db.String(64), nullable=False)
    model = db.Column(db.String(64), nullable=False)
    serial_number = db.Column(db.String(64), nullable=True)
    # Hours on this component when it was installed on this aircraft
    time_at_install = db.Column(db.Numeric(8, 1), nullable=True)

    installed_at = db.Column(db.Date, nullable=True)
    removed_at = db.Column(db.Date, nullable=True)  # NULL = currently installed

    # Life limits: hours between overhauls (TBO) and/or a calendar life limit
    # (e.g. 12-year rubber hoses).  overhauled_at_hours records the component
    # hours at the last overhaul — it resets the TBO reference point without
    # touching history; overhauled_on is the matching date for the records.
    tbo_hours = db.Column(db.Numeric(8, 1), nullable=True)
    life_limit_date = db.Column(db.Date, nullable=True)
    overhauled_at_hours = db.Column(db.Numeric(8, 1), nullable=True)
    overhauled_on = db.Column(db.Date, nullable=True)

    # Type-specific attributes (blade count, TBO, firmware version, …)
    extras = db.Column(db.JSON, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", back_populates="components")
    documents = db.relationship(
        "Document",
        back_populates="component",
        cascade="all, delete-orphan",
    )
    easa_source_nodes = db.relationship(
        "EASASourceNode",
        back_populates="component",
        cascade="all, delete-orphan",
    )
    airworthiness_documents = db.relationship(
        "AirworthinessDocument",
        back_populates="component",
        cascade="all, delete-orphan",
        foreign_keys="AirworthinessDocument.component_id",
    )


# ── Phase 3: Flight Logging ───────────────────────────────────────────────────


class CrewRole:
    PIC = "PIC"
    IP = "IP"
    SP = "SP"
    COPILOT = "COPILOT"
    STUDENT = "STUDENT"
    ALL = [PIC, IP, SP, COPILOT, STUDENT]
    LABELS = {
        PIC: "PIC",
        IP: "Instructor",
        SP: "Safety Pilot",
        COPILOT: "Co-Pilot",
        STUDENT: "Student",
    }


# ── Phase 31b: GPS Track (standalone, linkable from FlightEntry or PilotLogbookEntry) ──


class GpsTrack(db.Model):
    __tablename__ = "gps_tracks"

    id = db.Column(db.Integer, primary_key=True)
    source_filename = db.Column(db.String(256), nullable=True)
    device_id = db.Column(db.String(64), nullable=True, index=True)
    block_off_utc = db.Column(db.DateTime(timezone=True), nullable=True)
    block_on_utc = db.Column(db.DateTime(timezone=True), nullable=True)
    departure_icao = db.Column(db.String(4), nullable=True)
    arrival_icao = db.Column(db.String(4), nullable=True)
    geojson = db.Column(db.JSON, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    # Render cache for the default (landscape, low-res) single-flight PNG/GIF —
    # geojson never changes once saved, so no invalidation is ever needed.
    cached_png = db.Column(db.LargeBinary, nullable=True)
    cached_gif = db.Column(db.LargeBinary, nullable=True)


class FlightEntry(db.Model):
    __tablename__ = "flight_entries"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    date = db.Column(db.Date, nullable=False)
    departure_icao = db.Column(db.String(4), nullable=False)
    arrival_icao = db.Column(db.String(4), nullable=False)
    departure_time = db.Column(db.Time, nullable=True)
    arrival_time = db.Column(db.Time, nullable=True)
    flight_time = db.Column(db.Numeric(4, 1), nullable=True)
    nature_of_flight = db.Column(db.String(100), nullable=True)
    passenger_count = db.Column(db.Integer, nullable=True)
    landing_count = db.Column(db.Integer, nullable=True)
    flight_time_counter_start = db.Column(db.Numeric(8, 1), nullable=True)
    flight_time_counter_end = db.Column(db.Numeric(8, 1), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    engine_time_counter_start = db.Column(db.Numeric(8, 1), nullable=True)
    engine_time_counter_end = db.Column(db.Numeric(8, 1), nullable=True)
    flight_counter_photo = db.Column(db.String(255), nullable=True)
    engine_counter_photo = db.Column(db.String(255), nullable=True)
    fuel_event = db.Column(db.String(8), nullable=True)  # 'before' | 'after' | None
    fuel_added_qty = db.Column(db.Numeric(8, 2), nullable=True)
    fuel_added_unit = db.Column(db.String(8), nullable=True)
    fuel_remaining_qty = db.Column(db.Numeric(8, 2), nullable=True)
    fuel_photo = db.Column(db.String(255), nullable=True)
    oil_added_l = db.Column(db.Numeric(4, 2), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    # Phase 30: GPS import
    source = db.Column(db.String(32), nullable=True)  # "gps_import" | "import" | None
    gps_import_batch_id = db.Column(
        db.Integer,
        db.ForeignKey("aircraft_gps_import_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Bulk airframe logbook import (CSV/Excel)
    airframe_import_batch_id = db.Column(
        db.Integer,
        db.ForeignKey("airframe_import_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    block_off_utc = db.Column(db.DateTime(timezone=True), nullable=True)
    block_on_utc = db.Column(db.DateTime(timezone=True), nullable=True)
    gps_track_id = db.Column(
        db.Integer,
        db.ForeignKey("gps_tracks.id", ondelete="SET NULL"),
        nullable=True,
    )

    aircraft = db.relationship("Aircraft", back_populates="flights")
    gps_track = db.relationship("GpsTrack", foreign_keys=[gps_track_id])
    gps_import_batch = db.relationship(
        "AircraftGpsImportBatch", foreign_keys=[gps_import_batch_id]
    )
    crew = db.relationship(
        "FlightCrew",
        back_populates="flight",
        cascade="all, delete-orphan",
        order_by="FlightCrew.sort_order",
    )
    expenses = db.relationship("Expense", back_populates="flight_entry")
    documents = db.relationship(
        "Document",
        back_populates="flight_entry",
        cascade="all, delete-orphan",
    )

    # Matches the standard airframe-log ordering (date DESC, id DESC per aircraft).
    __table_args__ = (
        db.Index(
            "ix_flight_entries_aircraft_id_date_id",
            aircraft_id,
            date.desc(),
            id.desc(),
        ),
    )


class FlightCrew(db.Model):
    __tablename__ = "flight_crew"

    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(
        db.Integer,
        db.ForeignKey("flight_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(16), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    flight = db.relationship("FlightEntry", back_populates="crew")
    user = db.relationship("User")

    __table_args__ = (db.Index("ix_flight_crew_flight_id", flight_id),)


# ── Phase 17: Pilot Profile & Manual Logbook ─────────────────────────────────


class PilotProfile(db.Model):
    __tablename__ = "pilot_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    license_number = db.Column(db.String(64), nullable=True)
    medical_expiry = db.Column(db.Date, nullable=True)
    sep_expiry = db.Column(db.Date, nullable=True)
    first_solo_date = db.Column(db.Date, nullable=True)
    ppl_issue_date = db.Column(db.Date, nullable=True)

    user = db.relationship("User")


class LogbookEntryType:
    FLIGHT = "flight"
    FSTD = "fstd"  # synthetic training device / simulator session
    ALL = {FLIGHT, FSTD}


class FstdType:
    FFS = "FFS"
    FTD = "FTD"
    FNPT = "FNPT"
    BITD = "BITD"
    AATD = "AATD"
    ALL = [FFS, FTD, FNPT, BITD, AATD]


class PilotLogbookEntry(db.Model):
    __tablename__ = "pilot_logbook_entries"

    id = db.Column(db.Integer, primary_key=True)
    pilot_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    flight_id = db.Column(
        db.Integer,
        db.ForeignKey("flight_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    date = db.Column(db.Date, nullable=False)
    aircraft_type = db.Column(db.String(64), nullable=True)
    aircraft_type_icao = db.Column(db.String(16), nullable=True)
    aircraft_registration = db.Column(db.String(16), nullable=True)
    departure_place = db.Column(db.String(64), nullable=True)
    departure_time = db.Column(db.Time, nullable=True)
    arrival_place = db.Column(db.String(64), nullable=True)
    arrival_time = db.Column(db.Time, nullable=True)
    pic_name = db.Column(db.String(128), nullable=True)
    night_time = db.Column(db.Numeric(4, 1), nullable=True)
    instrument_time = db.Column(db.Numeric(4, 1), nullable=True)
    cross_country = db.Column(db.Numeric(4, 1), nullable=True)
    landings_day = db.Column(db.Integer, nullable=True)
    landings_night = db.Column(db.Integer, nullable=True)
    single_pilot_se = db.Column(db.Numeric(4, 1), nullable=True)
    single_pilot_me = db.Column(db.Numeric(4, 1), nullable=True)
    multi_pilot = db.Column(db.Numeric(4, 1), nullable=True)
    function_pic = db.Column(db.Numeric(4, 1), nullable=True)
    function_copilot = db.Column(db.Numeric(4, 1), nullable=True)
    function_dual = db.Column(db.Numeric(4, 1), nullable=True)
    function_instructor = db.Column(db.Numeric(4, 1), nullable=True)
    remarks = db.Column(db.Text, nullable=True)

    # EASA AMC1 FCL.050 column 10 — FSTD/simulator sessions (LogbookEntryType constant)
    entry_type = db.Column(
        db.String(16),
        nullable=False,
        default=LogbookEntryType.FLIGHT,
        server_default=LogbookEntryType.FLIGHT,
    )
    fstd_type = db.Column(db.String(16), nullable=True)  # FstdType constant
    fstd_duration = db.Column(db.Numeric(4, 1), nullable=True)

    source = db.Column(db.String(32), nullable=True)  # "import" | "gps_import" | None
    import_batch_id = db.Column(
        db.Integer,
        db.ForeignKey("logbook_import_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Phase 30: GPS-derived pilot logbook entries link to an AircraftGpsImportBatch
    gps_batch_id = db.Column(
        db.Integer,
        db.ForeignKey("aircraft_gps_import_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Phase 31b: standalone GPS track linkable independently of aircraft log
    gps_track_id = db.Column(
        db.Integer,
        db.ForeignKey("gps_tracks.id", ondelete="SET NULL"),
        nullable=True,
    )

    pilot = db.relationship("User", foreign_keys=[pilot_user_id])
    flight = db.relationship("FlightEntry")
    import_batch = db.relationship("LogbookImportBatch", foreign_keys=[import_batch_id])
    gps_batch = db.relationship("AircraftGpsImportBatch", foreign_keys=[gps_batch_id])
    gps_track = db.relationship("GpsTrack", foreign_keys=[gps_track_id])

    # Matches the pilot logbook list ordering (date DESC, id DESC per pilot).
    __table_args__ = (
        db.Index(
            "ix_pilot_logbook_entries_pilot_user_id_date_id",
            pilot_user_id,
            date.desc(),
            id.desc(),
        ),
    )

    @property
    def total_flight_time(self):
        parts = [self.single_pilot_se, self.single_pilot_me, self.multi_pilot]
        vals = [float(p) for p in parts if p is not None]
        return round(sum(vals), 1) if vals else None


# ── Phase 28: Pilot Logbook Import ───────────────────────────────────────────


class LogbookImportMapping(db.Model):
    __tablename__ = "logbook_import_mappings"

    id = db.Column(db.Integer, primary_key=True)
    pilot_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_fingerprint = db.Column(db.String(64), nullable=False, index=True)
    # JSON: {norm_col_key: target_field_or_"ignore"}
    column_mapping = db.Column(db.Text, nullable=False)
    # JSON list of norm_col_keys — stored for fuzzy matching future uploads
    source_columns = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)

    pilot = db.relationship("User")


class LogbookImportBatch(db.Model):
    __tablename__ = "logbook_import_batches"

    id = db.Column(db.Integer, primary_key=True)
    pilot_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    mapping_id = db.Column(
        db.Integer,
        db.ForeignKey("logbook_import_mappings.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_filename = db.Column(db.String(256), nullable=False)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=False)
    row_count = db.Column(db.Integer, nullable=False, default=0)
    subtotal_count = db.Column(db.Integer, nullable=False, default=0)
    skipped_count = db.Column(db.Integer, nullable=False, default=0)
    has_opening_balance = db.Column(db.Boolean, nullable=False, default=False)

    pilot = db.relationship("User")
    mapping = db.relationship("LogbookImportMapping")
    entries = db.relationship(
        "PilotLogbookEntry",
        foreign_keys="PilotLogbookEntry.import_batch_id",
        lazy="dynamic",
        overlaps="import_batch",
    )


class AirframeImportMapping(db.Model):
    """Fingerprint-keyed column mapping memory for airframe logbook imports
    (the aircraft-record twin of LogbookImportMapping)."""

    __tablename__ = "airframe_import_mappings"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    source_fingerprint = db.Column(db.String(64), nullable=False, index=True)
    # JSON: {norm_col_key: target_field_or_"ignore"}
    column_mapping = db.Column(db.Text, nullable=False)
    # JSON list of norm_col_keys
    source_columns = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)


class AirframeImportBatch(db.Model):
    """One executed airframe logbook import for one aircraft, undoable as a unit."""

    __tablename__ = "airframe_import_batches"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    mapping_id = db.Column(
        db.Integer,
        db.ForeignKey("airframe_import_mappings.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_filename = db.Column(db.String(256), nullable=False)
    imported_at = db.Column(db.DateTime(timezone=True), nullable=False)
    row_count = db.Column(db.Integer, nullable=False, default=0)
    subtotal_count = db.Column(db.Integer, nullable=False, default=0)
    skipped_count = db.Column(db.Integer, nullable=False, default=0)
    warning_count = db.Column(db.Integer, nullable=False, default=0)
    has_opening_counters = db.Column(db.Boolean, nullable=False, default=False)

    aircraft = db.relationship("Aircraft")


# ── Phase 30: Aircraft GPS Log Import ────────────────────────────────────────


class AircraftGpsImportBatch(db.Model):
    """Metadata for one GPS-import session (1+ files → 1+ FlightEntry records)."""

    __tablename__ = "aircraft_gps_import_batches"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    pilot_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # JSON list of original filenames, e.g. ["log_260518_EBNM.csv", "track.gpx"]
    source_filenames = db.Column(db.JSON, nullable=False, default=list)
    imported_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    format_detected = db.Column(
        db.String(16), nullable=False
    )  # "gpx"|"kml"|"garmin_csv"|"mixed"
    segments_found = db.Column(db.Integer, nullable=False, default=0)
    segments_imported = db.Column(db.Integer, nullable=False, default=0)
    # IDs of pre-existing FlightEntry rows that received a GPS track (not created).
    linked_flight_entry_ids = db.Column(db.JSON, nullable=False, default=list)
    # Pilot role selected during import: 'pic' | 'dual' | 'none'
    pilot_role = db.Column(db.String(8), nullable=True)
    # Set when the import is for an aircraft not in this instance (Phase 31).
    other_aircraft_make_model = db.Column(db.String(128), nullable=True)
    other_aircraft_registration = db.Column(db.String(16), nullable=True)

    aircraft = db.relationship(
        "Aircraft",
        backref=db.backref("gps_import_batches", cascade="all, delete-orphan"),
    )
    pilot = db.relationship("User", foreign_keys=[pilot_user_id])
    flight_entries = db.relationship(
        "FlightEntry",
        foreign_keys="FlightEntry.gps_import_batch_id",
        lazy="dynamic",
        overlaps="gps_import_batch",
    )
    pilot_logbook_entries = db.relationship(
        "PilotLogbookEntry",
        foreign_keys="PilotLogbookEntry.gps_batch_id",
        lazy="dynamic",
        overlaps="gps_batch",
    )


# ── Phase 4: Maintenance Tracking ────────────────────────────────────────────


class TriggerType:
    CALENDAR = "calendar"  # due on a specific date
    HOURS = "hours"  # due at a specific hobbs reading
    ALL = {CALENDAR, HOURS}


class MaintenanceTrigger(db.Model):
    __tablename__ = "maintenance_triggers"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    name = db.Column(db.String(128), nullable=False)
    trigger_type = db.Column(db.String(16), nullable=False)  # TriggerType constant

    # Calendar trigger fields
    due_date = db.Column(db.Date, nullable=True)
    interval_days = db.Column(db.Integer, nullable=True)  # advance due_date on service

    # Hours trigger fields
    due_engine_hours = db.Column(db.Numeric(8, 1), nullable=True)
    interval_hours = db.Column(
        db.Numeric(8, 1), nullable=True
    )  # advance due_engine_hours on service

    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", back_populates="maintenance_triggers")
    records = db.relationship(
        "MaintenanceRecord",
        back_populates="trigger",
        cascade="all, delete-orphan",
        order_by="MaintenanceRecord.performed_at.desc()",
    )

    __table_args__ = (db.Index("ix_maintenance_triggers_aircraft_id", aircraft_id),)

    def status(self, current_hobbs=None):
        """Return 'overdue', 'due_soon', or 'ok'."""
        from datetime import date as _date

        if self.trigger_type == TriggerType.CALENDAR and self.due_date:
            delta = (self.due_date - _date.today()).days
            if delta < 0:
                return "overdue"
            if delta <= 30:
                return "due_soon"
        elif (
            self.trigger_type == TriggerType.HOURS and self.due_engine_hours is not None
        ):
            if current_hobbs is None:
                return "ok"
            remaining = float(self.due_engine_hours) - float(current_hobbs)
            if remaining <= 0:
                return "overdue"
            warn = float(self.interval_hours) * 0.1 if self.interval_hours else 10.0
            if remaining <= max(warn, 5.0):
                return "due_soon"
        return "ok"

    @property
    def last_record(self):
        return self.records[0] if self.records else None


# ── Phase 6: Demo Mode ────────────────────────────────────────────────────────


class DemoSlot(db.Model):
    """One isolated visitor slot in demo mode. Each slot is its own tenant+user pair."""

    __tablename__ = "demo_slots"

    id = db.Column(db.Integer, primary_key=True)  # slot number 1..N
    display_id = db.Column(db.Integer, nullable=True)  # random 1000-9999, shown in UI
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    renter_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    maintenance_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    viewer_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    sole_pilot_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    sole_operator_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    last_activity_at = db.Column(db.DateTime(timezone=True), nullable=True)


class MaintenanceRecord(db.Model):
    __tablename__ = "maintenance_records"

    id = db.Column(db.Integer, primary_key=True)
    trigger_id = db.Column(
        db.Integer,
        db.ForeignKey("maintenance_triggers.id", ondelete="CASCADE"),
        nullable=False,
    )
    performed_at = db.Column(db.Date, nullable=False)
    hobbs_at_service = db.Column(db.Numeric(8, 1), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    trigger = db.relationship("MaintenanceTrigger", back_populates="records")


# ── Phase 8: Cost Tracking ────────────────────────────────────────────────────


class ExpenseType:
    FUEL = "fuel"
    PARTS = "parts"
    INSURANCE = "insurance"
    OTHER = "other"

    ALL = {FUEL, PARTS, INSURANCE, OTHER}
    LABELS = {
        FUEL: "Fuel",
        PARTS: "Parts & Maintenance",
        INSURANCE: "Insurance",
        OTHER: "Other",
    }


class ExpenseCategory:
    """Phase 36: fixed costs (pro-rated by time) vs. operating costs (usage-based)."""

    FIXED = "fixed"
    OPERATING = "operating"

    ALL = {FIXED, OPERATING}
    LABELS = {
        FIXED: "Fixed",
        OPERATING: "Operating",
    }
    # Default category per expense type; user may override on a per-entry basis.
    DEFAULTS = {
        ExpenseType.FUEL: OPERATING,
        ExpenseType.PARTS: OPERATING,
        ExpenseType.INSURANCE: FIXED,
        ExpenseType.OTHER: OPERATING,
    }


class ExpenseRecurrence:
    """Recurring fixed costs: how often a template expense repeats."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"

    MONTHS = {MONTHLY: 1, QUARTERLY: 3, YEARLY: 12}
    ALL = set(MONTHS)


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    flight_entry_id = db.Column(
        db.Integer,
        db.ForeignKey("flight_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    date = db.Column(db.Date, nullable=False)
    expense_type = db.Column(db.String(32), nullable=False, default=ExpenseType.OTHER)
    expense_category = db.Column(
        db.String(16), nullable=False, default=ExpenseCategory.OPERATING
    )
    description = db.Column(db.String(255), nullable=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(4), nullable=False, default="EUR")
    quantity = db.Column(db.Numeric(8, 2), nullable=True)  # litres or gallons of fuel
    unit = db.Column(db.String(8), nullable=True)  # L, gal
    # Phase 36: optional coverage span for fixed costs (e.g. an annual insurance
    # premium), used to pro-rate the amount across a report period shorter than
    # the coverage span. Left null, the expense counts in full on its `date`.
    coverage_start = db.Column(db.Date, nullable=True)
    coverage_end = db.Column(db.Date, nullable=True)
    # Recurring fixed costs: a template expense carries `recurrence`
    # (ExpenseRecurrence value, optionally bounded by recurrence_end); the
    # daily pass materialises ordinary Expense rows linked back through
    # recurring_template_id.  recurrence_last_date is the materialiser's
    # cursor — deleting a generated row must not resurrect it next run.
    recurrence = db.Column(db.String(16), nullable=True)
    recurrence_end = db.Column(db.Date, nullable=True)
    recurrence_last_date = db.Column(db.Date, nullable=True)
    recurring_template_id = db.Column(
        db.Integer, db.ForeignKey("expenses.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", back_populates="expenses")
    flight_entry = db.relationship("FlightEntry", back_populates="expenses")
    receipts = db.relationship(
        "Document",
        back_populates="expense",
        cascade="all, delete-orphan",
    )
    recurring_template = db.relationship(
        "Expense",
        foreign_keys=[recurring_template_id],
        remote_side="Expense.id",
        uselist=False,
    )

    __table_args__ = (db.Index("ix_expenses_aircraft_id", aircraft_id),)


# ── Phase 9 / 27: Document & Photo Uploads ───────────────────────────────────


class DocType:
    LICENSE = "license"
    MEDICAL = "medical"
    INSURANCE_CERT = "insurance_certificate"


class DocCategory:
    """Broad document categories that map 1-to-1 to on-disk folder names.

    Used by the Syncthing-compatible canonical path layout:
      {tenant_slug}/{aircraft_reg}/{category}/{YYYY-MM-DD} - {title}.{ext}
    """

    MAINTENANCE = "maintenance"
    INSURANCE = "insurance"
    POH = "poh"
    AIRWORTHINESS = "airworthiness"
    LOGBOOK = "logbook"
    INVOICE = "invoice"
    OTHER = "other"
    UNCATEGORISED = "uncategorised"

    ALL = [
        MAINTENANCE,
        INSURANCE,
        POH,
        AIRWORTHINESS,
        LOGBOOK,
        INVOICE,
        OTHER,
        UNCATEGORISED,
    ]


class Document(db.Model):
    """
    A document or photo attached to an aircraft, component, flight entry, or
    pilot profile.  aircraft_id or pilot_user_id must be set (not both).
    """

    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=True
    )
    pilot_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    component_id = db.Column(
        db.Integer, db.ForeignKey("components.id", ondelete="CASCADE"), nullable=True
    )
    flight_entry_id = db.Column(
        db.Integer,
        db.ForeignKey("flight_entries.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Receipt/invoice attached to an expense; such documents also carry
    # aircraft_id so access control and the aircraft document list apply.
    expense_id = db.Column(
        db.Integer, db.ForeignKey("expenses.id", ondelete="CASCADE"), nullable=True
    )
    # Signed rental agreement attached to a RenterAuthorization (Phase 37c).
    # Carries no aircraft_id — visible to the renter concerned and is_owner
    # users, not to the aircraft document list.
    renter_authorization_id = db.Column(
        db.Integer,
        db.ForeignKey("renter_authorizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    filename = db.Column(
        db.String(512), nullable=False
    )  # stored path on disk (may include subdirectories)
    original_filename = db.Column(db.String(255), nullable=False)  # as uploaded
    mime_type = db.Column(db.String(128), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)
    title = db.Column(db.String(128), nullable=True)  # optional display name
    doc_type = db.Column(db.String(32), nullable=True)  # DocType constant
    category = db.Column(
        db.String(32), nullable=True
    )  # DocCategory value; drives on-disk folder
    valid_until = db.Column(db.Date, nullable=True)
    superseded_by_id = db.Column(
        db.Integer, db.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    is_sensitive = db.Column(db.Boolean, nullable=False, default=False)
    uploaded_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship(
        "Aircraft",
        back_populates="documents",
        foreign_keys=[aircraft_id],
    )
    pilot_user = db.relationship("User", foreign_keys=[pilot_user_id])
    component = db.relationship("Component", back_populates="documents")
    flight_entry = db.relationship("FlightEntry", back_populates="documents")
    expense = db.relationship("Expense", back_populates="receipts")
    renter_authorization = db.relationship(
        "RenterAuthorization", back_populates="agreement_documents"
    )
    superseded_by = db.relationship(
        "Document",
        foreign_keys=[superseded_by_id],
        remote_side="Document.id",
        uselist=False,
    )

    __table_args__ = (
        db.Index("ix_documents_aircraft_id", aircraft_id),
        db.Index("ix_documents_pilot_user_id", pilot_user_id),
        db.Index("ix_documents_component_id", component_id),
        db.Index("ix_documents_flight_entry_id", flight_entry_id),
        db.Index("ix_documents_expense_id", expense_id),
    )

    @property
    def owner_type(self) -> str:
        if self.pilot_user_id:
            return "pilot"
        if self.component_id:
            return "component"
        if self.flight_entry_id:
            return "entry"
        if self.expense_id:
            return "expense"
        return "aircraft"

    @property
    def is_image(self) -> bool:
        return bool(self.mime_type and self.mime_type.startswith("image/"))

    @property
    def is_pdf(self) -> bool:
        return self.mime_type == "application/pdf"

    @property
    def is_expiring_soon(self) -> bool:
        """True when valid_until is set and within 90 days from today."""
        from datetime import date as _date

        if self.valid_until is None:
            return False
        return (self.valid_until - _date.today()).days <= 90


# ── Syncthing reconcile queue ─────────────────────────────────────────────────


class PendingReconcile(db.Model):
    """Files found on disk (via Syncthing or manual copy) that are not yet
    tracked in the documents table.  The reconcile screen lets owners review
    these files and import them as Document rows with a single click.

    filepath is relative to UPLOAD_FOLDER (e.g. 'my-hangar/OO-PNH/maintenance/
    2024-03-15 - Annual inspection.pdf').  The unique constraint prevents the
    same file from appearing twice in the queue.
    """

    __tablename__ = "pending_reconcile"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="SET NULL"), nullable=True
    )
    filepath = db.Column(db.String(512), nullable=False, unique=True)
    category = db.Column(db.String(32), nullable=True)
    title_hint = db.Column(db.String(255), nullable=True)
    date_hint = db.Column(db.Date, nullable=True)
    detected_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    reconciled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    ignored = db.Column(db.Boolean, nullable=False, default=False)

    tenant = db.relationship("Tenant")
    aircraft = db.relationship("Aircraft")


# ── Phase 10: Backup & Restore ────────────────────────────────────────────────


class BackupRecord(db.Model):
    __tablename__ = "backup_records"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    path = db.Column(db.String(512), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=True)
    sha256 = db.Column(db.String(64), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    status = db.Column(db.String(32), nullable=False, default="ok")  # ok / failed
    app_version = db.Column(db.String(64), nullable=True)
    alembic_head = db.Column(db.String(64), nullable=True)


# ── Phase 11: Read-only Share Links ──────────────────────────────────────────


class ShareToken(db.Model):
    __tablename__ = "share_tokens"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    token = db.Column(db.String(16), unique=True, nullable=False, index=True)
    access_level = db.Column(
        db.String(16), nullable=False, default="summary"
    )  # summary / full
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True, default=None)

    aircraft = db.relationship("Aircraft", back_populates="share_tokens")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


# ── Phase 12: Snag List ───────────────────────────────────────────────────────


class Snag(db.Model):
    __tablename__ = "snags"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    title = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    reporter = db.Column(db.String(128), nullable=True)
    is_grounding = db.Column(db.Boolean, nullable=False, default=False)
    reported_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True, default=None)
    resolution_note = db.Column(db.Text, nullable=True)

    aircraft = db.relationship("Aircraft", back_populates="snags")

    __table_args__ = (db.Index("ix_snags_aircraft_id", aircraft_id),)

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None


# ── Phase 22: Reservations ───────────────────────────────────────────────────


class ReservationStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class Reservation(db.Model):
    __tablename__ = "reservations"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    pilot_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    start_dt = db.Column(db.DateTime(timezone=True), nullable=False)
    end_dt = db.Column(db.DateTime(timezone=True), nullable=False)
    status = db.Column(
        db.Enum(ReservationStatus), nullable=False, default=ReservationStatus.PENDING
    )
    notes = db.Column(db.Text, nullable=True)
    hourly_rate = db.Column(db.Numeric(8, 2), nullable=True)  # EUR/h snapshot
    estimated_cost = db.Column(db.Numeric(10, 2), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", back_populates="reservations")
    pilot = db.relationship("User", foreign_keys=[pilot_user_id])

    __table_args__ = (db.Index("ix_reservations_aircraft_id", aircraft_id),)

    @property
    def duration_hours(self) -> float:
        delta = self.end_dt - self.start_dt
        return round(delta.total_seconds() / 3600, 2)


class RateBasis:
    """Phase 37b: which counter delta a rental charge is billed against."""

    ENGINE_TIME = "engine_time"
    FLIGHT_TIME = "flight_time"

    ALL = {ENGINE_TIME, FLIGHT_TIME}
    LABELS = {
        ENGINE_TIME: "Engine time",
        FLIGHT_TIME: "Flight time",
    }


class RateType:
    """Phase 37b: wet (fuel included) vs. dry (fuel billed separately)."""

    WET = "wet"
    DRY = "dry"

    ALL = {WET, DRY}
    LABELS = {
        WET: "Wet",
        DRY: "Dry",
    }


class AircraftBookingSettings(db.Model):
    """Per-aircraft booking rules and hourly rate for cost estimation."""

    __tablename__ = "aircraft_booking_settings"

    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), primary_key=True
    )
    min_booking_hours = db.Column(db.Numeric(4, 1), nullable=True)
    max_booking_hours = db.Column(db.Numeric(4, 1), nullable=True)
    hourly_rate = db.Column(db.Numeric(8, 2), nullable=True)  # EUR/h
    rate_basis = db.Column(db.String(16), nullable=False, default=RateBasis.ENGINE_TIME)
    rate_type = db.Column(db.String(8), nullable=False, default=RateType.WET)
    min_hours_per_day = db.Column(db.Numeric(4, 1), nullable=True)

    aircraft = db.relationship("Aircraft", back_populates="booking_settings")


class RenterAuthorization(db.Model):
    """Phase 37c: owner-verified rental qualification for one renter, scoped
    to one aircraft or the whole fleet (aircraft_id is NULL).

    These are owner-entered verification facts — deliberately NOT automatic
    reads of the renter's private PilotProfile (see decisions log in
    docs/phase37_rental_spec.md).
    """

    __tablename__ = "renter_authorizations"
    __table_args__ = (
        db.Index("ix_renter_authorizations_tenant_id", "tenant_id"),
        db.Index("ix_renter_authorizations_renter_user_id", "renter_user_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    renter_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=True
    )  # NULL = whole fleet
    authorized_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_on = db.Column(db.Date, nullable=False)
    expires_on = db.Column(db.Date, nullable=True)  # NULL = does not expire
    checkout_flight_on = db.Column(db.Date, nullable=True)
    licence_seen_on = db.Column(db.Date, nullable=True)
    medical_valid_until = db.Column(db.Date, nullable=True)  # owner-entered
    notes = db.Column(db.Text, nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    tenant = db.relationship("Tenant")
    renter_user = db.relationship("User", foreign_keys=[renter_user_id])
    authorized_by = db.relationship("User", foreign_keys=[authorized_by_id])
    aircraft = db.relationship("Aircraft")
    agreement_documents = db.relationship(
        "Document", back_populates="renter_authorization"
    )

    @property
    def is_valid(self) -> bool:
        from datetime import date as _date

        if self.revoked_at is not None:
            return False
        today = _date.today()
        if self.expires_on is not None and self.expires_on < today:
            return False
        return not (
            self.medical_valid_until is not None and self.medical_valid_until < today
        )

    @staticmethod
    def valid_for(
        renter_user_id: int, aircraft_id: int
    ) -> "RenterAuthorization | None":
        """Return a valid authorization covering this renter+aircraft — a
        fleet-wide row (aircraft_id IS NULL) or a per-aircraft row — or None."""
        candidates = RenterAuthorization.query.filter(
            RenterAuthorization.renter_user_id == renter_user_id,
            RenterAuthorization.revoked_at.is_(None),
            db.or_(
                RenterAuthorization.aircraft_id.is_(None),
                RenterAuthorization.aircraft_id == aircraft_id,
            ),
        ).all()
        return next((c for c in candidates if c.is_valid), None)


# ── Phase 20: Mass & Balance ──────────────────────────────────────────────────

FUEL_DENSITY = {
    "avgas": 0.72,  # Avgas 100LL
    "ul91": 0.72,  # UL91 — unleaded avgas replacement
    "mogas": 0.74,  # Automotive gasoline (Mogas)
    "jet_a1": 0.81,  # Jet-A1 (kerosene)
}  # kg/L
GAL_TO_L = 3.78541  # US gallons to litres


class WeightBalanceConfig(db.Model):
    __tablename__ = "wb_configs"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer,
        db.ForeignKey("aircraft.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    empty_weight = db.Column(db.Numeric(7, 2), nullable=False)  # kg
    empty_cg_arm = db.Column(db.Numeric(7, 2), nullable=False)  # m from datum
    max_takeoff_weight = db.Column(db.Numeric(7, 2), nullable=False)  # kg
    forward_cg_limit = db.Column(db.Numeric(7, 2), nullable=False)  # m
    aft_cg_limit = db.Column(db.Numeric(7, 2), nullable=False)  # m
    fuel_unit = db.Column(db.String(3), nullable=False, default="L")  # "L" or "gal"
    # Optional non-rectangular envelope: list of [arm_m, weight_kg] pairs in polygon order.
    # When ≥ 3 points are present they override forward_cg_limit/aft_cg_limit/max_takeoff_weight
    # for the in-envelope check.
    envelope_points = db.Column(db.JSON, nullable=True)
    datum_note = db.Column(db.String(200), nullable=True)

    aircraft = db.relationship("Aircraft", back_populates="wb_config")
    stations = db.relationship(
        "WeightBalanceStation",
        back_populates="config",
        cascade="all, delete-orphan",
        order_by="WeightBalanceStation.position",
    )
    entries = db.relationship(
        "WeightBalanceEntry",
        back_populates="config",
        cascade="all, delete-orphan",
    )


class WeightBalanceStation(db.Model):
    __tablename__ = "wb_stations"

    id = db.Column(db.Integer, primary_key=True)
    config_id = db.Column(
        db.Integer, db.ForeignKey("wb_configs.id", ondelete="CASCADE"), nullable=False
    )
    label = db.Column(db.String(64), nullable=False)
    arm = db.Column(db.Numeric(7, 2), nullable=False)  # m from datum
    max_weight = db.Column(db.Numeric(6, 2), nullable=True)  # kg limit (non-fuel only)
    capacity = db.Column(db.Float, nullable=True)  # L or gal (fuel stations)
    is_fuel = db.Column(db.Boolean, nullable=False, default=False)
    position = db.Column(db.Integer, nullable=False, default=0)  # display order

    config = db.relationship("WeightBalanceConfig", back_populates="stations")


class WeightBalanceEntry(db.Model):
    __tablename__ = "wb_entries"

    id = db.Column(db.Integer, primary_key=True)
    config_id = db.Column(
        db.Integer, db.ForeignKey("wb_configs.id", ondelete="CASCADE"), nullable=False
    )
    date = db.Column(db.Date, nullable=False)
    label = db.Column(db.String(100), nullable=True)
    total_weight = db.Column(db.Numeric(7, 2), nullable=False)  # kg
    loaded_cg = db.Column(db.Numeric(7, 2), nullable=False)  # mm
    is_in_envelope = db.Column(db.Boolean, nullable=False)
    # {station_id_str: value} — fuel stations store volume (L or gal), non-fuel store kg
    station_weights = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    config = db.relationship("WeightBalanceConfig", back_populates="entries")


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=True)


# ── Phase 33: Airworthiness Requirements Tracker ──────────────────────────────


class EASASourceNode(db.Model):
    """
    Maps a Component to one leaf node in the EASA Safety Publications Tool
    taxonomy tree (TC holder → type → model). One component may have multiple
    nodes (e.g. base TC plus an installed STC that also carries ADs).
    """

    __tablename__ = "easa_source_nodes"

    id = db.Column(db.Integer, primary_key=True)
    component_id = db.Column(
        db.Integer, db.ForeignKey("components.id", ondelete="CASCADE"), nullable=False
    )
    tc_holder_node_id = db.Column(db.String(16), nullable=False)
    tc_holder_name = db.Column(db.String(128), nullable=False)
    type_node_id = db.Column(db.String(16), nullable=False)
    type_name = db.Column(db.String(128), nullable=False)
    model_node_id = db.Column(db.String(16), nullable=False)
    model_name = db.Column(db.String(128), nullable=False)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True)
    consecutive_errors = db.Column(db.Integer, nullable=False, default=0)

    component = db.relationship("Component", back_populates="easa_source_nodes")
    documents = db.relationship(
        "AirworthinessDocument",
        back_populates="source_node",
        cascade="all, delete-orphan",
    )

    @property
    def display_path(self) -> str:
        return f"{self.tc_holder_name} / {self.type_name} / {self.model_name}"


class AirworthinessDocType:
    AD = "ad"
    MANDATORY_SB = "mandatory_sb"
    SB = "sb"
    SIB = "sib"
    ARC = "arc"
    MANUAL = "manual"

    ALL = (AD, MANDATORY_SB, SB, SIB, ARC, MANUAL)
    SYNCED = (AD, SIB)  # types populated by EASA sync
    LABELS = {
        AD: "AD",
        MANDATORY_SB: "Mandatory SB",
        SB: "SB",
        SIB: "SIB",
        ARC: "ARC",
        MANUAL: "Manual",
    }


class AirworthinessDocStatus:
    PENDING_REVIEW = "pending_review"
    COMPLIED = "complied"
    NOT_APPLICABLE = "not_applicable"
    DEFERRED = "deferred"
    QUESTION = "question"

    ALL = (PENDING_REVIEW, COMPLIED, NOT_APPLICABLE, DEFERRED, QUESTION)


class AirworthinessDocument(db.Model):
    """
    One airworthiness-related document (AD, SB, SIB, ARC, …) applicable to a
    component.  Synced documents reference a source_node; manually entered
    documents have source_node_id = NULL.
    """

    __tablename__ = "airworthiness_documents"

    id = db.Column(db.Integer, primary_key=True)
    doc_type = db.Column(db.String(16), nullable=False)
    reference = db.Column(db.String(64), nullable=False)
    title = db.Column(db.String(256), nullable=True)
    source_node_id = db.Column(
        db.Integer,
        db.ForeignKey("easa_source_nodes.id", ondelete="CASCADE"),
        nullable=True,
    )
    # For manual entries without a source node, store the component directly
    component_id = db.Column(
        db.Integer, db.ForeignKey("components.id", ondelete="CASCADE"), nullable=True
    )
    doc_url = db.Column(db.String(512), nullable=True)
    # For ARC: date the certificate expires
    expiry_date = db.Column(db.Date, nullable=True)
    first_seen_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    source_node = db.relationship("EASASourceNode", back_populates="documents")
    component = db.relationship("Component", back_populates="airworthiness_documents")
    statuses = db.relationship(
        "AirworthinessDocumentStatus",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    @property
    def is_manual(self) -> bool:
        return self.source_node_id is None


class AirworthinessDocumentStatus(db.Model):
    """
    Compliance state of one AirworthinessDocument for one aircraft.
    Unique per (aircraft_id, document_id).
    """

    __tablename__ = "airworthiness_document_statuses"
    __table_args__ = (
        db.UniqueConstraint("aircraft_id", "document_id", name="uq_aw_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    document_id = db.Column(
        db.Integer,
        db.ForeignKey("airworthiness_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = db.Column(
        db.String(24), nullable=False, default=AirworthinessDocStatus.PENDING_REVIEW
    )
    notes = db.Column(db.Text, nullable=True)
    compliance_date = db.Column(db.Date, nullable=True)
    next_review_date = db.Column(db.Date, nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", back_populates="airworthiness_statuses")
    document = db.relationship("AirworthinessDocument", back_populates="statuses")


class InstalledSTC(db.Model):
    """
    Registry of Supplemental Type Certificates physically installed on an
    aircraft.  No compliance workflow — presence/absence is the record.
    """

    __tablename__ = "installed_stcs"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    stc_number = db.Column(db.String(64), nullable=False)
    title = db.Column(db.String(256), nullable=True)
    tc_holder = db.Column(db.String(128), nullable=True)
    installation_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    aircraft = db.relationship("Aircraft", back_populates="installed_stcs")


# ── Phase 34: Email Notifications ─────────────────────────────────────────────


class NotificationType:
    """String constants for all supported notification types.

    Stored as plain strings in the DB — no enum migration needed to add types.
    """

    GROUNDING_SNAG_OPENED = "grounding_snag_opened"
    SNAG_REPORTED = "snag_reported"
    RESERVATION_CONFIRMED = "reservation_confirmed"
    RESERVATION_CANCELLED = "reservation_cancelled"
    RESERVATION_REQUEST = "reservation_request"
    MAINTENANCE_DUE_SOON = "maintenance_due_soon"
    MAINTENANCE_OVERDUE = "maintenance_overdue"
    INSURANCE_EXPIRING = "insurance_expiring"
    MEDICAL_EXPIRING = "medical_expiring"
    SEP_RATING_EXPIRING = "sep_rating_expiring"
    DOCUMENT_EXPIRING = "document_expiring"
    NEW_MEMBER_JOINED = "new_member_joined"
    AIRWORTHINESS_REVIEW_DUE = "airworthiness_review_due"
    EASA_SYNC_NEW_AD = "easa_sync_new_ad"
    RENTER_AUTHORIZATION_EXPIRY = "renter_authorization_expiry"

    ALL: list[str] = [
        GROUNDING_SNAG_OPENED,
        SNAG_REPORTED,
        RESERVATION_CONFIRMED,
        RESERVATION_CANCELLED,
        RESERVATION_REQUEST,
        MAINTENANCE_DUE_SOON,
        MAINTENANCE_OVERDUE,
        INSURANCE_EXPIRING,
        MEDICAL_EXPIRING,
        SEP_RATING_EXPIRING,
        DOCUMENT_EXPIRING,
        NEW_MEMBER_JOINED,
        AIRWORTHINESS_REVIEW_DUE,
        EASA_SYNC_NEW_AD,
        RENTER_AUTHORIZATION_EXPIRY,
    ]

    # System defaults — coded constants; DB only stores per-user or per-tenant overrides
    SYSTEM_DEFAULTS: dict[str, dict] = {
        GROUNDING_SNAG_OPENED: {"enabled": True, "threshold_days": None},
        SNAG_REPORTED: {"enabled": False, "threshold_days": None},
        RESERVATION_CONFIRMED: {"enabled": True, "threshold_days": None},
        RESERVATION_CANCELLED: {"enabled": True, "threshold_days": None},
        RESERVATION_REQUEST: {"enabled": True, "threshold_days": None},
        MAINTENANCE_DUE_SOON: {"enabled": True, "threshold_days": 30},
        MAINTENANCE_OVERDUE: {"enabled": True, "threshold_days": None},
        INSURANCE_EXPIRING: {"enabled": True, "threshold_days": 30},
        MEDICAL_EXPIRING: {"enabled": True, "threshold_days": 60},
        SEP_RATING_EXPIRING: {"enabled": True, "threshold_days": 60},
        DOCUMENT_EXPIRING: {"enabled": True, "threshold_days": 30},
        NEW_MEMBER_JOINED: {"enabled": False, "threshold_days": None},
        AIRWORTHINESS_REVIEW_DUE: {"enabled": True, "threshold_days": 30},
        EASA_SYNC_NEW_AD: {"enabled": True, "threshold_days": None},
        RENTER_AUTHORIZATION_EXPIRY: {"enabled": True, "threshold_days": 30},
    }

    # Capability flags required — user sees this type in their prefs if they have >= 1
    # "is_owner" | "is_pilot" | "is_maint" match init.py context processor naming
    REQUIRED_CAPS: dict[str, list[str]] = {
        GROUNDING_SNAG_OPENED: ["is_owner", "is_maint"],
        SNAG_REPORTED: ["is_owner"],
        RESERVATION_CONFIRMED: ["is_pilot"],
        RESERVATION_CANCELLED: ["is_pilot"],
        RESERVATION_REQUEST: ["is_owner"],
        MAINTENANCE_DUE_SOON: ["is_owner", "is_maint"],
        MAINTENANCE_OVERDUE: ["is_owner", "is_maint"],
        INSURANCE_EXPIRING: ["is_owner"],
        MEDICAL_EXPIRING: ["is_pilot"],
        SEP_RATING_EXPIRING: ["is_pilot"],
        DOCUMENT_EXPIRING: ["is_owner", "is_maint"],
        NEW_MEMBER_JOINED: ["is_owner"],
        AIRWORTHINESS_REVIEW_DUE: ["is_owner", "is_maint"],
        EASA_SYNC_NEW_AD: ["is_owner", "is_maint"],
        RENTER_AUTHORIZATION_EXPIRY: ["is_owner"],
    }

    # Types that have a configurable days-ahead threshold
    HAS_THRESHOLD: set[str] = {
        MAINTENANCE_DUE_SOON,
        INSURANCE_EXPIRING,
        MEDICAL_EXPIRING,
        SEP_RATING_EXPIRING,
        DOCUMENT_EXPIRING,
        AIRWORTHINESS_REVIEW_DUE,
        RENTER_AUTHORIZATION_EXPIRY,
    }


class NotificationPreference(db.Model):
    """Per-user notification preference override within a tenant (level 1 of 3)."""

    __tablename__ = "notification_preferences"
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "tenant_id", "notification_type", name="uq_notif_pref"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    notification_type = db.Column(db.String(64), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False)
    threshold_days = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = db.relationship("User")
    tenant = db.relationship("Tenant")


class TenantNotificationDefault(db.Model):
    """Per-tenant override of system notification defaults (level 2 of 3)."""

    __tablename__ = "tenant_notification_defaults"
    __table_args__ = (
        db.UniqueConstraint(
            "tenant_id", "notification_type", name="uq_tenant_notif_default"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    notification_type = db.Column(db.String(64), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False)
    threshold_days = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    tenant = db.relationship("Tenant")


class BillingAccountKind:
    """Shared billing core (Phases 37/38/39) — see docs/billing_service_design.md."""

    RENTER = "renter"  # Phase 37 — scoped to the tenant (all aircraft)
    CO_OWNER = "co_owner"  # Phase 38 — scoped to one aircraft
    MEMBER = "member"  # Phase 39 — scoped to the tenant

    ALL = {RENTER, CO_OWNER, MEMBER}


class BillingAccount(db.Model):
    """One row per (tenant, user, scope). Created lazily by BillingService —
    there is no UI to create an account directly."""

    __tablename__ = "billing_accounts"
    __table_args__ = (
        # A plain UniqueConstraint on (tenant_id, user_id, kind, aircraft_id)
        # would NOT prevent duplicate renter/member accounts: aircraft_id is
        # NULL for those (tenant-scoped, not aircraft-scoped) kinds, and SQL
        # unique constraints treat NULL as distinct from every other NULL.
        # Two partial unique indexes close that gap: one for aircraft-scoped
        # (co_owner) rows, one for tenant-scoped (renter/member) rows.
        db.Index(
            "uq_billing_account_scope_aircraft",
            "tenant_id",
            "user_id",
            "kind",
            "aircraft_id",
            unique=True,
            sqlite_where=db.text("aircraft_id IS NOT NULL"),
            postgresql_where=db.text("aircraft_id IS NOT NULL"),
        ),
        db.Index(
            "uq_billing_account_scope_fleet",
            "tenant_id",
            "user_id",
            "kind",
            unique=True,
            sqlite_where=db.text("aircraft_id IS NULL"),
            postgresql_where=db.text("aircraft_id IS NULL"),
        ),
        db.Index("ix_billing_accounts_tenant_id", "tenant_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=True
    )  # co_owner only
    kind = db.Column(db.String(16), nullable=False)  # BillingAccountKind
    currency = db.Column(db.String(4), nullable=False, default="EUR")
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    tenant = db.relationship("Tenant")
    user = db.relationship("User", foreign_keys=[user_id])
    aircraft = db.relationship("Aircraft")
    entries = db.relationship(
        "LedgerEntry", back_populates="account", cascade="all, delete-orphan"
    )


class LedgerEntryType:
    """Append-only ledger entry types. No update/delete route may ever exist
    for LedgerEntry — corrections are posted as reversal entries."""

    CHARGE = "charge"  # money the account holder owes (positive amount)
    PAYMENT = "payment"  # money received from the holder (negative amount)
    CREDIT = "credit"  # reduction of debt, e.g. fuel reimbursement (negative)
    ADJUSTMENT = "adjustment"  # manual correction, either sign, requires note
    OPENING = "opening"  # opening balance / co-owner buy-in

    ALL = {CHARGE, PAYMENT, CREDIT, ADJUSTMENT, OPENING}


class LedgerEntry(db.Model):
    """Append-only. Sign convention: positive amount = the holder owes more;
    negative = the holder owes less. balance = sum(amount)."""

    __tablename__ = "ledger_entries"
    __table_args__ = (db.Index("ix_ledger_entries_account_id", "account_id"),)

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(
        db.Integer,
        db.ForeignKey("billing_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    entry_type = db.Column(db.String(16), nullable=False)  # LedgerEntryType
    amount = db.Column(db.Numeric(10, 2), nullable=False)  # signed
    description = db.Column(db.String(255), nullable=False)
    entry_date = db.Column(db.Date, nullable=False)  # business date, not created_at
    # Link back to the domain object that produced the entry, for drill-down:
    source_type = db.Column(db.String(32), nullable=True)  # e.g. "rental_charge"
    source_id = db.Column(db.Integer, nullable=True)
    # SET NULL, not RESTRICT: entries are never deleted in the app (append-only,
    # no delete route) so this only matters for admin-level DB surgery / test
    # cleanup — a self-referential RESTRICT here blocks a bulk DELETE FROM
    # ledger_entries entirely, since SQLite can't order same-table FK checks.
    reverses_id = db.Column(
        db.Integer,
        db.ForeignKey("ledger_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    account = db.relationship("BillingAccount", back_populates="entries")
    reverses = db.relationship(
        "LedgerEntry", foreign_keys=[reverses_id], remote_side="LedgerEntry.id"
    )
    created_by = db.relationship("User", foreign_keys=[created_by_id])
