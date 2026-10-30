# Remote shipping-label handoff

Production purpose, source system and shipping method are separate settings. Admin → Shipping setup defines source defaults and approved ship-to destinations. `erp` represents ERP/SAP requests. Melken Pilot defaults are ERP → make to stock / LTL / planner labels, and Smartsheet → make to order / FedEx / planner labels. Other organizations define their own rules; no customer names or shipping assumptions are hard-coded into workflow logic.

## Planner and packing workflow

1. Open the job's Packing & dispatch page → Shipping plan, boxes & labels. Source defaults prefill purpose, shipping method and label responsibility. Review against the request, select an approved destination, and save with a planning or override reason. Later default changes do not alter saved plans.
2. Record each physical box as a separate packing record using the existing accepted quantities or serial-unit selections. Create its box identification with measured gross weight in kg and dimensions in cm. Print the contents label using the browser Print command. Labels identify the job, PN/revision, quantity, source/order reference, destination and serials where applicable.
3. Open Shipping. Start a parcel shipment or pallet with a box. Add further boxes from the same destination, method and label-owner policy. LTL pallets may contain different jobs, products and source orders. They cannot mix destinations.
4. For an LTL pallet, record overall dimensions and gross weight including the pallet and packaging. Mark the manifest complete and request labels. The Shipping link shows waiting work, and the Shipping queue refreshes every 60 seconds with label requests first. This is an in-app handoff, not email or push delivery.
5. The configured label owner prepares documents in SAP, FedEx or the carrier's system, then uploads one PDF packet containing all required labels and documents, with carrier and tracking/PRO reference. The packet can be replaced; only the latest packet for the current manifest is downloadable. It is private, tenant-authorized, size-bounded and hash-verified.
6. Packing downloads and prints the current packet. Downloads are audited but do not claim successful printing. The packer records shipping checks and confirms physical handoff. Dispatch atomically writes the existing per-job and serial-unit departure ledger, so quantities remain consistent and a partially shipped job stays open. Business-system checkpoints and production acceptance still apply.

## Changes and evidence

Reopen a requested/labeled manifest before adding or removing boxes. Reopening invalidates old shipping labels and requires a fresh request and upload. Previously downloaded/printed PDFs cannot be recalled; discard old copies whenever the manifest or packet changes. For incorrect box identification, remove the box from an editable manifest, then use the planner/admin correction control to void and re-create it. The original identification remains in audit history.

Boxed quantities cannot bypass the manifest through the old per-ticket departure action. Pack records attached to active boxes cannot be independently reversed; void the unassigned identification first. Shipment-linked departures are immutable and cannot be reversed independently in the legacy correction screen. Customer returns continue through the existing return process. A dedicated whole-shipment recording-correction flow is not part of this release.

Shipping label packets, audit events and departure links are append-only at the database level. Labels remain retained; existing part-library retention previews do not purge them. Object backup/reconciliation must include `shipping_labels.object_key` references as well as part drawings and DXFs. No automated orphan cleanup runs. Failed storage/database combinations may leave unreferenced objects, which must not be blindly deleted.

## Deployment

Apply `20261030003000_shipping_handoff.sql` using the migration role before deploying the app; it adds private-schema tables and least-privilege runtime access without changing existing customer records. Record the immutable file checksum in the application's migration history. Existing local SQLite installations run `flask --app productionrunner init-db` after backup to create the additive tables. The standard startup does not migrate the cloud database.

PDF files use the configured existing private storage backend (Supabase for hosted staging; embedded database bytes for local SQLite). No carrier purchase, FedEx API call, SAP confirmation, automatic email, or remote printer driver is implemented. ProductionRunner coordinates the handoff and records physical dispatch.
