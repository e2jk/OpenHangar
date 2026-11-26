# Clickable place cards and dismissing all data checks at once

**Places**

- A click anywhere on a place card in Places opens that place, not just its
  name; its airfield and tag pills still filter the list.

**Data checks**

- Dismiss all open data checks for the current hangar in one action.

**Fixes**

- A container run read-only, as the reference setups do, no longer logs
  "Fontconfig error: No writable cache directories" on every PDF: the
  font cache now lives in the writable /tmp.
