# OpenHangar — install instructions for AI assistants

> **Human reader?** This page is written for an AI assistant (ChatGPT,
> Claude, Codex, Gemini, …) to follow on your behalf. Give your assistant
> this link and ask it to install OpenHangar:
>
> ```text
> Install OpenHangar for me by following
> https://raw.githubusercontent.com/e2jk/OpenHangar/main/docs/agent-install.md
> ```
>
> Prefer doing it by hand? Use the [Raspberry Pi guide](raspberry-pi.md)
> (home network, works on any Linux box) or the
> [self-hosting guide](self-hosting.md) (internet-facing, HTTPS).

---

## Instructions for the assistant

You are installing **OpenHangar**, a self-hosted aircraft and logbook
management web app, as a Docker Compose stack. Follow the steps below in
order. Every command here is meant to be run on the machine that will host
OpenHangar.

### How to work

- **If you can run shell commands** (you're a coding agent with a terminal),
  run them yourself, show the user what you ran, and read the output.
- **If you can't** (you're a chat assistant), give the user **one command at
  a time**, say what it does in one sentence, ask them to paste back the
  output, and only then continue. Never hand over a long script to run
  blind.
- Keep the user informed in plain language. Most users are pilots, not
  sysadmins.

### Ground rules (do not skip)

1. **Ask before** running `sudo`, opening or forwarding ports, or changing
   anything outside the install directory.
