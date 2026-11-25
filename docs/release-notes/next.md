# Security fixes: file folders and access checks

- Renaming a misnamed document folder on the "Files to review" page now only
  accepts that hangar's own `<registration>/<folder>` paths. Before, a
  crafted request from an owner or admin could move folders that belonged to
  another hangar or to other users.
- Owners can no longer demote, revoke or change the flags of an admin, and
  only the instance administrator can change the instance administrator's
  access. Revoking someone's access to a hangar now removes that membership
  only: an account that still belongs to another hangar (for example
  someone who joined a passport group) keeps working there.
- The pilot GPS import only links a track to one of the flights it offered
  in the "select the matching flight" list.
- A flight on an aircraft you have no access to can no longer be opened or
  edited (nor its track images, GPS parts or photos), unless you flew it.
  Re-saving your own flight after losing access to its aircraft now keeps
  it on that aircraft instead of moving it off the aircraft's log.
