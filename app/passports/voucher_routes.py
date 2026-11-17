"""Personal voucher forms; a shared pool exposes its balance, not private ledgers."""

import secrets
from typing import Any

from flask import abort, redirect, render_template, request, session, url_for
from flask.typing import ResponseReturnValue
from flask_babel import gettext as _
from models import (
    Flight,
    PassportParticipant,
    db,
)
from models import (
    PassportVoucherAllocation as Allocation,
)
from sqlalchemy.exc import IntegrityError
from utils import db_id, login_required

from passports import passports_bp, vouchers
from passports.access import require_passport
from passports.eligibility import eligible_flight, participant_for, passport_progress
from passports.evidence import writable_participant
from passports.management import BuilderError, require_actor


def _id(name: str) -> int:
    value = request.form.get(name, type=db_id)
    if value is None:
        abort(400)
    return value


@passports_bp.route("/<int:passport_id>/vouchers", methods=["GET", "POST"])
@login_required
def voucher_ledger(passport_id: int) -> ResponseReturnValue:
    passport = require_passport(passport_id)
    user_id = session["user_id"]
    error = None
    status = 200
    if request.method == "POST":
        try:
            action = request.form.get("action")
            if action == "default":
                vouchers.configure_default(
                    passport, user_id, request.form.get("quantity", "")
                )
            elif action == "allocate":
                scope = request.form.get("scope", "personal")
                if scope not in {"personal", "shared"}:
                    abort(400)
                personal_user_id = (
                    _id("personal_user_id") if scope == "personal" else None
                )
                vouchers.configure(
                    passport, user_id, _id("target_id"), request.form, personal_user_id
                )
            elif action in {"redeem", "correct"}:
                vouchers.redeem(
                    passport,
                    user_id,
                    _id("allocation_id"),
                    request.form,
                    replaces_id=_id("redemption_id") if action == "correct" else None,
                )
            elif action == "undo":
                vouchers.undo(passport, user_id, _id("redemption_id"))
            elif action == "delete_allocation":
                vouchers.delete_unused(passport, user_id, _id("allocation_id"))
            else:
                abort(400)
            db.session.commit()
            return redirect(
                url_for("passports.voucher_ledger", passport_id=passport.id)
            )
        except BuilderError as exc:
            db.session.rollback()
            error, status = str(exc), exc.status
        except IntegrityError:
            db.session.rollback()
            error, status = (
                _("The voucher record changed. Reload the page before trying again."),
                409,
            )
    data = vouchers.ledger(passport, user_id)
    progress = passport_progress(passport, user_id)
    targets = {row["target_id"]: row for row in progress["rows"]}
    participant = participant_for(passport.id, user_id)
    can_record = False
    try:
        writable_participant(passport, user_id)
        can_record = True
    except BuilderError:
        can_record = False
    can_manage = passport.owner_user_id == user_id
    try:
        require_actor(passport.tenant_id, user_id)
    except BuilderError:
        can_manage = False
    edit = None
    if request.args.get("edit") or request.form.get("action") == "correct":
        edit_id = request.args.get("edit", type=db_id) or request.form.get(
            "redemption_id", type=db_id
        )
        edit = next((row for row in data["redemptions"] if row.id == edit_id), None)
        if edit is None:
            abort(404)
    participants = (
        PassportParticipant.query.filter_by(passport_id=passport.id, is_active=True)
        .filter(PassportParticipant.accepted_at.is_not(None))
        .all()
        if can_manage
        else []
    )
    config = (
        Allocation.query.filter_by(passport_id=passport.id)
        .order_by(Allocation.id)
        .all()
        if can_manage
        else []
    )
    names = {person.user_id: person.display_name for person in participants}
    balances = {row["allocation"].id: row for row in data["pools"]}
    limits = {
        "redemption_date": 32,
        "quantity": 64,
        "note": 8000,
        "saving_amount": 64,
        "currency": 8,
        "supporting_flight": 32,
        "allocation_id": 32,
        "redemption_id": 32,
        "request_key": 64,
        "target_id": 32,
        "personal_user_id": 32,
        "scope": 16,
        "valid_from": 32,
        "valid_until": 32,
    }
    submitted = {
        key: request.form[key][:limit]
        for key, limit in limits.items()
        if key in request.form
    }
    form: dict[str, Any] = (
        submitted
        if request.method == "POST"
        and request.form.get("action") in {"redeem", "correct"}
        else {
            "redemption_date": edit.redemption_date.isoformat() if edit else "",
            "quantity": str(edit.quantity) if edit else "1",
            "note": edit.note or "" if edit else "",
            "saving_amount": str(edit.saving_amount)
            if edit and edit.saving_amount is not None
            else "",
            "currency": edit.currency or "" if edit else "",
            "supporting_flight": str(edit.flight_id) if edit and edit.flight_id else "",
        }
    )
    supporting_flights = progress["supporting_flights"]
    if (
        edit
        and edit.flight_id
        and edit.flight_id not in {flight.id for flight in supporting_flights}
    ):
        supporting = Flight.query.filter(
            Flight.id == edit.flight_id,
            db.or_(
                Flight.pic_user_id == user_id, Flight.second_crew_user_id == user_id
            ),
            Flight.date == edit.redemption_date,
        ).first()
        if supporting is not None and eligible_flight(supporting):
            supporting_flights.append(supporting)
    return render_template(
        "passports/vouchers.html",
        passport=passport,
        data=data,
        targets=targets,
        balances=balances,
        participant=participant,
        can_record=can_record,
        can_manage=can_manage and passport.archived_at is None,
        config=config,
        participants=participants,
        names=names,
        edit=edit,
        form=form,
        allocation_form=submitted if request.form.get("action") == "allocate" else {},
        supporting_flights=supporting_flights,
        request_key=form.get("request_key") or secrets.token_urlsafe(24),
        error=error,
    ), status
