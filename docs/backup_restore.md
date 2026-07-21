# Backup & Restore

## Overview

OpenHangar produces **encrypted full backups** — each backup is a ZIP archive
containing the PostgreSQL database dump, all uploaded documents, and a
metadata file recording the exact app version and Alembic revision the backup
was made with. The archive is encrypted with AES-256-GCM using a key you
supply via environment variable.

Backups are stored inside the container at `/data/backups`, which should be a
host-mounted volume so they survive container restarts. The restore script is
published to the same folder automatically on every container startup.

---

## Configuration

Add these variables to your `.env` file (or the `environment:` block of your
`docker-compose.yml`):

| Variable | Default | Description |
|---|---|---|
| `OPENHANGAR_BACKUP_ENCRYPTION_KEY` | *(empty — unencrypted!)* | Passphrase used to derive the AES-256 key. Set this before running any backup. |
| `OPENHANGAR_RESTORE_ENCRYPTION_KEY` | *(unset)* | Key used **only for restoring** backups. Set this when the backup was created with a different key than the one currently configured — e.g. restoring a production backup onto a development server. If unset, the restore script prompts interactively. |
| `OPENHANGAR_BACKUP_FOLDER` | `/data/backups` | Path **inside** the container where backup files are written. Mount a host directory here. |
| `OPENHANGAR_BACKUP_TIME` | *(unset — scheduling disabled)* | `HH:MM` (UTC) at which the built-in scheduler runs a daily backup. See [Built-in daily scheduling](#built-in-daily-scheduling-recommended) below. |
| `OPENHANGAR_BACKUP_RETENTION` | `simple` | `simple` or `gfs` — which retention scheme prunes old backups after a successful scheduled backup. See [Retention schemes](#retention-schemes) below. |
| `OPENHANGAR_BACKUP_KEEP` | `30` | `simple` scheme only: how many of the newest backups to keep. |
| `OPENHANGAR_BACKUP_KEEP_DAYS` | `7` | `gfs` scheme only: days of history to keep in full. |
| `OPENHANGAR_BACKUP_KEEP_WEEKS` | `4` | `gfs` scheme only: weeks to keep one backup per week after the daily window. |
| `OPENHANGAR_BACKUP_KEEP_MONTHS` | `12` | `gfs` scheme only: months to keep one backup per month after the weekly window; older backups are kept one per year, forever. |

Example `docker-compose.yml` snippet:

```yaml
services:
  web:
    environment:
      - OPENHANGAR_BACKUP_ENCRYPTION_KEY=${OPENHANGAR_BACKUP_ENCRYPTION_KEY}
      - OPENHANGAR_RESTORE_ENCRYPTION_KEY=${OPENHANGAR_RESTORE_ENCRYPTION_KEY}
      - OPENHANGAR_BACKUP_FOLDER=/data/backups
    volumes:
      - ./openhangar/data/backups:/data/backups
      - ./openhangar/data/uploads:/data/uploads
```

Both variables can share a single value in `.env` (e.g. on a same-server restore where the key is the same), or use different values when restoring across environments:

```ini
# .env — production server: one key for both creating and restoring backups
OPENHANGAR_BACKUP_ENCRYPTION_KEY=your-secret-passphrase

# .env — development server: separate key for restoring production backups
OPENHANGAR_BACKUP_ENCRYPTION_KEY=dev-backup-key
OPENHANGAR_RESTORE_ENCRYPTION_KEY=production-backup-key
```

> **Keep `OPENHANGAR_BACKUP_ENCRYPTION_KEY` secret.** Without it a backup cannot be
> decrypted. Store it in a password manager separate from the backup files.

---

## Running a backup

### Via the web UI

Navigate to **Configuration** in the navbar and click **Backup now**.

![Configuration page — backup section](screenshots/config.png)

### Built-in daily scheduling (recommended)

Set `OPENHANGAR_BACKUP_TIME` (HH:MM, UTC) in the `environment:` section of
your compose file and the application backs itself up once a day — no
host-side moving parts (no cron, no extra container):

```yaml
services:
  web:
    environment:
      - OPENHANGAR_BACKUP_TIME=02:30
```

Only one gunicorn worker performs the backup per day (guarded by a
PostgreSQL advisory lock), so this is safe with the default multi-worker
setup. If `OPENHANGAR_BACKUP_TIME` is left unset, scheduling stays disabled
and nothing changes from on-demand/`flask backup-now` behaviour.

After every **successful** scheduled backup, retention prunes the backup
folder so it cannot grow without bound — a failed backup never triggers
pruning, so a broken pipeline can never delete backups you still have. The
Configuration page shows the active schedule and the age of the last
successful backup, and warns when it is older than 2 days while scheduling
is enabled.

Retention only runs after a *scheduled* backup — backups triggered manually
via the web UI or `flask backup-now` are never pruned automatically.

#### Retention schemes

Set `OPENHANGAR_BACKUP_RETENTION` to choose how pruning decides what to keep:

- **`simple`** (default) — keep the newest `OPENHANGAR_BACKUP_KEEP` backups
  (default 30), delete the rest:

  ```yaml
      environment:
        - OPENHANGAR_BACKUP_TIME=02:30
        - OPENHANGAR_BACKUP_KEEP=30   # optional, this is the default
  ```

- **`gfs`** (grandfather-father-son) — keep *every* backup for
  `OPENHANGAR_BACKUP_KEEP_DAYS` days (default 7), then the newest backup of
  each week for `OPENHANGAR_BACKUP_KEEP_WEEKS` weeks (default 4), then the
  newest of each month for `OPENHANGAR_BACKUP_KEEP_MONTHS` months
  (default 12), then the newest of each year forever. With the defaults
  that's 7 daily + 4 weekly + 12 monthly + 1/year — roughly a year of
  granular history without unbounded growth:

  ```yaml
      environment:
        - OPENHANGAR_BACKUP_TIME=02:30
        - OPENHANGAR_BACKUP_RETENTION=gfs
        - OPENHANGAR_BACKUP_KEEP_DAYS=7      # optional, these three are the defaults
        - OPENHANGAR_BACKUP_KEEP_WEEKS=4
        - OPENHANGAR_BACKUP_KEEP_MONTHS=12
  ```

  Weeks/months/years are counted as distinct calendar periods that actually
  have a backup (not fixed day-count windows), so a gap in the schedule
  never shrinks the retained history — it just means fewer backups exist
  for that period.

### Via the CLI

```bash
docker compose exec web flask backup-now
```

### Automated daily backups with host cron (alternative)

If you prefer an external scheduler over the built-in one, add a cron job on
the Docker host (note: this path does not apply retention — prune old
archives yourself):

```
0 2 * * * docker compose -f /path/to/docker-compose.yml exec -T web flask backup-now >> /var/log/openhangar-backup.log 2>&1
```

---

## Backup file format

Each backup produces two files:

```
openhangar_backup_<YYYYMMDDTHHMMSSZ>.zip.enc   ← encrypted archive
openhangar_backup_<YYYYMMDDTHHMMSSZ>.meta      ← unencrypted version sidecar
```

The `.meta` file contains version information in plain JSON so the restore
script can read the backup's version without decrypting anything:

```json
{
  "app_version": "0.200.0",
  "alembic_head": "3f8a2c91b047",
  "created_at": "2026-05-19T12:00:00+00:00"
}
```

When `BACKUP_ENCRYPTION_KEY` is set the `.zip.enc` file contains:

```
[12 bytes nonce][AES-256-GCM ciphertext]
```

The ciphertext decrypts to a standard ZIP archive:

```
openhangar.sql          ← full pg_dump output
metadata.json           ← same version info as the .meta sidecar
uploads/
  doc_ac1_abc123.pdf    ← uploaded documents
  …
```

---

## Restoring a backup

> **Important:** Restore only into a container with an **empty database**
> (freshly started, no existing users or data). Restoring into a non-empty
> database is blocked automatically.

### Automated restore (recommended)

The restore script is published to your backups folder on every container
startup. Run it from the Docker host:

```bash
# Default: restore then upgrade container to latest image
/path/to/openhangar/data/backups/restore.sh openhangar_backup_TIMESTAMP.zip.enc

# Upgrade to a specific version after restore
/path/to/openhangar/data/backups/restore.sh openhangar_backup_TIMESTAMP.zip.enc --upgrade-to=v0.300.0

# Restore only — do not upgrade the image afterwards
/path/to/openhangar/data/backups/restore.sh openhangar_backup_TIMESTAMP.zip.enc --upgrade-to=none
```

#### Decryption key

For encrypted archives (`.zip.enc`), the script resolves the decryption key in this order:

1. **`--key-file=PATH`** — path to a file containing the raw key (useful for scripted restores)
2. **`OPENHANGAR_RESTORE_ENCRYPTION_KEY`** in the current shell environment
3. **`OPENHANGAR_RESTORE_ENCRYPTION_KEY`** already set inside the container — the recommended "set once" approach: map your source environment's backup key to this variable in `docker-compose.yml` (e.g. `OPENHANGAR_RESTORE_ENCRYPTION_KEY=${OPENHANGAR_PROD_BACKUP_ENCRYPTION_KEY}`) and the script uses it automatically without any prompt
4. **Interactive prompt** — the key is typed once, never stored in shell history or visible in process arguments

When the key comes from step 1 or 2 it is passed to the container via `docker exec -e VARNAME` (name only, not `NAME=VALUE`); the value never touches disk and never appears in `ps` output. When using step 3 the `docker exec` call carries no key at all — the container uses its own environment directly.

> **`OPENHANGAR_BACKUP_ENCRYPTION_KEY` is never used for restoration**, even if it is set.
> This prevents silently attempting to decrypt with the wrong key when the backup originates
> from a different environment. Always supply the key through one of the methods above.

The script:
1. Reads the `.meta` sidecar to determine the backup's app version
2. Verifies the database is empty (`flask check-empty-db`)
3. Calls `flask restore-backup` inside the container, which:
   - Verifies the backup's Alembic revision is in this container's migration chain
   - Drops the current (empty) schema
   - Restores the database from the SQL dump
   - Restores uploaded files
4. Pulls the target image and restarts the container
5. Alembic runs `upgrade head` automatically on startup, migrating from the
   backup's schema version to the latest

**Version compatibility:** The restore command rejects a backup whose Alembic
revision is not known to the running container — this prevents restoring a
backup made with a newer version of the app into an older container. In that
case, pull the matching or newer image first, or use `--upgrade-to=vX.Y.Z`
with the appropriate target version.

### `--upgrade-to` options

| Flag | Behaviour |
|---|---|
| `--upgrade-to=latest` | *(default)* Pull the latest image and restart; Alembic migrates on startup |
| `--upgrade-to=vX.Y.Z` | Pull a specific release and restart |
| `--upgrade-to=none` | Leave the container at the backup's image version; restart manually to apply migrations |

### Manual restore (advanced)

If you prefer to restore without the script, use the Flask CLI directly.
The container must be running and its database must be empty.

```bash
# Check the database is empty
docker compose exec web flask check-empty-db

# Restore (decryption, schema drop, psql, uploads all handled automatically)
# For encrypted archives, set OPENHANGAR_RESTORE_ENCRYPTION_KEY first:
read -rsp "Decryption key: " KEY && export OPENHANGAR_RESTORE_ENCRYPTION_KEY="$KEY" && unset KEY
docker compose exec -e OPENHANGAR_RESTORE_ENCRYPTION_KEY web flask restore-backup /data/backups/openhangar_backup_TIMESTAMP.zip.enc
unset OPENHANGAR_RESTORE_ENCRYPTION_KEY

# Restart to apply Alembic migrations
docker compose restart web
```

If you need to restore on a system without Docker or outside the container,
use the legacy manual steps below.

### Legacy manual restore

<details>
<summary>Expand for step-by-step manual instructions</summary>

#### 1 — Decrypt the backup file

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

key = HKDF(
    algorithm=hashes.SHA256(),
    length=32,
    salt=b"openhangar-backup-kdf-salt-v1",
    info=b"openhangar-backup-v1",
).derive(b"YOUR_BACKUP_ENCRYPTION_KEY")

with open("openhangar_backup_TIMESTAMP.zip.enc", "rb") as fh:
    data = fh.read()

nonce, ct = data[:12], data[12:]
zip_bytes = AESGCM(key).decrypt(nonce, ct, None)

with open("openhangar_backup.zip", "wb") as fh:
    fh.write(zip_bytes)
```

If the backup was created without an encryption key, skip this step.

#### 2 — Check the backup version

```bash
cat openhangar_backup_TIMESTAMP.meta
```

Note the `app_version` and `alembic_head`. You need a container running at
least that version to restore successfully.

#### 3 — Extract and restore

```bash
unzip openhangar_backup.zip

# Restore uploaded documents
cp -r uploads/. /path/to/host/uploads/

# Drop and recreate the database
docker compose exec db psql -U postgres -c "DROP DATABASE IF EXISTS openhangar;"
docker compose exec db psql -U postgres -c "CREATE DATABASE openhangar OWNER postgres;"

# Restore the SQL dump
docker compose exec -T db psql -U postgres openhangar < openhangar.sql

# Restart the web container (runs Alembic migrations on startup)
docker compose restart web
```

</details>

---

## Verifying a backup

Each backup record in the database includes a **SHA-256 checksum** of the
encrypted file. To verify on the host:

```bash
sha256sum openhangar_backup_TIMESTAMP.zip.enc
```

Compare against the `sha256` column shown in the Configuration page or in the
`backup_records` table.

### Automated restore verification (every scheduled backup)

Every time the [built-in daily scheduler](#built-in-daily-scheduling-recommended)
produces a backup, OpenHangar immediately decrypts it and checks it is
well-formed: every file in the archive passes its zip CRC check,
`openhangar.sql` is present and looks like real `pg_dump` output, and
`metadata.json` is present and parses as valid JSON. This catches a broken
backup pipeline (bad key, disk corruption, a truncated write) the same day
it happens, not months later during a real restore.

A failure fires a `[SECURITY] backup.verification_failed` alert through
whichever channels are configured — see
[Security alerting](self-hosting.md#security-alerting). This check does not
run for on-demand backups (web UI / `flask backup-now`), matching retention's
scheduled-only scope; verify one manually with the sha256sum command above
if needed.

This only proves the archive is intact and decryptable — it does not spin up
a scratch database and replay the SQL dump. For that level of confidence,
run the quarterly restore drill below.

### Quarterly restore drill (manual, recommended)

The checks above prove a backup is well-formed; they don't prove Postgres
would actually accept every statement in it. Periodically (quarterly is a
reasonable cadence) do a real restore against a **disposable, isolated**
stack — never against production:

```bash
# 1. Pick a recent backup and copy it (and its .meta sidecar) somewhere the
#    scratch stack can reach, e.g. a fresh directory next to your compose files:
mkdir -p /tmp/openhangar-restore-drill
cp /path/to/openhangar/data/backups/openhangar_backup_TIMESTAMP.zip.enc \
   /path/to/openhangar/data/backups/openhangar_backup_TIMESTAMP.meta \
   /tmp/openhangar-restore-drill/

# 2. Stand up an isolated scratch compose project — a separate .env with a
#    different OPENHANGAR_HOSTNAME/TRAEFIK_HOSTNAME and fresh
#    ./openhangar/data/{postgres,uploads,backups} directories, so it shares
#    nothing with the real deployment:
docker compose -p openhangar-restore-drill --env-file .env.restore-drill \
  -f docker-compose.yml up -d

# 3. Copy the backup into the scratch stack's mounted backups folder, then run
#    restore.sh exactly as documented above (Automated restore), or the
#    manual `flask restore-backup` steps — against THIS project only:
docker compose -p openhangar-restore-drill exec web flask check-empty-db
docker compose -p openhangar-restore-drill exec -e OPENHANGAR_RESTORE_ENCRYPTION_KEY \
  web flask restore-backup /data/backups/openhangar_backup_TIMESTAMP.zip.enc

# 4. Confirm the app is usable — log in, check a few records look right, spot
#    check an uploaded document opens.

# 5. Tear the scratch stack down completely, including its volumes:
docker compose -p openhangar-restore-drill down -v
rm -rf /tmp/openhangar-restore-drill
```

Record the date and outcome of each drill somewhere durable (a runbook, an
issue tracker, a calendar reminder note) — the value of this drill is
catching restore-path bit-rot (a schema assumption that quietly broke, an
undocumented manual step) before an actual disaster forces you to find out
live.
