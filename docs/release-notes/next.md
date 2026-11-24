# Sign-in after sign-out and password changes keep the app signed in

- Signing in right after signing out (for example as another account) no
  longer sometimes shows the "You're offline" page instead of the sign-in
  form.
- Changing your password no longer sometimes signs the app out or fails to
  show the next page: a background request still on its way with the old
  login is no longer taken as a sign-out.

**Maintenance**

- The passport browser tests wait for an airfield's page to finish loading
  after clicking it in a passport, instead of failing on a slow CI runner.
- A release is numbered as a patch or a new minor version by comparing it
  with the latest *published* release, not with the latest version tag: a
  build that failed after reserving its number made v3.61.1 a "patch" for a
  release full of new features.
