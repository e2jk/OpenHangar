"""Shared fixtures for the functional (intent-based) journey test suite.

See docs/functional_test_plan.md for the plan these tests implement.
Journeys drive the product through real HTTP requests (POST forms,
follow redirects) rather than direct model writes, so route-level side
effects (milestones, prefills, derived fields) are exercised the same
way a real user's actions would trigger them. Existing `app`/`client`/
`clean_db` fixtures (tests/conftest.py) are inherited unchanged.
"""

import re
from dataclasses import dataclass

import pytest  # pyright: ignore[reportMissingImports]


@dataclass
class OwnerEnv:
    client: object
    tenant_id: int
    aircraft_id: int
    email: str
    password: str


def submit(client, url, data, expect_error=False, **kwargs):
    """POST `data` to `url`, follow redirects, and assert the outcome.

    This is the workhorse for every journey step: most product bugs this
    plan targets ("the form silently 200s but saves nothing") show up as
    an unexpected `alert-danger` flash (or its absence when one was
    expected), not as a non-200 status.
    """
    resp = client.post(url, data=data, follow_redirects=True, **kwargs)
    assert resp.status_code == 200, f"POST {url} -> {resp.status_code}"
    has_error = b"alert-danger" in resp.data
    if expect_error:
        assert has_error, f"POST {url}: expected an error flash, got none"
    else:
        assert not has_error, (
            f"POST {url}: unexpected error flash in response:\n"
            f"{resp.data.decode('utf-8', 'replace')[:2000]}"
        )
    return resp


@pytest.fixture
def owner_env(client, app):
    """Fresh instance via the setup wizard: `sole_operator` operating model,
    logged in as the admin, with one aircraft carrying an engine component.
    """
    email = "owner@example.com"
    password = "SuperSecret123!"

    submit(
        client,
        "/setup",
        {"step": "account", "email": email, "password": password, "name": "Owner"},
    )
    submit(client, "/setup", {"step": "totp", "action": "skip"})
    submit(
        client,
        "/setup",
        {"step": "operating_model", "operating_model": "sole_operator"},
    )
    submit(client, "/setup", {"step": "aircraft_count", "aircraft_count": "1"})

    resp = client.post(
        "/aircraft/new",
        data={
            "registration": "OO-TST",
            "make": "Cessna",
            "model": "172S",
            "year": "2010",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.status_code
    match = re.search(r"/aircraft/(\d+)", resp.headers["Location"])
    assert match, resp.headers["Location"]
    aircraft_id = int(match.group(1))

    submit(
        client,
        f"/aircraft/{aircraft_id}/components/new",
        {
            "type": "engine",
            "make": "Lycoming",
            "model": "IO-360-L2A",
            "serial_number": "L-00001",
            "time_at_install": "1000.0",
            "installed_at": "2020-01-01",
        },
    )

    with app.app_context():
        from models import TenantUser, User  # pyright: ignore[reportMissingImports]

        user = User.query.filter_by(email=email).first()
        tenant_user = TenantUser.query.filter_by(user_id=user.id).first()
        tenant_id = tenant_user.tenant_id

    return OwnerEnv(
        client=client,
        tenant_id=tenant_id,
        aircraft_id=aircraft_id,
        email=email,
        password=password,
    )


def second_user(app, client_factory, tenant_admin_client, role, email, display_name):
    """Invite + accept a second user for `tenant_admin_client`'s tenant,
    returning a *fresh* logged-in client (two clients = two concurrent
    sessions; never share one client between actors in a journey).

    `client_factory` is `app.test_client` — pass it explicitly since the
    invitee needs their own client instance, not the admin's.
    """
    submit(
        tenant_admin_client,
        "/config/users/invite",
        {
            "email": [email],
            "display_name": [display_name],
            "role": [role],
            "aircraft_ids": [""],
        },
    )

    with app.app_context():
        from models import UserInvitation  # pyright: ignore[reportMissingImports]

        invitation = UserInvitation.query.filter_by(email=email).first()
        assert invitation is not None, f"no invitation created for {email}"
        token = invitation.token

    new_client = client_factory()
    password = "SecondUserPass1!"
    submit(
        new_client,
        f"/config/users/invite/{token}",
        {"email": email, "password": password, "password2": password},
    )
    submit(new_client, "/login", {"email": email, "password": password})
    return new_client


@pytest.fixture
def client_factory(app):
    """Factory for additional independent test clients (second_user etc.)."""
    return app.test_client


def log_flight(client, app, aircraft_id, **fields):
    """POST the real "Log a flight" form; returns the created FlightEntry id.

    The redirect doesn't echo the new id (see docs/functional_test_plan.md's
    research notes), so this looks it up directly afterwards — a sanctioned
    direct read (not a write) purely to hand the id back to the caller.
    """
    from models import FlightEntry  # pyright: ignore[reportMissingImports]

    data = {
        "aircraft_id": str(aircraft_id),
        "date": "2024-06-01",
        "departure_icao": "EBOS",
        "arrival_icao": "EBBR",
        "crew_name_0": "Test Pilot",
        "crew_role_0": "PIC",
        "pilot_role": "pic",
    }
    data.update(fields)
    submit(client, "/flights/new", data)

    with app.app_context():
        fe = (
            FlightEntry.query.filter_by(aircraft_id=aircraft_id)
            .order_by(FlightEntry.id.desc())
            .first()
        )
        assert fe is not None, "log_flight: no FlightEntry was created"
        return fe.id


def edit_flight(client, flight_id, aircraft_id, **fields):
    """POST the real flight-edit form for an existing FlightEntry.

    A real edit submits the *whole* form (browsers resubmit every field,
    prefilled), so this needs the same required fields as `log_flight`,
    not just the ones being changed.
    """
    data = {
        "aircraft_id": str(aircraft_id),
        "date": "2024-06-01",
        "departure_icao": "EBOS",
        "arrival_icao": "EBBR",
        "crew_name_0": "Test Pilot",
        "crew_role_0": "PIC",
        "pilot_role": "pic",
    }
    data.update(fields)
    return submit(client, f"/flights/{flight_id}/edit", data)
