# Check that upgrades build the database the app expects

**Maintenance**
- New test: running every database migration from scratch must produce exactly the tables, columns, indexes and links that the app's models describe, including what happens to linked records on deletion. The rest of the test suite builds its database straight from the models, so a migration that missed something would otherwise only show after an upgrade.
