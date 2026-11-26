"""Wait until PostgreSQL accepts connections, then exit.

Called by docker-entrypoint.sh in place of pg_isready so that
postgresql-client does not need to be installed in the runtime image.
"""

import os
import time

import psycopg

# Mirrors init._env_or_file()'s *_FILE fallback (INFRA-05) — this script is
# deliberately standalone (no postgresql-client / app import needed), so the
# same tiny check is duplicated rather than imported.
_file_path = os.environ.get("OPENHANGAR_DATABASE_URL_FILE")
if _file_path:
    with open(_file_path) as _fh:
        database_url = _fh.read().strip()
else:
    database_url = os.environ["OPENHANGAR_DATABASE_URL"]

# libpq (and psycopg's conninfo parser) don't understand SQLAlchemy's
# +driver dialect suffix (e.g. postgresql+psycopg://) — strip it down to a
# plain postgresql:// URL in case OPENHANGAR_DATABASE_URL was set with one.
_scheme, _sep, _rest = database_url.partition("://")
if _sep:
    database_url = f"{_scheme.split('+', 1)[0]}{_sep}{_rest}"

# Says why it is waiting (every 10 s), stops at once on an error that
# waiting cannot fix (a wrong password, user or database), and gives up after
# 5 minutes: it used to loop forever, silently, on a typo in the password.
_FATAL = ("password authentication failed", "does not exist", "no password supplied")
_GIVE_UP_AFTER_S = 300
_started = time.monotonic()
_attempt = 0
while True:
    _attempt += 1
    try:
        psycopg.connect(database_url, connect_timeout=1).close()
        break
    except psycopg.OperationalError as exc:
        # psycopg's message names the host and user, never the password.
        reason = " ".join(str(exc).split())
        if any(marker in reason for marker in _FATAL):
            print(f"Cannot connect to PostgreSQL: {reason}", flush=True)
            raise SystemExit(1) from None
        if time.monotonic() - _started > _GIVE_UP_AFTER_S:
            print(f"PostgreSQL still unreachable after 5 minutes: {reason}", flush=True)
            raise SystemExit(1) from None
        if _attempt % 10 == 1:
            print(f"Waiting for PostgreSQL ({reason})", flush=True)
        time.sleep(1)
