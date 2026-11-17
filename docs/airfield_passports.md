# Airfield passports

Phases 41a/41b provide the data foundation, owner-only builder and airfield
selection map. Visit/stamp maps and invitations follow in later increments of
[Phase 41](implementation_plan.md#phase-41--airfield-passports).

## Read access

The Airfield passports navigation link appears for pilots and existing passport
owners/accepted participants, including users without pilot capability. The list
and detail pages reveal only passports in the user's current tenant which they
own or have actively accepted. An administrator role alone does not disclose
someone else's passport. Pending/inactive membership grants no access.

Both pages opt out of HTMX history and service-worker caching. No passport-only
registration or broader account-permission changes are enabled by this increment.

## Build and manage a passport

Choose **Airfield passports → Create passport**, enter a name and optional dates,
then search by countries, regions, airfield types, code or name. Tick results and
apply them, or add all matches (up to 5,000 targets). The first 500 matches are
listed; narrow the search to inspect more. Paste codes separated by spaces,
commas, semicolons or new lines. Unknown/ambiguous codes must be corrected or
explicitly dropped before saving. Missing fields can be entered manually, with
optional coordinates; coordinate-less fields remain in the list.

Military and ULM filters preserve an explicit unknown state. Manual classification
is saved with author/time provenance and shared across passports in this tenant;
it is not copied into the source dataset. The builder's blue markers are selected
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
