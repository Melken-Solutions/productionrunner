# PostgreSQL migration and recovery

Status: PostgreSQL support is implemented and verified against isolated PostgreSQL 17. The dedicated **ProductionRunner Staging** Supabase project (`imhplsizgijerkqemxjy`, Melken Solutions, `us-east-1`) has been created and its initial schema applied. No Render web service is deployed yet. SQLite remains the local installation's default; no customer data has been imported into staging.

Staging verification: 74 application/migration tables, 86 workflow/audit triggers, three checksummed application migrations, and zero tickets. Neither `anon` nor `authenticated` has schema access; the runtime role cannot create schema objects. Supabase's security advisor reported no findings. Its performance advisor reported missing foreign-key indexes that require review before load acceptance, plus unused-index notices expected for an empty database; do not remove those indexes based on empty-project statistics.

Render CLI is connected to **Melken TechWork** (`tea-dbg7l1ifngtc738uct0g`); the Render connector still points to a different personal workspace. The Supabase connector and CLI now both access the correct Melken Solutions organization. The local checkout is linked to ProductionRunner Staging (`imhplsizgijerkqemxjy`); CLI connection metadata under `supabase/.temp/` is ignored by Git. Never infer account selection from another connector's login.

## Connections and privileges

Set `PR_DATABASE_URL` only for a PostgreSQL installation. Use the provider's verified connection details, TLS certificate validation (`sslmode=verify-full` plus the appropriate root CA), and session pooler when direct IPv6 is unavailable. Never commit the URL or print it in logs. The app disables prepared statements and uses short request-scoped connections; size Render concurrency to the database pool budget.

Use a migration/backup identity for schema changes, imports and dumps. Provision a separate login inheriting `productionrunner_runtime` for the web app. That role has application DML and sequence access, but does not own tables and cannot alter schema, disable audit triggers, or change migration history. Do not deploy the web app with the Supabase `postgres` owner credential. ProductionRunner's tables are in the private `productionrunner` schema; do not add it to Supabase's exposed Data API schemas. Tenant authorization remains enforced by the application and composite foreign keys; this is not a claim that per-tenant PostgreSQL RLS has been implemented.

## Versioned schema

Run `python -m flask --app productionrunner init-db` with the migration identity's `PR_DATABASE_URL`. PostgreSQL applies ordered files in `supabase/migrations/` transactionally, recording their SHA-256 hashes. A changed previously applied migration fails closed; create a new migration instead. Never run migrations automatically on each web request. Schema files preserve the original immutable history and closed-request triggers.

The PostgreSQL bridge retains serialized application writes using one transaction advisory lock, preserving the original SQLite concurrency guarantees across web workers. Reads remain concurrent. This is deliberately conservative for the pilot and is a throughput limit: measure load before increasing shop count or replacing it with narrower locks. Any direct writer must honor the same lock; restrict write credentials accordingly.

## Offline import

1. Stop source writers and make a verified, private SQLite backup with `backup-db`.
2. Upgrade a COPY of the source snapshot to the current SQLite schema, if needed.
3. Apply PostgreSQL migrations to a dedicated empty destination.
4. Using the migration identity, run `python -m flask --app productionrunner import-sqlite /absolute/path/snapshot.sqlite3 --confirm-empty`.
5. The importer checks source integrity, exact table coverage, destination emptiness, all row contents (including file bytes), and resets generated ID sequences. Existing destination data causes refusal. All inserts and trigger restoration occur in one transaction; a failure rolls back the import. User audit triggers are temporarily disabled only inside that trusted offline transaction; foreign keys remain enforced.
6. Switch to the runtime identity, verify readiness, sign-in, tenant boundaries, drawings/DXF, production, independent inspection and departure before allowing writes.

Never point the test suite at a customer database. `PR_TEST_POSTGRES_URL` explicitly permits only a localhost database whose name ends in `_test`; its fixtures DROP the application schema. `PR_MIGRATION_TEST_URL` is a separate disposable local database for import rehearsals.

## Backup and restore rehearsal

Use `pg_dump -Fc` through the migration/backup identity and a direct connection, with a PostgreSQL client compatible with the server. Keep credentials in private environment/secret storage, not command arguments or a checked-in file. Write backups with mode 0600; encrypt off-host backups and restrict access. Restore with `pg_restore --exit-on-error` into a separate empty database. Compare row fingerprints and file hashes, verify triggers and runtime access, and exercise a complete job before calling the restore successful.

A local synthetic rehearsal restored all 74 application/migration tables and verified identical row content, including embedded files and audit records. This does not configure Supabase backup schedules, off-host storage, alerts or a customer recovery objective. Once files move to private object storage, database backups alone will no longer constitute a complete recovery set; back up and verify object versions/manifests separately.

## Remaining cloud gates

- Transfer the verified runtime connection to Render secret configuration; project, schema, restricted login and private bucket provisioning are complete.
- Verified object backup/restore and safe orphan reconciliation; private PDF/DXF routes are integrated, while retention remains preview-only.
- Public HTTPS origin, email delivery, account recovery and privileged MFA.
- Provider backup policies, off-host file backups, restore drill on hosted staging, uptime/error alerts and incident owner.
- Render staging configuration and reviewed source push; no merge without review.
- Full hosted acceptance test, a second synthetic organization, and concurrent TV/station load.

