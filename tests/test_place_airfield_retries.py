"""Duplicate selections keep the winner; unrelated database failures still surface."""

from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from models import PlaceAirfield, db
from passports import catalogue as airfield_catalogue
from places.access import PlaceError
from places.airfields import add_link, linked_airfields
from places.candidates import select_airfield
from sqlalchemy.exc import IntegrityError

from tests.test_place_airfield_pages import catalogue, links, people

__all__ = ["catalogue", "links", "people"]


def test_duplicate_link_uses_the_committed_winner_without_overwriting_its_data(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        add_link(
            t, u, x["place"], x["field"], note="Original directions", preferred=True
        )
        db.session.commit()
        db.session.expunge_all()
        real_get = db.session.get
        missed = False

        def stale_lookup(model: Any, identity: Any, **kwargs: Any) -> Any:
            nonlocal missed
            if model is PlaceAirfield and not missed:
                missed = True
                return None
            return real_get(model, identity, **kwargs)

        with patch.object(db.session, "get", side_effect=stale_lookup):
            link = add_link(t, u, x["place"], x["field"], note="Stale second click")
        db.session.commit()
        assert link.note == "Original directions" and link.preferred
        assert PlaceAirfield.query.filter_by(place_id=x["place"]).count() == 1


def test_link_insert_failure_without_a_winner_is_not_suppressed(
    app: Flask, people: dict[str, int], links: dict[str, int]
) -> None:
    p, x = people, links
    with app.app_context():
        real_flush = db.session.flush

        def failed_insert(*args: Any, **kwargs: Any) -> None:
            if any(isinstance(obj, PlaceAirfield) for obj in db.session.new):
                raise IntegrityError("INSERT", {}, RuntimeError("injected failure"))
            real_flush(*args, **kwargs)

        with (
            patch.object(db.session, "flush", side_effect=failed_insert),
            pytest.raises(IntegrityError, match="injected failure"),
        ):
            add_link(p["tenant"], p["creator"], x["place"], x["field"])
        assert linked_airfields(p["tenant"], p["creator"]) == []


@pytest.mark.parametrize("conflict_kind", ["database", "alias"])
@pytest.mark.parametrize("winner_exists", [True, False])
def test_catalogue_conflict_reuses_only_an_existing_identity_for_that_source(
    app: Flask,
    people: dict[str, int],
    links: dict[str, int],
    catalogue: list,
    winner_exists: bool,
    conflict_kind: str,
) -> None:
    p, x = people, links
    with app.app_context():
        t, u = p["tenant"], p["creator"]
        # A matching source in another tenant must never count as the winner.
        airfield_catalogue.materialise(
            t if winner_exists else p["elsewhere"], catalogue[0]
        )
        db.session.commit()
        stale_choice = {
            "catalogue:near": {
                "id": None,
                "source_id": "near",
                "public_catalogue": True,
            }
        }
        error = (
            IntegrityError("INSERT", {}, RuntimeError("injected source conflict"))
            if conflict_kind == "database"
            else airfield_catalogue.AirfieldConflict("injected source conflict")
        )
        with (
            patch("places.candidates.available_airfields", return_value=stale_choice),
            patch("places.candidates.catalogue.materialise", side_effect=error),
        ):
            if winner_exists:
                select_airfield(t, u, x["place"], "catalogue:near")
                db.session.commit()
                rows = linked_airfields(t, u)
                assert len(rows) == 1 and rows[0]["name"] == "Near small airfield"
                assert rows[0]["public_catalogue"]
            else:
                with pytest.raises(
                    IntegrityError if conflict_kind == "database" else PlaceError
                ):
                    select_airfield(t, u, x["place"], "catalogue:near")
                assert linked_airfields(t, u) == []
