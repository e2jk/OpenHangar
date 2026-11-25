# Security fixes: file folders and access checks

- Renaming a misnamed document folder on the "Files to review" page now only
  accepts that hangar's own `<registration>/<folder>` paths. Before, a
  crafted request from an owner or admin could move folders that belonged to
  another hangar or to other users.
- Owners can no longer demote, revoke or change the flags of an admin, and
  only the instance administrator can change the instance administrator's
  access. Revoking someone's access to a hangar now removes that membership
  only: an account that still belongs to another hangar (for example
  someone who joined a passport group) keeps working there.
- The pilot GPS import only links a track to one of the flights it offered
  in the "select the matching flight" list.
- A flight on an aircraft you have no access to can no longer be opened or
  edited (nor its track images, GPS parts or photos), unless you flew it.
  Re-saving your own flight after losing access to its aircraft now keeps
  it on that aircraft instead of moving it off the aircraft's log.
- Backups, the email server settings, the OpenAIP map key, the storage
  sizes and the version check refresh are now reserved to the instance
  administrator: they cover every hangar of the instance, so a hangar's own
  admins and owners no longer see or change them.
- Share links with the registration in them, showcase links and reminder
  snooze links no longer reach the access log with their secret token. The
  reference Docker Compose file now leaves Traefik's own access log off, as
  it can't hide these tokens: OpenHangar's access log already covers it.
- Outgoing e-mail now checks the mail server's certificate, so nobody on
  the network can pose as it and read the SMTP password or the mail. A mail
  relay with a self-signed or private certificate needs that certificate
  trusted (see `OPENHANGAR_SMTP_USE_TLS` in the configuration guide).
  Security alert e-mails now also work on port 465.
- OpenHangar no longer starts with the example backup encryption key from
  `docker/.env.example` or the Raspberry Pi compose file: that key is
  public. If your instance stops at startup with this message, generate a
  real key (`openssl rand -hex 32`) and keep the old one to restore backups
  made with it.
- "Where next?" links now name the chosen passports `stamp_book=` in the
  address instead of `passport=`, which security scanners mistook for a
  password in the URL. Existing bookmarks keep working.
- Bookings and maintenance downtimes must now start and end within ten
  years of today. A booking saved with a far-off end date (such as
  9999-12-31) no longer slows down or breaks the aircraft calendar and the
  home page calendar.
- After five wrong authenticator codes, an account's two-factor codes are
  refused for 30 minutes, at sign-in and when changing or removing the
  authenticator. Entering the password again no longer resets the count,
  and the lock raises a security alert.
