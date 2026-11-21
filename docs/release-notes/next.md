# Airfield tips and questions for your passport group

Group passports now have a conversation for each airfield. Ask about an approach
before your first visit, share flying tips or recommend a restaurant nearby.
The discussion stays with the group across editions, so advice from earlier years
remains available. Categories and date filters help you find relevant comments;
each shows its author, posting date and optional date of the experience.

Discussions are visible to active members of that group, including passport-only
friends who join later. Authors can edit or delete their own comments; group
owners can remove them. Private flight and visit notes stay separate. Your
personal data export includes only your own contributions, including comments
retained after leaving the group. Access is checked when opening or posting.

Take your next destinations with you: GPX and KML downloads contain the open
airfields you have not visited in your current passport selection or **Where
next?** results. Fields without usable coordinates are listed separately.
Downloads contain names, codes and coordinates, without flight tracks, private
notes or friends’ details.

Print a personal passport with your completion and first visit dates, or save
it as a PDF through your browser. Choose which of your stamp photos to include;
private notes and other participants’ photos stay out. The preview also works
without JavaScript using the browser’s Print command.

On group maps, visit frequency now counts **your own visits** by default. Choose
**Count visits by** to see the whole group or one participant instead; the map,
list and popups always say whose visits they count, and the per-edition counts
follow the same choice. Previously the counts silently added your friends' visits
to yours.

On group passports you can now share a stamp photo with your friends. Open an
airfield and choose **Share with this group**: the edition's other participants
see it with your group name under **Photos shared by friends**. Photos stay
private until you share them, sharing with the group never publishes them on a
public link, and **Stop sharing with the group** hides them again straight
away. Uploading a new photo makes it private again.

Friends with a passport-only account who fly can now keep their own pilot
logbook on the same account. Choose **Start my pilot logbook** on the passports
page: flights they log fill their passports automatically, and the logbook
gives no access to your aircraft, reservations, maintenance or other pilots'
logbooks. By default an administrator enables it from the user list
(**Enable personal logbook**); a new setting in **Configuration** lets account
holders start it themselves.

Recording a visit on a phone is quicker: **Record a visit** on a passport opens a
simple form where you choose the airfield and date, add an optional note and
save, with no map needed. You can still edit or delete the visit from its
airfield page.

Share a read-only passport map with a public link. The owner can replace or
revoke the link and set an expiry date. Each participant separately chooses
whether to publish their visited airfields, visit counts, photos and an optional
photo credit. The public list and map show month/year dates, with a frequency
toggle and a printable view; notes, flights, aircraft registrations and contact
details stay private. Withdrawing permission or revoking the link stops new
access immediately, including photo requests.

**Maintenance**

- Background navigation prefetches no longer create competing sign-in forms,
  which could replace the login CSRF cookie while switching accounts.

- Passport browser checks exercise personal ZIP downloads outside the signup flow,
  wait for photo uploads to finish and keep failure traces for diagnosis.

- Planning only, no code yet: the roadmap now has a Phase 42 “Explore” —
  shareable collections of places worth flying to (towns, attractions,
  restaurants) linked to nearby airfields, with **Where next?** becoming one
  search over places and passports, and *Personal* accounts. Later phases moved
  up by one number.

- Two ideas added to the backlog for later: an optional, off-by-default set of
  ULM / microlight fields, and cleaning every stored photo of its location data
  after reading what's useful from it — such as suggesting the departure or
  arrival airfield from where a counter photo was taken.

- A ship-later pull request's description now shows only the release-summary
  entries its own changes added, instead of the whole summary of the coming
  release, which presented earlier changes as its own. The descriptions of the
  56 affected pull requests since 7 November, three of their titles and the
  corresponding lines in the v3.38.0 and v3.44.0 release notes were corrected.

- Public links use shared storage and revocable, expiring tokens. Existing visits
  and photos remain private until their participants choose to publish them.

- The demo and development data now include a group passport shared by two
  pilots and a friend with a passport-only account, to try the group features.

- New automated checks for airfield passports: map marker sizes in the visit
  frequency view, separate arrivals on the same day, redirect targets in
  invitation links and friends who join a group's discussion later.

- The automatic backup check now also restores airfield passports — groups,
  friends' accounts, visits, vouchers, comments and stamp photos — and fails
  if any of them is missing after the restore.

- Documentation only: an audit of the airfield-passport roadmap ticked the
  edition, history, colour, reminder and invitation items that were already
  built and tested; the few still open now say what remains.

- Public-passport browser checks now declare their pytest markers for static
  analysis and wait for publication changes to finish before checking public
  access, avoiding a timing-dependent test failure.

- Planning only: three optional airfield-passport ideas — GPS-suggested
  intermediate stops, a runway suitability comparison in **Where next?** and a
  publisher-operated passport-only instance — moved from the roadmap to the
  backlog, so the passport phase can be completed without them.
