# Co-owner check and your own visits after you stop sharing

**Security:** the aircraft co-owner form accepted any user account, including
users of another organisation, and listed passport-only friends. Only full
members of the aircraft's organisation can now be chosen as co-owners.

In a past group edition, a participant who turned off visit sharing saw their
own stamps disappear; they now always see their own visits, while the group
no longer counts them.

**Maintenance**

- French: 56 older messages now use the narrow no-break space before
  `:`, `;`, `!`, `?` and `»` (and after `«`), as French typography requires.

- Airfield-passport code now has one definition of an active participant and
  one helper for comparing stored timestamps, instead of copies in a dozen
  modules. A share-link test no longer fails when a random page nonce happens
  to contain the number it checks for.

- Shipping from a parallel worktree: `scripts/ship.sh --later` run in another
  agent's `git worktree` now pushes to that worktree's own
  `ship-later--<branch>` lane with its own pull request, so two lines of work
  can land side by side. Releases straight away (and security fixes) still go
  through the main worktree.
