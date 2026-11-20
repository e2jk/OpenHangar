"""Passport-only catalogue reads cannot reveal tenant-private fields or aliases."""

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask, session
from flask.testing import FlaskClient
from models import (
    Flight,
    Passport,
    PassportAirfield,
    PassportAirfieldIdentity,
    TenantUser,
    User,
    db,
)
from passports import builder, discovery, memberships
from passports import catalogue as cat
from passports.catalogue_access import visible_identity_ids
from passports.management import BuilderError

from tests.test_passport_groups import group
from tests.test_passport_invitations import invited
from tests.test_passport_management import owner
from tests.test_passport_memberships import members

__all__ = ["group", "invited", "members", "owner"]


@pytest.fixture
def catalogue(app: Flask, members: dict[str, int]) -> dict[str, Any]:
    with app.app_context():
        hidden = cat.create_custom(
            members["tenant"], "PRIVATE STRIP NAME", code="SECRETSTRIP"
        )
        cat.add_alias(hidden, "SECRETALIAS")
        row = next(r for r in cat.load_catalogue() if r.ident == "EBNM")
        materialised = cat.materialise(members["tenant"], row)
        cat.correct_airfield(
            materialised, {"name": "PRIVATE OVERRIDE", "latitude": 42.0}
        )
        private = Passport(
            tenant_id=members["tenant"],
            owner_user_id=members["user"],
            title="Other pilot private collection",
        )
        own = Passport(
            tenant_id=members["tenant"],
            owner_user_id=members["basic"],
            title="Own private collection",
        )
        own_field = cat.create_custom(
            members["tenant"], "Own private strip", code="OWNSTRIP"
        )
        db.session.add_all([private, own])
        db.session.flush()
        for f in (hidden, materialised):
            db.session.add(
                PassportAirfield(
                    tenant_id=members["tenant"],
                    passport_id=private.id,
                    airfield_id=f.id,
                )
            )
        db.session.add(
            PassportAirfield(
                tenant_id=members["tenant"],
                passport_id=own.id,
                airfield_id=own_field.id,
            )
        )
        db.session.commit()
        return members | {
            "hidden": hidden.id,
            "public": materialised.id,
            "own_field": own_field.id,
            "source": row.source_id,
            "public_name": row.name,
        }


def test_restricted_search_preserves_public_data_without_private_overrides(
    app: Flask, catalogue: dict[str, Any]
) -> None:
    c = catalogue
    with app.app_context():
        fields = builder.available_fields(c["tenant"], user_id=c["basic"])
        assert f"field:{c['hidden']}" not in fields
        assert f"field:{c['public']}" not in fields
        assert f"field:{c['own_field']}" in fields and f"field:{c['field']}" in fields
        public = fields["catalogue:" + c["source"]]
        assert public["name"] == c["public_name"] and public["id"] is None
        assert (
            discovery.origin_choices(
                c["tenant"], "PRIVATE STRIP NAME", user_id=c["basic"]
            )
            == []
        )
        assert discovery.origin_choices(c["tenant"], "Own private", user_id=c["basic"])
        assert discovery.origin_choices(
            c["tenant"], "PRIVATE STRIP NAME", user_id=c["user"]
        )
        for uid in (999999, c["basic"]):
            if uid == c["basic"]:
                db.session.get(User, uid).is_active = False
            assert f"field:{c['own_field']}" not in builder.available_fields(
                c["tenant"], user_id=uid
            )
        assert not list(
            db.session.scalars(visible_identity_ids(c["tenant"] + 100, c["basic"]))
        )


def test_code_import_and_saved_references_revalidate_request_scope(
    app: Flask, catalogue: dict[str, Any]
) -> None:
    c = catalogue
    with app.test_request_context():
        session["user_id"] = c["basic"]
        state = {"selected": []}
        builder.add_codes(c["tenant"], state, "SECRETSTRIP SECRETALIAS OWNSTRIP EBNM")
        assert state["unresolved"] == ["SECRETSTRIP", "SECRETALIAS"]
        assert f"field:{c['own_field']}" in state["selected"]
        assert "catalogue:" + c["source"] in state["selected"]
        for value in (
            "SECRETSTRIP",
            "SECRETALIAS",
            f"field:{c['hidden']}",
            f"field:{c['public']}",
        ):
            with pytest.raises(BuilderError):
                discovery.resolve_home(c["tenant"], value)
        candidate = discovery.resolve_home(c["tenant"], "EBNM")
        assert (
            isinstance(candidate, cat.CatalogueAirfield)
            and candidate.name == c["public_name"]
        )
        hidden = db.session.get(PassportAirfieldIdentity, c["hidden"])
        assert (
            discovery.home_descriptor(
                SimpleNamespace(home_airfield=hidden), c["tenant"]
            )
            is None
        )
        own = db.session.get(PassportAirfieldIdentity, c["own_field"])
        assert (
            discovery.home_descriptor(SimpleNamespace(home_airfield=own), c["tenant"])[
                "name"
            ]
            == "Own private strip"
        )
        with pytest.raises(BuilderError):
            builder.selected_fields(c["tenant"], {"selected": [f"field:{c['hidden']}"]})
        # Write-time uniqueness remains global: a hidden alias cannot be stolen.
        with pytest.raises(cat.AirfieldConflict):
            cat.create_custom(c["tenant"], "Attempted duplicate", code="SECRETALIAS")
        memberships.leave(c["tenant"], c["basic"], c["series"])
        db.session.commit()
        assert f"field:{c['field']}" not in builder.available_fields(c["tenant"])
        assert f"field:{c['own_field']}" in builder.available_fields(c["tenant"])
    with app.test_request_context():
        assert f"field:{c['hidden']}" not in builder.available_fields(c["tenant"])


