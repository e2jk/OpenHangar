"""
Tests for the personal minimums feature (docs/backlog.md "Pilots: personal
minimums") — versioned per-pilot minimums document (draft -> active ->
superseded), starter templates, section/item CRUD, recency nudges, and the
read-only surfacing on the flight form and logbook page.
"""

from datetime import date, timedelta

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    PersonalMinimumsItem,
    PersonalMinimumsRevision,
    PersonalMinimumsSection,
    PersonalMinimumsStatus,
    PersonalMinimumsTag,
    PilotLogbookEntry,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)
from pilots.personal_minimums import (  # pyright: ignore[reportMissingImports]
    STARTER_FULL,
    STARTER_LIGHT,
    recency_breaches,
)


def _make_user(app, email, role=Role.PILOT):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(email=email, password_hash=_pw_hash.hash("pw"), is_active=True)
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _draft(app, uid):
    """Must be called from within an already-open app.app_context()."""
    return PersonalMinimumsRevision.query.filter_by(
        user_id=uid, status=PersonalMinimumsStatus.DRAFT
    ).one()


def _add_section(app, client, uid, title):
    client.post("/pilot/minimums/section/add", data={"title": title})
    with app.app_context():
        return (
            PersonalMinimumsSection.query.filter_by(revision_id=_draft(app, uid).id)
            .filter_by(title=title)
            .one()
            .id
        )


def _add_item(app, client, section_id, label, value="", tag="", numeric=""):
    client.post(
        "/pilot/minimums/item/add",
        data={
            "section_id": str(section_id),
            "label": label,
            "value": value,
            "tag": tag,
            "numeric_value": numeric,
        },
    )


def _add_logbook_entry(app, uid, days_ago, dual=0.0):
    with app.app_context():
        db.session.add(
            PilotLogbookEntry(
                pilot_user_id=uid,
                date=date.today() - timedelta(days=days_ago),
                single_pilot_se=1.5,
                function_dual=dual or None,
            )
        )
        db.session.commit()


# ── Starter picker / creation ─────────────────────────────────────────────────


