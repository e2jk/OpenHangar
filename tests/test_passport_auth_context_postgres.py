"""Actual-cookie organisation context and nullable-role authorisation on PostgreSQL."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_auth_context import (
    memberships,
    test_context_owns_cache_and_renewal_namespace,
    test_default_login_uses_active_membership_and_required_two_factor,
    test_password_change_preserves_selected_context_and_revokes_other_logins,
    test_roleless_context_keeps_other_full_membership_and_default,
    test_service_permissions_deny_scoped_and_foreign_aircraft_even_with_grants,
)

__all__ = [
    "memberships",
    "test_context_owns_cache_and_renewal_namespace",
    "test_default_login_uses_active_membership_and_required_two_factor",
    "test_password_change_preserves_selected_context_and_revokes_other_logins",
    "test_roleless_context_keeps_other_full_membership_and_default",
    "test_service_permissions_deny_scoped_and_foreign_aircraft_even_with_grants",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
