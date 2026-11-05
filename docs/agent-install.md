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
- **If you can't** (you're a chat assistant), the user is your hands, and
  every round trip costs them a copy and a paste. Each step below has **one
  code block** meant to be pasted as a whole into a terminal. It prints a
  labelled summary of everything you need. For each step: say in a sentence
  or two what the block does, give it to the user **unchanged** (apart from
  filling in placeholders like `<domain>`), ask them to paste back the
  **whole** output, and only then continue. Don't split a block into single
  commands, and don't merge several steps into one paste: there's a
  question or a decision between them.
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

Run this block first. It only reads information, apart from the last two
lines, which **start downloading the images in the background**. Both images
are needed whatever the user answers, so by the time the questions are done
the download usually is too. It returns immediately. Tell the user the
download has started.

```bash
echo "arch:       $(uname -m)"
echo "os:         $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
echo "docker:     $(docker --version 2>&1)"
echo "compose:    $(docker compose version 2>&1)"
echo "user:       $(id -un) (uid $(id -u)), groups: $(id -nG)"
echo "disk free:  $(df -h ~ | awk 'NR==2 {print $4}')"
echo "hostname:   $(hostname)"
echo "lan ip:     $(hostname -I 2>/dev/null | awk '{print $1}' | grep . || ip -4 route get 1.1.1.1 2>/dev/null | grep -o 'src [0-9.]*' | cut -d' ' -f2)"
echo "existing:   $(ls -d ~/openhangar 2>/dev/null || echo none)"
echo "listening:  $(ss -ltnH 2>/dev/null | awk '{print $4}' | grep -oE ':(80|443|8087)$' | sort -u | tr '\n' ' ')"
echo "containers:"; docker ps --format '  {{.Names}}  {{.Ports}}' 2>&1
( (docker pull -q ghcr.io/e2jk/openhangar:latest && docker pull -q postgres:18-alpine) > /tmp/openhangar-pull.log 2>&1 & )
echo "image download: started in the background"
```

Interpret the results:

- **Architecture**: images exist for `amd64` and `arm64` only. On `armv7l` /
  `armhf` (32-bit Raspberry Pi OS), stop: the user needs to reinstall with
  **Raspberry Pi OS Lite (64-bit)**, see the
  [Raspberry Pi guide](raspberry-pi.md).
- **`docker:` or `compose:` shows "command not found"** (Compose must be v2
  or later: `docker compose`, not the old `docker-compose`): install Docker
  with its official convenience script
  (`curl -fsSL https://get.docker.com | sudo sh`, which is the one exception
  to rule 4: ask first, it needs `sudo`). Then add the user to the `docker`
  group (`sudo usermod -aG docker "$USER"`, then log out and back in), and
  run the preflight block again.
- **`containers:` shows "permission denied"**: Docker is installed, but the
  user isn't in the `docker` group yet. Same `usermod` fix as above. The
  background image download will have failed for the same reason, which is
  harmless: Step 4 downloads whatever is missing.
- **`uid` is `1000`**: the user has the same ID as the app inside the
  container. The `chown` in Step 3 then works without `sudo`, so there's no
  password prompt. (The Step 3 blocks try without `sudo` first anyway.)
