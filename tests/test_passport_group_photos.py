"""Stamp photos shared with an edition's group: explicit, withdrawable, never
public, and rechecked on every page and image request."""

import io
from datetime import UTC, datetime, timedelta

from flask import Flask
from flask.testing import FlaskClient
from models import (
    Passport,
    PassportAirfield,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    PassportStampPhoto,
    TenantUser,
    User,
    db,
)
from passports import photo_sharing

from tests.test_passport_builder import signin
from tests.test_passport_groups import group
from tests.test_passport_management import owner
from tests.test_passport_stamps import image_bytes

__all__ = ["group", "owner"]


def _target(group: dict[str, int]) -> int:
    return PassportAirfield.query.filter_by(passport_id=group["new"]).one().id


def _upload(client: FlaskClient, group: dict[str, int], target: int) -> str:
    base = f"/passports/{group['new']}/airfields/{target}"
    response = client.post(
        base,
        data={"action": "photo", "photo": (io.BytesIO(image_bytes()), "p.jpg")},
    )
    assert response.status_code == 302
    return base


def _share(client: FlaskClient, base: str, share: str = "yes") -> int:
    return client.post(base, data={"action": "share_photo", "share": share}).status_code


def _friend_photo(app: Flask, group: dict[str, int]) -> int:
    with app.app_context():
        participant = PassportParticipant.query.filter_by(
            passport_id=group["new"], user_id=group["other"]
        ).one()
        return (
            PassportStampPhoto.query.filter_by(participant_id=participant.id).one().id
        )


