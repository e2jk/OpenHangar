# Read-only offline logbooks; 2FA changes need re-authentication

**Upgrade notes — please read before upgrading**
- **Offline editing is removed.** Anyone who still has offline changes waiting on a device should open **Offline changes** on that device and sync them *before* you upgrade: the upgrade deletes changes that were never synced.

**Offline**
- Offline editing is gone: flights and logbook entries can no longer be logged or corrected without a connection, and the **Offline changes** page and its navbar badge are gone. A form submitted offline now says so, and nothing is queued.
- Reading offline stays. **Offline logbook** (on an aircraft's logbook and on your Pilot logbook) shows a read-only copy of the whole logbook saved on your device, refreshed each time you open that logbook online, with the time it was saved. Your offline pilot logbook now lists all your flights, including those in fleet aircraft.
- Recently opened pages, installing the app and **Share to OpenHangar** work as before.

**Account security**
- No more bare "CSRF token" error page. A sign-in form submitted twice (for example the two-factor code sent automatically and again with Enter) now takes you to your dashboard, since the first submission already signed you in; any other stale sign-in form is simply shown again. Other forms opened before your session changed show a page explaining what to do, with links back that also work in the installed app.
- Setting up two-factor authentication on your profile now asks for your password first. Replacing your authenticator app or disabling two-factor authentication asks for your password and a code from your current app, so an open session alone can no longer change your second factor. Lost your authenticator? Your administrator's password reset link still removes it.
