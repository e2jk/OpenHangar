"""Wait until PostgreSQL accepts connections, then exit.

Called by docker-entrypoint.sh in place of pg_isready so that
postgresql-client does not need to be installed in the runtime image.
"""

import os
import time

import psycopg2

database_url = os.environ["DATABASE_URL"]

while True:
    try:
        psycopg2.connect(database_url, connect_timeout=1).close()
        break
    except psycopg2.OperationalError:
        time.sleep(1)
