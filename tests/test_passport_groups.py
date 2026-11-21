"""Group consent, separate edition/history scope and strictly sanitised map evidence."""

from datetime import UTC, date, datetime

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Passport,
    PassportAirfield,
    PassportParticipant,
    PassportSeries,
    PassportSeriesMember,
    PassportVisit,
    PassportVoucherAllocation,
    db,
)
from passports import editions, groups
from passports.catalogue import create_custom
from passports.management import BuilderError

from tests.test_passport_builder import posted, signin
from tests.test_passport_editions import extra
from tests.test_passport_eligibility import flight
from tests.test_passport_management import owner

__all__ = ["owner"]


def consent_values(name: str = "Pilot friend", history: bool = True) -> dict[str, str]:
    return {
        "series_title": "Our group",
        "display_name": name,
        "share_visits": "yes",
        "share_history": "yes" if history else "",
    }


@pytest.fixture
def group(app: Flask, owner: dict[str, int]) -> dict[str, int]:
    with app.app_context():
        old = db.session.get(Passport, owner["passport"])
        old.start_date, old.end_date = date(2024, 1, 1), date(2024, 12, 31)
        old.edition_label = "2024"
        series = groups.enable(old, owner["user"], consent_values("Original pilot"))
        new = extra(owner, "2025", date(2025, 1, 1), date(2025, 12, 31))
        editions.link(new, owner["user"], series_id=series.id)
        groups.enable(new, owner["user"], consent_values("Original pilot"))
        db.session.add(
            PassportParticipant(
                passport_id=new.id,
                user_id=owner["other"],
                accepted_at=datetime.now(UTC),
                display_name="Not shared yet",
            )
        )
        db.session.flush()
        groups.consent(new, owner["other"], consent_values("Later friend"))
        own_visit = flight(
            owner["user"],
            date=date(2024, 6, 1),
            notes="PRIVATE FLIGHT",
            other_aircraft_registration="PRIVATE REG",
        )
        flight(
            owner["other"], date=date(2024, 6, 1)
        )  # never backfilled into the unjoined edition
        flight(owner["other"], date=date(2025, 6, 1))
        db.session.flush()
        db.session.add(
            PassportVisit(
                passport_id=old.id,
                participant_id=PassportParticipant.query.filter_by(passport_id=old.id)
                .one()
                .id,
                target_id=owner["target"],
                author_user_id=owner["user"],
                visit_date=own_visit.date,
                flight_id=own_visit.id,
                note="PRIVATE MANUAL",
            )
        )
        db.session.commit()
        return owner | {"series": series.id, "old": old.id, "new": new.id}


