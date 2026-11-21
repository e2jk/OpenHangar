"""Public views are independently projected and every request rechecks consent."""

from datetime import date, timedelta

from flask import Flask
from flask.testing import FlaskClient
from models import Passport, PassportParticipant, PassportVisit, PublicShare, Tenant, db
from passports import public_access

from tests.test_passport_builder import signin
from tests.test_passport_groups import group
from tests.test_passport_management import owner

__all__ = ["group", "owner"]


def settings(owner: dict[str, int]) -> str:
    return f"/passports/{owner['passport']}/public-sharing"


def enable(app: Flask, client: FlaskClient, owner: dict[str, int]) -> str:
    from services.public_shares import recover

    signin(client, owner["user"])
    assert client.post(settings(owner), data={"action": "rotate"}).status_code == 302
    with app.app_context():
        return str(recover(PublicShare.query.one()))


def test_public_allowlist_and_immediate_withdrawal(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    token = enable(app, client, owner)
    base = f"/passports/public/{token}"
    with app.app_context():
        participant = PassportParticipant.query.filter_by(
            passport_id=owner["passport"]
        ).one()
        db.session.add(
            PassportVisit(
                passport_id=owner["passport"],
                participant_id=participant.id,
                target_id=owner["target"],
                visit_date=date(2024, 6, 17),
                author_user_id=owner["user"],
                note="SECRET NOTE",
                evidence_kind="manual",
            )
        )
        db.session.commit()
    assert not client.get(base + "/data.json").json["targets"][0]["visited"]
    assert (
        client.post(
            settings(owner), data={"action": "choices", "publish_visits": "1"}
        ).status_code
        == 302
    )
    data = client.get(base + "/data.json").json
    assert data["targets"][0]["month"] == "2024-06"
    assert data["targets"][0]["frequency"] is None
    for suffix in ("", "/data.json", "/print", "/print?photos=1"):
        response = client.get(base + suffix)
        assert response.status_code == 200
        assert "no-store" in response.headers["Cache-Control"]
        for secret in (
            "SECRET NOTE",
            "2024-06-17",
            "builder@test.example",
            '"events"',
            '"user_id"',
            '"flight_id"',
        ):
            assert secret not in response.get_data(as_text=True)
    assert (
        client.post(
            settings(owner),
            data={"action": "choices", "publish_visits": "1", "publish_frequency": "1"},
        ).status_code
        == 302
    )
    assert client.get(base + "/data.json").json["targets"][0]["frequency"] == 1
    assert client.post(settings(owner), data={"action": "withdraw"}).status_code == 302
    assert not client.get(base + "/data.json").json["targets"][0]["visited"]
    assert client.post(settings(owner), data={"action": "rotate"}).status_code == 302
    for suffix in ("", "/data.json", "/print", "/photos/1"):
        response = client.get(base + suffix)
        assert (
            response.status_code == 404
            and "no-store" in response.headers["Cache-Control"]
        )
    with app.app_context():
        from services.public_shares import recover

        token = str(recover(PublicShare.query.one()))
    assert client.post(settings(owner), data={"action": "revoke"}).status_code == 302
    assert client.get(f"/passports/public/{token}").status_code == 404


def test_public_controls_errors_and_access(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    assert client.get(settings(owner)).status_code == 200
    for values, expected in (
        ({"action": "revoke"}, 302),
        ({"action": "bad"}, 403),
        ({"action": "rotate", "expires_on": "invalid"}, 400),
        ({"action": "rotate", "expires_on": "2000-01-01"}, 400),
        ({"action": "choices", "publish_frequency": "1"}, 400),
        ({"action": "choices", "photos": "invalid"}, 400),
        ({"action": "choices", "photos": ["1"] * 5001}, 400),
        ({"action": "choices", "publish_visits": "1", "public_name": "x" * 129}, 400),
        ({"action": "choices", "publish_visits": "1", "photos": "999"}, 404),
    ):
        assert client.post(settings(owner), data=values).status_code == expected
    assert (
        client.post(
            settings(owner), data={"action": "rotate", "expires_on": "2099-01-01"}
        ).status_code
        == 302
    )
    assert client.get(settings(owner)).status_code == 200
    signin(client, owner["other"])
    assert client.get(settings(owner)).status_code == 404
    assert client.get("/passports/public/invalid").status_code == 404


def test_live_resource_and_consent_epoch(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    token = enable(app, client, owner)
    with app.app_context():
        passport = db.session.get(Passport, owner["passport"])
        participant = PassportParticipant.query.filter_by(passport_id=passport.id).one()
        participant.public_shared_at = participant.accepted_at - timedelta(seconds=1)
        assert not public_access.publishes_visits(passport, participant)
        assert not public_access.current_consent(None, participant)
        assert public_access.resolve(token) is not None
        participant.is_active = False
        db.session.commit()
    assert client.get(f"/passports/public/{token}").status_code == 404
    with app.app_context():
        participant = PassportParticipant.query.one()
        participant.is_active = True
        tenant = db.session.get(Tenant, owner["tenant"])
        tenant.is_active = False
        db.session.commit()
    assert client.get(f"/passports/public/{token}").status_code == 404
    with app.app_context():
        db.session.get(Tenant, owner["tenant"]).is_active = True
        db.session.get(Passport, owner["passport"]).owner_user_id = owner["other"]
        db.session.commit()
    assert client.get(f"/passports/public/{token}").status_code == 404


def test_explicit_photo_publication_and_rejoin(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    from models import PassportStampPhoto
    from passports.evidence import photo_path

    from tests.test_passport_stamps import image_bytes

    token = enable(app, client, owner)
    with app.app_context():
        participant = PassportParticipant.query.one()
        path = photo_path("public-test.jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_bytes())
        photo = PassportStampPhoto(
            passport_id=owner["passport"],
            participant_id=participant.id,
            target_id=owner["target"],
            uploader_user_id=owner["user"],
            filename=path.name,
        )
        db.session.add(photo)
        db.session.commit()
        photo_id = photo.id
    base = f"/passports/public/{token}"
    photo_url = base + f"/photos/{photo_id}"
    try:
        assert client.get(photo_url).status_code == 404
        assert (
            client.post(
                settings(owner),
                data={
                    "action": "choices",
                    "publish_visits": "1",
                    "public_name": "Chosen public credit",
                    "photos": str(photo_id),
                },
            ).status_code
            == 302
        )
        response = client.get(photo_url, headers={"If-None-Match": "*"})
        assert (
            response.status_code == 200
            and "no-store" in response.headers["Cache-Control"]
        )
        assert "ETag" not in response.headers
        assert "Last-Modified" not in response.headers
        data = client.get(base + "/data.json").json
        assert data["targets"][0]["photos"] == [
            {"url": photo_url, "name": "Chosen public credit"}
        ]
        assert "Chosen public credit" in client.get(base).text
        assert "Chosen public credit" in client.get(base + "/print?photos=1").text
        assert "Chosen public credit" not in client.get(base + "/print").text
        assert client.get(base + "/photos/99999").status_code == 404
        with app.app_context():
            participant = PassportParticipant.query.one()
            participant.accepted_at += timedelta(days=1)
            db.session.commit()
        assert client.get(photo_url).status_code == 404
        assert client.get(base + "/data.json").json["targets"][0]["photos"] == []
        with app.app_context():
            participant = PassportParticipant.query.one()
            participant.accepted_at -= timedelta(days=1)
            db.session.commit()
        path.unlink()
        assert client.get(photo_url).status_code == 404
        assert client.get(base + "/data.json").json["targets"][0]["photos"] == []
        assert (
            client.post(settings(owner), data={"action": "choices"}).status_code == 302
        )
        assert client.get(photo_url).status_code == 404
    finally:
        path.unlink(missing_ok=True)


def test_group_publication_is_each_participants_choice(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    from unittest.mock import patch

    from models import PassportSeries
    from passports.management import BuilderError

    world = {**group, "passport": group["new"]}
    token = enable(app, client, world)
    base = f"/passports/public/{token}"
    assert (
        client.post(
            settings(world), data={"action": "choices", "publish_visits": "1"}
        ).status_code
        == 302
    )
    assert client.get(base + "/data.json").status_code == 200
    signin(client, group["other"])
    assert client.post(settings(world), data={"action": "rotate"}).status_code == 403
    assert (
        client.post(
            settings(world), data={"action": "choices", "publish_visits": "1"}
        ).status_code
        == 302
    )
    assert client.get(base + "/data.json").json["targets"][0]["visited"]
    with app.app_context():
        participant = PassportParticipant.query.filter_by(
            passport_id=group["new"], user_id=group["other"]
        ).one()
        participant.share_visits = False
        db.session.commit()
    assert (
        client.post(
            settings(world), data={"action": "choices", "publish_visits": "1"}
        ).status_code
        == 400
    )
    assert client.post(settings(world), data={"action": "choices"}).status_code == 302
    with patch(
        "passports.public_routes.groups.lock_passport",
        side_effect=BuilderError("Busy", 409),
    ):
        assert (
            client.post(settings(world), data={"action": "choices"}).status_code == 409
        )
    with app.app_context():
        passport = db.session.get(Passport, group["new"])
        series = db.session.get(PassportSeries, passport.series_id)
        series.is_group = False
        db.session.commit()
    assert client.get(base).status_code == 404


def test_withdrawal_is_possible_for_view_only_participant(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:

    from models import User

    token = enable(app, client, owner)
    with app.app_context():
        db.session.get(User, owner["user"]).view_only = True
        db.session.commit()
    assert client.post(settings(owner), data={"action": "choices"}).status_code == 302
    assert (
        client.post(
            settings(owner), data={"action": "choices", "publish_visits": "1"}
        ).status_code
        == 403
    )
    assert client.post(settings(owner), data={"action": "rotate"}).status_code == 403
    assert client.post(settings(owner), data={"action": "revoke"}).status_code == 302
    assert client.get(f"/passports/public/{token}").status_code == 404
    with app.app_context():
        participant = PassportParticipant.query.one()
        participant.accepted_at = None
        db.session.commit()
    assert client.post(settings(owner), data={"action": "choices"}).status_code == 404


def test_passport_only_account_can_open_capabilities_without_private_chrome(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    from models import TenantUser
    from passports import tokens

    token = enable(app, client, owner)
    with app.app_context():
        member = db.session.get(TenantUser, (owner["user"], owner["tenant"]))
        member.access_scope, member.role = "passport", None
        db.session.commit()
        invitation_token = tokens.create("invite", owner["passport"])[1]
    with client.session_transaction() as sess:
        sess.pop("auth_session", None)
    assert client.get(settings(owner)).status_code == 200
    response = client.get(f"/passports/public/{token}")
    assert response.status_code == 200
    assert "builder@test.example" not in response.text
    assert client.get(f"/passports/public/{invitation_token}").status_code == 404
    for suffix in ("/photos/999999999999999999999999999", "/missing"):
        response = client.get(f"/passports/public/{token}" + suffix)
        assert response.status_code in (403, 404)
        assert response.mimetype == "text/plain"
        assert "builder@test.example" not in response.text
        assert response.headers["Referrer-Policy"] == "no-referrer"
