# One release at a time, and re-runnable release publishing

**Maintenance**

- The daily release no longer runs alongside a release being shipped: it
  waits its turn, and doesn't publish when the commit it was started for is
  already part of that release. On 2026-11-24 a daily release GitHub had
  delayed by hours published older code (v3.63.0) a few minutes after the
  release with the password-change fix (v3.62.0), and took its place as
  the latest version.
- A release whose publishing failed after the GitHub release was created
  (for example the documentation site deploy, during a GitHub outage) can
  now be re-run: the existing release is kept and the failed steps run
  again. That failure left v3.64.0 out of the list every instance checks
  for updates.
