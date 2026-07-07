"""
EASA Safety Publications Tool sync job.

Queries the EASA AD search endpoint for each EASASourceNode, diffs against
stored AirworthinessDocument records, and creates pending_review statuses for
newly discovered documents on all aircraft that have the relevant component.

Public API
----------
sync_all_nodes(app)   — called by the background scheduler; syncs every node.
sync_aircraft(ac)     — called from the manual-trigger route; syncs only the
                        nodes that belong to this aircraft's components.
"""

import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from models import (  # pyright: ignore[reportMissingImports]
    Aircraft,
    AirworthinessDocument,
    AirworthinessDocStatus,
    AirworthinessDocType,
    AirworthinessDocumentStatus,
    EASASourceNode,
    db,
)

_log = logging.getLogger(__name__)

_EASA_SEARCH_URL = "https://ad.easa.europa.eu/search/advanced/result/"
_REQUEST_TIMEOUT = 15
_COURTESY_DELAY = 2.0  # seconds between requests
_USER_AGENT = "OpenHangar/airworthiness-sync (+https://github.com/e2jk/OpenHangar)"

# Matches "AD 2023-0048", "AD 2006-0345R", etc.
_AD_RE = re.compile(r"\bAD\s+\d{4}-\d+[A-Z]*\b")
# Matches "SIB 2024-01" etc.
_SIB_RE = re.compile(r"\bSIB\s+\d{4}-\d+[A-Z]*\b")


def _build_tree_path(node: EASASourceNode) -> str:
    return (
        f"{node.tc_holder_node_id}@@@@0@@{node.tc_holder_name}"
        f"|||{node.type_node_id}@@{node.tc_holder_node_id}@@1@@{node.type_name}"
        f"|||{node.model_node_id}@@{node.type_node_id}@@2@@{node.model_name}"
    )


