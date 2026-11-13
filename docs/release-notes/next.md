# Read-only offline logbooks; 2FA changes need re-authentication

**Upgrade notes — please read before upgrading**
- **Offline editing is removed.** Anyone who still has offline changes waiting on a device should open **Offline changes** on that device and sync them *before* you upgrade: the upgrade deletes changes that were never synced.

**Offline**
- Offline editing is gone: flights and logbook entries can no longer be logged or corrected without a connection, and the **Offline changes** page and its navbar badge are gone. A form submitted offline now says so, and nothing is queued.
- Reading offline stays. **Offline logbook** (on an aircraft's logbook and on your Pilot logbook) shows a read-only copy of the whole logbook saved on your device, refreshed each time you open that logbook online, with the time it was saved. Your offline pilot logbook now lists all your flights, including those in fleet aircraft.
- Recently opened pages, installing the app and **Share to OpenHangar** work as before.

**Account security**
- Repeated failed sign-ins from one address are now refused at once with "try again in N seconds" while the address must wait, instead of the server holding each failed attempt open for up to a minute, so a burst of failures no longer ties the server up.
- Failed sign-in counts, account locks and used two-factor codes are now stored in the database: they are shared by every server process and survive a restart, and a two-factor code can be used only once.
- No more bare "CSRF token" error page. A sign-in form submitted twice (for example the two-factor code sent automatically and again with Enter) now takes you to your dashboard, since the first submission already signed you in; any other stale sign-in form is simply shown again. Other forms opened before your session changed show a page explaining what to do, with links back that also work in the installed app.
- Setting up two-factor authentication on your profile now asks for your password first. Replacing your authenticator app or disabling two-factor authentication asks for your password and a code from your current app, so an open session alone can no longer change your second factor. Lost your authenticator? Your administrator's password reset link still removes it.

**Billing**
- Finalizing a rental charge, reversing a ledger entry or opening the co-owner billing page from two places at once can no longer post the same charge, reversal or co-owner entry twice: the database now lets exactly one win, and the other request is told the charge or entry was already handled.
