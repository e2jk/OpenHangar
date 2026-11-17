"""Identifier-only source parsing, staged review, reimports and provenance."""

import copy
import json
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient
from models import (
    Passport,
    PassportAirfield,
    PassportDraft,
    PassportParticipant,
    PassportVisit,
    db,
)
from passports import imports
from passports.management import BuilderError
from passports.source_fetch import Fetched, SourceError

from tests.test_passport_builder import posted, signin
from tests.test_passport_foundation import catalogue
from tests.test_passport_management import owner

__all__ = ["catalogue", "owner"]


def source(body: str = "EBNM ZZZZ", content_type: str = "text/html") -> Fetched:
    return Fetched("https://source.example/list", content_type, body.encode())


def test_generic_and_publisher_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert imports.generic_codes(
        source(
            "<p>EBNM</p><script>EVIL</script><style>STYLE EEEE</style><template>HIDE</template> EBNM HELI"
        )
    ) == ["EBNM", "HELI"]
    assert imports.generic_codes(source("EB12 1ABC 2027 HTML")) == [
        "EB12",
        "1ABC",
        "HTML",
    ]
    assert imports.generic_codes(source('{"icao":"EBNM"}', "application/json")) == [
        "EBNM"
    ]
    with pytest.raises(SourceError):
        imports.generic_codes(source("<script>EBNM</script>"))
    with pytest.raises(SourceError):
        imports._json(source("{bad", "application/json"))
    assert imports.publisher_codes(
        source(
            '{"airfields":[{"icao":"ebnm", "profile":{"ppr":"DO NOT RETAIN"}},{"icao":"EBNM"}]}',
            "application/json",
        )
    ) == ["EBNM"]
    for body in (
        "[]",
        "{}",
        '{"airfields":[{}]}',
        '{"airfields":[1]}',
        '{"airfields":[{"icao":"BAD-CODE"}]}',
    ):
        with pytest.raises(SourceError):
            imports.publisher_codes(source(body, "application/json"))
    monkeypatch.setattr(imports, "MAX_CODES", 1)
    with pytest.raises(SourceError):
        imports.generic_codes(source("EBNM HELI"))
    with pytest.raises(SourceError):
        imports.publisher_codes(
            source('{"airfields":[{"icao":"EBNM"},{"icao":"HEL"}]}', "application/json")
        )
    with pytest.raises(BuilderError):
        imports.apply({}, replace=True)
    assert imports.source_values({}) == {
        "source_url": None,
        "source_retrieved_at": None,
    }
    for value in (
        {"url": "https://source.example/", "retrieved_at": "bad"},
        {"url": "https://source.example/"},
        "bad",
    ):
        with pytest.raises(BuilderError):
            imports.source_values({"source": value})
    assert (
        imports.source_values(
            {
                "source": {
                    "url": "https://source.example/",
                    "retrieved_at": "2026-01-01T12:00:00",
                }
            }
        )["source_retrieved_at"].tzinfo
        is not None
    )


