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
