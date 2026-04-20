import enum
from datetime import datetime, timezone

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

    @property
    def total_hobbs(self):
        """Current hobbs reading — the highest hobbs_end across all flight entries."""
        if not self.flights:
            return None
        return max(float(f.hobbs_end) for f in self.flights)


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

class FlightEntry(db.Model):
    __tablename__ = "flight_entries"

    id = db.Column(db.Integer, primary_key=True)
    aircraft_id = db.Column(
        db.Integer, db.ForeignKey("aircraft.id", ondelete="CASCADE"), nullable=False
    )
    date = db.Column(db.Date, nullable=False)
    departure_icao = db.Column(db.String(4), nullable=False)
    arrival_icao = db.Column(db.String(4), nullable=False)
    hobbs_start = db.Column(db.Numeric(8, 1), nullable=False)
    hobbs_end = db.Column(db.Numeric(8, 1), nullable=False)
    pilot = db.Column(db.String(128), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    tach_start = db.Column(db.Numeric(8, 1), nullable=True)
    tach_end = db.Column(db.Numeric(8, 1), nullable=True)
    hobbs_photo = db.Column(db.String(255), nullable=True)
    tach_photo = db.Column(db.String(255), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", back_populates="flights")
    expenses = db.relationship("Expense", back_populates="flight_entry")
    documents = db.relationship(
        "Document", back_populates="flight_entry", cascade="all, delete-orphan",
    )


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
    due_hobbs = db.Column(db.Numeric(8, 1), nullable=True)
    interval_hours = db.Column(db.Numeric(8, 1), nullable=True)  # advance due_hobbs on service

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
        elif self.trigger_type == TriggerType.HOURS and self.due_hobbs is not None:
            if current_hobbs is None:
                return "ok"
            remaining = float(self.due_hobbs) - float(current_hobbs)
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
