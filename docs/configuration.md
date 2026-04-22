# OpenHangar — Configuration Reference

All configuration is done via environment variables, typically in your
`docker-compose.yml` or a `.env` file alongside it.

---

## Core

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | — | PostgreSQL connection string, e.g. `postgresql://user:pass@db/openhangar` |
| `SECRET_KEY` | Yes | `dev-insecure-change-me` | Flask session signing key — set a long random string in production |
| `FLASK_ENV` | No | `production` | `production`, `development`, `test`, or `demo` |
| `UPLOAD_FOLDER` | No | `/data/uploads` | Host path for uploaded documents and photos |
| `BACKUP_FOLDER` | No | `/data/backups` | Host path for encrypted backup files |
| `BACKUP_ENCRYPTION_KEY` | No | *(unencrypted)* | Passphrase used to AES-256-GCM encrypt backup ZIPs |

---

## Email {#email}

Outbound email is used for transactional messages such as welcome emails,
maintenance alerts, and reservation confirmations (future phases).

All settings are read at send time from environment variables — no database
row is involved.  To apply a change, update your `.env` / `docker-compose.yml`
and restart the container.

The Configuration page (`/config/`) shows the current status of each variable
(password masked; unset variables clearly marked).

| Variable | Required | Default | Description |
|---|---|---|---|
| `SMTP_HOST` | **Yes** (to enable email) | — | SMTP server hostname, e.g. `smtp.gmail.com` or `mail.example.com` |
| `SMTP_PORT` | No | `587` | SMTP server port.  Use `587` for STARTTLS (recommended), `465` for implicit TLS, `25` for plain SMTP |
| `SMTP_USER` | No | — | SMTP login username (leave unset for unauthenticated relays) |
| `SMTP_PASSWORD` | No | — | SMTP login password |
| `SMTP_USE_TLS` | No | `true` | Set to `true` to use STARTTLS (recommended), `false` for plain SMTP.  For port 465 implicit TLS, leave as `true` and set `SMTP_PORT=465` — note: implicit TLS support requires a future update |
| `SMTP_FROM_ADDRESS` | **Yes** (to enable email) | — | The `From` address for all outgoing email, e.g. `no-reply@example.com` |
| `SMTP_FROM_NAME` | No | `OpenHangar` | Display name shown alongside the `From` address |

Email is **disabled** when:
- `SMTP_HOST` is not set, or
- `SMTP_FROM_ADDRESS` is not set, or
- `FLASK_ENV=demo` (demo mode never sends real email)

### Example `.env` snippet

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-account@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_USE_TLS=true
SMTP_FROM_ADDRESS=your-account@gmail.com
SMTP_FROM_NAME=OpenHangar
```

> **Gmail note:** Use an [App Password](https://support.google.com/accounts/answer/185833)
> rather than your account password.  App Passwords require 2-Step Verification to be enabled.

---

## Demo mode

| Variable | Description |
|---|---|
| `DEMO_SITE_URL` | Public URL of the demo instance (e.g. `https://openhangar-demo.example.com/`).  Used by the static GitHub Pages landing page to render a "Try the demo" button. |
| `DEMO_NEXT_WIPE_UTC` | ISO-8601 datetime of the next scheduled demo wipe.  Shown in the pre-wipe countdown banner. This variable doesn't need to be set manually, the `demo/refresh.sh` script updates it after every run. |
