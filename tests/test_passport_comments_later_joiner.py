"""A friend who joins the group later reads the discussion that came before."""

from datetime import UTC, datetime

import pytest
from flask import Flask
from models import Passport, PassportParticipant, TenantUser, User, db
from passports import comments, groups
from passports.management import BuilderError

from tests.test_passport_groups import consent_values, group
from tests.test_passport_management import owner

__all__ = ["group", "owner"]


def test_later_joiner_sees_earlier_comments_only_after_joining(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        new = db.session.get(Passport, group["new"])
        tenant, series = new.tenant_id, new.series_id
        from models import PassportAirfield

        airfield = (
            PassportAirfield.query.filter_by(passport_id=new.id).one().airfield_id
        )
        comments.add(
            tenant,
            group["user"],
            series,
            airfield,
            {"category": "airfield", "body": "Runway 26 has a slight slope."},
        )
        newcomer = User(email="newcomer@test.example", password_hash="unused")
        db.session.add(newcomer)
        db.session.flush()
        db.session.add(
            TenantUser(
                tenant_id=tenant,
                user_id=newcomer.id,
                role=None,
                access_scope="personal",
            )
        )
        db.session.flush()
        # Before joining, the discussion is closed to them.
        with pytest.raises(BuilderError) as refused:
            comments.context(tenant, newcomer.id, series, airfield)
        assert refused.value.status == 404
        db.session.add(
            PassportParticipant(
                passport_id=new.id,
                user_id=newcomer.id,
                accepted_at=datetime.now(UTC),
                display_name="Newcomer",
            )
        )
        db.session.flush()
        groups.consent(new, newcomer.id, consent_values("Newcomer"))
        scope = comments.context(tenant, newcomer.id, series, airfield)
        assert [c.body for c in comments.feed(scope)] == [
            "Runway 26 has a slight slope."
        ]
