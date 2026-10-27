# ProductionRunner

A runnable, **provisional** manufacturing workflow MVP: one part number per ticket, a physical JobRunner barcode traveler that stays with the material, organization-scoped, role-controlled production operations, and independent final inspection.

**Specification status:** PR v1.9 was referenced but its contents were unavailable through the linked chat. This implementation is based on the current request and the visible traveler/hybrid-tracking decisions. It is not a claim of PR v1.9 compliance. See [requirements and open decisions](docs/requirements.md) before approving the draft PR.

## Cloud subscription direction

Melken Solutions is the developer/service provider; subscribing shops are customer organizations. Cloud hosting is the primary direction. See the [cloud pilot plan and implemented hosting foundations](docs/cloud-pilot.md). A hosted customer needs no local application server; the local server runbook remains an alternative. No cloud service has been purchased or deployed, and native MFA/billing are not implemented.

## Initial shop installation

The agreed pilot layout is **Laser 1, Laser 2 and Packing**, each with a mini PC driving a station TV and a separate operator desk monitor, plus a fourth mini PC/TV for the whole-shop queue. Shared shop equipment is one network printer; the application server is hosted centrally in the preferred cloud arrangement; an existing office workstation handles planner/admin uploads and scheduling. **No tablets are planned.** Operators print travelers/drawings and transfer downloaded DXFs to the lasers by USB. TV and operator sessions use separate browser profiles.

See [the installation plan and pilot acceptance procedure](docs/hardware.md) for equipment totals, document/revision handling, network and backup needs, and deployment work still outstanding.

## Shop installation guides

- [Server installation, HTTPS, startup, backups and recovery](docs/server-installation.md) — alternative on-premises Windows/Ubuntu deployment, not required for cloud customers.
- [Windows mini PC installation](docs/mini-pc-installation.md) — extended displays, separate browser profiles, printing, USB transfer and shift-start checks.

These are deployment runbooks, not a claim that a shop server or unattended kiosk has been installed or qualified.

## Run locally

