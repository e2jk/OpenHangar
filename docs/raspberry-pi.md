# OpenHangar on a Raspberry Pi

This guide takes you from a blank SD card to a working OpenHangar
installation that anyone on your home or club network can reach with a
browser. It makes all the technical decisions for you so you can focus on
flying.

Once you're up and running, the [self-hosting guide](self-hosting.md)
covers every available option: HTTPS, email notifications, aviation map
overlay, backups, upgrades, multi-tenant use, and more.

---

## What you will need

### Hardware

| Item | Recommendation |
|---|---|
| Raspberry Pi | **Pi 4 (4 GB RAM)** — Pi 3B+ works but is slower; Pi 5 is great too |
| Power supply | Official Raspberry Pi USB-C adapter — cheap adapters cause random crashes |
| SD card | **32 GB, Class 10 / A1 or better** (SanDisk Endurance, Samsung PRO Endurance, …) |
| Network cable | Any standard Ethernet cable — more reliable than Wi-Fi for a server |
| Computer | Windows, Mac, or Linux — used only during the initial setup |

### Software (on your computer)

**Raspberry Pi Imager** — the official tool for writing the OS to the SD
card. Download it free from
[raspberrypi.com/software](https://www.raspberrypi.com/software/).

---

## Step 1 — Write Raspberry Pi OS to the SD card

1. Insert the SD card into your computer.
2. Open **Raspberry Pi Imager**.
3. **Choose Device** → your Pi model.
4. **Choose OS** → *Raspberry Pi OS (other)* →
   **Raspberry Pi OS Lite (64-bit)**.

   > *Lite* means no graphical desktop — perfect for a server. It uses far
   > fewer resources and is more stable for always-on use.

5. **Choose Storage** → your SD card.
6. Click **Next** → **Edit Settings** and fill in:

   **General tab**
   - **Hostname**: `openhangar`
   - **Username / password**: choose a username (e.g. `pi`) and a strong
     password — **write it down**.
   - **Wi-Fi** *(optional)*: fill in only if you can't use a network cable.
   - **Locale**: your time zone and keyboard layout.

   **Services tab**
   - **Enable SSH** → *Use password authentication*.

   Save → Yes to apply.

7. **Yes** to write. Takes 2–5 minutes.
8. Eject the SD card safely, insert it into the Pi, connect the Ethernet
   cable and the power supply. The Pi boots automatically — no screen needed.

---

## Step 2 — Connect to your Pi

Wait about 60–90 seconds for the first boot to complete.

### Connect with SSH

SSH lets you type commands on the Pi from your own computer.

**Windows 10/11 — Terminal or Command Prompt:**

```
ssh pi@openhangar.local
```

**Mac / Linux — Terminal:**

```
ssh pi@openhangar.local
```

This works on most home networks. If you kept the default Raspberry Pi
hostname instead of setting it to `openhangar`, try `raspberrypi.local`
instead. If neither name works, see the fallback below.

```
ssh pi@raspberrypi.local
```

<details>
<summary><strong>Not working? Here's how to find the Pi on your network</strong></summary>

Log into your home router's admin page — usually at `http://192.168.1.1`
or `http://192.168.0.1` (check the label on the back of your router).
Look for a section called *Connected devices*, *DHCP clients*, or similar.
You should see a device named `openhangar` (or `raspberrypi` if you kept
the default) with a number like `192.168.1.105` next to it. Use that
number in your SSH command and anywhere this guide mentions
`openhangar.local`.

</details>

The first time you connect, type `yes` when asked about the host
fingerprint. Enter your password when prompted (nothing appears as you type
— that's normal).

When you see a prompt ending in `$`, you are on the Pi. All remaining
commands in this guide are typed in that SSH window.

---

## Step 3 — Update the system

```bash
sudo apt update && sudo apt upgrade -y
```

---

## Step 4 — Install Docker

Docker is the platform that runs OpenHangar.

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

Add your user to the `docker` group so you don't need `sudo` before every
Docker command:

```bash
sudo usermod -aG docker $USER
```

Log out and back in for the change to take effect:

```bash
exit
```

[Reconnect with SSH](#connect-with-ssh), then verify the installation:

```bash
docker run --rm hello-world
```

You should see *"Hello from Docker!"* in the output.

---

## Step 5 — Download and configure OpenHangar

### Create a working directory

```bash
mkdir ~/openhangar
cd ~/openhangar
```

### Create the data folders

OpenHangar runs inside its container as an unprivileged user with ID `1000`,
not as root. It needs to own the folders where it stores uploads and backups.
If you skip this step, Docker creates them owned by `root` on first start,
and the `web` container keeps restarting with a *Permission denied* error.

```bash
mkdir -p uploads backups
sudo chown 1000:1000 uploads backups
```

(The database folder, `db_data`, is handled automatically by the database
container. You don't need to create it.)

### Download the Compose file

```bash
curl -fsSL https://raw.githubusercontent.com/e2jk/OpenHangar/main/docker/docker-compose.raspberry-pi.yml \
     -o docker-compose.yml
```

### Fill in the secrets

Run this single command — it generates three random secrets, writes them
directly into the file, and then prints them so you can save them somewhere
safe:

```bash
DB_PASS=$(openssl rand -hex 20) && \
SECRET_KEY=$(openssl rand -hex 32) && \
BACKUP_KEY=$(openssl rand -hex 32) && \
sed -i \
  -e "s/CHANGE_THIS_db_password/$DB_PASS/g" \
  -e "s/CHANGE_THIS_secret_key_min_32_chars/$SECRET_KEY/" \
  -e "s/CHANGE_THIS_backup_enc_key/$BACKUP_KEY/" \
  docker-compose.yml && \
echo "" && \
echo "Secrets written to docker-compose.yml — save these somewhere safe:" && \
echo "" && \
echo "  DB password:     $DB_PASS" && \
echo "  Secret key:      $SECRET_KEY" && \
echo "  Backup enc. key: $BACKUP_KEY" && \
echo ""
```

Copy the three values that appear on screen into a password manager or
notes app. You'll need the **backup encryption key** if you ever need to
restore from a backup — keep it somewhere separate from the Pi itself.

> **Why these matter:** the *secret key* protects user sessions; the
> *backup encryption key* encrypts backup files. Neither can be recovered
> if lost.

---

## Step 6 — Start OpenHangar

```bash
docker compose pull        # download the images (takes a few minutes)
docker compose up -d       # start in the background
docker compose ps          # verify both db and web show "Up"
```

If `web` shows *Restarting*, wait 30 seconds and run `docker compose ps`
again — it waits for the database to be ready before starting.

---

## Step 7 — Open OpenHangar

From any device on the same network, open a browser and go to:

```
http://openhangar.local:8087
```

If you kept the default Raspberry Pi hostname, try:

```
http://raspberrypi.local:8087
```

or, if neither name works, replace `192.168.1.105` with
[the IP address you found earlier](#not-working-heres-how-to-find-the-pi-on-your-network):

```
http://192.168.1.105:8087
```

Click **Get Started** to create your admin account. The first account you
create becomes the administrator.

---

## Everyday commands

```bash
cd ~/openhangar

# Stop / start
docker compose stop
docker compose start

# Update to a new release
docker compose pull && docker compose up -d

# Take a manual backup
docker compose exec web flask backup-now
# → saved to ~/openhangar/backups/
```

> **Prefer automatic backups?** No cron job needed — set `OPENHANGAR_BACKUP_TIME`
> in your compose file's `environment:` block and the container backs itself up
> daily with automatic retention pruning. See
> [built-in daily scheduling](backup_restore.md#built-in-daily-scheduling-recommended).

```bash
# View live logs
docker compose logs -f web
```

---

## Plain HTTP: what it means, and when to switch to HTTPS

This guide sets OpenHangar up over plain `http://`. That keeps things
simple, with no certificates to manage, and it's fine for a home network
only you and your family use. Your browser will show a *Not secure* label
in the address bar, which is expected here.

There are two trade-offs to know about:

- **No app install, no offline mode.** Browsers only allow installing
  OpenHangar as an app on your phone's home screen, and editing your
  logbook offline, when the site is served over HTTPS. Over plain HTTP,
  OpenHangar works normally in the browser, but only while you're
  connected.
- **Passwords travel unencrypted on your network.** At home that's a small
  risk. If the Pi sits on a **network other people share**, such as club,
  hangar or airfield Wi-Fi, anyone on that network could read the
  passwords. In that case, set up HTTPS.

> **Don't use a self-signed certificate.** Every device shows a full-page
> security warning, and browsers still won't turn on app install or
> offline mode, so you'd get the warnings without the benefits.

### Switching to HTTPS

The [self-hosting guide](self-hosting.md) covers the full setup with
Traefik as a reverse proxy, which gets a free certificate automatically
from Let's Encrypt and also removes the `:8087` from the URL. Be aware
that this setup needs:

- a **domain name** you control (for example `hangar.example.com`), pointed
  at your home's public IP address, and
- **port 443 forwarded** from your router to the Pi.

That makes OpenHangar reachable from the internet. It's useful if you want
to log flights from the airfield, but it also means your instance is
exposed, so keep TOTP two-factor authentication on for every account.

Once OpenHangar is served over HTTPS, remove the
`OPENHANGAR_SESSION_COOKIE_SECURE=false` line from your compose file (or
set it to `true`) and run `docker compose up -d`.

---

## Troubleshooting

**"Connection refused" in the browser**
- Run `docker compose ps` — both containers should say *Up*.
- Run `docker compose logs` to look for errors.
- Confirm you're on the same network as the Pi.

**`web` container keeps restarting**
- Check `docker compose logs web` for details.
- If you see a database connection error, the secrets command above may not
  have run completely — try running it again, then `docker compose up -d`.
- If you see *Permission denied* on `/data/uploads` or `/data/backups`, the
  data folders are owned by the wrong user. Fix them with
  `sudo chown -R 1000:1000 ~/openhangar/uploads ~/openhangar/backups`, then
  `docker compose up -d`.

**The setup wizard or login page just reloads (or shows "400 Bad Request")**
- Your `docker-compose.yml` is probably missing the line
  `- OPENHANGAR_SESSION_COOKIE_SECURE=false` in the `web` service's
  `environment:` block. Older copies of the Raspberry Pi compose file didn't
  have it. Without it, the browser won't send the login cookie back over
  plain `http://`. Add the line, run `docker compose up -d`, and reload the
  page. See
  [`OPENHANGAR_SESSION_COOKIE_SECURE`](configuration.md#openhangar_session_cookie_secure).

**Start completely fresh** *(deletes all data)*

```bash
docker compose down
rm -rf ~/openhangar/db_data ~/openhangar/uploads
docker compose up -d
```

> Your backup archives in `~/openhangar/backups/` are left untouched.

---

## Next steps

Now that OpenHangar is running, the [self-hosting guide](self-hosting.md)
explains all the optional features:
- **Aviation map overlay** (OpenAIP API key)
- **Email notifications** (SMTP)
- **Automated backups and restore**
- **HTTPS** for access from outside your network
- **Multiple organisations** sharing one installation

---

## Security note

This setup is accessible to devices on your local network only, which is
fine for home or small-club use. Do **not** forward port 8087 on your
router to the Pi without first adding HTTPS and additional hardening.
See the [self-hosting guide](self-hosting.md) for details.
