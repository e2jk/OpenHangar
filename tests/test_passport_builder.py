"""End-to-end HTTP builder flows and adversarial draft/ownership boundaries."""

import copy
import re
from datetime import date
from html import unescape
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Passport,
    PassportAirfield,
    PassportAirfieldIdentity,
    PassportDraft,
    PassportParticipant,
    PassportStampPhoto,
    PassportVisit,
    User,
    db,
)
from passports import builder
from passports.drafts import new_draft
from passports.management import BuilderError
from werkzeug.datastructures import MultiDict

from tests.test_passport_foundation import catalogue
from tests.test_passport_management import owner

__all__ = ["catalogue", "owner"]


def signin(client: FlaskClient, uid: int) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def posted(response: Any, action: str, **values: Any) -> MultiDict[str, str]:
    data = MultiDict()
    for name in ("draft", "revision"):
        found = re.search(r'name="' + name + r'" value="([^"]*)"', response.text)
        assert found is not None, response.text[:500]
        data[name] = unescape(found[1])
    for match in re.finditer(r'<input[^>]+name="choices"[^>]*>', response.text):
        if "checked" in match[0]:
            data.add("choices", unescape(re.search(r'value="([^"]*)"', match[0])[1]))
    data["action"] = action
    for key, value in values.items():
        if isinstance(value, list):
            data.setlist(key, value)
        else:
            data[key] = str(value)
    return data