Requires Python 3.12 or newer. No external database or cloud account is needed.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m flask --app productionrunner init-db
python -m flask --app productionrunner add-tenant demo-shop --name "Demo Customer Shop"
python -m flask --app productionrunner add-user planner --tenant demo-shop --roles planner
python -m flask --app productionrunner add-user operator --tenant demo-shop --roles operator
python -m flask --app productionrunner add-user inspector --tenant demo-shop --roles inspector
python -m flask --app productionrunner run --host 127.0.0.1 --port 5000
```

Each `add-user` command prompts for a password (12+ characters). There are no seeded accounts or default passwords. Open <http://127.0.0.1:5000>. Enter organization `demo-shop` when signing in. Use separate browser profiles or sign out between roles or organizations. `init-db` preserves existing records.

SQLite data and the generated session signing secret live in ignored `instance/`. Keep both private. The built-in server is for local evaluation; do not expose it as a production service.

## Upgrade an existing single-tenant database

Stop the application and all writers. Keep the database and session secret private. `init-db` refuses to silently upgrade the original schema. Explicitly assign **all existing users and records** to the organization that owns them:

```sh
python -m flask --app productionrunner migrate-tenancy --tenant demo-shop --name "Melken Solutions" --backup instance/pre-tenancy.sqlite3
```

The backup path must not exist. Migration preserves IDs, credentials, workflow state and audit entries, checks ownership constraints, and rolls back on failure. Keep the backup secure; it contains password hashes and manufacturing data. Existing sessions must sign in again with an organization. To restore, stop the app and replace its database with the backup, then run the previous application version; do not run the new app against the legacy schema.

For additional organizations, run `add-tenant`. Usernames identify people globally; membership roles belong to one organization. Give an existing identity access or revoke access without deleting history:

```sh
python -m flask --app productionrunner set-membership planner --tenant another-shop --roles viewer
python -m flask --app productionrunner set-membership planner --tenant another-shop --revoke
```

Create `another-shop` first. Role changes and revocation take effect on the next request. CLI provisioning is trusted host administration, not a web admin privilege. Passwords remain available; optional PIN sign-in is described below.

## PIN sign-in and security history

For an existing tenant-enabled database, stop the app, make a SQLite backup, and run `python -m flask --app productionrunner init-db` once before restarting. This adds credential, attempt-limit and security-history tables without changing existing accounts or workflow records. Original single-tenant databases must use `migrate-tenancy` first.

A trusted local administrator can enroll or reset an individual membership's PIN:

```sh
python -m flask --app productionrunner set-pin operator --tenant demo-shop --reason "Initial station enrollment"
```

Enter the new PIN twice at the hidden prompts. PINs contain 6–12 digits, are hashed, and are scoped to the named organization. Do not put credentials in command-line arguments or audit reasons. On the sign-in page, select **PIN** and enter organization, username and PIN. This is individual authentication; shared role PINs are not supported. Roles and independent-inspection rules are unchanged.

Run `set-pin` again to reset, or add `--disable` to disable PIN sign-in without prompting for a new PIN. Both invalidate existing PIN sessions for that organization on their next request; password sessions remain valid. CLI actions record the local OS account as the administrator, the target membership, reason and UTC timestamp. Tenant admins can open **Security history** to see the latest 200 events; PIN values/hashes are never included there.

Both sign-in methods share a persistent five-failure limit per organization/username in a 15-minute window. A source-address limit of 50 failures in that window also covers unknown accounts. Locked requests return HTTP 429 until the original window expires; successful authentication clears the account counter, but not the source counter. PIN reset does not clear throttling. Forwarded-IP headers are not trusted; behind a reverse proxy, requests may share the proxy's source limit. Limits are local-MVP policy choices pending requirements review.

## Try the vertical slice

1. Sign in as `planner`; create a ticket for `PN-1001`, revision `A`, quantity `2`. Enter work instructions with acceptance criteria and two routing steps, one per line (`Cut | Cut to drawing dimensions`, then `Assemble | Fit per work instruction`).
2. Choose **Prepare print / reprint**, print at 100%, scan-test the Code 128 symbol, and attach the JobRunner to the material container.
3. Sign in as `operator`. Scan into the work queue field, using a keyboard-wedge scanner with an Enter suffix, or type the printed `JR-…` ID. Open the ticket and complete each operation in sequence for all units.
4. Sign in as `inspector`. Record a pass with evidence to accept the batch, or a failure to hold it. A user who completed any production step cannot inspect that ticket, even if assigned both roles.
5. Inspect the audit history. Refresh or restart the server: records persist.

Batch tracking remains the default. New serial tickets now create independent unit travelers and progress records. Serials must be unique **within the ticket**; no cross-ticket serial registry is enforced. Split/merge and different route definitions per unit are not implemented. Held work supports documented repair and independent reinspection as described below.

## Try independent serial travelers

1. Create a new ticket with **Independent serial units**, quantity 2, serials `S1` and `S2`, and an ordered route.
2. Open each unit from the ticket’s progress table. Choose **Prepare unit print / reprint**, print and scan-test its `JR-U-…` barcode, and attach it to that individual unit. The parent `JR-…` barcode opens the ticket overview; its printed page is a cover sheet, not a unit traveler.
3. Scan a unit barcode as an operator and complete its route one step at a time. Every unit has its own version and progress; advancing S1 does not advance S2. Reprints retain the same unit ID.
4. Inspect each finished unit separately. Anyone who performed production on that unit is disqualified from inspecting it; production on a different unit does not disqualify the inspector. These independence rules are provisional pending specification review.
5. Pass accepts that unit; fail holds only that unit. Segregate held material. Other units may continue, but the ticket summary remains held if any unit is held and becomes accepted only when all units pass. The ticket summary is not a shipping authorization.

For an existing database, stop the app, make a SQLite backup and run `python -m flask --app productionrunner init-db` before restarting to add the serial tables. For the original pre-tenancy schema, use `migrate-tenancy` first. **Existing serial-inventory tickets retain whole-batch execution and are marked Legacy serial inventory.** No historical operation or inspection is expanded into inferred unit records. There is no automatic conversion of in-progress or completed tickets.


## Verify

```sh
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover independent unit routing/inspection, parallel progress, held siblings, unit travelers, cross-tenant denials, stale/duplicate submissions, unit audit atomicity, legacy serial compatibility, PIN workflow/inspection, reset/disable, hashing, concurrent and persistent throttling, security-history access, additive upgrades, two-organization isolation, scoped roles, membership revocation, migration/backup/rollback, the end-to-end workflow, permissions, self-inspection denial, route order, fail/hold behavior, stale and concurrent submissions, validation rollback, CSRF, output escaping, persistence, barcode output, and audit immutability. GitHub Actions runs the suite on Python 3.12 and 3.14.

