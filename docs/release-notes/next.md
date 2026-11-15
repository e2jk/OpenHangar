# Numbers too large for the database are refused with a message

- A decimal or whole number too large for where it is stored (for example 1,000 hours of night time in one logbook entry, or an extra digit in an engine counter) is now refused with a message instead of a server error; the pilot logbook and flight forms keep what was typed, other forms go back with nothing saved.

**Maintenance**
- Security scan: a new, manually run workflow tries ZAP's browser-based crawler (a headless Firefox that clicks through pages built by JavaScript and HTMX) on the signed-in app, and lists the pages only it finds, to decide whether it should join the weekly signed-in scan.
