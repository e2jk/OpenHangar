# Visited pages no longer stay in the browser tab after sign-out

**Privacy**
- The pages you visited are no longer left in the browser tab after you sign out. For an instant Back button, the app keeps a copy of the last pages visited in the tab; that copy is now dropped as soon as a page loads signed out or under another account. Pages showing other people's email addresses (user management, permissions, the fleet reservations list, setup, the AMP declaration) are never kept in it.

**Maintenance**
- Browser tests use Playwright 1.65.2, which no longer depends on pyee, so pyee is gone from the test requirements (its updates kept clashing with Playwright's version limits). The CI cache of the test browser now follows the Playwright version, instead of downloading the browser again on every run.
