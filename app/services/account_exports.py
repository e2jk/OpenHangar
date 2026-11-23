"""One versioned account archive assembled from independently scoped features.

Providers receive an explicit tenant and account, and must export only data that
account may recover. The assembler does not widen a provider's permissions.
"""

import json
import re
import tempfile
import zipfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, cast


@dataclass(frozen=True)
class ExportContext:
    tenant_id: int
    user_id: int


@dataclass(frozen=True)
class ExportFile:
    name: str
    path: Path
    metadata: dict[str, Any]


@dataclass
class ExportSection:
    data: dict[str, Any]
    files: list[ExportFile] = field(default_factory=list)
    version: int = 1
    legacy_document: str | None = None
    # Lists that only describe where the account's records live (e.g. the
    # collections it belongs to): on their own they are nothing to export.
    context_keys: frozenset[str] = frozenset()

    def has_data(self) -> bool:
        """Whether the section holds at least one of the account's records."""
        return bool(self.files) or any(
            isinstance(value, list) and value
            for key, value in self.data.items()
            if key not in self.context_keys
        )


_providers: dict[str, Callable[[ExportContext], ExportSection]] = {}


def register(name: str, provider: Callable[[ExportContext], ExportSection]) -> None:
    if re.fullmatch(r"[a-z][a-z0-9_]*", name) is None:
        raise ValueError("Invalid account export section name")
    _providers[name] = provider


def _claim(name: str, used: set[str]) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or not path.name
        or path.as_posix() != name
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or ":" in name
        or name in used
    ):
        raise ValueError("Invalid or duplicate archive entry")
    used.add(name)


def _sections(
    context: ExportContext, overrides: Mapping[str, ExportSection] | None
) -> Iterator[tuple[str, ExportSection]]:
    for name, provider in sorted(_providers.items()):
        if overrides is not None and name in overrides:
            yield name, overrides[name]
        else:
            yield name, provider(context)


def account_has_data(
    tenant_id: int,
    user_id: int,
    *,
    overrides: Mapping[str, ExportSection] | None = None,
) -> bool:
    """Whether account_archive() would hold any of the account's records, so a
    page offers the download only when there is something in it."""
    context = ExportContext(tenant_id, user_id)
    return any(section.has_data() for _name, section in _sections(context, overrides))


def account_archive(
    tenant_id: int,
    user_id: int,
    *,
    overrides: Mapping[str, ExportSection] | None = None,
) -> BinaryIO:
    """Write registered sections; internal adapters may supply an already-scoped section.

    Preserve explicitly named legacy documents while exposing all feature data
    through account-data.json. Files spill to disk above four MiB. The response
    owns the returned file; any failure closes it here.
    """
    context = ExportContext(tenant_id, user_id)
    output = tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode="w+b")  # noqa: SIM115
    try:
        sections: dict[str, Any] = {}
        used = {"account-data.json"}
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, section in _sections(context, overrides):
                for item in section.files:
                    _claim(item.name, used)
                    try:
                        archive.write(
                            item.path, item.name, compress_type=zipfile.ZIP_STORED
                        )
                        item.metadata["included"] = True
                    except FileNotFoundError:
                        item.metadata["included"] = False
                sections[name] = {"version": section.version, "data": section.data}
                if section.legacy_document is not None:
                    _claim(section.legacy_document, used)
                    archive.writestr(
                        section.legacy_document,
                        json.dumps(
                            section.data, ensure_ascii=False, indent=2, default=str
                        ),
                    )
            archive.writestr(
                "account-data.json",
                json.dumps(
                    {
                        "format": "openhangar-account-data",
                        "version": 1,
                        "exported_at": datetime.now(UTC).isoformat(),
                        "sections": sections,
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
            )
        output.seek(0)
        return cast(BinaryIO, output)
    except Exception:
        output.close()
        raise
