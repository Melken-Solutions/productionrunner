# Cloud subscription product — first customer pilot

## Product ownership and deployment direction

**Melken Solutions develops and operates ProductionRunner. Customer shops subscribe to it.** A customer organization is the boundary for manufacturing jobs, users, stations, drawings/DXFs, branding and history. Melken's platform developer account is separate from a customer's organization administrator and production identities. Melken is not automatically the first manufacturing customer.

Cloud hosting is the primary direction. The first physical pilot layout remains two lasers, Packing and a whole-shop display: four mini PCs/TVs, three desk monitors and a shared shop printer. A customer does **not** need a dedicated local application server for this hosted arrangement. The [local server runbook](server-installation.md) remains an alternative, not a prerequisite. Keep existing laser-controller computers for machine-specific software. Remote planners/admins use the same HTTPS service; printing still occurs through their local browser/printer unless a separate shop-print feature is developed.

The existing local tenant named `melken` is historical development/test data. Do not rename it, delete it or turn it into a customer without an explicit data decision. Provision a fresh, correctly named customer organization for the pilot. The developer account must never be used to record production work.

## Current implementation versus launch readiness

Implemented hosting foundations:

- `PR_DATA_DIR` selects an absolute persistent directory for the SQLite database and local session-secret file. With `PR_SECRET_KEY` supplied, no unused fallback secret file is generated. Existing development installs still use `instance/` by default.
- `PR_ENV=production` requires explicit persistent directory configuration, secret-manager key of at least 32 characters, `PR_HTTPS=1` and exact `PR_TRUSTED_HOSTS`; it rejects debug/testing startup. Generate a cryptographically random secret; length checking alone does not prove randomness. These checks cannot prove that a platform volume is actually persistent or that HTTPS is correctly configured; verify both operationally.
- `GET /health/ready` reads the database without creating a missing file, checks required tables and selected columns, and returns generic `ready`/`not_ready` JSON with 200/503. It sets no session cookie and exposes no tenant records. It is a connectivity/schema probe, not a complete integrity, writeability, backup or load check. The probe's Host must be allowlisted too.
- `python -m flask --app productionrunner backup-db NEW_PATH` uses SQLite's consistent backup API, refuses to overwrite, checks integrity/foreign keys and creates a private snapshot (0600 on POSIX). Verify Windows ACLs separately. Upload that snapshot to an encrypted off-host backup destination. The command does not schedule backups, upload them, back up secrets, or establish a retention policy.
- `python -m productionrunner.serve` starts Waitress with the current factory in production mode; it never initializes or migrates the database automatically. Install `requirements-hosting.txt` first.

Customer data isolation, role permissions, independent inspection and audit guards already have local regression tests. This is not a claim that the application is ready for unrestricted public registration or production SaaS use.

## Proposed initial hosting approach (not purchased or selected)

Use a managed web service with HTTPS ingress, persistent storage, secret configuration, logs, health monitoring and controlled deployment. Keep staging separate from the customer service. Begin with one application instance and a small invited pilot; this matches the present SQLite implementation and avoids a database rewrite in the same step as the first hosting trial.

