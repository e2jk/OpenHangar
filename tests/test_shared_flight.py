"""
Tests for editing a flight that is in more than one pilot's logbook
(flights/shared_flight.py): who may edit the shared fields, protection of the
other pilot's personal fields, the "my part of this flight" page, correction
suggestions and the related notifications.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from models import (  # pyright: ignore[reportMissingImports]
    CorrectionStatus,
    CrewRole,
    CrewSlot,
    Flight,
    FlightCorrectionSuggestion,
    FlightCrewInvite,
    NotificationType,
    Role,
    db,
)
from offline.serialize import (  # pyright: ignore[reportMissingImports]
    canonical_entry,
    canonical_pilot_entry,
)

from tests.test_crew_invites import (  # pyright: ignore[reportMissingImports]
    _add_flight,
    _add_invite,
    _flight_form,
    _login,
    _make_user,
    _world,
)
from tests.test_crew_removal import (  # pyright: ignore[reportMissingImports]
    _flight,
    _world_with_aircraft,
)

DISPATCH = "services.notification_service.dispatch"


def _shared(app, logger, other, *, created_by=True, **fields):
    """A confirmed shared flight: *logger* PIC (and creator), *other* as
    instructor in the second slot with their own remark."""
    values = {
        "pic_user_id": logger,
        "pic_name": "Kris Logger",
        "function_pic": Decimal("1.5"),
        "second_crew_user_id": other,
        "second_crew_name": "Jan Peeters",
        "second_crew_role": CrewRole.IP,
        "function_instructor": Decimal("1.5"),
        "second_crew_remarks": "Jan's own note",
        "created_by_user_id": logger if created_by else None,
    }
    values.update(fields)
    return _add_flight(app, **values)


def _dispatched(mock, notification_type):
    return [c for c in mock.call_args_list if c.args[0] == notification_type]


# ── Who may edit the shared fields ────────────────────────────────────────────


class TestEditRights:
    def test_rules(self, app):
        _tid, kris, jan, els, owner, acid = _world_with_aircraft(app)
        shared = _shared(app, kris, jan)
        legacy = _shared(app, kris, jan, created_by=False)
        logger_left = _shared(app, None, jan, created_by=False, created_by_user_id=els)
        managed = _shared(app, kris, jan, aircraft_id=acid)
        with app.app_context():
            from flights.shared_flight import can_edit_shared, shared_editor_ids

            fe = db.session.get(Flight, shared)
            assert shared_editor_ids(fe) == {kris}
            assert can_edit_shared(fe, kris, Role.PILOT)
            assert not can_edit_shared(fe, jan, Role.INSTRUCTOR)
            # Not on the flight at all: normal access rules, not this module.
            assert can_edit_shared(fe, els, Role.PILOT)
            assert shared_editor_ids(db.session.get(Flight, legacy)) == {kris, jan}
            assert shared_editor_ids(db.session.get(Flight, logger_left)) == {jan}
            fe_managed = db.session.get(Flight, managed)
            assert not can_edit_shared(fe_managed, jan, Role.PILOT)
            assert can_edit_shared(fe_managed, jan, Role.OWNER)
            # Owners/admins only get that override on a managed aircraft.
            assert not can_edit_shared(fe, jan, Role.ADMIN)
            assert owner not in shared_editor_ids(fe_managed)

    def test_non_logger_is_sent_to_their_part_of_the_flight(self, app, client):
        _tid, kris, jan, _els, _owner, acid = _world_with_aircraft(app)
        standalone = _shared(app, kris, jan)
        managed = _shared(app, kris, jan, aircraft_id=acid)
        _login(client, jan)
        for url, fid in (
            (f"/flights/{standalone}/edit", standalone),
            (f"/pilot/logbook/{standalone}/edit", standalone),
            (f"/flights/{managed}/edit", managed),
        ):
            for method in (client.get, client.post):
                resp = method(url)
                assert resp.status_code == 302
                assert resp.headers["Location"].endswith(f"/flights/{fid}/my-part")

    def test_offline_sync_refuses_non_logger(self, app, client):
        _tid, kris, jan, _els, _owner, acid = _world_with_aircraft(app)
        standalone = _shared(app, kris, jan)
        managed = _shared(app, kris, jan, aircraft_id=acid)
        _login(client, jan)
        with app.app_context():
            fe = db.session.get(Flight, managed)
            base = canonical_entry(fe)
            pe = db.session.get(Flight, standalone)
            pbase = canonical_pilot_entry(pe)
        resp = client.post(
            f"/api/offline/flights/{managed}/sync",
            json={"fields": {**base, "notes": "sneaky"}, "base": base},
        )
        assert resp.status_code == 400
        assert "only they can change" in resp.get_json()["errors"][0]
        resp = client.post(
            f"/api/offline/pilot/logbook/{standalone}/sync",
            json={"fields": {**pbase, "remarks": "sneaky"}, "base": pbase},
        )
        assert resp.status_code == 400
        assert _flight(app, managed).notes is None
        assert _flight(app, standalone).notes is None


# ── The logger's saves never touch the other pilot's part ────────────────────


class TestOtherPilotProtected:
    def test_flight_form_keeps_other_pilot_fields_and_notifies(self, app, client):
        tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan, flight_time=Decimal("1.5"))
        _login(client, kris)
        html = client.get(f"/flights/{fid}/edit").data.decode()
        assert "Confirmed by Jan Peeters" in html
        assert 'id="crew_name_1"' in html and "readonly" in html

        with patch(DISPATCH) as mock_dispatch:
            resp = client.post(
                f"/flights/{fid}/edit",
                data=_flight_form(
                    pilot_role="pic",
                    crew_name_0="Kris Logger",
                    crew_name_1="Someone Else",
                    crew_role_1=CrewRole.STUDENT,
                    # The unified form derives flight time from these.
                    takeoff_time="10:00",
                    landing_time="12:00",
                ),
            )
        assert resp.status_code == 302
        fe = _flight(app, fid)
        assert fe.flight_time == Decimal("2.0")
        assert fe.second_crew_name == "Jan Peeters"
        assert fe.second_crew_role == CrewRole.IP
        assert fe.second_crew_remarks == "Jan's own note"
        # Jan's hours matched the old flight time, so they follow the new one.
        assert fe.function_instructor == Decimal("2.0")
        assert fe.function_dual is None
        sent = _dispatched(mock_dispatch, NotificationType.SHARED_FLIGHT_CHANGED)
        assert len(sent) == 1
        assert sent[0].args[1] == tid
        assert sent[0].kwargs["target_user_ids"] == [jan]
        details = {str(k): v for k, v in sent[0].args[2]["details"]}
        assert details["Flight time"] == "1.5 → 2.0"

    def test_custom_function_hours_do_not_follow(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(
            app,
            kris,
            jan,
            flight_time=Decimal("1.5"),
            function_instructor=Decimal("0.7"),
        )
        _login(client, kris)
        with patch(DISPATCH):
            client.post(
                f"/flights/{fid}/edit",
                data=_flight_form(
                    pilot_role="pic",
                    crew_name_0="Kris Logger",
                    crew_name_1="Jan Peeters",
                    takeoff_time="10:00",
                    landing_time="12:00",
                ),
            )
        assert _flight(app, fid).flight_time == Decimal("2.0")
        assert _flight(app, fid).function_instructor == Decimal("0.7")

    def test_logger_cannot_take_the_confirmed_pilots_slot(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan)
        _login(client, kris)
        resp = client.post(
            f"/flights/{fid}/edit",
            data=_flight_form(pilot_role="dual", crew_name_0="Kris Logger"),
        )
        assert resp.status_code == 200
        assert b"already confirmed by Jan Peeters" in resp.data
        assert _flight(app, fid).second_crew_user_id == jan

    def test_standalone_entry_form_keeps_other_pilot_fields(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan, single_pilot_se=Decimal("1.5"))
        _login(client, kris)
        with patch(DISPATCH) as mock_dispatch:
            resp = client.post(
                f"/pilot/logbook/{fid}/edit",
                data={
                    "date": "2026-09-02",
                    "aircraft_type": "Cessna C172",
                    "aircraft_registration": "OO-TST",
                    "departure_place": "EBAW",
                    "arrival_place": "EBOS",
                    "pic_name": "Kris Logger",
                    "single_pilot_se": "1.5",
                    "function_pic": "1.5",
                    "function_instructor": "",
                    "remarks": "Logger remark",
                },
            )
        assert resp.status_code == 302
        fe = _flight(app, fid)
        assert fe.date == date(2026, 9, 2)
        assert fe.notes == "Logger remark"
        assert fe.function_instructor == Decimal("1.5")
        assert (
            len(_dispatched(mock_dispatch, NotificationType.SHARED_FLIGHT_CHANGED)) == 1
        )

    def test_offline_sync_by_logger_keeps_other_pilot_fields(self, app, client):
        _tid, kris, jan, _els, _owner, acid = _world_with_aircraft(app)
        managed = _shared(app, kris, jan, aircraft_id=acid)
        standalone = _shared(app, kris, jan)
        with app.app_context():
            base = canonical_entry(db.session.get(Flight, managed))
            pbase = canonical_pilot_entry(db.session.get(Flight, standalone))
        _login(client, kris)
        with patch(DISPATCH) as mock_dispatch:
            resp = client.post(
                f"/api/offline/flights/{managed}/sync",
                json={
                    "fields": {**base, "notes": "Offline note", "crew_name_1": "X"},
                    "base": base,
                },
            )
            assert resp.status_code == 200, resp.get_json()
            resp = client.post(
                f"/api/offline/pilot/logbook/{standalone}/sync",
                json={
                    "fields": {
                        **pbase,
                        "remarks": "Offline",
                        "function_instructor": "",
                    },
                    "base": pbase,
                },
            )
            assert resp.status_code == 200, resp.get_json()
        assert _flight(app, managed).notes == "Offline note"
        assert _flight(app, managed).second_crew_name == "Jan Peeters"
        assert _flight(app, standalone).function_instructor == Decimal("1.5")
        assert (
            len(_dispatched(mock_dispatch, NotificationType.SHARED_FLIGHT_CHANGED)) == 2
        )

    def test_saving_without_changes_sends_nothing(self, app):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan)
        with app.test_request_context(), patch(DISPATCH) as mock_dispatch:
            from flights.shared_flight import notify_shared_changes, shared_snapshot

            fe = db.session.get(Flight, fid)
            notify_shared_changes(fe, kris, shared_snapshot(fe))
        mock_dispatch.assert_not_called()


# ── "My part of this flight" ─────────────────────────────────────────────────


class TestCrewEntryPage:
    def test_only_linked_pilots_can_open_it(self, app, client):
        _tid, kris, jan, els = _world(app)
        fid = _shared(app, kris, jan)
        _login(client, els)
        assert client.get(f"/flights/{fid}/my-part").status_code == 404
        assert client.get("/flights/999999/my-part").status_code == 404

    def test_page_for_logger_and_for_confirmed_pilot(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan)
        _login(client, kris)
        html = client.get(f"/flights/{fid}/my-part").data.decode()
        assert "Edit flight details" in html
        assert "Suggest a correction" not in html
        _login(client, jan)
        html = client.get(f"/flights/{fid}/my-part").data.decode()
        assert "Kris Logger logged this flight" in html
        assert "Suggest a correction" in html
        assert "Jan&#39;s own note" in html

    def test_save_personal_fields_second_slot(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan)
        _login(client, jan)
        resp = client.post(
            f"/flights/{fid}/my-part",
            data={
                "action": "personal",
                "name": "Jan P.",
                "role": CrewRole.COPILOT,
                "function_hours": "1.2",
                "remark": "Crosswind landing",
            },
            follow_redirects=True,
        )
        assert b"Your part of this flight was saved." in resp.data
        fe = _flight(app, fid)
        assert fe.second_crew_name == "Jan P."
        assert fe.second_crew_role == CrewRole.COPILOT
        assert fe.function_copilot == Decimal("1.2")
        assert fe.function_instructor is None
        assert fe.second_crew_remarks == "Crosswind landing"
        # Kris's part untouched.
        assert (fe.pic_name, fe.function_pic) == ("Kris Logger", Decimal("1.5"))

        client.post(
            f"/flights/{fid}/my-part",
            data={"action": "personal", "name": "Jan P.", "role": CrewRole.SP},
        )
        fe = _flight(app, fid)
        assert fe.second_crew_role == CrewRole.SP
        assert fe.function_copilot is None
        assert fe.second_crew_remarks is None

    def test_save_personal_fields_pic_slot(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan)
        _login(client, kris)
        client.post(
            f"/flights/{fid}/my-part",
            data={
                "action": "personal",
                "name": "K. Logger",
                "function_hours": "1.4",
                "remark": "Mine",
            },
        )
        fe = _flight(app, fid)
        assert (fe.pic_name, fe.function_pic, fe.pic_remarks) == (
            "K. Logger",
            Decimal("1.4"),
            "Mine",
        )
        assert fe.personal_remark_for(kris) == "Mine"
        assert fe.personal_remark_for(jan) == "Jan's own note"
        assert fe.personal_remark_for(None) is None

    def test_personal_fields_validation(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan)
        _login(client, jan)
        resp = client.post(
            f"/flights/{fid}/my-part",
            data={"action": "personal", "name": "", "function_hours": "abc"},
        )
        html = resp.data.decode()
        assert "Name is required." in html
        assert "must be a number" in html
        assert "Please choose your role on this flight." in html
        assert _flight(app, fid).second_crew_name == "Jan Peeters"

    def test_bad_action_is_rejected(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan)
        _login(client, kris)
        # The logger has nothing to suggest: they edit the flight directly.
        assert (
            client.post(
                f"/flights/{fid}/my-part", data={"action": "suggest"}
            ).status_code
            == 400
        )
        assert client.post(f"/flights/{fid}/my-part", data={}).status_code == 400


# ── Correction suggestions ───────────────────────────────────────────────────


def _suggest(client, fid, **values):
    return client.post(
        f"/flights/{fid}/my-part",
        data={"action": "suggest", **values},
        follow_redirects=True,
    )


def _standalone_suggest_form(**overrides):
    data = {
        "date": "2026-09-01",
        "other_aircraft_registration": "OO-TST",
        "other_aircraft_type": "Cessna C172",
        "departure_icao": "EBAW",
        "arrival_icao": "EBOS",
        "flight_time": "1.5",
    }
    data.update(overrides)
    return data


class TestCorrectionSuggestions:
    def test_suggest_then_accept(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(
            app, kris, jan, flight_time=Decimal("1.5"), single_pilot_se=Decimal("1.5")
        )
        _login(client, jan)
        with patch(DISPATCH) as mock_dispatch:
            resp = _suggest(
                client,
                fid,
                **_standalone_suggest_form(
                    arrival_icao="EBKT", flight_time="1.8", landings_day="2"
                ),
            )
        assert b"Correction sent." in resp.data
        assert b"Your corrections waiting for review" in resp.data
        sent = _dispatched(mock_dispatch, NotificationType.FLIGHT_CORRECTION)
        assert [c.kwargs["target_user_ids"] for c in sent] == [[kris]]
        with app.app_context():
            s = FlightCorrectionSuggestion.query.one()
            assert s.changes == {
                "arrival_icao": ["EBOS", "EBKT"],
                "flight_time": ["1.5", "1.8"],
                "landings_day": ["", "2"],
            }
            sid = s.id
        assert _flight(app, fid).arrival_icao == "EBOS"  # nothing applied yet

        _login(client, kris)
        for url in ("/", "/pilot/logbook"):
            html = client.get(url).data.decode()
            assert "Suggested corrections to your flights" in html
            assert f"/flights/corrections/{sid}/accept" in html
            assert "Arrival" in html

        with patch(DISPATCH) as mock_dispatch:
            resp = client.post(
                f"/flights/corrections/{sid}/accept",
                data={"next": "dashboard"},
            )
        assert resp.headers["Location"].endswith("/#flight-corrections")
        fe = _flight(app, fid)
        assert fe.arrival_icao == "EBKT"
        assert fe.flight_time == Decimal("1.8")
        assert fe.single_pilot_se == Decimal("1.8")
        assert fe.landings_day == 2
        # Both pilots' hours followed the corrected flight time.
        assert fe.function_pic == Decimal("1.8")
        assert fe.function_instructor == Decimal("1.8")
        changed = _dispatched(mock_dispatch, NotificationType.SHARED_FLIGHT_CHANGED)
        assert [c.kwargs["target_user_ids"] for c in changed] == [[jan]]
        with app.app_context():
            assert (
                db.session.get(FlightCorrectionSuggestion, sid).status
                == CorrectionStatus.ACCEPTED
            )

        resp = client.post(f"/flights/corrections/{sid}/accept", follow_redirects=True)
        assert b"already been handled" in resp.data

    def test_reject_notifies_suggester(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan, flight_time=Decimal("1.5"))
        _login(client, jan)
        with patch(DISPATCH):
            _suggest(client, fid, **_standalone_suggest_form(departure_icao="EBBR"))
        with app.app_context():
            sid = FlightCorrectionSuggestion.query.one().id
        _login(client, kris)
        with patch(DISPATCH) as mock_dispatch:
            resp = client.post(f"/flights/corrections/{sid}/reject")
        assert resp.headers["Location"].endswith("/pilot/logbook#flight-corrections")
        sent = _dispatched(mock_dispatch, NotificationType.FLIGHT_CORRECTION)
        assert [c.kwargs["target_user_ids"] for c in sent] == [[jan]]
        assert _flight(app, fid).departure_icao == "EBAW"
        resp = client.post(f"/flights/corrections/{sid}/reject", follow_redirects=True)
        assert b"already been handled" in resp.data

    def test_only_the_logger_reviews(self, app, client):
        _tid, kris, jan, els = _world(app)
        fid = _shared(app, kris, jan)
        with app.app_context():
            s = FlightCorrectionSuggestion(
                flight_id=fid, suggested_by_user_id=jan, changes={"notes": ["", "x"]}
            )
            db.session.add(s)
            db.session.commit()
            sid = s.id
        for uid in (jan, els):
            _login(client, uid)
            for action in ("accept", "reject"):
                assert (
                    client.post(f"/flights/corrections/{sid}/{action}").status_code
                    == 404
                )
        assert client.post("/flights/corrections/999999/accept").status_code == 404

    def test_suggestion_validation(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan, flight_time=Decimal("1.5"))
        _login(client, jan)
        resp = _suggest(client, fid, **_standalone_suggest_form())
        assert b"Nothing to suggest" in resp.data
        resp = _suggest(
            client, fid, **_standalone_suggest_form(date="", departure_time="25:99")
        )
        assert b"Date is required." in resp.data
        assert b"enter a valid HH:MM time" in resp.data
        resp = _suggest(client, fid, **_standalone_suggest_form(date="not-a-date"))
        assert b"enter a valid date" in resp.data
        with app.app_context():
            assert FlightCorrectionSuggestion.query.count() == 0

    def test_managed_aircraft_fields_and_landing_count(self, app, client):
        _tid, kris, jan, _els, _owner, acid = _world_with_aircraft(app)
        fid = _shared(app, kris, jan, aircraft_id=acid, landing_count=1, landings_day=1)
        _login(client, jan)
        html = client.get(f"/flights/{fid}/my-part").data.decode()
        assert 'name="landings_night"' in html
        assert 'name="departure_time"' not in html
        with patch(DISPATCH):
            _suggest(
                client,
                fid,
                date="2026-09-01",
                departure_icao="EBAW",
                arrival_icao="EBOS",
                landings_day="1",
                landings_night="2",
            )
        with app.app_context():
            sid = FlightCorrectionSuggestion.query.one().id
        _login(client, kris)
        with patch(DISPATCH):
            client.post(f"/flights/corrections/{sid}/accept")
        fe = _flight(app, fid)
        assert (fe.landings_night, fe.landing_count) == (2, 3)

    def test_apply_suggestion_details(self, app):
        _tid, kris, jan, _els, _owner, acid = _world_with_aircraft(app)
        me_flight = _shared(
            app,
            kris,
            jan,
            flight_time=Decimal("1.5"),
            single_pilot_me=Decimal("1.5"),
        )
        no_ft = _shared(
            app, kris, jan, flight_time=None, single_pilot_se=Decimal("1.0")
        )
        moved = _shared(app, kris, jan, aircraft_id=acid)
        with app.app_context():
            from flights.shared_flight import (
                apply_suggestion,
                field_label,
                suggestable_fields,
            )

            assert "single_pilot_se" in suggestable_fields(
                db.session.get(Flight, no_ft)
            )
            assert "flight_time" not in suggestable_fields(
                db.session.get(Flight, no_ft)
            )
            assert field_label("unknown_field") == "unknown_field"
            s1 = FlightCorrectionSuggestion(
                flight_id=me_flight,
                suggested_by_user_id=jan,
                changes={"flight_time": ["1.5", "2.0"]},
            )
            # Suggested while standalone, but the flight is now on a managed
            # aircraft: the registration is no longer a suggestable field.
            s2 = FlightCorrectionSuggestion(
                flight_id=moved,
                suggested_by_user_id=jan,
                changes={
                    "other_aircraft_registration": ["OO-TST", "OO-NEW"],
                    "notes": ["", "fixed"],
                },
            )
            db.session.add_all([s1, s2])
            db.session.flush()
            apply_suggestion(s1, kris)
            apply_suggestion(s2, kris)
            db.session.commit()
        fe = _flight(app, me_flight)
        assert (fe.single_pilot_me, fe.single_pilot_se) == (Decimal("2.0"), None)
        fe = _flight(app, moved)
        assert fe.notes == "fixed"
        assert fe.other_aircraft_registration == "OO-TST"

    def test_nav_badge_counts_invites_and_corrections(self, app, client):
        _tid, kris, jan, els = _world(app)
        fid = _shared(app, kris, jan)
        other = _add_flight(app, pic_user_id=els, created_by_user_id=els)
        _add_invite(app, other, CrewSlot.SECOND, kris, els)
        with app.app_context():
            db.session.add(
                FlightCorrectionSuggestion(
                    flight_id=fid,
                    suggested_by_user_id=jan,
                    changes={"notes": ["", "x"]},
                )
            )
            db.session.commit()
        _login(client, kris)
        html = client.get("/pilot/logbook").data.decode()
        assert (
            '<span class="badge rounded-pill bg-warning text-dark ms-1 oh-fs-07" '
            'title="Flights waiting for your confirmation">2</span>'
        ) in html


# ── Logbook display of a shared flight ───────────────────────────────────────


class TestSharedFlightDisplay:
    def test_each_pilot_sees_only_their_own_hours_and_remark(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan, function_pic=Decimal("1.3"), notes="Shared note")
        _login(client, jan)
        html = client.get("/pilot/logbook").data.decode()
        assert "1.3" not in html  # Kris's PIC hours
        assert "Shared note · Jan&#39;s own note" in html
        assert f"/flights/{fid}/my-part" in html
        detail = client.get(f"/pilot/logbook/{fid}/view").data.decode()
        assert "Your personal remark" in detail
        assert "My part of this flight" in detail
        with app.app_context():
            fe = db.session.get(Flight, fid)
            assert fe.visible_function_fields(kris) == {"function_pic"}
            assert fe.visible_function_fields(jan) == {
                "function_copilot",
                "function_dual",
                "function_instructor",
            }
            assert len(fe.visible_function_fields(None)) == 4


# ── Invite answers and logger tracking ───────────────────────────────────────


class TestInviteAnswersAndLogger:
    def test_inviter_hears_back(self, app, client):
        tid, kris, jan, els = _world(app)
        fid = _add_flight(app, pic_user_id=kris, created_by_user_id=kris)
        accept_id = _add_invite(app, fid, CrewSlot.SECOND, jan, kris)
        other = _add_flight(app, pic_user_id=kris, created_by_user_id=kris)
        decline_id = _add_invite(app, other, CrewSlot.SECOND, els, kris)
        nobody = _add_flight(app, pic_user_id=kris)
        orphan_id = _add_invite(app, nobody, CrewSlot.SECOND, els, None)

        with patch(DISPATCH) as mock_dispatch:
            _login(client, jan)
            client.post(f"/flights/crew-invites/{accept_id}/accept")
            _login(client, els)
            client.post(f"/flights/crew-invites/{decline_id}/decline")
            client.post(f"/flights/crew-invites/{orphan_id}/decline")
        answered = _dispatched(mock_dispatch, NotificationType.CREW_INVITE_ANSWERED)
        assert [c.kwargs["target_user_ids"] for c in answered] == [[kris], [kris]]
        assert answered[0].args[1] == tid
        assert "confirmed" in str(answered[0].args[2]["notification_title_key"])
        assert "declined" in str(answered[1].args[2]["notification_title_key"])

    def test_first_invite_makes_the_inviter_the_logger(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _add_flight(app, pic_user_id=kris, pic_name="Kris Logger")
        _login(client, kris)
        with patch(DISPATCH):
            client.post(
                f"/flights/{fid}/edit",
                data=_flight_form(
                    pilot_role="pic",
                    crew_name_0="Kris Logger",
                    crew_name_1="Jan Peeters",
                    crew_user_id_1=str(jan),
                    crew_role_1=CrewRole.IP,
                ),
            )
        assert _flight(app, fid).created_by_user_id == kris
        with app.app_context():
            assert FlightCrewInvite.query.count() == 1

    def test_new_flight_records_its_logger(self, app, client):
        _tid, kris, _jan, _els = _world(app)
        _login(client, kris)
        client.post(
            "/flights/new", data=_flight_form(pilot_role="pic", crew_name_0="K")
        )
        with app.app_context():
            assert Flight.query.one().created_by_user_id == kris

    def test_removing_yourself_clears_your_remark(self, app):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan, pic_remarks="Kris's note")
        with app.app_context():
            from flights.crew_removal import unlink_user

            fe = db.session.get(Flight, fid)
            unlink_user(fe, kris)
            unlink_user(fe, jan)
            assert (fe.pic_remarks, fe.second_crew_remarks) == (None, None)


# ── Notification delivery ────────────────────────────────────────────────────


class TestNotificationDelivery:
    def test_change_email_renders(self, app, client):
        _tid, kris, jan, _els = _world(app)
        fid = _shared(app, kris, jan, flight_time=Decimal("1.5"))
        _login(client, kris)
        with (
            patch("services.email_service.send_email") as mock_send,
            patch("services.email_service._record_health"),
        ):
            client.post(
                f"/flights/{fid}/edit",
                data=_flight_form(
                    pilot_role="pic",
                    crew_name_0="Kris Logger",
                    crew_name_1="Jan Peeters",
                    arrival_icao="EBKT",
                ),
            )
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["to"] == "jan@example.com"
        assert "Kris Logger updated a flight you&#39;re on" in kwargs["html_body"]
        assert "EBOS → EBKT" in kwargs["text_body"]

    def test_user_without_tenant_or_failing_dispatch_is_skipped(self, app):
        _tid, kris, _jan, _els = _world(app)
        with app.app_context():
            from flights.shared_flight import dispatch_to_user
            from models import TenantUser

            TenantUser.query.filter_by(user_id=kris).delete()
            db.session.commit()
            with patch(DISPATCH) as mock_dispatch:
                dispatch_to_user(kris, NotificationType.SHARED_FLIGHT_CHANGED, {})
            mock_dispatch.assert_not_called()

        _tid2, kris2, _jan2, _els2 = _world_again(app)
        with app.app_context():
            from flights.shared_flight import dispatch_to_user

            with patch(DISPATCH, side_effect=RuntimeError("smtp down")):
                dispatch_to_user(kris2, NotificationType.SHARED_FLIGHT_CHANGED, {})

    def test_notification_types_listed_in_preferences(self, app, client):
        _tid, kris, _jan, _els = _world(app)
        _login(client, kris)
        html = client.get("/config/notifications/").data.decode()
        for label in (
            "Flight crew confirmation answered",
            "Shared flight changed",
            "Flight correction suggestions",
        ):
            assert label in html


def _world_again(app):
    """A second, independent tenant + pilots (distinct e-mails)."""
    from tests.test_crew_invites import (
        _make_tenant,  # pyright: ignore[reportMissingImports]
    )

    tid = _make_tenant(app, "Second Hangar")
    kris = _make_user(app, tid, "kris2@example.com", "Kris Two")
    jan = _make_user(app, tid, "jan2@example.com", "Jan Two")
    els = _make_user(app, tid, "els2@example.com", "Els Two")
    return tid, kris, jan, els
