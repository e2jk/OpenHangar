"""Default edition maps expose shared stamps, never another member's evidence."""

import json
from datetime import date
from html.parser import HTMLParser
from typing import Any
from unittest.mock import patch

from flask import Flask
from flask.testing import FlaskClient
from models import (
    Passport,
    PassportAirfield,
    PassportParticipant,
    PassportSeries,
    PassportVisit,
    db,
)
from passports import editions, groups, memberships
from passports.catalogue import create_custom
from passports.management import BuilderError

from tests.test_passport_builder import signin
from tests.test_passport_editions import extra
from tests.test_passport_groups import consent_values, group
from tests.test_passport_invitations import invited
from tests.test_passport_management import owner
from tests.test_passport_memberships import members

__all__ = ["group", "invited", "members", "owner"]


class MapData(HTMLParser):
    fields: list[dict[str, Any]]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if data.get("id") == "passport-progress":
            self.fields = json.loads(data["data-fields"])


def map_fields(response: Any) -> list[dict[str, Any]]:
    parser = MapData()
    parser.feed(response.get_data(as_text=True))
    return parser.fields


def test_default_group_detail_has_three_states_and_private_friend_list(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, members["passport"])
        own_field = create_custom(p.tenant_id, "Both visited", code="BOTH")
        empty = create_custom(p.tenant_id, "Nobody visited", code="NONE")
        targets = [
            PassportAirfield(tenant_id=p.tenant_id, passport_id=p.id, airfield_id=f.id)
            for f in (own_field, empty)
        ]
        db.session.add_all(targets)
        db.session.flush()
        for uid in (members["other"], members["basic"]):
            part = PassportParticipant.query.filter_by(
                passport_id=p.id, user_id=uid
            ).one()
            db.session.add(
                PassportVisit(
                    passport_id=p.id,
                    participant_id=part.id,
                    target_id=targets[0].id,
                    author_user_id=uid,
                    visit_date=date(2026, 1, 2),
                    note="PRIVATE FRIEND DETAIL",
                )
            )
        db.session.commit()
        own_target = targets[0].id
    signin(client, members["basic"])
    response = client.get(f"/passports/{members['passport']}/?frequency=1")
    assert response.status_code == 200
    fields = {row["name"]: row for row in map_fields(response)}
    assert (
        fields["First field"]["state"] == "others"
        and not fields["First field"]["visited"]
    )
    # Frequency counts the viewer's own visits by default; the group on request.
    assert (
        fields["Both visited"]["state"] == "own"
        and fields["Both visited"]["count"] == 1
    )
    group_fields = {
        row["name"]: row
        for row in map_fields(
            client.get(f"/passports/{members['passport']}/?frequency=1&count=group")
        )
    }
    assert group_fields["Both visited"]["count"] == 2
    assert fields["Nobody visited"]["state"] == "unvisited"
    serialized = json.dumps(fields)
    for private in (
        "PRIVATE",
        "flight_id",
        "user_id",
        "participant_id",
        "aircraft",
        "events",
    ):
        assert private not in serialized
    assert fields["First field"]["friends"][0]["name"] == "Pilot friend"
    target = client.get(
        f"/passports/{members['passport']}/airfields/{members['target']}"
    )
    assert (
        b"Friends who have been here" in target.data and b"Pilot friend" in target.data
    )
    assert (
        b"PRIVATE OWN NOTE" not in target.data and b"PRIVATE VOUCHER" not in target.data
    )
    # Friend information is available even on a target the viewer already stamped.
    assert (
        b"Pilot friend"
        in client.get(f"/passports/{members['passport']}/airfields/{own_target}").data
    )
    with app.app_context():
        memberships.remove(
            members["tenant"], members["user"], members["series"], members["other"]
        )
        db.session.commit()
    fields = {
        row["name"]: row
        for row in map_fields(client.get(f"/passports/{members['passport']}/"))
    }
    assert (
        fields["First field"]["state"] == "unvisited"
        and not fields["First field"]["friends"]
    )
    assert (
        fields["Both visited"]["state"] == "own"
        and fields["Both visited"]["count"] == 1
    )


def test_same_friend_across_editions_is_listed_once_with_all_dates(
    app: Flask, members: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, members["passport"])
        p.start_date, p.end_date = date(2026, 1, 1), date(2026, 1, 31)
        second = extra(members, "Next edition", date(2026, 2, 1), date(2026, 2, 28))
        editions.link(second, members["user"], series_id=p.series_id)
        groups.enable(second, members["user"], consent_values("Owner"))
        db.session.add(
            PassportParticipant(
                passport_id=second.id,
                user_id=members["other"],
                accepted_at=date.today(),
                display_name="Temporary",
            )
        )
        db.session.flush()
        groups.consent(second, members["other"], consent_values("Friend renamed"))
        part = PassportParticipant.query.filter_by(
            passport_id=second.id, user_id=members["other"]
        ).one()
        target = PassportAirfield.query.filter_by(passport_id=second.id).one()
        for day in (date(2026, 2, 1), date(2026, 2, 2)):
            db.session.add(
                PassportVisit(
                    passport_id=second.id,
                    participant_id=part.id,
                    target_id=target.id,
                    author_user_id=members["other"],
                    visit_date=day,
                )
            )
        db.session.commit()
        series = db.session.get(PassportSeries, p.series_id)
        result = groups.combined(series, [p, second], members["basic"])
        friend = result["rows"][0]["friends"]
        assert len(friend) == 1 and friend[0]["name"] == "Friend renamed"
        assert friend[0]["editions"] == [
            {"label": p.title, "dates": ["2026-01-01"]},
            {"label": "Next edition", "dates": ["2026-02-01", "2026-02-02"]},
        ]
        assert "user_id" not in json.dumps(friend)


def test_group_view_revalidates_missing_or_revoked_series(
    app: Flask, client: FlaskClient, members: dict[str, int]
) -> None:
    signin(client, members["basic"])
    with app.app_context():
        get = db.session.get

        def missing(model: Any, identity: Any, **kwargs: Any) -> Any:
            return None if model is PassportSeries else get(model, identity, **kwargs)

        with patch.object(db.session, "get", side_effect=missing):
            assert client.get(f"/passports/{members['passport']}/").status_code == 404
    with patch("passports.groups.combined", side_effect=BuilderError("revoked", 404)):
        assert client.get(f"/passports/{members['passport']}/").status_code == 404
