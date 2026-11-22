"""CodeQL findings that kept coming back, caught before a push.

The code-scanning alerts of the past weeks were mostly the same few notes,
raised again by each new feature. Ruff has no rule for them (or one that
also flags code CodeQL accepts), so these checks mirror CodeQL's own:

- py/import-and-import-from: a module both ``import x`` and
  ``from x import y`` at module level — pick one;
- py/empty-except: an ``except`` doing nothing (only ``pass``, no ``else``
  on the ``try``) without a comment saying why that's fine;
- py/uninitialized-local-variable: CodeQL doesn't know ``abort()`` never
  returns, so a bare ``abort(...)`` ending an ``except`` reads as falling
  through with the ``try``'s variables unset — write ``return abort(...)``.
"""

import ast
import io
import tokenize
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted(
    path
    for folder in ("app", "tests", "scripts")
    for path in (ROOT / folder).rglob("*.py")
    if "migrations" not in path.parts
)


def _parsed() -> Iterator[tuple[str, str, ast.Module]]:
    for path in SOURCES:
        source = path.read_text(encoding="utf-8")
        yield str(path.relative_to(ROOT)), source, ast.parse(source)


def mixed_imports(tree: ast.Module) -> list[str]:
    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    from_imported = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
    }
    return sorted(imported & from_imported)


def silent_excepts(source: str, tree: ast.Module) -> list[int]:
    comments = {
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }
    return [
        handler.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Try) and not node.orelse
        for handler in node.handlers
        if len(handler.body) == 1
        and isinstance(handler.body[0], ast.Pass)
        and not comments & set(range(handler.lineno, handler.body[0].lineno + 1))
    ]


def bare_aborts_ending_excepts(tree: ast.Module) -> list[int]:
    return [
        last.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        for last in node.body[-1:]
        if isinstance(last, ast.Expr)
        and isinstance(last.value, ast.Call)
        and isinstance(last.value.func, ast.Name)
        and last.value.func.id == "abort"
    ]


def test_each_module_is_imported_one_way() -> None:
    found = [
        f"{name}: {module}"
        for name, _source, tree in _parsed()
        for module in mixed_imports(tree)
    ]
    assert not found, "import x and from x import y in the same file:\n" + "\n".join(
        found
    )


def test_an_except_that_does_nothing_says_why() -> None:
    found = [
        f"{name}:{line}"
        for name, source, tree in _parsed()
        for line in silent_excepts(source, tree)
    ]
    assert not found, "except: pass without a comment:\n" + "\n".join(found)


def test_an_except_ending_in_abort_returns_it() -> None:
    found = [
        f"{name}:{line}"
        for name, _source, tree in _parsed()
        for line in bare_aborts_ending_excepts(tree)
    ]
    assert not found, "use return abort(...) to end an except:\n" + "\n".join(found)


@pytest.mark.parametrize(
    ("source", "mixed", "silent", "aborts"),
    [
        ("import os\nfrom os import path\n", ["os"], [], []),
        ("import os\ndef f():\n    from os import path\n", [], [], []),
        ("try:\n    x = 1\nexcept ValueError:\n    pass\n", [], [3], []),
        ("try:\n    x = 1\nexcept ValueError:\n    # fine\n    pass\n", [], [], []),
        (
            "try:\n    x = 1\nexcept ValueError:\n    pass\nelse:\n    y = x\n",
            [],
            [],
            [],
        ),
        ("try:\n    x = 1\nexcept ValueError:\n    abort(400)\n", [], [], [4]),
        ("try:\n    x = 1\nexcept ValueError:\n    return abort(400)\n", [], [], []),
    ],
)
def test_the_checks_themselves(
    source: str, mixed: list[str], silent: list[int], aborts: list[int]
) -> None:
    if "return" in source:
        source = "def f():\n" + "".join(f"    {line}\n" for line in source.splitlines())
        aborts = [line + 1 for line in aborts]
    tree = ast.parse(source)
    assert mixed_imports(tree) == mixed
    assert silent_excepts(source, tree) == silent
    assert bare_aborts_ending_excepts(tree) == aborts
