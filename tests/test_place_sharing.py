"""Exact-email invitation privacy and collection rights on authenticated pages."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    PlaceCollection,
    PlaceCollectionInvitation,
    PlaceCollectionMember,
    PlaceVisit,
    User,
    db,
)
from places.access import PlaceError, collection_access
from places.collections import (
    create_collection,
    personal_collection,
    set_member,
    transfer_collection,
)
from places.sharing import accept, invite_email, pending_for_user, revoke
from werkzeug.exceptions import Forbidden

from tests.test_place_collections import destination, people

__all__ = ["people"]


def signin(client: FlaskClient, p: dict[str, int], name: str = "creator") -> None:
    with client.session_transaction() as sess:
        sess.clear()
        sess["user_id"] = p[name]
        sess["tenant_id"] = p["tenant"]


def test_email_invitations_do_not_add_or_expose_existing_users_before_acceptance(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        t, u, v = p["tenant"], p["personal"], p["reader"]
        c = create_collection(t, u, "Friends")
        row = invite_email(t, u, c.id, " READER@PLACES.TEST ", can_edit=False)
        db.session.flush()
        iid = row.id
        assert row.email == "reader@places.test"
        assert db.session.get(PlaceCollectionMember, (c.id, v)) is None
        with pytest.raises(PlaceError, match="exact email"):
            set_member(t, u, c.id, p["outsider"])
        unknown = invite_email(t, u, c.id, "unknown@places.test")
        db.session.flush()
        assert unknown.id != iid
        assert pending_for_user(t, v).one().id == iid
        assert pending_for_user(t, p["outsider"]).count() == 0
        with pytest.raises(PlaceError):
            accept(t, p["outsider"], iid)
        assert accept(t, v, iid) == c.id
        db.session.flush()
        assert not db.session.get(PlaceCollectionMember, (c.id, v)).can_edit
        assert pending_for_user(t, v).count() == 0
        # A personal manager may update an accepted member already in their list.
        set_member(t, u, c.id, v, can_edit=True)
        db.session.flush()
        assert db.session.get(PlaceCollectionMember, (c.id, v)).can_edit
        again = invite_email(t, u, c.id, "reader@places.test", can_edit=False)
        db.session.flush()
        same = invite_email(t, u, c.id, "READER@PLACES.TEST", can_edit=True)
        assert same.id == again.id
        member = db.session.get(PlaceCollectionMember, (c.id, v))
        member.can_edit = False
        assert accept(t, v, again.id) == c.id
        db.session.flush()
        assert not member.can_edit  # stale invitations cannot restore wider rights
        expired = invite_email(t, u, c.id, "reader@places.test")
        db.session.flush()
        expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.session.flush()
        with pytest.raises(PlaceError):
            accept(t, v, expired.id)
        unknown.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.session.flush()
        with pytest.raises(PlaceError):
            accept(t, v, unknown.id)
        revoke(t, u, c.id, unknown.id)
        db.session.flush()
        with pytest.raises(PlaceError):
            revoke(t, u, c.id, unknown.id)


@pytest.mark.parametrize(
    "email",
    [
        "",
        "not-an-email",
        "user@localhost",
        "a b@places.test",
        "ü@places.test",
        "x" * 256 + "@places.test",
        "user\n@places.test",
    ],
)
def test_email_invitation_validation(
    app: Flask, people: dict[str, int], email: str
) -> None:
    with app.app_context():
        c = create_collection(people["tenant"], people["creator"], "Email")
        with pytest.raises(PlaceError):
            invite_email(people["tenant"], people["creator"], c.id, email)


def test_collection_transfer_keeps_the_recipients_own_personal_collection(
    app: Flask, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        t, u, v = p["tenant"], p["creator"], p["reader"]
        c = personal_collection(t, u)
        mine = personal_collection(t, v)
        with pytest.raises(PlaceError):
            transfer_collection(t, u, c.id, v)
        set_member(t, u, c.id, v, can_edit=False)
        db.session.flush()
        transfer_collection(t, u, c.id, v)
        db.session.flush()
        assert c.created_by_user_id == v and not c.is_personal
        assert collection_access(t, v, c.id, managing=True).id == c.id
        assert personal_collection(t, v).id == mine.id


def test_collection_pages_are_private_tenant_scoped_and_available_without_pilot_role(
    app: Flask, client: FlaskClient, people: dict[str, int]
) -> None:
    p = people
    signin(client, p)
    response = client.get("/places/")
    assert response.status_code == 200 and "My places" in response.text
    assert (
        "no-store" in response.headers["Cache-Control"]
        and "Cookie" in response.headers["Vary"]
    )
    assert client.get("/places/collections/new").status_code == 200
    bad = client.post("/places/collections/new", data={"name": " "})
    assert bad.status_code == 400
    response = client.post("/places/collections/new", data={"name": "Friends"})
    assert response.status_code == 302
    settings = response.headers["Location"]
    with app.app_context():
        c = PlaceCollection.query.filter_by(name="Friends").one()
        cid = c.id
        destination(cid, p["creator"], address="Secret museum address")
        db.session.commit()
    page = client.get(settings)
    assert page.status_code == 200 and 'name="member"' in page.text
    assert client.get(f"/places/?collection={cid}").status_code == 200
    assert (
        client.post(settings, data={"action": "rename", "name": "Renamed"}).status_code
        == 302
    )
    assert client.post(settings, data={"action": "default"}).status_code == 302
    assert (
        client.post(
            settings,
            data={"action": "member", "member": str(p["reader"]), "rights": "read"},
        ).status_code
        == 302
    )
    assert (
        client.post(
            settings, data={"action": "remove", "member": p["reader"]}
        ).status_code
        == 302
    )
    assert (
        client.post(
            settings, data={"action": "member", "member": p["reader"], "rights": "read"}
        ).status_code
        == 302
    )
    signin(client, p, "reader")
    assert client.get(settings).status_code == 200
    assert "Secret museum address" in client.get(f"/places/?collection={cid}").text
    denied = client.post(settings, data={"action": "rename", "name": "Stolen"})
    assert denied.status_code == 400
    assert "Secret museum address" not in client.get("/places/?collection=99999").text
    assert client.get("/places/?collection=99999").status_code == 404
    assert client.get("/places/collections/99999/settings").status_code == 404
    assert client.post(settings, data={"action": "leave"}).status_code == 302
    assert client.get(settings).status_code == 404
    signin(client, p, "outsider")
    assert client.get(f"/places/?collection={cid}").status_code == 404
    signin(client, p, "admin")
    assert client.get(settings).status_code == 200  # management only, not destinations
    assert client.get(f"/places/?collection={cid}").status_code == 404
    with app.app_context():
        db.session.get(User, p["admin"]).view_only = True
        db.session.commit()
    assert client.get("/places/").status_code == 200
    assert client.get("/places/collections/new").status_code == 403


def test_personal_pages_blind_email_invitation_acceptance_and_no_fleet_access(
    app: Flask, client: FlaskClient, people: dict[str, int]
) -> None:
    p = people
    signin(client, p, "personal")
    response = client.post("/places/collections/new", data={"name": "Personal friends"})
    assert response.status_code == 302
    settings = response.headers["Location"]
    page = client.get(settings)
    assert 'id="collection-email"' in page.text
    assert 'id="collection-user"' not in page.text
    assert "outsider@places.test" not in page.text
    for email in ("reader@places.test", "unknown@places.test"):
        response = client.post(
            settings, data={"action": "invite", "email": email, "rights": "read"}
        )
        assert response.status_code == 302 and response.headers["Location"] == settings
    assert client.get("/aircraft/").status_code == 403
    assert (
        client.post(
            settings, data={"action": "member", "member": p["outsider"]}
        ).status_code
        == 400
    )
    with app.app_context():
        c = PlaceCollection.query.filter_by(name="Personal friends").one()
        cid = c.id
        unknown = (
            PlaceCollectionInvitation.query.filter_by(email="unknown@places.test")
            .one()
            .id
        )
    assert (
        client.post(
            settings, data={"action": "revoke", "invitation": unknown}
        ).status_code
        == 302
    )
    signin(client, p, "outsider")
    assert client.get(f"/places/collections/{cid}/join").status_code == 404
    signin(client, p, "reader")
    assert "Personal friends" in client.get("/places/").text
    invitation_page = client.get(f"/places/collections/{cid}/join")
    assert (
        invitation_page.status_code == 200
        and "Can view and record own visits" in invitation_page.text
    )
    assert client.post(f"/places/collections/{cid}/join").status_code == 302
    assert client.get(f"/places/collections/{cid}/join").status_code == 302
    signin(client, p, "personal")
    assert (
        client.post(
            settings, data={"action": "member", "member": p["reader"], "rights": "edit"}
        ).status_code
        == 302
    )
    assert (
        client.post(
            settings, data={"action": "transfer", "member": p["reader"]}
        ).status_code
        == 302
    )
    assert client.post(settings, data={"action": "leave"}).status_code == 302


@pytest.mark.parametrize("action", ["member", "remove", "transfer", "revoke"])
def test_missing_or_huge_action_ids_are_refused(
    app: Flask, client: FlaskClient, people: dict[str, int], action: str
) -> None:
    p = people
    signin(client, p)
    response = client.post("/places/collections/new", data={"name": "IDs"})
    settings = response.headers["Location"]
    assert client.post(settings, data={"action": action}).status_code == 400
    assert (
        client.post(
            settings,
            data={"action": action, "member": "9" * 1000, "invitation": "9" * 1000},
        ).status_code
        == 400
    )
    assert client.post(settings, data={"action": "unknown"}).status_code == 400


def test_collection_delete_requires_current_counts_and_confirmation(
    app: Flask, client: FlaskClient, people: dict[str, int]
) -> None:
    from datetime import date

    p = people
    with app.app_context():
        c = create_collection(p["tenant"], p["creator"], "Delete with counts")
        place = destination(c.id, p["creator"])
        db.session.add(
            PlaceVisit(place_id=place.id, user_id=p["creator"], visit_date=date.today())
        )
        db.session.commit()
        cid = c.id
    signin(client, p)
    settings = f"/places/collections/{cid}/settings"
    assert client.post(settings, data={"action": "delete"}).status_code == 400
    assert (
        client.post(
            settings,
            data={"action": "delete", "confirm": "yes", "places": "0", "visits": "0"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            settings,
            data={"action": "delete", "confirm": "yes", "places": "1", "visits": "1"},
        ).status_code
        == 302
    )
    with app.app_context():
        assert db.session.get(PlaceCollection, cid) is None


def test_missing_membership_context_fails_closed(app: Flask) -> None:
    from places.routes import context

    with (
        app.test_request_context(),
        patch("places.routes.current_membership", return_value=None),
        pytest.raises(Forbidden),
    ):
        context()


def test_invitation_expiring_during_acceptance_is_refused(
    app: Flask, client: FlaskClient, people: dict[str, int]
) -> None:
    p = people
    with app.app_context():
        c = create_collection(p["tenant"], p["creator"], "Race")
        invite_email(p["tenant"], p["creator"], c.id, "reader@places.test")
        db.session.commit()
        cid = c.id
    signin(client, p, "reader")
    with patch("places.routes.sharing.accept", side_effect=PlaceError("expired")):
        assert client.post(f"/places/collections/{cid}/join").status_code == 404
