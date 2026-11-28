"""Independent editions, explicit copies, private combined maps and colour priority."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Passport,
    PassportAirfield,
    PassportAirfieldIdentity,
    PassportDraft,
    PassportParticipant,
    PassportSeries,
    PassportVisit,
    db,
)
from passports import editions
from passports.catalogue import create_custom
from passports.management import BuilderError, create_personal, metadata_values

from tests.test_passport_builder import posted, signin
from tests.test_passport_eligibility import flight
from tests.test_passport_management import owner

__all__ = ["owner"]


def extra(
    owner: dict[str, int],
    title: str,
    start: date | None = None,
    end: date | None = None,
) -> Passport:
    passport = create_personal(
        owner["tenant"], owner["user"], metadata_values({"title": title})
    )
    passport.start_date, passport.end_date = start, end
    db.session.add(
        PassportAirfield(
            tenant_id=owner["tenant"],
            passport_id=passport.id,
            airfield_id=owner["field"],
        )
    )
    db.session.flush()
    return passport


def test_current_selection_ordering_and_fallback(
    app: Flask, owner: dict[str, int]
) -> None:
    with app.app_context():
        undated = db.session.get(Passport, owner["passport"])
        old = extra(owner, "Winter", date(2024, 1, 1), date(2024, 3, 31))
        current = extra(owner, "Season", date(2025, 1, 1), date(2025, 12, 31))
        overlap = extra(owner, "November", date(2025, 11, 1), date(2025, 11, 30))
        tie = extra(owner, "Other November", overlap.start_date, overlap.end_date)
        end_only = extra(owner, "Until November", end=date(2025, 11, 30))
        passports = [undated, old, current, overlap, tie, end_only]
        today = date(2025, 11, 15)
        assert editions.current_edition(passports, today=today) == tie
        tie.archived_at = datetime.now(UTC)
        assert editions.current_edition(passports, today=today) == overlap
        assert editions.current_edition(passports, today=date(2026, 1, 1)) is None
        assert editions.current_edition([undated]) is None
        assert editions.latest_edition(passports) == tie
        assert editions.latest_edition([undated]) == undated
        assert editions.latest_edition([]) is None
        assert editions.ordered(list(reversed(passports)))[-1] == undated
        assert editions.current_edition([end_only], today=today) == end_only


def test_combined_identity_counts_dates_and_priority(
    app: Flask, owner: dict[str, int]
) -> None:
    with app.app_context():
        old = db.session.get(Passport, owner["passport"])
        old.start_date, old.end_date = date(2024, 1, 1), date(2024, 12, 31)
        old.edition_label = "2024"
        new = extra(owner, "2025", date(2025, 1, 1), date(2025, 12, 31))
        overlap = extra(
            owner, "Overlapping season", date(2024, 1, 1), date(2025, 12, 31)
        )
        for day in [
            date(2024, 6, 1),
            date(2025, 6, 1),
            date(2025, 7, 1),
            date(2025, 8, 1),
        ]:
            flight(owner["user"], date=day)
        # Excluded manual evidence must not alter frequency; private note must never enter map rows.
        part = PassportParticipant.query.filter_by(passport_id=old.id).one()
        db.session.add(
            PassportVisit(
                passport_id=old.id,
                participant_id=part.id,
                target_id=owner["target"],
                author_user_id=owner["user"],
                visit_date=date(2023, 1, 1),
                note="PRIVATE",
            )
        )
        retired = create_custom(owner["tenant"], "Removed field", code="OLD1")
        old_target = PassportAirfield(
            tenant_id=owner["tenant"], passport_id=old.id, airfield_id=retired.id
        )
        db.session.add(old_target)
        db.session.flush()
        db.session.add(
            PassportVisit(
                passport_id=old.id,
                participant_id=part.id,
                target_id=old_target.id,
                author_user_id=owner["user"],
                visit_date=date(2024, 2, 1),
                note="PRIVATE manual visit",
            )
        )
        db.session.commit()
        newest = editions.combined([old, new, overlap], owner["user"], current=new)
        row = newest["rows"][0]
        assert len(newest["rows"]) == 2 and row["count"] == 4 and row["current"]
        assert row["edition_label"] == "2025"
        assert [e["count"] for e in row["editions"]] == [1, 4, 3]
        oldest = editions.combined([old, new, overlap], owner["user"], oldest=True)
        assert oldest["rows"][0]["edition_label"] == "2024"
        assert oldest["rows"][0]["count"] == 4
        assert editions.combined([new], owner["user"])["rows"][0]["count"] == 3
        assert all(
            key not in row for key in ("events", "note", "flight_id", "aircraft")
        )
        assert newest["comparisons"][0]["added"] == 2
        assert newest["comparisons"][1]["removed"] == 1
        assert newest["rows"][1]["count"] == 1 and not newest["rows"][1]["current"]
        assert newest["comparisons"][1]["added"] == 0
        # An unvisited current target doesn't steal an older stamp's colour.
        new.start_date, new.end_date = date(2026, 1, 1), date(2026, 12, 31)
        assert (
            editions.combined([old, new], owner["user"])["rows"][0]["edition_label"]
            == "2024"
        )
        assert editions.combined([new], owner["other"])["rows"][0]["colour"] is None


def test_link_owner_scope_and_explicit_relationship(
    app: Flask, owner: dict[str, int]
) -> None:
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        for title in ("", "x" * 129):
            with pytest.raises(BuilderError):
                editions.link(p, owner["user"], title=title)
        series = editions.link(p, owner["user"], title="Booklet")
        second = extra(owner, "Different name")
        editions.link(second, owner["user"], series_id=series.id)
        assert p.series_display_order < second.series_display_order
        assert editions.ordered([second, p]) == [p, second]
        with pytest.raises(BuilderError):
            editions.owned_series(owner["tenant"], owner["other"], series.id)
        with pytest.raises(BuilderError):
            editions.link(p, owner["other"], series_id=series.id)
        with pytest.raises(BuilderError):
            editions.owned_series(owner["tenant"] + 1, owner["user"], series.id)


def test_link_and_series_http_privacy(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    base = f"/passports/{owner['passport']}"
    assert "Create next edition" in client.get(base + "/").text
    assert client.get(base + "/series").status_code == 200
    assert client.post(base + "/series", data={"series_id": "bad"}).status_code == 400
    assert client.post(base + "/series", data={"title": ""}).status_code == 400
    response = client.post(base + "/series", data={"title": "Booklet"})
    assert response.status_code == 302
    url = response.location
    response = client.get(url)
    assert (
        response.status_code == 200
        and "No participating edition is currently active" in response.text
    )
    assert (
        client.get(url + "?edition=all&priority=oldest&frequency=1").status_code == 200
    )
    for value in ("bad", "9" * 100, "999999"):
        assert client.get(url + "?edition=" + value).status_code == 404
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        p.start_date, p.end_date = date.today(), date.today() + timedelta(days=1)
        p.edition_label = "Sample edition"
        series_id = p.series_id
        field = db.session.get(PassportAirfield, owner["target"])
        identity = db.session.get(PassportAirfieldIdentity, field.airfield_id)
        identity.latitude, identity.longitude = 50, 5
        db.session.commit()
    response = client.get(url)
    assert "Current edition" in response.text
    detail = client.get(base + "/").text
    assert f'Sample edition · <a href="{url}?edition=all">Linked editions</a>' in detail
    assert client.get(url + f"?edition={owner['passport']}").status_code == 200
    assert (
        client.post(base + "/series", data={"series_id": str(series_id)}).status_code
        == 302
    )
    with patch("passports.series_routes.current_membership", return_value=None):
        assert client.get(url).status_code == 403
    signin(client, owner["other"])
    assert client.get(url).status_code == 404
    assert (
        client.post(base + "/series", data={"series_id": str(series_id)}).status_code
        == 404
    )
    assert client.get(base + "/next-edition").status_code == 404
    signin(client, owner["user"])
    with app.app_context():
        empty = PassportSeries(
            tenant_id=owner["tenant"], owner_user_id=owner["user"], title="Empty"
        )
        db.session.add(empty)
        db.session.commit()
        empty_id = empty.id
    assert client.get(f"/passports/series/{empty_id}/").status_code == 200


@pytest.mark.parametrize("already_linked", [False, True])
def test_explicit_copy_review_save_no_evidence_or_participants(
    app: Flask,
    client: FlaskClient,
    owner: dict[str, int],
    already_linked: bool,
) -> None:
    signin(client, owner["user"])
    base = f"/passports/{owner['passport']}"
    with app.app_context():
        source = db.session.get(Passport, owner["passport"])
        if already_linked:
            editions.link(source, owner["user"], title="Voucher booklet")
            source.source_url = "https://source.example/list"
            source.source_retrieved_at = datetime.now(UTC)
        source.archived_at = datetime.now(UTC)
        db.session.add(
            PassportParticipant(
                passport_id=source.id,
                user_id=owner["other"],
                display_name="Friend",
                accepted_at=datetime.now(UTC),
            )
        )
        db.session.commit()
    assert client.get(base + "/next-edition").status_code == 200
    assert client.post(base + "/next-edition", data={"title": ""}).status_code == 400
    response = client.post(
        base + "/next-edition",
        data={
            "title": "Summer 2027",
            "start_date": "2027-05-01",
            "end_date": "2027-08-31",
        },
    )
    assert response.status_code == 200 and 'action="/passports/new"' in response.text
    with app.app_context():
        assert Passport.query.count() == 1
        assert PassportDraft.query.one().payload["selected"] == [
            f"field:{owner['field']}"
        ]
    response = client.post("/passports/new", data=posted(response, "review"))
    assert response.status_code == 200
    response = client.post("/passports/new", data=posted(response, "save"))
    assert response.status_code == 302
    with app.app_context():
        old = db.session.get(Passport, owner["passport"])
        new = Passport.query.filter(Passport.id != old.id).one()
        assert new.series_id == old.series_id and new.series_id is not None
        assert new.source_url == old.source_url
        assert new.start_date == date(2027, 5, 1) and old.start_date is None
        assert PassportParticipant.query.filter_by(passport_id=new.id).count() == 1
        assert PassportVisit.query.filter_by(passport_id=new.id).count() == 0
        assert PassportDraft.query.count() == 0
        assert new.archived_at is None and old.archived_at is not None
        assert (
            PassportAirfield.query.filter_by(passport_id=new.id).one().airfield_id
            == owner["field"]
        )
    assert client.get(response.location).status_code == 200


def test_copy_refuses_changed_source(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    signin(client, owner["user"])
    response = client.post(
        f"/passports/{owner['passport']}/next-edition", data={"title": "Next"}
    )
    with app.app_context():
        source = db.session.get(Passport, owner["passport"])
        source.title = "Edited in another tab"
        db.session.commit()
    response = client.post("/passports/new", data=posted(response, "review"))
    assert response.status_code == 409 and "source edition changed" in response.text
    with app.app_context():
        assert Passport.query.count() == 1


def test_passport_index_groups_editions_with_maps_and_progress(
    app: Flask, client: FlaskClient, owner: dict[str, int]
) -> None:
    with app.app_context():
        old = db.session.get(Passport, owner["passport"])
        old.title = "Previous booklet"
        old.end_date = date.today() - timedelta(days=1)
        current = extra(owner, "Current booklet", date.today())
        standalone = extra(owner, "Standalone passport")
        series = editions.link(old, owner["user"], title="Voucher booklets")
        editions.link(current, owner["user"], series_id=series.id)
        flight(owner["user"], date=date.today())
        db.session.commit()
        old_id, current_id, standalone_id = old.id, current.id, standalone.id

    signin(client, owner["user"])
    page = client.get("/passports/").get_data(as_text=True)
    assert page.count("data-passport-progress") == 2
    assert f'href="/passports/{current_id}/">Current booklet' in page
    assert f'href="/passports/{standalone_id}/">Standalone passport' in page
    assert f'href="/passports/{old_id}/">Previous booklet' in page
    assert "Other editions" in page
    assert ">Voucher booklets</h2>" in page
    assert "1 of 1 airfields visited (100%)" in page
    assert "0 of 1 airfields visited (0%)" in page
    assert page.index("Export my passport data") > page.index("Standalone passport")
