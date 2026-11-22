"""The shared forum never imports private notes or bypasses group membership."""

import io
import json
import re
import zipfile
from datetime import UTC, datetime
from html import unescape
from unittest.mock import patch

from flask import Flask
from flask.testing import FlaskClient
from models import (
    PassportAirfieldComment,
    PassportSeriesMember,
    Role,
    TenantUser,
    User,
    db,
)
from passports import comments
from passports.management import BuilderError

from tests.test_passport_builder import signin
from tests.test_passport_groups import group
from tests.test_passport_management import owner

__all__ = ["group", "owner"]


def path(s: dict[str, int]) -> str:
    return f"/passports/series/{s['series']}/airfields/{s['field']}/discussion"


def test_roleless_member_posts_edits_and_exports_only_their_own_comments(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        membership = db.session.get(TenantUser, (s["other"], s["tenant"]))
        membership.role, membership.access_scope = None, "personal"
        comments.add(
            s["tenant"],
            s["user"],
            s["series"],
            s["field"],
            {"body": "Owner group comment"},
        )
        db.session.commit()
    signin(client, s["other"])
    response = client.get(path(s))
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert b"Visible to members of this group" in response.data
    assert (
        b"PRIVATE MANUAL" not in response.data
        and b"PRIVATE FLIGHT" not in response.data
    )
    assert 'id="comment-body" maxlength="8000" required></textarea>' in response.text
    body = "<script>literal group advice</script>\nA question"
    response = client.post(
        path(s),
        data={
            "action": "add",
            "body": body,
            "category": "question",
            "experience_date": "2025-06-01",
            "author_user_id": s["user"],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "<script>literal group advice</script>" not in response.text
    assert "&lt;script&gt;literal group advice&lt;/script&gt;" in response.text
    with app.app_context():
        row = PassportAirfieldComment.query.filter_by(author_user_id=s["other"]).one()
        row_id = row.id
        created = row.created_at
        assert row.author_display_name == "Later friend"
    assert (
        client.post(
            path(s),
            data={
                "action": "edit",
                "comment_id": row_id,
                "body": "Edited own comment",
                "category": "nearby",
            },
        ).status_code
        == 302
    )
    with app.app_context():
        row = db.session.get(PassportAirfieldComment, row_id)
        assert row.edited_at is not None and row.created_at == created
        db.session.get(
            PassportSeriesMember, (s["series"], s["other"])
        ).is_active = False
        db.session.commit()
    assert client.get(path(s)).status_code == 404
    response = client.get("/passports/export")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        exported = json.loads(archive.read("passport-data.json"))
    response.close()
    assert [c["body"] for c in exported["comments"]] == ["Edited own comment"]
    assert "Owner group comment" not in json.dumps(exported)


def test_owner_can_delete_but_cannot_edit_another_author(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        row = comments.add(
            s["tenant"], s["other"], s["series"], s["field"], {"body": "Friend's post"}
        )
        db.session.commit()
        ident = row.id
    signin(client, s["user"])
    html = client.get(path(s)).text
    assert "Delete comment" in html and "Edit comment" not in html
    assert (
        client.post(
            path(s),
            data={
                "action": "edit",
                "comment_id": ident,
                "body": "Not my words",
            },
        ).status_code
        == 403
    )
    assert (
        client.post(path(s), data={"action": "delete", "comment_id": ident}).status_code
        == 302
    )
    assert (
        client.post(path(s), data={"action": "delete", "comment_id": ident}).status_code
        == 404
    )
    assert (
        client.post(
            path(s), data={"action": "delete", "comment_id": "9" * 100}
        ).status_code
        == 404
    )
    assert client.post(path(s), data={"action": "unknown"}).status_code == 400


def test_filters_pagination_and_posting_date_are_independent_of_experience_date(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        for i in range(25):
            row = comments.add(
                s["tenant"],
                s["user"],
                s["series"],
                s["field"],
                {
                    "body": f"Topic {i}",
                    "category": "nearby" if i == 0 else "airfield",
                    "experience_date": "2024-01-01",
                },
            )
            row.created_at = datetime(2025, 6, 1, tzinfo=UTC)
        db.session.commit()
    signin(client, s["other"])
    first = client.get(path(s))
    assert first.text.count("<article ") == 20
    second = client.get(path(s), query_string={"page": "2"})
    assert second.text.count("<article ") == 5
    filtered = client.get(
        path(s),
        query_string={
            "category": "nearby",
            "after": "2025-01-01",
            "before": "2025-12-31",
        },
    )
    assert [
        unescape(body)
        for body in re.findall(
            r'<p class="passport-comment-body text-break">(.*?)</p>',
            filtered.text,
            re.DOTALL,
        )
    ] == ["Topic 0"]
    for args in (
        {"category": "bad"},
        {"after": "bad"},
        {"before": "bad"},
        {"after": "2026-01-01", "before": "2025-01-01"},
    ):
        assert client.get(path(s), query_string=args).status_code == 400
    assert client.get(path(s), query_string={"page": "9" * 100}).status_code == 200


def test_validation_readonly_and_membership_revocation_do_not_render_stale_content(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    s = group
    signin(client, s["other"])
    response = client.post(
        path(s),
        data={
            "action": "add",
            "body": "Keep my typed text",
            "category": "invalid",
        },
    )
    assert response.status_code == 400 and "Keep my typed text" in response.text
    with patch(
        "passports.discussion_routes.comments.add",
        side_effect=BuilderError("Retry safely", 409),
    ):
        assert (
            client.post(path(s), data={"action": "add", "body": "Retry"}).status_code
            == 409
        )
    with app.app_context():
        db.session.get(User, s["other"]).view_only = True
        db.session.commit()
    assert 'id="passport-comment-form"' not in client.get(path(s)).text
    assert (
        client.post(path(s), data={"action": "add", "body": "No write"}).status_code
        == 403
    )
    with app.app_context():
        db.session.get(User, s["other"]).view_only = False
        db.session.commit()

    def revoke(*_args, **_kwargs):
        db.session.get(
            PassportSeriesMember, (s["series"], s["other"])
        ).is_active = False
        db.session.commit()
        raise BuilderError("Changed", 409)

    with patch("passports.discussion_routes.comments.add", side_effect=revoke):
        response = client.post(path(s), data={"action": "add", "body": "Must not post"})
    assert response.status_code == 404 and b"Keep my typed text" not in response.data


def test_organisation_admin_does_not_inherit_group_access(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        user = User(
            email="outsider@discussion.test",
            password_hash="unused",
            is_instance_admin=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(tenant_id=s["tenant"], user_id=user.id, role=Role.ADMIN)
        )
        db.session.commit()
        ident = user.id
    signin(client, ident)
    assert client.get(path(s)).status_code == 404
    assert (
        client.post(path(s), data={"action": "add", "body": "Not a member"}).status_code
        == 404
    )


def test_rate_limit_keys_are_per_account_and_safe_before_login(app: Flask) -> None:
    from flask import session
    from passports.discussion_routes import _comment_actor

    with app.test_request_context():
        assert _comment_actor() == "passport-comments:anonymous"
        session["user_id"] = 21
        assert _comment_actor() == "passport-comments:21"
        session["user_id"] = 22
        assert _comment_actor() == "passport-comments:22"


def test_failed_edit_keeps_input_and_missing_membership_is_refused(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        row = comments.add(
            s["tenant"], s["other"], s["series"], s["field"], {"body": "Original"}
        )
        db.session.commit()
        ident = row.id
    signin(client, s["other"])
    response = client.post(
        path(s),
        data={
            "action": "edit",
            "comment_id": ident,
            "body": "Keep this edit",
            "category": "bad",
        },
    )
    assert response.status_code == 400
    assert "<details open>" in response.text
    assert "Keep this edit</textarea>" in response.text
    with patch("passports.discussion_routes.current_membership", return_value=None):
        assert client.get(path(s)).status_code == 403


def test_airfield_preview_shows_only_recent_group_comments_and_rechecks_access(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    s = group
    with app.app_context():
        for i in range(4):
            row = comments.add(
                s["tenant"],
                s["other"],
                s["series"],
                s["field"],
                {"body": f"Group tip {i}"},
            )
            row.created_at = datetime(2025, 1, i + 1, tzinfo=UTC)
        db.session.commit()
    signin(client, s["user"])
    target_url = f"/passports/{s['old']}/airfields/{s['target']}"
    response = client.get(target_url)
    assert response.status_code == 200
    assert "Group tip 0" not in response.text
    assert all(f"Group tip {i}" in response.text for i in (1, 2, 3))
    assert path(s) in response.text
    with patch(
        "passports.routes.comments.context", side_effect=BuilderError("Revoked", 404)
    ):
        response = client.get(target_url)
    assert response.status_code == 404 and "Group tip" not in response.text
