"""Passport memberships carry no tenant role and fail closed on unrelated routes."""

import re
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from flask import Flask, request, session
from flask.testing import FlaskClient
from models import NotificationType as NT
from models import Role, TenantUser, User, db
from services.account_scope import enforce
from services.notification_service import _find_recipients
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import Forbidden
from werkzeug.routing import Rule

from tests.test_passport_builder import signin
from tests.test_passport_groups import consent_values, group
from tests.test_passport_management import owner

__all__ = ["group", "owner"]


@pytest.fixture
def scoped(app: Flask, owner: dict[str, int]) -> Iterator[dict[str, int]]:
    with app.app_context():
        membership = db.session.get(TenantUser, (owner["user"], owner["tenant"]))
        membership.access_scope = "personal"
        membership.role = None
        user = db.session.get(User, owner["user"])
        # Global account flags cannot bypass a restricted tenant membership.
        user.is_pilot = user.is_maintenance = user.is_instance_admin = True
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(TenantUser, (owner["user"], owner["tenant"])).role is None
    yield owner


@pytest.mark.parametrize(
    "path",
    [
        "/aircraft/",
        "/aircraft/1",
        "/maintenance/",
        "/pilot/",
        "/flights/new",
        "/config/",
        "/users/",
        "/config/users/",
        "/config/users/1/role",
        "/reservations/",
        "/documents/",
        "/expenses/",
        "/airport-search?q=TEST",
        "/aircraft-type-search?q=C172",
    ],
)
@pytest.mark.parametrize("method", ["get", "post"])
def test_unrelated_routes_are_denied_before_any_role_override(
    app: Flask, client: FlaskClient, scoped: dict[str, int], path: str, method: str
) -> None:
    signin(client, scoped["user"])
    response = getattr(client, method)(path)
    assert response.status_code == 403
    assert b"Content-Security-Policy" not in response.data
    assert re.search(
        r"nonce-[A-Za-z0-9_-]{20,}", response.headers["Content-Security-Policy"]
    )


def test_future_routes_default_to_denied(app: Flask, scoped: dict[str, int]) -> None:
    for method in ("GET", "POST"):
        with app.test_request_context("/scope-future-probe", method=method):
            session["user_id"] = scoped["user"]
            request.url_rule = Rule(
                "/scope-future-probe", endpoint="future_instance_feature"
            )
            with pytest.raises(Forbidden):
                enforce()


def test_home_navigation_profile_and_own_stamps(
    app: Flask, client: FlaskClient, scoped: dict[str, int]
) -> None:
    signin(client, scoped["user"])
    assert client.get("/").location.endswith("/passports/")
    index = client.get("/passports/")
    assert index.status_code == 200
    assert b'href="/aircraft/"' not in index.data
    assert b'href="/pilot/"' not in index.data
    assert b'href="/config/"' not in index.data
    assert b"Airfield passports" in index.data
    assert client.get("/profile").status_code == 200
    assert client.get("/static/css/base.css").status_code == 200
    detail = client.get(f"/passports/{scoped['passport']}/")
    assert detail.status_code == 200 and b"Create next edition" in detail.data
    assert b"Where next?" in detail.data
    assert (
        client.get(
            f"/passports/{scoped['passport']}/where-next", follow_redirects=True
        ).status_code
        == 200
    )
    # The real target route remains participant-scoped; find its canonical URL.
    with app.test_request_context():
        from flask import url_for

        target = url_for(
            "passports.target_detail",
            passport_id=scoped["passport"],
            target_id=scoped["target"],
        )
    assert client.get(target).status_code == 200
    result = client.post(
        target,
        data={
            "action": "visit",
            "visit_date": "2025-01-01",
            "note": "Own private note",
        },
    )
    assert result.status_code == 302
    assert b"Own private note" in client.get(target).data
    assert client.get("/passports/999999/").status_code == 404


def test_only_passport_notifications_remain_visible_and_deliverable(
    app: Flask, client: FlaskClient, scoped: dict[str, int]
) -> None:
    signin(client, scoped["user"])
    page = client.get("/config/notifications/")
    assert page.status_code == 200
    assert b"enabled_passport_expiring" in page.data
    assert b"enabled_maintenance_due_soon" not in page.data
    assert b"enabled_crew_invite" not in page.data
    with app.app_context():
        assert (
            _find_recipients(NT.CREW_INVITE, scoped["tenant"], [scoped["user"]]) == []
        )
        assert (
            _find_recipients(
                NT.GROUNDING_SNAG_OPENED, scoped["tenant"], [scoped["user"]]
            )
            == []
        )
        assert [
            u.id
            for u in _find_recipients(
                NT.PASSPORT_EXPIRING, scoped["tenant"], [scoped["user"]]
            )
        ] == [scoped["user"]]


