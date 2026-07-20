"""J16 — Share-link lifecycle (docs/functional_test_plan.md).

Intent: create a share link -> an anonymous client sees exactly the
shared scope (and nothing else -- probe sibling objects) -> revoke ->
404.

Existing: test_share.py covers each route in isolation (create/revoke/
public_view/token_qr, access_level content differences) exhaustively,
but always against a single aircraft and never as one create-view-revoke
chain through real routes; more importantly, no existing test creates a
*second* aircraft/tenant-wide resource and confirms an anonymous
visitor holding a valid token for aircraft A can't reach it. That
negative-scope probe is what this journey adds.
"""

import re

from tests.functional.conftest import submit


def test_share_link_scope_and_revocation(owner_env, app, client_factory):
    owner = owner_env.client
    aircraft_id = owner_env.aircraft_id

    # A sibling aircraft in the same tenant -- no token issued for this one.
    resp = owner.post(
        "/aircraft/new",
        data={"registration": "OO-SIB", "make": "Piper", "model": "PA-28"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Redirect lands on /aircraft/<registration> (AircraftRefConverter) —
    # look the row up directly rather than depending on the URL's shape.
    with app.app_context():
        from models import Aircraft  # pyright: ignore[reportMissingImports]

        other_aircraft_id = Aircraft.query.filter_by(registration="OO-SIB").first().id

    # Create a "full" share link for the first aircraft through the real form.
    create_resp = submit(
        owner,
        f"/aircraft/{aircraft_id}/share/create",
        {"access_level": "full"},
    )
    # The rendered link is /share/<registration>/<token> — take the segment
    # right before the closing quote, which is always the token.
    token_match = re.search(
        rb'/share/[A-Za-z0-9_-]+/([A-Za-z0-9_-]+)"', create_resp.data
    )
    assert token_match, "share token not found in aircraft detail page"
    token = token_match.group(1).decode()

    anon = client_factory()

    # Anonymous client sees exactly the shared aircraft's scope.
    shared_resp = anon.get(f"/share/{token}")
    assert shared_resp.status_code == 200
    assert b"OO-TST" in shared_resp.data
    assert b"OO-SIB" not in shared_resp.data

    # Sibling probes: nothing else on the app surface is reachable through
    # a valid share token -- these all bounce to login, not 404, and not
    # the sibling's own data.
    assert anon.get(f"/aircraft/{other_aircraft_id}").status_code in (302, 308)
    assert anon.get(f"/aircraft/{aircraft_id}").status_code in (
        302,
        308,
    )  # the real owner-facing page, not /share/<token>
    assert anon.get("/aircraft/").status_code in (302, 308)
    assert anon.get("/config/").status_code in (302, 308)

    # Revoke, then the same link 404s -- indistinguishable from an
    # unknown token, no "this link was revoked" page.
    with app.app_context():
        from models import ShareToken  # pyright: ignore[reportMissingImports]

        token_id = ShareToken.query.filter_by(token=token).first().id
    submit(owner, f"/aircraft/{aircraft_id}/share/{token_id}/revoke", {})

    revoked_resp = anon.get(f"/share/{token}")
    assert revoked_resp.status_code == 404
