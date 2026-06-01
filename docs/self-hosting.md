# OpenHangar — Self-Hosting Guide

This guide covers everything you need to deploy and operate OpenHangar on
your own Docker host.

> **New to self-hosting?** The [Raspberry Pi guide](raspberry-pi.md) walks
> you through the entire process — from blank SD card to working installation
> — with every command spelled out and no decisions left to you.

---

## Prerequisites

- Docker and Docker Compose installed on the host.
- A PostgreSQL database (easiest: a `db` service in the same Compose file).
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
3. Start the stack:
   ```bash
   docker compose up -d
   ```

### Minimal setup (no reverse proxy)

Without Traefik, a minimal `docker-compose.yml` that exposes port 5000 directly:

```yaml
services:
  db:
    image: postgres:18
    environment:
      POSTGRES_DB: openhangar
      POSTGRES_USER: openhangar
      POSTGRES_PASSWORD: changeme
    volumes:
      - db_data:/var/lib/postgresql/data

  web:
    image: ghcr.io/e2jk/openhangar:latest
    depends_on:
      - db
    environment:
      DATABASE_URL: postgresql://openhangar:changeme@db/openhangar
      SECRET_KEY: change-this-to-a-long-random-string
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
| `DATABASE_URL` | PostgreSQL connection string |
| `SECRET_KEY` | Long random string — protects session cookies (`openssl rand -hex 32`) |
| `BACKUP_ENCRYPTION_KEY` | Encrypts backup files; keep this separate from the backups themselves |
| `SMTP_HOST` | Required to enable email notifications (also set `SMTP_FROM_ADDRESS`, `SMTP_USER`, `SMTP_PASSWORD`) |

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
docker compose exec web flask backup-now
```

---

## Upgrades

OpenHangar checks for new releases daily and displays a notification in the
Settings page when an update is available.

To upgrade, pull the new image and restart the web container:

```bash
docker compose pull web
docker compose up -d web
```

The container runs `alembic upgrade head` automatically on startup to apply
any pending database migrations.

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
- **Background tasks**: no separate worker in v1 — backups are triggered on-demand or via a host cron job calling `flask backup-now`.

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

## Security notes

- Set `SECRET_KEY` to a long random string; never use the default in production.
- Set `BACKUP_ENCRYPTION_KEY` and store it separately from the backup files (e.g. in a password manager). Without it a backup cannot be decrypted.
- Terminate TLS at the reverse proxy; do not expose port 5000 directly to the internet.
- The container runs as a non-root user (`appuser`).
- Users can enable TOTP 2FA from their profile page; enforce it by policy.
- `FLASK_ENV` defaults to `production`; never set it to `development` on an internet-facing host.
