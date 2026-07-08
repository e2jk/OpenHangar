"""
Tests for attaching a receipt/invoice document to an expense:
  - upload on add/edit (a new upload replaces the previous receipt)
  - extension validation
  - the receipt is an ordinary Document carrying aircraft_id + expense_id
    (invoice category), served through the authorised upload route
  - deleting the expense removes the receipt row
  - list page shows the paperclip / viewer trigger
"""

from io import BytesIO

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (
    Aircraft,
    DocCategory,
    Document,
    Expense,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)  # pyright: ignore[reportMissingImports]


def _create_user_and_tenant(app, email="owner@example.com"):
    with app.app_context():
        tenant = Tenant(name="Test Hangar")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            email=email, password_hash=_pw_hash.hash("testpassword123"), is_active=True
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=user.id, tenant_id=tenant.id, role=Role.OWNER)
        )
        db.session.commit()
        return user.id, tenant.id


def _login(app, client, email="owner@example.com"):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _add_aircraft(app, tenant_id, registration="OO-RCT"):
    with app.app_context():
        ac = Aircraft(
            tenant_id=tenant_id, registration=registration, make="Cessna", model="172S"
        )
        db.session.add(ac)
        db.session.commit()
        return ac.id


def _form(**kwargs):
    data = {
        "date": "2026-01-15",
        "expense_type": "other",
        "expense_category": "operating",
        "amount": "99.00",
        "currency": "EUR",
        "description": "Landing fee",
    }
    data.update(kwargs)
    return data


def _receipt(name="receipt.pdf", content=b"%PDF-1.4 fake"):
    return (BytesIO(content), name, "application/pdf")


class TestReceiptUpload:
    def test_receipt_saved_with_expense(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/expenses/add",
            data=_form(receipt=_receipt()),
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            exp = Expense.query.filter_by(aircraft_id=acid).one()
            doc = Document.query.filter_by(expense_id=exp.id).one()
            assert doc.aircraft_id == acid
            assert doc.category == DocCategory.INVOICE
            assert doc.title == "Landing fee"
            assert doc.original_filename == "receipt.pdf"
            assert doc.mime_type == "application/pdf"
            assert doc.owner_type == "expense"
            assert exp.receipts[0].id == doc.id

    def test_expense_without_receipt_has_none(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/expenses/add",
            data=_form(),
            content_type="multipart/form-data",
        )
        with app.app_context():
            exp = Expense.query.filter_by(aircraft_id=acid).one()
            assert exp.receipts == []

    def test_empty_file_field_treated_as_no_receipt(self, app, client):
        """Browsers submit the file input with an empty filename when unused."""
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/expenses/add",
            data=_form(receipt=(BytesIO(b""), "", "application/octet-stream")),
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            exp = Expense.query.filter_by(aircraft_id=acid).one()
            assert exp.receipts == []

    def test_receipt_title_falls_back_to_date(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/expenses/add",
            data=_form(description="", receipt=_receipt()),
            content_type="multipart/form-data",
        )
        with app.app_context():
            doc = Document.query.filter(Document.expense_id.isnot(None)).one()
            assert "2026-01-15" in (doc.title or "")

    def test_disallowed_extension_rejected(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        resp = client.post(
            f"/aircraft/{acid}/expenses/add",
            data=_form(receipt=(BytesIO(b"#!/bin/sh"), "evil.sh", "text/x-sh")),
            content_type="multipart/form-data",
        )
        assert b"This file type is not allowed for receipts." in resp.data
        with app.app_context():
            assert Expense.query.filter_by(aircraft_id=acid).count() == 0

    def test_new_upload_replaces_previous_receipt(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/expenses/add",
            data=_form(receipt=_receipt("first.pdf")),
            content_type="multipart/form-data",
        )
        with app.app_context():
            exp_id = Expense.query.filter_by(aircraft_id=acid).one().id
        resp = client.post(
            f"/aircraft/{acid}/expenses/{exp_id}/edit",
            data=_form(receipt=_receipt("second.pdf")),
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        with app.app_context():
            docs = Document.query.filter_by(expense_id=exp_id).all()
            assert len(docs) == 1
            assert docs[0].original_filename == "second.pdf"

    def test_edit_without_file_keeps_receipt(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/expenses/add",
            data=_form(receipt=_receipt()),
            content_type="multipart/form-data",
        )
        with app.app_context():
            exp_id = Expense.query.filter_by(aircraft_id=acid).one().id
        client.post(
            f"/aircraft/{acid}/expenses/{exp_id}/edit",
            data=_form(amount="120.00"),
            content_type="multipart/form-data",
        )
        with app.app_context():
            assert Document.query.filter_by(expense_id=exp_id).count() == 1
            assert float(db.session.get(Expense, exp_id).amount) == 120.0

    def test_receipt_served_through_upload_route(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/expenses/add",
            data=_form(receipt=_receipt(content=b"%PDF-1.4 receipt-bytes")),
            content_type="multipart/form-data",
        )
        with app.app_context():
            doc = Document.query.filter(Document.expense_id.isnot(None)).one()
            fname = doc.filename
        resp = client.get(f"/uploads/{fname}")
        assert resp.status_code == 200
        assert b"receipt-bytes" in resp.data

    def test_deleting_expense_removes_receipt_row(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/expenses/add",
            data=_form(receipt=_receipt()),
            content_type="multipart/form-data",
        )
        with app.app_context():
            exp_id = Expense.query.filter_by(aircraft_id=acid).one().id
        resp = client.post(
            f"/aircraft/{acid}/expenses/{exp_id}/delete", follow_redirects=False
        )
        assert resp.status_code == 302
        with app.app_context():
            assert Document.query.filter_by(expense_id=exp_id).count() == 0

    def test_list_shows_receipt_viewer_button(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/expenses/add",
            data=_form(receipt=_receipt()),
            content_type="multipart/form-data",
        )
        resp = client.get(f"/aircraft/{acid}/expenses?period=0")
        assert resp.status_code == 200
        assert b"bi-paperclip" in resp.data
        assert b"docModal" in resp.data

    def test_receipt_listed_in_aircraft_documents(self, app, client):
        _uid, tid = _create_user_and_tenant(app)
        acid = _add_aircraft(app, tid)
        _login(app, client)
        client.post(
            f"/aircraft/{acid}/expenses/add",
            data=_form(receipt=_receipt()),
            content_type="multipart/form-data",
        )
        resp = client.get(f"/aircraft/{acid}/documents")
        assert resp.status_code == 200
        assert b"Landing fee" in resp.data
        assert b"bi-receipt" in resp.data
