# Password and PIN management

Signed-in organization and platform users can open **Account security** from the header. Organization users can change their password, set or change their organization PIN, or disable that PIN. Platform identities use passwords only. Display accounts can manage their own credentials without gaining production access.

Every self-service change requires the current password, including changes made from a PIN session. New passwords require 12–256 characters and confirmation; PINs require 6–12 ASCII digits and confirmation. Five incorrect current-password attempts block changes for 15 minutes. Forms have CSRF protection and do not echo or retain entered credentials. Credentials are hashed; audit events record actions without credential values.

Password changes invalidate all sessions for that identity across organizations. PIN changes invalidate sessions for that organization. Platform password changes invalidate all sessions for that platform identity. The user must sign in again after a successful change. Organization admins retain the existing audited PIN reset/disable controls in User management.

## Forgotten-password recovery

Recovery currently requires a trusted server administrator; there is no email reset flow. Verify the requester's identity through an established channel before resetting access. Run these commands in the configured application environment and enter the new password at the hidden confirmation prompts, never as a command-line argument:

```sh
flask --app productionrunner reset-password USERNAME --reason "Verified account-owner recovery"
flask --app productionrunner reset-password USERNAME --platform --reason "Verified platform-owner recovery"
```

Recovery is audited and revokes existing sessions. Organization passwords belong to the global user identity, so a reset affects all of that user's memberships. Organization admins cannot reset another organization's global password through the web UI. Recovery does not reactivate a disabled account or grant roles. Deliver the replacement password privately and have the owner change it using Account security; changing a temporary password is not yet automatically enforced.

Existing saved staging credential files are provisioning records, not synchronized password stores. After changing a password, save the new one in the user's password manager; the old provisioning file no longer works. PINs are not stored by the application's remember-sign-in feature.

## Permanently removing an organization account

In **People & access → Manage → Delete account from this organization**, review the deletion, type the exact username and select a reason. This works for people and dedicated TV accounts, including previously revoked accounts. Unlike revoking access, deletion removes the account from the directory and prevents restoration through the UI or membership CLI. Existing sessions and the organization PIN are invalidated atomically with an immutable deletion event. Self-deletion is blocked and an active admin must remain.

This is permanent account removal, not erasure of historical identity rows: production, inspection and security records retain attribution, and the username stays reserved. Other organization memberships and their password remain unchanged. For an identity with no other membership the old password is replaced by a random, undisclosed credential. Pending invitations for this username in this organization are revoked. Platform accounts are separate and are not affected.
