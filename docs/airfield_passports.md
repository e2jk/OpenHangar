# Airfield passports

[Phase 41](implementation_plan.md#phase-41--airfield-passports) provides
personal collections, live stamps, visit maps, manual visits, private stamp
photos, linked voucher editions and invitation-based group participation.
The live implementation plan records the remaining increments.

## Read access

The Airfield passports navigation link appears for pilots and existing passport
owners/accepted participants, including users without pilot capability. The list
and detail pages reveal only passports in the user's current tenant which they
own or have actively accepted. An administrator role alone does not disclose
someone else's passport. Pending/inactive membership grants no access.

Both pages opt out of HTMX history and service-worker caching. Passport-only
registration is bound to a valid group invitation and email verification.

## Build and manage a passport

Choose **Airfield passports → Create passport**, enter a name and optional dates,
then search by countries, regions, airfield types, code or name. Tick results and
apply them, or add all matches (up to 5,000 targets). The first 500 matches are
listed; narrow the search to inspect more. Paste codes separated by spaces,
commas, semicolons or new lines. Unknown/ambiguous codes must be corrected or
explicitly dropped before saving. Missing fields can be entered manually, with
optional coordinates; coordinate-less fields remain in the list.

Military and ULM filters preserve an explicit unknown state. Passport-only owners
store classifications on their own targets. Full operators retain tenant-wide
curated classifications on existing non-public targets, with author/time
provenance; public projections keep their own local classifications. No manual
classification is copied into the source dataset. The builder's blue markers are selected
fields and grey markers are unselected search results. The lists and forms also
work without JavaScript.

**Review passport** shows the selected list/map and how date/target changes affect
your own completion. It never reads other members' private flights. Removing a
target with saved visit/photo evidence needs explicit acknowledgement before those
passport records are removed. Your flight logbook remains independent. Save is
atomic and refuses an outdated passport, changed review or stale draft revision.

Owners can edit a passport, archive it to keep it read-only, reopen it, or delete
it through a confirmation page. Membership/admin status does not grant ownership.
View-only users cannot mutate passports. A newly created personal passport includes
its owner as an accepted participant.

Draft selections are stored in `passport_drafts`, bound to the user/tenant and
optional edited passport; cookies do not hold selection lists. Drafts expire after
two hours, with at most ten per user/tenant, 5,000 targets and a 2 MB JSON payload.
Starting a builder prunes expired/old drafts. Final save consumes the draft so a
replayed submission cannot create a duplicate passport. Cancelling leaves only
an expiring draft, not a partially created passport or selected airfield records.

## Airfield identity

`passports/catalogue.py` reads the bundled OurAirports CSV without network access.
`find_airfields` and `catalogue_choices` do not populate the database. `ident` and
`icao_code` are separate; entries without ICAO codes or coordinates are retained.
Missing or invalid coordinate pairs are unavailable for mapping, not omitted
from the builder catalogue. Missing catalogue files raise an error rather than
silently presenting an empty country.

`materialise` creates or refreshes only a referenced identity, keyed by tenant,
source and source ID. Identity rows are tenant-scoped; their lifetime is independent
of any passport or user. Dataset values and explicit JSON corrections are stored
separately; `effective_airfield` overlays corrections for display. Unknown military
and ULM classification remain unknown. Source refresh does not delete missing rows
or infer closure from their absence.

Aliases retain old identifiers. `resolve_code` accepts an optional visit date,
and ambiguous matches raise `AirfieldConflict`; it never chooses a nearby field
or merges two source IDs. `add_alias` allows explicit dated code reconciliation.
`create_custom`, `correct_airfield` and `reconcile_custom` are service operations
for the later builder/review UI, not unauthenticated endpoints. Reconciliation
preserves the custom identity and its manually entered values. Callers own the
transaction and must check tenant/actor permissions before calling mutation helpers.

## Schema and lifetimes

A passport retains its own dates, targets and participants. Optional series
membership is a simple relationship, not a recurrence rule. Deleting a series
detaches its passports; deleting a passport never deletes flights or shared
airfield identities. Composite foreign keys prevent targets from crossing tenants
and prevent visit/photo records from crossing passports.

The schema supports independent manual visit events, per-participant arrival
exclusions and a photo per participant/target. Automatic credit and the earliest
eligible visit calculation are delivered in 41c. Deleting a flight nulls a manual
visit's supporting flight link and removes arrival exclusions; it does not delete
the independently asserted visit or stamp photo. Deleting users removes their
participations/authored evidence/uploads and passports they still own.

Photo filenames are opaque safe basenames under `UPLOAD_FOLDER/passport_photos`.
ORM deletion of a photo, participant, target, passport, owner or tenant queues file
cleanup until the outer transaction commits. Rollbacks/savepoint rollback retain
files. Failed file deletion is logged after commit and never pretends that the
committed database deletion rolled back. Use ORM deletions for passport records
with files: bulk/Core SQL bypasses those hooks. Photo upload/serving is added in
41c using the existing upload-validation conventions.

The Alembic migration is additive. Existing instances must apply it before running
this code (restart the development web container to run its migration entrypoint).
Validation covers PostgreSQL migration/model parity and upgrade/downgrade/re-upgrade
on a disposable schema with existing users and tenants, as well as the normal full
coverage gate.


## Visits, stamps and progress

Each viewer sees their own recorded visits. Like the existing pilot logbook, flight
evidence is personal across organisations: only the viewer’s own crew slots count,
including flights on another organisation’s or an unmanaged aircraft. Owning/administering
an aircraft is insufficient. Passports, aliases and manual visits remain tenant-bound.
Automatic evidence comes from their
PIC or accepted second-crew logbook slots, including unmanaged aircraft. Owning
or administering an aircraft gives no visit credit. FSTD sessions, future flights,
and explicit zero-airborne/no-landing ground runs are excluded. Historical entries
with unknown duration can still count; cancelled/no-takeoff reservations are not
logbook flights. A logged arrival is presumed a full stop: on the airfield's page,
use **Exclude this arrival** for a touch-and-go, low approach or other nonqualifying
arrival. **Restore arrival credit** reverses that choice. Departure codes and
landing totals never infer intermediate destinations.

Date boundaries are inclusive and use the logged date. The first eligible evidence
wins: date, known arrival time (timed arrivals before date-only visits that day),
evidence kind, then stable ID. Date-only visits keep their unknown time. Flights
are evaluated live; edits, deletion/import undo, crew changes, aliases and passport
date changes immediately update stamps and counts. Ambiguous aliases across all
tenant identities never earn an automatic stamp, even if only one is selected.

Open an airfield from the map or list to read your evidence, add a dated manual
visit and optional note, or delete your manual visit. An optional supporting flight
must be from your own logbook on that date; this also records intermediate stops
on a multi-stop flight. Linking a manual visit to the same flight/airfield as an
arrival avoids counting it twice. Independent manual visits count separately.
Manual evidence survives flight changes; a flight link is shown only while you
still occupy one of its logbook slots. The forms and progress work without JS.
GPS stop suggestions remain a later enhancement: proximity alone awards no credit.

The normal map is a checklist with equal marker size for all destinations.
**Show visit frequency** adds bounded logarithmic radii (6 px for zero, 8 px for
one, maximum 18 px), with exact counts in the list and popups. Counts represent
recorded full-stop events, not landing totals or stamps. Two arrivals on one day
count twice; repeat-visit data is only as complete as the recorded evidence.
This increment shows only your counts. Participant/group scope, edition colours
and published aggregates are integrated with the corresponding later group,
series and public-sharing subphases; no other pilot's flight details are exposed.

Completion is stamped targets / (open targets + stamped closed targets). Closed
unvisited fields remain visible but excluded, and original/excluded target counts
are displayed separately. No achievable targets means no percentage. Reopened
fields rejoin the denominator. Fields without coordinates remain in the list.

## Private stamp photos

Each participant may keep one photo per target, independently of which evidence
earns the stamp. Upload JPEG, PNG or WebP up to 10 MB and 20 megapixels. Actual image
content is decoded and re-encoded as a bounded JPEG with EXIF/GPS metadata removed;
the upload name is replaced with a random filename. A photo itself earns no credit.
Photo endpoints require your active passport participation and return no-store
responses. Photos are visible only to their participant, even to other accepted
members or the owner. Replacements/deletions remove old files after commit;
failed database saves remove the new file and retain the old one. Publication
is a separate, explicit later workflow; uploads never enable public consent.


## Where next?

Open a passport and choose **Where next?** to find open, unvisited targets. Only
your recorded visits determine this view. Choose an accessible aircraft to use its
explicit home airfield and cruise speed. Its home takes precedence over your pilot
profile's home; homes are never inferred from flights. Owners can set aircraft
homes/speeds in the aircraft editor, and pilots can set their own home in their
profile. Inputs accept a catalogue code or a saved `field:123` reference (useful
for fields without codes). References from another tenant are refused/hidden.
Clearing a saved home or speed is supported; older profiles/aircraft have neither.

Alternatively search any starting airfield by code/name (up to 50 results), or
choose your latest resolvable eligible logged arrival. This shortcut uses only
your logbook, with dated aliases and your passport's arrival exclusions; it never
uses another pilot's itinerary. A starting point without coordinates cannot give
a distance. Missing-coordinate destinations remain in the main passport list and
the discovery view displays their excluded count.

Filter by great-circle nautical miles or still-air flight minutes. The map shows
the selected radius; numeric and slider inputs are linked. Results sort nearest
first (equivalently earliest at the same constant cruise speed). Distance mode
works with no cruise speed. Time mode explicitly asks for speed. All forms and
results also work without JavaScript. Blank discovery speed/flow inputs reuse the selected aircraft’s defaults; a blank
reserve reuses a published personal minimum when available. Discovery parameters
stay in the page URL;
they do not change aircraft/profile defaults or create an unmanaged aircraft.

The optional theoretical radius uses `(usable litres / litres per hour - reserve
minutes / 60) × cruise knots`. Return without refuelling divides that radius by
two, budgeting both legs. Usable fuel must be entered explicitly; the app never
uses tank capacity as usable fuel. A published personal-minimum reserve is reused
when available (the largest published value if several sections specify one),
otherwise enter one explicitly; zero is an explicit choice, not a default. Missing,
non-finite, nonpositive speed/flow/fuel, or fuel exhausted by reserve leaves range
unknown. An unknown range does not mean unlimited range: the selected distance/time
filter remains applied and a warning explains that the optional range bound was
not applied. Estimates assume direct still-air flight at constant cruise speed,
without climb, routing or approach. They are destination discovery, not dispatch.

No licensed runway/restriction dataset is currently loaded, so suitability is
explicitly unknown. No airfield is marked suitable merely from length, weight or
its map position. Licensed suitability comparison remains the optional later item
in the implementation plan.


## Imported lists and known sources

In the builder, open **Import an airfield list**. Choose a public page/JSON/text
URL or the Landegutscheinheft publisher adapter. The latter uses the publisher's
current list; enter and verify its edition label yourself. No year is inferred
from the current date, an old product URL or the number of records. The publisher
now serves a JavaScript storefront, so the adapter uses its explicit public JSON
endpoints, not a browser or a scraped bundle. The list-status endpoint must mark
the list released before identifiers are imported. As verified on 2026-11-17 it
still marks the final list unreleased; use a verified pasted list or try later.

**Preview source import** fetches identifiers into your expiring, actor-bound
builder draft. It does not change the passport or create airfield identities.
Review candidate counts, local catalogue matches, unknown/ambiguous codes and the
map. Generic sources extract uppercase four-character alphanumeric candidate codes containing a letter from visible
HTML or JSON/text, ignoring scripts, styles and templates; unrelated uppercase
words can be candidates. Names/coordinates shown in review come from the local
catalogue, never remote descriptions. Unknown/dynamic formats, failed requests or
unreleased publisher lists produce a clear pasted-code fallback.

Choose **Add imported list to selection**, **Replace selection with imported list**,
or **Cancel import**. Replacement keeps saved-visit/photo targets even if omitted
by the new list. You can explicitly remove those separately in the builder, with
the existing final deletion acknowledgement. Other removals/additions and progress
impact appear in the final passport review. Unknown codes still require correction
or explicit dropping. The normal final **Save passport** is mandatory, including
after a reimport. The flow works without JavaScript and survives HTMX navigation.

Saved provenance is the final fetched URL, UTC retrieval timestamp and the edition
label you confirmed. Existing provenance survives ordinary edits. Remote HTML,
publisher names/descriptions, photos, hours, PPR, frequencies and runway information
are not stored. The source link is shown as provenance, not rendered or embedded.
Publisher source verification: [list status](https://www.landegut.de/api/airfields/list-state)
and [public identifiers](https://www.landegut.de/api/airfields/public).

### Fetch boundaries

- HTTP port 80 or HTTPS port 443 only; no credentials, control characters, proxy
  environment, forwarded cookies or authentication. URLs are bounded to 512 chars.
- All resolved IPv4/IPv6 addresses must be public, non-reserved and non-multicast.
  Local/link-local/shared/reserved destinations, IPv4-mapped and transition IPv6
  are refused. Connections use an already validated IP without a second hostname
  lookup; HTTPS retains the original hostname for certificate verification/SNI.
- Every redirect is revalidated; at most three redirects and no redirect loops.
  HTTPS-to-HTTP redirects are refused.
  No page-linked scripts, images, stylesheets or other resources are fetched.
- A 15-second absolute deadline covers DNS, connect, TLS, headers and streaming body
  (shared across the adapter's status/list requests). Connect/read waits are also
  bounded. A separate socket shutdown handle interrupts even a body handed off to
  the HTTP response. At most two DNS lookups can remain outstanding per worker.
- At most 1,000,000 encoded bytes and 2,000,000 decoded bytes per response. Gzip/deflate decoding
  is bounded; truncated/concatenated compressed bodies and unsupported encodings or
  content types fail closed. At most 5,000 candidate codes are staged.
- At most two fetches may run at once across the instance's workers. Nonblocking
  file locks in `UPLOAD_FOLDER/passport_fetch_locks` (left out of backups) share admission across worker
  processes and release automatically on worker exit. Excess fetches fail fast;
  they do not wait on a semaphore and occupy another request thread. Keep uploads
  storage shared when running several workers/replicas. A local semaphore also
  bounds standalone/non-Flask client use.
- Fetch actions share one five-per-minute quota per IP across create/edit endpoints
  (using the app's limiter backend, in-memory per worker by default). Normal selection
  and confirmation actions do not consume that quota. Existing two-hour draft
  expiry, ten-draft cap, revision checks and payload/selection caps still apply.


### Import lookup cost

Code lists resolve tenant aliases in one batch, regardless of list length. A
regression test verifies that resolving 5,000 codes uses two selection queries
(materialised identities plus aliases), retaining ambiguity and tenant checks.
The immutable CSV is already cached once per worker by `load_catalogue`; merged
search views are rebuilt to reflect current tenant corrections, without rereading
the CSV from disk. List-page batching across passports remains tracked in 41g
before enabling large groups; the current list still evaluates each passport.

## Linked editions (41f, personal edition views)

Use **Link editions** on a passport to create or choose a series you own. A series
is a relationship between independent passports, not a renewal schedule. Names
need not match; dates may describe a calendar year, a month, a season or any
other period, and undated passports remain supported. Linking never moves visits.

**Linked editions** opens the current non-archived edition you participate in.
Overlapping editions use the latest start date, then stable ID; when none is
current, the page says so and offers your latest edition. All editions remain
selectable, including archived editions. Undated editions have no timed priority;
their explicit order is assigned when linked (relinking places one last).

**All editions** combines targets by stable airfield identity, including destinations
removed from later lists. Each edition retains its own completion and visit dates.
The comparison shows targets added or removed since the preceding edition. A stamp
from an old edition never checks off the same destination in the current one.
Choose **Newest visits / Oldest visits** to select the marker's earned-edition
colour; exact edition names and visit counts remain in the list and popups.
**Show visit frequency** counts a flight only once per airfield across overlapping
editions. Changing colour priority does not change frequency or any saved evidence.

**Create next edition** copies a target selection into a fresh builder draft. Enter
the name and dates, review the targets (including any replacement import), then
save. The draft may be cancelled without creating an edition or series. Source
provenance is retained when available, but visits, photos and other participants
are never copied. A concurrent source change requires starting the copy again.
Existing manual visit evidence remains solely in the original passport; the new
personal edition evaluates its owner's logbook independently within its new dates.

These first series views are owner-only and contain only the viewer's evidence.
Series membership and consented group history are subsequent
41f increments; group invitations and account registration follow in 41g. A series
relationship does not grant access to any other pilot's personal passport.

The collection list and series map batch the viewer's logbook, aliases, participants,
targets, exclusions and manual visits. Evidence snapshots are request-local and
explicitly bound to one tenant and viewer; no cross-request cache of flight data
is created. Other organisations' own crew-slot flights retain the personal-logbook
policy described above; administering an aircraft never grants a stamp.

## Each person's vouchers

Open **Vouchers** on a passport to track booklet use separately from visits. For
Landegutscheinheft, each person has their own voucher at each airfield: using
yours never reduces a friend's allowance. The owner sets **Vouchers per person
and airfield** (normally one). Each accepted participant gets an independent
allowance; new friends receive theirs when they accept that edition. Membership
of a series or reading an older map alone never grants historical vouchers.

Existing quantities and recorded uses stay intact when the default changes.
Use an individual override for a different quantity at a particular airfield.
Optional validity overrides must fall within the edition dates. The remaining
quantity is the allocation minus its active redemption events, not a count of
landings. A landing earns a stamp without spending a voucher; recording voucher
use earns no stamp. Record the actual date and quantity, with an optional own
supporting flight, private note and actual **total** saving for that use. Savings
are added separately by currency, without estimating fees or exchange rates.

**Correct voucher use** keeps the original as corrected and records the replacement;
**Undo voucher use** restores its quantity while retaining history. Duplicate
submissions cannot spend the same form twice. Row locks prevent two sessions
from overspending the same person's allowance. Two different friends can each
spend their own final voucher independently. Expired allowances still permit
recording an actual past use within their validity; archived editions must be
reopened before editing.

The advanced **Individual overrides and shared booklets** section also supports
an explicitly shared physical booklet. This is optional and is not the normal
friends-group setup. Only that deliberately shared pool has one combined balance.
Neither group ownership nor sharing a pool exposes another participant's notes,
supporting flights, individual uses or savings. On removal of a participant or
account, private financial details are cleared; spent units in an optional shared
pool remain consumed instead of becoming available again.

Copying allowances into a new edition is an explicit choice in **Create next
edition**, which can also be changed in the builder. Review the new dates,
common personal allowance and individual exceptions. Fresh quantities inherit
the new dates; old uses, participants and invitation links are not copied.
Unchanged common allowances are established only when a friend accepts the new
edition. Up to 5,000 individual/shared exceptions can be reviewed in a copy.
Removing targets with voucher records is refused; archive the old edition, or
remove unused allocations before removing their targets. Date changes cannot
invalidate saved voucher validity or redemption history. None of this changes
the underlying flight logbook.

### Edition expiry reminders

Under **Email Notifications**, accepted passport participants can opt into
**Passport edition expiring** and choose their warning period (30 days by
default). This is available without pilot privileges. The reminder links to the
specific edition and its remaining airfields and vouchers; it never treats a
stamp as a voucher redemption. Passport pages show whether the end date has passed.

Daily checks skip archived, not-yet-started, expired and undated editions, inactive
accounts, departed organisation members and inactive or unaccepted participation.
Each recipient's preference and warning period apply independently. Existing email
deduplication limits an edition to one reminder per day; the existing snooze link
suppresses that deadline until it changes. Email delivery still requires configured
SMTP and the instance notification scheduler.

### Group edition history and consent

An owner can explicitly enable **Group visit sharing** for an edition, choosing a
group name and a display name. Linking passports alone never shares a personal
passport: every shared edition must be enabled separately. Existing accepted
participants can confirm their own sharing consent; invitation-based enrolment and
member management are a following increment. No new tenant permissions are granted.

Sharing consent explains that the owner can update the edition’s target list.
The shared date range is fixed: a different period requires a new edition and
fresh acceptance, rather than silently exposing another period of a logbook.

Group membership persists independently of annual participation. The series defaults
to editions the viewer accepted. **Include earlier editions I did not participate
in** adds earlier, ended or archived shared editions as read-only history. It never
creates historical participation, vouchers, logbook stamps or completion credit.
Private passports linked to the same series are excluded.

Ordinary edition sharing exposes only eligible airfields, the chosen group display
name and visit dates. A separate checkbox permits these visits to appear to later
group members viewing editions they did not join; its consent can be withdrawn
without changing the underlying visits. This does not share notes, flight links,
aircraft, voucher ledgers or photos. Group maps distinguish your own visits,
friends-only visits and nobody-visited targets by outline and emphasis. Your own
stamped edition takes colour priority over friends' editions; newest/oldest toggles
change only presentation. Frequency deduplicates the same event per participant
across overlapping editions. Blocked/inactive group membership is checked on each
series request and excluded from shared evidence.

### Passport-only account boundary

Passport-only membership is a separate access scope, with no tenant role (including
VIEWER). Known passport views, own visit/photo/voucher actions, group consent and
account security/preferences are permitted; unrelated pages, APIs, files and new
endpoints are denied by default. Global pilot/maintenance flags and existing
instance-administrator flags do not bypass a restricted membership. Navigation
hides fleet features, and the home page leads to passports. Notification delivery
and preferences exclude fleet events even when those global flags were set.

Changing membership scope revokes existing authenticated sessions. The next
authenticated request clears the retired login and its offline identity instead
of reusing its broader privileges. An organisation owner can
deliberately assign a normal tenant role through user management, making that
membership full-access under the selected role; joining a passport never does so.
An older schema cannot represent a role-less membership: the migration refuses a
downgrade while restricted memberships exist, rather than silently granting a role.

Invitation-based friend accounts use this authorization boundary for signup and
email verification. Later personal-pilot activation remains a separate increment.
Passport-only accounts can use discovery with catalogue reads limited to public
data and authorised own/group airfields, and build their own collections through
scoped catalogue writes. Joining a group never grants fleet access.

### Authentication context for existing accounts

The session layer can bind an authenticated account to a specific organisation
using its server-held login record. The selected membership must belong to that
account and remain active; URL parameters and extra cookie fields do not select
it. Losing that membership ends the login instead of falling back to an owner
role in another organisation. Roles and resource queries, notification preferences
and browser-storage identity use the same context. Credential changes retain the
chosen organisation while renewing the login. Mandatory MFA is checked for the
bound organisation.

Normal sign-in prefers an existing full membership over passport-only membership,
so joining a passport in an older organisation does not replace a pilot's normal
default. Personal reminder delivery keeps the same full default. Logbook evidence
remains personal across organisations; fleet permission checks remain bound to
the selected organisation and refuse mismatched aircraft. This is session-layer
preparation for the invitation hand-off, not a general organisation-switching UI.


### Invitation service foundation

The invitation service stores one admission capability per group edition. The
stored nonce and digest require the instance signing key to reconstruct the URL;
the URL is never a participant identity. Replacing or disabling it prevents new
joins without removing existing participants. An ended or archived edition does
not accept new joins. A separate expiring, server-held authentication context
retains the selected edition and explicit sharing choices. Completion rechecks
admission and binds the result to the authenticated account; replay cannot enrol
a second identity or reset voucher balances. Each participant receives their own
allowance, including when reusing an existing account from another organisation.

Membership changes lock the group before its passport parents, with a one-second
PostgreSQL lock timeout and a retryable conflict response. Removal records can
cover one edition or the whole group and retain a keyed email fingerprint rather
than an email address, allowing forthcoming owner controls to prevent self-rejoin
when an account or organisation membership has been deleted. Rotating the server
signing key invalidates admission URLs; owners must replace those links.

After enabling group sharing, the owner opens **Invite friends** to create and
copy an edition-specific link, optionally with an expiry date. Replacing or
disabling it stops new joins and leaves existing participants enrolled. A friend
with an existing account opens the link, chooses a display name and explicitly
accepts eligible-visit sharing, then signs in. The validated context survives
password entry and two-factor authentication; the invited organisation’s MFA
policy applies even when their usual organisation does not require it. A signed-in
friend uses **Join this passport**; an existing participant goes straight to the map.
Opening a preview never creates an account or participation. Invitation and sign-in
responses avoid caching, indexing and referrer leakage, and URL tokens are redacted
from access logs.

Friends without an account choose **Create an account and join this passport**,
enter their group display name, email and password, and verify a six-digit emailed
code. It expires after ten minutes and allows five attempts; requesting another
email is limited to once a minute per address across the instance. Account creation
starts only after email proof. Participation follows any mandatory authenticator
setup, then opens the invited edition automatically. Existing addresses must still
sign in with their original credentials; verification never replaces their password
or authenticator. A concurrently changed account must sign in again.

Configure the instance’s existing SMTP settings to support registration. Sending
failures explain how to retry without leaving a partial account. Demo mode does
not send registration emails. Pending verification uses expiring database challenges
with only opaque browser tokens, a keyed code digest and a password hash; neither
the code nor password is stored in the browser session.


### Leaving and managing a group

Open **Group members** from the passport or group editions page. Members see group
display names, without email addresses or private flight and voucher details.
**Leave edition** ends participation in the chosen edition and stops sharing its
visits, while retaining access to the group’s authorised history. **Leave group**
ends all access derived from that group. Neither action deletes private visit
records or spends or resets personal vouchers. Returning requires accepting an
active invitation again; another edition always needs its own acceptance.

The group page offers available invitations for editions not currently joined.
It reuses your account and group display name. The optional history view includes
ended or archived group editions you skipped, including gaps between years you
joined. It gives no retrospective participation, stamps or voucher allowance.
Unjoined future editions and private linked passports stay outside that history.

Owners can remove a member from one edition or the whole group, including its
history. Reusing the shared invitation cannot bypass removal, even after deleting
and recreating the account with the same email. **Allow rejoining** lifts the
selected removal restriction; the friend must accept an invitation again before
participation or visit sharing resumes. Separate edition restrictions remain in
force. Group permissions never grant aircraft or club permissions. In **Group ownership**, the owner selects an active participant and makes an
offer. That friend signs in and explicitly accepts within seven days, or declines;
the owner can cancel or replace the offer. A pending offer grants no permissions.
Acceptance rechecks both membership and ownership and transfers the group and all
its group editions together. Private linked passports, personal evidence and
voucher balances, aircraft permissions and account capabilities stay with their
existing owners. The former owner remains a participant and can then leave.
Removed or departed participants cannot accept an old offer, and replaced offers
cannot be accepted from stale forms. Owners cannot leave before transferring
ownership.


### Shared maps and visiting friends

A shared passport opens with all its targets together: muted markers for fields
nobody has visited, medium outlines for friends’ visits, and strong outlines for
your own visits. Closure remains a separate dashed outline. The list carries the
same labels, including fields without coordinates. Your completion percentage and
first-visit dates remain your own; a friend’s stamp never checks off your passport.
The frequency toggle counts your visits and visits shared by active friends.

Map popups, the airfield list and airfield details show which friends have visited,
using their group display names and shared visit dates. Combined editions list
each friend once, with dates grouped by edition, so repeat visits and multiple
years do not turn one person into several entries. You can also see these friends
on fields you have already visited. Private notes, aircraft details, flight links
and private photos stay out of this shared representation. Removal or leaving
withdraws that participant’s visits from the live shared map immediately.

After a transfer, **Manage group editions** on the members page includes editions
the new owner has not joined. They can manage those invitations and explicitly
accept participation separately. The passport list links only to joined group
maps; ownership alone never silently grants stamps or voucher allowances.

### Group progress and filters

A shared edition's page adds **Group progress**: how many airfields have been
visited by anyone and by everyone, and a leaderboard of each participant's
airfields and coverage. Tied participants share a rank and the next rank skips
(1, 1, 3); ties are listed alphabetically.

Everyone is measured against one common set of airfields: every open target,
plus each closed target that at least one participant stamped. A closure therefore
never penalises the people who got there in time, and a closed field nobody
visited drops out for everyone; the page says how many were left out. With
nothing achievable, or nobody sharing, there is no percentage.

The figures count the participants sharing their visits in that edition. Joining
through an invitation always includes that consent, so in practice this is every
friend who joined; the only exception is an owner who turned a personal passport
into a group without confirming sharing yet. Counting people who haven't agreed
to share would reveal their progress through the totals. Figures are calculated
live: leaving, removal or a changed flight shows up straight away, with no
archived snapshot. They are counts only: no flight, note or aircraft detail.

**Show airfields** gains "Visited by anyone", "Visited by everyone" and "Not
visited by anyone", and **Visited by participant** narrows the map and list to
one person's airfields. Filtering never changes who "you" are: marker styles and
your own progress still describe you. Participants are identified in the address
by their position in the list, never by an account ID.


### Destination discovery for passport-only accounts

Friends can open **Where next?** from an accepted passport and search public
OurAirports data or fields visible through their own passports and accepted group
participation. They can enter cruise speed and optional fuel/range estimates
manually. Aircraft selection and fleet lookups are unavailable in this account
scope, even when the same identity has broader permissions in another context.

Catalogue visibility is checked for name searches, code aliases, saved field
references, saved homes and the latest-arrival shortcut. Hidden materialised
public fields contribute only their original public CSV data, without tenant
corrections or custom aliases. Leaving a group or changing account scope affects
the next lookup, including one that already cached its former membership. Internal
non-request catalogue maintenance retains its explicit tenant-wide behaviour.


### Personal collections for passport-only friends

Passport-only accounts can create and edit their own collections, add missing
airfields and copy a linked next edition, without a pilot or fleet role. Existing
owner, draft, review and protected-removal checks apply unchanged.

Public catalogue targets retain the canonical identity but use original public
source details rather than tenant corrections. Owning such a target never grants
access to private corrections or manual aliases. Catalogue aliases record their
public source; only these aliases contribute automatic arrival credit to a public
target. Historical public aliases retain their date boundaries. Military/ULM
classifications entered by a passport-only owner are stored on that target, not
on the shared identity, and survive editing and edition copying. Existing curated
targets retain their earlier semantics. Classification provenance is excluded
from builder and map descriptors.

The additive migration backfills public provenance only for existing canonical
source codes. Downgrading refuses to discard public projections or local
classifications; these targets must first be explicitly removed.

Manual visit correction uses the same bounded date/note/own-flight validation as
creation. The update rechecks active participation, editable edition, target,
participant and manual evidence kind. Confirmed GPS evidence and another
participant’s visits cannot be rewritten through this form. The visit identity
and creation timestamp are retained; its flight link may be removed without
altering the flight. The form works without JavaScript.

### Portable participant data

**Export my passport data** on the passport list downloads an authenticated,
non-cacheable ZIP for the current organisation. This capability also works for
passport-only and read-only accounts. It never uses group ownership as permission
to export a friend's records.

The archive contains UTF-8 `passport-data.json` with
`format: "openhangar-passport-data"` and `version: 1`, plus available own photos
under `photos/<photo-id>.jpg`. JSON sections contain current accessible passport
metadata, own participation/consent records, target references, manual/GPS visits,
arrival exclusions, personal voucher allocations, own voucher use/corrections,
current eligible completion evidence and photo references. Dates/timestamps use
ISO notation; monetary amounts use decimal strings with their stored currency.
Flight IDs are references, not a second copy of the flight logbook. Shared-pool
events belonging to other participants, private flight notes, invitation keys,
idempotency tokens, contact lists and other participants' photos are excluded.

Withdrawn/inactive participation does not discard the person's stored evidence.
The export retains their own records and photo files, but omits inaccessible
passport titles and future targets. A retained target supplies public source
metadata where available; inaccessible custom fields supply stable IDs only.
Existing public projections and local classifications keep their usual
permissions. Current completion is a live snapshot for accessible passports, not
a historical group snapshot. A missing/concurrently removed photo has
`included: false`; its absence does not prevent the remaining export.

The ZIP spills to an anonymous temporary file after 4 MiB; response closure and
failure paths close it. Photos are copied from already sanitised private uploads.
The download uses normal navigation rather than HTMX and is excluded from
service-worker and history caches.

### Comparing group editions

The linked-edition comparison includes anyone/everyone coverage and each sharing
participant's coverage, beside that edition's independent personal completion and
stable-identity target additions/removals. Each edition uses its own common
denominator: open fields plus closed fields reached by at least one sharing
participant. No percentage is produced for an empty or unachievable group scope.

Figures follow the viewer's selected history scope and sharing consent. A later
friend viewing an earlier edition is labelled as a read-only viewer and is not
added to its leaderboard or everyone count. Withheld historical visits do not
contribute even to aggregate counts. Existing request-batched evidence supplies
the comparison; no per-edition statistics queries are added. Selecting an older
map while another edition is current never indexes evidence outside that map.

### Group airfield discussions

Discussion records belong to a group series and stable airfield identity, with
composite tenant foreign keys. They contain the explicitly shared author alias,
category (airfield, nearby recommendation or question), body, optional experience
date, posted time and optional edited time. Bodies are bounded to 8,000 characters.
The airfield detail shows the latest three comments and opens a paginated discussion
with category and posting-date filters. The optional experience date is separate
from the posting date. Normal CSRF-protected forms work without JavaScript; writes
are limited per account. Responses are private, non-cacheable and excluded from
HTMX history. The participant ZIP includes only comments authored by its requester,
including retained comments after leaving, never another member’s posts.

Comments do not create stamps, participants or voucher use. They survive target
or edition removal while the group exists. Active group members may discuss the
group's airfields independently of edition participation; this permission never
exposes private flights, notes or voucher ledgers. A private passport linked to
the series does not make its unique airfields discussion topics.

Writes use the same bounded group mutex as membership/ownership changes and
recheck membership and ownership after acquiring it. Authors edit/delete their
comments; group owners can delete another author's comment but cannot rewrite
it. Leaving or removal revokes access while the deliberately group-visible post
remains; deleting the author removes their comments. Deleting a group removes
its discussion, following the existing requirement to remove its editions first.
The migration refuses a downgrade that would discard stored discussion records.

### Private destination waypoints

The edition map/list and **Where next?** offer GPX and KML downloads of open
airfields the authenticated viewer has not visited. Edition state/participant
filters and discovery distance, time and range inputs are preserved; choosing
another participant never changes whose own stamps exclude a destination.
Discovery refuses downloads with missing reference fields or invalid filters.

The serializer projects only names, codes and coordinates. Files contain no
flights, tracks, visitor lists, conversations, private notes or voucher records.
Fields without usable coordinates are named on the page and in file metadata;
they cannot be tested against a distance limit and never become invented points.
Downloads remain authenticated, private and non-cacheable.

Outputs follow the [GPX 1.1 schema](https://www.topografix.com/GPX/1/1/) and
[KML 2.2 Point format](https://developers.google.com/kml/documentation/kmlreference#point).
Fixtures check namespaces, waypoint element ordering and coordinate order.
GPX represents the positive 180° meridian as −180° to respect its exclusive upper
longitude bound. XML-invalid text characters are omitted; KML descriptions are
escaped as HTML before XML serialization. No navigation track or altitude is
inferred.
