import enum
from datetime import datetime, timezone, time

from flask_sqlalchemy import SQLAlchemy # pyright: ignore[reportMissingImports]

db = SQLAlchemy()


class Role(str, enum.Enum):
    ADMIN = "admin"
    OWNER = "owner"
    VIEWER = "viewer"


class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
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
    language = db.Column(db.String(8), nullable=True, default="en")
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    tenants = db.relationship(
        "TenantUser", back_populates="user", cascade="all, delete-orphan"
    )


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


# ── Phase 1: Aircraft & Component Models ──────────────────────────────────────

# Application-level component type constants.
# Stored as plain strings in the DB so new types never require a migration.
class ComponentType:
    ENGINE    = "engine"
    PROPELLER = "propeller"
    AVIONICS  = "avionics"

    ALL = {ENGINE, PROPELLER, AVIONICS}


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
    is_placeholder = db.Column(db.Boolean, nullable=False, default=False)
    regime = db.Column(db.String(8), nullable=False, default="EASA")
    has_flight_counter = db.Column(db.Boolean, nullable=False, default=True)
    flight_counter_offset = db.Column(db.Numeric(3, 1), nullable=False, default=0.3)
    fuel_flow = db.Column(db.Numeric(6, 2), nullable=True)  # typical fuel consumption in L/h
    fuel_type = db.Column(db.String(8), nullable=False, default="avgas")  # "avgas" | "jet_a1"
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
        "Document", back_populates="aircraft", cascade="all, delete-orphan",
        foreign_keys="Document.aircraft_id",
    )
    share_tokens = db.relationship(
        "ShareToken", back_populates="aircraft", cascade="all, delete-orphan",
    )
    snags = db.relationship(
        "Snag", back_populates="aircraft", cascade="all, delete-orphan",
    )
    wb_config = db.relationship(
        "WeightBalanceConfig", back_populates="aircraft",
        cascade="all, delete-orphan", uselist=False,
    )

    @property
    def total_engine_hours(self):
        """Current engine hours — the highest engine_time_counter_end across all flight entries."""
        vals = [float(f.engine_time_counter_end) for f in self.flights if f.engine_time_counter_end is not None]
        return max(vals) if vals else None

    @property
    def total_flight_hours(self):
        """Current flight hours — the highest flight_time_counter_end across all flight entries."""
        vals = [float(f.flight_time_counter_end) for f in self.flights if f.flight_time_counter_end is not None]
        return max(vals) if vals else None

    @property
    def is_grounded(self) -> bool:
        """True when any unresolved grounding snag exists."""
        return any(s.is_grounding and s.is_open for s in self.snags)


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

    # Type-specific attributes (blade count, TBO, firmware version, …)
    extras = db.Column(db.JSON, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", back_populates="components")
    documents = db.relationship(
        "Document", back_populates="component", cascade="all, delete-orphan",
    )


# ── Phase 3: Flight Logging ───────────────────────────────────────────────────

class CrewRole:
    PIC     = "PIC"
    IP      = "IP"
    SP      = "SP"
    COPILOT = "COPILOT"
    ALL = [PIC, IP, SP, COPILOT]
    LABELS = {PIC: "PIC", IP: "Instructor", SP: "Safety Pilot", COPILOT: "Co-Pilot"}


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
    fuel_event = db.Column(db.String(8), nullable=True)   # 'before' | 'after' | None
    fuel_added_qty = db.Column(db.Numeric(8, 2), nullable=True)
    fuel_added_unit = db.Column(db.String(8), nullable=True)
    fuel_remaining_qty = db.Column(db.Numeric(8, 2), nullable=True)
    fuel_photo = db.Column(db.String(255), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", back_populates="flights")
    crew = db.relationship(
        "FlightCrew", back_populates="flight",
        cascade="all, delete-orphan",
        order_by="FlightCrew.sort_order",
    )
    expenses = db.relationship("Expense", back_populates="flight_entry")
    documents = db.relationship(
        "Document", back_populates="flight_entry", cascade="all, delete-orphan",
    )


class FlightCrew(db.Model):
    __tablename__ = "flight_crew"

    id         = db.Column(db.Integer, primary_key=True)
    flight_id  = db.Column(
        db.Integer, db.ForeignKey("flight_entries.id", ondelete="CASCADE"), nullable=False
    )
    user_id    = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name       = db.Column(db.String(128), nullable=False)
    role       = db.Column(db.String(16), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    flight = db.relationship("FlightEntry", back_populates="crew")
    user   = db.relationship("User")


# ── Phase 17: Pilot Profile & Manual Logbook ─────────────────────────────────

class PilotProfile(db.Model):
    __tablename__ = "pilot_profiles"

    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    license_number  = db.Column(db.String(64), nullable=True)
    medical_expiry  = db.Column(db.Date, nullable=True)
    sep_expiry      = db.Column(db.Date, nullable=True)

    user = db.relationship("User")


class PilotLogbookEntry(db.Model):
    __tablename__ = "pilot_logbook_entries"

    id                   = db.Column(db.Integer, primary_key=True)
    pilot_user_id        = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    flight_id            = db.Column(
        db.Integer, db.ForeignKey("flight_entries.id", ondelete="SET NULL"), nullable=True
    )
    date                 = db.Column(db.Date, nullable=False)
    aircraft_type        = db.Column(db.String(64), nullable=True)
    aircraft_registration = db.Column(db.String(16), nullable=True)
    departure_place      = db.Column(db.String(64), nullable=True)
    departure_time       = db.Column(db.Time, nullable=True)
    arrival_place        = db.Column(db.String(64), nullable=True)
    arrival_time         = db.Column(db.Time, nullable=True)
    pic_name             = db.Column(db.String(128), nullable=True)
    night_time           = db.Column(db.Numeric(4, 1), nullable=True)
    instrument_time      = db.Column(db.Numeric(4, 1), nullable=True)
    landings_day         = db.Column(db.Integer, nullable=True)
    landings_night       = db.Column(db.Integer, nullable=True)
    single_pilot_se      = db.Column(db.Numeric(4, 1), nullable=True)
    single_pilot_me      = db.Column(db.Numeric(4, 1), nullable=True)
    multi_pilot          = db.Column(db.Numeric(4, 1), nullable=True)
    function_pic         = db.Column(db.Numeric(4, 1), nullable=True)
    function_copilot     = db.Column(db.Numeric(4, 1), nullable=True)
    function_dual        = db.Column(db.Numeric(4, 1), nullable=True)
    function_instructor  = db.Column(db.Numeric(4, 1), nullable=True)
    remarks              = db.Column(db.Text, nullable=True)

    pilot = db.relationship("User", foreign_keys=[pilot_user_id])
    flight = db.relationship("FlightEntry")

    @property
    def total_flight_time(self):
        parts = [self.single_pilot_se, self.single_pilot_me, self.multi_pilot]
        vals = [float(p) for p in parts if p is not None]
        return round(sum(vals), 1) if vals else None


# ── Phase 4: Maintenance Tracking ────────────────────────────────────────────

class TriggerType:
    CALENDAR = "calendar"   # due on a specific date
    HOURS    = "hours"      # due at a specific hobbs reading
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
    interval_days = db.Column(db.Integer, nullable=True)   # advance due_date on service

    # Hours trigger fields
    due_engine_hours = db.Column(db.Numeric(8, 1), nullable=True)
    interval_hours = db.Column(db.Numeric(8, 1), nullable=True)  # advance due_engine_hours on service

    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", back_populates="maintenance_triggers")
    records = db.relationship(
        "MaintenanceRecord", back_populates="trigger",
        cascade="all, delete-orphan",
        order_by="MaintenanceRecord.performed_at.desc()",
    )

    def status(self, current_hobbs=None):
        """Return 'overdue', 'due_soon', or 'ok'."""
        from datetime import date as _date
        if self.trigger_type == TriggerType.CALENDAR and self.due_date:
            delta = (self.due_date - _date.today()).days
            if delta < 0:
                return "overdue"
            if delta <= 30:
                return "due_soon"
        elif self.trigger_type == TriggerType.HOURS and self.due_engine_hours is not None:
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
    last_activity_at = db.Column(db.DateTime(timezone=True), nullable=True)


class MaintenanceRecord(db.Model):
    __tablename__ = "maintenance_records"

    id = db.Column(db.Integer, primary_key=True)
    trigger_id = db.Column(
        db.Integer, db.ForeignKey("maintenance_triggers.id", ondelete="CASCADE"),
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
    FUEL      = "fuel"
    PARTS     = "parts"
    INSURANCE = "insurance"
    OTHER     = "other"

    ALL = {FUEL, PARTS, INSURANCE, OTHER}
    LABELS = {
        FUEL:      "Fuel",
        PARTS:     "Parts & Maintenance",
        INSURANCE: "Insurance",
        OTHER:     "Other",
    }


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    flight_entry_id = db.Column(
        db.Integer, db.ForeignKey("flight_entries.id", ondelete="SET NULL"), nullable=True
    )
    date = db.Column(db.Date, nullable=False)
    expense_type = db.Column(db.String(32), nullable=False, default=ExpenseType.OTHER)
    description = db.Column(db.String(255), nullable=True)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(4), nullable=False, default="EUR")
    quantity = db.Column(db.Numeric(8, 2), nullable=True)  # litres or gallons of fuel
    unit = db.Column(db.String(8), nullable=True)          # L, gal
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", back_populates="expenses")
    flight_entry = db.relationship("FlightEntry", back_populates="expenses")


# ── Phase 9: Document & Photo Uploads ────────────────────────────────────────

class Document(db.Model):
    """
    A document or photo attached to an aircraft, component, or flight entry.
    aircraft_id is always required; component_id and flight_entry_id optionally
    narrow the scope to a specific component or flight.
    """
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    component_id = db.Column(
        db.Integer, db.ForeignKey("components.id", ondelete="CASCADE"), nullable=True
    )
    flight_entry_id = db.Column(
        db.Integer, db.ForeignKey("flight_entries.id", ondelete="CASCADE"), nullable=True
    )
    filename = db.Column(db.String(255), nullable=False)           # stored name on disk
    original_filename = db.Column(db.String(255), nullable=False)  # as uploaded
    mime_type = db.Column(db.String(128), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)
    title = db.Column(db.String(128), nullable=True)               # optional display name
    is_sensitive = db.Column(db.Boolean, nullable=False, default=False)
    uploaded_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship(
        "Aircraft", back_populates="documents", foreign_keys=[aircraft_id]
    )
    component = db.relationship("Component", back_populates="documents")
    flight_entry = db.relationship("FlightEntry", back_populates="documents")

    @property
    def owner_type(self) -> str:
        if self.component_id:
            return "component"
        if self.flight_entry_id:
            return "entry"
        return "aircraft"

    @property
    def is_image(self) -> bool:
        return bool(self.mime_type and self.mime_type.startswith("image/"))


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


# ── Phase 11: Read-only Share Links ──────────────────────────────────────────

class ShareToken(db.Model):
    __tablename__ = "share_tokens"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    token = db.Column(db.String(8), unique=True, nullable=False, index=True)
    access_level = db.Column(db.String(16), nullable=False, default="summary")  # summary / full
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

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None


# ── Phase 20: Mass & Balance ──────────────────────────────────────────────────

FUEL_DENSITY = {"avgas": 0.72, "jet_a1": 0.81}  # kg/L
GAL_TO_L = 3.78541  # US gallons to litres


class WeightBalanceConfig(db.Model):
    __tablename__ = "wb_configs"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    empty_weight = db.Column(db.Numeric(7, 2), nullable=False)   # kg
    empty_cg_arm = db.Column(db.Numeric(7, 2), nullable=False)   # m from datum
    max_takeoff_weight = db.Column(db.Numeric(7, 2), nullable=False)  # kg
    forward_cg_limit = db.Column(db.Numeric(7, 2), nullable=False)   # m
    aft_cg_limit = db.Column(db.Numeric(7, 2), nullable=False)        # m
    fuel_unit = db.Column(db.String(3), nullable=False, default="L")  # "L" or "gal"
    # Optional non-rectangular envelope: list of [arm_m, weight_kg] pairs in polygon order.
    # When ≥ 3 points are present they override forward_cg_limit/aft_cg_limit/max_takeoff_weight
    # for the in-envelope check.
    envelope_points = db.Column(db.JSON, nullable=True)
    datum_note = db.Column(db.String(200), nullable=True)

    aircraft = db.relationship("Aircraft", back_populates="wb_config")
    stations = db.relationship(
        "WeightBalanceStation", back_populates="config",
        cascade="all, delete-orphan",
        order_by="WeightBalanceStation.position",
    )
    entries = db.relationship(
        "WeightBalanceEntry", back_populates="config",
        cascade="all, delete-orphan",
    )


class WeightBalanceStation(db.Model):
    __tablename__ = "wb_stations"

    id = db.Column(db.Integer, primary_key=True)
    config_id = db.Column(
        db.Integer, db.ForeignKey("wb_configs.id", ondelete="CASCADE"), nullable=False
    )
    label = db.Column(db.String(64), nullable=False)
    arm = db.Column(db.Numeric(7, 2), nullable=False)         # m from datum
    max_weight = db.Column(db.Numeric(6, 2), nullable=True)   # kg limit (non-fuel only)
    capacity = db.Column(db.Float, nullable=True)             # L or gal (fuel stations)
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
    loaded_cg = db.Column(db.Numeric(7, 2), nullable=False)     # mm
    is_in_envelope = db.Column(db.Boolean, nullable=False)
    # {station_id_str: value} — fuel stations store volume (L or gal), non-fuel store kg
    station_weights = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    config = db.relationship("WeightBalanceConfig", back_populates="entries")
