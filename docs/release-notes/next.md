# Maintenance commands no longer start background jobs

- One-off `flask` commands (`backup-now`, `restore-backup`, `shell`, …) no longer start the web server's background jobs (backup schedule, update check, notifications, sync watcher). A restore no longer runs next to them; it previously did even inside the web container. The web server itself, including `flask run` in development, is unchanged.
