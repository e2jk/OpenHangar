# OpenHangar — Self-Hosting Guide

This guide covers everything you need to deploy and operate OpenHangar on
your own Docker host.

---

## Prerequisites

- Docker and Docker Compose installed on the host.
- A PostgreSQL database (easiest: a `db` service in the same Compose file).
- *(Optional)* A reverse proxy such as Traefik or nginx for HTTPS.

---

## Quick start

Minimal `docker-compose.yml`:

```yaml
services:
  db:
    image: postgres:16
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
      UPLOAD_FOLDER: /data/uploads
      BACKUP_FOLDER: /data/backups
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
| `SMTP_HOST` + `SMTP_FROM_ADDRESS` | Required to enable email notifications |

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

Pull the new image and restart the web container:

```bash
docker compose pull web
docker compose up -d web
```

The container runs `flask db upgrade` automatically on startup — database
migrations are applied without manual intervention.

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

## Security notes

- Set `SECRET_KEY` to a long random string; never use the default in production.
- Set `BACKUP_ENCRYPTION_KEY` and store it separately from the backup files (e.g. in a password manager). Without it a backup cannot be decrypted.
- Terminate TLS at the reverse proxy; do not expose port 5000 directly to the internet.
- The container runs as a non-root user (`appuser`).
- Users can enable TOTP 2FA from their profile page; enforce it by policy.
- `FLASK_ENV` defaults to `production`; never set it to `development` on an internet-facing host.
