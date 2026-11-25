# Security fixes: sign-in, e-mail, logs and resource limits

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
- An instance started with `OPENHANGAR_ENV=demo` by mistake no longer
  erases its database: demo mode, which wipes and reseeds the database on
  every start, now refuses a database that holds real accounts. The
  configuration guide now says what each `OPENHANGAR_ENV` value does.
- Spreadsheets that declare huge or numerous merged cell ranges, or more
  rows than an import takes, are now refused or read without exhausting the
  server's memory (maintenance programme, pilot logbook and airframe log
  imports).
