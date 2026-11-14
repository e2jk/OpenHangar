"""Every page refuses a signed-out visitor, except a known list of public ones.

The signed-in ZAP scan's "Cookie Slack Detector" (90027) tries to find pages
that answer the same without the session cookie, but that scan adds the
cookie back to every request (that is how it stays signed in), so the rule
cannot tell. This sweep does the check directly: each GET route, requested
without a session on a set-up instance, must send the visitor to the login
page, answer 401 (the JSON APIs) or 404 (an unknown share or invite token).
A new public page has to be added to PUBLIC on purpose.
"""

import re

PUBLIC = {
    # endpoint: (status, Location or None)
    "index": (200, None),  # the landing page
    "auth.login": (200, None),
    "auth.logout": (302, "/"),
    "auth.setup": (302, "/config/"),  # already set up; /config/ then asks to sign in
    "health": (200, None),
    "health_ready": (200, None),
    "robots_txt": (200, None),
    "favicon": (200, None),
    "pwa_manifest": (200, None),
    "service_worker": (200, None),
    "hangar.secret": (200, None),  # static easter-egg page, no data
    "airport_search": (200, None),  # empty results signed out, checked below
    "aircraft_type_search": (200, None),  # same
    "set_language": (400, None),  # "x" is no language
    "set_theme": (400, None),  # "x" is no theme
    "not_yet_implemented": (501, None),
}


def _sample_path(rule, aircraft_id):
    path = rule.rule
    for name, conv in rule._converters.items():
        numeric = type(conv).__name__ in ("DbIdConverter", "AircraftRefConverter")
        value = str(aircraft_id) if numeric else "x"
        path = re.sub(r"<[^>]*\b" + name + ">", value, path)
    return path


def test_every_page_refuses_a_signed_out_visitor(owner_env, app):
    anonymous = app.test_client()
    problems = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        path = _sample_path(rule, owner_env.aircraft_id)
        resp = anonymous.get(path)
        location = resp.headers.get("Location", "").split("?")[0] or None
        if rule.endpoint in PUBLIC:
            if (resp.status_code, location) != PUBLIC[rule.endpoint]:
                problems.append(f"{path}: {resp.status_code} {location}")
        elif not (
            (resp.status_code == 302 and location == "/login")
            or resp.status_code in (401, 404)
        ):
            problems.append(f"{path} ({rule.endpoint}): {resp.status_code} {location}")
    assert not problems, "\n".join(problems)


def test_lookups_return_nothing_signed_out(owner_env, app):
    anonymous = app.test_client()
    for path in ("/airport-search", "/aircraft-type-search"):
        assert anonymous.get(path, query_string={"q": "ce"}).get_json() == {
            "results": []
        }
