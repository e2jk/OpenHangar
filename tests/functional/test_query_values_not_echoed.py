"""Findings from the signed-in ZAP scan (2026-11-14).

- "Big Redirect" (10044): the language and theme switches answered with
  Werkzeug's HTML body repeating the target; they now redirect with no body.
- "User Controllable HTML Element Attribute" (10031): list pages copied an
  invalid `order` / `per_page` back into links and hidden fields (escaped,
  so never exploitable); only validated values are echoed now.
"""

import pytest  # pyright: ignore[reportMissingImports]

MARKER = "zapmarker"


@pytest.mark.parametrize("path", ["/set-language/fr", "/set-theme/dark"])
def test_switches_redirect_without_a_body(owner_env, path):
    target = "/pilot/logbook?" + "&".join(f"p{i}=v{i}" for i in range(40))
    resp = owner_env.client.get(path, query_string={"next": target})
    assert resp.status_code == 302
    assert resp.headers["Location"] == target
    assert resp.get_data() == b""


def test_switches_still_refuse_off_site_targets(owner_env):
    resp = owner_env.client.get(
        "/set-theme/dark", query_string={"next": "https://evil.example/"}
    )
    assert resp.headers["Location"] == "/"
    assert resp.get_data() == b""


def _pages(owner_env):
    ac = owner_env.aircraft_id
    return [
        "/pilot/logbook",
        f"/aircraft/{ac}/flights",
        f"/aircraft/{ac}/documents",
    ]


def test_invalid_list_parameters_are_not_echoed(owner_env):
    for page in _pages(owner_env):
        resp = owner_env.client.get(
            page, query_string={"order": MARKER, "per_page": MARKER}
        )
        assert resp.status_code == 200, page
        body = resp.get_data(as_text=True)
        # The page's own links and fields; the language/theme switches still
        # carry the current address, URL-encoded, in next= (by design).
        assert f"={MARKER}" not in body, page
        assert f'"{MARKER}"' not in body, page


def test_valid_list_parameters_are_kept(owner_env):
    body = owner_env.client.get(
        "/pilot/logbook", query_string={"order": "asc", "per_page": "all"}
    ).get_data(as_text=True)
    assert 'name="order" value="asc"' in body
    assert 'name="per_page" value="all"' in body