Melken has now selected Render for application hosting and has created an account; Supabase is the intended database/file platform. No ProductionRunner service or project has been deployed by this repository change. The following disk constraints describe the earlier SQLite pilot option; the Supabase migration remains required for the intended architecture. Its persistent disks are attached to paid services, are available at runtime rather than build/pre-deploy time, restrict a service to one instance and require brief downtime during deployment. Ephemeral/free filesystem storage is unsuitable for this database. Other providers need equivalent explicit persistence checks. See [Render persistent disk documentation](https://render.com/docs/disks).

The longer-term shared subscription service needs a separate decision about managed database and document storage, availability targets, tenant quotas, retention and scaling. SQLite on one volume is an initial pilot constraint, not the final scaling design. Do not place a shared SQLite file on network storage or scale replicas against it. No Postgres/object-storage migration has been implemented by this change.

## Pilot deployment procedure

1. Select hosting provider/region and the business domain with Melken. Record budget, customer data location requirements, support owner and the first customer's organization details. No account purchase, external upload of existing shop records or DNS change is authorized just by this document.
2. Create a private staging environment first. Use synthetic records only until access and recovery checks pass. Connect a reviewed commit; leave automatic deployment from a moving development branch disabled. Do not merge the draft PR as part of setup.
3. Install Python 3.12 or 3.14 and run `python -m pip install -r requirements-hosting.txt` in the service environment. Preserve an installation manifest of exact resolved versions. The older `requirements-lock.txt` predates branding and is not a current hosting lockfile; it must not replace these requirements.
4. Attach a persistent local disk and configure the variables in [cloud.env.example](../deploy/cloud.env.example). Use a new random secret in the host's secret manager, never a value copied from source control. Set the exact service/custom hostnames. Add an internal probe hostname only if the provider actually uses it. `PR_BIND_HOST=0.0.0.0` is appropriate only inside managed ingress isolation; for a VM with a local proxy, retain `127.0.0.1`.
5. In the runtime environment that actually mounts that disk, run `python -m flask --app productionrunner init-db`. For updates, stop all writers and make/verify a backup before running schema initialization. A provider build hook cannot initialize a disk it cannot mount. If a platform cannot supply a runtime shell until startup, use a temporary non-serving maintenance process to perform this controlled step; never disable readiness/authentication or automate schema changes on every boot to work around it.
6. Provision a Melken developer identity using `add-developer`, then use the developer portal or trusted host CLI to create the customer's organization and its initial local admin. Use private password prompts; no credentials in build logs or shell arguments. Create the customer's individual planner/operator/inspector accounts and exclusive display identities. Do not seed shared production users or copy development credentials.
7. Start with `python -m productionrunner.serve`; configure `/health/ready` as the readiness probe. Confirm startup fails for missing production settings. Check the platform sends an allowlisted Host and terminates HTTPS before reaching the application. The app does not enable generic forwarded-header trust.
8. Complete the access gate below before giving customer users remote access. Verify HTTPS, Secure/HttpOnly/SameSite cookies, organization boundaries and safe sign-in/out on real desktop/TV browser profiles.
9. Take a consistent snapshot with `backup-db` to a new path, transfer it off the app's volume and restore it into an isolated environment using the matching release/configuration. Record restore time, recovered job/document counts and the acceptable data-loss window. Schedule and monitor this procedure before customer production.
10. Configure the shop devices using the [workstation runbook](mini-pc-installation.md), replacing its local-server address with the hosted URL. Linux clients are also an option, but their printer/display setup still needs a dedicated tested runbook. Finish physical print/USB/scan and a realistic shift trial.

## Access and reliability gates before customer production

| Area | Required work / acceptance |
|---|---|
| Remote authentication | MFA for Melken developer and customer-admin access; secure account recovery and credential lifecycle. Native MFA is not implemented. A managed identity/access gateway can be evaluated, but its policies must protect all relevant routes without breaking display sessions. |
| Operator PINs | Do not treat existing short PIN login as Internet-ready identity assurance. Decide whether to restrict PIN access to managed station access or require stronger remote authentication. Apply the policy on the server/gateway, not merely by hiding controls. |
| Proxy trust and throttling | Current application ignores forwarded IPs; a proxy can make customers share the source-failure bucket. Design/test a provider-specific trusted proxy chain and abuse controls before public use; never trust arbitrary forwarded headers. |
| Customer isolation | Test two synthetic customers across every read/write/download/admin path and authentication transitions; verify that developer access does not grant manufacturing execution. |
| Recovery | Off-host encrypted backups, failure alerts, documented restore/rollback and a successful rehearsal. Back up secrets/deployment configuration separately from the database. |
| Operations | TLS renewal, error/uptime alerts, disk usage, upload/storage limits, reviewed release process and a named incident owner. Check load with four TV feeds and concurrent station/office use. |
| Outage behavior | No offline writes exist. Decide backup internet and a written outage/reconciliation procedure; old printouts/TV snapshots must not be mistaken for current job authorization. |
| Customer onboarding | Correct organization, initial admin, station/display provisioning and branding, with an offboarding/export/retention policy. No automated customer signup or deletion is supplied. |
| Subscriptions | Choose billing/entitlement design after pilot validation; no payment collection, plans, quotas, trial expiry or automated subscription suspension is implemented. |

Organization-specific login URLs can later remove repetitive organization entry while preserving tenant scope. The current shared login still requires the organization code. Do not silently default remote users to whichever tenant happens to exist.

## Update and rollback

Pause production writers, verify a fresh backup, record the deployed revision and dependencies, deploy a reviewed revision, run required migrations explicitly, check readiness and smoke-test authentication, work progression and document access. Reopen production only after the checks pass. If rollback is needed, restore the compatible code/database/configuration together and reconcile any work since that snapshot. Keep the failed state and backup for investigation.

[Flask trusted-host configuration](https://flask.palletsprojects.com/en/stable/config/#TRUSTED_HOSTS) and [Waitress deployment guidance](https://flask.palletsprojects.com/en/stable/deploying/waitress/) underpin the provider-neutral configuration. Actual deployment, domain, billing and customer access remain pending decisions and verification.
