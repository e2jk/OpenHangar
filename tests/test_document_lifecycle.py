"""Certificate lifecycle (services.document_lifecycle), the same for an
aircraft's insurance/ARC and a pilot's medical/licence:

- the one in force: started (valid_from unset or reached), valid the
  longest; others started are superseded, a renewal not started yet is
  upcoming and only supersedes the current one on its start date;
- coverage through renewals on file: no "expires soon" while the next one
  is already there (dashboard, status, medical reminder, document badges);
- the synced dates (aircraft insurance/ARC expiry, profile medical expiry);
- Replace from the documents lists, the old one kept on file.
"""

import io
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    DocType,
    Document,
    NotificationType,
    PilotProfile,
    db,
)
from services import document_lifecycle as lc  # pyright: ignore[reportMissingImports]

from tests.test_pilot_documents import (  # pyright: ignore[reportMissingImports]
    _add_aircraft,
    _create_user_and_tenant,
    _login,
)

T = date.today()


def _doc(owner_kw, doc_type, until=None, start=None, uploaded_days_ago=0):
    doc = Document(
        filename=f"f{datetime.now(UTC).timestamp()}.pdf",
        original_filename="cert.pdf",
        mime_type="application/pdf",
        doc_type=doc_type,
        valid_from=start,
        valid_until=until,
        uploaded_at=datetime.now(UTC) - timedelta(days=uploaded_days_ago),
        **owner_kw,
    )
    db.session.add(doc)
    db.session.flush()
    return doc


def _pdf(name="cert.pdf"):
    return (io.BytesIO(b"%PDF-1.4"), name, "application/pdf")


class TestRules:
    def test_renewal_on_file_takes_over_on_its_start_date(self, app):
        _uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        with app.app_context():
            kw = {"aircraft_id": ac_id}
            owner = lc.Owner(aircraft_id=ac_id)
            current = _doc(kw, DocType.INSURANCE_CERT, until=T + timedelta(days=10))
            renewal = _doc(
                kw,
                DocType.INSURANCE_CERT,
                start=T + timedelta(days=11),
                until=T + timedelta(days=100),
            )
            lc.refresh(owner, DocType.INSURANCE_CERT)
            db.session.commit()
            # Until the renewal starts: the current one stays in force…
            assert lc.states(owner, DocType.INSURANCE_CERT) == {
                current.id: lc.ACTIVE,
                renewal.id: lc.UPCOMING,
            }
            assert current.superseded_by_id is None
            assert renewal.superseded_by_id is None
            assert lc.upcoming(owner, DocType.INSURANCE_CERT).id == renewal.id
            ac = db.session.get(Aircraft, ac_id)
            assert ac.insurance_expiry == T + timedelta(days=10)
            # …but no "expiring soon": the renewal covers the gap.
            assert ac.insurance_covered_until == T + timedelta(days=100)
            assert ac.insurance_status == "ok"
            assert not ac.is_grounded

            # On its start date the renewal is in force, the old one superseded.
            lc.refresh_all(as_of=T + timedelta(days=11))
            db.session.commit()
            assert current.superseded_by_id == renewal.id
            assert renewal.superseded_by_id is None
            assert ac.insurance_expiry == T + timedelta(days=100)

    def test_a_gap_before_the_renewal_still_warns(self, app):
        _uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        with app.app_context():
            kw = {"aircraft_id": ac_id}
            _doc(kw, DocType.ARC, until=T + timedelta(days=10))
            _doc(
                kw,
                DocType.ARC,
                start=T + timedelta(days=20),
                until=T + timedelta(days=400),
            )
            lc.refresh(lc.Owner(aircraft_id=ac_id), DocType.ARC)
            db.session.commit()
            ac = db.session.get(Aircraft, ac_id)
            assert ac.arc_covered_until == T + timedelta(days=10)
            assert ac.arc_status == "expiring_soon"

    def test_longest_valid_wins_and_older_ones_uploaded_later_stay_superseded(
        self, app
    ):
        uid, _tid = _create_user_and_tenant(app)
        with app.app_context():
            kw = {"pilot_user_id": uid}
            owner = lc.Owner(pilot_user_id=uid)
            new = _doc(kw, DocType.MEDICAL, start=T, until=T + timedelta(days=380))
            # Uploaded afterwards, for the record: an older certificate.
            old = _doc(
                kw,
                DocType.MEDICAL,
                start=T - timedelta(days=340),
                until=T + timedelta(days=20),
            )
            lc.refresh(owner, DocType.MEDICAL)
            db.session.commit()
            assert lc.active(owner, DocType.MEDICAL).id == new.id
            assert old.superseded_by_id == new.id
            profile = PilotProfile.query.filter_by(user_id=uid).one()
            assert profile.medical_expiry == T + timedelta(days=380)

    def test_open_ended_licences_newest_upload_wins(self, app):
        uid, _tid = _create_user_and_tenant(app)
        with app.app_context():
            kw = {"pilot_user_id": uid}
            older = _doc(kw, DocType.LICENSE, uploaded_days_ago=30)
            newer = _doc(kw, DocType.LICENSE)
            owner = lc.Owner(pilot_user_id=uid)
            lc.refresh(owner, DocType.LICENSE)
            db.session.commit()
            assert older.superseded_by_id == newer.id
            assert lc.coverage_until(owner, DocType.LICENSE) is None
            # A licence drives no profile date.
            assert PilotProfile.query.filter_by(user_id=uid).first() is None

    def test_no_op_cases(self, app):
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid, insurance_expiry=T + timedelta(days=5))
        with app.app_context():
            # Untyped, or a type without a lifecycle for that owner.
            lc.refresh(lc.Owner(aircraft_id=ac_id), None)
            lc.refresh(lc.Owner(aircraft_id=ac_id), DocType.MEDICAL)
            # A component's certificate doesn't drive the aircraft's date.
            lc.refresh(lc.Owner(aircraft_id=ac_id, component_id=1), DocType.ARC)
            # A deleted aircraft: nothing to sync.
            lc.refresh(lc.Owner(aircraft_id=999999), DocType.ARC)
            # A pilot medical only renewing later: profile date untouched.
            db.session.add(PilotProfile(user_id=uid, medical_expiry=T))
            _doc(
                {"pilot_user_id": uid},
                DocType.MEDICAL,
                start=T + timedelta(days=5),
                until=T + timedelta(days=400),
            )
            lc.refresh(lc.Owner(pilot_user_id=uid), DocType.MEDICAL)
            db.session.commit()
            assert db.session.get(Aircraft, ac_id).insurance_expiry == T + timedelta(
                days=5
            )
            profile = PilotProfile.query.filter_by(user_id=uid).one()
            assert profile.medical_expiry == T
            # …though the reminder already looks through to the renewal.
            assert lc.medical_covered_until(profile) == T
            assert lc.medical_covered_until(object()) is None


