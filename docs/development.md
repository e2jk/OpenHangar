# Development Guide

## Running the application

The app runs via Docker Compose. From the directory containing your `docker-compose.yml`:

```bash
docker compose up openhangar-db openhangar-web
```

The Flask app is served at the host configured in your `.env` file (via Traefik).

Set `OPENHANGAR_ENV=development` in your environment to enable Flask's dev server
with auto-reload; any other value runs gunicorn in production mode.

---

## Running the tests

Tests are run locally using a Python virtual environment. The test suite uses
Flask's built-in test client and does not require the database or Docker to be running.

### First-time setup

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r ../docker/docker-requirements.txt
pip install -r requirements-dev.txt
```

### Running tests

```bash
cd backend
source .venv/bin/activate
pytest
```

For more verbose output:

```bash
pytest -v
```

### Test layout

```
backend/
  pytest.ini              # pytest configuration (testpaths, pythonpath)
  requirements-dev.txt    # test-only dependencies (pytest)
  tests/
    conftest.py           # shared fixtures: app, client, captured_templates
    test_routes.py        # HTTP-level tests (status codes, response content)
    test_templates.py     # template rendering tests (correct template, CSS links)
```

### Notes

- `.venv/` is listed in `.gitignore` and should never be committed.
- When DB tests are added in a future iteration, a dedicated Docker-based test
  service will be introduced. Until then, the local venv approach is sufficient.
