"""A public link to a group edition never carries the group's social layer:
no airfield discussion, no visitor-name lists and no member display names,
even when every participant publishes their visits."""

from flask import Flask
from flask.testing import FlaskClient
from models import Passport, PassportAirfield, PublicShare, db
from passports import comments

from tests.test_passport_builder import signin
from tests.test_passport_groups import group
from tests.test_passport_management import owner

__all__ = ["group", "owner"]


def test_public_group_edition_excludes_comments_and_names(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    from services.public_shares import recover

    settings = f"/passports/{group['new']}/public-sharing"
    with app.app_context():
        new = db.session.get(Passport, group["new"])
        airfield = PassportAirfield.query.filter_by(passport_id=new.id).one()
        comments.add(
            new.tenant_id,
            group["other"],
            new.series_id,
            airfield.airfield_id,
            {"category": "nearby", "body": "FRIEND ONLY TIP"},
        )
        db.session.commit()
    for uid in (group["user"], group["other"]):
        signin(client, uid)
        if uid == group["user"]:
            assert client.post(settings, data={"action": "rotate"}).status_code == 302
        assert (
            client.post(
                settings,
                data={"action": "choices", "publish_visits": "1", "frequency": "1"},
            ).status_code
            == 302
        )
    with app.app_context():
        token = str(recover(PublicShare.query.one()))
    client.get("/logout")
    base = f"/passports/public/{token}"
    data = client.get(base + "/data.json").json
    # The friend's 2025 flight is published as an aggregate visit...
    assert any(target["visited"] for target in data["targets"])
    for suffix in ("", "/data.json", "/print"):
        text = client.get(base + suffix).get_data(as_text=True)
        for private in (
            "FRIEND ONLY TIP",
            "Later friend",
            "Original pilot",
            "Friends who have been here",
            "discussion",
        ):
            assert private not in text, (suffix, private)
