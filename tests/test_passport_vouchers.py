"""Voucher use is explicit, private, bounded and auditable independently of stamps."""

import re
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Flight,
    LogbookEntryType,
    Passport,
    PassportAirfield,
    PassportParticipant,
    User,
    db,
)
from models import (
    PassportVoucherAllocation as Allocation,
)
from models import (
    PassportVoucherRedemption as Redemption,
)
from passports import vouchers
from passports.eligibility import passport_progress
from passports.management import BuilderError, fingerprint, protected_targets
from sqlalchemy.exc import IntegrityError

from tests.auth_helpers import set_membership_flags
from tests.test_passport_builder import posted, signin
from tests.test_passport_eligibility import flight
from tests.test_passport_management import owner

__all__ = ["owner"]


def use(**values: Any) -> dict[str, str]:
    return {
        "redemption_date": "2025-06-01",
        "quantity": "1",
        "request_key": secrets.token_urlsafe(24),
        **values,
    }


@pytest.fixture
def booklet(app: Flask, owner: dict[str, int]) -> dict[str, int]:
    with app.app_context():
        passport = db.session.get(Passport, owner["passport"])
        passport.start_date, passport.end_date = date(2025, 1, 1), date(2025, 12, 31)
        db.session.add(
            PassportParticipant(
                passport_id=passport.id,
                user_id=owner["other"],
                display_name="Friend",
                accepted_at=datetime.now(UTC),
            )
        )
        personal = vouchers.configure(
            passport, owner["user"], owner["target"], {"quantity": "4"}, owner["user"]
        )
        shared = vouchers.configure(
            passport, owner["user"], owner["target"], {"quantity": "2"}, None
        )
        friend = vouchers.configure(
            passport, owner["user"], owner["target"], {"quantity": "3"}, owner["other"]
        )
        db.session.commit()
        return owner | {
            "personal": personal.id,
            "shared": shared.id,
            "friend": friend.id,
        }


def test_explicit_use_currency_totals_and_shared_privacy(
    app: Flask, booklet: dict[str, int]
) -> None:
    b = booklet
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        assert passport_progress(p, b["user"])["visited"] == 0
        vouchers.redeem(
            p,
            b["user"],
            b["personal"],
            use(saving_amount="12.34", currency="eur", note="Own private note"),
        )
        vouchers.redeem(
            p, b["user"], b["personal"], use(saving_amount="5", currency="USD")
        )
        vouchers.redeem(
            p, b["user"], b["personal"], use(saving_amount="0.66", currency="EUR")
        )
        friend_use = vouchers.redeem(
            p,
            b["other"],
            b["shared"],
            use(saving_amount="99", currency="GBP", note="FRIEND SECRET"),
        )
        db.session.commit()
        assert passport_progress(p, b["user"])["visited"] == 0
        data = vouchers.ledger(p, b["user"])
        assert len(data["redemptions"]) == 3
        assert data["savings"] == {"EUR": Decimal("13.00"), "USD": Decimal(5)}
        shared = next(
            row for row in data["pools"] if row["allocation"].id == b["shared"]
        )
        assert (shared["used"], shared["remaining"]) == (1, 1)
        assert all(row.id != friend_use.id for row in data["redemptions"])
        with pytest.raises(BuilderError):
            vouchers.redeem(p, b["user"], b["friend"], use())
        with pytest.raises(BuilderError):
            vouchers.undo(p, b["user"], friend_use.id)
        with pytest.raises(BuilderError):
            vouchers.redeem(p, b["user"], b["shared"], use(), replaces_id=friend_use.id)
        part = PassportParticipant.query.filter_by(
            passport_id=p.id, user_id=b["user"]
        ).one()
        part.is_active = False
        db.session.flush()
        assert vouchers.ledger(p, b["user"]) == {
            "pools": [],
            "redemptions": [],
            "savings": {},
        }


