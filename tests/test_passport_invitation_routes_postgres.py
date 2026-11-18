"""PostgreSQL invitation-to-login transactions and target-organisation MFA."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from tests.postgres_schema import postgres_app
from tests.test_passport_invitation_routes import (
    friend,
    test_replaced_invitation_during_auth_never_enrols,
    test_signin_automatically_joins_specific_edition_without_role_promotion,
    test_target_organisation_mfa_before_join,
)
from tests.test_passport_invitations import invited
from tests.test_passport_management import owner

__all__ = [
    "friend",
    "invited",
    "owner",
    "test_replaced_invitation_during_auth_never_enrols",
    "test_signin_automatically_joins_specific_edition_without_role_promotion",
    "test_target_organisation_mfa_before_join",
]


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Flask]:
    yield from postgres_app(monkeypatch, tmp_path)
