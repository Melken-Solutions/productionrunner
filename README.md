# ProductionRunner

A runnable, **provisional** manufacturing workflow MVP: one part number per ticket, a physical JobRunner barcode traveler that stays with the material, role-controlled production operations, and independent final inspection.

**Specification status:** PR v1.9 was referenced but its contents were unavailable through the linked chat. This implementation is based on the current request and the visible traveler/hybrid-tracking decisions. It is not a claim of PR v1.9 compliance. See [requirements and open decisions](docs/requirements.md) before approving the draft PR.

## Run locally

Requires Python 3.12 or newer. No external database or cloud account is needed.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m flask --app productionrunner init-db
python -m flask --app productionrunner add-user planner --roles planner
python -m flask --app productionrunner add-user operator --roles operator
python -m flask --app productionrunner add-user inspector --roles inspector
python -m flask --app productionrunner run --host 127.0.0.1 --port 5000
```

Each `add-user` command prompts for a password (12+ characters). There are no seeded accounts or default passwords. Open <http://127.0.0.1:5000>. Use separate browser profiles or sign out between roles. `init-db` preserves existing records.

SQLite data and the generated session signing secret live in ignored `instance/`. Keep both private. The built-in server is for local evaluation; do not expose it as a production service.

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

Tests cover the end-to-end workflow, permissions, self-inspection denial, route order, fail/hold behavior, stale and concurrent submissions, validation rollback, CSRF, output escaping, persistence, barcode output, and audit immutability. GitHub Actions runs the suite on Python 3.12 and 3.14.

## Documentation

- [Product and software architecture](docs/architecture.md)
- [Hardware and station design](docs/hardware.md)
- [Requirements, assumptions and PR v1.9 reconciliation](docs/requirements.md)
- [Repository inspection and delivery record](docs/repository-inspection.md)

This is a local evaluation slice. Before shop-floor rollout, reconcile the specification, validate physical printers/scanners, harden authentication and hosting, establish backup/restore, and approve the manufacturing workflow.
