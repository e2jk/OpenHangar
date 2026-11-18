# Invite friends to your group airfield passport

**Invite friends**

A group passport can now be shared with friends through an invitation link for a specific edition. Friends pick the name the group sees them under and confirm they're happy to share their eligible visits, then open their own copy of the passport map.

- **Friends with an account** sign in and join with one click. **New friends** choose a password and confirm a code sent to their email. Two-factor sign-in still applies, and the invitation is checked again just before they join.
- **Friend-only accounts** can record their own visits and manage their account security, but can't see the fleet or other pilots' logbooks.
- **The owner controls the link**: copy it, replace it, switch it off, or give it an expiry date. Replacing a link stops new joins through the old one; people who already joined stay.
- **Everyone keeps their own vouchers**: joining never touches anyone else's balance.
- Sending the verification codes needs working email (SMTP) settings on the instance.

**Group membership**

- Friends can leave a single edition and stay in the group, or leave the group entirely.
- Owners can remove members, and later explicitly let them back in. A forwarded invitation can't get a removed member back in.
- Leaving or being removed keeps your private visits and voucher records.
- Group pages list editions you can still join, and can show the years you skipped. Joining an older edition never gives retrospective stamps or vouchers.

**Handing over a group**

A group owner can offer ownership to an active participant, who has seven days to accept. Accepting hands over the group and all its editions; everyone keeps their own vouchers and private records. The former owner can then leave. An offer that's pending, cancelled or replaced gives no control.

Owning an edition doesn't make you a participant. The members page lists every edition with its invitation, including ones the new owner hasn't joined; their maps appear in your list once you join them.

**Shared maps for each edition**

Each shared edition now opens on the group map, which until now was only available for all editions combined. It shows the airfields nobody has visited, the ones friends have visited, and your own stamps. One visit checks off an airfield, and the frequency view highlights repeat visits. Your completion still counts only your own visits.

Airfield details and map popups list which friends have been there. When you view several editions together, their visits are grouped by edition. Flight details and private notes are never shown to others.

**Maintenance**
- Invitations and sign-in: invitation links stay out of server logs and aren't passed on to other sites, and invitation and sign-in pages aren't cached or indexed by search engines. Joining twice by accident creates no duplicate and doesn't reset anyone's voucher balance.
- Email verification limits wrong codes and repeated sends, across all server processes. Registering with an existing account's email can't replace its password or authenticator, even when two registrations happen at once, and changing your credentials during sign-up asks you to sign in again.
- A passport photo whose file is missing now gives a normal "not found" page instead of an error. Who may see private photos is unchanged.
- The ship script and ship-later PRs no longer promise a 23:20 UTC release: GitHub often starts the scheduled release hours later.
- Code-scanning fixes: clearer error handling in group history and invitation pages, and simpler locking in email verification. The project's shipping guidance now also asks to read advisory review comments, including after a pull request merges automatically.
- More reliable browser tests for setting up invitations.
- Translation files are back in their standard generated layout; no translation changed.