## Documentation

- [Product and software architecture](docs/architecture.md)
- [Hardware and station design](docs/hardware.md)
- [Requirements, assumptions and PR v1.9 reconciliation](docs/requirements.md)
- [Repository inspection and delivery record](docs/repository-inspection.md)

This is a local evaluation slice. Before shop-floor rollout, reconcile the specification, validate physical printers/scanners, harden authentication and hosting, establish backup/restore, and approve the manufacturing workflow.

## HOT jobs and queue priority

Planners and admins can use **Mark HOT** in the work queue or ticket detail, choose a priority from 1–99 (1 highest, default 10), update that priority, or **Clear HOT**. Other roles see the flag without editing controls. HOT belongs to the whole job and appears on its individual serial-unit pages.

The queue lists HOT jobs first, ordered by priority number, then the oldest HOT designation, then stable ticket ID. Equal numbers are allowed. Changing the number preserves the HOT timestamp; clearing and reapplying HOT starts a new timestamp. Normal jobs retain newest-first ordering. Changes record actor, timestamp and old/new priority in ticket audit history. Stale changes require a reload.

HOT affects queue order only: it never releases held material, skips routing or permits self-inspection. Status remains visible, including for held/accepted jobs; HOT is not automatically cleared on acceptance. After backing up an existing database, run `init-db` before restarting to add the priority table without changing existing jobs.

## Parts, revisions and drawings

Open **Parts & drawings** from the header. Planners/admins can add an exact PN/revision with a description and either a PDF upload (maximum 10 MB) or an HTTPS drawing link. Search by PN/description and open any revision’s drawing. Uploads are stored inside the SQLite database with a SHA-256 digest; database backups include the drawing bytes. No external document service is contacted by the server.

On **Create ticket**, choose a PN/revision from the list. Its PN and revision fill automatically, with a link to view the drawing, and the drawing reference is attached on creation. Manual PN entry remains supported and automatically matches an existing exact PN/revision in the current organization. If none exists, the ticket is created without a drawing, as before. Batch tickets, serial-ticket overviews and individual units all show the linked drawing.

Existing tickets without a drawing can use **Attach matching drawing** after that exact PN/revision has been added to the library. This requires planner/admin permission and records an audit event; it never replaces a drawing already attached. New library entries are immutable. Add a new revision when a drawing changes. External URLs are fixed references, not snapshots of their destinations: use version-specific HTTPS URLs and verify the revision at the source. Local filesystem/SMB paths are not web links; upload the PDF instead. External authentication remains with the source system.

Stop the app, make a database backup and run `init-db` before restarting an existing installation. This adds library/mapping/audit tables without changing current tickets or guessing drawing associations. PDF admission checks the extension, signature and size, not malware or full document validity; use trusted drawings during local testing. Uploaded PDFs are served only to authenticated members of the owning tenant with PDF MIME, no-sniff, no-store and sandbox headers. There is no formal drawing approval/release workflow yet.

## Laser DXF files

Planners/admins can upload an optional DXF alongside the drawing when creating a part revision. For an existing revision, open it in **Parts & drawings** and use **Attach DXF**. One nonempty `.dxf` file up to 20 MB is allowed per revision. Once attached, it cannot be replaced or deleted; add a new part revision for a changed production file. Adding the first DXF makes it available to all jobs already linked to that exact revision.

Operators can choose **Download DXF for laser** on the part, ticket or individual unit page. All authenticated members of the owning organization have the same download access as for its drawings. The download retains the original bytes and uses a PN/revision-based `.dxf` filename. It is an attachment, not a browser preview. Upload and download-request events record the user/time in the part revision history; a download-request event does not prove that the file was saved or loaded into a machine. Downloading never completes a production operation.

Files are stored with a SHA-256 digest in SQLite and are included in database backups. Validation checks only extension, nonempty content and size; there is no CAD geometry, units, toolpath, malware or laser compatibility validation. Verify the file and setup in the laser’s own software before production. ProductionRunner does not connect to or operate the laser. Back up and run `init-db` before restarting an older installation to add the DXF table without changing existing part/drawing records.


## Admin user management and production controls

