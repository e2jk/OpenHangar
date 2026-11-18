"""Ownership offers serialize with removal and match the migrated PostgreSQL schema."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from flask import Flask
from models import (
    Passport,
    PassportOwnershipOffer,
    PassportParticipant,
    PassportSeries,
    db,
)
from passports import memberships, ownership
from passports.management import BuilderError

from tests.postgres_schema import postgres_app
from tests.test_passport_groups import group
from tests.test_passport_invitations import invited
from tests.test_passport_management import owner
from tests.test_passport_memberships import members
from tests.test_passport_ownership import (
    test_acceptance_transfers_only_group_editions_and_no_personal_vouchers,
    test_new_owner_manages_unjoined_edition_without_receiving_participation,
)
from tests.test_postgres_locks_and_migrations import (
    empty_schema,
    test_migrations_build_the_schema_the_models_describe,
)

__all__ = [
    "empty_schema",
    "group",
    "invited",
    "members",
    "owner",
    "test_acceptance_transfers_only_group_editions_and_no_personal_vouchers",
    "test_migrations_build_the_schema_the_models_describe",
    "test_new_owner_manages_unjoined_edition_without_receiving_participation",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def test_removal_and_acceptance_cannot_create_a_removed_group_owner(
    app: Flask, members: dict[str, int]
) -> None:
    with app.app_context():
        ownership.offer(
            members["tenant"], members["user"], members["series"], members["basic"]
        )
        db.session.commit()
        key = db.session.get(PassportOwnershipOffer, members["series"]).request_key
    barrier = Barrier(2)

    def operate(accept: bool) -> bool:
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                if accept:
                    ownership.respond(
                        members["tenant"],
                        members["basic"],
                        members["series"],
                        key,
                        accept=True,
                    )
                else:
                    memberships.remove(
                        members["tenant"],
                        members["user"],
                        members["series"],
                        members["basic"],
                    )
                db.session.commit()
                return True
            except BuilderError as exc:
                db.session.rollback()
                assert exc.status in {403, 404, 409}
                return False

    with ThreadPoolExecutor(max_workers=2) as workers:
        accepted, removed = (
            workers.submit(operate, True),
            workers.submit(operate, False),
        )
        outcomes = accepted.result(timeout=15), removed.result(timeout=15)
    assert sum(outcomes) == 1
    with app.app_context():
        series = db.session.get(PassportSeries, members["series"])
        part = PassportParticipant.query.filter_by(
            passport_id=members["passport"], user_id=members["basic"]
        ).one()
        if outcomes[0]:
            assert (
                series.owner_user_id == members["basic"]
                and part.is_active
                and not part.blocked
            )
        else:
            assert (
                series.owner_user_id == members["user"]
                and part.blocked
                and not part.is_active
            )
        assert (
            db.session.get(Passport, members["passport"]).owner_user_id
            == series.owner_user_id
        )
