# OpenHangar — Demo Deployment Guide

This folder contains everything needed to run the public demo instance of OpenHangar on a VPS.

## Prerequisites

- Docker and Docker Compose installed on the VPS
- Traefik already running with a `traefik-network` external network and a Let's Encrypt cert resolver
- A DNS A record pointing your demo hostname (e.g. `demo.openhangar.aero`) to the VPS IP
- The repo cloned or these files copied to a directory on the VPS (e.g. `/opt/openhangar/demo/`)

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
| `TRAEFIK_CERT_RESOLVER` | Name of the Let's Encrypt resolver in your Traefik config (commonly `letsencrypt`) |

Leave `DEMO_NEXT_WIPE_UTC` empty for now — `refresh.sh` writes it automatically.

### 2. Make the refresh script executable

```bash
chmod +x refresh.sh
```

### 3. Start the stack

```bash
docker compose up -d
```

The first startup seeds the database automatically. Check logs with:

```bash
docker compose logs -f openhangar-demo-web
```

### 4. Set up the refresh cron job

Open the crontab for the user that has Docker access:

```bash
crontab -e
```

Add the following line (runs every 3 hours, offset by 7 minutes to avoid the `:00` spike):

```cron
7 */3 * * * /opt/openhangar/demo/refresh.sh >> /var/log/openhangar-demo.log 2>&1
```

Adjust the path if you placed the files elsewhere. To verify cron is working, check the log after the first scheduled run:

```bash
tail -f /var/log/openhangar-demo.log
```

---

## What the refresh script does

Each time `refresh.sh` runs:

1. **Writes the next wipe timestamp** to `.env` (`DEMO_NEXT_WIPE_UTC = now + 3 h`) — the app reads this to show the countdown banner.
2. **Pulls the latest image** from `ghcr.io/e2jk/openhangar:latest`.
3. **Rebuilds the container** if a newer image was found; otherwise just restarts the app container.
4. **Waits for the container to be healthy**, then runs `flask seed-demo` inside it to wipe and reseed all visitor slots.

The script is idempotent — safe to run manually at any time:

```bash
./refresh.sh
```

---

## Updating to a new release

New releases are published automatically to GHCR on every merge to `main`. The next scheduled cron run will pick up the new image. To update immediately:

```bash
./refresh.sh
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

**Certificate not issued** — verify your DNS is pointing to the VPS and that `TRAEFIK_CERT_RESOLVER` matches the resolver name in your Traefik config.

**Container unhealthy** — check app logs:
```bash
docker logs openhangar-demo-web --tail 50
```