Sign in as an admin and open **Admin** in the header. Create individual users with an initial password, assign explicit roles, enroll/reset/disable their tenant PIN, or revoke/restore organization access. Every web change requires a reason and creates a security-history entry. PIN edits invalidate PIN sessions; role/access changes invalidate all sessions for that membership, including sessions that remained unused while access was revoked. Passwords are global identities and cannot be reset by a tenant admin. Existing usernames cannot be claimed through this console. Last-admin protection applies to web access edits and `set-membership`; grant another admin before removing the last one.

On a ticket, admins can **Cancel ticket** with a reason and confirmation. Cancellation is permanent, blocks new recorded production/inspection/rework for all its units, appears on live and newly printed travelers, and moves the ticket behind active jobs in the queue. Historical state and records remain intact. Notify operators and segregate material: this software does not stop machinery or invalidate paper already printed. Part-library downloads remain available.

For a failed batch or individual unit, an admin can **Authorize rework** by recording a reason, repair instructions and acceptance criteria. This first slice supports one documented repair step per case. An operator records repair evidence, then an independent inspector records pass/fail against the criteria. Any original production contributor or contributor to any repair round for that target is excluded from reinspection. A pass accepts that batch/unit; a fail keeps it held and permits a new case. Original failed inspections are never overwritten, and each round remains in the traveler audit history. Admin alone cannot complete repairs, inspect, or directly clear a hold. Serial siblings retain their own progress.

Stop the server, back up SQLite, and run `init-db` before restart. The upgrade adds control tables without rewriting existing production history. These are provisional local-testing workflows, pending manufacturing-process review; concessions, scrap disposition, split/merge and multi-step rework routing remain out of scope.


## Organization management

The **Admin** header link opens a central portal for organization details, users, production controls and security history. Choose **Manage organization** to edit the current organization’s display name, contact name/email, phone and address. Contact fields are optional. Its existing organization sign-in code remains read-only, so a name change does not affect login. Name changes appear in the header and newly rendered travelers; previously printed paper is unchanged. Contact details do not send notifications.

Local admins can view or change only their current organization’s profile. Saves require a reason, reject stale forms, and atomically record before/after values in security history. Other organizations cannot be selected through a form or URL. This first version manages the signed-in organization; developer admins manage organizations separately at `/developer`. Back up the database and run `init-db` before restarting to add the profile table without changing existing organization names or sign-in codes.


## Two admin levels

**Organization Admin** uses the normal organization sign-in and manages only that organization’s profile, members, PINs and production controls. The local role remains `admin`. It cannot grant developer access.

**Developer Admin** signs in at `/developer/login` with a separate platform username/password, without an organization code or PIN. The developer portal lists all organizations, creates an organization and its initial local admin atomically, edits organization profiles, creates additional local admins, and grants/removes local admin from existing organization members. Removing the last local admin is blocked. Removing an admin’s only role disables their organization access; other roles are preserved. Assignments invalidate affected membership sessions. Existing global usernames are not claimed or reset when creating local admins.

Developer accounts are provisioned only by the trusted local host:

```sh
python -m flask --app productionrunner add-developer your-name --reason 'Initial developer administrator'
python -m flask --app productionrunner set-developer-access your-name --inactive --reason 'Access revoked'
```

The first command prompts for a confirmed password (12–256 characters). Use `--active` to restore access. Last-active-developer protection applies; disabling/restoring increments a credential version so old sessions cannot return. Developer creation/access changes are recorded with the local OS actor. No developer password or role management is exposed to organization admins.

Developer sign-in uses durable account/source throttling and CSRF protection. Its credential store and sessions are separate from organization users. Developer access does not grant manufacturing execution or inspection, and the portal does not impersonate a tenant user. Sign out and use an individual organization account for production work. Platform writes record immutable platform history; local admins can see developer changes to their own organization in Security History. Organization deletion/suspension and browser-based developer-account provisioning are outside this slice.

Back up SQLite and run `init-db` before restarting to add the separate developer accounts and platform history tables. No existing organization account is automatically promoted.


## Tablet Shop Floor mode

Operators open **Shop Floor** in the header or `/shop-floor`. The dedicated touch view uses 72-pixel controls, 80-pixel completion buttons, large text and spaced job cards. Scan a traveler using a keyboard-mode barcode scanner (Enter suffix), or tap Open job. The queue retains HOT ranking and shows active tickets, including held work; accepted/cancelled tickets remain accessible by scanning. Lists paginate in groups of 50. Tap the scan field before scanning; camera scanning is not implemented.