def test_staged_import_unknowns_and_saved_provenance(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    signin(client, owner["user"])
    response = client.get("/passports/new")
    with patch(
        "passports.imports.fetch",
        return_value=source("<p>EBNM ZZZZ</p><script>EVIL</script>"),
    ) as fetch:
        response = client.post(
            "/passports/new",
            data=posted(
                response,
                "preview_import",
                title="Imported",
                import_source="generic",
                import_url="https://source.example/",
            ),
        )
        assert (
            response.status_code == 200
            and "Review source import" in response.text
            and "Namur" in response.text
            and "ZZZZ" in response.text
            and "EVIL" not in response.text
        )
        assert fetch.call_count == 1
    with app.app_context():
        assert Passport.query.filter_by(title="Imported").count() == 0
        draft = PassportDraft.query.one()
        assert (
            draft.payload["import_preview"]["source"]["retrieved_at"]
            not in response.text
        )
        assert draft.payload["selected"] == [] and draft.payload["import_preview"][
            "matched"
        ] == ["catalogue:1"]
    response = client.post("/passports/new", data=posted(response, "import_merge"))
    response = client.post("/passports/new", data=posted(response, "review"))
    assert response.status_code == 400 and "Correct the unknown" in response.text
    response = client.post("/passports/new", data=posted(response, "drop_unknown"))
    response = client.post("/passports/new", data=posted(response, "review"))
    assert "https://source.example/list" in response.text
    response = client.post("/passports/new", data=posted(response, "save"))
    assert response.status_code == 302
    with app.app_context():
        passport = Passport.query.filter_by(title="Imported").one()
        assert (
            passport.source_url == "https://source.example/list"
            and passport.source_retrieved_at is not None
        )
        pid = passport.id
    response = client.get(f"/passports/{pid}/edit")
    assert "https://source.example/list" in response.text
    response = client.post(f"/passports/{pid}/edit", data=posted(response, "review"))
    assert (
        client.post(f"/passports/{pid}/edit", data=posted(response, "save")).status_code
        == 302
    )
    assert "https://source.example/list" in client.get(f"/passports/{pid}/").text


def test_replace_preserves_history_and_cancel_keeps_original(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    signin(client, owner["user"])
    pid = owner["passport"]
    path = f"/passports/{pid}/edit"
    with app.app_context():
        participant = PassportParticipant.query.filter_by(
            passport_id=pid, user_id=owner["user"]
        ).one()
        db.session.add(
            PassportVisit(
                passport_id=pid,
                participant_id=participant.id,
                target_id=owner["target"],
                visit_date=date(2025, 1, 1),
                author_user_id=owner["user"],
            )
        )
        db.session.commit()
    response = client.get(path)
    with patch("passports.imports.fetch", return_value=source("EBNM")):
        response = client.post(
            path,
            data=posted(
                response,
                "preview_import",
                import_source="generic",
                import_url="https://source.example/",
            ),
        )
    response = client.post(path, data=posted(response, "cancel_import"))
    assert "First field" in response.text
    with patch("passports.imports.fetch", return_value=source("EBNM")):
        response = client.post(
            path,
            data=posted(
                response,
                "preview_import",
                import_source="generic",
                import_url="https://source.example/",
            ),
        )
    response = client.post(path, data=posted(response, "import_replace"))
    assert "First field" in response.text and "Namur" in response.text
    response = client.post(path, data=posted(response, "review"))
    assert client.post(path, data=posted(response, "save")).status_code == 302
    with app.app_context():
        assert PassportVisit.query.count() == 1
        assert PassportAirfield.query.filter_by(passport_id=pid).count() == 2


def test_publisher_status_edition_and_failure_fallback(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    signin(client, owner["user"])
    response = client.get("/passports/new")
    response = client.post(
        "/passports/new",
        data=posted(response, "preview_import", import_source="landegutscheinheft"),
    )
    assert response.status_code == 400 and "edition label" in response.text
    with patch(
        "passports.imports.fetch",
        return_value=source('{"enabled":false}', "application/json"),
    ) as fetch:
        response = client.post(
            "/passports/new",
            data=posted(
                response,
                "preview_import",
                edition_label="2027",
                import_source="landegutscheinheft",
            ),
        )
        assert (
            response.status_code == 400
            and "not released" in response.text
            and fetch.call_count == 1
        )
    with patch(
        "passports.imports.fetch",
        side_effect=[
            source('{"enabled":true}', "application/json"),
            source(
                '{"airfields":[{"icao":"EBNM","name":"DO NOT RETAIN","profile":{"frequency":"DO NOT RETAIN"}}]}',
                "application/json",
            ),
        ],
    ):
        response = client.post(
            "/passports/new",
            data=posted(response, "preview_import", import_source="landegutscheinheft"),
        )
    assert response.status_code == 200 and "DO NOT RETAIN" not in response.text
    with app.app_context():
        assert "DO NOT RETAIN" not in json.dumps(PassportDraft.query.one().payload)
    response = client.post("/passports/new", data=posted(response, "cancel_import"))
    with patch(
        "passports.imports.fetch", side_effect=SourceError("Paste the codes instead.")
    ):
        response = client.post(
            "/passports/new",
            data=posted(
                response,
                "preview_import",
                import_source="generic",
                import_url="https://source.example/",
            ),
        )
    assert response.status_code == 400 and "Paste the codes" in response.text
    response = client.post(
        "/passports/new",
        data=posted(response, "preview_import", import_source="invalid"),
    )
    assert response.status_code == 400


def test_import_rate_limit(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    from extensions import limiter

    signin(client, owner["user"])
    app.config["RATELIMIT_ENABLED"] = True
    limiter.reset()
    try:
        response = client.get("/passports/new")
        with patch("passports.imports.fetch", return_value=source("EBNM")) as fetch:
            for _index in range(5):
                response = client.post(
                    "/passports/new",
                    data=posted(
                        response,
                        "preview_import",
                        import_source="generic",
                        import_url="https://source.example/",
                    ),
                )
                assert response.status_code == 200
            blocked = client.post(
                "/passports/new",
                data=posted(
                    response,
                    "preview_import",
                    import_source="generic",
                    import_url="https://source.example/",
                ),
            )
            assert blocked.status_code == 429 and fetch.call_count == 5
            edit_path = f"/passports/{owner['passport']}/edit"
            editing = client.get(edit_path)
            blocked_edit = client.post(
                edit_path,
                data=posted(
                    editing,
                    "preview_import",
                    import_source="generic",
                    import_url="https://source.example/",
                ),
            )
            assert blocked_edit.status_code == 429 and fetch.call_count == 5
            assert (
                client.post(
                    "/passports/new", data=posted(response, "cancel_import")
                ).status_code
                == 200
            )
    finally:
        app.config["RATELIMIT_ENABLED"] = False
        limiter.reset()


def test_fetch_failure_does_not_overwrite_a_concurrent_draft(
    app: Flask, client: FlaskClient, owner: dict[str, int], catalogue: Any
) -> None:
    signin(client, owner["user"])
    response = client.get("/passports/new")

    def failing_source(*args: Any, **kwargs: Any) -> Any:
        # Simulate a committed newer draft becoming visible before recovery.
        draft = PassportDraft.query.one()
        updated = copy.deepcopy(draft.payload)
        updated["metadata"]["title"] = "Saved in another tab"
        draft.payload = updated
        draft.revision += 1
        db.session.commit()
        raise SourceError("Failed source")

    with patch("passports.imports.fetch", side_effect=failing_source):
        response = client.post(
            "/passports/new",
            data=posted(
                response,
                "preview_import",
                import_source="generic",
                import_url="https://source.example/",
            ),
        )
    assert response.status_code == 409 and "Start again" in response.text
    with app.app_context():
        assert (
            PassportDraft.query.one().payload["metadata"]["title"]
            == "Saved in another tab"
        )


def test_long_code_lists_use_bounded_alias_queries(
    app: Flask, owner: dict[str, int], catalogue: Any
) -> None:
    from models import PassportAirfieldIdentity
    from passports import catalogue as cat
    from passports.builder import add_codes, available_fields
    from passports.catalogue import add_alias, create_custom
    from sqlalchemy import event

    with app.app_context():
        field = db.session.get(PassportAirfieldIdentity, owner["field"])
        add_alias(field, "HIST")
        add_alias(field, "AMB")
        other = create_custom(owner["tenant"], "Ambiguous field")
        add_alias(other, "AMB")
        db.session.commit()
        queries: list[str] = []

        def record(
            _connection: Any,
            _cursor: Any,
            sql: str,
            _parameters: Any,
            _context: Any,
            _many: Any,
        ) -> None:
            queries.append(sql)

        event.listen(db.engine, "before_cursor_execute", record)
        try:
            state: dict[str, Any] = {"selected": []}
            add_codes(
                owner["tenant"],
                state,
                "TEST HIST AMB EBNM "
                + " ".join(f"X{index:04}" for index in range(5000)),
            )
            assert len(queries) == 2
            assert state["selected"] == ["catalogue:1", f"field:{field.id}"]
            assert "AMB" in state["unresolved"] and "HIST" not in state["unresolved"]
            # load_catalogue is already cached; rebuilding a merged view does no disk read.
            with patch.object(
                cat.Path, "open", side_effect=AssertionError("Unexpected CSV reread")
            ):
                assert available_fields(owner["tenant"])
                assert available_fields(owner["tenant"])
        finally:
            event.remove(db.engine, "before_cursor_execute", record)


def test_busy_source_capacity_is_visible_without_network(
    app: Flask,
    client: FlaskClient,
    owner: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from passports import source_fetch

    signin(client, owner["user"])
    response = client.get("/passports/new")
    monkeypatch.setattr(source_fetch, "_FETCH_SLOTS", threading.BoundedSemaphore(0))
    with patch.object(source_fetch, "resolve") as resolver:
        response = client.post(
            "/passports/new",
            data=posted(
                response,
                "preview_import",
                title="Keep my title",
                import_source="generic",
                import_url="https://source.example/",
            ),
        )
    assert (
        response.status_code == 409 and "Other source imports are busy" in response.text
    )
    assert "Keep my title" in response.text
    resolver.assert_not_called()