def _fetch_references(node: EASASourceNode) -> list[tuple[str, str]]:
    """
    POST to the EASA search endpoint and return a list of (reference, doc_type)
    tuples for all documents found.  Raises requests.RequestException on failure.
    """
    payload = {
        "fi_action": "advanced",
        "fi_tree": _build_tree_path(node),
        "fi_keyword": "",
        "fi_date_start": "",
        "fi_date_end": "",
        "ps_src_tree": "",
        "fi_notification": "N",
        "is_default": "N",
        "fi_basket[]": node.model_node_id,
    }
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(
        _EASA_SEARCH_URL,
        data=data,
        headers={"User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        html = resp.read().decode()

    refs: list[tuple[str, str]] = []
    for m in _AD_RE.finditer(html):
        refs.append((m.group().strip(), AirworthinessDocType.AD))
    for m in _SIB_RE.finditer(html):
        refs.append((m.group().strip(), AirworthinessDocType.SIB))
    return refs


def _easa_doc_url(reference: str) -> str:
    slug = reference.replace(" ", "_").replace("/", "-")
    return f"https://ad.easa.europa.eu/ad/{slug}"


def _process_node(node: EASASourceNode) -> tuple[int, bool]:
    """
    Sync one node. Returns (new_docs_added, had_error).
    Creates AirworthinessDocumentStatus records (pending_review) for each
    aircraft that has a component referencing this node.
    """
    try:
        refs = _fetch_references(node)
    except Exception as exc:
        _log.warning(
            "EASA sync error for node %s (%s): %s", node.id, node.display_path, exc
        )
        node.consecutive_errors = (node.consecutive_errors or 0) + 1
        db.session.commit()
        return 0, True

    # Existing references for this node
    existing = {
        d.reference
        for d in AirworthinessDocument.query.filter_by(source_node_id=node.id).all()
    }

    aircraft_id = node.component.aircraft_id

    added = 0
    for reference, doc_type in refs:
        if reference in existing:
            continue
        doc = AirworthinessDocument(
            doc_type=doc_type,
            reference=reference,
            source_node_id=node.id,
            doc_url=_easa_doc_url(reference),
        )
        db.session.add(doc)
        db.session.flush()

        # Create pending_review status for the aircraft
        st = AirworthinessDocumentStatus(
            aircraft_id=aircraft_id,
            document_id=doc.id,
            status=AirworthinessDocStatus.PENDING_REVIEW,
        )
        db.session.add(st)
        added += 1

    node.consecutive_errors = 0
    node.last_synced_at = datetime.now(timezone.utc)
    db.session.commit()
    return added, False


def sync_aircraft(ac: Aircraft) -> tuple[int, int]:
    """
    Sync all EASA source nodes for the given aircraft.
    Returns (total_new_docs, total_error_nodes).
    """
    total_added = 0
    total_errors = 0
    first = True
    for comp in ac.components:  # type: ignore[attr-defined]
        for node in comp.easa_source_nodes:
            if not first:
                time.sleep(_COURTESY_DELAY)
            first = False
            added, had_error = _process_node(node)
            total_added += added
            if had_error:
                total_errors += 1
    return total_added, total_errors


def sync_all_nodes(app: object) -> None:
    """
    Sync every EASASourceNode in the database. Called by the background
    scheduler (once per 24 h).  Logs a warning if a node has not synced
    successfully in 72 h.

    Guarded by an advisory lock (see services.advisory_lock) so that only one
    gunicorn worker performs the sync per scheduled tick — without it, all
    four production workers would hit the EASA endpoint independently and
    each create their own copy of any new document. A session-scoped lock
    (not a transaction-scoped one) is required here because _process_node
    commits once per node, and a transaction-scoped lock would release at
    the first commit.
    """
    import flask  # pyright: ignore[reportMissingImports]

    from services.advisory_lock import advisory_lock_scope  # pyright: ignore[reportMissingImports]

    assert isinstance(app, flask.Flask)

    with app.app_context():
        with advisory_lock_scope(db, 7283910458) as acquired:
            if not acquired:
                _log.info(
                    "EASA sync: another worker holds the lock — skipping this run"
                )
                return

            nodes = EASASourceNode.query.all()
            _log.info("EASA sync: starting sync for %d node(s)", len(nodes))
            total_added = 0
            total_errors = 0
            skipped = 0
            now = datetime.now(timezone.utc)
            first_processed = True
            for node in nodes:
                # Exponential backoff: after 2+ consecutive failures, wait before retrying.
                # backoff = min(2^errors, 7) days from last successful sync.
                errors = node.consecutive_errors or 0
                if errors >= 2 and node.last_synced_at is not None:
                    backoff_days = min(2**errors, 7)
                    last = (
                        node.last_synced_at.replace(tzinfo=timezone.utc)
                        if node.last_synced_at.tzinfo is None
                        else node.last_synced_at
                    )
                    if (now - last).days < backoff_days:
                        _log.info(
                            "EASA sync: skipping node %s (%s) — %d error(s), backoff %d day(s)",
                            node.id,
                            node.display_path,
                            errors,
                            backoff_days,
                        )
                        skipped += 1
                        continue

                if not first_processed:
                    time.sleep(_COURTESY_DELAY)
                first_processed = False

                added, had_error = _process_node(node)
                total_added += added
                if had_error:
                    total_errors += 1
                else:
                    _log.debug(
                        "EASA sync: node %s (%s) — %d new doc(s)",
                        node.id,
                        node.display_path,
                        added,
                    )

            _log.info(
                "EASA sync complete: %d new document(s), %d error(s), %d skipped (backoff)",
                total_added,
                total_errors,
                skipped,
            )

            # Warn for nodes overdue (72 h without a successful sync)
            from datetime import timedelta

            cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
            overdue = [
                n
                for n in nodes
                if n.last_synced_at is None
                or (
                    n.last_synced_at.replace(tzinfo=timezone.utc)
                    if n.last_synced_at.tzinfo is None
                    else n.last_synced_at
                )
                < cutoff
            ]
            for node in overdue:
                _log.warning(
                    "[AIRWORTHINESS] EASA sync overdue for node %s (%s) — last success: %s",
                    node.id,
                    node.display_path,
                    node.last_synced_at,
                )