Batch travelers show the next operation, its instructions and the job instructions. Serial-ticket covers require selecting/scanning an individual unit before completion. **Complete operation** opens a read-only confirmation page showing PN, revision, batch quantity or serial and operation. **Confirm completion** submits to the existing guarded completion endpoint and returns to the touch view. Duplicate taps are disabled while saving; server version/order checks reject repeats and stale confirmations even without JavaScript. No work is queued offline. Reopen the live traveler if a network failure leaves the save outcome unclear.

Drawing/DXF buttons retain existing tenant/file audit behavior. Inspection, rework, printing and full history remain available through **Full traveler / history / rework** in the standard view. This slice does not replace sign-in with a custom PIN keypad. It requires testing with the actual tablet, gloves, stylus and scanner; larger targets do not guarantee glove detection.


## Stations, assignments and TV boards

1. An organization admin opens **Admin → Stations** to create named stations (for example Laser 1 or Assembly). Stations can be renamed or deactivated with a reason. A station referenced by an open, noncancelled ticket cannot be deactivated until the work is completed/cancelled or its unfinished operations reassigned. Completed assignments stay in history.
2. The planner creates a ticket, opens **Assign stations / operators & release**, assigns an active station to every route operation, and optionally chooses an active operator. Save each operation, then **Release ticket** with a reason. New tickets are planning-only until release; direct batch/unit completion requests are rejected too.
3. Assigned operator is scheduling guidance. Any authorized operator in the organization can cover. Each original-route completion atomically stores an immutable station-name/operator snapshot, the actual user and time, alongside its production record and event. Assignment changes are versioned and audited, invalidating stale operator confirmations. For serial work, an operation can be reassigned for remaining units; completed units retain their snapshots. Original production contributors remain disqualified from inspecting their own work.
4. Open **TV boards** to choose the whole-shop queue or a station board. Station rows represent a ticket’s routing operation, with unit counts ready now, upcoming, awaiting final inspection, held or not released. A serial ticket can appear in several station queues at once. Final-inspection waits/holds are attributed to the last production station; this is queue attribution, not a claim about physical material location. Rework uses the existing documented repair workflow, not a separately assigned station route.
5. On a shared TV, use a dedicated account with only the **display** role. Admin → User management can create one. Display accounts land on `/tv`, can read only their organization’s TV views/data, and cannot access travelers, documents, administration or production actions. Do not combine display with other roles. Developer and local-admin roles do not automatically turn into display accounts.

TV pages refresh every 15 seconds, rotate pages every 12 seconds, and fit their page size to screen height. HOT order is preserved; held work remains in a separate panel. Completed/cancelled tickets leave the queue. The last successful update remains visible; failed, offline or expired-session refreshes show a prominent NOT LIVE/STALE warning. JavaScript is required. Browser full-screen mode is available, but kiosk startup, physical TV provisioning and network deployment are not configured by this feature.

Back up SQLite and run `init-db` before restart. Existing tickets remain executable under their original workflow and appear as unassigned where no station is known; no historical assignment is invented. Assign remaining operations to include them on station TVs. Only newly created tickets require the explicit station-assignment/release step. Existing workflow fixtures cover this legacy compatibility; dispatch tests use real assignment/release endpoints for new jobs.

## Organization branding

Local organization admins open **Admin → Organization management → Branding** to set an optional display name, primary/accent colors and PNG/JPEG logo. **Preview changes** renders an unsaved preview, including app header and TV/print identity samples; **Save branding** applies it, and **Reset to defaults** removes the logo/custom display name and restores the default colors. Saving/resetting requires a reason, checks the current version and records before/after values and logo hashes in Security history. The organization profile name and sign-in code are unchanged; an empty display name follows the profile name.

Logos are limited to 2 MB and 2048 × 2048 pixels, decoded as still PNG/JPEG, resized within 800 × 400 and normalized to PNG without original metadata. SVG and animated images are not accepted. Stored logos are tenant-authenticated, including read-only display access, and included in SQLite backups. The preview is not persisted; its normalized logo travels in the admin form and is validated again on save. Black/white foreground is selected automatically for brand backgrounds. HOT, held, stale-state colors and barcode rendering remain fixed.

