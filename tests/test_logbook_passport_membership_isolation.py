"""A pilot's passport-only membership in another organisation never exposes
or receives that organisation's aircraft flights through the logbook."""

from datetime import date

import pytest
from flask import Flask
from models import Aircraft, Flight, Tenant, TenantUser, db
from pilots.logbook_import import (
    find_conflicting_rows,
    full_tenant_ids,
    link_entries_to_aircraft,
)

from tests.test_logbook_import import _make_parsed_file, _make_user


@pytest.mark.parametrize("scope", ["personal", "logbook"])
def test_restricted_membership_aircraft_stay_out_of_the_logbook(
    app: Flask, scope: str
) -> None:
    with app.app_context():
        uid = _make_user(f"two-orgs-{scope}@example.com")
        home = TenantUser.query.filter_by(user_id=uid).one().tenant_id
        club = Tenant(name="Friends' club")
        db.session.add(club)
        db.session.flush()
        db.session.add(
            TenantUser(user_id=uid, tenant_id=club.id, role=None, access_scope=scope)
        )
        aircraft = Aircraft(
            tenant_id=club.id, registration="OO-CLB", make="Robin", model="DR400"
        )
        db.session.add(aircraft)
        db.session.flush()
        # A crew-less club flight the pilot has no access to.
        club_flight = Flight(
            aircraft_id=aircraft.id,
            date=date(2024, 3, 15),
            departure_icao="EBNM",
            arrival_icao="EBSP",
            single_pilot_se=1.0,
            landings_day=1,
        )
        own = Flight(
            pic_user_id=uid,
            date=date(2024, 3, 16),
            other_aircraft_registration="OO-CLB",
        )
        db.session.add_all([club_flight, own])
        db.session.commit()
        assert full_tenant_ids(uid) == {home}
        # The club's flight is never offered as a match for an imported row...
        parsed = _make_parsed_file(
            ["date", "reg", "se", "day"], [["15/03/24", "OO-CLB", "1.0", "1"]]
        )
        mapping = {
            "date": "date",
            "reg": "aircraft_registration",
            "se": "single_pilot_se",
            "day": "landings_day",
        }
        assert find_conflicting_rows(parsed, mapping, uid) == []
        # ...and the pilot's own entry is never moved into the club's aircraft.
        assert link_entries_to_aircraft([own]) == 0
        assert own.aircraft_id is None
        assert Flight.query.filter_by(aircraft_id=aircraft.id).count() == 1


def test_full_membership_still_matches_its_aircraft(app: Flask) -> None:
    with app.app_context():
        uid = _make_user("one-org@example.com")
        home = TenantUser.query.filter_by(user_id=uid).one().tenant_id
        aircraft = Aircraft(
            tenant_id=home, registration="OO-OWN", make="Robin", model="DR400"
        )
        db.session.add(aircraft)
        db.session.flush()
        own = Flight(
            pic_user_id=uid,
            date=date(2024, 3, 16),
            other_aircraft_registration="OO-OWN",
        )
        db.session.add(own)
        db.session.commit()
        assert link_entries_to_aircraft([own]) == 1
        assert own.aircraft_id == aircraft.id
