"""Shared voucher races and audit lifetimes on PostgreSQL, using real sessions."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from flask import Flask
from models import Passport, db
from models import PassportVoucherAllocation as Allocation
from models import PassportVoucherRedemption as Redemption
from passports import vouchers
from passports.management import BuilderError

from tests.postgres_schema import postgres_app
from tests.test_passport_management import owner
from tests.test_passport_vouchers import (
    booklet,
    test_account_deletion_scrubs_private_details_but_keeps_shared_use,
    test_builder_protects_vouchers_and_copy_is_fresh,
    test_correction_undo_idempotency_and_budget,
    test_next_edition_inherits_per_person_allowance_without_enrolling_friends,
    test_shared_consumption_survives_participant_deletion,
    test_voucher_http_privacy_mutation_and_archived_forms,
    use,
)

__all__ = [
    "booklet",
    "owner",
    "test_account_deletion_scrubs_private_details_but_keeps_shared_use",
    "test_builder_protects_vouchers_and_copy_is_fresh",
    "test_correction_undo_idempotency_and_budget",
    "test_next_edition_inherits_per_person_allowance_without_enrolling_friends",
    "test_shared_consumption_survives_participant_deletion",
    "test_voucher_http_privacy_mutation_and_archived_forms",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


@pytest.mark.parametrize("kind", ["personal", "shared"])
def test_last_voucher_cannot_be_overspent(
    app: Flask, booklet: dict[str, int], kind: str
) -> None:
    b = booklet
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        pool = vouchers.configure(
            p,
            b["user"],
            b["target"],
            {"quantity": "1"},
            b["user"] if kind == "personal" else None,
        )
        db.session.commit()
        pool_id = pool.id
    barrier = Barrier(2)

    def spend(user_id: int) -> str:
        with app.app_context():
            passport = db.session.get(Passport, b["passport"])
            barrier.wait(timeout=10)
            try:
                vouchers.redeem(passport, user_id, pool_id, use())
                db.session.commit()
                return "spent"
            except BuilderError as exc:
                db.session.rollback()
                assert exc.status == 409 and "not enough" in str(exc)
                return "empty"
            finally:
                db.session.remove()

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(spend, b["user"])
        second = workers.submit(spend, b["user"] if kind == "personal" else b["other"])
        assert sorted([first.result(timeout=20), second.result(timeout=20)]) == [
            "empty",
            "spent",
        ]
    with app.app_context():
        assert Redemption.query.filter_by(allocation_id=pool_id).count() == 1
        assert vouchers.used(db.session.get(Allocation, pool_id)) == 1


def test_cached_passport_refreshes_archive_under_lock(
    app: Flask, booklet: dict[str, int]
) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import update

    b = booklet
    with app.app_context():
        passport = db.session.get(Passport, b["passport"])
        assert passport.archived_at is None
        with db.engine.begin() as connection:
            connection.execute(
                update(Passport)
                .where(Passport.id == passport.id)
                .values(archived_at=datetime.now(UTC))
            )
        assert passport.archived_at is None  # the Session still has its old snapshot
        with pytest.raises(BuilderError, match="Reopen"):
            vouchers.configure(
                passport, b["user"], b["target"], {"quantity": "10"}, None
            )
        db.session.rollback()
        assert db.session.get(Allocation, b["shared"]).quantity == 2


def test_friends_each_spend_their_own_last_voucher(
    app: Flask, booklet: dict[str, int]
) -> None:
    b = booklet
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        for pool in Allocation.query.filter_by(passport_id=p.id).all():
            db.session.delete(pool)
        db.session.flush()
        vouchers.configure_default(p, b["user"], "1")
        db.session.commit()
        pools = {
            pool.personal_user_id: pool.id
            for pool in Allocation.query.filter_by(passport_id=p.id).all()
        }
    barrier = Barrier(2)

    def spend(user_id: int) -> None:
        with app.app_context():
            passport = db.session.get(Passport, b["passport"])
            barrier.wait(timeout=10)
            vouchers.redeem(passport, user_id, pools[user_id], use())
            db.session.commit()
            db.session.remove()

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(spend, b["user"])
        second = workers.submit(spend, b["other"])
        first.result(timeout=20)
        second.result(timeout=20)
    with app.app_context():
        assert Redemption.query.count() == 2
        for pool in Allocation.query.filter_by(passport_id=b["passport"]).all():
            assert vouchers.used(pool) == 1 and pool.quantity == 1