def test_correction_undo_idempotency_and_budget(
    app: Flask, booklet: dict[str, int]
) -> None:
    b = booklet
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        original_values = use(quantity="2", saving_amount="20", currency="EUR")
        original = vouchers.redeem(p, b["user"], b["shared"], original_values)
        db.session.commit()
        pool = db.session.get(Allocation, b["shared"])
        assert vouchers.used(pool) == 2
        with pytest.raises(BuilderError, match="already saved"):
            vouchers.redeem(p, b["user"], b["shared"], original_values)
        with pytest.raises(BuilderError, match="not enough"):
            vouchers.redeem(p, b["other"], b["shared"], use())
        corrected = vouchers.redeem(
            p,
            b["user"],
            b["shared"],
            use(quantity="1", saving_amount="7", currency="GBP"),
            replaces_id=original.id,
        )
        db.session.commit()
        assert original.voided_at is not None and corrected.replaces_id == original.id
        assert vouchers.used(pool) == 1
        assert vouchers.ledger(p, b["user"])["savings"] == {"GBP": Decimal(7)}
        with pytest.raises(BuilderError, match="already corrected"):
            vouchers.redeem(p, b["user"], b["shared"], use(), replaces_id=original.id)
        vouchers.undo(p, b["user"], corrected.id)
        vouchers.undo(p, b["user"], corrected.id)
        db.session.commit()
        assert vouchers.used(pool) == 0
        assert len(vouchers.ledger(p, b["user"])["redemptions"]) == 2
        assert vouchers.ledger(p, b["user"])["savings"] == {}


@pytest.mark.parametrize(
    "values",
    [
        {"quantity": "-1"},
        {"quantity": "100001"},
        {"quantity": "1.1"},
        {"quantity": "9" * 100},
        {"valid_from": "bad"},
        {"valid_from": "2025-08-01", "valid_until": "2025-01-01"},
        {"valid_from": "2024-12-31"},
        {"valid_until": "2026-01-01"},
    ],
)
def test_allocation_bounds(
    app: Flask, booklet: dict[str, int], values: dict[str, str]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, booklet["passport"])
        with pytest.raises(BuilderError):
            vouchers.configure(
                p,
                booklet["user"],
                booklet["target"],
                {"quantity": "2"} | values,
                booklet["user"],
            )


@pytest.mark.parametrize(
    "values",
    [
        {"quantity": "0"},
        {"quantity": "-1"},
        {"quantity": "100001"},
        {"quantity": "x"},
        {"redemption_date": ""},
        {"redemption_date": "bad"},
        {"redemption_date": "2024-12-31"},
        {"redemption_date": (date.today() + timedelta(days=1)).isoformat()},
        {"request_key": "bad"},
        {"note": "x" * 8001},
        {"saving_amount": "NaN", "currency": "EUR"},
        {"saving_amount": "sNaN", "currency": "EUR"},
        {"saving_amount": "Infinity", "currency": "EUR"},
        {"saving_amount": "-1", "currency": "EUR"},
        {"saving_amount": "10000000000", "currency": "EUR"},
        {"saving_amount": "1.001", "currency": "EUR"},
        {"saving_amount": "1", "currency": "€UR"},
        {"saving_amount": "1", "currency": "EU"},
        {"saving_amount": "", "currency": "EUR"},
        {"saving_amount": "1", "currency": ""},
        {"saving_amount": "x", "currency": "EUR"},
        {"saving_amount": "1" * 41, "currency": "EUR"},
        {"supporting_flight": "bad"},
        {"supporting_flight": "999999"},
    ],
)
def test_redemption_validation(
    app: Flask, booklet: dict[str, int], values: dict[str, str]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, booklet["passport"])
        with pytest.raises(BuilderError):
            vouchers.redeem(p, booklet["user"], booklet["personal"], use(**values))
        assert Redemption.query.count() == 0


