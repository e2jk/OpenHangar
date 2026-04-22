# OpenHangar — Demo Deployment Guide

This folder contains everything needed to run the public demo instance of OpenHangar on a VPS.

## Prerequisites

- Docker and Docker Compose installed on the VPS
- Traefik already running with a `traefik-network` external network and a Let's Encrypt cert resolver
- A DNS A record pointing your demo hostname (e.g. `demo.openhangar.aero`) to the VPS IP

> **Note:** You no longer need to manually download `refresh.sh` from the repo.
> The script is bundled inside the Docker image and published to the host automatically
> on each container start (see [Self-updating refresh script](#self-updating-refresh-script) below).
> Only the initial bootstrap requires a manual step.

## First-time setup

### 1. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and fill in all values:

| Variable | Description |
|---|---|
| `OPENHANGAR_DEMO_HOSTNAME` | Public hostname, e.g. `demo.openhangar.aero` |
| `OPENHANGAR_DEMO_POSTGRES_DB` | PostgreSQL database name |
| `OPENHANGAR_DEMO_POSTGRES_USER` | PostgreSQL username |
| `OPENHANGAR_DEMO_POSTGRES_PASSWORD` | **Strong** password — not exposed publicly |
| `OPENHANGAR_DEMO_SECRET_KEY` | Flask secret key — min. 32 random characters |
| `DEMO_SLOT_COUNT` | Number of isolated visitor slots (default: `20`) |

Leave `DEMO_NEXT_WIPE_UTC` empty for now — `refresh.sh` writes it automatically.

### 2. Create the refresh mount point

The refresh script is exported from the container on each start.
Create the host directory it will be written to:

```bash
mkdir -p /opt/openhangar/refresh
```

Make sure this path matches the `REFRESH_MOUNT` volume entry in your `docker-compose.yml`.

### 3. Start the stack

```bash
docker compose up -d
```

The first startup seeds the database automatically. Check logs with:

```bash
docker compose logs -f openhangar-demo-web
```

### 4. Set up the refresh cron job

After the first `docker compose up -d`, the refresh script will appear in `/opt/openhangar/refresh/`.
Point cron at that path so it always uses the version shipped with the running image:

```bash
crontab -e
```

Add the following line (runs every 3 hours, offset by 7 minutes to avoid the `:00` spike):

```cron
7 */3 * * * /opt/openhangar/refresh/refresh.sh >> /var/log/openhangar-demo.log 2>&1
```

To verify cron is working, check the log after the first scheduled run:

```bash
tail -f /var/log/openhangar-demo.log
```

### 5. Set up log rotation

Create a logrotate config to cap the log at 1 MB and keep 16 compressed old copies (2 days):

```bash
sudo tee /etc/logrotate.d/openhangar-demo > /dev/null << 'EOF'
/var/log/openhangar-demo.log {
    size 1M
    rotate 16
    compress
    missingok
    notifempty
    copytruncate
}
EOF
```

`copytruncate` truncates the live file in place rather than moving it, so the cron append (`>>`) always writes to the right file. Logrotate runs daily by default via `/etc/cron.daily/logrotate`; no further setup is needed.

---

## What the refresh script does

Each time `refresh.sh` runs:

1. **Writes the next wipe timestamp** to `.env` (`DEMO_NEXT_WIPE_UTC = now + 3 h`) — the app reads this to show the countdown banner.
2. **Pulls the latest image** from `ghcr.io/e2jk/openhangar:latest`.
3. **Rebuilds the container** if a newer image was found; otherwise just restarts the app container.
4. **Prunes dangling Docker images** (`docker image prune -f`) to reclaim disk space.
5. **Waits for the container to be healthy**, then runs `flask reset-db` and `flask seed-demo` inside it to wipe and reseed all visitor slots.

The script is idempotent — safe to run manually at any time:

```bash
/opt/openhangar/refresh/refresh.sh
```

---

## Self-updating refresh script

`refresh.sh` and `webhook.py` are bundled inside the Docker image (under `/app/demo/`).
On each container start, the entrypoint copies them to the `/refresh` bind-mount, which maps to `/opt/openhangar/refresh/` on the host.

This means:
- You never need to manually download a new version of the script.
- After a new image is pulled and the container restarted, the updated scripts are available to the cron job on its next run (at most 3 hours later).
- One update is always "one wipe behind" the image — acceptable for a demo.

---

## Instant trigger via webhook (optional)

Instead of waiting up to 3 hours for cron, GitHub Actions can notify your server immediately after publishing a new image.

### How it works

The OpenHangar app exposes `POST /demo/webhook` in demo mode. GitHub Actions POSTs to it with a shared secret after a successful image push. The app validates the secret and launches `refresh.sh` in the background. No separate port, no separate process — Traefik handles TLS as usual.

### Setup

**In your `.env`:**

```bash
# Generate with: openssl rand -hex 32
DEMO_WEBHOOK_SECRET=<long-random-string>
```

**In GitHub (repository Settings → Secrets and variables → Actions):**

| Secret | Value |
|---|---|
| `DEMO_SITE_URL` | Your demo URL, e.g. `https://openhangar-demo.devolenvol.eu/` (trailing slash) |
| `DEMO_WEBHOOK_SECRET` | Same value as in your `.env` |

The publish workflow will POST to `${DEMO_SITE_URL}demo/webhook` automatically after each successful image push. The step is `continue-on-error: true`, so a missing or unreachable server never blocks a release.

### Security notes

- Requests without the correct `Authorization: Bearer <secret>` header are rejected with `403`.
- The secret is compared using constant-time HMAC to prevent timing attacks.
- Traffic goes through Traefik over HTTPS — no extra firewall port needed.
- The webhook only triggers `refresh.sh`, which is idempotent — even if somehow called repeatedly, the result is just a reseed, not data loss.

---

## Updating to a new release

New releases are published automatically to GHCR on every merge to `main`. The next scheduled cron run (within 3 hours) will pick up the new image automatically. If the webhook is configured, the update happens within seconds of the image push. To update immediately without waiting:

```bash
/opt/openhangar/refresh/refresh.sh
```

---

## Stopping the demo

```bash
docker compose down
```

To also remove the database volume:

```bash
docker compose down -v
```

---

## Troubleshooting

**Slots not being assigned** — check that the seed ran successfully:
```bash
docker exec openhangar-demo-web flask seed-demo
```

**Certificate not issued** — verify your DNS is pointing to the VPS and that the `letsencrypt` cert resolver name in `docker-compose.yml` matches the one configured in your Traefik instance.

**Container unhealthy** — check app logs:
```bash
docker logs openhangar-demo-web --tail 50
```
