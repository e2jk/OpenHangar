"""Explicit ownership acceptance, stale-offer rejection and permission isolation."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Passport,
    PassportOwnershipOffer,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    TenantUser,
    User,
    db,
)
from passports import editions, groups, memberships, ownership, vouchers
from passports.management import BuilderError

from tests.test_passport_builder import signin
from tests.test_passport_editions import extra
from tests.test_passport_groups import consent_values, group
from tests.test_passport_invitations import invited
from tests.test_passport_management import owner
from tests.test_passport_memberships import members

__all__ = ["group", "invited", "members", "owner"]


def ledger_values(passport: Passport, user_id: int) -> dict[str, Any]:
    ledger = vouchers.ledger(passport, user_id)
    return {
        "pools": [
            (p["allocation"].id, p["allocation"].quantity, p["used"], p["remaining"])
            for p in ledger["pools"]
        ],
        "redemptions": [
            (r.id, r.quantity, r.note, r.redemption_date, r.voided_at)
            for r in ledger["redemptions"]
        ],
        "savings": ledger["savings"],
    }


def test_acceptance_transfers_only_group_editions_and_no_personal_vouchers(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, members["passport"])
        next_p = extra(members, "Another group edition")
        editions.link(next_p, members["user"], series_id=members["series"])
        groups.enable(next_p, members["user"], consent_values("Organiser"))
        private = extra(members, "Private linked edition")
        editions.link(private, members["user"], series_id=members["series"])
        db.session.commit()
        group_ids, private_id = [p.id, next_p.id], private.id
        before = ledger_values(p, members["other"])
        ownership.offer(
            members["tenant"], members["user"], members["series"], members["basic"]
        )
        db.session.commit()
        key = db.session.get(PassportOwnershipOffer, members["series"]).request_key
        assert (
            db.session.get(PassportSeries, members["series"]).owner_user_id
            == members["user"]
        )
        assert (
            ownership.view(
                db.session.get(PassportSeries, members["series"]), members["other"]
            )
            is None
        )
    path = f"/passports/series/{members['series']}/members"
    signin(client, members["basic"])
    page = client.get(path)
    assert b"Accept group ownership" in page.data and key.encode() in page.data
    assert (
        client.post(
            path, data={"action": "accept-ownership", "request_key": key}
        ).status_code
        == 302
    )
    with app.app_context():
        series = db.session.get(PassportSeries, members["series"])
        assert series.owner_user_id == members["basic"]
        assert all(
            db.session.get(Passport, pid).owner_user_id == members["basic"]
            for pid in group_ids
        )
        assert db.session.get(Passport, private_id).owner_user_id == members["user"]
        assert not db.session.get(PassportOwnershipOffer, series.id)
        assert (
            db.session.get(TenantUser, (members["basic"], members["tenant"])).role
            is None
        )
        assert not db.session.get(User, members["basic"]).is_pilot
        assert (
            ledger_values(
                db.session.get(Passport, members["passport"]), members["other"]
            )
            == before
        )
        memberships.leave(members["tenant"], members["user"], series.id)
        db.session.commit()
        assert not db.session.get(
            PassportSeriesMember, (series.id, members["user"])
        ).is_active
    signin(client, members["user"])
    assert client.get(path).status_code == 404


def test_offer_replacement_decline_cancel_and_replay(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    path = f"/passports/series/{members['series']}/members"
    signin(client, members["user"])
    assert client.post(path, data={"action": "offer-ownership"}).status_code == 400
    assert (
        client.post(
            path, data={"action": "offer-ownership", "user_id": members["basic"]}
        ).status_code
        == 302
    )
    with app.app_context():
        old = db.session.get(PassportOwnershipOffer, members["series"]).request_key
    assert (
        client.post(
            path, data={"action": "offer-ownership", "user_id": members["basic"]}
        ).status_code
        == 302
    )
    with app.app_context():
        key = db.session.get(PassportOwnershipOffer, members["series"]).request_key
        assert key != old
    signin(client, members["basic"])
    assert (
        client.post(
            path, data={"action": "accept-ownership", "request_key": old}
        ).status_code
        == 409
    )
    assert (
        client.post(
            path, data={"action": "decline-ownership", "request_key": key}
        ).status_code
        == 302
    )
    assert (
        client.post(
            path, data={"action": "accept-ownership", "request_key": key}
        ).status_code
        == 409
    )
    signin(client, members["user"])
    assert (
        client.post(
            path, data={"action": "cancel-ownership", "request_key": "é"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            path, data={"action": "offer-ownership", "user_id": members["basic"]}
        ).status_code
        == 302
    )
    with app.app_context():
        key = db.session.get(PassportOwnershipOffer, members["series"]).request_key
        db.session.get(PassportOwnershipOffer, members["series"]).expires_at = (
            datetime.now(UTC) - timedelta(seconds=1)
        )
        db.session.commit()
    assert (
        client.post(
            path, data={"action": "cancel-ownership", "request_key": key}
        ).status_code
        == 302
    )


@pytest.mark.parametrize(
    "reason",
    [
        "self",
        "nonowner",
        "no-participation",
        "left",
        "removed",
        "readonly",
        "inactive",
        "expired",
        "changed-owner",
        "changed-edition-owner",
        "wrong-recipient",
    ],
)
def test_ownership_revalidates_actor_participation_and_offer(
    app: Flask, members: dict[str, int], reason: str
) -> None:
    with app.app_context():
        tenant, original, target, series_id = (
            members["tenant"],
            members["user"],
            members["basic"],
            members["series"],
        )
        if reason in {"self", "nonowner"}:
            with pytest.raises(BuilderError):
                ownership.offer(
                    tenant,
                    original if reason == "self" else target,
                    series_id,
                    original,
                )
            return
        ownership.offer(tenant, original, series_id, target)
        db.session.commit()
        row = db.session.get(PassportOwnershipOffer, series_id)
        key = row.request_key
        if reason == "no-participation":
            db.session.query(PassportParticipant).filter_by(user_id=target).update(
                {"is_active": False}
            )
        elif reason == "left":
            memberships.leave(tenant, target, series_id)
        elif reason == "removed":
            memberships.remove(tenant, original, series_id, target)
        elif reason == "readonly":
            db.session.get(User, target).view_only = True
        elif reason == "inactive":
            db.session.get(User, target).is_active = False
        elif reason == "expired":
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif reason == "changed-owner":
            db.session.get(PassportSeries, series_id).owner_user_id = members["other"]
        elif reason == "changed-edition-owner":
            db.session.get(Passport, members["passport"]).owner_user_id = members[
                "other"
            ]
        db.session.commit()
        with pytest.raises(BuilderError):
            ownership.respond(
                tenant,
                members["other"] if reason == "wrong-recipient" else target,
                series_id,
                key,
                accept=True,
            )
        db.session.rollback()
        assert db.session.get(PassportSeries, series_id).owner_user_id != target


def test_offer_view_handles_deleted_membership(
    app: Flask, members: dict[str, int]
) -> None:
    with app.app_context():
        series = db.session.get(PassportSeries, members["series"])
        assert ownership.view(series, members["user"]) is None
        ownership.offer(members["tenant"], members["user"], series.id, members["basic"])
        db.session.commit()
        member = db.session.get(PassportSeriesMember, (series.id, members["basic"]))
        db.session.delete(member)
        db.session.commit()
        assert ownership.view(series, members["user"]) is None


def test_new_owner_manages_unjoined_edition_without_receiving_participation(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    from models import PassportVoucherAllocation
    from passports import invitations

    from tests.test_passport_invitations import raw_token

    with app.app_context():
        p = extra(members, "Unjoined inherited edition")
        p.default_voucher_quantity = 1
        editions.link(p, members["user"], series_id=members["series"])
        groups.enable(p, members["user"], consent_values("Owner"))
        invitations.replace(p)
        ownership.offer(
            members["tenant"], members["user"], members["series"], members["basic"]
        )
        db.session.flush()
        key = db.session.get(PassportOwnershipOffer, members["series"]).request_key
        ownership.respond(
            members["tenant"], members["basic"], members["series"], key, accept=True
        )
        db.session.commit()
        pid, token = p.id, raw_token(p)
        assert not PassportParticipant.query.filter_by(
            passport_id=pid, user_id=members["basic"]
        ).count()
        assert not PassportVoucherAllocation.query.filter_by(
            passport_id=pid, personal_user_id=members["basic"]
        ).count()
    signin(client, members["basic"])
    index = client.get("/passports/")
    assert f'href="/passports/{pid}/"'.encode() not in index.data
    assert client.get(f"/passports/{pid}/").status_code == 404
    page = client.get(f"/passports/series/{members['series']}/members")
    assert b"Unjoined inherited edition" in page.data
    assert f'href="/passports/{pid}/invitation"'.encode() in page.data
    settings = client.get(f"/passports/{pid}/invitation")
    assert settings.status_code == 200
    assert f'href="/passports/{pid}/"'.encode() not in settings.data
    assert client.get(f"/passports/join/{token}").status_code == 200
    assert (
        client.post(
            f"/passports/join/{token}", data=consent_values("Basic friend")
        ).status_code
        == 302
    )
    assert client.get(f"/passports/{pid}/").status_code == 200
    assert f'href="/passports/{pid}/"'.encode() in client.get("/passports/").data
    with app.app_context():
        assert (
            PassportParticipant.query.filter_by(
                passport_id=pid, user_id=members["basic"]
            ).count()
            == 1
        )
        assert (
            PassportVoucherAllocation.query.filter_by(
                passport_id=pid, personal_user_id=members["basic"]
            ).count()
            == 1
        )
