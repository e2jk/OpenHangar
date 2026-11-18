# Invite friends to your group airfield passport

Share an edition-specific invitation with friends who already have an OpenHangar
account. They choose their group display name and what eligible visits to share,
then sign in and land on their own passport map. If they are already signed in,
joining takes one action. Every participant keeps their own vouchers.

The owner can copy, replace, disable or set an expiry date on the invitation.
Replacing a link stops new joins through that link; it leaves existing participants
in place. Sign-in keeps track of the invited passport through two-factor
authentication and checks the link again before joining. Registration for friends
without an account is still being developed.

**Maintenance**

- Group enrolment handles repeated requests without duplicate participation or reset voucher balances. Invitation URLs stay out of access logs and referrers, and invitation/sign-in pages are not cached or indexed.
- Made the group-history error response explicit, resolving a CodeQL return-path warning while keeping access failures unchanged.
- The ship script and ship-later PRs no longer promise a 23:20 UTC release: GitHub often starts the scheduled release hours later.
