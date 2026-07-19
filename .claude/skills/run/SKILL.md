---
name: run
description: Launch the OpenHangar dev server (Docker Compose) and drive it end-to-end in a browser to verify a change actually works.
---

# Running OpenHangar locally

## Start the server

```bash
cd <your-dev-compose-directory>   # sibling of this repo — see docs/development.md
docker compose up -d openhangar-db openhangar-web   # add --build after Dockerfile/deps changes
```

`app/` is volume-mounted into the container, so Python and template changes
take effect immediately — no rebuild needed. Only rebuild (`--build`) after
changing `Dockerfile`, `requirements/*.txt`, or `requirements/package-lock.json`.

Reachable at **http://hangar.localhost:5000/**.

Gotcha: the container does **not** auto-apply new Alembic migrations on
reload. After adding a model change + migration, restart the container:
```bash
docker compose restart openhangar-web
```
Otherwise affected pages 500 with `UndefinedColumn`.

## Verify a change end-to-end

1. Start the server (above) if not already running.
2. Log in as a seeded user (check `docker compose logs openhangar-web` on
   first boot, or the seed script, for credentials — dev-only, never real
   secrets).
3. Navigate to the affected page(s) and exercise the actual feature, not
   just the code path — click through the golden path and at least one
   edge case (empty state, validation error, permission-gated view).
4. If the change touches JS: reload the page AND navigate to it via an
   HTMX-boosted link (click a nav link rather than typing the URL) — HTMX
   swaps only `<body>`, which is where "works on first load, breaks after
   navigation" bugs from inline `<script>` blocks show up. See AGENTS.md
   § "Inline scripts silently dropped after hx-boost navigation".
5. If the change touches a seeded report/dashboard view that looks empty,
   check whether it needs a query param to show data — e.g. the cost
   dashboard needs `?period=0`.

## Screenshots

`scripts/take_screenshots.py` drives a headless Chromium browser against
`docs/screenshots/manifest.yml` (one entry per screenshot: URL template,
viewport, optional setup steps). Requires the dev server running. See
AGENTS.md § "Screenshot generation workflow" for manifest gotchas.

```bash
.venv/bin/python scripts/take_screenshots.py
```
