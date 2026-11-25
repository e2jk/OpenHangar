# Security fixes: file folders and access checks

- Renaming a misnamed document folder on the "Files to review" page now only
  accepts that hangar's own `<registration>/<folder>` paths. Before, a
  crafted request from an owner or admin could move folders that belonged to
  another hangar or to other users.