class TestStarterPicker:
    def test_view_shows_starter_picker_when_no_revision(self, app, client):
        uid, tid = _make_user(app, "p1@ex.com")
        _login(app, client, uid)
        r = client.get("/pilot/minimums")
        assert r.status_code == 200
        assert b"Start with Light" in r.data
        assert b"Start with Full" in r.data
        assert b"Start blank" in r.data

    def test_edit_with_no_draft_redirects(self, app, client):
        uid, tid = _make_user(app, "p2@ex.com")
        _login(app, client, uid)
        r = client.get("/pilot/minimums/edit")
        assert r.status_code == 302

    def test_create_blank_starter(self, app, client):
        uid, tid = _make_user(app, "p3@ex.com")
        _login(app, client, uid)
        r = client.post("/pilot/minimums/create", data={"starter": "blank"})
        assert r.status_code == 302
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(user_id=uid).one()
            assert rev.revision_number == 1
            assert rev.status == PersonalMinimumsStatus.DRAFT
            assert rev.sections == []

    def test_create_light_starter_has_expected_sections(self, app, client):
        uid, tid = _make_user(app, "p4@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "light"})
        r = client.get("/pilot/minimums/edit")
        assert r.status_code == 200
        assert b"Winds" in r.data
        assert b"Weather" in r.data
        assert b"Fuel" in r.data
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(user_id=uid).one()
            assert len(rev.sections) == len(STARTER_LIGHT)
            fuel_section = next(s for s in rev.sections if s.title == "Fuel")
            assert (
                fuel_section.items[0].semantic_tag
                == PersonalMinimumsTag.MIN_FUEL_RESERVE_MINUTES
            )

    def test_create_full_starter_has_expected_sections(self, app, client):
        uid, tid = _make_user(app, "p5@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "full"})
        r = client.get("/pilot/minimums/edit")
        assert r.status_code == 200
        assert b"Guiding principles" in r.data
        assert b"Recency commitments" in r.data
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(user_id=uid).one()
            assert len(rev.sections) == len(STARTER_FULL)

    def test_invalid_starter_falls_back_to_blank(self, app, client):
        uid, tid = _make_user(app, "p6@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "not-a-starter"})
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(user_id=uid).one()
            assert rev.sections == []

    def test_create_refuses_second_draft(self, app, client):
        uid, tid = _make_user(app, "p7@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        r = client.post(
            "/pilot/minimums/create", data={"starter": "light"}, follow_redirects=True
        )
        assert r.status_code == 200
        with app.app_context():
            assert PersonalMinimumsRevision.query.filter_by(user_id=uid).count() == 1

    def test_view_redirects_to_edit_when_draft_exists_and_no_active(self, app, client):
        uid, tid = _make_user(app, "p8@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        r = client.get("/pilot/minimums")
        assert r.status_code == 302
        assert "/pilot/minimums/edit" in r.headers["Location"]

    def test_starters_are_well_formed(self):
        for starter in (STARTER_LIGHT, STARTER_FULL):
            assert len(starter) > 0
            for _title, items in starter:
                assert len(items) > 0
                for label, tag in items:
                    assert str(label)
                    if tag is not None:
                        assert tag in PersonalMinimumsTag.ALL


# ── Section / item CRUD ─────────────────────────────────────────────────────────


class TestSectionCrud:
    def test_add_section(self, app, client):
        uid, tid = _make_user(app, "s1@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        r = client.post("/pilot/minimums/section/add", data={"title": "Weather limits"})
        assert r.status_code == 302
        with app.app_context():
            rev = _draft(app, uid)
            assert len(rev.sections) == 1
            assert rev.sections[0].title == "Weather limits"
            assert rev.sections[0].sort_order == 0

    def test_add_section_without_title_rejected(self, app, client):
        uid, tid = _make_user(app, "s2@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        client.post("/pilot/minimums/section/add", data={"title": ""})
        with app.app_context():
            assert _draft(app, uid).sections == []

    def test_add_section_without_draft_404s(self, app, client):
        uid, tid = _make_user(app, "s3@ex.com")
        _login(app, client, uid)
        r = client.post("/pilot/minimums/section/add", data={"title": "X"})
        assert r.status_code == 404

    def test_edit_section_title(self, app, client):
        uid, tid = _make_user(app, "s4@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Old title")
        r = client.post(
            f"/pilot/minimums/section/{sid}/edit", data={"title": "New title"}
        )
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(PersonalMinimumsSection, sid).title == "New title"

    def test_edit_section_blank_title_rejected(self, app, client):
        uid, tid = _make_user(app, "s5@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Keep me")
        client.post(f"/pilot/minimums/section/{sid}/edit", data={"title": ""})
        with app.app_context():
            assert db.session.get(PersonalMinimumsSection, sid).title == "Keep me"

    def test_delete_section_cascades_items(self, app, client):
        uid, tid = _make_user(app, "s6@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Doomed")
        _add_item(app, client, sid, "Item A", "value")
        r = client.post(f"/pilot/minimums/section/{sid}/delete")
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(PersonalMinimumsSection, sid) is None
            assert PersonalMinimumsItem.query.filter_by(section_id=sid).count() == 0

    def test_move_section_up_and_down(self, app, client):
        uid, tid = _make_user(app, "s7@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid_a = _add_section(app, client, uid, "A")
        sid_b = _add_section(app, client, uid, "B")
        client.post(f"/pilot/minimums/section/{sid_b}/move-up")
        with app.app_context():
            a = db.session.get(PersonalMinimumsSection, sid_a)
            b = db.session.get(PersonalMinimumsSection, sid_b)
            assert b.sort_order < a.sort_order
        client.post(f"/pilot/minimums/section/{sid_b}/move-down")
        with app.app_context():
            a = db.session.get(PersonalMinimumsSection, sid_a)
            b = db.session.get(PersonalMinimumsSection, sid_b)
            assert a.sort_order < b.sort_order

    def test_move_section_up_at_top_is_noop(self, app, client):
        uid, tid = _make_user(app, "s8@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Only one")
        r = client.post(f"/pilot/minimums/section/{sid}/move-up")
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(PersonalMinimumsSection, sid).sort_order == 0

    def test_section_actions_404_on_non_draft(self, app, client):
        uid, tid = _make_user(app, "s9@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "To publish")
        _add_item(app, client, sid, "Item", "value")
        client.post("/pilot/minimums/publish")
        r = client.post(f"/pilot/minimums/section/{sid}/edit", data={"title": "Nope"})
        assert r.status_code == 404


class TestItemCrud:
    def test_add_item(self, app, client):
        uid, tid = _make_user(app, "i1@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        r = client.post(
            "/pilot/minimums/item/add",
            data={
                "section_id": str(sid),
                "label": "Max crosswind",
                "value": "15 kt",
                "tag": "",
                "numeric_value": "",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            section = db.session.get(PersonalMinimumsSection, sid)
            assert len(section.items) == 1
            assert section.items[0].label == "Max crosswind"
            assert section.items[0].value == "15 kt"

    def test_add_item_without_label_rejected(self, app, client):
        uid, tid = _make_user(app, "i2@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "", "value")
        with app.app_context():
            assert db.session.get(PersonalMinimumsSection, sid).items == []

    def test_add_item_missing_section_404s(self, app, client):
        uid, tid = _make_user(app, "i3@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        r = client.post(
            "/pilot/minimums/item/add",
            data={"section_id": "999999", "label": "X", "value": "Y"},
        )
        assert r.status_code == 404

    def test_add_item_no_section_id_404s(self, app, client):
        uid, tid = _make_user(app, "i3b@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        r = client.post("/pilot/minimums/item/add", data={"label": "X", "value": "Y"})
        assert r.status_code == 404

    def test_tag_without_numeric_value_rejected(self, app, client):
        uid, tid = _make_user(app, "i4@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Days since last flight",
            "30 days",
            tag=PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
            numeric="",
        )
        with app.app_context():
            assert db.session.get(PersonalMinimumsSection, sid).items == []

    def test_tag_with_non_numeric_value_rejected(self, app, client):
        uid, tid = _make_user(app, "i4b@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Days since last flight",
            "30 days",
            tag=PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
            numeric="soon",
        )
        with app.app_context():
            assert db.session.get(PersonalMinimumsSection, sid).items == []

    def test_unrecognized_tag_rejected(self, app, client):
        uid, tid = _make_user(app, "i4c@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "X", "Y", tag="not_a_real_tag", numeric="")
        with app.app_context():
            assert db.session.get(PersonalMinimumsSection, sid).items == []

    def test_tag_with_valid_numeric_value_accepted(self, app, client):
        uid, tid = _make_user(app, "i5@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Days since last flight",
            "30 days",
            tag=PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
            numeric="30",
        )
        with app.app_context():
            item = db.session.get(PersonalMinimumsSection, sid).items[0]
            assert item.semantic_tag == PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT
            assert float(item.numeric_value) == 30.0

    def test_edit_item(self, app, client):
        uid, tid = _make_user(app, "i6@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Old", "value")
        with app.app_context():
            item_id = db.session.get(PersonalMinimumsSection, sid).items[0].id
        r = client.post(
            f"/pilot/minimums/item/{item_id}/edit",
            data={"label": "New", "value": "new value", "tag": "", "numeric_value": ""},
        )
        assert r.status_code == 302
        with app.app_context():
            item = db.session.get(PersonalMinimumsItem, item_id)
            assert item.label == "New"
            assert item.value == "new value"

    def test_edit_item_blank_label_rejected(self, app, client):
        uid, tid = _make_user(app, "i7@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Keep", "value")
        with app.app_context():
            item_id = db.session.get(PersonalMinimumsSection, sid).items[0].id
        client.post(
            f"/pilot/minimums/item/{item_id}/edit",
            data={"label": "", "value": "x", "tag": "", "numeric_value": ""},
        )
        with app.app_context():
            assert db.session.get(PersonalMinimumsItem, item_id).label == "Keep"

    def test_delete_item(self, app, client):
        uid, tid = _make_user(app, "i8@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Gone", "value")
        with app.app_context():
            item_id = db.session.get(PersonalMinimumsSection, sid).items[0].id
        r = client.post(f"/pilot/minimums/item/{item_id}/delete")
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(PersonalMinimumsItem, item_id) is None

    def test_move_item_up_and_down(self, app, client):
        uid, tid = _make_user(app, "i9@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "A", "1")
        _add_item(app, client, sid, "B", "2")
        with app.app_context():
            items = db.session.get(PersonalMinimumsSection, sid).items
            id_a, id_b = items[0].id, items[1].id
        client.post(f"/pilot/minimums/item/{id_b}/move-up")
        with app.app_context():
            a = db.session.get(PersonalMinimumsItem, id_a)
            b = db.session.get(PersonalMinimumsItem, id_b)
            assert b.sort_order < a.sort_order
        r = client.post(f"/pilot/minimums/item/{id_b}/move-down")
        assert r.status_code == 302
        with app.app_context():
            a = db.session.get(PersonalMinimumsItem, id_a)
            b = db.session.get(PersonalMinimumsItem, id_b)
            assert a.sort_order < b.sort_order

    def test_edit_item_tag_without_numeric_value_rejected(self, app, client):
        uid, tid = _make_user(app, "i11@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(app, client, sid, "Days since last flight", "30 days")
        with app.app_context():
            item_id = db.session.get(PersonalMinimumsSection, sid).items[0].id
        r = client.post(
            f"/pilot/minimums/item/{item_id}/edit",
            data={
                "label": "Days since last flight",
                "value": "30 days",
                "tag": PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
                "numeric_value": "",
            },
        )
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(PersonalMinimumsItem, item_id).semantic_tag is None

    def test_item_actions_404_on_non_draft(self, app, client):
        uid, tid = _make_user(app, "i10@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        with app.app_context():
            item_id = db.session.get(PersonalMinimumsSection, sid).items[0].id
        client.post("/pilot/minimums/publish")
        r = client.post(f"/pilot/minimums/item/{item_id}/delete")
        assert r.status_code == 404


# ── Lifecycle: publish / revise / delete-draft ────────────────────────────────


class TestLifecycle:
    def test_publish_without_draft_redirects(self, app, client):
        uid, tid = _make_user(app, "l0@ex.com")
        _login(app, client, uid)
        r = client.post("/pilot/minimums/publish", follow_redirects=True)
        assert r.status_code == 200
        r = client.get("/pilot/minimums/publish", follow_redirects=True)
        assert r.status_code == 200

    def test_publish_confirm_shows_stronger_warning_when_flew_today(self, app, client):
        uid, tid = _make_user(app, "l0b@ex.com")
        _add_logbook_entry(app, uid, days_ago=0)
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        r = client.get("/pilot/minimums/publish")
        assert r.status_code == 200
        assert b"day of a flight" in r.data

    def test_publish_requires_at_least_one_section(self, app, client):
        uid, tid = _make_user(app, "l1@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        r = client.post("/pilot/minimums/publish", follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            assert (
                PersonalMinimumsRevision.query.filter_by(user_id=uid).one().status
                == PersonalMinimumsStatus.DRAFT
            )

    def test_publish_confirm_page_renders(self, app, client):
        uid, tid = _make_user(app, "l2@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        r = client.get("/pilot/minimums/publish")
        assert r.status_code == 200
        assert b"never changed on the day of a flight" in r.data

    def test_publish_stamps_date_and_hours(self, app, client):
        uid, tid = _make_user(app, "l3@ex.com")
        _add_logbook_entry(app, uid, days_ago=5)
        _add_logbook_entry(app, uid, days_ago=10)
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        r = client.post("/pilot/minimums/publish")
        assert r.status_code == 302
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(user_id=uid).one()
            assert rev.status == PersonalMinimumsStatus.ACTIVE
            assert rev.published_on == date.today()
            assert float(rev.experience_hours) == 3.0

    def test_publish_with_no_logbook_entries_stamps_zero(self, app, client):
        uid, tid = _make_user(app, "l4@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        client.post("/pilot/minimums/publish")
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(user_id=uid).one()
            assert float(rev.experience_hours) == 0.0

    def test_publish_supersedes_previous_active(self, app, client):
        uid, tid = _make_user(app, "l5@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        client.post("/pilot/minimums/publish")
        with app.app_context():
            rev1_id = (
                PersonalMinimumsRevision.query.filter_by(user_id=uid)
                .filter_by(revision_number=1)
                .one()
                .id
            )
        client.post("/pilot/minimums/revise")
        client.post("/pilot/minimums/publish")
        with app.app_context():
            revisions = (
                PersonalMinimumsRevision.query.filter_by(user_id=uid)
                .order_by(PersonalMinimumsRevision.revision_number)
                .all()
            )
            assert [r.revision_number for r in revisions] == [1, 2]
            assert revisions[0].status == PersonalMinimumsStatus.SUPERSEDED
            assert revisions[1].status == PersonalMinimumsStatus.ACTIVE
            assert revisions[0].id == rev1_id

    def test_only_one_draft_and_one_active_at_a_time(self, app, client):
        uid, tid = _make_user(app, "l6@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        client.post("/pilot/minimums/publish")
        client.post("/pilot/minimums/revise")
        with app.app_context():
            statuses = [
                r.status
                for r in PersonalMinimumsRevision.query.filter_by(user_id=uid).all()
            ]
            assert statuses.count(PersonalMinimumsStatus.DRAFT) == 1
            assert statuses.count(PersonalMinimumsStatus.ACTIVE) == 1

    def test_revise_copies_sections_and_items(self, app, client):
        uid, tid = _make_user(app, "l7@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "15 kt")
        client.post("/pilot/minimums/publish")
        client.post("/pilot/minimums/revise")
        with app.app_context():
            draft = _draft(app, uid)
            assert len(draft.sections) == 1
            assert draft.sections[0].title == "Section"
            assert draft.sections[0].items[0].label == "Item"
            assert draft.sections[0].items[0].value == "15 kt"

    def test_revise_without_active_rejected(self, app, client):
        uid, tid = _make_user(app, "l8@ex.com")
        _login(app, client, uid)
        r = client.post("/pilot/minimums/revise", follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            assert PersonalMinimumsRevision.query.filter_by(user_id=uid).count() == 0

    def test_revise_refused_when_draft_already_exists(self, app, client):
        uid, tid = _make_user(app, "l9@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        client.post("/pilot/minimums/publish")
        client.post("/pilot/minimums/revise")
        r = client.post("/pilot/minimums/revise", follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            drafts = PersonalMinimumsRevision.query.filter_by(
                user_id=uid, status=PersonalMinimumsStatus.DRAFT
            ).count()
            assert drafts == 1

    def test_delete_draft(self, app, client):
        uid, tid = _make_user(app, "l10@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        r = client.post("/pilot/minimums/delete-draft")
        assert r.status_code == 302
        with app.app_context():
            assert PersonalMinimumsRevision.query.filter_by(user_id=uid).count() == 0

    def test_delete_draft_without_draft_404s(self, app, client):
        uid, tid = _make_user(app, "l11@ex.com")
        _login(app, client, uid)
        r = client.post("/pilot/minimums/delete-draft")
        assert r.status_code == 404

    def test_active_revision_cannot_be_deleted(self, app, client):
        """There is no delete route for active/superseded revisions —
        delete-draft only ever touches the draft."""
        uid, tid = _make_user(app, "l12@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        client.post("/pilot/minimums/publish")
        r = client.post("/pilot/minimums/delete-draft")
        assert r.status_code == 404
        with app.app_context():
            assert (
                PersonalMinimumsRevision.query.filter_by(user_id=uid).one().status
                == PersonalMinimumsStatus.ACTIVE
            )


# ── View / history / privacy ───────────────────────────────────────────────────


class TestViewAndHistory:
    def test_active_revision_shows_revise_button(self, app, client):
        uid, tid = _make_user(app, "v1@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        client.post("/pilot/minimums/publish")
        r = client.get("/pilot/minimums")
        assert r.status_code == 200
        assert b"Revise" in r.data

    def test_history_lists_all_revisions(self, app, client):
        uid, tid = _make_user(app, "v2@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        client.post("/pilot/minimums/publish")
        client.post("/pilot/minimums/revise")
        r = client.get("/pilot/minimums/history")
        assert r.status_code == 200
        assert b"Current" in r.data
        assert b"Draft" in r.data

    def test_view_superseded_revision_is_read_only(self, app, client):
        uid, tid = _make_user(app, "v3@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        client.post("/pilot/minimums/publish")
        with app.app_context():
            rev1_id = (
                PersonalMinimumsRevision.query.filter_by(user_id=uid)
                .filter_by(revision_number=1)
                .one()
                .id
            )
        client.post("/pilot/minimums/revise")
        with app.app_context():
            item_id = _draft(app, uid).sections[0].items[0].id
        client.post(
            f"/pilot/minimums/item/{item_id}/edit",
            data={"label": "Item", "value": "changed", "tag": "", "numeric_value": ""},
        )
        client.post("/pilot/minimums/publish")
        r = client.get(f"/pilot/minimums/revision/{rev1_id}")
        assert r.status_code == 200
        assert b"read-only revision" in r.data
        assert b"Revise" not in r.data

    def test_view_own_draft_shows_continue_editing(self, app, client):
        uid, tid = _make_user(app, "v4@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        with app.app_context():
            draft_id = _draft(app, uid).id
        r = client.get(f"/pilot/minimums/revision/{draft_id}")
        assert r.status_code == 200
        assert b"Continue editing" in r.data

    def test_view_nonexistent_revision_404s(self, app, client):
        uid, tid = _make_user(app, "v5@ex.com")
        _login(app, client, uid)
        r = client.get("/pilot/minimums/revision/999999")
        assert r.status_code == 404

    def test_cross_user_revision_isolation(self, app, client):
        uid, tid = _make_user(app, "v6@ex.com")
        other_uid, _ = _make_user(app, "v7@ex.com")
        _login(app, client, other_uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        with app.app_context():
            other_rev_id = _draft(app, other_uid).id
        _login(app, client, uid)
        r = client.get(f"/pilot/minimums/revision/{other_rev_id}")
        assert r.status_code == 404

    def test_cross_user_cannot_mutate_sections(self, app, client):
        uid, tid = _make_user(app, "v8@ex.com")
        other_uid, _ = _make_user(app, "v9@ex.com")
        _login(app, client, other_uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, other_uid, "Owned by other")
        _login(app, client, uid)
        r = client.post(f"/pilot/minimums/section/{sid}/edit", data={"title": "Hack"})
        assert r.status_code == 404
        r = client.post(f"/pilot/minimums/section/{sid}/delete")
        assert r.status_code == 404

    def test_cross_user_cannot_mutate_items(self, app, client):
        uid, tid = _make_user(app, "v10@ex.com")
        other_uid, _ = _make_user(app, "v11@ex.com")
        _login(app, client, other_uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, other_uid, "Section")
        _add_item(app, client, sid, "Item", "value")
        with app.app_context():
            item_id = db.session.get(PersonalMinimumsSection, sid).items[0].id
        _login(app, client, uid)
        r = client.post(
            f"/pilot/minimums/item/{item_id}/edit",
            data={"label": "Hack", "value": "x", "tag": "", "numeric_value": ""},
        )
        assert r.status_code == 404
        r = client.post(f"/pilot/minimums/item/{item_id}/delete")
        assert r.status_code == 404


# ── Permissions ──────────────────────────────────────────────────────────────────


class TestPermissions:
    def test_viewer_role_blocked(self, app, client):
        uid, tid = _make_user(app, "p25@ex.com", role=Role.VIEWER)
        _login(app, client, uid)
        r = client.get("/pilot/minimums")
        assert r.status_code == 403

    def test_login_required(self, client):
        r = client.get("/pilot/minimums")
        assert r.status_code in (302, 401, 403)


# ── Recency nudges ────────────────────────────────────────────────────────────────


class TestRecencyBreaches:
    def test_no_active_revision_returns_empty_via_route(self, app, client):
        uid, tid = _make_user(app, "r1@ex.com")
        _login(app, client, uid)
        r = client.get("/pilot/logbook")
        assert r.status_code == 200
        assert b"Personal minimums" in r.data  # nav link only, no breach banner
        assert b"recency reminder" not in r.data

    def test_untagged_items_never_breach(self, app, client):
        uid, tid = _make_user(app, "r2@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Untagged", "value")
        client.post("/pilot/minimums/publish")
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(
                user_id=uid, status=PersonalMinimumsStatus.ACTIVE
            ).one()
            assert recency_breaches(rev, uid) == []

    def test_no_matching_flight_is_a_breach(self, app, client):
        uid, tid = _make_user(app, "r3@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Days since last flight",
            "30 days",
            tag=PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
            numeric="30",
        )
        client.post("/pilot/minimums/publish")
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(
                user_id=uid, status=PersonalMinimumsStatus.ACTIVE
            ).one()
            breaches = recency_breaches(rev, uid)
            assert len(breaches) == 1
            assert breaches[0]["days_since"] is None
            assert breaches[0]["threshold"] == 30

    def test_within_threshold_is_not_a_breach(self, app, client):
        uid, tid = _make_user(app, "r4@ex.com")
        _add_logbook_entry(app, uid, days_ago=5)
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Days since last flight",
            "30 days",
            tag=PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
            numeric="30",
        )
        client.post("/pilot/minimums/publish")
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(
                user_id=uid, status=PersonalMinimumsStatus.ACTIVE
            ).one()
            assert recency_breaches(rev, uid) == []

    def test_exceeding_threshold_is_a_breach(self, app, client):
        uid, tid = _make_user(app, "r5@ex.com")
        _add_logbook_entry(app, uid, days_ago=45)
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Days since last flight",
            "30 days",
            tag=PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
            numeric="30",
        )
        client.post("/pilot/minimums/publish")
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(
                user_id=uid, status=PersonalMinimumsStatus.ACTIVE
            ).one()
            breaches = recency_breaches(rev, uid)
            assert len(breaches) == 1
            assert breaches[0]["days_since"] == 45

    def test_instructor_flight_tag_uses_dual_time_only(self, app, client):
        uid, tid = _make_user(app, "r6@ex.com")
        _add_logbook_entry(app, uid, days_ago=2, dual=0.0)  # solo, no instructor
        _add_logbook_entry(app, uid, days_ago=50, dual=1.0)  # last dual flight
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Days since instructor flight",
            "30 days",
            tag=PersonalMinimumsTag.MAX_DAYS_SINCE_INSTRUCTOR_FLIGHT,
            numeric="30",
        )
        client.post("/pilot/minimums/publish")
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(
                user_id=uid, status=PersonalMinimumsStatus.ACTIVE
            ).one()
            breaches = recency_breaches(rev, uid)
            assert len(breaches) == 1
            assert breaches[0]["days_since"] == 50

    def test_banner_shown_on_logbook_when_breached(self, app, client):
        uid, tid = _make_user(app, "r7@ex.com")
        _add_logbook_entry(app, uid, days_ago=45)
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Days since last flight",
            "30 days",
            tag=PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
            numeric="30",
        )
        client.post("/pilot/minimums/publish")
        r = client.get("/pilot/logbook")
        assert r.status_code == 200
        assert b"recency reminder" in r.data

    def test_manoeuvres_and_fuel_tags_not_auto_checked(self, app, client):
        uid, tid = _make_user(app, "r8@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Manoeuvres practice",
            "6 months",
            tag=PersonalMinimumsTag.MANOEUVRES_PRACTICE_INTERVAL_MONTHS,
            numeric="6",
        )
        client.post("/pilot/minimums/publish")
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(
                user_id=uid, status=PersonalMinimumsStatus.ACTIVE
            ).one()
            assert recency_breaches(rev, uid) == []

    def test_tagged_item_without_numeric_value_skipped(self, app, client):
        """A tag with no numeric_value can't happen through the normal
        add/edit routes (validated together), but the model layer allows
        it — guard against direct DB manipulation."""
        uid, tid = _make_user(app, "r9@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(app, client, sid, "Days since last flight", "30 days")
        client.post("/pilot/minimums/publish")
        with app.app_context():
            rev = PersonalMinimumsRevision.query.filter_by(
                user_id=uid, status=PersonalMinimumsStatus.ACTIVE
            ).one()
            rev.sections[0].items[
                0
            ].semantic_tag = PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT
            db.session.commit()
            assert recency_breaches(rev, uid) == []


# ── Notification check (daily) ────────────────────────────────────────────────────


class TestPersonalMinimumsNotification:
    def test_no_breach_no_dispatch(self, app, client):
        from services.notification_service import _check_personal_minimums_recency

        uid, tid = _make_user(app, "n1@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Section")
        _add_item(app, client, sid, "Untagged", "value")
        client.post("/pilot/minimums/publish")

        sent = []
        import services.notification_service as ns

        original = ns._dispatch_in_context

        def _capture(notification_type, tenant_id, ctx, target_user_ids=None):
            sent.append(notification_type)
            return original(notification_type, tenant_id, ctx, target_user_ids)

        ns._dispatch_in_context = _capture
        try:
            with app.app_context():
                _check_personal_minimums_recency(app)
        finally:
            ns._dispatch_in_context = original
        assert sent == []

    def test_breach_dispatches_once_per_pilot(self, app, client):
        from services.notification_service import _check_personal_minimums_recency

        uid, tid = _make_user(app, "n2@ex.com")
        _add_logbook_entry(app, uid, days_ago=45)
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Days since last flight",
            "30 days",
            tag=PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
            numeric="30",
        )
        client.post("/pilot/minimums/publish")

        sent = []
        import services.notification_service as ns

        original = ns._dispatch_in_context

        def _capture(notification_type, tenant_id, ctx, target_user_ids=None):
            sent.append((notification_type, target_user_ids))
            return original(notification_type, tenant_id, ctx, target_user_ids)

        ns._dispatch_in_context = _capture
        try:
            with app.app_context():
                _check_personal_minimums_recency(app)
        finally:
            ns._dispatch_in_context = original

        from models import NotificationType

        matching = [
            c for c in sent if c[0] == NotificationType.PERSONAL_MINIMUMS_RECENCY
        ]
        assert len(matching) == 1
        assert matching[0][1] == [uid]

    def test_inactive_user_skipped(self, app, client):
        from services.notification_service import _check_personal_minimums_recency

        uid, tid = _make_user(app, "n3@ex.com")
        _add_logbook_entry(app, uid, days_ago=45)
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Days since last flight",
            "30 days",
            tag=PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
            numeric="30",
        )
        client.post("/pilot/minimums/publish")
        with app.app_context():
            user = db.session.get(User, uid)
            user.is_active = False
            db.session.commit()

        sent = []
        import services.notification_service as ns

        original = ns._dispatch_in_context

        def _capture(notification_type, tenant_id, ctx, target_user_ids=None):
            sent.append(notification_type)
            return original(notification_type, tenant_id, ctx, target_user_ids)

        ns._dispatch_in_context = _capture
        try:
            with app.app_context():
                _check_personal_minimums_recency(app)
        finally:
            ns._dispatch_in_context = original
        assert sent == []

    def test_no_active_revisions_at_all_is_a_noop(self, app):
        from services.notification_service import _check_personal_minimums_recency

        with app.app_context():
            _check_personal_minimums_recency(app)  # must not raise

    def test_user_without_tenant_membership_skipped(self, app, client):
        """An active revision whose user has no TenantUser row (orphaned
        membership) must not raise — just skipped."""
        from services.notification_service import _check_personal_minimums_recency

        uid, tid = _make_user(app, "n4@ex.com")
        _add_logbook_entry(app, uid, days_ago=45)
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Recency")
        _add_item(
            app,
            client,
            sid,
            "Days since last flight",
            "30 days",
            tag=PersonalMinimumsTag.MAX_DAYS_SINCE_LAST_FLIGHT,
            numeric="30",
        )
        client.post("/pilot/minimums/publish")
        with app.app_context():
            TenantUser.query.filter_by(user_id=uid).delete()
            db.session.commit()
        with app.app_context():
            _check_personal_minimums_recency(app)  # must not raise


# ── Print view / flight-form surfacing ─────────────────────────────────────────────


class TestPrintAndFlightFormSurfacing:
    def test_print_without_active_redirects(self, app, client):
        uid, tid = _make_user(app, "pf1@ex.com")
        _login(app, client, uid)
        r = client.post("/pilot/minimums/create", data={"starter": "blank"})
        r = client.get("/pilot/minimums/print")
        assert r.status_code == 302

    def test_print_renders_active_revision(self, app, client):
        uid, tid = _make_user(app, "pf2@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Weather limits")
        _add_item(app, client, sid, "Max crosswind", "15 kt")
        client.post("/pilot/minimums/publish")
        r = client.get("/pilot/minimums/print")
        assert r.status_code == 200
        assert b"Weather limits" in r.data
        assert b"Max crosswind" in r.data

    def test_banner_hidden_when_no_minimums(self, app, client):
        uid, tid = _make_user(app, "pf3@ex.com")
        _login(app, client, uid)
        r = client.get("/flights/new")
        assert r.status_code == 200
        assert b"Your personal minimums" not in r.data

    def test_banner_shown_when_minimums_exist(self, app, client):
        uid, tid = _make_user(app, "pf4@ex.com")
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Weather limits")
        _add_item(app, client, sid, "Max crosswind", "15 kt")
        client.post("/pilot/minimums/publish")
        r = client.get("/flights/new")
        assert r.status_code == 200
        assert b"Your personal minimums" in r.data
        assert b"Review them" in r.data

    def test_banner_hidden_when_editing_existing_flight(self, app, client):
        from models import Aircraft, FlightEntry

        uid, tid = _make_user(app, "pf5@ex.com", role=Role.OWNER)
        _login(app, client, uid)
        client.post("/pilot/minimums/create", data={"starter": "blank"})
        sid = _add_section(app, client, uid, "Weather limits")
        _add_item(app, client, sid, "Max crosswind", "15 kt")
        client.post("/pilot/minimums/publish")
        with app.app_context():
            ac = Aircraft(
                tenant_id=tid, registration="OO-PF5", make="Cessna", model="172"
            )
            db.session.add(ac)
            db.session.flush()
            fe = FlightEntry(
                aircraft_id=ac.id,
                date=date.today(),
                departure_icao="EBOS",
                arrival_icao="EBBR",
            )
            db.session.add(fe)
            db.session.commit()
            flight_id = fe.id
        r = client.get(f"/flights/{flight_id}/edit")
        assert r.status_code == 200
        assert b"Your personal minimums" not in r.data
