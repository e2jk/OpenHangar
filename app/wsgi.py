import os

from init import create_app

# The production server entry point. Without a database URL the app falls
# back to an in-memory SQLite database (handy for tests): served for real,
# every worker would get its own empty database with an open setup wizard,
# and everything entered would be lost on restart.
if os.environ.get("OPENHANGAR_ENV", "production") in ("production", "demo") and not (
    os.environ.get("OPENHANGAR_DATABASE_URL")
    or os.environ.get("OPENHANGAR_DATABASE_URL_FILE")
):
    raise SystemExit(
        "OPENHANGAR_DATABASE_URL is not set: refusing to serve without a database."
    )

app = create_app()
