"""
Verify that in-place form actions on long pages carry the scroll-anchor
mechanism instead of letting htmx scroll the page back to the top.

Background
----------
base.html sets hx-boost="true" on <body>, so htmx treats every <form
method="post"> submit as a boosted navigation, and its
scrollIntoViewOnBoost: true default scrolls document.body to the top after
every swap — even when the action's own result renders right next to the
button the user just clicked, somewhere down a long page.

The fix (see app/static/js/ui.js, data-scroll-anchor handling): the form
carries hx-swap="innerHTML show:none" (suppresses htmx's default scroll for
that one action) plus data-scroll-anchor="<id>" naming an ancestor element
to scroll back to once the swap settles.

Maintenance
-----------
When adding a new in-place action on a page that can be long (a delete/
toggle/backfill button whose result renders elsewhere on the same page),
add it to _SCROLL_ANCHOR_FORMS below so this test enforces the pattern.
"""

import pathlib
import re

import pytest

_TEMPLATE_DIR = pathlib.Path(__file__).parent.parent / "app" / "templates"

# Each entry is (endpoint, expected data-scroll-anchor value).
_SCROLL_ANCHOR_FORMS = [
    ("config.check_version", "version-check-card"),
    ("config.trigger_upgrade", "version-check-card"),
    ("config.backfill_aircraft_type_icao", "backfill-icao-card"),
    ("config.backfill_pilot_log_to_flight_entries", "backfill-pilotlog-card"),
    ("config.run_backup_now", "backup-section"),
    ("aircraft.delete_component", "comp-card-"),
    ("documents.upload_insurance_cert", "insurance-section"),
    ("aircraft.upload_photo", "photos-section"),
    ("aircraft.delete_photo", "photos-section"),
    ("share.revoke_token", "share-links-section"),
    ("airworthiness.trigger_sync", "aw-page-header"),
    ("airworthiness.delete_document", "docs-table"),
    ("airworthiness.delete_node", "easa-nodes-card"),
    ("airworthiness.delete_stc", "stcs-card"),
    ("users.change_role", "user-row-"),
    ("users.toggle_all_planes", "user-row-"),
    ("users.update_aircraft_access", "user-row-"),
    ("users.update_user_flags", "user-row-"),
]

# Matches an entire <form …> opening tag, including multi-line ones.
_FORM_TAG_RE = re.compile(r"<form\b[^>]*>", re.DOTALL | re.IGNORECASE)


class TestScrollAnchorOnInPlaceForms:
    """Every in-place-action <form> must suppress htmx's default
    scroll-to-top and name an anchor to scroll back to instead."""

    @pytest.mark.parametrize(
        "endpoint,anchor",
        _SCROLL_ANCHOR_FORMS,
        ids=[ep for ep, _ in _SCROLL_ANCHOR_FORMS],
    )
    def test_form_has_scroll_anchor(self, endpoint: str, anchor: str) -> None:
        violations: list[str] = []
        found = False

        for tmpl in sorted(_TEMPLATE_DIR.rglob("*.html")):
            content = tmpl.read_text(encoding="utf-8")
            rel = tmpl.relative_to(_TEMPLATE_DIR)

            for m in _FORM_TAG_RE.finditer(content):
                tag = m.group(0)
                if f"url_for('{endpoint}'" not in tag and (
                    f'url_for("{endpoint}"' not in tag
                ):
                    continue
                found = True
                line = content[: m.start()].count("\n") + 1
                if 'hx-swap="innerHTML show:none"' not in tag:
                    violations.append(
                        f'  {rel}:{line}  missing hx-swap="innerHTML show:none"'
                    )
                if f'data-scroll-anchor="{anchor}' not in tag:
                    violations.append(
                        f'  {rel}:{line}  missing data-scroll-anchor="{anchor}..."'
                    )

        assert found, f"No <form> referencing endpoint {endpoint!r} was found."
        assert not violations, (
            f"\nForm(s) posting to {endpoint!r} must carry both "
            'hx-swap="innerHTML show:none" and a matching data-scroll-anchor '
            "so htmx doesn't jump the page back to the top after this "
            "in-place action.\n\n" + "\n".join(violations)
        )