PN routing automation remains a product enhancement; it is not required to turn a verified staging deployment on.

## Staging connection checkpoint

The `productionrunner_app` login inherits `productionrunner_runtime`, has no elevated role attributes and is limited to 12 connections. A connection through the Supabase session pooler on port 5432 passed `sslmode=verify-full` with the official CA in `deploy/supabase-ca.crt`. Its application-schema CREATE privilege is false. The local generated staging credentials are in ignored `instance/staging-secrets.json` with mode 0600; never commit, print, or send this file in chat. Render's runtime URL must use `sslrootcert=deploy/supabase-ca.crt`, not the local absolute Mac path. The public CA is obtained from the URL in Supabase's official dashboard configuration: https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt. Track CA expiry/rotation as part of maintenance.

The `productionrunner-files` bucket is private, capped at 20 MiB per object, with PDF, binary DXF and normalized PNG MIME types allowed. A disposable probe passed authenticated upload/download with identical bytes, while public and unauthenticated reads were denied; the probe was then deleted. No browser/client grants were added. The server-only secret key bypasses Storage RLS: application tenant authorization must precede every object operation. PDF/DXF single uploads and bulk confirmations now use private storage when `PR_FILE_STORAGE=supabase`. Existing embedded files remain readable. Logos and temporary bulk-preview payloads remain in the database. Retention deletion and a full object-storage recovery drill are not implemented.

`render.yaml` describes a single Virginia Python web service using the existing Waitress entry point, four request threads, manual deploys and database readiness checks. The free compute plan is for staging configuration only; validate capacity and choose an always-on plan before shop-floor use. This file has not been synced or deployed. Source review/push, secret transfer, canonical host/origin configuration and full hosted acceptance remain required. Never run migrations at web startup with the runtime identity.

## Private file behavior and recovery

Enable `PR_FILE_STORAGE=supabase` with `PR_SUPABASE_URL`, `PR_SUPABASE_SECRET_KEY` and `PR_STORAGE_BUCKET` only on PostgreSQL installations with the third migration applied. SQLite continues storing bytes; `init-db` adds nullable metadata columns to older local databases. No existing PDF/DXF is automatically moved or deleted. Uploaded PDF/DXF bytes are absent from new cloud catalog rows; immutable object keys, sizes and SHA-256 hashes remain in the database.

Keys use organization ID plus random IDs; original filenames are never object paths. Tenant authorization precedes reads, and planner/admin permissions are rechecked before committing uploaded metadata. Storage redirects are rejected, requests have bounded timeouts and response sizes, and downloads must match the recorded byte count and hash. Storage failures return a retryable error without publishing incomplete catalog records. External drawing links retain their existing behavior.

Bulk previews retain their bounded, temporary base64 payload in the database until confirmation/cancellation/expiry cleanup. Confirmation uploads before taking the database write lock, then checks the current preview, library and actor again before atomic catalog publication. Repeated completed confirmations do not re-upload. Concurrent/failed confirmations can leave **unreferenced private objects**. Do not enable a bucket-wide expiry policy or delete those objects blindly: a future reconciler must compare every immutable PDF/DXF reference, allow an upload grace period, and resolve ambiguous commits before deletion. Shared objects may be referenced by multiple rows in one batch. No automatic orphan cleanup or retention purge runs in this implementation.

Database-only backups no longer include new cloud PDF/DXF bytes. Recovery must include a verified object copy and metadata manifest. Never purge active-job, master-keep or history-protected files. The current retention UI remains a projection only.

Live integration verification uses a disposable local PostgreSQL database and the real staging bucket, not customer records: set `PR_TEST_POSTGRES_URL` to a localhost database ending `_test` and `PR_LIVE_STORAGE_CONFIG` to the private staging JSON, then run `tests/test_object_storage.py::test_live_staging_storage_routes`. The test cleans up only its generated keys. Regular suites use an in-memory storage double and require no credentials.

## Render startup and credential transfer

The Render entry point is `python -m productionrunner.render_serve`. It accepts an optional `PR_SECRET_FILE` path to a JSON file with only the database URL, session secret and storage settings. It never prints those values. Explicit environment variables take precedence. Without explicit host/origin configuration it uses the validated `RENDER_EXTERNAL_HOSTNAME` supplied by Render; custom domains require explicit trusted hosts and canonical HTTPS origin.

For the CLI-created staging service, supply a private `productionrunner.json` secret file and `PR_SECRET_FILE=/etc/secrets/productionrunner.json`; use a database URL with `sslrootcert=deploy/supabase-ca.crt`. The credentials must not contain a developer-machine path. Nonsecret environment settings are `PR_ENV=production`, `PR_HTTPS=1`, `PR_BIND_HOST=0.0.0.0`. No database migrations run on startup. `render.yaml` remains the reproducible configuration reference; a later Blueprint adoption must reconcile the existing secret-file configuration rather than creating a second service or replacing secrets with empty values.

Deploy the feature branch for staging; do not merge the draft PR without review. Keep automatic deploys disabled until hosted acceptance and the production recovery/authentication gates are complete.
