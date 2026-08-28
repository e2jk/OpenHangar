"""Tests for Phase 40: AMP document export route (maintenance.export_amp)."""

import re
from datetime import date

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AmpCategory,
    AmpDeclaration,
    Component,
    ComponentType,
    MaintenanceTrigger,
    Role,
    Tenant,
    TenantUser,
    TriggerType,
    User,
    db,
)


def _create_user_and_tenant(app, email="pilot@example.com", role=Role.ADMIN):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email,
            password_hash=_pw_hash.hash("testpassword123"),
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(TenantUser(user_id=user.id, tenant_id=tenant.id, role=role))
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="pilot@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid
    return uid


def _add_aircraft(app, tenant_id, registration="OO-PNH"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Robin", model="DR400"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _add_declaration(app, aircraft_id, **kwargs):
    with app.app_context():
        decl = AmpDeclaration(aircraft_id=aircraft_id, **kwargs)
        db.session.add(decl)
        db.session.commit()
        return aircraft_id


def _add_trigger(app, aircraft_id, **kwargs):
    kwargs.setdefault("trigger_type", TriggerType.CALENDAR)
    kwargs.setdefault("due_date", date.today())
    with app.app_context():
        t = MaintenanceTrigger(aircraft_id=aircraft_id, **kwargs)
        db.session.add(t)
        db.session.commit()
        return t.id


class TestAuthAndGating:
    def test_redirects_when_not_logged_in(self, client):
        r = client.get("/aircraft/1/maintenance/amp/export")
        assert r.status_code == 302

    def test_403_for_viewer(self, app, client):
        _uid, tid = _create_user_and_tenant(app, role=Role.VIEWER)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert r.status_code == 403

    def test_no_declaration_redirects_to_edit_form(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(
            f"/aircraft/{acid}/maintenance/amp/export", follow_redirects=False
        )
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/amp/edit")

    def test_no_declaration_flashes_prompt(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(
            f"/aircraft/{acid}/maintenance/amp/export", follow_redirects=True
        )
        assert b"Fill in the AMP declaration profile" in r.data


class TestExportContent:
    def test_renders_blocks_1_to_3(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, registration="OO-LKN")
        _add_declaration(
            app,
            acid,
            dah_ica_airframe_ref="DOC 1001586 GB",
            certifying_party_name="Zorg Piloot",
        )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert r.status_code == 200
        assert b"OO-LKN" in r.data
        assert b"DOC 1001586 GB" in r.data
        assert b"Zorg Piloot" in r.data

    def test_block_4_yes_for_category_with_triggers(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(
            app,
            acid,
            name="AD check",
            category=AmpCategory.REPETITIVE_ADS,
        )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        html = r.data.decode()
        idx = html.index(AmpCategory.REPETITIVE_ADS)
        row = html[idx : html.index("</tr>", idx)]
        yes_cell = re.search(r'class="amp-export-yesno">([^<]*)</td>', row).group(1)
        assert yes_cell == "☑"

    def test_block_4_no_for_category_without_triggers(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        html = r.data.decode()
        idx = html.index(AmpCategory.REPETITIVE_ADS)
        row = html[idx : html.index("</tr>", idx)]
        yes_cell = re.search(r'class="amp-export-yesno">([^<]*)</td>', row).group(1)
        assert yes_cell == ""

    def test_block_5_yes_when_alternative_task_exists(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(app, acid, name="Deviation", is_alternative_to_ica=True)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert b"see Appendix C" in r.data

    def test_appendix_b_groups_by_category(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(
            app,
            acid,
            name="AD compliance check",
            category=AmpCategory.REPETITIVE_ADS,
            reference="AD 2023-0048",
            interval_hours=100.0,
            interval_days=360,
        )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert r.status_code == 200
        assert b"Appendix B" in r.data
        assert b"AD compliance check" in r.data
        assert b"AD 2023-0048" in r.data
        assert b"100FH / 12MO" in r.data

    def test_appendix_b_absent_when_no_categorised_triggers(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(app, acid, name="Routine inspection")  # no category
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert "Appendix B —" not in r.data.decode()

    def test_appendix_c_present_with_alternative_task(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(
            app,
            acid,
            name="Extended TBO",
            is_alternative_to_ica=True,
            alternative_task_notes="Recommended 2000h, extended to 2200h",
        )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert b"Appendix C" in r.data
        assert b"Extended TBO" in r.data
        assert b"Recommended 2000h, extended to 2200h" in r.data

    def test_appendix_c_absent_without_alternative_tasks(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(app, acid, name="Routine inspection")
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert "Appendix C —" not in r.data.decode()

    def test_appendix_d_present_with_notes(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid, appendix_d_notes="See source workbook for detail")
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert b"Appendix D" in r.data
        assert b"See source workbook for detail" in r.data

    def test_appendix_d_present_with_revision_only(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(
            app,
            acid,
            revision_number="0",
            revision_content="Initial release",
            revision_date=date(2026, 8, 25),
        )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert b"Appendix D" in r.data
        assert b"Initial release" in r.data
        assert b"2026-08-25" in r.data

    def test_appendix_d_absent_without_notes_or_revision(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert "Appendix D —" not in r.data.decode()

    def test_engine_and_propeller_references_rendered(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            db.session.add(
                Component(
                    aircraft_id=acid,
                    type=ComponentType.ENGINE,
                    make="Continental",
                    model="CD-155",
                )
            )
            db.session.add(
                Component(
                    aircraft_id=acid,
                    type=ComponentType.PROPELLER,
                    make="MT-Propeller",
                    model="MTV-6-A",
                )
            )
            db.session.commit()
        _add_declaration(
            app,
            acid,
            dah_ica_engine_ref="OM-02-02",
            dah_ica_propeller_ref="E-124",
        )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert b"Continental" in r.data
        assert b"CD-155" in r.data
        assert b"OM-02-02" in r.data
        assert b"MT-Propeller" in r.data
        assert b"E-124" in r.data

    def test_404_for_other_tenant(self, app, client):
        _create_user_and_tenant(app)
        _, other_tid = _create_user_and_tenant(app, email="other@example.com")
        other_acid = _add_aircraft(app, other_tid, registration="OO-OTH")
        _login(app, client)
        r = client.get(f"/aircraft/{other_acid}/maintenance/amp/export")
        assert r.status_code == 404


class TestPendingReviewSection:
    """A needs_review trigger has no interval yet and may have no category
    either — appendix_b_groups would silently drop an uncategorised one, so
    the dedicated pending section (and the top banner) must cover it
    regardless of category."""

    def test_no_banner_or_section_without_pending_triggers(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(app, acid, name="Routine inspection")
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert b"pending shop input" not in r.data
        assert "Pending — needs shop input".encode() not in r.data

    def test_banner_shows_singular_count(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(app, acid, name="Undecided item", due_date=None, needs_review=True)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert b"one item is still pending shop input" in r.data

    def test_banner_shows_plural_count(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(app, acid, name="Item 1", due_date=None, needs_review=True)
        _add_trigger(app, acid, name="Item 2", due_date=None, needs_review=True)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert b"2 items are still pending shop input" in r.data

    def test_uncategorised_pending_trigger_absent_from_appendix_b(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(
            app,
            acid,
            name="Uncategorised pending item",
            due_date=None,
            needs_review=True,
        )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        html = r.data.decode()
        assert "Appendix B —" not in html

    def test_uncategorised_pending_trigger_appears_in_pending_section(
        self, app, client
    ):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(
            app,
            acid,
            name="Uncategorised pending item",
            due_date=None,
            needs_review=True,
            notes="Waiting on SB TM TAE 125-0001 R24 clarification",
        )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert b"Pending" in r.data
        assert b"Uncategorised pending item" in r.data
        assert b"Waiting on SB TM TAE 125-0001 R24 clarification" in r.data

    def test_categorised_pending_trigger_shows_marker_not_blank_in_appendix_b(
        self, app, client
    ):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(
            app,
            acid,
            name="Categorised pending item",
            due_date=None,
            needs_review=True,
            category=AmpCategory.REPETITIVE_ADS,
        )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        html = r.data.decode()
        idx = html.index("Categorised pending item")
        row = html[idx : html.index("</tr>", idx)]
        assert "Pending shop input" in row
        # also listed in the dedicated pending section, not just Appendix B
        assert html.count("Categorised pending item") == 2

    def test_non_pending_trigger_not_in_pending_section(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _add_declaration(app, acid)
        _add_trigger(
            app,
            acid,
            name="Undecided item",
            due_date=None,
            needs_review=True,
        )
        _add_trigger(
            app,
            acid,
            name="Resolved item",
            category=AmpCategory.REPETITIVE_ADS,
        )
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        html = r.data.decode()
        # present in the document at all (Appendix B), just not repeated here
        assert "Resolved item" in html
        # The banner text above also quotes the section title, so anchor on
        # the actual <h2> heading, not the first (banner) occurrence.
        pending_idx = html.index(">Pending — needs shop input</h2>")
        pending_section = html[pending_idx:]
        assert "Undecided item" in pending_section
        assert "Resolved item" not in pending_section


class TestImportExportRoundTrip:
    """End-to-end: a spreadsheet imported through the real upload/commit
    routes exports back out with the same registration and categorised task
    counts — the round-trip property the whole import/export split is
    designed around (see docs/maintenance_import.md)."""

    def test_imported_rows_appear_correctly_categorised_on_export(self, app, client):
        import io

        import openpyxl

        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid, registration="OO-LKN")
        _add_declaration(app, acid, certifying_party_name="Zorg Piloot")
        _login(app, client)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Category", "Task description", "Reference", "Action", "Interval"])
        ws.append(
            [
                "Maintenance due to repetitive ADs",
                "AD compliance check",
                "AD 2023-0048",
                "INSPECTION",
                "100FH / 12MO",
            ]
        )
        ws.append(
            [
                "Maintenance recommendations (TBO via SB/SL, non-mandatory)",
                "Engine TBO recommendation",
                "SL TMG 000-1004",
                "TBO",
                "2000FH",
            ]
        )
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        client.post(
            f"/aircraft/{acid}/maintenance/import",
            data={"amp_file": (buf, "sched.xlsx")},
            content_type="multipart/form-data",
        )
        r = client.post(f"/aircraft/{acid}/maintenance/import/commit", data={})
        assert r.status_code == 302

        r = client.get(f"/aircraft/{acid}/maintenance/amp/export")
        assert r.status_code == 200
        html = r.data.decode()

        assert "OO-LKN" in html
        assert "Zorg Piloot" in html
        assert "AD compliance check" in html
        assert "AD 2023-0048" in html
        assert "100FH / 12MO" in html
        assert "Engine TBO recommendation" in html
        assert "2000FH" in html

        # Both categories are grouped under their own Appendix B section.
        idx_ads = html.index(AmpCategory.REPETITIVE_ADS)
        row_ads = html[idx_ads : html.index("</tr>", idx_ads)]
        assert (
            re.search(r'class="amp-export-yesno">([^<]*)</td>', row_ads).group(1) == "☑"
        )

        idx_tbo = html.index(AmpCategory.TBO_RECOMMENDATIONS)
        row_tbo = html[idx_tbo : html.index("</tr>", idx_tbo)]
        assert (
            re.search(r'class="amp-export-yesno">([^<]*)</td>', row_tbo).group(1) == "☑"
        )