Branding appears on authenticated app/Shop Floor headers, station/whole-shop TV headers, and batch/serial printed traveler identities. Reload open pages after saving; existing paper does not change. Print identities use black text on white and a bounded logo, leaving the barcode separate. The shared login and separate developer portal remain generic; organization-specific public login URLs and configurable layouts are not part of this slice. Drawing PDFs and DXFs are never restyled. Physical printer/logo/barcode readability still requires shop testing.

Before upgrading, install `requirements.txt` (adds pinned Pillow), stop the app, back up SQLite and run `init-db`; restart afterward. This adds the organization branding table without modifying existing production records. Keep image decoding dependencies maintained; the implementation follows [Pillow's image-loading guidance](https://pillow.readthedocs.io/en/stable/_modules/PIL/Image.html).

## Document storage and retention previews

Organization admins open **Admin → Storage & retention** to see uploaded PDF/DXF content bytes, files without matching jobs, external link counts, duplicate-byte opportunities and retention eligibility. Choose separate PDF and DXF defaults: indefinitely, 365/1095/2555 days, or a custom 1–36500 days. A configurable 7–365 day grace period follows expiry (default 30). Existing files default to indefinite retention. Preview does not save; saving requires a reason and a current version, with atomic before/after security-history entries. Per-revision PDF and DXF permanent-keep controls have their own audited versions.

This release **does not delete files**. It also does not physically deduplicate files, quarantine them, recover deleted objects or run a purge schedule. The future purge worker must recheck eligibility at deletion time, retain metadata/audit evidence, and implement a recoverable grace workflow before it is enabled. The preview's duplicate estimate compares checksum/type/length within one organization; it is not reclaimed or billable storage. Totals exclude logos, database overhead and backups.

Active or held matching jobs protect documents, including exact PN/revision matches even if an old ticket lacks a drawing association. Cancelled jobs are closed unless still marked held (conservatively protected). Accepted jobs require final acceptance evidence; nonaccepted serial units prevent treating an accepted parent as safely closed. Retention starts at the later of the file's upload or latest matching job closure. Unused files age from upload. Missing or invalid timestamps/closure evidence require review. New matching jobs protect the files again. Durations are elapsed UTC days, not regulatory defaults or calendar-year anniversaries. External URL destinations and backup retention remain separate.

Back up the database and run `init-db` before restarting to add policy/control tables. No file bytes, production records or existing immutable-file protections are altered. This first slice operates on current SQLite storage; migrating it to Supabase Storage and implementing tenant-scoped deduplication/purging remains future work.

### Bulk parts-library onboarding

Planners and organization admins can open **Parts & drawings → Import parts & drawings** (also linked from Admin). Download the UTF-8 CSV template with exact headers `pn,revision,description,drawing_file,dxf_file`, then select the mapped PDF/DXF files together. Filenames match exactly; no filename guessing, directory/ZIP imports or external-link imports are performed. Each new revision needs a PDF; DXF is optional. For an existing revision, a blank description means retain its current description, and a DXF-only row can add its first DXF.

Preview validates the entire batch before catalog changes. Duplicate PN/revision rows, missing/unmapped files, different existing descriptions and differing immutable file content block the batch; correct and re-upload. Identical files are skipped. Confirmation rechecks the library under a write lock and commits catalog files, per-file history and the batch audit together. A changed library requires a fresh preview. Repeated confirmation is idempotent; re-uploading an already imported batch skips identical files. Existing tickets are not automatically retargeted.

Limits: 200 rows, 256 KB CSV, 50 MB total file content, 10 MB per PDF and 20 MB per DXF (51 MB multipart request limit). Split large libraries into batches. PDF validation checks the header; DXF validation checks extension/nonempty content, not geometry or machine suitability. Optional permanent keep applies only to newly added files, with audit events; otherwise organization retention defaults apply. Existing file protection is preserved.

Previews are private to the uploading user and organization, expire after one hour, and only one pending preview per person/organization is retained. Confirmation releases staged upload bytes; cancellation removes the preview. Expired previews are removed on the next valid preview for that organization. Run `.venv/bin/python -m flask --app productionrunner clear-expired-imports` regularly to clean abandoned previews even when no subsequent import occurs (schedule this during deployment; no scheduler is installed automatically). This removes temporary staging records only, not catalog files or immutable audit history. SQLite backups may contain staged content until their separate backup retention expires. Back up the database and run `init-db` before restarting an existing installation for the additive `part_imports` table.
