"""Real PostgreSQL membership revocation, retained ledgers and admission races."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from flask import Flask
from models import (
    Passport,
    PassportGroupBlock,
    PassportInvitation,
    PassportParticipant,
    db,
)
from passports import invitations, memberships
from passports.management import BuilderError

from tests.postgres_schema import postgres_app
from tests.test_passport_groups import consent_values, group
from tests.test_passport_invitations import invited
from tests.test_passport_management import owner
from tests.test_passport_memberships import (
    members,
    test_edition_ban_survives_group_reinstatement,
    test_owner_removal_cannot_be_bypassed_and_reinstatement_requires_fresh_acceptance,
)

__all__ = [
    "group",
    "invited",
    "members",
    "owner",
    "test_edition_ban_survives_group_reinstatement",
    "test_owner_removal_cannot_be_bypassed_and_reinstatement_requires_fresh_acceptance",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def test_removal_serializes_with_repeated_invitation_acceptance(
    app: Flask, members: dict[str, int]
) -> None:
    with app.app_context():
        token_hash = db.session.get(PassportInvitation, members["passport"]).token_hash
    barrier = Barrier(2)

    def remove() -> None:
        with app.app_context():
            barrier.wait(timeout=10)
            memberships.remove(
                members["tenant"], members["user"], members["series"], members["basic"]
            )
            db.session.commit()

    def join() -> None:
        with app.app_context():
            # Cache the parent before the competing transaction, as real requests can.
            db.session.get(Passport, members["passport"])
            barrier.wait(timeout=10)
            try:
                invitations.join(
                    members["basic"], token_hash, members["passport"], consent_values()
                )
                db.session.commit()
            except BuilderError as exc:
                db.session.rollback()
                assert exc.status == 403

    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = workers.submit(remove), workers.submit(join)
        first.result(timeout=15)
        second.result(timeout=15)
    with app.app_context():
        part = PassportParticipant.query.filter_by(
            passport_id=members["passport"], user_id=members["basic"]
        ).one()
        assert part.blocked and not part.is_active and not part.share_visits
        assert (
            PassportGroupBlock.query.filter_by(
                series_id=members["series"], user_id=members["basic"]
            ).count()
            == 1
        )
        with pytest.raises(BuilderError):
            invitations.join(
                members["basic"], token_hash, members["passport"], consent_values()
            )
