"""
Tests for crew invites: naming another tenant pilot in a flight's crew slot
asks them to confirm the flight in their own logbook (flights/crew_invites.py).
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pw_hash as _pw_hash  # pyright: ignore[reportMissingImports]
from models import (  # pyright: ignore[reportMissingImports]
    CrewInviteStatus,
    CrewRole,
    CrewSlot,
    Flight,
    FlightCrewInvite,
    NotificationType,
    Role,
    Tenant,
    TenantUser,
    User,
    db,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_tenant(app, name="Crew Hangar"):
    with app.app_context():
        t = Tenant(name=name)
        db.session.add(t)
        db.session.commit()
        return t.id


def _make_user(app, tid, email, name, role=Role.PILOT, is_active=True):
    with app.app_context():
        u = User(
            email=email,
            password_hash=_pw_hash.hash("x"),
            is_active=is_active,
            name=name,
        )
        db.session.add(u)
        db.session.flush()
        db.session.add(TenantUser(user_id=u.id, tenant_id=tid, role=role))
        db.session.commit()
        return u.id


def _login(client, uid):
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _world(app):
    """Tenant with the logging pilot (Kris) and two colleagues."""
    tid = _make_tenant(app)
    me = _make_user(app, tid, "kris@example.com", "Kris Logger")
    jan = _make_user(app, tid, "jan@example.com", "Jan Peeters", role=Role.INSTRUCTOR)
    els = _make_user(app, tid, "els@example.com", "Els Janssens")
    return tid, me, jan, els


def _flight_form(**overrides):
    data = {
        "other_aircraft": "1",
        "other_ac_make_model": "Cessna C172",
        "other_ac_reg": "OO-TST",
        "date": "2026-09-01",
        "departure_icao": "EBAW",
        "arrival_icao": "EBOS",
        "flight_time": "1.5",
        "pilot_role": "dual",
        "crew_name_0": "",
        "crew_name_1": "Kris Logger",
        "crew_role_1": CrewRole.STUDENT,
    }
    data.update(overrides)
    return data


def _post_new_flight(client, **overrides):
    with patch("services.notification_service.dispatch") as mock_dispatch:
        resp = client.post(
            "/flights/new", data=_flight_form(**overrides), follow_redirects=False
        )
    return resp, mock_dispatch


def _add_flight(app, *, pic_user_id=None, second_crew_user_id=None, **fields):
    values = {
        "date": date(2026, 9, 1),
        "departure_icao": "EBAW",
        "arrival_icao": "EBOS",
        "other_aircraft_registration": "OO-TST",
        "other_aircraft_type": "Cessna C172",
        "flight_time": Decimal("1.5"),
        **fields,
    }
    with app.app_context():
        fe = Flight(
            pic_user_id=pic_user_id,
            second_crew_user_id=second_crew_user_id,
            **values,
        )
        db.session.add(fe)
        db.session.commit()
        return fe.id


def _add_invite(app, flight_id, slot, invited, inviter, status=None):
    with app.app_context():
        inv = FlightCrewInvite(
            flight_id=flight_id,
            slot=slot,
            invited_user_id=invited,
            invited_by_user_id=inviter,
            status=status or CrewInviteStatus.PENDING,
        )
        db.session.add(inv)
        db.session.commit()
        return inv.id


def _invites(app):
    with app.app_context():
        return [
            (i.slot, i.invited_user_id, i.status)
            for i in FlightCrewInvite.query.order_by(FlightCrewInvite.id).all()
        ]


# ── tenant_pilots ─────────────────────────────────────────────────────────────


class TestTenantPilots:
    def test_duplicate_display_names_get_email_suffix(self, app):
        tid = _make_tenant(app)
        a = _make_user(app, tid, "jan.a@example.com", "Jan Peeters")
        b = _make_user(app, tid, "jp@example.com", "jan peeters")
        c = _make_user(app, tid, "els@example.com", "Els Janssens")
        with app.app_context():
            from utils import tenant_pilot_names, tenant_pilots

            pilots = dict(tenant_pilots(tid))
            assert pilots[a] == "Jan Peeters (jan.a)"
            assert pilots[b] == "jan peeters (jp)"
            assert pilots[c] == "Els Janssens"
            assert tenant_pilot_names(tid) == [
                "Els Janssens",
                "Jan Peeters (jan.a)",
                "jan peeters (jp)",
            ]


# ── Creating invites from the flight form ─────────────────────────────────────


class TestInviteFromFlightForm:
    def test_picking_tenant_pilot_creates_pending_invite_and_notifies(
        self, app, client
    ):
        tid, me, jan, _els = _world(app)
        _login(client, me)
        resp, mock_dispatch = _post_new_flight(
            client, crew_name_0="Jan Peeters", crew_user_id_0=str(jan)
        )
        assert resp.status_code == 302
        assert _invites(app) == [(CrewSlot.PIC, jan, CrewInviteStatus.PENDING)]
        with app.app_context():
            fe = Flight.query.one()
            # Not linked until Jan confirms.
            assert fe.pic_user_id is None
            assert fe.pic_name == "Jan Peeters"
            assert fe.second_crew_user_id == me
        mock_dispatch.assert_called_once()
        args, kwargs = mock_dispatch.call_args
        assert args[0] == NotificationType.CREW_INVITE
        assert args[1] == tid
        assert kwargs["target_user_ids"] == [jan]
        assert args[2]["cta_url"].endswith("/pilot/logbook#crew-invites")

    def test_second_crew_slot_invite_when_logger_is_pic(self, app, client):
        _tid, me, _jan, els = _world(app)
        _login(client, me)
        _post_new_flight(
            client,
            pilot_role="pic",
            crew_name_0="Kris Logger",
            crew_name_1="Els Janssens",
            crew_user_id_1=str(els),
            crew_role_1=CrewRole.COPILOT,
        )
        assert _invites(app) == [(CrewSlot.SECOND, els, CrewInviteStatus.PENDING)]

    def test_free_text_name_creates_no_invite(self, app, client):
        _tid, me, _jan, _els = _world(app)
        _login(client, me)
        _resp, mock_dispatch = _post_new_flight(client, crew_name_0="Outside Pilot")
        assert _invites(app) == []
        mock_dispatch.assert_not_called()

    def test_ignored_user_ids(self, app, client):
        """Stale id (name edited), self, own slot, other tenant, garbage."""
        _tid, me, jan, els = _world(app)
        other_tid = _make_tenant(app, "Other")
        outsider = _make_user(app, other_tid, "out@example.com", "Out Sider")
        _login(client, me)
        cases = [
            {"crew_name_0": "Someone Else", "crew_user_id_0": str(jan)},
            {"crew_name_0": "Kris Logger", "crew_user_id_0": str(me)},
            {"crew_name_0": "Out Sider", "crew_user_id_0": str(outsider)},
            {"crew_name_0": "Jan Peeters", "crew_user_id_0": "abc"},
            # Logger is second crew ("dual"): their own slot never gets an invite.
            {
                "crew_name_0": "Jan Peeters",
                "crew_name_1": "Els Janssens",
                "crew_user_id_1": str(els),
            },
        ]
        for i, case in enumerate(cases):
            _post_new_flight(client, departure_icao=f"EB{i:02d}", **case)
        assert _invites(app) == []

    def test_resave_changes_cancel_and_reinvite(self, app, client):
        _tid, me, jan, els = _world(app)
        _login(client, me)
        _post_new_flight(client, crew_name_0="Jan Peeters", crew_user_id_0=str(jan))
        with app.app_context():
            fid = Flight.query.one().id

        # Same pilot again: nothing new, no second e-mail.
        _resp, mock_dispatch = self._edit(
            client, fid, crew_name_0="Jan Peeters", crew_user_id_0=str(jan)
        )
        mock_dispatch.assert_not_called()
        assert _invites(app) == [(CrewSlot.PIC, jan, CrewInviteStatus.PENDING)]

        # Switch to Els: Jan's invite cancelled, Els invited.
        _resp, mock_dispatch = self._edit(
            client, fid, crew_name_0="Els Janssens", crew_user_id_0=str(els)
        )
        mock_dispatch.assert_called_once()
        assert _invites(app) == [
            (CrewSlot.PIC, jan, CrewInviteStatus.CANCELLED),
            (CrewSlot.PIC, els, CrewInviteStatus.PENDING),
        ]

        # Plain text name: Els's invite cancelled too.
        self._edit(client, fid, crew_name_0="Outside Pilot")
        assert [s for _slot, _u, s in _invites(app)] == [
            CrewInviteStatus.CANCELLED,
            CrewInviteStatus.CANCELLED,
        ]

    def test_declined_pilot_is_not_asked_again(self, app, client):
        _tid, me, jan, _els = _world(app)
        fid = _add_flight(
            app, second_crew_user_id=me, second_crew_role=CrewRole.STUDENT
        )
        _add_invite(app, fid, CrewSlot.PIC, jan, me, CrewInviteStatus.DECLINED)
        _login(client, me)
        _resp, mock_dispatch = self._edit(
            client, fid, crew_name_0="Jan Peeters", crew_user_id_0=str(jan)
        )
        mock_dispatch.assert_not_called()
        assert _invites(app) == [(CrewSlot.PIC, jan, CrewInviteStatus.DECLINED)]

    def test_filled_slot_is_never_invited_or_unlinked(self, app, client):
        _tid, me, jan, els = _world(app)
        fid = _add_flight(
            app,
            pic_user_id=jan,
            pic_name="Jan Peeters",
            second_crew_user_id=me,
            second_crew_role=CrewRole.STUDENT,
        )
        _login(client, me)
        self._edit(client, fid, crew_name_0="Els Janssens", crew_user_id_0=str(els))
        assert _invites(app) == []
        with app.app_context():
            assert db.session.get(Flight, fid).pic_user_id == jan

    def test_edit_form_shows_pending_invite(self, app, client):
        _tid, me, jan, _els = _world(app)
        fid = _add_flight(
            app,
            pic_name="Jan Peeters",
            second_crew_user_id=me,
            second_crew_role=CrewRole.STUDENT,
        )
        _add_invite(app, fid, CrewSlot.PIC, jan, me)
        _login(client, me)
        html = client.get(f"/flights/{fid}/edit").data.decode()
        assert "Waiting for Jan Peeters to confirm this flight." in html
        assert f'data-pending-user-id="{jan}"' in html
        assert f'value="{jan}"' in html
        assert f'data-user-id="{jan}"' in html

    def test_form_rerender_keeps_picked_user_id(self, app, client):
        _tid, me, jan, _els = _world(app)
        _login(client, me)
        resp, _mock = _post_new_flight(
            client,
            crew_name_0="Jan Peeters",
            crew_user_id_0=str(jan),
            other_ac_reg="",  # validation error → form re-rendered
        )
        assert resp.status_code == 200
        assert 'name="crew_user_id_0" id="crew_user_id_0"' in resp.data.decode()
        assert f'value="{jan}"' in resp.data.decode()
        assert _invites(app) == []

    @staticmethod
    def _edit(client, fid, **overrides):
        with patch("services.notification_service.dispatch") as mock_dispatch:
            resp = client.post(
                f"/flights/{fid}/edit",
                data=_flight_form(**overrides),
                follow_redirects=False,
            )
        assert resp.status_code == 302
        return resp, mock_dispatch


# ── Notification e-mail ───────────────────────────────────────────────────────


class TestInviteEmail:
    def test_email_rendered_and_sent_to_invited_pilot(self, app, client):
        _tid, me, jan, _els = _world(app)
        _login(client, me)
        with (
            patch("services.email_service.send_email") as mock_send,
            patch("services.email_service._record_health"),
        ):
            client.post(
                "/flights/new",
                data=_flight_form(
                    pilot_role="pic",
                    crew_name_0="Kris Logger",
                    crew_name_1="Jan Peeters",
                    crew_user_id_1=str(jan),
                    crew_role_1=CrewRole.IP,
                ),
            )
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to"] == "jan@example.com"
        assert "2026-09-01" in kwargs["subject"]
        assert "Kris Logger added you to a flight" in kwargs["html_body"]
        assert "Instructor" in kwargs["html_body"]
        assert "EBAW → EBOS" in kwargs["text_body"]

    def test_dispatch_failure_does_not_break_saving(self, app, client):
        _tid, me, jan, _els = _world(app)
        _login(client, me)
        with patch(
            "services.notification_service.dispatch", side_effect=RuntimeError("smtp")
        ):
            resp = client.post(
                "/flights/new",
                data=_flight_form(crew_name_0="Jan Peeters", crew_user_id_0=str(jan)),
            )
        assert resp.status_code == 302
        assert _invites(app) == [(CrewSlot.PIC, jan, CrewInviteStatus.PENDING)]

    def test_invite_without_inviter_and_role_labels(self, app):
        _tid, me, jan, els = _world(app)
        fid = _add_flight(app, second_crew_name="Els Janssens")
        pic_inv = _add_invite(app, fid, CrewSlot.PIC, jan, None)
        second_inv = _add_invite(app, fid, CrewSlot.SECOND, els, me)
        with (
            app.test_request_context(),
            patch("services.notification_service.dispatch") as mock_dispatch,
        ):
            from flights.crew_invites import notify_invites

            invites = [
                db.session.get(FlightCrewInvite, pic_inv),
                db.session.get(FlightCrewInvite, second_inv),
            ]
            notify_invites(invites, _tid)
            notify_invites([], _tid)
        first, second = (c.args[2] for c in mock_dispatch.call_args_list)
        assert first["notification_title_args"] == {"name": "—"}
        assert str(first["details"][3][1]) == "PIC / Commander"
        assert str(second["details"][3][1]) == "Second crew"

    def test_notification_type_listed_in_preferences(self, app, client):
        _tid, me, _jan, _els = _world(app)
        _login(client, me)
        html = client.get("/config/notifications/").data.decode()
        assert "Flight crew confirmation request" in html


# ── Seeing and answering invites ──────────────────────────────────────────────


class TestAnswerInvite:
    def test_dashboard_logbook_and_nav_show_pending_invite(self, app, client):
        _tid, me, jan, _els = _world(app)
        fid = _add_flight(
            app,
            pic_name="Jan Peeters",
            second_crew_user_id=me,
            second_crew_role=CrewRole.STUDENT,
        )
        inv = _add_invite(app, fid, CrewSlot.PIC, jan, me)
        _login(client, jan)
        for url in ("/", "/pilot/logbook"):
            html = client.get(url).data.decode()
            assert "Flights waiting for your confirmation" in html
            assert "Kris Logger" in html
            assert f"/flights/crew-invites/{inv}/accept" in html
            assert "badge rounded-pill bg-warning text-dark" in html

        _login(client, me)
        for url in ("/", "/pilot/logbook"):
            assert "Flights waiting for your confirmation" not in (
                client.get(url).data.decode()
            )

    def test_accept_pic_slot_links_flight_to_logbook(self, app, client):
        _tid, me, jan, _els = _world(app)
        fid = _add_flight(
            app, second_crew_user_id=me, second_crew_role=CrewRole.STUDENT
        )
        inv = _add_invite(app, fid, CrewSlot.PIC, jan, me)
        _login(client, jan)
        resp = client.post(f"/flights/crew-invites/{inv}/accept", data={})
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/pilot/logbook#crew-invites")
        with app.app_context():
            fe = db.session.get(Flight, fid)
            assert fe.pic_user_id == jan
            assert fe.pic_name == "Jan Peeters"
            assert fe.function_pic == Decimal("1.5")
            assert fe.second_crew_user_id == me
            assert db.session.get(FlightCrewInvite, inv).responded_at is not None
        assert _invites(app) == [(CrewSlot.PIC, jan, CrewInviteStatus.ACCEPTED)]
        assert b"EBAW" in client.get("/pilot/logbook").data

    def test_accept_second_slot_sets_function_for_role(self, app, client):
        _tid, me, jan, els = _world(app)
        for user, role, field in [
            (jan, CrewRole.IP, "function_instructor"),
            (els, CrewRole.COPILOT, "function_copilot"),
        ]:
            fid = _add_flight(
                app,
                pic_user_id=me,
                pic_name="Kris Logger",
                second_crew_role=role,
                second_crew_name="Named Before",
            )
            inv = _add_invite(app, fid, CrewSlot.SECOND, user, me)
            _login(client, user)
            resp = client.post(
                f"/flights/crew-invites/{inv}/accept", data={"next": "dashboard"}
            )
            assert resp.headers["Location"].endswith("/#crew-invites")
            with app.app_context():
                fe = db.session.get(Flight, fid)
                assert fe.second_crew_user_id == user
                assert fe.second_crew_name == "Named Before"
                assert getattr(fe, field) == Decimal("1.5")

    def test_accept_safety_pilot_fills_name_but_no_function(self, app, client):
        _tid, me, _jan, els = _world(app)
        fid = _add_flight(app, pic_user_id=me, second_crew_role=CrewRole.SP)
        inv = _add_invite(app, fid, CrewSlot.SECOND, els, me)
        _login(client, els)
        client.post(f"/flights/crew-invites/{inv}/accept", data={})
        with app.app_context():
            fe = db.session.get(Flight, fid)
            assert fe.second_crew_user_id == els
            assert fe.second_crew_name == "Els Janssens"
            assert fe.function_copilot is None
            assert fe.function_instructor is None
            assert fe.function_dual is None

    def test_accept_when_slot_already_filled_cancels(self, app, client):
        _tid, me, jan, els = _world(app)
        fid = _add_flight(app, pic_user_id=els, second_crew_user_id=me)
        inv = _add_invite(app, fid, CrewSlot.PIC, jan, me)
        _login(client, jan)
        resp = client.post(
            f"/flights/crew-invites/{inv}/accept", data={}, follow_redirects=True
        )
        assert b"already been filled" in resp.data
        with app.app_context():
            assert db.session.get(Flight, fid).pic_user_id == els
        assert _invites(app) == [(CrewSlot.PIC, jan, CrewInviteStatus.CANCELLED)]

    def test_decline_leaves_slot_name_only(self, app, client):
        _tid, me, jan, _els = _world(app)
        fid = _add_flight(app, pic_name="Jan Peeters", second_crew_user_id=me)
        inv = _add_invite(app, fid, CrewSlot.PIC, jan, me)
        _login(client, jan)
        resp = client.post(
            f"/flights/crew-invites/{inv}/decline", data={}, follow_redirects=True
        )
        assert b"Flight invitation declined." in resp.data
        with app.app_context():
            fe = db.session.get(Flight, fid)
            assert fe.pic_user_id is None
            assert fe.pic_name == "Jan Peeters"
        assert _invites(app) == [(CrewSlot.PIC, jan, CrewInviteStatus.DECLINED)]

    def test_answering_someone_elses_invite_is_404(self, app, client):
        _tid, me, jan, els = _world(app)
        fid = _add_flight(app, second_crew_user_id=me)
        inv = _add_invite(app, fid, CrewSlot.PIC, jan, me)
        _login(client, els)
        for action in ("accept", "decline"):
            assert (
                client.post(f"/flights/crew-invites/{inv}/{action}").status_code == 404
            )
        assert client.post("/flights/crew-invites/999999/accept").status_code == 404
        assert _invites(app) == [(CrewSlot.PIC, jan, CrewInviteStatus.PENDING)]

    def test_answering_closed_invite_warns(self, app, client):
        _tid, me, jan, _els = _world(app)
        fid = _add_flight(app, second_crew_user_id=me)
        inv = _add_invite(app, fid, CrewSlot.PIC, jan, me, CrewInviteStatus.CANCELLED)
        _login(client, jan)
        for action in ("accept", "decline"):
            resp = client.post(
                f"/flights/crew-invites/{inv}/{action}", follow_redirects=True
            )
            assert b"no longer open" in resp.data
        assert _invites(app) == [(CrewSlot.PIC, jan, CrewInviteStatus.CANCELLED)]

    def test_deleting_flight_removes_invites(self, app):
        _tid, me, jan, _els = _world(app)
        fid = _add_flight(app, second_crew_user_id=me)
        _add_invite(app, fid, CrewSlot.PIC, jan, me)
        with app.app_context():
            db.session.delete(db.session.get(Flight, fid))
            db.session.commit()
        assert _invites(app) == []