def _shared_setup(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> tuple[str, int]:
    with app.app_context():
        target = _target(group)
    signin(client, group["other"])
    base = _upload(client, group, target)
    page = client.get(base).text
    assert "Private stamp photo" in page and "Share with this group" in page
    assert _share(client, base) == 302
    page = client.get(base).text
    assert "Stamp photo shared with this group" in page
    assert "Stop sharing with the group" in page
    return base, _friend_photo(app, group)


def test_shared_photo_reaches_the_group_and_withdrawal_hides_it(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    base, photo_id = _shared_setup(app, client, group)
    image = f"/passports/{group['new']}/photos/{photo_id}"
    signin(client, group["user"])
    page = client.get(base).text
    assert "Photos shared by friends" in page
    assert "Stamp photo shared by Later friend" in page and image in page
    response = client.get(image)
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    # Withdrawal is immediate, for the page and for the image URL.
    signin(client, group["other"])
    assert _share(client, base, "no") == 302
    assert "Private stamp photo" in client.get(base).text
    signin(client, group["user"])
    assert "Photos shared by friends" not in client.get(base).text
    assert client.get(image).status_code == 404


def test_replacing_a_photo_or_rejoining_needs_a_new_choice(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    base, photo_id = _shared_setup(app, client, group)
    _upload(client, group, int(base.rsplit("/", 1)[1]))
    with app.app_context():
        assert db.session.get(PassportStampPhoto, photo_id).group_shared_at is None
    assert _share(client, base) == 302
    with app.app_context():
        participant = PassportParticipant.query.filter_by(
            passport_id=group["new"], user_id=group["other"]
        ).one()
        # A later acceptance (a rejoin) makes the earlier share stale.
        participant.accepted_at = datetime.now(UTC) + timedelta(minutes=1)
        db.session.commit()
        photo = db.session.get(PassportStampPhoto, photo_id)
        assert not photo_sharing.is_shared(photo, participant)
        new = db.session.get(Passport, group["new"])
        assert photo_sharing.visible_photo(new, photo_id, group["user"]) is None
    assert "Private stamp photo" in client.get(base).text


def test_lost_sharing_on_either_side_hides_the_photo(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    base, photo_id = _shared_setup(app, client, group)
    with app.app_context():
        new = db.session.get(Passport, group["new"])
        target = int(base.rsplit("/", 1)[1])

        def seen() -> bool:
            return bool(photo_sharing.friends_photos(new, target, group["user"]))

        assert seen()
        uploader = PassportParticipant.query.filter_by(
            passport_id=new.id, user_id=group["other"]
        ).one()
        uploader.share_visits = False
        db.session.flush()
        assert not seen()
        assert photo_sharing.visible_photo(new, photo_id, group["user"]) is None
        uploader.share_visits = True
        viewer = PassportParticipant.query.filter_by(
            passport_id=new.id, user_id=group["user"]
        ).one()
        viewer.share_visits = False
        db.session.flush()
        # A viewer who keeps their own visits private sees no friends' photos.
        assert not seen()
        assert photo_sharing.visible_photo(new, photo_id, group["user"]) is None
        viewer.share_visits = True
        member = db.session.get(PassportSeriesMember, (new.series_id, group["user"]))
        member.is_active = False
        db.session.flush()
        assert not seen()
        member.is_active = True
        db.session.get(User, group["other"]).is_active = False
        db.session.flush()
        assert not seen()
        db.session.get(User, group["other"]).is_active = True
        series = db.session.get(PassportSeries, new.series_id)
        series.is_group = False
        db.session.flush()
        assert not seen()
        series.is_group = True
        new.is_group = False
        db.session.flush()
        assert not seen()
        assert not photo_sharing.viewer_may_see(new, group["user"])


def test_photo_url_refused_to_outsiders_and_other_editions(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    _base, photo_id = _shared_setup(app, client, group)
    with app.app_context():
        outsider = User(email="outsider@test.example", password_hash="unused")
        db.session.add(outsider)
        db.session.flush()
        tenant = db.session.get(Passport, group["new"]).tenant_id
        db.session.add(
            TenantUser(
                tenant_id=tenant,
                user_id=outsider.id,
                role=TenantUser.query.filter_by(user_id=group["user"]).one().role,
            )
        )
        db.session.commit()
        outsider_id = outsider.id
        old = db.session.get(Passport, group["old"])
        # The photo belongs to the 2025 edition, never to another one.
        assert photo_sharing.visible_photo(old, photo_id, group["user"]) is None
        new = db.session.get(Passport, group["new"])
        assert not photo_sharing.viewer_may_see(new, outsider_id)
        assert photo_sharing.friends_photos(new, 1, outsider_id) == []
    signin(client, outsider_id)
    assert client.get(f"/passports/{group['new']}/photos/{photo_id}").status_code == 404
    signin(client, group["user"])
    assert client.get(f"/passports/{group['old']}/photos/{photo_id}").status_code == 404


def test_sharing_rules_for_archived_unshared_and_personal_passports(
    app: Flask, client: FlaskClient, group: dict[str, int], owner: dict[str, int]
) -> None:
    base, photo_id = _shared_setup(app, client, group)
    with app.app_context():
        new = db.session.get(Passport, group["new"])
        new.archived_at = datetime.now(UTC)
        db.session.commit()
    # Archived: sharing is refused, withdrawing still works.
    assert _share(client, base, "yes") == 409
    assert _share(client, base, "no") == 302
    with app.app_context():
        assert db.session.get(PassportStampPhoto, photo_id).group_shared_at is None
        new = db.session.get(Passport, group["new"])
        new.archived_at = None
        PassportParticipant.query.filter_by(
            passport_id=new.id, user_id=group["other"]
        ).one().share_visits = False
        db.session.commit()
    page = client.get(base).text
    assert "Share with this group" not in page
    assert _share(client, base) == 409
    assert "Share your visits with this group before sharing photos." in (
        client.post(base, data={"action": "share_photo", "share": "yes"}).text
    )
    # Another participant with no photo of their own: nothing to share.
    signin(client, group["user"])
    assert _share(client, base) == 404


def test_personal_passport_has_no_group_sharing(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    base = f"/passports/{owner['passport']}/airfields/{owner['target']}"
    client.post(
        base,
        data={"action": "photo", "photo": (io.BytesIO(image_bytes()), "p.jpg")},
    )
    page = client.get(base).text
    assert "Private stamp photo" in page and "Share with this group" not in page
    assert _share(client, base) == 404
    assert "Photos shared by friends" not in page
    signin(client, owner["other"])
    with app.app_context():
        photo_id = PassportStampPhoto.query.one().id
    assert (
        client.get(f"/passports/{owner['passport']}/photos/{photo_id}").status_code
        == 404
    )
