# No more font-cache errors in the logs of a read-only container

- Dismiss all open data checks for the current hangar in one action.

**Fixes**

- A container run read-only, as the reference setups do, no longer logs
  "Fontconfig error: No writable cache directories" on every PDF: the
  font cache now lives in the writable /tmp.