def test_allocation_edit_history_guards_and_flights(
    app: Flask, booklet: dict[str, int]
) -> None:
    b = booklet
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        for values in [
            {"pic_user_id": b["other"]},
            {"date": date(2025, 6, 2)},
            {"entry_type": LogbookEntryType.FSTD},
        ]:
            f = flight(b["user"], **({"date": date(2025, 6, 1)} | values))
            db.session.flush()
            with pytest.raises(BuilderError):
                vouchers.redeem(
                    p, b["user"], b["personal"], use(supporting_flight=str(f.id))
                )
        f = flight(b["user"], date=date(2025, 6, 1))
        db.session.flush()
        row = vouchers.redeem(
            p, b["user"], b["personal"], use(supporting_flight=str(f.id))
        )
        db.session.commit()
        old_fingerprint = fingerprint(p)
        for params in [
            {"quantity": "0"},
            {"quantity": "4", "valid_from": "2025-07-01"},
        ]:
            with pytest.raises(BuilderError):
                vouchers.configure(p, b["user"], b["target"], params, b["user"])
        pool = vouchers.configure(
            p,
            b["user"],
            b["target"],
            {"quantity": "5", "valid_from": "2025-02-01", "valid_until": "2025-12-01"},
            b["user"],
        )
        db.session.flush()
        assert fingerprint(p) != old_fingerprint
        assert vouchers.protected_targets(p.id, {b["target"]}) == {b["target"]}
        assert protected_targets(p.id, {b["target"]}) == {b["target"]}
        with pytest.raises(BuilderError):
            vouchers.redeem(p, b["user"], pool.id, use(redemption_date="2025-01-01"))
        with pytest.raises(BuilderError):
            vouchers.redeem(p, b["user"], pool.id, use(redemption_date="2025-12-31"))
        with pytest.raises(BuilderError):
            vouchers.validate_dates(p, date(2025, 3, 1), None)
        pool.valid_from, pool.valid_until = None, None
        db.session.flush()
        with pytest.raises(BuilderError):
            vouchers.validate_dates(p, date(2025, 7, 1), None)
        vouchers.validate_dates(p, p.start_date, p.end_date)
        with pytest.raises(BuilderError):
            vouchers.delete_unused(p, b["user"], pool.id)
        with pytest.raises(BuilderError):
            vouchers.delete_unused(p, b["user"], 999999)
        vouchers.delete_unused(p, b["user"], b["friend"])
        db.session.delete(f)
        db.session.commit()
        assert db.session.get(Redemption, row.id).flight_id is None
        with pytest.raises(BuilderError):
            vouchers.configure(p, b["user"], 999999, {"quantity": "1"}, b["user"])
        with pytest.raises(BuilderError):
            vouchers.configure(p, b["user"], b["target"], {"quantity": "1"}, 999999)
        with pytest.raises(BuilderError):
            vouchers._context(Passport(id=999999, tenant_id=b["tenant"]), b["user"])


def test_shared_consumption_survives_participant_deletion(
    app: Flask, booklet: dict[str, int]
) -> None:
    b = booklet
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        row = vouchers.redeem(
            p,
            b["other"],
            b["shared"],
            use(note="Removed private data", saving_amount="5", currency="EUR"),
        )
        db.session.commit()
        participant = db.session.get(PassportParticipant, row.participant_id)
        db.session.delete(participant)
        db.session.commit()
        db.session.expire_all()
        saved = db.session.get(Redemption, row.id)
        assert saved.participant_id is None
        assert (
            saved.note is None
            and saved.saving_amount is None
            and saved.currency is None
        )
        assert vouchers.used(db.session.get(Allocation, b["shared"])) == 1


