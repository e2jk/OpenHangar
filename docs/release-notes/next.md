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
