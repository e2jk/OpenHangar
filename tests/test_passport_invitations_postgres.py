"""Real schema integrity, bounded lock waits and concurrent invitation joins."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from time import monotonic

import pytest
from flask import Flask
from models import (
    Passport,
    PassportInvitation,
    PassportParticipant,
    PassportVoucherAllocation,
    db,
)
from passports import groups, invitations
from passports.management import BuilderError
from sqlalchemy import text

from tests.postgres_schema import postgres_app
from tests.test_passport_groups import consent_values
from tests.test_passport_invitations import (
    invited,
    test_join_preserves_roles_and_personal_vouchers,
)
from tests.test_passport_management import owner

__all__ = ["invited", "owner", "test_join_preserves_roles_and_personal_vouchers"]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def test_concurrent_join_has_one_participant_and_allowance(
    app: Flask, invited: dict[str, int]
) -> None:
    with app.app_context():
        token_hash = db.session.get(PassportInvitation, invited["passport"]).token_hash
    barrier = Barrier(2)

    def join() -> int:
        with app.app_context():
            barrier.wait(timeout=10)
            p = invitations.join(
                invited["other"], token_hash, invited["passport"], consent_values()
            )
            db.session.commit()
            return p.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: join(), range(2)))
    assert results == [invited["passport"]] * 2
    with app.app_context():
        assert (
            PassportParticipant.query.filter_by(
                passport_id=invited["passport"], user_id=invited["other"]
            ).count()
            == 1
        )
        assert (
            PassportVoucherAllocation.query.filter_by(
                passport_id=invited["passport"], personal_user_id=invited["other"]
            ).count()
            == 1
        )


def test_membership_mutex_and_parent_waits_are_bounded(
    app: Flask, invited: dict[str, int]
) -> None:
    with app.app_context():
        engine = db.engine
        token_hash = db.session.get(PassportInvitation, invited["passport"]).token_hash
        db.session.rollback()
        with engine.connect() as holder:
            txn = holder.begin()
            holder.execute(
                text("SELECT pg_advisory_xact_lock(:ns, :sid)"),
                {"ns": 0x4F485047, "sid": invited["series"]},
            )
            started = monotonic()
            with pytest.raises(BuilderError, match="being updated"):
                groups.lock_series(invited["series"])
            assert monotonic() - started < 3
            db.session.rollback()
            txn.rollback()
            txn = holder.begin()
            holder.execute(
                text("SELECT id FROM passports WHERE id=:pid FOR UPDATE"),
                {"pid": invited["passport"]},
            )
            with pytest.raises(BuilderError, match="being updated"):
                invitations.join(
                    invited["other"], token_hash, invited["passport"], consent_values()
                )
            db.session.rollback()
            with pytest.raises(BuilderError, match="being updated"):
                invitations.owner_passport(
                    invited["tenant"], invited["user"], invited["passport"]
                )
            db.session.rollback()
            with pytest.raises(BuilderError, match="being updated"):
                groups.enable(
                    db.session.get(Passport, invited["passport"]),
                    invited["user"],
                    consent_values(),
                )
            db.session.rollback()
            txn.rollback()


def test_one_pending_join_cannot_enrol_two_accounts(
    app: Flask, invited: dict[str, int]
) -> None:
    from flask import session
    from models import User

    from tests.test_passport_invitations import raw_token

    with app.test_request_context():
        users = [
            User(email=f"concurrent-{n}@example.test", password_hash="unused")
            for n in range(2)
        ]
        db.session.add_all(users)
        db.session.commit()
        user_ids = [user.id for user in users]
        p = db.session.get(Passport, invited["passport"])
        invitations.prepare(raw_token(p), consent_values())
        cookie = session[invitations.JOIN_KEY]
    barrier = Barrier(2)

    def complete(uid: int) -> int:
        with app.test_request_context():
            session[invitations.JOIN_KEY] = cookie
            user = db.session.get(User, uid)
            barrier.wait(timeout=10)
            try:
                invitations.complete(user)
                db.session.commit()
                return uid
            except BuilderError as exc:
                db.session.rollback()
                assert exc.status == 403
                return 0

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(complete, user_ids))
    assert len([uid for uid in results if uid]) == 1
    with app.app_context():
        assert (
            PassportParticipant.query.filter(
                PassportParticipant.passport_id == invited["passport"],
                PassportParticipant.user_id.in_(user_ids),
            ).count()
            == 1
        )
