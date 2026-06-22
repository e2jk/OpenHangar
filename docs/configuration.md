# OpenHangar — Configuration Reference

All configuration is done via environment variables, typically in your
`docker-compose.yml` or a `.env` file alongside it.

Every variable that OpenHangar reads starts with `OPENHANGAR_`.

---

## Master variable list

| Variable | Required | Default | Section |
|---|---|---|---|
| [`OPENHANGAR_DATABASE_URL`](#openhangar_database_url) | Yes | — | [Database](#database) |
| [`OPENHANGAR_SECRET_KEY`](#openhangar_secret_key) | Yes | — | [Core](#core) |
| [`OPENHANGAR_ENV`](#openhangar_env) | No | `production` | [Core](#core) |
| [`OPENHANGAR_SESSION_LIFETIME_DAYS`](#openhangar_session_lifetime_days) | No | `30` | [Core](#core) |
| [`OPENHANGAR_UPLOAD_FOLDER`](#openhangar_upload_folder) | No | `/data/uploads` | [Storage](#storage) |
| [`OPENHANGAR_BACKUP_FOLDER`](#openhangar_backup_folder) | No | `/data/backups` | [Storage](#storage) |
| [`OPENHANGAR_BACKUP_ENCRYPTION_KEY`](#openhangar_backup_encryption_key) | No | *(unencrypted)* | [Storage](#storage) |
| [`OPENHANGAR_MAX_UPLOAD_BYTES`](#openhangar_max_upload_bytes) | No | `52428800` | [Storage](#storage) |
| [`OPENHANGAR_SYNC_SCAN_INTERVAL`](#openhangar_sync_scan_interval) | No | `60` | [Storage](#storage) |
| [`OPENHANGAR_SMTP_HOST`](#openhangar_smtp_host) | No | — | [Email](#email) |
| [`OPENHANGAR_SMTP_PORT`](#openhangar_smtp_port) | No | `587` | [Email](#email) |
| [`OPENHANGAR_SMTP_USER`](#openhangar_smtp_user) | No | — | [Email](#email) |
| [`OPENHANGAR_SMTP_PASSWORD`](#openhangar_smtp_password) | No | — | [Email](#email) |
| [`OPENHANGAR_SMTP_USE_TLS`](#openhangar_smtp_use_tls) | No | `true` | [Email](#email) |
| [`OPENHANGAR_SMTP_FROM_ADDRESS`](#openhangar_smtp_from_address) | No | — | [Email](#email) |
| [`OPENHANGAR_SMTP_FROM_NAME`](#openhangar_smtp_from_name) | No | `OpenHangar` | [Email](#email) |
| [`OPENHANGAR_NOTIFICATION_TIME`](#openhangar_notification_time) | No | `08:00` | [Email](#email) |
| [`OPENHANGAR_ALERT_NTFY_TOPIC_URL`](#openhangar_alert_ntfy_topic_url) | No | — | [Security alerting](#security-alerting) |
| [`OPENHANGAR_ALERT_EMAIL_TO`](#openhangar_alert_email_to) | No | — | [Security alerting](#security-alerting) |
| [`OPENHANGAR_ALERT_WEBHOOK_URL`](#openhangar_alert_webhook_url) | No | — | [Security alerting](#security-alerting) |
| [`OPENHANGAR_OPENAIP_API_KEY`](#openhangar_openaip_api_key) | No | — | [Maps](#maps) |
| [`OPENHANGAR_AIRWORTHINESS_EASA_SYNC_HOUR`](#openhangar_airworthiness_easa_sync_hour) | No | *(random 1–5)* | [Airworthiness](#airworthiness) |
| [`OPENHANGAR_GATUS_ENDPOINT_URL`](#openhangar_gatus_endpoint_url) | No | — | [Monitoring](#monitoring) |
| [`OPENHANGAR_GATUS_AUTH_HEADER`](#openhangar_gatus_auth_header) | No | — | [Monitoring](#monitoring) |
| [`OPENHANGAR_DEMO_SITE_URL`](#openhangar_demo_site_url) | No | — | [Demo mode](#demo-mode) |
| [`OPENHANGAR_DEMO_NEXT_WIPE_UTC`](#openhangar_demo_next_wipe_utc) | No | — | [Demo mode](#demo-mode) |
| [`OPENHANGAR_DEMO_SLOT_COUNT`](#openhangar_demo_slot_count) | No | `20` | [Demo mode](#demo-mode) |
| [`OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES`](#openhangar_demo_busy_window_minutes) | No | `60` | [Demo mode](#demo-mode) |
| [`OPENHANGAR_INSTANCE_URL`](#openhangar_instance_url) | No | — | [Core](#core) |
| [`OPENHANGAR_REPO_URL`](#openhangar_repo_url) | No | GitHub URL | [Core](#core) |

---

## Core

### `OPENHANGAR_SESSION_LIFETIME_DAYS`

How long a logged-in session remains valid, in days.

- **Default**: `30`
- **Example**: `OPENHANGAR_SESSION_LIFETIME_DAYS=90`

Increase this if users (especially PWA users on mobile) find themselves logged
out too frequently. The session cookie is `HttpOnly`, `Secure`, and
`SameSite=Lax`; with TOTP enforced, a longer lifetime is a reasonable
trade-off against the re-authentication burden.

### `OPENHANGAR_SECRET_KEY`

Flask session signing key. Protects session cookies and CSRF tokens.

- **Required**: yes — startup fails if absent or a known placeholder
- **Minimum length**: 32 characters
- **Generate with**: `openssl rand -hex 32`
- **Never reuse** across instances; rotate by restarting the container

### `OPENHANGAR_ENV`

Application environment. Controls debug output, seed data loading, and email
suppression.

- **Allowed values**: `production`, `development`, `test`, `demo`
- **Default**: `production`
- Never set to `development` on an internet-facing host.

### `OPENHANGAR_INSTANCE_URL`

Public URL of this OpenHangar instance. When set, it is included in the footer
of outgoing emails so recipients know which instance sent the message.

- **Default**: unset — emails show a generic "this OpenHangar instance" phrase
- **Example**: `https://hangar.example.com`
- Must be a fully qualified URL (including `https://`).
- Typically derived from `OPENHANGAR_HOSTNAME` in the compose file:
  `OPENHANGAR_INSTANCE_URL=https://${OPENHANGAR_HOSTNAME}`

### `OPENHANGAR_REPO_URL`

Public repository URL shown in the footer and update-check notifications.

- **Default**: `https://github.com/e2jk/OpenHangar`
- Override only if you fork and self-host a renamed instance.

---

## Database

### `OPENHANGAR_DATABASE_URL`

PostgreSQL connection string.

- **Required**: yes in production
- **Format**: `postgresql://user:password@host:port/dbname`
- SQLite (`sqlite:///:memory:`) is accepted in `development` and `test` environments only.
- Startup validation rejects non-PostgreSQL schemes in `production` and `demo` modes.

---

## Storage

### `OPENHANGAR_UPLOAD_FOLDER`

Host path inside the container where uploaded documents and photos are stored.

- **Default**: `/data/uploads`
- Mount a host directory or a Syncthing-shared directory here — see the
  [Document storage & Syncthing/file syncing](self-hosting.md#document-storage--syncthing-file-syncing) guide.

### `OPENHANGAR_BACKUP_FOLDER`

Host path inside the container where encrypted backup archives are written.

- **Default**: `/data/backups`
- Mount a host directory so backups survive container restarts.

### `OPENHANGAR_BACKUP_ENCRYPTION_KEY`

Passphrase used to AES-256-GCM encrypt backup ZIP archives.

- **Default**: unset — backups are stored unencrypted
- Whitespace-only values are rejected at startup.
- **Store the key separately from the backup files** (e.g. in a password manager).
  Without it a backup cannot be decrypted.
- **Generate with**: `openssl rand -hex 32`

### `OPENHANGAR_MAX_UPLOAD_BYTES`

Maximum file size accepted for uploads (GPS files, photos, documents). Flask
returns HTTP 413 if exceeded.

- **Default**: `52428800` (50 MB)
- Must be a positive integer (bytes). No suffixes like `50MB`.

### `OPENHANGAR_SYNC_SCAN_INTERVAL`

How often (in seconds) the background watcher scans `OPENHANGAR_UPLOAD_FOLDER`
for new files that arrived via Syncthing or manual copy.

- **Default**: `60`
- Must be a positive integer.

---

## Email {#email}

Outbound email is used for transactional messages such as welcome emails,
maintenance alerts, and airworthiness notifications.

All settings are read at send time — no database row is involved. To apply a
change, update your `.env` / `docker-compose.yml` and restart the container.

The Configuration page (`/config/`) shows the current status of each variable
(password masked; unset variables clearly marked).

Email is **disabled** when `OPENHANGAR_SMTP_HOST` or `OPENHANGAR_SMTP_FROM_ADDRESS`
is unset, or when `OPENHANGAR_ENV=demo`.

> **Note — Sent folder:** OpenHangar submits mail via SMTP and does not save a
> copy to the sender's Sent folder. If you want outgoing messages to appear
> there, configure your mail server to do so (e.g. a Sieve "fileinto" rule, or
> a per-mailbox "copy to Sent" setting if your server supports it).

### `OPENHANGAR_SMTP_HOST`

SMTP server hostname.

- **Required** to enable email
- Example: `smtp.gmail.com`, `mail.example.com`

### `OPENHANGAR_SMTP_PORT`

SMTP server port.

- **Default**: `587`
- Use `587` for STARTTLS (recommended), `465` for implicit TLS, `25` for plain SMTP.
- Must be an integer between 1 and 65535.

### `OPENHANGAR_SMTP_USER`

SMTP login username. Leave unset for unauthenticated relays.

### `OPENHANGAR_SMTP_PASSWORD`

SMTP login password.

### `OPENHANGAR_SMTP_USE_TLS`

Whether to use STARTTLS.

- **Default**: `true`
- Set to `false` for plain SMTP (not recommended in production).

### `OPENHANGAR_SMTP_FROM_ADDRESS`

The `From` address for all outgoing email.

- **Required** to enable email
- Example: `no-reply@example.com`

### `OPENHANGAR_SMTP_FROM_NAME`

Display name shown alongside the `From` address.

- **Default**: `OpenHangar`

### `OPENHANGAR_NOTIFICATION_TIME`

Time of day (UTC) at which daily maintenance-due notifications are sent.

- **Default**: `08:00`
- Format: `HH:MM` (24-hour UTC). Example: `07:30`

### Example `.env` snippet

```dotenv
OPENHANGAR_SMTP_HOST=smtp.gmail.com
OPENHANGAR_SMTP_PORT=587
OPENHANGAR_SMTP_USER=your-account@gmail.com
OPENHANGAR_SMTP_PASSWORD=your-app-password
OPENHANGAR_SMTP_USE_TLS=true
OPENHANGAR_SMTP_FROM_ADDRESS=your-account@gmail.com
OPENHANGAR_SMTP_FROM_NAME=OpenHangar
```

> **Gmail note:** Use an [App Password](https://support.google.com/accounts/answer/185833)
> rather than your account password.  App Passwords require 2-Step Verification to be enabled.

---

## Security alerting

When a high-severity security event occurs (account lockout, TOTP replay attack,
privilege change), OpenHangar can push a real-time alert via up to three channels.
Each channel is independently enabled by setting its env var; unset vars silently
disable that channel. A 60-second debounce per event type prevents alert storms.

**Escalated events that trigger an alert:**
- `auth.login.account_locked` / `auth.login.account_blocked` — active brute force
- `auth.totp.replay` — targeted session attack
- `users.role.changed` / `users.access.revoked` — post-authentication privilege change

### `OPENHANGAR_ALERT_NTFY_TOPIC_URL`

ntfy topic URL for push notifications.

- Example: `https://ntfy.sh/your-private-topic`
- Works with the free hosted service or a self-hosted ntfy instance.
- Must start with `http://` or `https://`.
- Choose a long random topic name to keep it private (`openssl rand -hex 16`).

### `OPENHANGAR_ALERT_EMAIL_TO`

Recipient address for security alert emails.

- Example: `admin@example.com`
- Requires `OPENHANGAR_SMTP_HOST` and `OPENHANGAR_SMTP_FROM_ADDRESS` to also be set.
- Startup validation rejects this setting if SMTP is not configured.

### `OPENHANGAR_ALERT_WEBHOOK_URL`

HTTP(S) endpoint that receives a JSON POST `{"event": "...", "detail": "..."}`.

- Covers Slack/Discord incoming webhooks and custom receivers.
- Must start with `http://` or `https://`.

All three channels can be active simultaneously. See
[self-hosting.md](self-hosting.md#security-alerting) for setup examples.

---

## Maps

### `OPENHANGAR_OPENAIP_API_KEY`

[OpenAIP](https://www.openaip.net/) API key for aviation map tiles (airspaces,
airways, airports).

- When set, the container writes the key into the database on startup, overwriting
  any value previously saved via the UI.
- Removing the variable leaves the stored key untouched.

---

## Airworthiness

### `OPENHANGAR_AIRWORTHINESS_EASA_SYNC_HOUR`

Hour (UTC, 0–23) at which the daily EASA airworthiness sync runs (ADs, SIBs).

- **Default**: a random hour between 01:00 and 05:00 UTC chosen at startup, so
  that different instances do not all hit the EASA servers at the same time.
- Example: `3` (runs at 03:00 UTC)
- Only active in `production` mode.

---

## Monitoring

When `OPENHANGAR_GATUS_ENDPOINT_URL` is set, the **System** section of the
config page displays four live SVG badges fetched from your Gatus instance:
uptime over 24 h and 30 d, and response time over 24 h and 30 d.  The fetch
is done server-side, so credentials never reach the browser.

### `OPENHANGAR_GATUS_ENDPOINT_URL`

Full URL to the Gatus endpoint detail page for this OpenHangar instance.
Setting this variable is sufficient to enable the monitoring section.

- Example: `https://uptime.example.com/endpoints/openhangar_openhangar-production`
- The URL must contain `/endpoints/` so OpenHangar can split it into a base URL
  and an endpoint key.  The key follows Gatus's `<GROUP>_<ENDPOINT-NAME>`
  convention (spaces and special characters replaced by hyphens).

### `OPENHANGAR_GATUS_AUTH_HEADER`

Base64-encoded `user:password` string used in the `Authorization: Basic …`
header when fetching badges from a password-protected Gatus instance.

- Encode with: `echo -n 'user:password' | base64`
- **Optional** — leave unset if your Gatus instance is publicly accessible.

### Example `.env` snippet

```bash
OPENHANGAR_GATUS_ENDPOINT_URL=https://uptime.example.com/endpoints/openhangar_openhangar-production
# Only needed if Gatus is behind HTTP Basic Auth:
# OPENHANGAR_GATUS_AUTH_HEADER=dXNlcjpwYXNzd29yZA==
```

---

## Demo mode

These variables are only meaningful when `OPENHANGAR_ENV=demo`.

### `OPENHANGAR_DEMO_SITE_URL`

Public URL of the demo instance. Used by the static GitHub Pages landing page
to render a "Try the demo" button.

- Example: `https://openhangar-demo.example.com/`

### `OPENHANGAR_DEMO_NEXT_WIPE_UTC`

ISO-8601 datetime of the next scheduled demo wipe. Shown in the pre-wipe countdown
banner.

- This variable is normally written automatically by `demo/refresh.sh` after
  each wipe run; you do not need to set it manually.
- Example: `2024-06-01T09:07:00Z`

### `OPENHANGAR_DEMO_SLOT_COUNT`

Number of isolated demo slots (independent user sessions) available simultaneously.

- **Default**: `20`
- Must be a positive integer.

### `OPENHANGAR_DEMO_BUSY_WINDOW_MINUTES`

How long (in minutes) a demo slot is considered "active" after its last use,
for the purpose of the "demo is busy" check.

- **Default**: `60`
- Must be a positive integer.
