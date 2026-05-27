#!/usr/bin/env python3
"""
Add missing translations to all supported language catalogs.

Reads a TOML file that maps English source strings to their translations:

    [fr]
    "Source string" = "Translated string"

    [nl]
    "Source string" = "Translated string"

Only fills in entries whose msgstr is currently empty ("").  Already-translated
entries are left untouched.  After updating the .po files the catalogs are
recompiled to .mo.

Usage:
    python scripts/add_translations.py path/to/translations.toml [--dry-run]

The TOML file language sections must match the directory names under
app/translations/ (e.g. "fr", "nl").

Exit 0 on success, 1 on any error.
"""

import argparse
import pathlib
import re
import subprocess
import sys
from typing import Any

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-reuse-def]
    except ImportError:
        print("ERROR: requires Python 3.11+ (tomllib) or 'tomli' package")
        sys.exit(1)

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
TRANSLATIONS_DIR = PROJECT_ROOT / "app" / "translations"


def _load_toml(path: pathlib.Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _apply(po_path: pathlib.Path, mapping: dict[str, str], dry_run: bool) -> int:
    """Fill in empty msgstr entries.  Returns number of entries updated."""
    content = po_path.read_text(encoding="utf-8")
    count = 0
    for msgid, msgstr_val in mapping.items():
        if not msgstr_val:
            continue
        pattern = rf'(msgid "{re.escape(msgid)}"\nmsgstr )""'
        replacement = rf'\1"{msgstr_val}"'
        new_content, n = re.subn(pattern, replacement, content)
        if n > 0:
            content = new_content
            count += n
    if count and not dry_run:
        po_path.write_text(content, encoding="utf-8")
    return count


def _compile(lang: str, dry_run: bool) -> None:
    po_path = TRANSLATIONS_DIR / lang / "LC_MESSAGES" / "messages.po"
    mo_path = po_path.with_suffix(".mo")
    if dry_run:
        print(f"  [dry-run] would compile {po_path.relative_to(PROJECT_ROOT)}")
        return
    result = subprocess.run(
        ["pybabel", "compile", "-f", "-i", str(po_path), "-o", str(mo_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  WARNING: pybabel compile failed for {lang}:\n{result.stderr}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("toml_file", help="TOML file with translations")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be changed without writing",
    )
    args = parser.parse_args()

    toml_path = pathlib.Path(args.toml_file)
    if not toml_path.is_file():
        print(f"ERROR: file not found: {toml_path}")
        sys.exit(1)

    data = _load_toml(toml_path)
    if not data:
        print("ERROR: TOML file is empty or has no language sections")
        sys.exit(1)

    total = 0
    for lang, mapping in data.items():
        po_path = TRANSLATIONS_DIR / lang / "LC_MESSAGES" / "messages.po"
        if not po_path.is_file():
            print(f"WARNING: no catalog found for language '{lang}' at {po_path}")
            continue
        if not isinstance(mapping, dict):
            print(f"WARNING: section [{lang}] is not a mapping — skipped")
            continue

        count = _apply(po_path, mapping, args.dry_run)
        action = "would update" if args.dry_run else "updated"
        print(f"  [{lang}] {action} {count} entr{'y' if count == 1 else 'ies'}")
        total += count
        if not args.dry_run and count:
            _compile(lang, args.dry_run)

    if args.dry_run:
        print(f"\nDry run — {total} entr{'y' if total == 1 else 'ies'} would be added.")
    else:
        print(f"\nDone — {total} entr{'y' if total == 1 else 'ies'} added.")


if __name__ == "__main__":
    main()
