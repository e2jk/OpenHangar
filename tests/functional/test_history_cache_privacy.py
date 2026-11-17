"""Pages showing people's email addresses stay out of the back-button cache.

htmx keeps the last pages visited in the tab's sessionStorage
(``htmx-history-cache``) so that Back is instant. That copy outlives a
sign-out: on a shared computer, the next person could read it with the
browser's developer tools. ``offline_identity.js`` clears it whenever a page
loads signed out or under another account or login; on top of that, a page
that shows people's contact details is never cached at all: it carries the
``hx-history="false"`` marker (``base.html``; the page template sets
``no_history_cache``).

This sweep signs in as a fully populated tenant's admin, opens every GET
page it can build, and fails for each HTML page that shows another user's
email address without the marker — so a new page with other people's
contact details cannot be forgotten. The signed-in user's own address is on
every page (the account menu); clearing the cache on sign-out covers it.
"""

from werkzeug.routing import IntegerConverter

from tests.functional.test_tenant_isolation_sweep import _build_tenant_world

MARKER = 'hx-history="false"'

# GET endpoints not opened here, each with its reason.
SKIPPED = {
    "auth.logout": "would end the sweep's session",
    "set_language": "redirects back, no page of its own",
    "set_theme": "redirects back, no page of its own",
    "not_yet_implemented": "answers 501 on purpose",
}


def _path_for(adapter, rule, world) -> str | None:
    params = {}
    for arg in rule.arguments:
        converter = rule._converters.get(arg)
        numeric = isinstance(converter, IntegerConverter) or (
            type(converter).__name__ == "AircraftRefConverter"
        )
        if not numeric:
            return None  # tokens, file names, codes: no page to build here
        if arg == "entry_id" and "/wb/" in rule.rule:
            value = world.wb_entry_id
        else:
            value = getattr(world, arg, None)
        if value is None:
            return None
        params[arg] = value
    return adapter.build(rule.endpoint, params, method="GET")


def test_pages_showing_other_peoples_email_addresses_are_never_cached(
    app, client_factory
):
    world = _build_tenant_world(app, client_factory, "cache")
    with app.app_context():
        from models import User  # pyright: ignore[reportMissingImports]

        emails = [u.email for u in User.query.all() if u.id != world.admin_user_id]
        adapter = app.url_map.bind("localhost")
        paths = sorted(
            {
                path
                for rule in app.url_map.iter_rules()
                if "GET" in rule.methods
                and rule.endpoint not in SKIPPED
                and not rule.rule.startswith("/static")
                and (path := _path_for(adapter, rule, world))
            }
        )

    assert len(paths) > 100  # the sweep really covers the app
    missing = []
    errors = []
    showing = 0
    dashboard_checked = False
    for path in paths:
        resp = world.client.get(path)
        if resp.status_code >= 500:
            errors.append(f"{path} answered {resp.status_code}")
            continue
        if resp.status_code != 200 or resp.mimetype != "text/html":
            continue
        html = resp.get_data(as_text=True)
        if "<body" not in html:
            continue
        shown = [e for e in emails if e in html]
        if shown:
            showing += 1
            if MARKER not in html:
                missing.append(f"{path} shows {shown[0]}")
        elif path == "/":
            assert MARKER not in html  # other pages keep the instant Back
            dashboard_checked = True

    assert not errors, "server errors during the sweep:\n" + "\n".join(errors)
    assert dashboard_checked
    assert showing, "no page showed an email address: the sweep found nothing"
    assert not missing, (
        "pages showing email addresses must not be cached by htmx — add "
        "{% set no_history_cache = true %} to their template:\n" + "\n".join(missing)
    )
