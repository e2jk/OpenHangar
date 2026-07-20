#!/usr/bin/env python3
"""
Verify the Alembic migration chain is a valid, complete DAG.

Checks (all static — no database required):
  - Every revision file has a parseable revision ID and down_revision
  - Exactly one root migration (down_revision = None)
  - Exactly one head migration (no unresolved branches)
  - All down_revision values reference an existing revision
  - No cycles
  - No orphaned revisions (every file is reachable from the head)
  - Merge migrations (tuple down_revision) are allowed — they resolve branches

Exit 0 if everything is OK, 1 on any error.

Usage:
  python scripts/check_migrations.py
"""

import pathlib
import re
import sys

VERSIONS_DIR = pathlib.Path(__file__).parent.parent / "app" / "migrations" / "versions"


def _color(text: str, code: str) -> str:
    """Wrap text in an ANSI colour code, but only when writing to a real
    terminal — a CI log or piped/redirected output gets plain text."""
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


_REV_RE = re.compile(
    r'^revision\s*(?::\s*[\w\s|"\']+)?\s*=\s*["\']([0-9a-f]+)["\']',
    re.MULTILINE,
)
# Matches one of:
#   down_revision = None
#   down_revision = "abc123"
#   down_revision = ("abc123", "def456", ...)   # merge migration
_DOWN_RE = re.compile(
    r'^down_revision\s*(?::\s*[\w\s|"\']+)?\s*=\s*'
    r"(?:(None)"  # group 1: None
    r'|["\']([0-9a-f]+)["\']'  # group 2: single hex string
    r"|\(\s*([0-9a-f\"',\s]+)\s*\))",  # group 3: tuple of hex strings
    re.MULTILINE,
)


def _parse_down_revision(text: str, filename: str) -> list[str] | None:
    """Return a list of parent revision IDs, or None for the root migration."""
    m = _DOWN_RE.search(text)
    if not m:
        print(f"ERROR: cannot parse down_revision from {filename}")
        sys.exit(1)
    if m.group(1):  # None
        return None
    if m.group(2):  # single string
        return [m.group(2)]
    # tuple: extract all hex strings from the match
    raw = m.group(3)
    parents = re.findall(r"[0-9a-f]+", raw)
    if not parents:
        print(f"ERROR: cannot parse down_revision tuple from {filename}")
        sys.exit(1)
    return parents


def _load_chain() -> dict[str, list[str] | None]:
    """Return {revision: [parent_revisions] | None} for every migration file."""
    chain: dict[str, list[str] | None] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if path.stem.startswith("__"):
            continue
        text = path.read_text()
        rev_m = _REV_RE.search(text)
        if not rev_m:
            print(f"ERROR: cannot parse revision ID from {path.name}")
            sys.exit(1)
        rev = rev_m.group(1)
        if rev in chain:
            print(f"ERROR: duplicate revision ID '{rev}' in {path.name}")
            sys.exit(1)
        chain[rev] = _parse_down_revision(text, path.name)
    return chain


def _check_chain(chain: dict[str, list[str] | None]) -> tuple[list[str], str | None]:
    errors: list[str] = []
    all_revs = set(chain)
    # All revisions that are referenced as a parent by at least one migration
    all_parents: set[str] = set()
    for parents in chain.values():
        if parents:
            all_parents.update(parents)

    roots = [r for r, p in chain.items() if p is None]
    heads = [r for r in all_revs if r not in all_parents]

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
            "  Use a merge migration (tuple down_revision) to resolve branches."
        )

    for rev, parents in chain.items():
        if parents:
            for p in parents:
                if p not in all_revs:
                    errors.append(
                        f"migration '{rev}' references non-existent down_revision '{p}'"
                    )

    if not errors and roots and heads:
        # Walk the DAG backwards from head.
        # Use DFS with an "in_progress" set to detect cycles (a common ancestor
        # reached via two parents is NOT a cycle — only a back-edge is).
        head = heads[0]
        visited: set[str] = set()
        in_progress: set[str] = set()
        cycle_found = False

        def _dfs(rev: str) -> None:
            nonlocal cycle_found
            if cycle_found:
                return
            if rev in in_progress:
                errors.append(f"cycle detected at revision '{rev}'")
                cycle_found = True
                return
            if rev in visited:
                return  # already fully processed via another path — fine in a DAG
            in_progress.add(rev)
            for parent in chain.get(rev) or []:
                _dfs(parent)
            in_progress.discard(rev)
            visited.add(rev)

        _dfs(head)
        orphans = all_revs - visited
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
        print(_color("[check-migrations] FAILED:", "31"))
        for e in errors:
            print(f"  {e}")
        sys.exit(1)

    print(
        _color(
            f"[check-migrations] OK — {len(chain)} migration(s), single head, head = {head}",
            "32",
        )
    )


if __name__ == "__main__":
    main()
