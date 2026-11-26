"""Pins that must move together in requirements/runtime.txt."""

import re
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent / "requirements" / "runtime.txt"


def _pin(name: str) -> str:
    match = re.search(rf"^{re.escape(name)}==(\S+)", RUNTIME.read_text(), re.MULTILINE)
    assert match, f"{name} is not pinned in requirements/runtime.txt"
    return match.group(1)


def test_psycopg_and_its_binary_are_the_same_version():
    """psycopg loads the compiled parts of the same psycopg-binary version;
    updated apart, every database import fails (PRs #606/#607). Renovate
    groups them (renovate.json)."""
    assert _pin("psycopg") == _pin("psycopg-binary")
