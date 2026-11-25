# Security fixes: per-hangar permissions, enforced

- The pilot, maintenance and view-only flags of a user are now set per
  hangar. An owner of another hangar the person also belongs to (for
  example through a passport group) can no longer change them in the
  person's own hangar. Existing flags keep their current values.