def test_create_search_select_review_save(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    signin(client, owner["user"])
    response = client.get("/passports/new")
    assert response.status_code == 200 and "passport-selection-map" in response.text
    response = client.post(
        "/passports/new",
        data=posted(
            response,
            "filter",
            title="Country tour",
            countries=["BE"],
            types=["small_airport"],
        ),
    )
    assert (
        response.status_code == 200
        and "Namur" in response.text
        and "ULM field" in response.text
    )
    response = client.post(
        "/passports/new", data=posted(response, "selection", choices=["catalogue:1"])
    )
    assert response.status_code == 200
    response = client.post("/passports/new", data=posted(response, "review"))
    assert response.status_code == 200 and "Save passport" in response.text
    response = client.post("/passports/new", data=posted(response, "save"))
    assert response.status_code == 302
    detail = client.get(response.location)
    assert "Country tour" in detail.text and "Namur" in detail.text
    with app.app_context():
        passport = Passport.query.filter_by(title="Country tour").one()
        assert (
            PassportParticipant.query.filter_by(
                passport_id=passport.id, user_id=owner["user"]
            )
            .one()
            .accepted_at
            is not None
        )
        assert PassportAirfield.query.filter_by(passport_id=passport.id).count() == 1
        assert PassportDraft.query.count() == 0


def test_codes_unknown_review_back_and_drop(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    signin(client, owner["user"])
    response = client.get("/passports/new")
    response = client.post(
        "/passports/new",
        data=posted(response, "review", title="Codes", codes="ebnm; EBNM, BAD ZZZZ"),
    )
    assert (
        response.status_code == 400
        and "BAD" in response.text
        and "ZZZZ" in response.text
    )
    assert "Namur" in response.text
    response = client.post("/passports/new", data=posted(response, "drop_unknown"))
    assert response.status_code == 200
    response = client.post("/passports/new", data=posted(response, "review"))
    assert response.status_code == 200
    response = client.post("/passports/new", data=posted(response, "back"))
    assert response.status_code == 200 and "Choose airfields" in response.text
    response = client.post(
        "/passports/new", data=posted(response, "add_codes", codes="BE-123\nHEL")
    )
    assert (
        response.status_code == 200
        and "ULM field" in response.text
        and "Heliport" in response.text
    )
    response = client.post("/passports/new", data=posted(response, "review"))
    response = client.post("/passports/new", data=posted(response, "save"))
    assert response.status_code == 302
    with app.app_context():
        p = Passport.query.filter_by(title="Codes").one()
        assert PassportAirfield.query.filter_by(passport_id=p.id).count() == 3


def test_custom_classification_and_limits(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    signin(client, owner["user"])
    response = client.get("/passports/new")
    response = client.post(
        "/passports/new",
        data=posted(
            response,
            "add_custom",
            title="My strips",
            custom_name="Unknown strip",
            custom_code="STRIP",
        ),
    )
    assert response.status_code == 200 and "No coordinates" in response.text
    with app.app_context():
        draft = PassportDraft.query.one()
        key = draft.payload["selected"][0]
    response = client.post(
        "/passports/new",
        data=posted(
            response, "review", **{"ulm_" + key: "yes", "military_" + key: "no"}
        ),
    )
    assert response.status_code == 200
    response = client.post("/passports/new", data=posted(response, "save"))
    assert response.status_code == 302
    with app.app_context():
        field = PassportAirfieldIdentity.query.filter_by(ident="STRIP").one()
        assert field.overrides["ulm"] is True and field.overrides["military"] is False
        assert field.overrides["classification_provenance"]["user_id"] == owner["user"]
    response = client.get("/passports/new")
    response = client.post(
        "/passports/new",
        data=posted(
            response,
            "filter",
            title="All Belgium",
            countries=["BE"],
            types=["small_airport", "closed"],
            closed="on",
        ),
    )
    response = client.post("/passports/new", data=posted(response, "select_all"))
    assert response.status_code == 200 and "Closed field" in response.text
    with patch.object(builder, "MAX_TARGETS", 0):
        response = client.post("/passports/new", data=posted(response, "select_all"))
        assert response.status_code == 400


def test_edit_protected_removal_dates_archive_delete(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    with app.app_context():
        part = PassportParticipant.query.filter_by(passport_id=owner["passport"]).one()
        db.session.add(
            PassportVisit(
                passport_id=owner["passport"],
                participant_id=part.id,
                target_id=owner["target"],
                author_user_id=owner["user"],
                visit_date=date(2025, 1, 1),
            )
        )
        db.session.add(
            PassportStampPhoto(
                passport_id=owner["passport"],
                participant_id=part.id,
                target_id=owner["target"],
                uploader_user_id=owner["user"],
                filename="protected.png",
            )
        )
        db.session.commit()
    signin(client, owner["user"])
    path = f"/passports/{owner['passport']}/edit"
    response = client.get(path)
    response = client.post(
        path,
        data=posted(
            response, "remove:" + f"field:{owner['field']}", start_date="2026-01-01"
        ),
    )
    response = client.post(path, data=posted(response, "review"))
    assert (
        response.status_code == 200 and "saved visits and stamp photos" in response.text
    )
    assert "Your completion before this change" in response.text
    response = client.post(path, data=posted(response, "save"))
    assert response.status_code == 400
    with app.app_context():
        assert db.session.get(PassportAirfield, owner["target"]) is not None
    response = client.post(path, data=posted(response, "review"))
    response = client.post(path, data=posted(response, "save", confirm_removal="on"))
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(PassportAirfield, owner["target"]) is None
        assert db.session.get(Passport, owner["passport"]).start_date == date(
            2026, 1, 1
        )
    archive = f"/passports/{owner['passport']}/archive"
    assert client.get(archive).status_code == 200
    assert client.post(archive, data={}).status_code == 400
    assert (
        client.post(
            archive, data={"confirm": "yes", "operation": "archive"}
        ).status_code
        == 302
    )
    assert client.get(path).status_code == 409
    assert client.get(archive).status_code == 200
    assert (
        client.post(archive, data={"confirm": "yes", "operation": "reopen"}).status_code
        == 302
    )
    delete = f"/passports/{owner['passport']}/delete"
    assert client.get(delete).status_code == 200
    assert client.post(delete, data={}).status_code == 400
    assert client.post(delete, data={"confirm": "yes"}).status_code == 302
    with app.app_context():
        assert db.session.get(Passport, owner["passport"]) is None


def test_access_draft_tampering_stale_review_and_replay(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    assert client.get("/passports/new").status_code == 302
    signin(client, owner["other"])
    assert client.get("/passports/new").status_code == 403
    for endpoint in ("edit", "archive", "delete"):
        assert (
            client.get(f"/passports/{owner['passport']}/{endpoint}").status_code == 404
        )
    signin(client, owner["user"])
    response = client.get("/passports/new")
    form = posted(response, "review", title="Secure")
    assert (
        client.post(
            "/passports/new",
            data={"draft": form["draft"], "revision": "9999999999999999999999"},
        ).status_code
        == 400
    )
    assert (
        client.post("/passports/new", data={**form, "draft": "bad"}).status_code == 404
    )
    response = client.post("/passports/new", data=form)
    assert response.status_code == 200
    assert client.post("/passports/new", data=form).status_code == 409
    response = client.post(
        "/passports/new", data=posted(response, "save", title="Forged title")
    )
    assert response.status_code == 409
    response = client.post("/passports/new", data=posted(response, "review"))
    last = posted(response, "save")
    saved = client.post("/passports/new", data=last)
    assert (
        saved.status_code == 302
        and client.post("/passports/new", data=last).status_code == 404
    )
    path = f"/passports/{owner['passport']}/edit"
    response = client.get(path)
    response = client.post(path, data=posted(response, "review"))
    with app.app_context():
        p = db.session.get(Passport, owner["passport"])
        p.description = "Changed externally"
        db.session.commit()
    assert client.post(path, data=posted(response, "save")).status_code == 409


@pytest.mark.parametrize(
    "action,values",
    [
        ("no_such_action", {}),
        ("remove:fake", {}),
        ("selection", {"choices": ["catalogue:999"]}),
        ("review", {"title": ""}),
        ("review", {"title": "x", "start_date": "invalid"}),
        ("filter", {"countries": ["NOT-A-COUNTRY"]}),
        ("filter", {"query": "x" * 256}),
        ("filter", {"filter_ulm": "invalid"}),
        ("add_codes", {"codes": "x" * 50001}),
        ("add_custom", {"custom_name": ""}),
        (
            "add_custom",
            {"custom_name": "X", "custom_latitude": "nan", "custom_longitude": "3"},
        ),
    ],
)
def test_builder_validation_preserves_typed_fields(
    app: Flask,
    client: FlaskClient,
    owner: dict[str, int],
    catalogue: Any,
    action: str,
    values: dict[str, Any],
) -> None:
    signin(client, owner["user"])
    response = client.get("/passports/new")
    response = client.post(
        "/passports/new",
        data=posted(response, action, description="Keep my description", **values),
    )
    assert response.status_code == 400 and "Keep my description" in response.text


def test_custom_code_conflict_rolls_back_whole_creation(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    signin(client, owner["user"])
    response = client.get("/passports/new")
    response = client.post(
        "/passports/new",
        data=posted(
            response,
            "add_custom",
            title="Conflict",
            custom_name="Different place",
            custom_code="TEST",
        ),
    )
    response = client.post("/passports/new", data=posted(response, "review"))
    response = client.post("/passports/new", data=posted(response, "save"))
    assert response.status_code == 409
    with app.app_context():
        assert Passport.query.filter_by(title="Conflict").count() == 0


def test_filter_classification_services_and_reference_validation(
    app: Flask, owner: dict[str, int], catalogue: Any
) -> None:
    with app.app_context():
        _token, draft = new_draft(owner["tenant"], owner["user"])
        state = copy.deepcopy(draft.payload)
        with pytest.raises(BuilderError):
            builder.selected_fields(
                owner["tenant"], {**state, "selected": ["custom:missing"]}
            )
        with pytest.raises(BuilderError):
            builder.selected_fields(
                owner["tenant"], {**state, "selected": ["field:999999"]}
            )
        with pytest.raises(BuilderError):
            builder._classification("bad")
        state["selected"] = [f"field:{owner['field']}"]
        with pytest.raises(BuilderError):
            builder.update_form(
                state, MultiDict({"military_" + state["selected"][0]: "bad"})
            )
        with pytest.raises(BuilderError):
            builder.update_form(state, MultiDict({"title": "x" * 10001}))
        field = db.session.get(PassportAirfieldIdentity, owner["field"])
        field.airfield_type = "small_airport"
        field.country = "BE"
        field.overrides = {"ulm": True}
        db.session.flush()
        state["filters"].update(countries=["BE"], military="unknown", ulm="yes")
        assert len(builder.matching_fields(owner["tenant"], state)) == 1
        state["filters"].update(ulm="no")
        assert builder.matching_fields(owner["tenant"], state) == []
        state["filters"].update(
            ulm="any", military="any", query="Namur", regions=["BE-WAL"]
        )
        assert len(builder.matching_fields(owner["tenant"], state)) == 1
        state["selected"] = ["catalogue:1", "catalogue:1"]
        assert len(builder.selected_fields(owner["tenant"], state)) == 1


def test_materialised_catalogue_and_ambiguous_aliases(
    app: Flask, owner: dict[str, int], catalogue: Any
) -> None:
    from passports import catalogue as cat

    with app.app_context():
        field = cat.materialise(owner["tenant"], catalogue[0])
        alias_field = cat.create_custom(owner["tenant"], "Alias site")
        cat.add_alias(field, "DUP")
        cat.add_alias(alias_field, "DUP")
        db.session.flush()
        _token, draft = new_draft(owner["tenant"], owner["user"])
        state = copy.deepcopy(draft.payload)
        state["filters"].update(countries=["BE"], query="Namur")
        assert len(builder.matching_fields(owner["tenant"], state)) == 1
        builder.add_codes(owner["tenant"], state, "EBNM")
        assert state["selected"] == [f"field:{field.id}"]
        builder.add_codes(owner["tenant"], state, "DUP")
        assert state["unresolved"] == ["DUP"]
        builder.add_codes(owner["tenant"], state, "")
        assert state["unresolved"] == []
        with patch.object(builder, "MAX_TARGETS", 0), pytest.raises(BuilderError):
            builder.add_codes(owner["tenant"], state, "HEL")
        state["selected"] = ["field:999999"]
        assert (
            builder.selected_fields(owner["tenant"], state, allow_missing=True)[0][
                "latitude"
            ]
            is None
        )


def test_view_only_and_missing_membership(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    from passports.routes import _actor
    from werkzeug.exceptions import Forbidden

    with app.test_request_context("/passports/new"), pytest.raises(Forbidden):
        _actor()
    with app.app_context():
        user = db.session.get(User, owner["user"])
        user.view_only = True
        db.session.commit()
    signin(client, owner["user"])
    assert client.get("/passports/new").status_code == 403
    response = client.get(f"/passports/{owner['passport']}/")
    assert response.status_code == 200 and "Edit passport" not in response.text


def test_selection_and_payload_caps_recover_without_server_errors(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    from passports import drafts, routes

    signin(client, owner["user"])
    response = client.get("/passports/new")
    with (
        patch.object(builder, "MAX_TARGETS", 0),
        patch.object(routes, "MAX_TARGETS", 0),
        patch.object(drafts, "MAX_TARGETS", 0),
    ):
        response = client.post(
            "/passports/new",
            data=posted(response, "add_custom", custom_name="Over limit"),
        )
        assert response.status_code == 400
    with (
        patch.object(routes, "MAX_PAYLOAD_BYTES", 1000),
        patch.object(drafts, "MAX_PAYLOAD_BYTES", 1000),
    ):
        response = client.post(
            "/passports/new", data=posted(response, "selection", description="x" * 2000)
        )
        assert response.status_code == 400
    with app.app_context():
        assert PassportDraft.query.one().payload["selected"] == []


def test_missing_airfield_during_finalisation_rolls_back(
    app: Flask, owner: dict[str, int], catalogue: Any
) -> None:
    with app.app_context():
        passport = db.session.get(Passport, owner["passport"])
        _token, draft = new_draft(owner["tenant"], owner["user"], passport)
        state = copy.deepcopy(draft.payload)
        impact = builder.review(owner["tenant"], owner["user"], passport, draft, state)
        state["review"] = impact["signature"]
        with (
            patch.object(builder, "review", return_value=impact),
            patch.object(db.session, "get", return_value=None),
            pytest.raises(BuilderError),
        ):
            builder.commit_selection(
                owner["tenant"], owner["user"], passport, draft, state, False
            )
        db.session.rollback()
        assert db.session.get(PassportAirfield, owner["target"]) is not None


def test_keep_existing_target_when_editing(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    signin(client, owner["user"])
    path = f"/passports/{owner['passport']}/edit"
    response = client.get(path)
    response = client.post(path, data=posted(response, "review", title="Renamed"))
    response = client.post(path, data=posted(response, "save"))
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(PassportAirfield, owner["target"]) is not None
        assert db.session.get(Passport, owner["passport"]).title == "Renamed"
