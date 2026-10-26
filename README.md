# ProductionRunner

A runnable, **provisional** manufacturing workflow MVP: one part number per ticket, a physical JobRunner barcode traveler that stays with the material, organization-scoped, role-controlled production operations, and independent final inspection.

**Specification status:** PR v1.9 was referenced but its contents were unavailable through the linked chat. This implementation is based on the current request and the visible traveler/hybrid-tracking decisions. It is not a claim of PR v1.9 compliance. See [requirements and open decisions](docs/requirements.md) before approving the draft PR.

## Run locally

Requires Python 3.12 or newer. No external database or cloud account is needed.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m flask --app productionrunner init-db
python -m flask --app productionrunner add-tenant melken --name "Melken Solutions"
python -m flask --app productionrunner add-user planner --tenant melken --roles planner
python -m flask --app productionrunner add-user operator --tenant melken --roles operator
python -m flask --app productionrunner add-user inspector --tenant melken --roles inspector
python -m flask --app productionrunner run --host 127.0.0.1 --port 5000
```

Each `add-user` command prompts for a password (12+ characters). There are no seeded accounts or default passwords. Open <http://127.0.0.1:5000>. Enter organization `melken` when signing in. Use separate browser profiles or sign out between roles or organizations. `init-db` preserves existing records.

SQLite data and the generated session signing secret live in ignored `instance/`. Keep both private. The built-in server is for local evaluation; do not expose it as a production service.

## Upgrade an existing single-tenant database

Stop the application and all writers. Keep the database and session secret private. `init-db` refuses to silently upgrade the original schema. Explicitly assign **all existing users and records** to the organization that owns them:

```sh
python -m flask --app productionrunner migrate-tenancy --tenant melken --name "Melken Solutions" --backup instance/pre-tenancy.sqlite3
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
python -m flask --app productionrunner set-pin operator --tenant melken --reason "Initial station enrollment"
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

Batch tracking is the default. Serial mode requires one unique serial per unit **within the ticket**, but all units still move together. Split lots, individual serial routes, rework and hold release are not implemented.

## Verify

```sh
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover PIN workflow/inspection, reset/disable, hashing, concurrent and persistent throttling, security-history access, additive upgrades, two-organization isolation, scoped roles, membership revocation, migration/backup/rollback, the end-to-end workflow, permissions, self-inspection denial, route order, fail/hold behavior, stale and concurrent submissions, validation rollback, CSRF, output escaping, persistence, barcode output, and audit immutability. GitHub Actions runs the suite on Python 3.12 and 3.14.

## Documentation

- [Product and software architecture](docs/architecture.md)
- [Hardware and station design](docs/hardware.md)
- [Requirements, assumptions and PR v1.9 reconciliation](docs/requirements.md)
- [Repository inspection and delivery record](docs/repository-inspection.md)

This is a local evaluation slice. Before shop-floor rollout, reconcile the specification, validate physical printers/scanners, harden authentication and hosting, establish backup/restore, and approve the manufacturing workflow.
