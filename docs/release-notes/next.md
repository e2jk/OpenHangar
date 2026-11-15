# Security fix: expenses and costs visible to owners only

- **Security:** an aircraft's expense list and cost dashboard are now limited to owners and admins, as the aircraft page already showed them. Before, any user assigned to the aircraft (pilot, maintenance, viewer) could open them by their address, and the aircraft page had their browser fetch both in the background for offline use. Found by the new browser-based crawl of the signed-in security scan.
- A decimal or whole number too large for where it is stored (for example 1,000 hours of night time in one logbook entry, or an extra digit in an engine counter) is now refused with a message instead of a server error; the pilot logbook and flight forms keep what was typed, other forms go back with nothing saved.

**Maintenance**
- Security scan: a new, manually run workflow tries ZAP's browser-based crawler (a headless Firefox that clicks through pages built by JavaScript and HTMX) on the signed-in app, and lists the pages only it finds, to decide whether it should join the weekly signed-in scan.
