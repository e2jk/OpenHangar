from flask_limiter import Limiter  # pyright: ignore[reportMissingImports]
from flask_limiter.util import get_remote_address  # pyright: ignore[reportMissingImports]

limiter = Limiter(get_remote_address, storage_uri="memory://")
