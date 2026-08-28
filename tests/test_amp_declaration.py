"""Tests for Phase 40: AmpDeclaration model and its edit form."""

from datetime import date

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AmpBasis,
    AmpCertifyingPartyKind,
    AmpDeclaration,
    AmpDeclarationType,
    AmpRevision,
    Role,
    Tenant,
    TenantUser,
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


class TestAuthGuard:
    def test_redirects_when_not_logged_in(self, client):
        r = client.get("/aircraft/1/amp/edit")
        assert r.status_code == 302

    def test_403_for_viewer_role(self, app, client):
        _uid, tid = _create_user_and_tenant(app, role=Role.VIEWER)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/amp/edit")
        assert r.status_code == 403

    def test_404_for_other_tenant(self, app, client):
        _create_user_and_tenant(app)
        _, other_tid = _create_user_and_tenant(app, email="other@example.com")
        other_acid = _add_aircraft(app, other_tid, registration="OO-OTH")
        _login(app, client)
        r = client.get(f"/aircraft/{other_acid}/amp/edit")
        assert r.status_code == 404


class TestEditForm:
    def test_get_shows_blank_form_when_no_declaration_yet(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/amp/edit")
        assert r.status_code == 200
        assert b"AMP Declaration" in r.data

    def test_get_shows_existing_declaration_values(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            db.session.add(
                AmpDeclaration(
                    aircraft_id=acid,
                    basis=AmpBasis.DAH_ICA,
                    dah_ica_airframe_ref="DOC 1001586 GB",
                    pilot_owner_name="Jane Doe",
                )
            )
            db.session.commit()
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/amp/edit")
        assert b"DOC 1001586 GB" in r.data
        assert b"Jane Doe" in r.data

    def test_post_creates_declaration(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/amp/edit",
            data={
                "owner_name": "Planes4U",
                "owner_address": "Rue Test 1\n5020 Namur",
                "basis": AmpBasis.DAH_ICA,
                "dah_ica_airframe_ref": "DOC 1001586 GB",
                "dah_ica_engine_ref": "OM-02-02",
                "dah_ica_propeller_ref": "",
                "pilot_owner_maintenance": "on",
                "pilot_owner_name": "Emilien",
                "pilot_owner_licence_number": "BE.FCL.123",
                "declaration_type": AmpDeclarationType.OWNER,
                "certifying_party_kind": AmpCertifyingPartyKind.OWNER_LESSEE_OPERATOR,
                "certifying_party_name": "Zorg Piloot",
                "certifying_party_email": "test@example.com",
                "appendix_d_notes": "See workbook",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            decl = db.session.get(AmpDeclaration, acid)
            assert decl is not None
            assert decl.owner_name == "Planes4U"
            assert decl.owner_address == "Rue Test 1\n5020 Namur"
            assert decl.dah_ica_airframe_ref == "DOC 1001586 GB"
            assert decl.pilot_owner_maintenance is True
            assert decl.pilot_owner_name == "Emilien"

    def test_post_updates_existing_declaration(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            db.session.add(
                AmpDeclaration(aircraft_id=acid, pilot_owner_name="Old Name")
            )
            db.session.commit()
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/amp/edit",
            data={
                "basis": AmpBasis.DAH_ICA,
                "declaration_type": AmpDeclarationType.OWNER,
                "certifying_party_kind": AmpCertifyingPartyKind.OWNER_LESSEE_OPERATOR,
                "pilot_owner_name": "New Name",
            },
        )
        with app.app_context():
            decl = db.session.get(AmpDeclaration, acid)
            assert decl.pilot_owner_name == "New Name"
            # only one row exists (updated, not duplicated)
            assert AmpDeclaration.query.filter_by(aircraft_id=acid).count() == 1

    def test_post_camo_cao_declaration_type(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/amp/edit",
            data={
                "basis": AmpBasis.MIP,
                "mip_details": "Custom MIP tasks",
                "declaration_type": AmpDeclarationType.CAMO_CAO,
                "camo_cao_approval_reference": "BE.CAO.001",
                "certifying_party_kind": AmpCertifyingPartyKind.CAMO_CAO,
            },
        )
        with app.app_context():
            decl = db.session.get(AmpDeclaration, acid)
            assert decl.basis == AmpBasis.MIP
            assert decl.mip_details == "Custom MIP tasks"
            assert decl.declaration_type == AmpDeclarationType.CAMO_CAO
            assert decl.camo_cao_approval_reference == "BE.CAO.001"
            assert decl.certifying_party_kind == AmpCertifyingPartyKind.CAMO_CAO

    def test_post_rejects_invalid_basis(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/amp/edit",
            data={
                "basis": "bogus",
                "declaration_type": AmpDeclarationType.OWNER,
                "certifying_party_kind": AmpCertifyingPartyKind.OWNER_LESSEE_OPERATOR,
            },
        )
        assert r.status_code == 200
        assert b"Invalid programme basis" in r.data
        with app.app_context():
            assert db.session.get(AmpDeclaration, acid) is None

    def test_post_rejects_invalid_declaration_type(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/amp/edit",
            data={
                "basis": AmpBasis.DAH_ICA,
                "declaration_type": "bogus",
                "certifying_party_kind": AmpCertifyingPartyKind.OWNER_LESSEE_OPERATOR,
            },
        )
        assert r.status_code == 200
        assert b"Invalid declaration type" in r.data

    def test_post_rejects_invalid_certifying_party(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/amp/edit",
            data={
                "basis": AmpBasis.DAH_ICA,
                "declaration_type": AmpDeclarationType.OWNER,
                "certifying_party_kind": "bogus",
            },
        )
        assert r.status_code == 200
        assert b"Invalid certifying party" in r.data

    def test_post_blank_defaults_to_owner_basis_and_kind(self, app, client):
        """A blank/omitted radio value falls back to the model's own
        default, matching the AircraftBookingSettings precedent."""
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/amp/edit", data={})
        assert r.status_code == 302
        with app.app_context():
            decl = db.session.get(AmpDeclaration, acid)
            assert decl.basis == AmpBasis.DAH_ICA
            assert decl.declaration_type == AmpDeclarationType.OWNER
            assert decl.certifying_party_kind == (
                AmpCertifyingPartyKind.OWNER_LESSEE_OPERATOR
            )

    def test_deleting_aircraft_cascades_declaration(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            db.session.add(AmpDeclaration(aircraft_id=acid))
            db.session.commit()
            ac = db.session.get(Aircraft, acid)
            db.session.delete(ac)
            db.session.commit()
            assert db.session.get(AmpDeclaration, acid) is None


class TestRevisionHistory:
    """Block 10 (AmpRevision) — a one-to-many list, managed via its own
    add/delete routes rather than as fields on the declaration form."""

    def test_get_shows_no_revisions_message(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.get(f"/aircraft/{acid}/amp/edit")
        assert b"No revisions recorded yet" in r.data

    def test_add_revision(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/amp/revisions/add",
            data={
                "revision_number": "R00",
                "revision_content": "Initial release",
                "revision_date": "2026-08-25",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with app.app_context():
            revs = AmpRevision.query.filter_by(aircraft_id=acid).all()
            assert len(revs) == 1
            assert revs[0].revision_number == "R00"
            assert revs[0].revision_content == "Initial release"
            assert revs[0].revision_date == date(2026, 8, 25)

    def test_add_revision_without_declaration_yet(self, app, client):
        # AmpRevision is keyed on aircraft_id, not the declaration — adding
        # a revision before the declaration profile exists must still work.
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/amp/revisions/add",
            data={"revision_number": "R00"},
        )
        assert r.status_code == 302
        with app.app_context():
            assert AmpRevision.query.filter_by(aircraft_id=acid).count() == 1

    def test_add_revision_requires_number(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/amp/revisions/add",
            data={"revision_content": "Missing a number"},
            follow_redirects=True,
        )
        assert b"Revision number is required" in r.data
        with app.app_context():
            assert AmpRevision.query.filter_by(aircraft_id=acid).count() == 0

    def test_add_revision_rejects_invalid_date(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/amp/revisions/add",
            data={"revision_number": "R00", "revision_date": "not-a-date"},
            follow_redirects=True,
        )
        assert b"Revision date must be" in r.data

    def test_add_revision_403_for_viewer(self, app, client):
        _uid, tid = _create_user_and_tenant(app, role=Role.VIEWER)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/amp/revisions/add", data={"revision_number": "R00"}
        )
        assert r.status_code == 403

    def test_add_revision_404_for_other_tenant(self, app, client):
        _create_user_and_tenant(app)
        _, other_tid = _create_user_and_tenant(app, email="other@example.com")
        other_acid = _add_aircraft(app, other_tid, registration="OO-OTH")
        _login(app, client)
        r = client.post(
            f"/aircraft/{other_acid}/amp/revisions/add",
            data={"revision_number": "R00"},
        )
        assert r.status_code == 404

    def test_delete_revision(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            rev = AmpRevision(aircraft_id=acid, revision_number="R00")
            db.session.add(rev)
            db.session.commit()
            rev_id = rev.id
        _login(app, client)
        r = client.post(
            f"/aircraft/{acid}/amp/revisions/{rev_id}/delete", follow_redirects=False
        )
        assert r.status_code == 302
        with app.app_context():
            assert db.session.get(AmpRevision, rev_id) is None

    def test_delete_revision_404_for_wrong_aircraft(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        other_acid = _add_aircraft(app, tid, registration="OO-OTH")
        with app.app_context():
            rev = AmpRevision(aircraft_id=acid, revision_number="R00")
            db.session.add(rev)
            db.session.commit()
            rev_id = rev.id
        _login(app, client)
        r = client.post(f"/aircraft/{other_acid}/amp/revisions/{rev_id}/delete")
        assert r.status_code == 404
        with app.app_context():
            assert db.session.get(AmpRevision, rev_id) is not None

    def test_delete_revision_404_for_missing_revision(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        r = client.post(f"/aircraft/{acid}/amp/revisions/999999/delete")
        assert r.status_code == 404

    def test_deleting_aircraft_cascades_revisions(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        with app.app_context():
            rev = AmpRevision(aircraft_id=acid, revision_number="R00")
            db.session.add(rev)
            db.session.commit()
            rev_id = rev.id
            ac = db.session.get(Aircraft, acid)
            db.session.delete(ac)
            db.session.commit()
            assert db.session.get(AmpRevision, rev_id) is None
