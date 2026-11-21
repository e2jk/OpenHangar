"""Registered model files are removed after the enclosing transaction commits.

Adapters supply cascade predicates for their own relationships. Database/bulk
SQL deletes bypass these ORM hooks and must manage their files separately.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy import event, inspect, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement


@dataclass(frozen=True)
class Registration:
    model: type[Any]
    filename_column: str
    folder: Callable[[], Path]
    cascade_filter: Callable[[Any], ColumnElement[bool] | None] | None
    error_label: str


_registry: dict[tuple[type[Any], str], Registration] = {}
_QUEUE = "stored_files_pending_deletion"


def register(
    model: type[Any],
    filename_column: str,
    folder: Callable[[], Path],
    *,
    cascade_filter: Callable[[Any], ColumnElement[bool] | None] | None = None,
    error_label: str = "stored file",
) -> None:
    """Register once per model/column; repeated registration replaces its adapter.

    A file belongs to one record. Copies must store bytes under a new filename;
    shared file references need their own lifetime policy, not this registry.
    """
    _registry[model, filename_column] = Registration(
        model, filename_column, folder, cascade_filter, error_label
    )


@event.listens_for(Session, "before_flush")
def prepare_files(session: Session, _context: Any, _instances: Any) -> None:
    for registration in _registry.values():
        column = getattr(registration.model, registration.filename_column)
        filenames: set[str] = set()
        for obj in session.dirty:
            if isinstance(obj, registration.model):
                filenames.update(
                    name
                    for name in inspect(obj)
                    .attrs[registration.filename_column]
                    .history.deleted
                    if name
                )
        filters = []
        for obj in session.deleted:
            if isinstance(obj, registration.model):
                # Attribute values still exist for deleted rows, including rows
                # which have just been flushed into the current transaction.
                name = getattr(obj, registration.filename_column)
                if name:
                    filenames.add(name)
            elif registration.cascade_filter is not None:
                predicate = registration.cascade_filter(obj)
                if predicate is not None:
                    filters.append(predicate)
        if filters:
            filenames.update(
                name
                for name in session.execute(
                    select(column).where(or_(*filters))
                ).scalars()
                if name
            )
        if not filenames:
            continue
        transaction = session.get_nested_transaction() or session.get_transaction()
        queue = session.info.setdefault(_QUEUE, {}).setdefault(transaction, set())
        for filename in filenames:
            # Registered folders contain server-generated basenames, never paths.
            if Path(filename).name != filename or filename in {".", ".."}:
                raise ValueError("Invalid stored filename")
            queue.add((registration.folder() / filename, registration.error_label))


@event.listens_for(Session, "after_commit")
def remove_files(session: Session) -> None:
    queues = session.info.get(_QUEUE, {})
    transaction = session.get_nested_transaction() or session.get_transaction()
    files = queues.pop(transaction, set())
    if transaction is not None and transaction.parent is not None:
        queues.setdefault(transaction.parent, set()).update(files)
        return
    for path, label in files:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            current_app.logger.exception("Could not delete %s %s", label, path.name)


@event.listens_for(Session, "after_soft_rollback")
def retain_files(session: Session, previous_transaction: Any) -> None:
    session.info.get(_QUEUE, {}).pop(previous_transaction, None)