- **`existing:` isn't `none`**: go to [Existing install](#existing-install).
- **Containers named `openhangar-web` or `openhangar-db` already exist**:
  there's already an OpenHangar stack on this host, maybe a development
  copy. Go to [Existing install](#existing-install). The internet setup's
  compose file uses those exact container names and would fail to start
  next to it.
- **`listening:` shows `:80` or `:443`** (another Traefik, Nginx,
  Caddy…): note it. The internet setup below brings its own Traefik, which
  would conflict. Tell the user, and offer the home-network setup, or adding
  OpenHangar behind their existing proxy by hand (see the
  [self-hosting guide](self-hosting.md)). **`:8087`** in use means the
  home-network setup's port is taken: change the left side of `"8087:5000"`
  in its compose file to a free port.
- Keep `hostname` and `lan ip` for later: they give the address to open in
  the browser for the home-network setup.

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

This block downloads the compose file and creates the data folders. It
hands the folders to uid 1000, the user the app runs as inside the
container, trying without `sudo` first. Then it generates the secrets and
writes them into the compose file without printing them. If the user picked
another install directory, replace `~/openhangar` in the first line.

```bash
mkdir -p ~/openhangar && cd ~/openhangar && \
curl -fsSL https://raw.githubusercontent.com/e2jk/OpenHangar/main/docker/docker-compose.raspberry-pi.yml -o docker-compose.yml && \
mkdir -p uploads backups && \
{ chown 1000:1000 uploads backups 2>/dev/null || sudo chown 1000:1000 uploads backups; } && \
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
echo "placeholders left: $(grep -c CHANGE_THIS docker-compose.yml)" ; \
echo "cookie setting:    $(grep -o 'OPENHANGAR_SESSION_COOKIE_SECURE=[a-z]*' docker-compose.yml)" ; \
ls -ld "$PWD" uploads backups docker-compose.yml BACKUP_KEY.txt
```

Expect `placeholders left: 0`, `cookie setting: OPENHANGAR_SESSION_COOKIE_SECURE=false`,
`uploads` and `backups` owned by uid 1000 (shown as the user's name if
their uid is 1000), and the two files readable only by their owner
(`-rw-------`). The cookie setting is what plain HTTP needs. Without it,
login loops back with a 400 error.

Service name for later commands: **`web`**. App URL:
`http://<host>:8087`, where `<host>` is the `hostname` from the preflight
(try `http://<hostname>.local:8087` first) or its `lan ip`.

Continue to [Step 4](#step-4--start-and-verify) (or Step 5 first in guided
mode).

## Step 3b — Internet setup (HTTPS)

**First, a DNS check** (replace `<domain>`):

```bash
echo "domain resolves to: $(getent hosts <domain> | awk '{print $1}' | head -1 | grep . || echo '(does not resolve)')"
echo "this network's IP:  $(curl -fsS https://api.ipify.org)"
docker network ls --format '{{.Name}}' | grep -xE 'frontend|backend|socket-proxy-network' | sed 's/^/network already exists: /'
```

- If the two IPs differ, stop and help the user fix their DNS record (an
  `A` record pointing at the public IP). Let's Encrypt fails otherwise.
- If any `network already exists:` line shows up, stop: another stack on
  this host already uses one of the fixed network names this compose file
  needs.
- Also confirm with the user that ports **80 and 443** are forwarded from
  their router to this machine. You can't test that from inside the
  network.

**Then the install block** (replace `<domain>` and `<email>` with the
user's answers, and `~/openhangar` if they picked another directory). It
downloads the files and creates the data folders, generates the secrets
into `.env` without printing them, enables `OPENHANGAR_INSTANCE_URL` so
email links point to the right address, and pulls Traefik:

```bash
mkdir -p ~/openhangar/traefik && cd ~/openhangar && \
BASE=https://raw.githubusercontent.com/e2jk/OpenHangar/main/docker && \
curl -fsSL "$BASE/docker-compose.yml"  -o docker-compose.yml && \
curl -fsSL "$BASE/.env.example"        -o .env && \
curl -fsSL "$BASE/traefik/dynamic.yml" -o traefik/dynamic.yml && \
mkdir -p openhangar/data/uploads openhangar/data/backups && \
{ chown 1000:1000 openhangar/data/uploads openhangar/data/backups 2>/dev/null || sudo chown 1000:1000 openhangar/data/uploads openhangar/data/backups; } && \
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
sed -i 's|^      # - OPENHANGAR_INSTANCE_URL=|      - OPENHANGAR_INSTANCE_URL=|' docker-compose.yml && \
printf 'Backup encryption key: %s\n' "$BACKUP_KEY" > BACKUP_KEY.txt && \
docker pull -q traefik:v3.7 >/dev/null ; \
echo "placeholders left: $(grep -v '^#' .env | grep -c 'change-me\|example\.com')" ; \
grep -E '^(OPENHANGAR_HOSTNAME|TRAEFIK_ACME_EMAIL|SOCKET_PROXY_DOCKER_GID)=' .env ; \
echo "instance url: $(grep -c '^      - OPENHANGAR_INSTANCE_URL=' docker-compose.yml) line enabled" ; \
docker compose config -q && echo "compose config: OK" ; \
ls -ld openhangar/data/uploads openhangar/data/backups .env BACKUP_KEY.txt
```

Expect `placeholders left: 0`, the user's domain and email, a numeric
docker group ID, `instance url: 1 line enabled`, `compose config: OK`, and
the data folders owned by uid 1000.

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

One block per setup. Each one downloads whatever is still missing (almost
instant if the Step 1 download finished), starts the stack, and waits up to
two minutes for the web service to report `(healthy)`. The image's built-in
health check confirms the app is up **and** can reach the database. Then it
runs every check and prints the results. (There's no `curl` inside the
image, so don't `exec` one; these checks run from the host.)

**3a, home network:**

```bash
cd ~/openhangar && docker compose pull -q && docker compose up -d && \
echo "waiting for OpenHangar to start (up to 2 minutes)..." && \
for i in $(seq 1 40); do docker compose ps web | grep -q '(healthy)' && break; sleep 3; done ; \
docker compose ps --format '{{.Service}}: {{.Status}}' ; \
echo "health: $(curl -fsS http://localhost:8087/health 2>&1)" ; \
echo "cookie: $(curl -sL -o /dev/null -D - http://localhost:8087/login | grep -i '^set-cookie' | sed 's/session=[^;]*/session=(hidden)/')" ; \
echo "--- problems in the logs (empty is good):" ; \
docker compose logs --no-color --tail 100 2>&1 | grep -iE 'error|traceback|permission denied' | grep -v Fontconfig | tail -8
```

**3b, internet** (replace `<domain>`; the first HTTPS request can take
~30 seconds while Traefik obtains the certificate):

```bash
cd ~/openhangar && docker compose pull -q && docker compose up -d && \
echo "waiting for OpenHangar to start (up to 2 minutes)..." && \
for i in $(seq 1 40); do docker compose ps openhangar-web | grep -q '(healthy)' && break; sleep 3; done ; \
docker compose ps --format '{{.Service}}: {{.Status}}' ; sleep 20 ; \
echo "health: $(curl -fsS https://<domain>/health 2>&1)" ; \
echo "cookie: $(curl -sL -o /dev/null -D - https://<domain>/login | grep -i '^set-cookie' | sed 's/session=[^;]*/session=(hidden)/')" ; \
echo "--- certificate errors (empty is good):" ; \
docker compose logs --no-color traefik 2>&1 | grep -i acme | tail -3 ; \
echo "--- problems in the logs (empty is good):" ; \
docker compose logs --no-color --tail 100 openhangar-web openhangar-db 2>&1 | grep -iE 'error|traceback|permission denied' | grep -v Fontconfig | tail -8
```

Read the output:

- Every service should be `Up`, with `(healthy)` on web and database. If
  the **database** isn't healthy, ask for
  `docker compose logs --tail 30 db` (3a) or
  `docker compose logs --tail 30 openhangar-db` (3b).
- `health:` should be `{"status":"ok"}`.
- **Cookie** (the check that catches "login just reloads"): 3a must **not**
  contain `Secure`; if it does, `OPENHANGAR_SESSION_COOKIE_SECURE=false` is
  missing. 3b **must** contain `Secure`.
- 3b: a certificate error in `health:` along with ACME lines in the logs
  almost always means DNS or port forwarding. Re-check those with the user,
  then rerun the block.

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
