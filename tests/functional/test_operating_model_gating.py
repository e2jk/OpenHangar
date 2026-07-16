"""J13 — Operating-model gating (docs/functional_test_plan.md).

Intent per the plan: each of the five operating models presents only
its own features, with 2-4 model-inappropriate routes denying or hiding
per model.

Documented deviation (the plan's own "deviate only with a documented
reason" rule): the plan's illustrative examples ("sole_pilot: no fleet
maintenance, no reservations"; "flight_club: reservations present")
don't hold against the real gating mechanism. There is no
`OperatingModel` check anywhere in app/templates or any route decorator
-- grepping the whole template tree, `operating_model`/`OperatingModel`
only appears in the setup/settings/tenant-create templates, never in
base.html or dashboard.html. Nav/dashboard gating is driven entirely by
derived TenantProfile flags computed once in app/init.py's
inject_globals: `logbook_only` (planned_aircraft_count == 0) and
`allows_rental` (a separate bool, independent of which model was
chosen). Of the five models, only `sole_pilot` has any wizard-forced
consequence at all: it forces planned_aircraft_count to 0 server-side
regardless of what's posted, which is what actually hides the
Aircraft/Maintenance nav -- the other four models (sole_operator,
shared_ownership, flight_club, flight_school) are nav-identical
out of the box, and none of them get an "I plan to rent" checkbox in
the wizard UI (the server doesn't reject allows_rental=1 posted
directly on that step, but no wizard walk ever sends it for those two).
There is also no route-level deny anywhere: hitting /, /maintenance,
/reservations/fleet/, or POSTing /aircraft/new as the wizard-created
ADMIN returns 200 for every model, including sole_pilot -- "deny" per
the plan's wording doesn't exist in this codebase; only "hide" does,
and only along the logbook_only/allows_rental axes, not the raw
OperatingModel value.

This test asserts what's actually real and testable: (1) each of the
five models can be selected through the wizard and persists correctly
(all five step sequences differ -- sole_pilot finishes in one POST,
sole_operator in two, the rest in four); (2) sole_pilot is the one
model that hides Aircraft/Maintenance nav via logbook_only, while the
other four don't; (3) the reservations dashboard card is gated by
allows_rental alone, settable post-setup for any non-sole_pilot model
via the real /config/profile route -- demonstrating the actual gating
axis rather than a per-model one.

Each model gets its own test function/parametrize case rather than a
loop within one test: `/setup` is a one-shot, whole-instance wizard
(`auth.setup` redirects to `config.index` once any User row exists at
all, app/auth/routes.py's `_no_users()` guard) -- calling it a second
time within the same test, against the same DB session, silently no-ops
instead of creating a second tenant. Only clean_db's per-test teardown
(truncating every table) makes `/setup` reachable again, so each model
needs its own test.

Existing: test_onboarding_wizard.py asserts wizard storage and the
logbook_only/single_aircraft_mode derivation directly (via planned
aircraft counts set through a lower-level helper); this drives all five
models through the real wizard end-to-end and reads the resulting nav
context.
"""

import pytest  # pyright: ignore[reportMissingImports]

from tests.functional.conftest import submit

_ALL_MODELS = (
    "sole_pilot",
    "sole_operator",
    "shared_ownership",
    "flight_club",
    "flight_school",
)


def _setup_model(client, model, aircraft_count="1"):
    """Drive /setup for `model` end to end. Step sequence genuinely differs
    by model -- sole_pilot finishes in one POST; sole_operator in two; the
    rest in four.
    """
    submit(
        client,
        "/setup",
        {
            "step": "account",
            "email": "owner@example.com",
            "password": "SuperSecret123!",
            "name": "Owner",
        },
    )
    submit(client, "/setup", {"step": "totp", "action": "skip"})
    submit(client, "/setup", {"step": "operating_model", "operating_model": model})

    if model == "sole_pilot":
        return  # _setup_operating_model finishes the wizard directly

    submit(
        client, "/setup", {"step": "aircraft_count", "aircraft_count": aircraft_count}
    )

    if model == "sole_operator":
        return  # aircraft_count finishes the wizard directly for this model

    if model == "shared_ownership":
        submit(client, "/setup", {"step": "co_owners"})
    else:
        submit(client, "/setup", {"step": "org_name", "org_name": "Test Club"})
    submit(client, "/setup", {"step": "summary"})


@pytest.mark.parametrize("model", _ALL_MODELS)
def test_model_persists_through_the_wizard(app, client, model):
    _setup_model(client, model)
    with app.app_context():
        from models import (  # pyright: ignore[reportMissingImports]
            TenantProfile,
            TenantUser,
            User,
        )

        user = User.query.filter_by(email="owner@example.com").first()
        assert user is not None
        tenant_user = TenantUser.query.filter_by(user_id=user.id).first()
        profile = TenantProfile.query.filter_by(tenant_id=tenant_user.tenant_id).first()
        assert profile.operating_model == model


def test_sole_pilot_hides_aircraft_and_maintenance_nav(app, client, captured_templates):
    _setup_model(client, "sole_pilot")

    client.get("/")
    _, ctx = captured_templates[-1]
    assert ctx["logbook_only"] is True

    resp = client.get("/")
    assert b"Maintenance" not in resp.data


@pytest.mark.parametrize(
    "model", ("sole_operator", "shared_ownership", "flight_club", "flight_school")
)
def test_other_models_show_aircraft_and_maintenance_nav(
    app, client, captured_templates, model
):
    _setup_model(client, model)

    client.get("/")
    _, ctx = captured_templates[-1]
    assert ctx["logbook_only"] is False

    resp = client.get("/")
    assert b"Maintenance" in resp.data


def test_reservations_card_gated_by_allows_rental_not_by_model(app, client):
    # flight_club never gets the "I plan to rent" checkbox during setup
    # (no wizard UI path to it for this model) -- allows_rental starts
    # False regardless of the model choice.
    _setup_model(client, "flight_club")

    dash_before = client.get("/")
    assert b"reservations/fleet" not in dash_before.data

    # Toggling it afterwards, through the real (model-agnostic) settings
    # route, is what actually reveals the card -- proving the gate is
    # allows_rental, not the operating model.
    submit(
        client,
        "/config/profile",
        {
            "operating_model": "flight_club",
            "planned_aircraft_count": "1",
            "allows_rental": "on",
        },
    )
    dash_after = client.get("/")
    assert b"reservations/fleet" in dash_after.data
