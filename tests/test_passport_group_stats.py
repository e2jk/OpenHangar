"""Group statistics for a shared edition: common denominator, tied ranks,
anyone/everyone coverage and the participant filter."""

from typing import Any

from flask import Flask
from flask.testing import FlaskClient
from models import Passport, PassportParticipant, PassportSeries, db
from passports import groups

from tests.test_passport_builder import signin
from tests.test_passport_groups import group
from tests.test_passport_management import owner

__all__ = ["group", "owner"]


def rows(*specs: tuple[int, bool]) -> list[dict[str, Any]]:
    return [{"id": ident, "is_closed": closed} for ident, closed in specs]


def test_common_denominator_counts_closed_targets_only_once_stamped() -> None:
    # 1-3 open, 4 closed and stamped by Bea, 5 closed and never stamped.
    stats = groups.edition_stats(
        rows((1, False), (2, False), (3, False), (4, True), (5, True)),
        {10: "Ann", 20: "Bea", 30: "Cal"},
        {1: {10, 20, 30}, 2: {10, 20}, 4: {20}},
        viewer_id=10,
    )
    assert stats["denominator"] == 4
    assert stats["original"] == 5
    assert stats["closed_excluded"] == 1
    assert stats["closed_included"] == 1
    assert stats["anyone"] == 3
    assert stats["everyone"] == 1
    board = [
        (p["name"], p["visited"], p["rank"], p["percent"])
        for p in stats["participants"]
    ]
    # Bea's closed stamp counts for her; nobody is penalised for a closure.
    assert board == [("Bea", 3, 1, 75), ("Ann", 2, 2, 50), ("Cal", 1, 3, 25)]
    assert [p["you"] for p in stats["participants"]] == [False, True, False]


def test_tied_participants_share_a_rank_and_the_next_rank_skips() -> None:
    stats = groups.edition_stats(
        rows((1, False), (2, False)),
        {1: "Zoe", 2: "Adam", 3: "Mia"},
        {1: {1, 2}},
        viewer_id=3,
    )
    board = [(p["name"], p["rank"]) for p in stats["participants"]]
    # Ties are listed alphabetically, then the next rank skips (1, 1, 3).
    assert board == [("Adam", 1), ("Zoe", 1), ("Mia", 3)]


def test_filter_keys_are_opaque_positions_not_user_ids() -> None:
    stats = groups.edition_stats(
        rows((1, False)), {900: "Bea", 800: "Ann"}, {1: {900}}, viewer_id=800
    )
    keys = {p["name"]: p["key"] for p in stats["participants"]}
    assert keys == {"Ann": 1, "Bea": 2}
    assert stats["by_target"] == {1: {2}}


def test_empty_group_and_nothing_achievable_have_no_percentages() -> None:
    empty = groups.edition_stats(rows((1, False)), {}, {}, viewer_id=1)
    assert empty["participants"] == []
    assert empty["anyone"] is None and empty["everyone"] is None
    closed_only = groups.edition_stats(rows((1, True)), {1: "Ann"}, {}, viewer_id=1)
    assert closed_only["denominator"] == 0
    assert closed_only["participants"][0]["percent"] is None
    assert closed_only["everyone"] == 0


