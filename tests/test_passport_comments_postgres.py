"""Discussion scope, cascades and additive migration on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask
from flask_migrate import downgrade
from models import db
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from tests.postgres_schema import postgres_app
from tests.test_passport_comments import (
    test_account_and_group_deletion_remove_comments,
    test_authors_edit_owners_moderate_without_rewriting_other_people,
    test_comment_validation,
    test_database_guards_scope_body_and_category,
    test_existing_discussion_outlives_edition_target_removal,
    test_integrity_race_is_a_retryable_conflict,
    test_leaving_revokes_access_but_keeps_the_stated_group_audience,
    test_membership_and_group_are_rechecked_under_the_mutation_lock,
    test_no_foreign_group_private_linked_field_or_tenant_access,
    test_no_stamp_required_and_same_thread_survives_edition_archive,
)
from tests.test_passport_groups import group
from tests.test_passport_management import owner
from tests.test_postgres_locks_and_migrations import (
    MIGRATIONS,
    _insert,
    _upgrade,
    empty_schema,
    test_migrations_build_the_schema_the_models_describe,
)

__all__ = [
    "empty_schema",
    "group",
    "owner",
    "test_account_and_group_deletion_remove_comments",
    "test_authors_edit_owners_moderate_without_rewriting_other_people",
    "test_comment_validation",
    "test_database_guards_scope_body_and_category",
    "test_existing_discussion_outlives_edition_target_removal",
    "test_integrity_race_is_a_retryable_conflict",
    "test_leaving_revokes_access_but_keeps_the_stated_group_audience",
    "test_membership_and_group_are_rechecked_under_the_mutation_lock",
    "test_migrations_build_the_schema_the_models_describe",
    "test_no_foreign_group_private_linked_field_or_tenant_access",
    "test_no_stamp_required_and_same_thread_survives_edition_archive",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)


def test_migration_preserves_parents_and_refuses_to_drop_discussion_data(
    empty_schema: tuple[Flask, Engine],
) -> None:
    app, engine = empty_schema
    previous = "5abc051fdcae"
    _upgrade(app, previous)
    tenant = _insert(engine, "tenants", name="Discussion migration")
    user = _insert(
        engine, "users", email="discussion@migration.test", password_hash="unused"
    )
    series = _insert(
        engine,
        "passport_series",
        tenant_id=tenant,
        owner_user_id=user,
        title="Existing group",
        is_group=True,
    )
    field = _insert(
        engine,
        "passport_airfield_identities",
        tenant_id=tenant,
        source="custom",
        name="Existing airfield",
        overrides={},
    )
    _upgrade(app, "head")
    _insert(
        engine,
        "passport_airfield_comments",
        tenant_id=tenant,
        series_id=series,
        airfield_id=field,
        author_user_id=user,
        author_display_name="Group alias",
        category="question",
        body="Retain this group comment",
    )
    with app.app_context():
        with pytest.raises(SystemExit):
            downgrade(directory=MIGRATIONS, revision=previous)
        db.session.remove()
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT body FROM passport_airfield_comments")
            ).scalar_one()
            == "Retain this group comment"
        )
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM passport_airfield_comments"))
    with app.app_context():
        downgrade(directory=MIGRATIONS, revision=previous)
    assert "passport_airfield_comments" not in inspect(engine).get_table_names()
    _upgrade(app, "head")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT title FROM passport_series")).scalar_one()
            == "Existing group"
        )


def test_removal_committed_while_post_waits_is_rechecked_after_lock(
    app: Flask, group: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, local

    from models import PassportAirfieldComment
    from passports import comments, groups, memberships
    from passports.management import BuilderError

    s = group
    removed_pending = Event()
    post_prechecked = Event()
    thread_state = local()
    original_lock = groups.lock_series

    def instrumented_lock(series_id: int) -> None:
        if getattr(thread_state, "posting", False):
            post_prechecked.set()
        original_lock(series_id)

    monkeypatch.setattr(groups, "lock_series", instrumented_lock)

    def remove() -> None:
        with app.app_context():
            memberships.remove(s["tenant"], s["user"], s["series"], s["other"])
            db.session.flush()
            removed_pending.set()
            assert post_prechecked.wait(timeout=5)
            db.session.commit()

    def post() -> int | None:
        assert removed_pending.wait(timeout=5)
        with app.app_context():
            thread_state.posting = True
            try:
                comments.add(
                    s["tenant"],
                    s["other"],
                    s["series"],
                    s["field"],
                    {"body": "Must not be posted after removal"},
                )
                db.session.commit()
                return None
            except BuilderError as exc:
                db.session.rollback()
                return exc.status

    with ThreadPoolExecutor(max_workers=2) as workers:
        remover = workers.submit(remove)
        poster = workers.submit(post)
        remover.result(timeout=15)
        assert poster.result(timeout=15) == 404
    with app.app_context():
        assert PassportAirfieldComment.query.count() == 0
