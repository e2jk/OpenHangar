"""Group discussion HTTP privacy and filters against PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_discussion_routes import (
    test_failed_edit_keeps_input_and_missing_membership_is_refused,
    test_filters_pagination_and_posting_date_are_independent_of_experience_date,
    test_organisation_admin_does_not_inherit_group_access,
    test_owner_can_delete_but_cannot_edit_another_author,
    test_roleless_member_posts_edits_and_exports_only_their_own_comments,
    test_validation_readonly_and_membership_revocation_do_not_render_stale_content,
)
from tests.test_passport_groups import group
from tests.test_passport_management import owner

__all__ = [
    "group",
    "owner",
    "test_failed_edit_keeps_input_and_missing_membership_is_refused",
    "test_filters_pagination_and_posting_date_are_independent_of_experience_date",
    "test_organisation_admin_does_not_inherit_group_access",
    "test_owner_can_delete_but_cannot_edit_another_author",
    "test_roleless_member_posts_edits_and_exports_only_their_own_comments",
    "test_validation_readonly_and_membership_revocation_do_not_render_stale_content",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
