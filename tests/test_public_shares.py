"""Generic capabilities never confer resource permissions on their own."""

from datetime import UTC, datetime, timedelta

from flask import Flask
from models import PublicShare, Tenant, User, db
from services import public_shares as shares


def test_lifecycle_and_resource_binding(app: Flask) -> None:
    with app.app_context():
        tenant = Tenant(name="Public test")
        owner = User(email="public@example.test", password_hash="unused")
        db.session.add_all([tenant, owner])
        db.session.flush()
        created = datetime.now(UTC)
        share = PublicShare(
            tenant_id=tenant.id,
            issuer_user_id=owner.id,
            resource_type="passport",
            resource_id=1,
            resource_created_at=created,
        )
        token = shares.rotate(share)
        db.session.add(share)
        db.session.commit()
        assert shares.lookup(token, "passport") is share
        assert shares.lookup(token, "place") is None
        assert shares.lookup("invalid", "passport") is None
        assert shares.lookup("x" * 43, "passport") is None
        binding = {
            "tenant_id": tenant.id,
            "owner_user_id": owner.id,
            "created_at": created,
        }
        assert shares.matches_resource(share, **binding)
        for key, value in (
            ("tenant_id", tenant.id + 1),
            ("owner_user_id", owner.id + 1),
            ("created_at", created + timedelta(seconds=1)),
        ):
            assert not shares.matches_resource(share, **(binding | {key: value}))
        share.resource_created_at = created.replace(tzinfo=None)
        assert shares.matches_resource(share, **binding)
        replacement = shares.rotate(share, datetime.now(UTC) + timedelta(days=1))
        db.session.commit()
        assert shares.lookup(token, "passport") is None
        assert shares.lookup(replacement, "passport") is share
        shares.revoke(share)
        db.session.commit()
        assert shares.lookup(replacement, "passport") is None
        assert shares.recover(share) is None
        expired = shares.rotate(share, datetime.now(UTC) - timedelta(seconds=1))
        db.session.commit()
        assert shares.lookup(expired, "passport") is None
        token = shares.rotate(share)
        share.key_nonce = "different"
        db.session.commit()
        assert shares.lookup(token, "passport") is None
