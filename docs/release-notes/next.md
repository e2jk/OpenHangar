# Security fixes: per-hangar permissions, enforced

- The pilot, maintenance and view-only flags of a user are now set per
  hangar. An owner of another hangar the person also belongs to (for
  example through a passport group) can no longer change them in the
  person's own hangar. Existing flags keep their current values.
- The "View only" flag and the per-aircraft permissions set in the user
  list are now enforced. A view-only member can no longer change anything
  in the hangar's aircraft, flights, maintenance, bookings, documents,
  expenses or logbooks, and a permission removed from someone's aircraft
  grant (logging flights, booking, maintenance, components, editing the
  aircraft) now really stops that action. The pilot and maintenance flags
  give their usual permissions; students still log flights by default.