class TestPilotMedical:
    def test_upload_old_then_replace_with_new(self, app, client):
        """The scenario this was built for: the current medical uploaded for
        the record, then replaced by the new one — kept, superseded."""
        uid, _tid = _create_user_and_tenant(app)
        _login(app, client)
        old_until = T + timedelta(days=22)
        new_until = T + timedelta(days=387)
        client.post(
            "/pilot/documents/upload",
            data={
                "file": _pdf("medical-2025.pdf"),
                "title": "Medical 2025",
                "doc_type": "medical",
                "valid_from": (T - timedelta(days=343)).isoformat(),
                "valid_until": old_until.isoformat(),
            },
            content_type="multipart/form-data",
        )
        with app.app_context():
            old = Document.query.filter_by(pilot_user_id=uid).one()
            old_id = old.id
            assert PilotProfile.query.filter_by(user_id=uid).one().medical_expiry == (
                old_until
            )
        page = client.get("/pilot/profile").data.decode()
        assert "Document currently active" in page
        assert f"/pilot/documents/upload?replace={old_id}" in page
        # The profile's own field now follows the document, read-only.
        assert "Set from your medical certificate" in page
        assert "Expires " in page  # 22 days left, nothing on file after it

        form = client.get(f"/pilot/documents/upload?replace={old_id}").data.decode()
        assert "Replacing Medical 2025." in form
        assert '<option value="medical" selected>' in form

        client.post(
            "/pilot/documents/upload",
            data={
                "file": _pdf("medical-2026.pdf"),
                "title": "Medical 2026",
                "doc_type": "medical",
                "valid_from": T.isoformat(),
                "valid_until": new_until.isoformat(),
            },
            content_type="multipart/form-data",
        )
        with app.app_context():
            docs = {d.title: d for d in Document.query.filter_by(pilot_user_id=uid)}
            assert docs["Medical 2025"].superseded_by_id == docs["Medical 2026"].id
            assert PilotProfile.query.filter_by(user_id=uid).one().medical_expiry == (
                new_until
            )
            filename = docs["Medical 2025"].filename
        page = client.get("/pilot/profile").data.decode()
        assert page.count("Document superseded") == 1
        assert page.count("Document currently active") == 1
        assert page.count("bi-arrow-repeat") == 1  # Replace: the new one only
        assert filename in page  # the old one still viewable/downloadable
        assert "Expires " not in page

        # Typing a different medical expiry on the profile changes nothing.
        client.post("/pilot/profile", data={"medical_expiry": "2030-01-01"})
        with app.app_context():
            assert PilotProfile.query.filter_by(user_id=uid).one().medical_expiry == (
                new_until
            )

        # Deleting the new one puts the old one back in force.
        with app.app_context():
            new_id = Document.query.filter_by(title="Medical 2026").one().id
        client.post(f"/pilot/documents/{new_id}/delete")
        with app.app_context():
            assert db.session.get(Document, old_id).superseded_by_id is None
            assert PilotProfile.query.filter_by(user_id=uid).one().medical_expiry == (
                old_until
            )

    def test_renewal_starting_later_keeps_the_current_one_without_warnings(
        self, app, client
    ):
        uid, _tid = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            current = _doc(
                {"pilot_user_id": uid}, DocType.MEDICAL, until=T + timedelta(days=22)
            )
            lc.refresh(lc.Owner(pilot_user_id=uid), DocType.MEDICAL)
            db.session.commit()
            current_id = current.id
        client.post(
            "/pilot/documents/upload",
            data={
                "file": _pdf(),
                "doc_type": "medical",
                "valid_from": (T + timedelta(days=23)).isoformat(),
                "valid_until": (T + timedelta(days=387)).isoformat(),
            },
            content_type="multipart/form-data",
        )
        with app.app_context():
            assert db.session.get(Document, current_id).superseded_by_id is None
            profile = PilotProfile.query.filter_by(user_id=uid).one()
            assert profile.medical_expiry == T + timedelta(days=22)
            # The medical status and reminder look through to the renewal.
            from pilots.currency import (  # pyright: ignore[reportMissingImports]
                medical_status,
            )

            assert medical_status(profile)["status"] == "ok"
            with patch("services.notification_service.dispatch") as dispatch:
                from services.notification_service import (  # pyright: ignore[reportMissingImports]
                    _check_medical_and_sep,
                )

                _check_medical_and_sep(app)
                sent = [c.args[0] for c in dispatch.call_args_list]
                assert NotificationType.MEDICAL_EXPIRING not in sent
        page = client.get("/pilot/profile").data.decode()
        assert "Document upcoming" in page
        assert f"Valid from {(T + timedelta(days=23)).isoformat()}" in page
        assert "Expires " not in page

    def test_profile_date_stays_editable_until_a_document_drives_it(self, app, client):
        """A medical document without an expiry date, or a renewal that
        hasn't started, doesn't set the profile's date — so the field
        stays editable rather than locked on a value nothing updates."""
        uid, _tid = _create_user_and_tenant(app)
        _login(app, client)
        with app.app_context():
            _doc({"pilot_user_id": uid}, DocType.MEDICAL)  # no dates
            _doc(
                {"pilot_user_id": uid},
                DocType.MEDICAL,
                start=T + timedelta(days=30),
                until=T + timedelta(days=400),
            )
            db.session.commit()
        assert "Set from your medical certificate" not in (
            client.get("/pilot/profile").data.decode()
        )
        client.post("/pilot/profile", data={"medical_expiry": "2027-01-15"})
        with app.app_context():
            profile = PilotProfile.query.filter_by(user_id=uid).one()
            assert profile.medical_expiry == date(2027, 1, 15)

    def test_upload_validation(self, app, client):
        uid, _tid = _create_user_and_tenant(app)
        other, _ = _create_user_and_tenant(app, email="other@example.com")
        _login(app, client)
        rv = client.post(
            "/pilot/documents/upload",
            data={
                "file": _pdf(),
                "doc_type": "medical",
                "valid_from": "2027-01-02",
                "valid_until": "2027-01-01",
            },
            content_type="multipart/form-data",
        )
        assert b"must not be after" in rv.data
        client.post(
            "/pilot/documents/upload",
            data={"file": _pdf(), "doc_type": "bogus"},
            content_type="multipart/form-data",
        )
        with app.app_context():
            doc = Document.query.filter_by(pilot_user_id=uid).one()
            assert doc.doc_type is None
            # Someone else's document can't be "replaced" from here.
            theirs = _doc({"pilot_user_id": other}, DocType.MEDICAL)
            db.session.commit()
            theirs_id = theirs.id
        form = client.get(f"/pilot/documents/upload?replace={theirs_id}").data
        assert b"Replacing" not in form


