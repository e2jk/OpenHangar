"""Daily maintenance under the shared scheduler's cross-worker advisory lock."""

from datetime import UTC, datetime

from models import PlaceCollectionInvitation, db

from places.management import purge_expired


def purge_retired() -> tuple[int, int]:
    """Purge the 30-day bin and expired invitations, retaining files on failure."""
    try:
        places = purge_expired()
        invitations = PlaceCollectionInvitation.query.filter(
            PlaceCollectionInvitation.expires_at <= datetime.now(UTC)
        ).delete(synchronize_session="fetch")
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return places, invitations
