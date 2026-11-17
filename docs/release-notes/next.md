# Build personal airfield passports

**Privacy**
- The pages you visited are no longer left in the browser tab after you sign out. For an instant Back button, the app keeps a copy of the last pages visited in the tab; that copy is now dropped as soon as a page loads signed out or under another account. Pages showing other people's email addresses (user management, permissions, the fleet reservations list, setup, the AMP declaration) are never kept in it.

**Airfield passports**
- Calculate passport stamps and visit counts directly from the viewer’s own logbook and manual evidence. Builder completion previews use the same rules, including dated aliases, full-stop exclusions and closed-airfield totals.
- Build and edit personal passports by area, airfield name/code, pasted lists or custom fields, with a map and review before saving. Owners can archive, reopen or delete passports; removing saved visits/photos requires confirmation. Military/ULM classifications remain explicitly unknown until entered.
- Add the first data foundation for airfield passports: protected list/detail pages, stable airfield identities and historical code aliases. Catalogue selections include fields without ICAO codes; manual corrections survive catalogue refreshes. Visit/stamp maps and group invitations follow in later increments.

**Maintenance**
- Add a passport-only instance stretch goal to the roadmap, including a possible publisher-operated voucher service with personal maps and opt-in customer community sharing. Planning only.
- Plan airfield passports as the next development phase, with staged delivery for personal and group passports, voucher editions, destination discovery and exports; renumber the following roadmap phases. Shared maps distinguish fields nobody has visited, those visited by others, and the viewer's own visits. Related passports use a simple series relationship and explicit dates, supporting annual or other editions without a recurrence engine; combined maps offer newest/oldest edition styling. An optional frequency view weights markers by repeat visits while default completion still counts each airfield once. Group-specific invitation links enrol friends through self-service passport-only accounts, with optional later pilot activation. Group series retain annual participation and offer an optional read-only map of earlier editions a member did not join. Airfield details will identify friends who visited and support group-only experience/recommendation comments; flight debriefs remain a separate backlog idea to expand later. This is planning only.
- Browser tests use Playwright 1.65.2, which no longer depends on pyee, so pyee is gone from the test requirements (its updates kept clashing with Playwright's version limits). The CI cache of the test browser now follows the Playwright version, instead of downloading the browser again on every run.
- Add the passport schema migration, tenant/membership isolation checks, and photo cleanup that preserves files on transaction rollback.

- Prepare owner-only passport management and expiring, user-scoped builder drafts, with bounded selections and private completion-impact previews.

- Fix airfield alias lookup on PostgreSQL without requiring equality comparisons on JSON data.
- Correct the agent guide’s JSON data-bridge examples: HTMX strips JSON script tags as well as executable scripts; escaped data attributes survive navigation.
