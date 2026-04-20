# Backup & Restore

## Overview

OpenHangar produces **encrypted database backups** — each backup is a
PostgreSQL dump compressed into a ZIP file and then encrypted with
AES-256-GCM using a key you supply via environment variable.

Backups are stored inside the container at `/data/backups`, which should be
a host-mounted volume so they survive container restarts.

---

## Configuration

Add these variables to your `.env` file (or the `environment:` block of your
`docker-compose.yml`):

| Variable | Default | Description |
|---|---|---|
| `BACKUP_ENCRYPTION_KEY` | *(empty — unencrypted!)* | Passphrase used to derive the AES-256 key. Set this before running any backup. |
| `BACKUP_FOLDER` | `/data/backups` | Path **inside** the container where backup files are written. Mount a host directory here. |

Example `docker-compose.yml` snippet:

```yaml
services:
  web:
    environment:
      - BACKUP_ENCRYPTION_KEY=${BACKUP_ENCRYPTION_KEY}
      - BACKUP_FOLDER=/data/backups
    volumes:
      - ./openhangar/backups:/data/backups
```

> **Keep `BACKUP_ENCRYPTION_KEY` secret.** Without it the backup cannot be
> decrypted. Store it in a password manager separate from the backup files.

---

## Running a backup

### Via the web UI

Navigate to **Backups** in the navbar and click **Backup now**.

### Via the CLI (recommended for cron)

```bash
docker compose exec web flask backup-now
```

### Automated daily backups with cron

On the Docker host, add a cron job:

```
0 2 * * * docker compose -f /path/to/docker-compose.yml exec -T web flask backup-now >> /var/log/openhangar-backup.log 2>&1
```

Add a logrotate config to keep logs tidy
(`/etc/logrotate.d/openhangar-backup`):

```
/var/log/openhangar-backup.log {
    size 1M
    rotate 16
    compress
    missingok
    notifempty
    copytruncate
}
```

---

## Backup file format

Each backup file is named:

```
openhangar_backup_<YYYYMMDDTHHMMSSZ>.zip.enc
```

When `BACKUP_ENCRYPTION_KEY` is set the file contains:

```
[12 bytes nonce][AES-256-GCM ciphertext]
```

The ciphertext decrypts to a standard ZIP archive containing a single file,
`openhangar.sql`, which is the output of `pg_dump`.

---

## Restoring a backup

### 1 — Decrypt the backup file

Run this Python script (requires the `cryptography` package):

```python
import hashlib, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

key = hashlib.sha256(b"YOUR_BACKUP_ENCRYPTION_KEY").digest()
with open("openhangar_backup_TIMESTAMP.zip.enc", "rb") as fh:
    data = fh.read()

nonce, ct = data[:12], data[12:]
zip_bytes = AESGCM(key).decrypt(nonce, ct, None)

with open("openhangar_backup.zip", "wb") as fh:
    fh.write(zip_bytes)
print("Decrypted → openhangar_backup.zip")
```

If the backup was created **without** an encryption key, skip this step — the
file is already a plain ZIP.

### 2 — Extract the SQL dump

```bash
unzip openhangar_backup.zip   # produces openhangar.sql
```

### 3 — Restore into PostgreSQL

```bash
# Drop and recreate the database (adjust credentials as needed)
docker compose exec db psql -U postgres -c "DROP DATABASE IF EXISTS openhangar;"
docker compose exec db psql -U postgres -c "CREATE DATABASE openhangar OWNER postgres;"

# Restore
docker compose exec -T db psql -U postgres openhangar < openhangar.sql
```

### 4 — Restart the web container

```bash
docker compose restart web
```

The app will run `flask db upgrade` on startup to apply any pending
migrations, then start normally.

---

## Verifying a backup

Each backup record in the database includes a **SHA-256 checksum** of the
encrypted file. To verify a file on the host:

```bash
sha256sum openhangar_backup_TIMESTAMP.zip.enc
```

Compare the output against the `sha256` column shown in the Backups UI or in
the `backup_records` table.
