"""
Tests for crew claims: a pilot logging a flight that someone else already
logged (duplicate warning) asks to be added to it instead
(flights/crew_invites.py claim_option / create_claim).
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from models import (  # pyright: ignore[reportMissingImports]
    CrewInviteKind,
    CrewInviteStatus,
    CrewRole,
    CrewSlot,
    Flight,
    FlightCrewInvite,
    NotificationType,
    UserAllAircraftAccess,
    db,
)

from tests.test_crew_invites import (  # pyright: ignore[reportMissingImports]
    _add_flight,
    _add_invite,
    _login,
)
from tests.test_crew_removal import (  # pyright: ignore[reportMissingImports]
    _flight,
    _world_with_aircraft,
)

DISPATCH = "services.notification_service.dispatch"


def _setup(app):
    """Olga (owner) logged OO-ABC EBAW→EBOS on 2026-09-01 as PIC; Jan (a
    pilot with access to the whole fleet) flew it with her."""
    tid, kris, jan, els, owner, acid = _world_with_aircraft(app)
    with app.app_context():
        db.session.add(UserAllAircraftAccess(user_id=jan, tenant_id=tid))
        db.session.commit()
    fid = _add_flight(
        app,
        aircraft_id=acid,
        date=date(2026, 9, 1),
        pic_user_id=owner,
        pic_name="Olga Owner",
        created_by_user_id=owner,
        function_pic=Decimal("1.5"),
        flight_time_counter_start=Decimal("100.0"),
        flight_time_counter_end=Decimal("101.5"),
    )
    return tid, kris, jan, els, owner, acid, fid


def _log_same_flight(client, acid, **overrides):
    data = {
        "aircraft_id": str(acid),
        "date": "2026-09-01",
        "departure_icao": "EBAW",
        "arrival_icao": "EBOS",
        "flight_time_counter_start": "100.0",
        "flight_time_counter_end": "101.5",
        "pilot_role": "dual",
        "crew_name_0": "Olga Owner",
        "crew_name_1": "Jan Peeters",
        "crew_role_1": CrewRole.STUDENT,
    }
    data.update(overrides)
    with patch(DISPATCH) as mock_dispatch:
        resp = client.post("/flights/new", data=data, follow_redirects=False)
    return resp, mock_dispatch


def _claims(app):
    with app.app_context():
        return [
            (c.slot, c.invited_user_id, c.status, c.requested_role)
            for c in FlightCrewInvite.query.filter_by(kind=CrewInviteKind.CLAIM)
            .order_by(FlightCrewInvite.id)
            .all()
        ]


class TestClaimFromDuplicateWarning:
    def test_duplicate_warning_offers_claim_and_request_is_sent(self, app, client):
        _tid, _kris, jan, _els, owner, acid, fid = _setup(app)
        _login(client, jan)
        resp, _mock = _log_same_flight(client, acid)
        html = resp.data.decode()
        assert resp.status_code == 200
        assert "Possible duplicate detected" in html
        assert "Ask Olga Owner to add you to it instead" in html
        assert 'value="claim"' in html
        # The re-render keeps the submitted second-crew role, so clicking the
        # claim button posts it back with the request.
        assert (
            f'<option value="{CrewRole.STUDENT}"\n                    selected>' in html
        )

        resp, mock_dispatch = _log_same_flight(client, acid, duplicate_action="claim")
        assert resp.status_code == 302
        assert _claims(app) == [
            (CrewSlot.SECOND, jan, CrewInviteStatus.PENDING, CrewRole.STUDENT)
        ]
        sent = [
            c
            for c in mock_dispatch.call_args_list
            if c.args[0] == NotificationType.CREW_CLAIM
        ]
        assert [c.kwargs["target_user_ids"] for c in sent] == [[owner]]
        with app.app_context():
            # No duplicate flight, nothing linked yet.
            assert Flight.query.count() == 1
        assert _flight(app, fid).second_crew_user_id is None

        # Asking again: the warning says it's already requested, no button.
        resp, _mock = _log_same_flight(client, acid)
        html = resp.data.decode()
        assert "You already asked Olga Owner" in html
        assert 'value="claim"' not in html
        # Re-posting the claim action doesn't create a second request.
        _log_same_flight(client, acid, duplicate_action="claim")
        assert len(_claims(app)) == 1

    def test_approve_adds_claimant_to_flight(self, app, client):
        _tid, _kris, jan, _els, owner, acid, fid = _setup(app)
        _login(client, jan)
        _log_same_flight(client, acid, duplicate_action="claim")
        with app.app_context():
            claim_id = FlightCrewInvite.query.one().id
        # A claim never shows up as an invite for the claimant to answer.
        html = client.get("/pilot/logbook").data.decode()
        assert "Flights waiting for your confirmation" not in html
        assert (
            client.post(f"/flights/crew-invites/{claim_id}/accept").status_code == 404
        )

        _login(client, owner)
        for url in ("/", "/pilot/logbook"):
            html = client.get(url).data.decode()
            assert "Pilots asking to be added to your flights" in html
            assert f"/flights/crew-claims/{claim_id}/approve" in html
            assert "Student" in html
        with patch(DISPATCH) as mock_dispatch:
            resp = client.post(
                f"/flights/crew-claims/{claim_id}/approve", data={"next": "dashboard"}
            )
        assert resp.headers["Location"].endswith("/#crew-claims")
        fe = _flight(app, fid)
        assert fe.second_crew_user_id == jan
        assert fe.second_crew_name == "Jan Peeters"
        assert fe.second_crew_role == CrewRole.STUDENT
        assert fe.function_dual == fe.flight_time
        answered = [
            c
            for c in mock_dispatch.call_args_list
            if c.args[0] == NotificationType.CREW_INVITE_ANSWERED
        ]
        assert [c.kwargs["target_user_ids"] for c in answered] == [[jan]]
        assert "approved your request" in str(
            answered[0].args[2]["notification_title_key"]
        )

        resp = client.post(
            f"/flights/crew-claims/{claim_id}/approve", follow_redirects=True
        )
        assert b"already been handled" in resp.data

    def test_decline_keeps_flight_unchanged(self, app, client):
        _tid, _kris, jan, _els, owner, acid, fid = _setup(app)
        _login(client, jan)
        _log_same_flight(client, acid, duplicate_action="claim")
        with app.app_context():
            claim_id = FlightCrewInvite.query.one().id
        _login(client, owner)
        with patch(DISPATCH) as mock_dispatch:
            resp = client.post(f"/flights/crew-claims/{claim_id}/decline")
        assert resp.headers["Location"].endswith("/pilot/logbook#crew-claims")
        assert _flight(app, fid).second_crew_user_id is None
        assert _claims(app)[0][2] == CrewInviteStatus.DECLINED
        answered = [
            c
            for c in mock_dispatch.call_args_list
            if c.args[0] == NotificationType.CREW_INVITE_ANSWERED
        ]
        assert "declined your request" in str(
            answered[0].args[2]["notification_title_key"]
        )
        resp = client.post(
            f"/flights/crew-claims/{claim_id}/decline", follow_redirects=True
        )
        assert b"already been handled" in resp.data

    def test_only_the_logger_reviews_claims(self, app, client):
        _tid, kris, jan, _els, _owner, acid, fid = _setup(app)
        _login(client, jan)
        _log_same_flight(client, acid, duplicate_action="claim")
        with app.app_context():
            claim_id = FlightCrewInvite.query.one().id
        invite_id = _add_invite(app, fid, CrewSlot.PIC, kris, jan)
        for uid in (jan, kris):
            _login(client, uid)
            for action in ("approve", "decline"):
                assert (
                    client.post(f"/flights/crew-claims/{claim_id}/{action}").status_code
                    == 404
                )
        # An ordinary invite id isn't reviewable as a claim either.
        assert (
            client.post(f"/flights/crew-claims/{invite_id}/approve").status_code == 404
        )

    def test_slot_filled_before_approval(self, app, client):
        _tid, kris, jan, _els, owner, acid, fid = _setup(app)
        _login(client, jan)
        _log_same_flight(client, acid, duplicate_action="claim")
        with app.app_context():
            claim_id = FlightCrewInvite.query.one().id
            db.session.get(Flight, fid).second_crew_user_id = kris
            db.session.commit()
        _login(client, owner)
        resp = client.post(
            f"/flights/crew-claims/{claim_id}/approve", follow_redirects=True
        )
        assert b"already been filled" in resp.data
        assert _claims(app)[0][2] == CrewInviteStatus.CANCELLED


class TestClaimOption:
    def test_when_a_claim_is_not_offered(self, app, client):
        _tid, _kris, jan, _els, owner, acid, fid = _setup(app)
        unlinked = _add_flight(
            app, aircraft_id=acid, date=date(2026, 9, 2), pic_name="Paper Pilot"
        )
        with app.app_context():
            from flights.crew_invites import claim_option

            fe = db.session.get(Flight, fid)
            assert claim_option(fe, jan, "none") is None  # not logging as crew
            assert claim_option(fe, owner, "dual") is None  # already on it
            assert claim_option(fe, jan, "pic") is None  # PIC slot taken
            assert claim_option(db.session.get(Flight, unlinked), jan, "dual") is None
            option = claim_option(fe, jan, "dual")
            assert option == {
                "slot": CrewSlot.SECOND,
                "approver_names": "Olga Owner",
                "already_requested": False,
            }

    def test_editing_a_flight_never_offers_a_claim(self, app, client):
        _tid, _kris, jan, _els, _owner, acid, _fid = _setup(app)
        other = _add_flight(
            app,
            aircraft_id=acid,
            date=date(2026, 9, 1),
            pic_user_id=jan,
            created_by_user_id=jan,
            flight_time_counter_start=Decimal("90.0"),
            flight_time_counter_end=Decimal("91.0"),
        )
        _login(client, jan)
        with patch(DISPATCH):
            resp = client.post(
                f"/flights/{other}/edit",
                data={
                    "aircraft_id": str(acid),
                    "date": "2026-09-01",
                    "departure_icao": "EBAW",
                    "arrival_icao": "EBOS",
                    "flight_time_counter_start": "90.0",
                    "flight_time_counter_end": "91.0",
                    "pilot_role": "pic",
                    "crew_name_0": "Jan Peeters",
                },
            )
        assert b'value="claim"' not in resp.data

    def test_claim_request_without_matching_flight_is_refused(self, app, client):
        _tid, _kris, jan, _els, _owner, acid, _fid = _setup(app)
        _login(client, jan)
        resp, _mock = _log_same_flight(
            client, acid, duplicate_action="claim", arrival_icao="EBBR"
        )
        assert resp.status_code == 302
        assert _claims(app) == []
        assert b"can&#39;t be claimed" in client.get("/pilot/logbook").data

    def test_requested_role_ignored_for_pic_slot_and_invalid_roles(self, app):
        _tid, kris, jan, _els, _owner, _acid, fid = _setup(app)
        with app.app_context():
            from flights.crew_invites import create_claim

            fe = db.session.get(Flight, fid)
            pic_claim = create_claim(fe, jan, CrewSlot.PIC, CrewRole.STUDENT)
            bad_role = create_claim(fe, kris, CrewSlot.SECOND, "PIC")
            assert (pic_claim.requested_role, bad_role.requested_role) == (None, None)
            db.session.rollback()

    def test_claim_email_renders(self, app, client):
        _tid, _kris, jan, _els, _owner, acid, _fid = _setup(app)
        _login(client, jan)
        with (
            patch("services.email_service.send_email") as mock_send,
            patch("services.email_service._record_health"),
        ):
            client.post(
                "/flights/new",
                data={
                    "aircraft_id": str(acid),
                    "date": "2026-09-01",
                    "departure_icao": "EBAW",
                    "arrival_icao": "EBOS",
                    "flight_time_counter_start": "100.0",
                    "flight_time_counter_end": "101.5",
                    "pilot_role": "dual",
                    "crew_name_0": "Olga Owner",
                    "crew_name_1": "Jan Peeters",
                    "duplicate_action": "claim",
                },
            )
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to"] == "olga@example.com"
        assert "Jan Peeters asks to be added to your flight" in kwargs["html_body"]

    def test_notification_type_listed(self, app, client):
        _tid, _kris, jan, _els, _owner, _acid, _fid = _setup(app)
        _login(client, jan)
        html = client.get("/config/notifications/").data.decode()
        assert "Pilot asks to be added to your flight" in html