def test_voucher_http_privacy_mutation_and_archived_forms(
    app: Flask,
    client: FlaskClient,
    booklet: dict[str, int],
) -> None:
    b = booklet
    signin(client, b["user"])
    url = f"/passports/{b['passport']}/vouchers"
    assert client.get(url).status_code == 200
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        row = vouchers.redeem(
            p,
            b["other"],
            b["shared"],
            use(note="FRIEND PRIVATE", saving_amount="234.56", currency="GBP"),
        )
        db.session.commit()
        friend_row = row.id
    response = client.get(url)
    assert "FRIEND PRIVATE" not in response.text and "234.56" not in response.text
    assert client.get(url + f"?edit={friend_row}").status_code == 404
    assert client.get(url + "?edit=bad").status_code == 404
    assert client.post(url, data={"action": "bad"}).status_code == 400
    assert (
        client.post(
            url, data={"action": "undo", "redemption_id": "9" * 100}
        ).status_code
        == 400
    )
    assert (
        client.post(url, data={"action": "allocate", "scope": "bad"}).status_code == 400
    )
    response = client.post(
        url,
        data={
            "action": "redeem",
            "allocation_id": str(b["personal"]),
            **use(note="Own use"),
        },
    )
    assert response.status_code == 302
    with app.app_context():
        own = Redemption.query.filter(Redemption.id != friend_row).one()
        own_id = own.id
    assert client.get(url + f"?edit={own_id}").status_code == 200
    response = client.post(
        url,
        data={
            "action": "correct",
            "allocation_id": str(b["personal"]),
            "redemption_id": str(own_id),
            **use(note="Correction"),
        },
    )
    assert response.status_code == 302
    with app.app_context():
        corrected_id = Redemption.query.filter_by(replaces_id=own_id).one().id
    assert (
        client.post(
            url, data={"action": "undo", "redemption_id": str(corrected_id)}
        ).status_code
        == 302
    )
    assert (
        client.post(
            url,
            data={
                "action": "allocate",
                "scope": "personal",
                "personal_user_id": str(b["user"]),
                "target_id": str(b["target"]),
                "quantity": "5",
            },
        ).status_code
        == 302
    )
    assert (
        client.post(
            url,
            data={
                "action": "allocate",
                "scope": "shared",
                "target_id": str(b["target"]),
                "quantity": "0",
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            url, data={"action": "delete_allocation", "allocation_id": str(b["friend"])}
        ).status_code
        == 302
    )
    with patch(
        "passports.voucher_routes.db.session.commit",
        side_effect=IntegrityError("statement", {}, Exception()),
    ):
        assert (
            client.post(
                url, data={"action": "undo", "redemption_id": str(corrected_id)}
            ).status_code
            == 409
        )
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        p.archived_at = datetime.now(UTC)
        db.session.commit()
    assert client.get(url).status_code == 200
    assert (
        client.post(
            url, data={"action": "redeem", "allocation_id": str(b["personal"]), **use()}
        ).status_code
        == 409
    )
    with app.app_context():
        set_membership_flags(b["user"], view_only=True)
        db.session.commit()
    assert "Manage allocations" not in client.get(url).text
    signin(client, b["other"])
    assert "Own use" not in client.get(url).text
    assert (
        client.post(
            url,
            data={
                "action": "allocate",
                "scope": "shared",
                "target_id": str(b["target"]),
                "quantity": "4",
            },
        ).status_code
        == 404
    )


def test_builder_protects_vouchers_and_copy_is_fresh(
    app: Flask,
    client: FlaskClient,
    booklet: dict[str, int],
) -> None:
    b = booklet
    signin(client, b["user"])
    base = f"/passports/{b['passport']}"
    response = client.get(base + "/edit")
    response = client.post(
        base + "/edit", data=posted(response, f"remove:field:{b['field']}")
    )
    response = client.post(base + "/edit", data=posted(response, "review"))
    assert (
        response.status_code == 409
        and "Keep airfields with voucher allocations" in response.text
    )
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        vouchers.redeem(p, b["other"], b["shared"], use())
        db.session.commit()
    response = client.post(
        base + "/next-edition",
        data={
            "title": "2027 Booklet",
            "start_date": "2027-01-01",
            "end_date": "2027-12-31",
            "copy_allocations": "yes",
        },
    )
    response = client.post("/passports/new", data=posted(response, "review"))
    response = client.post("/passports/new", data=posted(response, "save"))
    assert response.status_code == 302
    with app.app_context():
        new = Passport.query.filter(Passport.id != b["passport"]).one()
        pools = Allocation.query.filter_by(passport_id=new.id).all()
        assert sorted(pool.quantity for pool in pools) == [2, 3, 4]
        assert all(
            pool.valid_from is None and pool.valid_until is None for pool in pools
        )
        assert Redemption.query.filter_by(passport_id=new.id).count() == 0
        assert PassportParticipant.query.filter_by(passport_id=new.id).count() == 1
        assert vouchers.ledger(new, b["other"])["pools"] == []
        assert vouchers.ledger(new, b["user"])["pools"][0]["future"]


def test_copy_limits_removed_targets_and_missing_members(
    app: Flask, booklet: dict[str, int]
) -> None:
    from models import TenantUser
    from passports.management import create_personal, metadata_values

    b = booklet
    with app.app_context():
        source = db.session.get(Passport, b["passport"])
        destination = create_personal(
            b["tenant"], b["user"], metadata_values({"title": "Empty next"})
        )
        db.session.flush()
        with pytest.raises(BuilderError):
            vouchers.copy_allocations(source, source, b["user"])
        with pytest.raises(BuilderError):
            vouchers.copy_allocations(
                source, Passport(id=9999, tenant_id=b["tenant"] + 1), b["user"]
            )
        vouchers.copy_allocations(source, destination, b["user"])
        assert Allocation.query.filter_by(passport_id=destination.id).count() == 0
        with (
            patch.object(vouchers, "MAX_ALLOCATION_COPIES", 2),
            pytest.raises(BuilderError, match="Copy at most"),
        ):
            vouchers.copy_preview(source, [b["field"]])
        db.session.add(
            PassportAirfield(
                tenant_id=b["tenant"],
                passport_id=destination.id,
                airfield_id=b["field"],
            )
        )
        db.session.delete(db.session.get(TenantUser, (b["other"], b["tenant"])))
        db.session.flush()
        vouchers.copy_allocations(source, destination, b["user"])
        db.session.flush()
        assert sorted(
            row.quantity
            for row in Allocation.query.filter_by(passport_id=destination.id).all()
        ) == [2, 4]


def test_account_deletion_scrubs_private_details_but_keeps_shared_use(
    app: Flask, booklet: dict[str, int]
) -> None:
    b = booklet
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        row = vouchers.redeem(
            p,
            b["other"],
            b["shared"],
            use(note="Private identity", saving_amount="7", currency="EUR"),
        )
        db.session.commit()
        redemption_id = row.id
        db.session.delete(db.session.get(User, b["other"]))
        db.session.commit()
        db.session.expire_all()
        row = db.session.get(Redemption, redemption_id)
        assert row.participant_id is None and row.note is None
        assert (
            row.saving_amount is None and row.currency is None and row.flight_id is None
        )
        assert vouchers.used(db.session.get(Allocation, b["shared"])) == 1


def test_correct_form_keeps_older_owned_flight_and_failed_correction_context(
    app: Flask,
    client: FlaskClient,
    booklet: dict[str, int],
) -> None:
    b = booklet
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        f = flight(b["user"], date=date(2025, 6, 1))
        db.session.flush()
        row = vouchers.redeem(
            p, b["user"], b["personal"], use(supporting_flight=str(f.id))
        )
        for offset in range(201):
            flight(b["user"], date=date(2025, 6, 2) + timedelta(days=offset % 60))
        db.session.commit()
        row_id, flight_id = row.id, f.id
    signin(client, b["user"])
    url = f"/passports/{b['passport']}/vouchers"
    response = client.get(url + f"?edit={row_id}")
    options = re.search(
        r'<select[^>]*id="voucher-flight".*?</select>', response.text, re.DOTALL
    )
    assert response.status_code == 200 and options is not None
    assert f'value="{flight_id}" selected' in options[0]
    response = client.post(
        url,
        data={
            "action": "correct",
            "redemption_id": str(row_id),
            "allocation_id": str(b["personal"]),
            **use(quantity="0"),
        },
    )
    assert (
        response.status_code == 400
        and f'name="redemption_id" value="{row_id}"' in response.text
    )
    with app.app_context():
        f = db.session.get(Flight, flight_id)
        f.pic_user_id = b["other"]
        db.session.commit()
    response = client.get(url + f"?edit={row_id}")
    options = re.search(
        r'<select[^>]*id="voucher-flight".*?</select>', response.text, re.DOTALL
    )
    assert response.status_code == 200 and options is not None
    assert f'value="{flight_id}" selected' not in options[0]


def test_invalid_voucher_fields_are_bounded_on_redisplay(
    client: FlaskClient, booklet: dict[str, int]
) -> None:
    b = booklet
    signin(client, b["user"])
    response = client.post(
        f"/passports/{b['passport']}/vouchers",
        data={
            "action": "redeem",
            "allocation_id": str(b["personal"]),
            **use(note="PRIVATE" * 10000, saving_amount="7" * 10000),
        },
    )
    assert response.status_code == 400
    assert len(response.data) < 100000
    assert "PRIVATE" * 2000 not in response.text


def test_copy_allocation_choice_can_be_changed_in_builder(
    app: Flask, client: FlaskClient, booklet: dict[str, int]
) -> None:
    b = booklet
    signin(client, b["user"])
    response = client.post(
        f"/passports/{b['passport']}/next-edition",
        data={"title": "No allowances", "copy_allocations": "yes"},
    )
    response = client.post(
        "/passports/new",
        data=posted(
            response, "review", edition_copy_options="yes", copy_allocations=""
        ),
    )
    assert (
        response.status_code == 200 and "New voucher allocations" not in response.text
    )
    response = client.post("/passports/new", data=posted(response, "save"))
    assert response.status_code == 302
    with app.app_context():
        new = Passport.query.filter(Passport.id != b["passport"]).one()
        assert Allocation.query.filter_by(passport_id=new.id).count() == 0


def test_default_vouchers_belong_to_each_person_and_never_reset_used_balances(
    app: Flask,
    booklet: dict[str, int],
) -> None:
    from models import Role, TenantUser

    b = booklet
    with app.app_context():
        p = db.session.get(Passport, b["passport"])
        for pool in Allocation.query.filter_by(passport_id=p.id).all():
            db.session.delete(pool)
        db.session.flush()
        vouchers.configure_default(p, b["user"], "1")
        db.session.commit()
        pools = Allocation.query.filter_by(passport_id=p.id).all()
        assert len(pools) == 2 and all(
            pool.personal_user_id is not None for pool in pools
        )
        ids = {pool.personal_user_id: pool.id for pool in pools}
        vouchers.redeem(p, b["user"], ids[b["user"]], use())
        db.session.commit()
        assert vouchers.ledger(p, b["user"])["pools"][0]["remaining"] == 0
        assert vouchers.ledger(p, b["other"])["pools"][0]["remaining"] == 1
        vouchers.redeem(p, b["other"], ids[b["other"]], use())
        db.session.commit()
        assert vouchers.ledger(p, b["other"])["pools"][0]["remaining"] == 0
        vouchers.provision_participant(p, b["other"])
        vouchers.configure_default(p, b["user"], "5")
        assert Allocation.query.filter_by(passport_id=p.id).count() == 2
        assert vouchers.ledger(p, b["user"])["pools"][0]["remaining"] == 0
        late = User(email="later@voucher.example", password_hash="unused", name="Later")
        db.session.add(late)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=late.id, tenant_id=b["tenant"], role=Role.VIEWER)
        )
        participant = PassportParticipant(
            passport_id=p.id, user_id=late.id, display_name="Later"
        )
        db.session.add(participant)
        db.session.flush()
        vouchers.configure_default(p, b["user"], "2")
        assert Allocation.query.filter_by(personal_user_id=late.id).count() == 0
        with pytest.raises(BuilderError):
            vouchers.provision_participant(p, late.id)
        participant.accepted_at = datetime.now(UTC)
        db.session.flush()
        vouchers.provision_participant(p, late.id)
        db.session.commit()
        assert vouchers.ledger(p, late.id)["pools"][0]["remaining"] == 2
        vouchers.configure_default(p, b["user"], "0")
        vouchers.provision_participant(p, late.id)
        assert vouchers.ledger(p, late.id)["pools"][0]["remaining"] == 2


