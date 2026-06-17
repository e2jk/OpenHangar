# OpenHangar — Self-Hosting Guide

This guide covers everything you need to deploy and operate OpenHangar on
your own Docker host.

> **New to self-hosting?** The [Raspberry Pi guide](raspberry-pi.md) walks
> you through the entire process — from blank SD card to working installation
> — with every command spelled out and no decisions left to you.

---

## Prerequisites

- Docker and Docker Compose installed on the host.
- A PostgreSQL database (easiest: an `openhangar-db` service in the same Compose file).
- *(Optional)* A reverse proxy such as Traefik or nginx for HTTPS.

---

## Quick start

The `docker/` folder ships with ready-made example files for a production
deployment behind a [Traefik](https://traefik.io/) reverse proxy:

| File | Purpose |
|---|---|
| [`docker/docker-compose.yml`](../docker/docker-compose.yml) | Production Compose stack (Traefik + PostgreSQL) |
| [`docker/.env.example`](../docker/.env.example) | All environment variables with documented defaults |

1. Copy both files to your deployment directory, renaming the example:
   ```bash
   cp docker/docker-compose.yml /your/deploy/path/
   cp docker/.env.example /your/deploy/path/.env
   ```
2. Edit `.env` — at minimum set `TRAEFIK_ACME_EMAIL`, database password,
   `OPENHANGAR_HOSTNAME`, `OPENHANGAR_SECRET_KEY`, and `OPENHANGAR_BACKUP_ENCRYPTION_KEY`.
   All app variables begin with `OPENHANGAR_` — see the full
   [configuration reference](configuration.md) for the complete list.
3. Start the stack:
   ```bash
   docker compose up -d
   ```

### Minimal setup (no reverse proxy)

Without Traefik, a minimal `docker-compose.yml` that exposes port 5000 directly:

```yaml
services:
  openhangar-db:
    image: postgres:18
    environment:
      POSTGRES_DB: openhangar
      POSTGRES_USER: openhangar
      POSTGRES_PASSWORD: changeme
    volumes:
      - db_data:/var/lib/postgresql/data

  openhangar-web:
    image: ghcr.io/e2jk/openhangar:latest
    depends_on:
      - openhangar-db
    environment:
      OPENHANGAR_DATABASE_URL: postgresql://openhangar:changeme@openhangar-db/openhangar
      OPENHANGAR_SECRET_KEY: change-this-to-a-long-random-string
    volumes:
      - ./openhangar/uploads:/data/uploads
      - ./openhangar/backups:/data/backups
    ports:
      - "5000:5000"

volumes:
  db_data:
```

```bash
docker compose up -d
```

The application runs on port 5000. Put a reverse proxy in front for HTTPS
in production.

---

## Configuration

All settings are provided via environment variables. See the full
[configuration reference](configuration.md) for every available variable.

Key variables to set in production:

| Variable | Why |
|---|---|
| `OPENHANGAR_DATABASE_URL` | PostgreSQL connection string |
| `OPENHANGAR_SECRET_KEY` | Long random string — protects session cookies (`openssl rand -hex 32`) |
| `OPENHANGAR_BACKUP_ENCRYPTION_KEY` | Encrypts backup files; keep this separate from the backups themselves |
| `OPENHANGAR_SMTP_HOST` | Required to enable email notifications (also set `OPENHANGAR_SMTP_FROM_ADDRESS`, `OPENHANGAR_SMTP_USER`, `OPENHANGAR_SMTP_PASSWORD`) |

---

## Map tiles

GPS flight tracks are rendered on **OpenStreetMap** by default — no account or
API key required.

For aviation-specific tiles (ICAO-style chart with airspaces, airways, and
airports), OpenHangar supports **OpenAIP**. When an OpenAIP key is configured,
the base map automatically switches to **CartoDB Positron** — a minimal
light-grey rendering of OSM data — so the aeronautical overlay is easy to read
without visual clutter.

![OpenAIP + CartoDB Positron (left) versus default OpenStreetMap (right)](screenshots/map_tiles.png)

*OpenAIP aeronautical overlay on CartoDB Positron (left) versus default OpenStreetMap (right)*

1. Register a free account at [openaip.net](https://www.openaip.net/user/api-clients) and
   generate an API key.
2. In OpenHangar, go to **Settings → Map tiles** and paste the key into the
   *OpenAIP API key* field.
3. Save — all flight-track maps will immediately switch to the CartoDB Positron
   base with the OpenAIP aeronautical overlay.

Removing the key reverts to plain OpenStreetMap.

Alternatively, set `OPENHANGAR_OPENAIP_API_KEY` in your `.env` file and the
container will write the key into the database automatically on every startup
(handy for automated / infrastructure-as-code deployments).

---

## Backups

OpenHangar produces encrypted ZIP backups of the database dump and all uploaded
documents. See the [backup & restore guide](backup_restore.md) for configuration,
scheduling with cron, and the full restore procedure.

Quick backup via CLI:

```bash
docker compose exec openhangar-web flask backup-now
```

---

## Upgrades

OpenHangar checks for new releases daily and displays a notification in the
Settings page when an update is available.

To upgrade, pull the new image and restart the web container:

```bash
docker compose pull openhangar-web
docker compose up -d openhangar-web
```

The container runs `alembic upgrade head` automatically on startup to apply
any pending database migrations.

---

## Health checks

OpenHangar exposes two probes with deliberately different semantics:

| Endpoint | Checks | Visibility | Use |
|----------|--------|------------|-----|
| `GET /health` | The web worker is alive and routing (**no** database access) | Public | Liveness — a simple "is the process up?" ping |
| `GET /health/ready` | Liveness **plus** database connectivity (`SELECT 1`) | **In-container only** | Readiness — used by the Docker `HEALTHCHECK` |

`/health` never touches the database on purpose: a liveness probe must not fail
just because a dependency is down, otherwise an orchestrator could restart a
perfectly healthy web container during a database outage (which never helps).

`/health/ready` is what the built-in Docker `HEALTHCHECK` calls, so
`docker ps` reflects real database connectivity — when the database is
unreachable the container is reported `unhealthy` while the web process keeps
running. It is restricted to **loopback callers** (the in-container health
check): requests proxied in from the public internet receive a `404`, so the
endpoint is neither exposed nor usable to force database round-trips. If you
deploy behind your own reverse proxy, point its health checks at `/health` and
let the container-internal check own `/health/ready`.

---

## HTTP access logging

In production and demo mode OpenHangar runs under **gunicorn**.
Application-level messages (startup, errors, security events) always appear
in `docker logs`:

```bash
docker compose logs -f openhangar-web
docker compose logs openhangar-web --since 1h
```

### Default behaviour — log file inside the container

By default, HTTP access logs are written to `/data/logs/openhangar-access.log` inside
the container.  That path sits under `/data/`, alongside uploads and backups,
so you can optionally expose it on the host by adding a volume mount to your
`docker-compose.yml`:

```yaml
volumes:
  - ./openhangar/data/logs:/data/logs
```

Then read or tail it from the host:

```bash
tail -f ./openhangar/data/logs/openhangar-access.log
```

Or directly inside the container without a volume mount:

```bash
docker compose exec openhangar-web tail -f /data/logs/openhangar-access.log
```

The log directory is created with owner-only permissions (`0700`) so the file
is not world-readable inside the container.  Access logs accumulate
indefinitely — rotate or prune them according to your retention policy (for
example with `logrotate` on the host once the directory is volume-mounted).

#### Secret-token redaction

A few links carry a single secret in the URL path (password-reset, invitation
and public share links).  OpenHangar's access logger masks that segment, so the
log records the endpoint that was hit without the token itself:

```
"GET /share/[REDACTED] HTTP/1.1" 200
"GET /reset-password/[REDACTED] HTTP/1.1" 200
```

### Forwarding access logs to `docker logs`

If you prefer to receive HTTP access logs in the container log stream instead
of a file — for example when `docker logs` is your primary log collection
mechanism — set `OPENHANGAR_ACCESS_LOG=1` in the `environment:` section of
the `openhangar-web` service:

```yaml
# docker-compose.yml
services:
  openhangar-web:
    environment:
      - OPENHANGAR_ACCESS_LOG=1
```

```bash
docker compose up -d openhangar-web
```

When this flag is set the logs go to stdout **only** (no file is written),
avoiding double logging.

> **Note**: most deployments that use Traefik or nginx already capture HTTP
> access logs at the reverse-proxy level.  In that case, neither the file nor
> the stdout option is necessary.  Be aware that the secret-token redaction
> described above applies only to OpenHangar's own access log — if your reverse
> proxy logs request paths, configure equivalent masking there as well.

---

## Architecture overview

```
Browser
  └─ Reverse proxy (Traefik / nginx) — TLS termination
       └─ OpenHangar web container (Flask / gunicorn, port 5000)
            ├─ PostgreSQL container (or external managed DB)
            └─ Host-mounted volumes
                 ├─ /data/uploads  — uploaded documents & photos
                 └─ /data/backups  — encrypted backup archives
```

- **Backend**: Flask serving server-rendered pages; gunicorn in production.
- **Database**: PostgreSQL (preferred). SQLite is used in the test suite only.
- **Authentication**: email + bcrypt password with optional TOTP 2FA.
- **File storage**: local filesystem inside the container, persisted via host-mounted volumes.
- **Background tasks**: a lightweight daemon thread (`sync-watcher`) scans the uploads folder every 60 s (configurable via `OPENHANGAR_SYNC_SCAN_INTERVAL`) and auto-imports documents that arrive via Syncthing (or another file-syncing tool). Backups are triggered on-demand or via a host cron job calling `flask backup-now`.

---

## Document storage & Syncthing/file syncing

### How documents are stored on disk

Every document uploaded through OpenHangar — whether via the web UI or discovered on disk — is stored in a **canonical path layout** inside `OPENHANGAR_UPLOAD_FOLDER`:

```
{tenant_slug}/{aircraft_reg}/{category}/YYYY-MM-DD - title.ext
```

Example:
```
example-hangar/OO-TUF/maintenance/2024-03-15 - Annual inspection.pdf
example-hangar/OO-TUF/insurance/2025-01-01 - Hull insurance.pdf
```

**Tenant slug** — the short identifier used as the top-level folder name. Set it in **Configuration → Tenants**: click the tenant name to open the edit form, then fill in the *Slug* field (e.g. `example-hangar`). The slug must be unique and URL-safe (lowercase letters, digits, hyphens). If no slug is set, the tenant folder is skipped by the background scan.

**Document categories** — the second-level folder name. Valid values:

| Folder name | Meaning |
|---|---|
| `maintenance` | Maintenance records, logbook endorsements |
| `insurance` | Hull and liability insurance certificates |
| `poh` | Pilot's Operating Handbook and AFM |
| `airworthiness` | ARC, airworthiness certificates, ADs |
| `logbook` | Aircraft technical logbook scans |
| `invoice` | Parts, maintenance, and fuel invoices |
| `other` | Anything that doesn't fit the categories above |
| `uncategorised` | Documents uploaded without a category |

**Aircraft registration** — the folder name is normalised to uppercase with hyphens and spaces stripped before matching (e.g. `oo-tuf`, `OO TUF`, and `OOTUF` all match aircraft registered as `OO-TUF`).

### Syncthing integration (optional)

OpenHangar is designed to work with [Syncthing](https://syncthing.net/) as a zero-configuration file-sync layer. Mount a Syncthing-shared directory as the `OPENHANGAR_UPLOAD_FOLDER` volume:

```yaml
volumes:
  - /path/to/syncthing/OpenHangar:/data/uploads
```

No Syncthing API integration is needed — Syncthing handles transport between peers, and OpenHangar reads whatever arrives on disk.

**Syncthing peer configuration:** disable *Receive Only* mode on the OpenHangar peer so it can both send and receive. Documents uploaded through the web UI are written to the canonical path and automatically picked up and propagated by Syncthing to other peers.

### Background scan and reconcile queue

A background thread scans `OPENHANGAR_UPLOAD_FOLDER` every **60 seconds** (configurable via `OPENHANGAR_SYNC_SCAN_INTERVAL`). For each file not yet tracked in the database:

- **Aircraft and category can both be resolved** → a `Document` row is created immediately (auto-import). No user action required.
- **Aircraft or category cannot be resolved** → the file is placed in the **reconcile queue** (`Documents → Reconcile`). The queue shows the detected filename, pre-filled title and date (parsed from the filename), and lets the user confirm or edit before importing.

You can also trigger a scan immediately from the **Documents → Reconcile** page with the *Scan for new files* button.

The scan is idempotent: a file already tracked in `documents` or already in the reconcile queue is silently skipped on subsequent passes.

### Deletion behaviour

- **Deleted via the OpenHangar UI** — the file is moved to a `_trash/` subfolder inside `OPENHANGAR_UPLOAD_FOLDER` rather than deleted outright. Syncthing propagates the move to other peers; the file is recoverable from `_trash/`.
- **Deleted on a peer (outside OpenHangar)** — Syncthing removes the file from disk. The `Document` row is preserved but the download link shows a broken-file warning. No automatic cleanup occurs.

### Limitations

- **Renaming a file on a peer** — Syncthing treats rename as delete + add. The original `Document` row becomes a broken link and a new reconcile entry is created for the renamed file. Always rename documents through the OpenHangar UI to keep the database consistent.
- **Pilot logbook attachments and aircraft photos** use a different storage path and are not auto-imported by the background scan.

---

## Rate limiting & brute-force protection

OpenHangar uses **three complementary layers** to stop password brute-force attacks:

### Layer 1 — Reverse-proxy rate limiting (per IP)

The reference `docker-compose.yml` already wires Traefik in front of
OpenHangar. The snippet below adds a **second, higher-priority router** that
applies a strict rate limit to the `/login` endpoint. Add these labels to the
`openhangar-web` service in your `docker-compose.yml`:

```yaml
# Separate router with a strict rate-limit for the login endpoint.
# Traefik picks this router over the main one because its rule is more specific.
- "traefik.http.routers.openhangar-auth.rule=Host(`${OPENHANGAR_HOSTNAME}`) && Path(`/login`)"
- "traefik.http.routers.openhangar-auth.entrypoints=websecure"
- "traefik.http.routers.openhangar-auth.tls=true"
- "traefik.http.routers.openhangar-auth.tls.certresolver=letsencrypt"
- "traefik.http.routers.openhangar-auth.service=openhangar"
- "traefik.http.routers.openhangar-auth.middlewares=openhangar-auth-ratelimit,openhangar-compress"
- "traefik.http.middlewares.openhangar-auth-ratelimit.ratelimit.average=5"
- "traefik.http.middlewares.openhangar-auth-ratelimit.ratelimit.burst=10"
- "traefik.http.middlewares.openhangar-auth-ratelimit.ratelimit.period=1m"
```

These settings allow a burst of 10 requests to `/login`, then enforce a steady
rate of 5 per minute per source IP. These labels are already included in the
reference `docker/docker-compose.yml`.

> **nginx alternative:** add a `limit_req_zone` / `limit_req` block targeting
> the `/login` location. The principle is the same; consult the nginx
> documentation for syntax.

### Layer 2 — Application-level IP backoff (per IP)

In addition to the reverse-proxy limit, the application itself imposes a
progressive **response delay** that grows with each consecutive failure from the
same IP address:

| Consecutive failures from same IP | Delay before response |
|---|---|
| 1–2 | none |
| 3 | 2 seconds |
| 4 | 10 seconds |
| 5 | 30 seconds |
| 6 or more | 60 seconds |

The counter resets automatically after 15 minutes of silence from that IP, or
immediately on a successful login. Each delay is logged as
`[SECURITY] auth.login.backoff` with the failure count and delay applied.

### Layer 3 — Account lockout (per account)

After **10 consecutive failed attempts** on the same e-mail address, the account
is temporarily locked for **30 minutes**. The lock is cache-based and lifts
automatically — no administrator action is required. Both the lock and any
subsequent blocked attempt are logged as `[SECURITY] auth.login.account_locked`
/ `auth.login.account_blocked`.

A locked user sees a clear message explaining when they can try again. The
30-minute window is enough to stop automated attacks while staying transparent
to a legitimate user who has genuinely forgotten their password.

---

## Multi-tenant deployments

A single OpenHangar installation can serve multiple completely independent organisations (tenants) from one database and one Docker container.  Each tenant has its own fleet, users, and data — tenants cannot see each other's information.

This is entirely optional.  If you only ever need one organisation, the multi-tenant UI never appears and the experience is identical to a single-tenant install.

### Instance admin

The very first user created during the setup wizard is automatically designated the **instance admin**.  The instance admin is a cross-tenant super-user: they provision new tenants and handle emergencies, but they do not need a seat inside every tenant.

The instance admin manages tenants from **Configuration → Tenants**:

![Tenant list](screenshots/config_tenants.png)

The tenant table shows each organisation's name, creation date, number of users, number of aircraft, and active/inactive status.  From this page the instance admin can:

- **Deactivate / reactivate** a tenant — deactivated tenants cannot log in; their data is preserved and can be restored at any time.
- **Reset the admin password** of any OWNER or ADMIN user within a tenant — generates a short-lived one-time token that is displayed on screen.  No email is required; relay the token to the tenant admin out-of-band (e.g. by phone or messaging).  The token forces a password change on first use and expires after 24 hours.

### Provisioning a new tenant

Click **Add tenant** (or **Add a second tenant** from the Settings page if this is your first expansion) to open the create-tenant form:

![Create tenant form](screenshots/config_tenants_create.png)

Fill in the tenant name, the operating model, and the email address of the person who will be the tenant's OWNER.  OpenHangar creates the organisation and sends an invitation link — the new owner follows the link to set a password and gets full access to their own independent tenant.

### Existing installations

If you upgrade an existing single-tenant installation to a version that includes multi-tenant support, the Alembic migration automatically promotes the oldest OWNER or ADMIN user to instance admin.  No manual steps are required.

---

## Security alerting

OpenHangar fires real-time alerts for high-severity security events (account
lockouts, TOTP replay attacks, privilege changes).  Three channels are available;
each is enabled only when its env var is set.

### ntfy (recommended)

[ntfy](https://ntfy.sh) delivers push notifications to Android and iOS via a
simple HTTP POST.  The free hosted service requires no account for private topics.

```bash
# .env
OPENHANGAR_ALERT_NTFY_TOPIC_URL=https://ntfy.sh/your-private-topic-name
```

To avoid your topic being public, choose a long random name
(`openssl rand -hex 16` works well).

**Self-hosted ntfy** (alerts survive even if OpenHangar is down):

```yaml
# docker-compose.yml — add alongside the openhangar service
ntfy:
  image: binwiederhier/ntfy
  command: serve
  volumes:
    - ./ntfy/data:/var/lib/ntfy
  ports:
    - "8080:80"
  restart: unless-stopped
```

Then set `OPENHANGAR_ALERT_NTFY_TOPIC_URL=http://ntfy:80/your-topic` (using the
Docker service name as hostname).

### Email

Reuses the existing `OPENHANGAR_SMTP_*` env vars.  Set `OPENHANGAR_SMTP_HOST` (and friends) first,
then add:

```bash
OPENHANGAR_ALERT_EMAIL_TO=admin@example.com
```

### Webhook (Slack, Discord, custom)

```bash
OPENHANGAR_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...
```

The payload is `{"event": "<type>", "detail": "<formatted log line>"}`.

All three channels can be active simultaneously.  See the
[configuration reference](configuration.md#security-alerting) for the full
variable list and validation rules.

---

## Security notes

- Set `OPENHANGAR_SECRET_KEY` to a long random string; never use the default in production.
- Set `OPENHANGAR_BACKUP_ENCRYPTION_KEY` and store it separately from the backup files (e.g. in a password manager). Without it a backup cannot be decrypted.
- Terminate TLS at the reverse proxy; do not expose port 5000 directly to the internet.
- The container runs as a non-root user (`appuser`).
- Users can enable TOTP 2FA from their profile page; enforce it by policy.
- `OPENHANGAR_ENV` defaults to `production`; never set it to `development` on an internet-facing host.