def test_history_targets_without_retroactive_stamps_or_vouchers(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        series = db.session.get(PassportSeries, group["series"])
        visible, accepted = groups.visible_editions(series, group["other"])
        assert [p.id for p in visible] == [group["new"]]
        assert accepted == {group["new"]}
        visible, _ = groups.visible_editions(
            series, group["other"], include_history=True
        )
        assert [p.id for p in visible] == [group["old"], group["new"]]
        old = db.session.get(Passport, group["old"])
        # Counts are the viewer's own unless the whole group is chosen.
        assert groups.combined(series, [old], group["other"])["rows"][0]["count"] == 0
        view = groups.combined(series, [old], group["other"], scope="group")
        row = view["rows"][0]
        assert row["state"] == "others" and row["count"] == 1 and row["own_count"] == 0
        assert row["friends"] == [
            {
                "name": "Original pilot",
                "editions": [{"label": "2024", "dates": ["2024-06-01"]}],
            }
        ]
        assert not PassportParticipant.query.filter_by(
            passport_id=old.id, user_id=group["other"]
        ).first()
        assert not PassportVoucherAllocation.query.filter_by(
            passport_id=old.id, personal_user_id=group["other"]
        ).first()
        assert view["progress"][old.id]["visited"] == 0
        assert not any(
            secret in repr(view["rows"])
            for secret in ("PRIVATE", "flight_id", "aircraft", "note")
        )
        # A personal passport linked to the series is not automatically shared.
        private = extra(group, "SECRET PERSONAL")
        editions.link(private, group["user"], series_id=series.id)
        db.session.commit()
        assert (
            private
            not in groups.visible_editions(
                series, group["other"], include_history=True
            )[0]
        )
        with pytest.raises(BuilderError):
            groups.shared_progress(series, [private], group["other"])


def test_own_colour_precedence_frequency_and_history_withdrawal(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        series = db.session.get(PassportSeries, group["series"])
        passports, _ = groups.visible_editions(
            series, group["other"], include_history=True
        )
        newest = groups.combined(series, passports, group["other"], scope="group")
        oldest = groups.combined(
            series, passports, group["other"], oldest=True, scope="group"
        )
        assert newest["rows"][0]["state"] == "own"
        assert (
            newest["rows"][0]["edition_label"]
            == oldest["rows"][0]["edition_label"]
            == "2025"
        )
        assert newest["rows"][0]["count"] == oldest["rows"][0]["count"] == 2
        old = db.session.get(Passport, group["old"])
        groups.consent(
            old, group["user"], consent_values("Original pilot", history=False)
        )
        db.session.commit()
        view = groups.combined(series, [old], group["other"])
        assert view["rows"][0]["state"] == "unvisited" and view["rows"][0]["count"] == 0
        # Participants still see consented visits in their own edition.
        new = db.session.get(Passport, group["new"])
        groups.consent(
            new, group["other"], consent_values("Later friend", history=False)
        )
        db.session.commit()
        assert (
            groups.combined(series, [new], group["user"])["rows"][0]["state"]
            == "others"
        )


def test_group_member_removal_blocks_every_history_view(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    signin(client, group["other"])
    path = f"/passports/series/{group['series']}/?history=1&edition=all"
    assert client.get(path).status_code == 200
    assert client.get(f"/passports/{group['old']}/").status_code == 404
    with app.app_context():
        member = db.session.get(PassportSeriesMember, (group["series"], group["other"]))
        member.is_active, member.blocked = False, True
        db.session.commit()
        with pytest.raises(BuilderError):
            groups.consent(
                db.session.get(Passport, group["new"]), group["other"], consent_values()
            )
    assert client.get(path).status_code == 404
    assert client.get(f"/passports/{group['new']}/").status_code == 404
    assert client.get(f"/passports/{group['new']}/group").status_code == 404
    signin(client, group["user"])
    assert (
        b"Later friend"
        not in client.get(f"/passports/series/{group['series']}/?edition=all").data
    )


@pytest.mark.parametrize(
    "field,value",
    [("display_name", ""), ("display_name", "x" * 129), ("share_visits", "")],
)
def test_consent_validation_rolls_back_enable(
    app: Flask, client: FlaskClient, owner: dict[str, int], field: str, value: str
) -> None:
    signin(client, owner["user"])
    response = client.post(
        f"/passports/{owner['passport']}/group", data=consent_values() | {field: value}
    )
    assert response.status_code == 400
    with app.app_context():
        assert not db.session.get(Passport, owner["passport"]).is_group
        assert PassportSeriesMember.query.count() == 0
        assert PassportSeries.query.count() == 0


def test_group_http_scopes_and_explicit_enabling(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    path = f"/passports/{owner['passport']}/group"
    signin(client, owner["other"])
    assert client.get(path).status_code == 404
    signin(client, owner["user"])
    assert client.get(path).status_code == 200
    response = client.post(path, data=consent_values("Owner"))
    assert response.status_code == 302
    series_url = response.location
    page = client.get(series_url)
    assert page.status_code == 200 and b"editions I participated in" in page.data
    assert b"hx-history" in page.data
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        db.session.add(
            PassportParticipant(
                passport_id=p.id,
                user_id=owner["other"],
                display_name="Before consent",
                accepted_at=datetime.now(UTC),
            )
        )
        db.session.commit()
    signin(client, owner["other"])
    assert client.get(series_url).status_code == 404
    assert client.get(path).status_code == 200
    assert client.post(path, data=consent_values("New friend")).status_code == 302
    assert client.get(series_url).status_code == 200
    assert client.get(series_url + "?edition=9999").status_code == 404
    assert client.get(series_url + "?edition=not-a-number").status_code == 404
    assert (
        client.get(series_url + "?edition=all&priority=oldest&frequency=1").status_code
        == 200
    )


def test_history_scope_does_not_include_future_or_current_unjoined_editions(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        series = db.session.get(PassportSeries, group["series"])
        future = extra(group, "Future", date(2090, 1, 1), date(2090, 12, 31))
        editions.link(future, group["user"], series_id=series.id)
        groups.enable(future, group["user"], consent_values())
        db.session.commit()
        assert (
            future
            not in groups.visible_editions(
                series, group["other"], include_history=True
            )[0]
        )
        with pytest.raises(BuilderError):
            editions.link(future, group["user"], title="Other")
        # No active/accepted participant means no accidental own logbook stamps.
        PassportParticipant.query.filter_by(
            passport_id=group["old"], user_id=group["other"]
        ).delete()
        member = db.session.get(PassportSeriesMember, (series.id, group["other"]))
        member.is_active = False
        db.session.commit()
        with pytest.raises(BuilderError):
            groups.visible_editions(series, group["other"], include_history=True)


def test_three_states_and_overlapping_frequency(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        series = db.session.get(PassportSeries, group["series"])
        new = db.session.get(Passport, group["new"])
        only_friend = create_custom(group["tenant"], "Friend only", code="FREN")
        nobody = create_custom(group["tenant"], "Nobody", code="NONE")
        db.session.flush()
        db.session.add_all(
            [
                PassportAirfield(
                    tenant_id=group["tenant"], passport_id=new.id, airfield_id=f.id
                )
                for f in (only_friend, nobody)
            ]
        )
        flight(group["user"], date=date(2025, 6, 1), arrival_icao="FREN")
        overlap = extra(group, "Overlap", date(2024, 1, 1), date(2025, 12, 31))
        editions.link(overlap, group["user"], series_id=series.id)
        groups.enable(overlap, group["user"], consent_values("Original pilot"))
        db.session.commit()
        passports, _ = groups.visible_editions(
            series, group["other"], include_history=True
        )
        view = groups.combined(
            series, passports, group["other"], current=new, scope="group"
        )
        rows = {r["id"]: r for r in view["rows"]}
        assert (
            rows[group["field"]]["count"] == 2
        )  # same original flight in two editions counts once
        assert rows[group["field"]]["state"] == "own"
        assert rows[only_friend.id]["state"] == "others"
        assert (
            rows[nobody.id]["state"] == "unvisited"
            and rows[nobody.id]["colour"] is None
        )
        # Both newest/oldest priorities apply to friends-only evidence as well.
        only_history = groups.combined(
            series, [passports[0], overlap], group["other"], oldest=True
        )
        assert only_history["rows"][0]["edition_label"] == "2024"
        assert (
            groups.combined(series, [passports[0], overlap], group["other"])["rows"][0][
                "edition_label"
            ]
            == "Overlap"
        )


def test_membership_validation_and_no_implicit_consent(
    app: Flask, owner: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        with pytest.raises(BuilderError):
            groups.consent(p, owner["user"], consent_values())
        groups.enable(p, owner["user"], consent_values())
        db.session.commit()
        with pytest.raises(BuilderError):
            groups.consent(p, owner["other"], consent_values())
        series = db.session.get(PassportSeries, p.series_id)
        assert groups.visible_editions(series, owner["user"])[1] == {p.id}
        series.is_group = False
        with pytest.raises(BuilderError):
            groups.require_series(series, owner["user"])
        db.session.rollback()


def test_legacy_unconsented_participation_gets_no_history_credit(
    app: Flask, group: dict[str, int]
) -> None:
    with app.app_context():
        series = db.session.get(PassportSeries, group["series"])
        old = db.session.get(Passport, group["old"])
        db.session.add(
            PassportParticipant(
                passport_id=old.id,
                user_id=group["other"],
                display_name="Unconsented",
                accepted_at=datetime.now(UTC),
            )
        )
        db.session.commit()
        view = groups.combined(series, [old], group["other"])
        assert view["rows"][0]["own_count"] == 0
        assert old.id not in view["participating"]


def test_empty_group_scope_and_orphaned_edition_fail_closed(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    signin(client, group["other"])
    with app.app_context():
        PassportParticipant.query.filter_by(user_id=group["other"]).update(
            {"is_active": False}
        )
        db.session.commit()
    assert client.get(f"/passports/series/{group['series']}/").status_code == 200
    assert (
        client.get(
            f"/passports/series/{group['series']}/?history=1&edition=all"
        ).status_code
        == 200
    )
    signin(client, group["user"])
    with app.app_context():
        p = db.session.get(Passport, group["new"])
        p.series_id = None
        db.session.commit()
    assert (
        client.post(
            f"/passports/{group['new']}/group", data=consent_values()
        ).status_code
        == 404
    )


def test_personal_participant_cannot_enable_owner_group(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    with app.app_context():
        db.session.add(
            PassportParticipant(
                passport_id=owner["passport"],
                user_id=owner["other"],
                display_name="Friend",
                accepted_at=datetime.now(UTC),
            )
        )
        db.session.commit()
    signin(client, owner["other"])
    assert client.get(f"/passports/{owner['passport']}/group").status_code == 404


def test_shared_period_is_frozen_but_name_edits_remain_available(
    app: Flask, client: FlaskClient, group: dict[str, int]
) -> None:
    signin(client, group["user"])
    path = f"/passports/{group['new']}/edit"
    draft = client.get(path)
    refused = client.post(path, data=posted(draft, "review", start_date="2024-01-01"))
    assert refused.status_code == 409
    assert b"Create a new edition to change the dates" in refused.data
    with app.app_context():
        p = db.session.get(Passport, group["new"])
        assert p.start_date == date(2025, 1, 1)
    draft = client.get(path)
    permitted = client.post(
        path, data=posted(draft, "review", title="New display label")
    )
    assert permitted.status_code == 200 and b"Save passport" in permitted.data
