# Clearer errors for over-long text; Download all matches the list

- Documents: "Download all" now contains the same documents as the list for each role. Pilots and maintenance users get the sensitive documents they can already open one by one (insurance and airworthiness certificates); viewers still get none.
- Text longer than the database can hold (for example a 300-character expense description) no longer ends in a server error: nothing is saved, and the form says some text was too long. The same guard already covers numbers that are far too large.

**Maintenance**
- Signed-in security scan: the "Bypassing 403" check no longer reports the home page answering a request that carries an `X-Original-URL` header — a signed-in user's home page always answers, and nothing in OpenHangar reads that header (a test checks it). Every other way that check tries to get past a 403 is still reported.
- Signed-in security scan: the "Cookie Slack Detector" check is now ignored, so the weekly scan is no longer red for it. Signed in, it cannot give a meaningful answer here (every page renews the session cookie it tries to remove); a test already checks that no signed-in page is served without that cookie.
- Signed-in security scan: the admin's browser crawl gets 20 minutes instead of 10, and each run starts both users' browser crawls from a page picked at random (an aircraft page, the logbook, settings…) instead of always the home page, so successive weekly runs explore different parts of the app.
