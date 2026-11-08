"""The aircraft GPS import and the crew of flights already in the logbook.

GPS data adds a track; the logbook names the crew. Confirming a segment on
an existing flight with "PIC" (or "dual") only adds the importing user when
that crew slot is free — or already carries their own name — never over the
pilot logged on it (e.g. a previous owner's flights). The review page
preselects the role the matched flights suggest.
"""

import re

import pytest
from models import Flight, User, db  # pyright: ignore[reportMissingImports]

from tests.test_gps_import import (  # pyright: ignore[reportMissingImports]
    _login,
    _make_user_and_aircraft,
)
from tests.test_gps_multi_file_tracks import (  # pyright: ignore[reportMissingImports]
    _logged_flight,
    _upload,
)


def _world(client, app, **crew):
    """An imported GPS segment matching a logged flight with *crew*."""
    uid, _tid, ac_id = _make_user_and_aircraft(app)
    _login(client, uid)
    (seg,), _ = _upload(client, ac_id)
    fid = _logged_flight(app, ac_id, seg)
    with app.app_context():
        f = db.session.get(Flight, fid)
        for key, value in crew.items():
            setattr(f, key, uid if value == "me" else value)
        f.landings_day = 2
        db.session.commit()
    return uid, ac_id, fid


def _other_user(app):
    with app.app_context():
        other = User(
            email="previous-owner@example.com", password_hash="x", is_active=True
        )
        db.session.add(other)
        db.session.commit()
        return other.id


def _confirm(client, ac_id, role):
    _upload(client, ac_id)
    return client.post(
        f"/aircraft/{ac_id}/gps-import/confirm-one",
        data={"seg_idx": "0", "pilot_role": role},
    )


def _checked_role(page):
    return re.search(r'value="(\w+)" checked>', page).group(1)


class TestConfirmOnExistingFlight:
    def test_named_pic_is_kept(self, client, app):
        _uid, ac_id, fid = _world(client, app, pic_name="Previous Owner")
        _confirm(client, ac_id, "pic")
        with app.app_context():
            f = db.session.get(Flight, fid)
            assert f.gps_track_id is not None
            assert f.pic_user_id is None and f.pic_name == "Previous Owner"
            assert f.function_pic is None and f.landings_day == 2

    def test_another_users_flight_is_kept(self, client, app):
        _uid, ac_id, fid = _world(client, app)
        other = _other_user(app)
        with app.app_context():
            db.session.get(Flight, fid).pic_user_id = other
            db.session.commit()
        _confirm(client, ac_id, "pic")
        with app.app_context():
            assert db.session.get(Flight, fid).pic_user_id == other

    def test_already_on_it_stays_as_logged(self, client, app):
        uid, ac_id, fid = _world(client, app, second_crew_user_id="me")
        _confirm(client, ac_id, "pic")
        with app.app_context():
            f = db.session.get(Flight, fid)
            assert f.pic_user_id is None and f.second_crew_user_id == uid

    @pytest.mark.parametrize(
        ("role", "crew", "slot"),
        [
            ("pic", {}, "pic_user_id"),
            ("pic", {"pic_name": " GPS "}, "pic_user_id"),  # their own name
            ("dual", {"pic_name": "Instructor"}, "second_crew_user_id"),
        ],
    )
    def test_free_slot_is_filled(self, client, app, role, crew, slot):
        uid, ac_id, fid = _world(client, app, **crew)
        with app.app_context():
            db.session.get(User, uid).name = "gps"
            db.session.commit()
        _confirm(client, ac_id, role)
        with app.app_context():
            f = db.session.get(Flight, fid)
            assert getattr(f, slot) == uid
            assert f.landings_day == 2  # the logged landings stay


class TestDefaultRole:
    def test_someone_elses_flight_defaults_to_not_flying(self, client, app):
        _uid, ac_id, _fid = _world(client, app, pic_name="Previous Owner")
        _upload(client, ac_id)
        page = client.get(f"/aircraft/{ac_id}/gps-import/review").data.decode()
        assert _checked_role(page) == "none"
        assert "Logged with Previous Owner as PIC." in page
        assert 'class="pilot-role-hidden" value="none"' in page

    @pytest.mark.parametrize(
        ("crew", "expected"),
        [({"pic_user_id": "me"}, "pic"), ({"second_crew_user_id": "me"}, "dual")],
    )
    def test_your_own_flights_default_to_your_role(self, client, app, crew, expected):
        _uid, ac_id, _fid = _world(client, app, **crew)
        _upload(client, ac_id)
        page = client.get(f"/aircraft/{ac_id}/gps-import/review").data.decode()
        assert _checked_role(page) == expected
        assert "Already in your pilot logbook" in page

    def test_a_new_flight_defaults_to_pic(self, client, app):
        uid, _tid, ac_id = _make_user_and_aircraft(app)
        _login(client, uid)
        _upload(client, ac_id)
        page = client.get(f"/aircraft/{ac_id}/gps-import/review").data.decode()
        assert _checked_role(page) == "pic"

    def test_helper_cases(self):
        from aircraft.routes import _default_pilot_role

        assert _default_pilot_role([], other_aircraft=True) == "pic"
        ground = {"matched_flight_id": None, "is_ground_only": True}
        assert _default_pilot_role([ground], other_aircraft=False) == "none"
