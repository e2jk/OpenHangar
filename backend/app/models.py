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
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    aircraft = db.relationship("Aircraft", back_populates="flights")