2. **Never overwrite an existing install.** If the install directory already
   has a `docker-compose.yml`, or `docker ps` already shows OpenHangar
   containers, stop and go to [Existing install](#existing-install).
3. **Secrets never go through the chat.** Generate them on the host with the
   commands below, which write them straight into files. Don't print them,
   don't ask the user to paste them to you, and don't repeat them back.
4. Don't download and run remote scripts (`curl … | bash`). Everything here
   is an explicit command.
5. Use only the files in this repository's `docker/` folder. Don't invent
   your own compose file: the provided ones carry security hardening that is
   easy to lose.

---

## Step 1 — Preflight (no questions yet)

Run these checks first. They are read-only, apart from the image download.

```bash
uname -m                      # must be x86_64 or aarch64
docker --version
docker compose version        # Compose v2 or later ("docker compose", not the old "docker-compose")
id -u; id -nG                 # is the user root, or in the "docker" group?
df -h ~                       # want at least 5 GB free
ls ~/openhangar 2>/dev/null   # existing install directory?
docker ps --format '{{.Names}}\t{{.Ports}}'   # existing OpenHangar or a proxy on 80/443?
```

Interpret the results:

- **Architecture**: images exist for `amd64` and `arm64` only. On `armv7l` /
  `armhf` (32-bit Raspberry Pi OS), stop: the user needs to reinstall with
  **Raspberry Pi OS Lite (64-bit)**, see the
  [Raspberry Pi guide](raspberry-pi.md).
- **No Docker or no Compose v2**: install it with Docker's official
  convenience script (`curl -fsSL https://get.docker.com | sudo sh`, which
  is the one exception to rule 4: ask first, it needs `sudo`), then add the
  user to the `docker` group (`sudo usermod -aG docker "$USER"`, then log
  out and back in).
- **`id -u` prints `1000`**: the user already has the same ID as the app
  inside the container, so the `chown` commands below work without `sudo`.
  Drop the `sudo` and save the user a password prompt.
- **Containers named `openhangar-web` or `openhangar-db` already exist**:
  there's already an OpenHangar stack on this host, maybe a development
  copy. Go to [Existing install](#existing-install). The internet setup's
  compose file uses those exact container names and would fail to start
  next to it.
- **Something already listens on ports 80/443** (another Traefik, Nginx,
  Caddy…): note it. The internet setup below brings its own Traefik, which
  would conflict. Tell the user, and offer the home-network setup, or adding
  OpenHangar behind their existing proxy by hand (see the
  [self-hosting guide](self-hosting.md)).

**Start the image download now**, in the background, while you ask the
questions. Both images are needed whatever the user answers. Tell the user
you've started it:

```bash
(docker pull ghcr.io/e2jk/openhangar:latest && docker pull postgres:18-alpine) > /tmp/openhangar-pull.log 2>&1 &
```

If your tool has its own "run in background" option, use that instead of
the trailing `&`. Run this line on its own: chaining more commands after
the `&` on the same line is easy to get wrong.

(Chat assistants: skip the background part. Ask the user to run
`docker pull ghcr.io/e2jk/openhangar:latest` in a second terminal window
while you talk.)

---

## Step 2 — Ask the questions (one batch)

Ask these together, in plain language, with the default marked:

1. **How will people reach OpenHangar?**
   - **(a) Home network only** *(default)*: open it as
     `http://<this-machine>:8087` from devices on the same network. No
     domain, no certificates, nothing exposed to the internet. Fine for
     personal use at home.
   - **(b) From the internet, with HTTPS**: a real address like
     `https://hangar.example.com`, reachable from anywhere (e.g. the
     airfield). Requires a domain name the user controls, and ports 80 and
     443 forwarded to this machine.
2. **(b only)**: the domain name, and an email address for Let's Encrypt
   certificate notices.
3. **Install directory**: default `~/openhangar`.
4. **Mode**:
   - **"Just get it working"** *(default)*: sensible defaults, nothing else
     asked.
   - **"Walk me through the settings"**: also go through
     [Step 5](#step-5--guided-mode-only-optional-settings) before starting.

Do **not** ask which kind of operation (solo pilot, club, school, …) or
create user accounts. The app's own first-run wizard does that in the
browser.

Help the user choose for question 1:

- **Plain HTTP (a)** means the browser shows *Not secure*, the app can't be
  installed on a phone's home screen, offline logbook editing is
  unavailable, and passwords cross the local network unencrypted. That's
  acceptable on a private home network. If the machine sits on **shared
  Wi-Fi** (club, hangar, airfield), recommend (b).
- **Internet (b)** gets all features, but the instance is publicly
  reachable. Recommend turning on two-factor authentication (TOTP) for every
  account.
- Never suggest a self-signed certificate: every device shows a full-page
  warning, and browsers still disable app install and offline mode.

---

## Step 3a — Home network setup

```bash
mkdir -p ~/openhangar && cd ~/openhangar
curl -fsSL https://raw.githubusercontent.com/e2jk/OpenHangar/main/docker/docker-compose.raspberry-pi.yml -o docker-compose.yml
mkdir -p uploads backups
sudo chown 1000:1000 uploads backups   # the app runs as uid 1000 inside the container
```

Generate the secrets and write them into the compose file, without printing
them:

```bash
umask 077 && \
DB_PASS=$(openssl rand -hex 20) && \
SECRET_KEY=$(openssl rand -hex 32) && \
BACKUP_KEY=$(openssl rand -hex 32) && \
sed -i \
  -e "s/CHANGE_THIS_db_password/$DB_PASS/g" \
  -e "s/CHANGE_THIS_secret_key_min_32_chars/$SECRET_KEY/" \
  -e "s/CHANGE_THIS_backup_enc_key/$BACKUP_KEY/" \
  docker-compose.yml && \
chmod 600 docker-compose.yml && \
printf 'Backup encryption key: %s\n' "$BACKUP_KEY" > BACKUP_KEY.txt && \
echo "Secrets written."
```

The compose file already sets `OPENHANGAR_SESSION_COOKIE_SECURE=false`,
which plain HTTP needs. Without it, login loops back with a 400 error. Leave
that line in.

Service name for later commands: **`web`**. App URL:
`http://<host>:8087`, where `<host>` is the machine's hostname
(`hostname`, try `http://<hostname>.local:8087`) or LAN IP. To find the
LAN IP, use `hostname -I` on Debian and Raspberry Pi OS. It doesn't exist
on every distro; this works everywhere:
`ip -4 route get 1.1.1.1 | grep -o 'src [0-9.]*' | cut -d' ' -f2`.

Continue to [Step 4](#step-4--start-and-verify) (or Step 5 first in guided
mode).

## Step 3b — Internet setup (HTTPS)

Before anything else, check that the domain resolves to this network's
public IP:

```bash
getent hosts <domain>      # the domain's IP
curl -fsS https://api.ipify.org; echo   # this network's public IP
```

If they differ, stop and help the user fix their DNS record (an `A` record
pointing at the public IP). Let's Encrypt will fail otherwise. Also confirm
with the user that ports **80 and 443** are forwarded from their router to
this machine. You can't test that from inside the network.

```bash
mkdir -p ~/openhangar/traefik && cd ~/openhangar
BASE=https://raw.githubusercontent.com/e2jk/OpenHangar/main/docker
curl -fsSL "$BASE/docker-compose.yml"      -o docker-compose.yml
curl -fsSL "$BASE/.env.example"            -o .env
curl -fsSL "$BASE/traefik/dynamic.yml"     -o traefik/dynamic.yml
mkdir -p openhangar/data/uploads openhangar/data/backups
sudo chown -R 1000:1000 openhangar/data/uploads openhangar/data/backups
docker network ls --format '{{.Name}}' | grep -xE 'frontend|backend|socket-proxy-network'
```

If that last command prints anything, a network with the same name already
exists on this host (probably from another stack). Stop and tell the user:
the compose file uses fixed network names and would join the other stack's
network.

Pull Traefik too: `docker pull traefik:v3.7`.

Fill in `.env` (replace `<domain>` and `<email>` with the user's answers):

```bash
umask 077 && \
DB_PASS=$(openssl rand -hex 20) && \
SECRET_KEY=$(openssl rand -hex 32) && \
BACKUP_KEY=$(openssl rand -hex 32) && \
DOCKER_GID=$(getent group docker | cut -d: -f3) && \
sed -i \
  -e "s/^OPENHANGAR_POSTGRES_PASSWORD=.*/OPENHANGAR_POSTGRES_PASSWORD=$DB_PASS/" \
  -e "s/^OPENHANGAR_SECRET_KEY=.*/OPENHANGAR_SECRET_KEY=$SECRET_KEY/" \
  -e "s/^OPENHANGAR_BACKUP_ENCRYPTION_KEY=.*/OPENHANGAR_BACKUP_ENCRYPTION_KEY=$BACKUP_KEY/" \
  -e "s/^SOCKET_PROXY_DOCKER_GID=.*/SOCKET_PROXY_DOCKER_GID=$DOCKER_GID/" \
  -e "s/^OPENHANGAR_HOSTNAME=.*/OPENHANGAR_HOSTNAME=<domain>/" \
  -e "s/^TRAEFIK_ACME_EMAIL=.*/TRAEFIK_ACME_EMAIL=<email>/" \
  .env && \
chmod 600 .env && \
printf 'Backup encryption key: %s\n' "$BACKUP_KEY" > BACKUP_KEY.txt && \
echo "Secrets written."
```

Also uncomment the `OPENHANGAR_INSTANCE_URL` line in the `environment:` list
of the `openhangar-web` service in `docker-compose.yml`, so email links
point to the right address.

Do **not** set `OPENHANGAR_SESSION_COOKIE_SECURE` here: the default
(`true`) is correct over HTTPS.

Service name for later commands: **`openhangar-web`**. App URL:
`https://<domain>`.

> **Important (both setups, but mostly 3b):** in `docker/docker-compose.yml`
> the `openhangar-web` service only receives the environment variables that
> are listed in its `environment:` block. Setting an optional variable in
> `.env` is **not enough**: also uncomment (or add) its line in that block,
> e.g. `- OPENHANGAR_SMTP_HOST=${OPENHANGAR_SMTP_HOST}`. In the home-network
> compose file, add the variable directly to the `web` service's
> `environment:` list.

---

## Step 4 — Start and verify

From the install directory:

```bash
docker compose pull      # near-instant if the Step 1 download finished; otherwise completes it
docker compose up -d
docker compose ps        # repeat until the web service shows "(healthy)", usually within ~1 minute
```

The web image has a built-in health check that confirms the app is up
**and** can reach the database, so `(healthy)` on the web service is the
readiness signal. (There's no `curl` inside the image, so don't try to
`exec` one.) Then, replacing `<service>` with `web` (3a) or
`openhangar-web` (3b):

```bash
curl -fsS http://localhost:8087/health   # 3a only, from the host; expect {"status":"ok"}
docker compose logs --tail 50 <service>
# look for "Running in production mode with gunicorn" and no tracebacks
```

If the **database** service never gets healthy, read
`docker compose logs db` (3a) or `docker compose logs openhangar-db` (3b).

**Cookie/scheme check.** This catches the most common "login just reloads"
problem:

```bash
# -L: on a fresh install /login redirects to /setup, which sets the cookie
curl -sL -o /dev/null -D - http://localhost:8087/login | grep -i '^set-cookie'   # 3a
curl -sL -o /dev/null -D - https://<domain>/login      | grep -i '^set-cookie'   # 3b
```

- 3a must **not** contain `Secure`. If it does,
  `OPENHANGAR_SESSION_COOKIE_SECURE=false` is missing.
- 3b **must** contain `Secure`.

For 3b, the first HTTPS request can take ~30 seconds while Traefik obtains
the certificate. If `curl` reports a certificate error after a minute, check
`docker compose logs traefik` for ACME errors (usually DNS or port
forwarding).

Common failures:

| Symptom | Cause | Fix |
|---|---|---|
| Web container restarting, `Permission denied` on `/data/uploads` or `/data/backups` | Data folders owned by root | `sudo chown -R 1000:1000` the uploads/backups folders, `docker compose up -d` |
| Login/setup page reloads, or `400 Bad Request` | `Secure` cookie over plain HTTP | Add `OPENHANGAR_SESSION_COOKIE_SECURE=false` (3a only), `docker compose up -d` |
| `OPENHANGAR_SECRET_KEY appears to be a placeholder` | Secret step didn't run | Re-run the secrets command |
| `exec format error` | 32-bit OS | Reinstall a 64-bit OS |
| Database unhealthy, log says *"in 18+, these Docker images are configured to store database data in a format which is compatible with pg_ctlcluster"* | Compose file from before 2026-11 mounts `/var/lib/postgresql/data` | Change the database volume to `./db_data:/var/lib/postgresql` (3a), then `docker compose up -d` |
| `Conflict. The container name "/openhangar-web" is already in use` | Another OpenHangar stack on this host | See [Existing install](#existing-install) |

---

## Step 5 — Guided mode only: optional settings

Skip this in "just get it working" mode. In guided mode, go through the
settings below **before** Step 4 (or re-run `docker compose up -d` after
changing them). For each one, explain it in a sentence, ask the user, and
link the reference section if they want details. Leave everything not
listed here at its default.

| Setting | Ask the user | Reference |
|---|---|---|
| Daily automatic backups | "Back up automatically every night? At what time?" Recommend yes, e.g. `03:00`. | [`OPENHANGAR_BACKUP_TIME`](configuration.md#openhangar_backup_time), [`OPENHANGAR_BACKUP_RETENTION`](configuration.md#openhangar_backup_retention) |
| Email notifications | "Should OpenHangar send emails (maintenance due, reservations…)? Which SMTP server?" Can also be done later. | [`OPENHANGAR_SMTP_HOST`](configuration.md#openhangar_smtp_host) and the rest of the [Email](configuration.md#email) section |
| Public address | 3b only, already set in Step 3b. | [`OPENHANGAR_INSTANCE_URL`](configuration.md#openhangar_instance_url) |
| Security alerts to the admin's phone | "Get a push notification on suspicious logins? (free, via ntfy)" | [`OPENHANGAR_ALERT_NTFY_TOPIC_URL`](configuration.md#openhangar_alert_ntfy_topic_url), [`OPENHANGAR_ALERT_EMAIL_TO`](configuration.md#openhangar_alert_email_to) |
| Stay logged in longer | "How many days before you have to log in again?" Default 30. | [`OPENHANGAR_SESSION_LIFETIME_DAYS`](configuration.md#openhangar_session_lifetime_days) |
| Aviation map overlay | "Show airspace and airfields on maps? Needs a free OpenAIP API key." | [`OPENHANGAR_OPENAIP_API_KEY`](configuration.md#openhangar_openaip_api_key) |
| One-click upgrades | 3b, technical users only: needs a host cron job. | [`OPENHANGAR_UPGRADE_DIR`](configuration.md#openhangar_upgrade_dir) |

Remember the note at the end of Step 3b: each variable must also be listed
in the web service's `environment:` block. SMTP passwords and API keys are
secrets: have the user type them into `.env` (or the compose file)
themselves, in an editor (`nano .env`), rather than pasting them to you.

---

## Step 6 — Hand over

Tell the user, briefly:

1. **The URL** to open, and that the first account they create in the
   browser becomes the administrator.
2. **Save the backup encryption key**: it's in `BACKUP_KEY.txt` in the
   install directory. Copy it into a password manager, ideally somewhere
   off this machine, then delete the file. Without it, backups can't be
   restored.
3. **Everyday commands**, run from the install directory:
   - Update: `docker compose pull && docker compose up -d`
   - Manual backup: `docker compose exec <service> flask backup-now`
   - Logs: `docker compose logs -f <service>`
4. **Where to go next**: the [user guide](user-guide.md), and (3a) the
   [plain-HTTP section of the Raspberry Pi guide](raspberry-pi.md#plain-http-what-it-means-and-when-to-switch-to-https)
   if they later want HTTPS.

---

## Existing install

If OpenHangar is already installed, don't reinstall. Offer instead:

- **Update**: `docker compose pull && docker compose up -d` in its
  directory.
- **Troubleshoot**: run the checks from [Step 4](#step-4--start-and-verify)
  against it.
- **Start over**: only if the user explicitly confirms they want to **delete
  all their data**. Suggest a backup first
  (`docker compose exec <service> flask backup-now`) and copying the
  `backups` folder somewhere safe.
