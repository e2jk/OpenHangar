# Invite friends to your group airfield passport

Share an edition-specific invitation with friends. They choose a group display
name and what eligible visits to share, then open their own passport map. Friends
who already have an account sign in; new friends choose a password and verify a
code sent to their email. A signed-in friend joins with one action. Every
participant keeps their own vouchers.

The owner can copy, replace, disable or set an expiry date on the invitation.
Replacing a link stops new joins through that link; it leaves existing participants
in place. Sign-in keeps track of the invited passport through two-factor
authentication and checks the link again before joining. A new passport-only
account can record its own visits and manage its security, without access to the
instance’s fleet or other pilots’ private logbooks. The instance needs working
SMTP settings to send verification emails.

**Maintenance**

- Group enrolment handles repeated requests without duplicate participation or reset voucher balances. Invitation URLs stay out of access logs and referrers, and invitation/sign-in pages are not cached or indexed.
- Made the group-history error response explicit, resolving a CodeQL return-path warning while keeping access failures unchanged.
- The ship script and ship-later PRs no longer promise a 23:20 UTC release: GitHub often starts the scheduled release hours later.
- Stabilised invitation setup in browser tests by waiting for sharing consent to finish saving, and made invitation error returns explicit for static analysis.
- Email verification limits failed attempts and repeat sends across workers. Existing accounts keep their credentials and authenticator; simultaneous registrations cannot overwrite them. Credential changes during enrolment require a fresh sign-in.
- Restored the translation catalogs’ standard generated formatting without changing their translations.
- Simplified verification locking to address an advisory CodeQL finding. Shipping guidance now requires reading advisory PR reviews as well as checking CI, including after auto-merge.