class TestAircraftCertificates:
    def test_replace_prefills_and_supersedes(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        with app.app_context():
            old = _doc(
                {"aircraft_id": ac_id, "category": "insurance"},
                DocType.INSURANCE_CERT,
                until=T + timedelta(days=60),
            )
            old.title = "Insurance Q4"
            lc.refresh(lc.Owner(aircraft_id=ac_id), DocType.INSURANCE_CERT)
            db.session.commit()
            old_id = old.id
        _login(app, client)
        page = client.get(f"/aircraft/{ac_id}/documents?sensitive=1").data.decode()
        assert f"/documents/upload?replace={old_id}" in page
        form = client.get(
            f"/aircraft/{ac_id}/documents/upload?replace={old_id}"
        ).data.decode()
        assert "Replacing Insurance Q4." in form
        assert '<option value="insurance_certificate" selected>' in form
        assert '<option value="insurance" selected>' in form
        client.post(
            f"/aircraft/{ac_id}/documents/upload",
            data={
                "file": _pdf(),
                "doc_type": "insurance_certificate",
                "category": "insurance",
                "valid_until": (T + timedelta(days=150)).isoformat(),
            },
            content_type="multipart/form-data",
        )
        with app.app_context():
            assert db.session.get(Document, old_id).superseded_by_id is not None
            ac = db.session.get(Aircraft, ac_id)
            assert ac.insurance_expiry == T + timedelta(days=150)

    def test_dashboard_no_expiring_soon_with_a_renewal_on_file(self, app, client):
        from models import TenantProfile  # pyright: ignore[reportMissingImports]

        _uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        with app.app_context():
            # One aircraft: the dashboard shows its insurance/ARC dates.
            db.session.add(TenantProfile(tenant_id=tid, planned_aircraft_count=1))
            kw = {"aircraft_id": ac_id}
            _doc(kw, DocType.INSURANCE_CERT, until=T + timedelta(days=10))
            _doc(
                kw,
                DocType.INSURANCE_CERT,
                start=T + timedelta(days=11),
                until=T + timedelta(days=100),
            )
            lc.refresh(lc.Owner(aircraft_id=ac_id), DocType.INSURANCE_CERT)
            db.session.commit()
        _login(app, client)
        page = client.get("/").data.decode()
        assert (T + timedelta(days=100)).isoformat() in page
        assert "Expiring soon" not in page

    def test_daily_job_rolls_over_and_fixes_stale_links(self, app):
        """A renewal uploaded ahead of time but never linked (as on
        production before this): the daily job marks it — and a pilot's
        medical renewal starting today takes over the profile's date."""
        uid, tid = _create_user_and_tenant(app)
        ac_id = _add_aircraft(app, tid)
        with app.app_context():
            db.session.add(PilotProfile(user_id=uid, medical_expiry=T))
            _doc({"pilot_user_id": uid}, DocType.MEDICAL, until=T - timedelta(days=1))
            _doc(
                {"pilot_user_id": uid},
                DocType.MEDICAL,
                start=T,
                until=T + timedelta(days=365),
            )
            kw = {"aircraft_id": ac_id}
            q3 = _doc(kw, DocType.INSURANCE_CERT, until=T - timedelta(days=1))
            q4 = _doc(
                kw,
                DocType.INSURANCE_CERT,
                start=T,
                until=T + timedelta(days=90),
            )
            db.session.commit()
            assert q3.superseded_by_id is None
            from services.notification_service import (  # pyright: ignore[reportMissingImports]
                _recompute_all_expiry_fields,
            )

            _recompute_all_expiry_fields()
            assert q3.superseded_by_id == q4.id
            assert db.session.get(Aircraft, ac_id).insurance_expiry == T + timedelta(
                days=90
            )
            profile = PilotProfile.query.filter_by(user_id=uid).one()
            assert profile.medical_expiry == T + timedelta(days=365)