def test_group_progress_page_ranks_filters_and_keeps_you(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    path = f"/passports/{group['new']}/"
    signin(client, group["user"])
    page = client.get(path).text
    assert "Group progress" in page
    # The friend visited the target in 2025; the owner's 2024 flight doesn't count.
    assert "Later friend" in page and "Original pilot (you)" in page
    assert "Visited by anyone" in page and "<strong>1 / 1</strong>" in page
    with app.app_context():
        series = db.session.get(PassportSeries, group["series"])
        new = db.session.get(Passport, group["new"])
        stats = groups.combined(series, [new], group["user"], current=new)["stats"]
        keys = {p["name"]: p["key"] for p in stats["participants"]}
    # Filtering by participant shows their airfields without changing "you".
    friend = client.get(path, query_string={"participant": keys["Later friend"]}).text
    assert "No airfields match this view." not in friend
    assert "Your progress" in friend and "<strong>0 / 1</strong>" in friend
    mine = client.get(path, query_string={"participant": keys["Original pilot"]}).text
    assert "No airfields match this view." in mine
    assert (
        "No airfields match this view."
        in client.get(path, query_string={"state": "nobody"}).text
    )
    assert (
        "No airfields match this view."
        in client.get(path, query_string={"state": "everyone"}).text
    )
    assert (
        "No airfields match this view."
        not in client.get(path, query_string={"state": "anyone"}).text
    )
    # An unknown or invalid key matches nothing, or is ignored.
    assert (
        "No airfields match this view."
        in client.get(path, query_string={"participant": 99}).text
    )
    assert (
        "No airfields match this view."
        not in client.get(path, query_string={"participant": "x"}).text
    )


def test_leaving_participant_drops_out_of_live_figures(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    with app.app_context():
        friend = PassportParticipant.query.filter_by(
            passport_id=group["new"], user_id=group["other"]
        ).one()
        friend.is_active = False
        db.session.commit()
    signin(client, group["user"])
    page = client.get(f"/passports/{group['new']}/").text
    assert "Later friend" not in page
    assert "Original pilot (you)" in page


def test_personal_passports_have_no_group_figures_or_filters(
    client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    path = f"/passports/{owner['passport']}/"
    page = client.get(path).text
    assert "Group progress" not in page and "Visited by anyone" not in page
    # Group-only filter values fall back to showing every airfield.
    nobody = client.get(path, query_string={"state": "nobody", "participant": 1}).text
    assert "No airfields match this view." not in nobody
    # The existing filters still apply: the owner's one target is open.
    assert (
        "No airfields match this view."
        in client.get(path, query_string={"state": "closed"}).text
    )
    shown = {
        state: "No airfields match this view."
        not in client.get(path, query_string={"state": state}).text
        for state in ("visited", "unvisited")
    }
    assert shown["visited"] != shown["unvisited"]


def test_linked_editions_compare_group_and_each_participant_independently(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    from models import PassportAirfield, TenantUser
    from passports.catalogue import create_custom

    with app.app_context():
        series = db.session.get(PassportSeries, group["series"])
        new = db.session.get(Passport, group["new"])
        for name, code, closed in (
            ("New open airfield", "NEWOPEN", False),
            ("Closed unvisited airfield", "NEWCLOSED", True),
        ):
            field = create_custom(group["tenant"], name, code=code)
            field.is_closed = closed
            db.session.add(
                PassportAirfield(
                    tenant_id=group["tenant"], passport_id=new.id, airfield_id=field.id
                )
            )
        db.session.flush()
        passports, _accepted = groups.visible_editions(series, group["user"])
        comparisons = groups.combined(series, passports, group["user"])["comparisons"]
        old, recent = comparisons
        assert (
            old["group_stats"]["anyone_percent"]
            == old["group_stats"]["everyone_percent"]
            == 100
        )
        assert recent["added"] == 2 and recent["removed"] == 0
        stats = recent["group_stats"]
        assert stats["denominator"] == 2 and stats["closed_excluded"] == 1
        assert stats["anyone_percent"] == 50 and stats["everyone_percent"] == 0
        assert {p["name"]: p["percent"] for p in stats["participants"]} == {
            "Original pilot": 0,
            "Later friend": 50,
        }
        # A roleless friend sees only authorised history, with no own retroactive
        # participation or progress in the old edition.
        member = db.session.get(TenantUser, (group["other"], group["tenant"]))
        member.role, member.access_scope = None, "personal"
        db.session.commit()
    signin(client, group["other"])
    page = client.get(
        f"/passports/series/{group['series']}/",
        query_string={"edition": "all", "history": "1"},
    )
    assert page.status_code == 200
    assert 'id="passport-edition-comparisons"' in page.text
    assert (
        "Participant progress" in page.text
        and "Read-only history; not a participant" in page.text
    )
    assert "1 / 2 (50%)" in page.text and "Later friend (you): 1 / 2 (50%)" in page.text
    assert "PRIVATE MANUAL" not in page.text and "PRIVATE FLIGHT" not in page.text


def test_history_comparisons_do_not_disclose_withheld_visit_totals(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        old = db.session.get(Passport, group["old"])
        participant = PassportParticipant.query.filter_by(
            passport_id=old.id, user_id=group["user"]
        ).one()
        participant.share_history = False
        db.session.commit()
        series = db.session.get(PassportSeries, group["series"])
        passports, _accepted = groups.visible_editions(
            series, group["other"], include_history=True
        )
        data = groups.combined(series, passports, group["other"])
        historical = next(e for e in data["comparisons"] if e["passport"].id == old.id)
        stats = historical["group_stats"]
        assert stats["participants"] == [] and stats["anyone"] is None
        assert stats["anyone_percent"] is None and stats["everyone_percent"] is None
        assert historical["progress"]["visited"] == 0