def test_scope_and_null_role_are_constrained(app: Flask, owner: dict[str, int]) -> None:
    with app.app_context():
        membership = db.session.get(TenantUser, (owner["user"], owner["tenant"]))
        for scope, role in [
            ("passport", Role.VIEWER),
            ("full", None),
            ("unknown", Role.PILOT),
        ]:
            membership.access_scope, membership.role = scope, role
            with pytest.raises(IntegrityError):
                db.session.commit()
            db.session.rollback()
        membership.access_scope, membership.role = "personal", None
        db.session.commit()
        assert membership.role is None


def test_scope_change_revokes_existing_login(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    assert client.get("/passports/").status_code == 200
    with app.app_context():
        user = db.session.get(User, owner["user"])
        old = user.auth_version
        membership = db.session.get(TenantUser, (owner["user"], owner["tenant"]))
        membership.access_scope, membership.role = "personal", None
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(User, owner["user"]).auth_version != old
    response = client.get("/passports/")
    assert response.status_code == 302 and "/login" in response.location


def test_operator_sees_and_deliberately_promotes_a_restricted_member(
    app: Flask, client: FlaskClient, scoped: dict[str, int]
) -> None:
    with app.app_context():
        admin = db.session.get(TenantUser, (scoped["other"], scoped["tenant"]))
        admin.role = Role.OWNER
        # The fixture makes the member an instance admin, whom only the
        # instance admin may manage: a plain member is what is promoted here.
        db.session.get(User, scoped["user"]).is_instance_admin = False
        db.session.commit()
    signin(client, scoped["other"])
    listing = client.get("/config/users/")
    assert listing.status_code == 200 and b"Personal account" in listing.data
    response = client.post(
        f"/config/users/{scoped['user']}/role", data={"role": "pilot"}
    )
    assert response.status_code == 302
    with app.app_context():
        member = db.session.get(TenantUser, (scoped["user"], scoped["tenant"]))
        assert member.role == Role.PILOT and member.access_scope == "full"


def test_banner_poll_uses_public_history_only(
    app: Flask, client: FlaskClient, scoped: dict[str, int]
) -> None:
    signin(client, scoped["user"])
    public = {
        "aviation_day_banner": "Public history",
        "pilot_anniversary": None,
        "pilot_anniversary_confetti": False,
    }
    with patch("init._compute_banners", return_value=public) as banners:
        response = client.get("/api/banners")
        assert response.status_code == 200 and response.json == public
        banners.assert_called_once_with(None)


def test_real_foreign_passport_ids_stay_private(
    app: Flask, client: FlaskClient, scoped: dict[str, int]
) -> None:
    from datetime import UTC, datetime

    from models import Passport, PassportAirfield, PassportParticipant

    with app.app_context():
        other = Passport(
            tenant_id=scoped["tenant"],
            owner_user_id=scoped["other"],
            title="OTHER PRIVATE PASSPORT",
        )
        db.session.add(other)
        db.session.flush()
        target = PassportAirfield(
            tenant_id=scoped["tenant"],
            passport_id=other.id,
            airfield_id=scoped["field"],
        )
        db.session.add_all(
            [
                target,
                PassportParticipant(
                    passport_id=other.id,
                    user_id=scoped["other"],
                    display_name="Other",
                    accepted_at=datetime.now(UTC),
                ),
            ]
        )
        db.session.commit()
        pid, target_id = other.id, target.id
    signin(client, scoped["user"])
    with app.test_request_context():
        from flask import url_for

        paths = [
            url_for("passports.detail", passport_id=pid),
            url_for("passports.target_detail", passport_id=pid, target_id=target_id),
            url_for("passports.voucher_ledger", passport_id=pid),
            url_for("passports.link_series", passport_id=pid),
        ]
    for path in paths:
        response = client.get(path)
        assert (
            response.status_code == 404
            and b"OTHER PRIVATE PASSPORT" not in response.data
        )


def test_scoped_friend_keeps_only_authorised_group_history(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    with app.app_context():
        membership = db.session.get(TenantUser, (group["other"], group["tenant"]))
        membership.access_scope, membership.role = "personal", None
        db.session.commit()
    signin(client, group["other"])
    url = f"/passports/series/{group['series']}/"
    ordinary = client.get(url)
    assert (
        ordinary.status_code == 200
        and f'<option value="{group["old"]}"'.encode() not in ordinary.data
    )
    history = client.get(url + "?history=1&edition=all")
    assert history.status_code == 200 and b"Original pilot" in history.data
    assert (
        b"PRIVATE FLIGHT" not in history.data and b"PRIVATE MANUAL" not in history.data
    )
    assert client.get(f"/passports/{group['old']}/").status_code == 404
    assert client.get(f"/passports/{group['new']}/").status_code == 200
    assert (
        client.post(
            f"/passports/{group['new']}/group",
            data=consent_values("Scoped friend", history=False),
        ).status_code
        == 302
    )
