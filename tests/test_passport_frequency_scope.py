"""Shared-map visit frequency: your own visits by default, the whole group or
one participant only when chosen, never an implicit mix (41c/41g/41f)."""

from datetime import date
from typing import Any

from flask import Flask
from flask.testing import FlaskClient
from models import Passport, PassportParticipant, PassportSeries, db
from passports import groups

from tests.test_passport_builder import signin
from tests.test_passport_eligibility import flight
from tests.test_passport_groups import group
from tests.test_passport_management import owner

__all__ = ["group", "owner"]


def _repeat_friend_visits(group: dict[str, int]) -> None:
    # Two more 2025 arrivals for the friend: three in the 2025 edition.
    flight(group["other"], date=date(2025, 7, 1))
    flight(group["other"], date=date(2025, 8, 1))
    db.session.flush()


def _row(view: dict[str, Any], target: int) -> dict[str, Any]:
    return next(row for row in view["rows"] if row["id"] == target)


def _view(group: dict[str, int], viewer: int, *, every: bool = False, **kw: Any):
    series = db.session.get(PassportSeries, group["series"])
    new = db.session.get(Passport, group["new"])
    if every:
        passports, _accepted = groups.visible_editions(series, viewer)
        return groups.combined(series, passports, viewer, **kw)
    return groups.combined(series, [new], viewer, current=new, **kw)


def _airfield(group: dict[str, int]) -> int:
    from models import PassportAirfield

    return PassportAirfield.query.filter_by(passport_id=group["new"]).one().airfield_id


def test_parse_count_scope() -> None:
    assert groups.parse_count_scope(None) == "you"
    assert groups.parse_count_scope("") == "you"
    assert groups.parse_count_scope("group") == "group"
    assert groups.parse_count_scope("you") == "you"
    assert groups.parse_count_scope("2") == 2
    assert groups.parse_count_scope("x") == "you"
    assert groups.parse_count_scope("-1") == "you"


def test_single_edition_counts_follow_the_chosen_scope(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        _repeat_friend_visits(group)
        field = _airfield(group)
        # Default: the viewer's own visits only (none in 2025), not the friend's.
        mine = _view(group, group["user"])
        row = _row(mine, field)
        assert mine["count_scope"] == "you" and mine["count_scope_name"] == ""
        assert row["count"] == row["own_count"] == 0
        assert [e["count"] for e in row["editions"]] == [0]
        # The friend still makes the marker "visited by friends".
        assert row["state"] == "others"
        whole = _row(_view(group, group["user"], scope="group"), field)
        assert whole["count"] == 3 and [e["count"] for e in whole["editions"]] == [3]
        assert whole["own_count"] == 0
        scopes = mine["count_scopes"]
        assert [s["key"] for s in scopes[:2]] == ["you", "group"]
        # Other participants only: the viewer is the "you" option.
        assert [s["name"] for s in scopes[2:]] == ["Later friend"]
        friend_key = scopes[2]["key"]
        one = _view(group, group["user"], scope=friend_key)
        assert one["count_scope_name"] == "Later friend"
        assert _row(one, field)["count"] == 3


def test_viewer_key_unknown_key_and_non_sharing_viewer(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        _repeat_friend_visits(group)
        field = _airfield(group)
        series = db.session.get(PassportSeries, group["series"])
        new = db.session.get(Passport, group["new"])
        _scopes, by_key = groups.count_scopes(
            [new], groups.shared_progress(series, [new], group["other"])["roster"], 0
        )
        own_key = next(k for k, uid in by_key.items() if uid == group["other"])
        # Choosing yourself by key is simply "you"; an unknown key falls back.
        assert _view(group, group["other"], scope=own_key)["count_scope"] == "you"
        assert _view(group, group["other"], scope=99)["count_scope"] == "you"
        assert _row(_view(group, group["other"]), field)["count"] == 3
        # A viewer who stops sharing visits is left out of the group total.
        PassportParticipant.query.filter_by(
            passport_id=new.id, user_id=group["other"]
        ).one().share_visits = False
        db.session.flush()
        assert _row(_view(group, group["other"], scope="group"), field)["count"] == 0


def test_combined_editions_total_and_per_edition_counts_per_scope(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        _repeat_friend_visits(group)
        field = _airfield(group)
        # The owner's linked 2024 flight and manual visit count once.
        mine = _row(_view(group, group["user"], every=True), field)
        assert mine["count"] == 1
        assert {e["label"]: e["count"] for e in mine["editions"]} == {
            "2024": 1,
            "2025": 0,
        }
        whole = _row(_view(group, group["user"], every=True, scope="group"), field)
        assert whole["count"] == 4
        assert {e["label"]: e["count"] for e in whole["editions"]} == {
            "2024": 1,
            "2025": 3,
        }
        # The newest/oldest marker priority never changes the counts.
        oldest = _row(
            _view(group, group["user"], every=True, scope="group", oldest=True), field
        )
        assert oldest["count"] == 4


def test_pages_label_the_scope_and_keep_it_in_downloads(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    with app.app_context():
        _repeat_friend_visits(group)
        db.session.commit()
    signin(client, group["user"])
    path = f"/passports/{group['new']}/"
    default = client.get(path, query_string={"frequency": "1"}).text
    assert 'name="count"' in default and "Count visits by" in default
    assert "Visit counts show your own visits." in default
    assert "Your recorded visits" in default
    whole = client.get(path, query_string={"frequency": "1", "count": "group"}).text
    assert "Visit counts show the whole group" in whole
    assert "Recorded visits by the whole group" in whole
    assert '<option value="group" selected>' in whole
    # The GPX/KML links keep the chosen scope with the other view settings.
    assert "count=group" in whole
    series = client.get(
        f"/passports/series/{group['series']}/",
        query_string={"edition": "all", "count": "group"},
    ).text
    assert "Recorded visits by the whole group" in series
    assert 'name="count"' in series


def test_personal_passports_have_no_scope_selector(
    client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    page = client.get(
        f"/passports/{owner['passport']}/", query_string={"frequency": "1"}
    ).text
    assert 'name="count"' not in page and "Count visits by" not in page


def test_participant_scope_page_names_the_participant(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    with app.app_context():
        key = _view(group, group["user"])["count_scopes"][2]["key"]
    signin(client, group["user"])
    page = client.get(
        f"/passports/{group['new']}/", query_string={"frequency": "1", "count": key}
    ).text
    assert "Visit counts show the visits of Later friend." in page
    assert "Recorded visits by Later friend" in page


def test_non_sharing_participant_still_sees_own_visits(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    with app.app_context():
        PassportParticipant.query.filter_by(
            passport_id=group["new"], user_id=group["other"]
        ).one().share_visits = False
        db.session.commit()
    signin(client, group["other"])
    page = client.get(f"/passports/{group['new']}/").text
    # Their own 2025 arrival still stamps their own (now history) edition...
    assert "Your progress: <strong>1 / 1</strong>" in page
    # ...while the group no longer counts them: no group figures include them.
    with app.app_context():
        view = _view(group, group["other"], scope="group")
        assert _row(view, _airfield(group))["count"] == 0
