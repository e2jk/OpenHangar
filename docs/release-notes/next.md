# Re-runnable documentation site deploy

**Maintenance**

- Re-running a release's failed publishing job no longer fails at the
  documentation site deploy: each attempt uploads the site under its own
  name, where the re-run used to find the failed attempt's upload too and
  stop ("Multiple artifacts named github-pages", v3.64.1).
