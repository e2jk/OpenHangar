"""Layout of the logbook lists (pilot logbook, an aircraft's flight log):
the page opens on the flights — filters folded behind a "Filter" button,
per-page choice and compact switch under the list, and on a phone the
pager only under the list (its top copy is for wide screens)."""

import re
from datetime import date, timedelta

from models import Flight, db  # pyright: ignore[reportMissingImports]

from tests.test_gps_import import (  # pyright: ignore[reportMissingImports]
    _login,
    _make_user_and_aircraft,
)


def _flights(app, n):
    """`n` flights of one aircraft, all with the user as PIC."""
    uid, _tid, ac_id = _make_user_and_aircraft(app)
    with app.app_context():
        for i in range(n):
            db.session.add(
                Flight(
                    aircraft_id=ac_id,
                    date=date(2025, 1, 1) + timedelta(days=i),
                    departure_icao="EBAW",
                    arrival_icao="EBOS",
                    pic_user_id=uid,
                )
            )
        db.session.commit()
    return uid, ac_id


def _urls(ac_id):
    return ("/pilot/logbook", f"/aircraft/{ac_id}/flights")


def _tag(page, marker):
    """The opening tag of the element carrying `marker`."""
    i = page.index(marker)
    return page[page.rindex("<", 0, i) : page.index(">", i) + 1]


class TestFiltersFolded:
    def test_folded_and_closed_without_filters(self, client, app):
        uid, ac_id = _flights(app, 3)
        _login(client, uid)
        for url in _urls(ac_id):
            page = client.get(url).data.decode()
            panel = _tag(page, 'id="list-filters-panel"')
            assert re.search(r'class="collapse"', panel)
            # The form sits inside the folded panel, the button controls it.
            assert page.index('id="list-filters-panel"') < page.index(
                'id="list-filters"'
            )
            toggle = _tag(page, 'id="list-filters-toggle"')
            assert 'data-bs-toggle="collapse"' in toggle
            assert 'data-bs-target="#list-filters-panel"' in toggle
            assert 'aria-expanded="false"' in toggle
            assert "active filter" not in page and "Clear filters" not in page
            assert "bi-funnel-fill" not in page

    def test_active_filters_shown_on_the_button(self, client, app):
        uid, ac_id = _flights(app, 3)
        _login(client, uid)
        for url in _urls(ac_id):
            page = client.get(f"{url}?gps=without&from=2025-01-01").data.decode()
            # Still folded: the list comes first, the button says it's filtered.
            assert 'class="collapse"' in _tag(page, 'id="list-filters-panel"')
            assert "2 active filters" in page and "bi-funnel-fill" in page
            assert "Clear filters" in page
            page = client.get(f"{url}?gps=with").data.decode()
            assert "one active filter" in page


class TestControlsUnderTheList:
    def test_display_options_after_the_list(self, client, app):
        uid, ac_id = _flights(app, 3)
        _login(client, uid)
        for url in _urls(ac_id):
            page = client.get(url).data.decode()
            options = page.index("data-oh-logbook-display-options")
            assert page.index("data-oh-logbook-detailed") < options
            assert page.index("data-oh-logbook-compact>") < options
            assert page.index('id="oh-logbook-compact-toggle"') > options
            assert page.index("Show:") > options
            # Only one copy of the switch (its id must stay unique).
            assert page.count('id="oh-logbook-compact-toggle"') == 1
            assert 'data-oh-pager="' not in page  # one page: no pager

    def test_top_pager_only_on_wide_screens(self, client, app):
        uid, ac_id = _flights(app, 12)
        _login(client, uid)
        for url in _urls(ac_id):
            page = client.get(f"{url}?per_page=10").data.decode()
            top = _tag(page, 'data-oh-pager="top"')
            bottom = _tag(page, 'data-oh-pager="bottom"')
            assert "d-none" in top and "d-lg-flex" in top
            assert "d-none" not in bottom
            assert page.index('data-oh-pager="top"') < page.index(
                "data-oh-logbook-detailed"
            )
            assert page.index('data-oh-pager="bottom"') > page.index(
                "data-oh-logbook-detailed"
            )
            # The pager links keep the page size (and the filters).
            assert "page=2" in page and "per_page=10" in page
