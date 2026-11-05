"""PostgreSQL 18+ images refuse to start with the pre-18 data mount.

From 18 on, the official image keeps its data in a version-specific
subfolder (e.g. /var/lib/postgresql/18/docker) and expects a single mount
at /var/lib/postgresql. A leftover /var/lib/postgresql/data mount makes the
database container exit on first boot, which is exactly what broke every
fresh Raspberry Pi install until this was caught. Guard the shipped compose
files and the compose snippets in the docs against regressing.

The image only enforces this when it initialises an empty database. An
existing container whose anonymous /var/lib/postgresql volume already holds
data keeps starting with the wrong mount, as the public demo did, until
that volume is lost. So a passing smoke test on a long-lived instance
proves nothing here.
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FILES = sorted(
    [
        *(_ROOT / "docker").glob("docker-compose*.yml"),
        *(_ROOT / "demo").glob("docker-compose*.yml"),
        *(_ROOT / "docs").glob("*.md"),
    ]
)
_PG18_IMAGE = re.compile(r"postgres:(1[89]|[2-9]\d)")
_OLD_MOUNT = re.compile(r"^\s*-\s*\S+:/var/lib/postgresql/data\b", re.MULTILINE)


def test_files_are_found():
    assert any(p.name == "docker-compose.raspberry-pi.yml" for p in _FILES)


def test_no_pre18_data_mount_with_postgres18_image():
    offenders = [
        str(path.relative_to(_ROOT))
        for path in _FILES
        if _PG18_IMAGE.search(text := path.read_text(encoding="utf-8"))
        and _OLD_MOUNT.search(text)
    ]
    assert not offenders, (
        "postgres:18+ with a /var/lib/postgresql/data mount (use "
        f"/var/lib/postgresql instead): {offenders}"
    )
