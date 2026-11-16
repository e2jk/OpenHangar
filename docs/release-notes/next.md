# Check that upgrades build the database the app expects

**Maintenance**
- New test: running every database migration from scratch must produce exactly the tables, columns, indexes and links that the app's models describe, including what happens to linked records on deletion. The rest of the test suite builds its database straight from the models, so a migration that missed something would otherwise only show after an upgrade.
- Security scans: an alert the scan rules mark as out of scope for some pages (the home page case of "Bypassing 403", signed in) no longer still shows up on GitHub's Security tab for those pages; it was already left out of the pass/fail result.
