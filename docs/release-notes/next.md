# Airfield passports: collect stamps for the airfields you fly to

**New: airfield passports**

An airfield passport is a list of airfields you'd like to fly to: every airfield in your country, a few favourite regions, or the airfields taking part in a landing-voucher booklet such as the German Landegutscheinheft. Landing at one of them earns a stamp. Stamps come from the flights in your own pilot logbook, so the passport fills itself in as you log flights. Open **Airfield passports** in the menu to start one.

- **Build a passport**: pick countries or regions, search for airfields by name or code, paste a list of codes, or add an airfield that isn't in the built-in list. A map and a review step show what you're about to save. Codes that aren't recognised are never guessed: you correct them or drop them.
- **Import a published list**: fetch the current Landegutscheinheft list, or the airfield codes from another public web page, and check them before adding them. Only the codes are kept, with the address the list came from and when it was fetched.
- **Track your progress**: a map and a list show which airfields you have already visited and how far along you are. Airfields that have closed don't count against you unless you landed there. An optional view sizes each airfield by how often you've landed there.
- **Record visits yourself**: add a visit that isn't in your logbook (for example an intermediate stop), set aside a logged arrival that wasn't a full-stop landing, and add a photo for each stamp. Visits and photos are private to you.
- **Where next?**: list the airfields you haven't visited yet that are near your home airfield, any airfield you search for, or wherever you last landed. Filter them by distance or flying time; an optional estimate of how far you can fly uses the usable fuel, fuel flow and reserve you enter. This helps you choose a destination; it's not fuel planning, and it can't tell you whether a runway suits your aircraft.
- **Home airfield and cruise speed**: aircraft and pilot profiles can now record a home airfield, and aircraft an optional cruise speed. "Where next?" uses them as its starting point and its flying-time estimate.

- **Keep editions together**: link successive booklets or challenges into a series. Open the current edition, revisit an archived one, or show all editions on a single map—including airfields that disappeared from later lists. Colours show the newest or oldest edition in which you visited; the optional frequency view still counts each visit just once. To start another edition, copy its targets into the builder and review the new name, dates and list. The original stays intact.

On the demo, the owner account comes with three example passports, already stamped from its logbook: every Belgian airfield, a dated edition of a fictional voucher booklet, and one that's complete.

Passports are personal for now. Group passports shared with friends (with or without their own OpenHangar account), voucher balances, public sharing and exports will follow.

**Privacy**
- The pages you visited are no longer left in the browser tab after you sign out. For an instant Back button, the app keeps a copy of the last pages visited in the tab; that copy is now dropped as soon as a page loads signed out or under another account. Pages showing other people's email addresses (user management, permissions, the fleet reservations list, setup, the AMP declaration) are never kept in it.

**Maintenance**
- Opening your passport collection now reads your logbook once, keeping the page responsive as you collect more passports. Each passport still applies its own dates and visit exclusions.
- Airfield passports are planned in stages in the implementation plan, as the next phase; the later phases were renumbered. A stretch goal for a passport-only instance, possibly run by a voucher publisher, was added to the roadmap.
- Airfields in a passport keep a stable identity, so renamed or reused airfield codes and manual corrections don't move stamps to another field. Fixed a lookup of those codes that failed on PostgreSQL.
- Imports from the web only reach public addresses and refuse anything that could reach the server's own network. Downloads are limited in time and size, and at most two run at once.
- HTTPS list imports explicitly require TLS 1.2 or newer.
- Browser tests use Playwright 1.65.2, which no longer depends on pyee, so pyee is gone from the test requirements (its updates kept clashing with Playwright's version limits). The CI cache of the test browser now follows the Playwright version, instead of downloading the browser again on every run.
- Corrected the agent guide's examples for passing data from templates to JavaScript.
