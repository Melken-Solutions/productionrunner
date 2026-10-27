# ProductionRunner server installation and recovery

**Cloud direction update:** Melken is the software provider and customer shops subscribe. See [cloud pilot](cloud-pilot.md). A hosted shop needs no dedicated local app server. Local-server instructions below are an alternative; workstation instructions apply to either hosted or local HTTPS addresses. Existing `melken` development data is not automatically a customer.

Status: planning/installation runbook. **Server OS is not yet selected.** Windows and Ubuntu paths below are alternatives, not instructions to install both. Windows 11 mini PC instructions are in [mini-pc-installation.md](mini-pc-installation.md); equipment counts and the physical pilot are in [hardware.md](hardware.md).

The app is currently a locally tested MVP on a draft branch. The commands/configuration below have been checked against the app's factory and CLI, but a Windows/Ubuntu shop server, TLS service, startup recovery and real network load have not yet been qualified. Complete the acceptance checks before authorizing shop use. Do not merge the draft PR merely to deploy a test.

## 1. Installation decisions and record

Complete this record with the installer before executing the selected path:

| Item | Decision to record |
|---|---|
| OS and support owner | Supported Windows edition or Ubuntu LTS; exact version and person responsible |
| Release | Reviewed Git commit/tag; current development branch is `feat/provisional-jobrunner-mvp` |
| Server name / address | Reserved LAN address and stable DNS hostname |
| Browser URL | Final `https://…` URL, identical at all workstations |
| HTTPS trust | Existing company certificate/CA or managed local CA; renewal and client trust owner |
| Data initialization | Fresh pilot organization, or explicitly approved migration of existing test records |
| Runtime identity | Dedicated non-administrator OS account with access only to required files |
| Backups | Separate destination, schedule, retention, failure notification and restore-test owner |
| Recovery objectives | Maximum acceptable lost work and outage duration; choose backup frequency accordingly |
| Power | UPS, safe shutdown and automatic restart configuration |

Illustrative paths are `C:\ProductionRunner\app` on Windows and `/opt/productionrunner/app` on Ubuntu. Both keep the database at `app/instance/productionrunner.sqlite3`. Replace example paths/hostnames consistently. Keep SQLite on the server's local disk; do not run it on SMB, OneDrive, Dropbox or another synchronized/network folder.

## 2. Target arrangement

```text
Windows station/office browsers
             |
       trusted HTTPS :443
             |
      Caddy reverse proxy
             |
       HTTP 127.0.0.1:8080
             |
     Waitress -> Flask app
             |
        local SQLite + instance/session-secret
```

