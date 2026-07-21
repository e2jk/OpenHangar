"""Wait until PostgreSQL accepts connections, then exit.

Called by docker-entrypoint.sh in place of pg_isready so that
postgresql-client does not need to be installed in the runtime image.
"""

import os
import time

import psycopg

database_url = os.environ["OPENHANGAR_DATABASE_URL"]

# libpq (and psycopg's conninfo parser) don't understand SQLAlchemy's
# +driver dialect suffix (e.g. postgresql+psycopg://) — strip it down to a
# plain postgresql:// URL in case OPENHANGAR_DATABASE_URL was set with one.
_scheme, _sep, _rest = database_url.partition("://")
if _sep:
    database_url = f"{_scheme.split('+', 1)[0]}{_sep}{_rest}"

while True:
    try:
        psycopg.connect(database_url, connect_timeout=1).close()
        break
    except psycopg.OperationalError:
        time.sleep(1)