def test_owner_can_set_per_person_default_through_form(
    app: Flask,
    client: FlaskClient,
    owner: dict[str, int],
) -> None:
    signin(client, owner["user"])
    response = client.post(
        f"/passports/{owner['passport']}/vouchers",
        data={"action": "default", "quantity": "1"},
    )
    assert response.status_code == 302
    response = client.get(response.location)
    assert "Each participant has one voucher per airfield." in response.text
    with app.app_context():
        pool = Allocation.query.one()
        assert pool.personal_user_id == owner["user"] and pool.quantity == 1


def test_next_edition_inherits_per_person_allowance_without_enrolling_friends(
    app: Flask,
    client: FlaskClient,
    booklet: dict[str, int],
) -> None:
    b = booklet
    with app.app_context():
        old = db.session.get(Passport, b["passport"])
        for pool in Allocation.query.filter_by(passport_id=old.id).all():
            db.session.delete(pool)
        db.session.flush()
        vouchers.configure_default(old, b["user"], "1")
        own_pool = Allocation.query.filter_by(
            passport_id=old.id, personal_user_id=b["user"]
        ).one()
        vouchers.redeem(old, b["user"], own_pool.id, use())
        vouchers.configure(old, b["user"], b["target"], {"quantity": "2"}, b["other"])
        db.session.commit()
    signin(client, b["user"])
    response = client.post(
        f"/passports/{b['passport']}/next-edition",
        data={"title": "Next personal booklet", "copy_allocations": "yes"},
    )
    response = client.post("/passports/new", data=posted(response, "review"))
    assert (
        response.status_code == 200
        and "Each participant has one voucher per airfield." in response.text
    )
    response = client.post("/passports/new", data=posted(response, "save"))
    assert response.status_code == 302
    with app.app_context():
        new = Passport.query.filter(Passport.id != b["passport"]).one()
        assert new.default_voucher_quantity == 1
        assert vouchers.ledger(new, b["user"])["pools"][0]["remaining"] == 1
        assert vouchers.ledger(new, b["other"])["pools"] == []
        db.session.add(
            PassportParticipant(
                passport_id=new.id,
                user_id=b["other"],
                display_name="Friend",
                accepted_at=datetime.now(UTC),
            )
        )
        db.session.flush()
        vouchers.provision_participant(new, b["other"])
        assert vouchers.ledger(new, b["other"])["pools"][0]["remaining"] == 2
        assert Redemption.query.filter_by(passport_id=new.id).count() == 0