Use one application process for this initial pilot. Waitress is a proposed cross-platform WSGI host; Flask documents its Windows support and app-factory invocation. It is an additional deployment dependency, not currently in the app requirements. Use a reviewed pinned version and record it (examples use `waitress==3.0.2`). Do not use `flask run` as the network service. See [Flask's Waitress guide](https://flask.palletsprojects.com/en/stable/deploying/waitress/) and [package releases](https://pypi.org/project/waitress/).

SQLite serializes writes. This guide does not establish production capacity; qualify four TV feeds plus the three interactive stations and office workload before rollout. The current app ignores forwarded client-IP headers, so users behind this proxy share the source-address login-failure bucket (50 failures per 15 minutes). The per-account limit also applies. Do not enable arbitrary proxy trust or remove throttling as an installation workaround; resolve this limitation in an application change before wider deployment if the pilot requires it.

## 3. Prepare the host and obtain a release

Install OS updates, enable correct time synchronization, configure the LAN reservation/DNS, UPS software and a separate administrator account. Create the restricted runtime account. Give the deployment administrator control of application code and the runtime account read/execute access to code plus write access to `instance/` and its logs only. Windows uses NTFS ACLs; Ubuntu uses ownership/mode permissions. Backups contain manufacturing documents, credential hashes and audit records and need restricted access too.

Install a supported Python 3.12 or 3.14 interpreter and Git from their official sources. The app's CI targets those Python versions. Obtain the private repository using the authorized installer's account; never place a GitHub token in a URL, runbook or station shortcut. A reviewed archive can be transferred instead of installing Git credentials on the server.

Ubuntu example (after the administrator creates a writable deployment parent):

```sh
sudo apt-get update
sudo apt-get install python3 python3-venv git
python3 --version
cd /opt/productionrunner
git clone https://github.com/Melken-Solutions/productionrunner.git app
cd app
git checkout --detach REVIEWED_COMMIT_SHA
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install waitress==3.0.2
.venv/bin/python -m pip check
```

Replace `REVIEWED_COMMIT_SHA` before running it. Check the distribution's installed Python version rather than assuming the `python3` package is suitable. Use the deployment account for clone/install; `sudo` above is only for OS packages.

Windows PowerShell example (after installing Python and Git, and creating the deployment directory):

```powershell
Set-Location C:\ProductionRunner
git clone https://github.com/Melken-Solutions/productionrunner.git app
Set-Location C:\ProductionRunner\app
git checkout --detach REVIEWED_COMMIT_SHA
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install waitress==3.0.2
.\.venv\Scripts\python.exe -m pip check
```

These examples call the virtual-environment executable directly, so no shell activation or PowerShell execution-policy change is needed. Check each command succeeds before continuing. Record `git rev-parse HEAD`, Python version and `python -m pip freeze` output in the installation record, using the selected virtual-environment Python. Keep credential values out of that record.

## 4. Initialize data and identities

Choose exactly one path:

- **Fresh installation:** initialize a new database and provision the organization/accounts below. Do not copy the developer's test credentials or synthetic demo records into a new shop installation.
- **Approved existing data:** stop the source app and all other writers, take a consistent backup, transfer the complete required `instance/` state securely, set destination permissions, and run the additive `init-db` with the matching reviewed release. Keep the source offline during cutover to avoid two divergent active databases. Do not rerun `add-tenant`/`add-user` for identities already present. For the original single-tenant schema only, follow the explicit migration procedure in [README](../README.md#upgrade-an-existing-single-tenant-database); do not guess ownership.

Fresh Ubuntu commands, from `/opt/productionrunner/app`:

```sh
umask 077
.venv/bin/python -m flask --app productionrunner init-db
.venv/bin/python -m flask --app productionrunner add-tenant melken --name 'Melken Solutions'
.venv/bin/python -m flask --app productionrunner add-user local-admin --tenant melken --roles admin
.venv/bin/python -m flask --app productionrunner add-user tv-laser-1 --tenant melken --roles display
.venv/bin/python -m flask --app productionrunner add-user tv-laser-2 --tenant melken --roles display
.venv/bin/python -m flask --app productionrunner add-user tv-packing --tenant melken --roles display
.venv/bin/python -m flask --app productionrunner add-user tv-shop --tenant melken --roles display
```

On Windows, use `.\.venv\Scripts\python.exe` in place of `.venv/bin/python`; omit `umask` and set equivalent NTFS ACLs on `instance/` before proceeding. Passwords are requested at hidden prompts; there are no default credentials. Replace example organization/name values if installing for another customer.

The local admin can create each person's planner/operator/inspector roles in User management after HTTPS is working. An office user who needs both planning and administration needs both roles. Optional individual PINs are enrolled through Admin; no shared production PIN. Display accounts must have only `display`.

If developer administration is required, a trusted host administrator separately runs `python -m flask --app productionrunner add-developer NAME --reason 'Initial platform provisioning'` using the venv Python. This creates a separate platform identity; local organization admins cannot grant it.

The app creates `instance/session-secret`. Preserve its permissions and persistence. The examples use this file and leave `PR_SECRET_KEY` unset; an environment override is optional and must be managed consistently for service, backup and restore. When an environment key is supplied, current versions do not generate an unused fallback secret file. Do not regenerate keys at each boot. `PR_HTTPS=1` must be present in the serving process behind HTTPS; it enables Secure cookies. Never use Flask debug mode in this configuration.

## 5. Start the application on loopback

First verify the app starts interactively under its runtime identity, then stop this foreground instance before enabling automatic startup.

Ubuntu:

```sh
cd /opt/productionrunner/app
PR_HTTPS=1 .venv/bin/waitress-serve --host=127.0.0.1 --port=8080 --threads=4 --call productionrunner:create_app
```

Windows PowerShell:

```powershell
Set-Location C:\ProductionRunner\app
$env:PR_HTTPS = '1'
.\.venv\Scripts\waitress-serve.exe --host=127.0.0.1 --port=8080 --threads=4 --call productionrunner:create_app
```

A host-local request to `http://127.0.0.1:8080/login` should return the login page. Do not test authenticated sessions over that HTTP URL with Secure cookies enabled; use the HTTPS proxy for sign-in. Port 8080 must remain loopback-only and inaccessible from station PCs.

### Ubuntu automatic startup

Create an OS account named `productionrunner` if that is the chosen runtime identity, ensure it can traverse/read the checkout and virtual environment, and grant it write access to `instance/`. Save the following as `/etc/systemd/system/productionrunner.service` with administrator privileges:

```ini
[Unit]
Description=ProductionRunner application
After=network.target

[Service]
Type=simple
User=productionrunner
Group=productionrunner
WorkingDirectory=/opt/productionrunner/app
Environment=PR_HTTPS=1
ExecStart=/opt/productionrunner/app/.venv/bin/waitress-serve --host=127.0.0.1 --port=8080 --threads=4 --call productionrunner:create_app
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Then:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now productionrunner
sudo systemctl status productionrunner
sudo journalctl -u productionrunner -n 50 --no-pager
```

Run CLI maintenance as the runtime identity or restore the correct ownership afterward; do not leave a root-owned database/journal preventing writes. This sample unit is a starting configuration, not a complete host-hardening policy.

### Windows automatic startup

Use the shop's approved service manager, or configure a pilot Task Scheduler task that launches at **system startup**, not only after a user signs in. One concrete Task Scheduler setup is:

1. Create `C:\ProductionRunner\start-app.cmd` with the content below and restrict editing to administrators.
2. Create a task named `ProductionRunner App` under the restricted runtime identity; select **Run whether user is logged on or not**. Supply its OS credential through the Windows dialog, never in the script. Grant batch-logon rights through IT policy if required.
3. Trigger: **At startup**, optionally delayed 30 seconds. Action: `C:\Windows\System32\cmd.exe`, arguments `/d /c C:\ProductionRunner\start-app.cmd`, Start in `C:\ProductionRunner\app`.
4. Configure restart on failure, no concurrent second instance, and remove any default time limit that would stop a long-running server. Review power conditions so normal UPS operation does not unexpectedly stop it. Do not grant administrator privileges to the app task.
5. Run the task, confirm the HTTPS app is reachable, sign out of Windows and confirm it remains reachable, then reboot and repeat without an interactive sign-in. Check Task Scheduler history and the protected application log.

```bat
@echo off
cd /d C:\ProductionRunner\app
set PR_HTTPS=1
C:\ProductionRunner\app\.venv\Scripts\waitress-serve.exe --host=127.0.0.1 --port=8080 --threads=4 --call productionrunner:create_app >> C:\ProductionRunner\logs\app.log 2>&1
exit /b %errorlevel%
```

Create `C:\ProductionRunner\logs` with runtime write permission first and arrange log rotation/retention; redirection alone does not rotate logs. Task Scheduler is a pilot alternative, not an installed Windows service. Validate stop/restart behavior, including that the child Waitress process actually exits before a backup or update. See [Microsoft's task execution guidance](https://techcommunity.microsoft.com/blog/askperf/help-my-scheduled-task-does-not-run8230/375528/).

## 6. Add HTTPS and restrict access

Install Caddy from its [official instructions](https://caddyserver.com/docs/install) for the selected OS. The proposed network installation uses Caddy only as a reverse proxy, not a file server; never expose the repository or `instance/` directory through a static-file rule.

Example Caddyfile for a LAN hostname with an internal CA (replace the hostname with the administrator's actual DNS name):

```caddyfile
https://productionrunner.shop.internal {
    tls internal
    reverse_proxy 127.0.0.1:8080
}
```

Have the network administrator configure that name to resolve to the server. `shop.internal` here is an example, not a DNS record the app creates. With an existing company certificate, substitute the approved TLS configuration rather than deploying a second CA unnecessarily.

Validate the configuration using `caddy validate --config PATH_TO_CADDYFILE --adapter caddyfile`. Configure Caddy for automatic startup separately from the app, using the official service instructions. On packaged Ubuntu installations, use the package's systemd service. On Windows, follow the [Caddy Windows service instructions](https://caddyserver.com/docs/running#windows-service); record the service identity, configuration path and persistent data location. Generating a CA under one interactive account and then switching to a service account with a different data directory can produce a different CA; provision client trust only after the final service configuration is established.

For internal TLS, distribute **only the verified public root certificate** to each mini PC and office workstation through IT. The server's trust configuration does not automatically make clients trust it. Preserve and protect Caddy's persistent data, including its CA private material, as part of server recovery; it must not go to client PCs. Follow [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https) and [service/data-directory guidance](https://caddyserver.com/docs/running).

Allow inbound HTTPS 443 from the intended shop networks only. If using HTTP-to-HTTPS redirects, allow port 80 only for that purpose as policy permits. Keep application port 8080 private, restrict administrative access and do not add Internet router port forwarding. Confirm from a station that HTTPS works without warnings and direct 8080 access fails. If choosing public certificates instead, document their issuance/renewal prerequisites separately.

After sign-in, verify the session cookie has Secure, HttpOnly and SameSite attributes using browser developer tools, without copying its value into logs. Application source-IP throttling still has the proxy limitation described above; TLS does not resolve that.

## 7. Configure the organization and prove the workflow

From the office browser, sign in as the local admin, complete the organization profile, optionally set Branding, create individual users/roles/PINs and create **Laser 1**, **Laser 2** and **Packing**. Use individual planner access to upload a test PDF/DXF revision, create a job, assign each route operation and release it. Follow [mini PC installation](mini-pc-installation.md) for the four displays and three operator desktops.

Run the [one-laser acceptance procedure](hardware.md#one-laser-pilot-acceptance). Test all four TV feeds alongside interactive work, document upload/download, printing, independent inspection, covering operators, serial progress, holds, loss of connectivity and expired/revoked access. Verify time stamps and server restart recovery. Do not silently import the developer laptop's pilot records into a fresh customer installation.

## 8. Backups and restoration

For the first pilot, use a scheduled maintenance window and an **offline consistent backup**:

1. Notify operators and prevent new work. Stop the app service/task and all CLI writers; verify no Waitress child process still owns port 8080. Stopping only Caddy is insufficient.
2. Copy the complete `instance/` directory to a new timestamped, access-restricted backup folder. Do not overwrite the previous backup. Include the database, session-secret file and any SQLite sidecar files present; do not manually delete journals/WAL files.
3. Save the deployed Git commit, Python/package versions, service/task definition, environment configuration, Caddy configuration and protected certificate/CA data alongside the recovery record. If using `PR_SECRET_KEY`, preserve it through the secret-management backup procedure instead of printing it in the record.
4. Transfer the backup to a separate protected device/location and verify the copy. A second folder on the server's SSD is not sufficient protection against disk loss. Configure encryption/access controls, retention and a failure alert with the selected backup tool.
5. Start the app, verify login and a read-only job/document view through HTTPS, and record completion of the backup. The backup job must alert on stop/copy/restart failures; merely creating a schedule is not verification.

Database backups include uploaded drawings, DXFs and logos. External drawing-link destinations are not included. Browser downloads, USB contents and printer output are not the authoritative database.

If downtime is unacceptable, implement and test SQLite's [online backup API](https://sqlite.org/backup.html) plus coordinated secret/configuration backups; do not simply copy an actively written SQLite file. No automatic backup agent is supplied by this repository yet.

Restore rehearsal:

1. Use an isolated recovery machine or separate directory/port with no production writers. Install the recorded application revision and dependencies.
2. Restore the backed-up `instance/` directory and required configuration, with correct runtime permissions and any configured secret override. Restore proxy trust material only through the authorized administrator.
3. Before exposing it, run SQLite `PRAGMA integrity_check` and `PRAGMA foreign_key_check` using a read-only connection. The former should return `ok`; the latter should return no rows. Check counts/representative audit records and verify a drawing, DXF, logo and independent serial traveler where present.
4. Verify HTTPS and logins with authorized test access, service restart and client trust. Record recovery time and restored backup timestamp.
5. For an actual recovery, stop/isolate the original server before directing clients to the replacement. Keep the original failed state for investigation; never overwrite the only recovery copy. Reconcile any work done after the restored backup before resuming production.

## 9. Updates, rollback and ongoing operation

Do not auto-pull a moving Git branch on a running server. For each reviewed update: record the existing commit, schedule downtime, stop writers, take and verify the backup above, deploy the reviewed commit, install its pinned requirements plus the recorded deployment packages, run `init-db`, then restart and smoke-test. Preserve `instance/` during code updates. Never use `git clean -fdx` on the live installation.

Run regression tests against an isolated temporary database before deploying a new release, not against the shop database. Read migration notes for the target version; the original single-tenant schema needs the explicit migration flow rather than plain `init-db`. A code rollback alone may be incompatible with the updated schema. If rollback is required, stop writers and restore the corresponding pre-update code, dependencies and database/configuration snapshot together; explicitly reconcile any newer recorded work.

At shift start, verify server reachability, TV freshness and printer availability. Check backup completion, free disk space, service failures and certificate validity regularly. Schedule OS/browser/runtime updates and repeat the restart/session checks. Confirm UPS shutdown/restart on actual hardware. Retest restoration periodically and after storage or deployment changes.

## Outstanding before the shop pilot

Select server OS and host, reviewed release, service identities, network/DNS/TLS arrangement, backup tool and recovery objectives. Execute and record the selected installation path, verify shared-proxy authentication throttling is acceptable for the pilot, and complete physical printer/scanner/USB/display and load qualification. Documentation is not evidence that these tasks have already been performed.
