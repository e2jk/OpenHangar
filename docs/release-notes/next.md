# Security hardening: sign-in, links, uploads, backups and upgrades

**Security and privacy**

- Ten wrong passwords no longer lock you out of your own account from a
  browser you signed in with before, and a password reset lifts the lock.
- A new instance can require a setup token (`OPENHANGAR_SETUP_TOKEN`) so
  nobody else can claim it first.
- Password reset links are stored only as a fingerprint, a new link voids
  older ones, and each reset link or invitation works once. An invitation
  sent to an address creates that address only.
- Share links stop working once the aircraft is archived or the hangar
  deactivated; documents on a full share link now open from the link.
- Uploaded files that could run script (HTML, SVG) download instead of
  opening, and photos uploaded as documents or receipts lose their GPS
  position.
- Only owners or maintenance can delete a grounding snag, and every snag
  deletion is logged.
- E-mail links use `OPENHANGAR_INSTANCE_URL` when set; another site can no
  longer sign you out or switch your language or theme; demo slots can only
  be opened from the demo page.
- Backups are readable by the app's user only, and the database password
  no longer appears in the server's process list.
- One-click upgrades refuse an image whose signature cannot be checked
  (`OPENHANGAR_ALLOW_UNVERIFIED_UPGRADE=1` to allow it), and the container
  backs up before applying migrations.
- The Docker image keeps its code read-only for the app; the Raspberry Pi,
  demo and minimal setups get the reference hardening; Traefik, PostgreSQL
  and rclone are pinned by digest; the Docker socket proxy allows only what
  Traefik needs.
- Authenticator QR codes are drawn on the server (one script less on every
  page).
- An instance admin without 2FA is signed out when their hangar requires
  it; a security alert no longer hides a second one of the same type for a
  minute; the AMP PDF fetches no file or link, the status badge is always an
  image, and data-file names and translations reach the page as text.

**Fixes**

- A one-off maintenance item (no interval) is no longer overdue once
  serviced, and due-date projections use the weeks actually flown.
- Sharing a file from the phone's share sheet works again; signed rental
  agreements can be opened; the invitation e-mail is translated; Personal
  accounts can open public aircraft and invitation links.
- `OPENHANGAR_SESSION_LIFETIME_DAYS` is checked at start (1 to 3650), and
  snooze links sent while answering a request are full links.
- A corrupt AMP workbook, a restore with folder entries, one bad GPX value
  or a report range near year 1 no longer end in an error page; track
  images update when their track changes.

**Maintenance**

- `/.well-known/security.txt` points to the project's security advisories.
- New backlog entries for the audit items that need a feature of their own
  (verifying link-only invitations, share-link expiry, a hangar time zone,
  expenses of deleted flights, costs in several currencies).
- A test no longer imports its own module (a CodeQL note).
- The app explains why it is waiting for PostgreSQL and stops on a wrong
  password, and refuses to serve production without a database URL.
- The PR-text job no longer runs code from the pushed branch with the
  owner's token; Renovate no longer merges major updates on its own.
- A release retries its documentation-site deploy once when GitHub has not
  listed the uploaded site yet, so the update check sees new versions
  without a manual re-run.
