# Organization administration experience

The organization Admin workspace uses a shared sidebar grouped into Workspace, Production setup, and Documents & insight. The overview links to existing management screens through compact setting rows.

Materials, outside vendors, and stations show collapsed records. Search and active/inactive filters narrow the visible list; counts and empty results are explicit. Open a record to edit it, or open the Add row to create one. Materials and vendors use two-column editors on larger screens, with a single column on small screens. User creation is also collapsed until requested.

The redesign preserves existing form actions, CSRF protection, version checks, audit reasons, and role restrictions. It introduces no database changes and does not change catalog validation or permission semantics. Search is local to the displayed catalog and does not change stored records. The standalone developer portal remains separate.

## Controlled material choices

Materials now use approved dropdown choices for metal, grade/alloy, gauge, thickness in millimeters, stock finish, sheet size, and required outside service. Grade, gauge, thickness and finish are scoped to their metal. Sheet size and service are organization-wide. Gauge does not determine thickness automatically.

Admin → Material choices provides creation and active/inactive management with reasons, version checks and audit events. Choice values are not renamed; retire an old option and add its replacement. New selections are validated on the server, including metal scope. Existing unchanged values remain editable if their choice is retired; historical job snapshots are not rewritten. New material names are generated in the form; existing names remain preserved.

Schema initialization imports distinct values from existing material records into the controlled vocabulary without changing the materials. Repeated initialization preserves retired choice status. Missing grades, dimensions and sheet sizes are not invented. Add them once through Manage choices. Final product color remains separate from stock finish and is not introduced by this change.

## Guided shop setup and custom fields

Admin → Set up your shop previews the versioned `sheet-metal-v1` starter. Admins explicitly select station and/or material sections and acknowledge the preview. Application is additive and repeatable: existing entries and retired choices are preserved. Each application records its version, actor, reason, created entries and skipped entries in security history. No vendor identities, alloys, thicknesses or sheet dimensions are invented. No production is released.

Admin → Custom fields defines organization-specific request details with text, number, date, dropdown or yes/no inputs. Each definition has a version, optional approval requirement, active status and audit record. Dropdowns have up to 50 approved values; an organization can define up to 50 fields. Input types are fixed after creation; retire and replace a field to change its type.

New Request and Edit Request render these fields under Shop details. A configuration revision token rejects stale forms. Values are validated server-side; required values are enforced at approval rather than draft save. Explicit request edits adopt the current definitions and invalidate approval when the saved field snapshot changes. Existing requests are not silently rewritten when the admin changes configuration. On explicit edit, retired fields retain their previous evidence and cease to block approval.

Conversion snapshots definition IDs, labels, types, options, requirements, versions and values into immutable JobRunner storage. Requests, tickets, unit screens and printed travelers display these details. Fields are request/job level, not per-unit measurements or automatic routing conditions. PN templates, gauge-to-station automation and a cloud database migration remain separate work.


## Guided onboarding and invitations

Admin → Getting started provides eight resumable review steps: company, branding, stations, materials, vendors, people, parts and first request. Optional steps can be deferred and every step reopened. Review status is organization-scoped and audited; it does not certify configuration completeness or bypass production release controls. Existing customer setup is preserved.

Admin → Invite your team creates an invitation for a new unique username with one or more approved roles. A TV display role must stand alone. The activation link is shown once for private manual sharing; email delivery is not yet connected. Links expire after seven days, can be revoked, and can be used only once. Only a SHA-256 token hash is stored. The secret travels in a URL fragment and POST body, not access-log query strings. Never share these links in public channels. Localhost links work only on the same machine; deployment must use the configured HTTPS public origin before remote onboarding.

The recipient reviews organization and roles, sets a 12–256 character password, optionally enrolls a 6–12 digit PIN for operator/inspector roles, then signs in. Activation never changes an existing global account or accepts role changes from the invitation form. Existing identities remain managed through the established administrator tools. Organization provisioning remains invite-only through Melken's developer administration; public signup, subscriptions, email delivery, and cloud authentication integration are separate work.