def test_latest_arrival_does_not_resolve_hidden_tenant_aliases(
    app: Flask, catalogue: dict[str, Any]
) -> None:
    c = catalogue
    with app.app_context():
        db.session.add_all(
            [
                Flight(
                    pic_user_id=c["basic"],
                    date=date(2026, 2, 2),
                    arrival_icao="SECRETALIAS",
                    flight_time=1,
                ),
                Flight(
                    pic_user_id=c["basic"],
                    date=date(2026, 2, 1),
                    arrival_icao="EBNM",
                    flight_time=1,
                ),
            ]
        )
        db.session.commit()
        result = discovery.latest_arrival(c["tenant"], c["basic"], None)
        assert (
            result["name"] == c["public_name"]
            and result["key"] == "catalogue:" + c["source"]
        )
        # Internal trusted jobs and full memberships retain their catalogue view.
        assert visible_identity_ids(c["tenant"]) is None
        assert f"field:{c['hidden']}" in builder.available_fields(
            c["tenant"], user_id=c["user"]
        )
        assert db.session.get(TenantUser, (c["basic"], c["tenant"])).role is None


def test_passport_only_discovery_uses_public_or_visible_origins_and_no_fleet(
    app: Flask, client: FlaskClient, catalogue: dict[str, Any]
) -> None:
    from unittest.mock import patch

    from tests.test_passport_builder import signin

    c = catalogue
    with app.app_context():
        user = db.session.get(User, c["basic"])
        user.is_pilot = user.is_maintenance = user.is_instance_admin = True
        db.session.commit()
    signin(client, c["basic"])
    path = f"/passports/{c['passport']}/where-next"
    with (
        patch("utils.accessible_aircraft", side_effect=AssertionError("fleet lookup")),
        patch(
            "passports.routes.current_membership",
            return_value=SimpleNamespace(access_scope="full"),
        ),
    ):
        response = client.get(path, query_string={"q": "SECRET"})
        assert (
            response.status_code == 200 and b"PRIVATE STRIP NAME" not in response.data
        )
        assert b'id="discovery-aircraft"' not in response.data
        for origin in (f"field:{c['hidden']}", f"field:{c['public']}"):
            response = client.get(path, query_string={"origin": origin})
            assert response.status_code == 200
            assert (
                b"PRIVATE OVERRIDE" not in response.data
                and b"PRIVATE STRIP NAME" not in response.data
            )
        response = client.get(path, query_string={"origin": "catalogue:" + c["source"]})
        assert (
            response.status_code == 200 and c["public_name"].encode() in response.data
        )
        assert b"PRIVATE OVERRIDE" not in response.data
        assert client.get(path, query_string={"aircraft": "1"}).status_code == 404
    assert b"Where next?" in client.get(f"/passports/{c['passport']}/").data
    assert client.get("/passports/999999/where-next").status_code == 404
    assert client.get("/passports/new").status_code == 403


def test_cached_full_membership_cannot_survive_scope_downgrade(
    app: Flask, catalogue: dict[str, Any]
) -> None:
    c = catalogue
    with app.test_request_context():
        session["user_id"] = c["other"]
        membership = db.session.get(TenantUser, (c["other"], c["tenant"]))
        assert f"field:{c['hidden']}" in builder.available_fields(c["tenant"])
        db.session.execute(
            db.update(TenantUser)
            .where(
                TenantUser.user_id == c["other"], TenantUser.tenant_id == c["tenant"]
            )
            .values(access_scope="passport", role=None),
            execution_options={"synchronize_session": False},
        )
        assert membership.access_scope == "full"  # An intentionally stale ORM row.
        assert f"field:{c['hidden']}" not in builder.available_fields(c["tenant"])
        assert f"field:{c['field']}" in builder.available_fields(c["tenant"])
