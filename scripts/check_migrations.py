#!/usr/bin/env python3
"""
Verify the Alembic migration chain is a valid, complete linear sequence.

Checks (all static — no database required):
  - Every revision file has a parseable revision ID and down_revision
  - Exactly one root migration (down_revision = None)
  - Exactly one head migration (no branches)
  - All down_revision values reference an existing revision
  - No cycles
  - No orphaned revisions (every file is reachable from the head)

Exit 0 if everything is OK, 1 on any error.

Usage:
  python scripts/check_migrations.py
"""

import pathlib
import re
import sys

VERSIONS_DIR = pathlib.Path(__file__).parent.parent / "app" / "migrations" / "versions"

_REV_RE = re.compile(
    r'^revision\s*(?::\s*[\w\s|"\']+)?\s*=\s*["\']([0-9a-f]+)["\']',
    re.MULTILINE,
)
_DOWN_RE = re.compile(
    r'^down_revision\s*(?::\s*[\w\s|"\']+)?\s*=\s*(?:["\']([0-9a-f]+)["\']|(None))',
    re.MULTILINE,
)


def _load_chain() -> dict[str, str | None]:
    """Return {revision: down_revision} for every migration file."""
    chain: dict[str, str | None] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if path.stem.startswith("__"):
            continue
        text = path.read_text()
        rev_m = _REV_RE.search(text)
        down_m = _DOWN_RE.search(text)
        if not rev_m:
            print(f"ERROR: cannot parse revision ID from {path.name}")
            sys.exit(1)
        if not down_m:
            print(f"ERROR: cannot parse down_revision from {path.name}")
            sys.exit(1)
        rev = rev_m.group(1)
        down: str | None = down_m.group(1) if down_m.group(1) else None
        if rev in chain:
            print(f"ERROR: duplicate revision ID '{rev}' in {path.name}")
            sys.exit(1)
        chain[rev] = down
    return chain


def _check_chain(chain: dict[str, str | None]) -> tuple[list[str], str | None]:
    errors: list[str] = []
    all_revs = set(chain)
    all_downs = {v for v in chain.values() if v is not None}

    roots = [r for r, d in chain.items() if d is None]
    heads = [r for r in all_revs if r not in all_downs]

    if len(roots) == 0:
        errors.append("no root migration found (no file has down_revision = None)")
    elif len(roots) > 1:
        errors.append(
            f"multiple root migrations (should be exactly one): {sorted(roots)}"
        )

    if len(heads) == 0:
        errors.append("no head migration found — circular chain?")
    elif len(heads) > 1:
        errors.append(
            f"branch detected — {len(heads)} heads found: {sorted(heads)}\n"
            "  Each migration must have exactly one successor."
        )

    for rev, down in chain.items():
        if down is not None and down not in all_revs:
            errors.append(
                f"migration '{rev}' references non-existent down_revision '{down}'"
            )

    if not errors and roots and heads:
        # Walk backwards from head, verify no cycle and no orphans.
        head = heads[0]
        visited: list[str] = []
        cur: str | None = head
        while cur is not None:
            if cur in visited:
                errors.append(f"cycle detected at revision '{cur}'")
                break
            visited.append(cur)
            cur = chain[cur]
        orphans = all_revs - set(visited)
        if orphans:
            errors.append(
                f"orphaned migration(s) not reachable from head: {sorted(orphans)}"
            )

    return errors, heads[0] if len(heads) == 1 else None


def main() -> None:
    if not VERSIONS_DIR.is_dir():
        print(f"ERROR: migrations versions directory not found: {VERSIONS_DIR}")
        sys.exit(1)

    chain = _load_chain()
    if not chain:
        print("ERROR: no migration files found")
        sys.exit(1)

    errors, head = _check_chain(chain)

    if errors:
        print("[check-migrations] FAILED:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)

    print(
        f"[check-migrations] OK — {len(chain)} migration(s), "
        f"single linear chain, head = {head}"
    )


if __name__ == "__main__":
    main()
