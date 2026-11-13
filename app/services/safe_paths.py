"""One trust policy for files under the uploads folder (audit S07).

The uploads folder can be written by other programs too (a Syncthing-synced
document tree), so a symbolic link inside it must never let OpenHangar read,
write, move, serve, archive or delete a file outside it.

- Every path is resolved to its real location (following links) and must
  stay inside the real uploads folder: `resolve_inside`.
- Files are served only after that check: `send_inside`.
- Folder walks that read, archive, move or delete files skip symbolic links
  (files and folders alike): `regular_files`. A link pointing back inside
  the folder is skipped too: its target is found under its real path anyway.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from flask import Response, abort, send_file


def resolve_inside(root: str, *parts: str) -> str | None:
    """The real path of `root/parts`, or None when it would leave `root`
    (traversal, an absolute part, or a symbolic link pointing outside)."""
    real_root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(real_root, *parts))
    if os.path.commonpath([real_root, target]) != real_root:
        return None
    return target


def send_inside(root: str, relpath: str, **kwargs: Any) -> Response:
    """Serve the regular file at `relpath` under `root` (404 otherwise),
    with send_file's conditional-request handling."""
    path = resolve_inside(root, relpath)
    if path is None or not os.path.isfile(path):
        abort(404)
    return send_file(path, **kwargs)


def regular_files(
    root: str, *, skip_top_dirs: frozenset[str] = frozenset()
) -> Iterator[str]:
    """Paths of the regular files under `root`, never through a link.

    os.walk does not descend into linked folders by default; linked files are
    left out here as well. `skip_top_dirs` names folders directly under
    `root` to leave out entirely.
    """
    for dirpath, dirs, files in os.walk(root):
        if dirpath == root and skip_top_dirs:
            dirs[:] = [d for d in dirs if d not in skip_top_dirs]
        for name in files:
            path = os.path.join(dirpath, name)
            if not os.path.islink(path) and os.path.isfile(path):
                yield path
