"""Production-database checks for restricted catalogue and discovery reads."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_catalogue_access import (
    catalogue,
    test_cached_full_membership_cannot_survive_scope_downgrade,
    test_code_import_and_saved_references_revalidate_request_scope,
    test_latest_arrival_does_not_resolve_hidden_tenant_aliases,
    test_passport_only_discovery_uses_public_or_visible_origins_and_no_fleet,
    test_restricted_search_preserves_public_data_without_private_overrides,
)
from tests.test_passport_groups import group
from tests.test_passport_invitations import invited
from tests.test_passport_management import owner
from tests.test_passport_memberships import members

__all__ = [
    "catalogue",
    "group",
    "invited",
    "members",
    "owner",
    "test_cached_full_membership_cannot_survive_scope_downgrade",
    "test_code_import_and_saved_references_revalidate_request_scope",
    "test_latest_arrival_does_not_resolve_hidden_tenant_aliases",
    "test_passport_only_discovery_uses_public_or_visible_origins_and_no_fleet",
    "test_restricted_search_preserves_public_data_without_private_overrides",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
