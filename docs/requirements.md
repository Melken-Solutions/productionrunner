# Requirements baseline and specification reconciliation

## Available evidence

The current task requests development from PR v1.9 with repository inspection, product/hardware architecture, and a runnable vertical slice for one-PN tickets, JobRunner barcode travelers, role permissions and independent inspection.

The referenced conversation is `6ab697c2-ea1c-83ea-9f84-82e9a01a8fd3`, “Clean Up SAP Message.” `read_thread` returned five visible user turns and placeholder references for assistant responses, with no older-page cursor and no attachments. The full PR v1.9 specification could not be retrieved. The visible user decisions additionally establish:

- A barcode JobRunner stays with the job/material so downstream workers can determine their work.
- Hybrid tracking: batch by default, optional individual serial tracking.
- Define equipment and begin development in the supplied GitHub repository.

These are evidence, not a substitute for the missing versioned specification.

## Implementation traceability

| Requirement | Slice implementation | Verification / gap |
|---|---|---|
| Multi-tenancy | Tenant memberships, scoped roles and tenant-owned workflow records | Two-tenant read/write denial tests, revocation, composite ownership constraints, explicit legacy migration |
| PIN roles | Individual tenant PIN sign-in, hashed credentials, CLI enrollment/reset/disable, shared password/PIN throttling, tenant-scoped roles | PIN workflow, independent inspection, credential invalidation, persistent/concurrent limits and admin security history tests; 6–12 digits and fixed windows are provisional |
| One-PN ticket | Single validated scalar PN, revision, positive integer quantity; immutable after creation | Reject blank/list PN, invalid quantity; no ticket-edit endpoint |
| Traveler stays with material | Printable route/instructions, PN/revision/quantity, stable JobRunner ID and Code 128 | SVG and print-page tests; physical scan/print qualification outstanding |
| Downstream instructions | Scan resolves authenticated live ticket with ordered route and next operation | Full workflow and route-order tests |
| Batch default | Entire quantity completes each operation together | Sequential transitions; no partial-quantity action |
| Optional individual serial tracking | New serial tickets have one traveler, ordered progress, inspection and history per unit; shared route definition | Concurrent units, individual hold, independence and scan tests; legacy tickets stay batch; cross-ticket uniqueness, genealogy and custom unit routes unresolved |
| Role permissions | Planner creates; operator completes; inspector inspects; viewer/admin read; planner/operator request traveler | Server-side role checks and denial tests |
| Independent inspection | Inspector may have performed none of the ticket’s production steps; all steps must be complete | Dual-role self-inspection blocked; early inspection blocked |
| Evidence/history | Attributed UTC events; inspection notes; immutable event rows | Persistence, rollback and immutability tests |
| Full PR v1.9 | Not available | **Blocked on source specification; do not approve as compliant** |

## Provisional decisions requiring reconciliation

- Local Flask/SQLite monolith, not a prescribed target production stack.
- Roles above and their exact permission mapping. `admin` does not silently grant production or inspection powers; accounts are provisioned by a trusted local CLI.
- Planner enters the released PN/revision, work instructions, criteria and route. No approved drawing registry or master-data lookup yet.
- Final whole-batch inspection for batch/legacy tickets; per-unit final inspection for new serial tickets after that unit’s operations. Whether inspection gates are required between operations remains unknown.
- Any production contributor is disqualified from final inspection of that batch or individual unit, regardless of role. Ticket creator is not automatically disqualified unless they also perform production.
- Fail holds the whole batch for batch/legacy tickets, or only the inspected unit for new serial tickets, with no override. Rework, concessions, scrap, split/merge, cancellation and hold release need approved rules.
- No inferred SAP interface, scheduling, work-center assignments, inventory moves, electronic signatures, regulatory compliance, offline queue, or externally visible deployment.
- Stable traveler ID is an opaque identifier, never an authorization credential. Scans require a signed-in account.

## Reconciliation checklist

Obtain PR v1.9 source; version it in the repository; map every clause to implementation/test/backlog; confirm role matrix, route/inspection gates, status names, identifier format, serial semantics and equipment constraints; update this document and code together. Keep the initial implementation in a draft PR until that review is complete.

## HOT scheduling — requested during local testing

HOT jobs appear before normal jobs. Multiple HOT jobs use planner/admin-assigned numeric rank (1–99, smaller first), then oldest HOT designation, then stable ticket ID. Default rank 10 allows insertion of more urgent jobs ahead of existing work. Equal ranks are supported. Priority is tenant-scoped, audited and subject to optimistic ticket-version checks. It does not grant production/release authority. Automatic due-date ordering, drag-and-drop scheduling and automatic HOT clearing are outside this slice.

## Part-number selection and matched drawings — requested during local testing

Support PDF uploads and external HTTPS links, a tenant-specific PN/revision list and search, automatic drawing association on ticket creation, and explicit exact-revision attachment to existing unmapped tickets. Drawings are accessible from both batch and unit pages. Uploaded bytes and revision associations are immutable; an external URL can still serve changed content and is visibly identified as external. Manual entry without a library drawing remains available for compatibility. Formal document release approval is not inferred.


## Operator DXF download — requested during local testing

Allow a planner/admin to attach one DXF to a part revision during creation or later if no DXF exists. Operators download that exact revision’s original file from the ticket/unit or part page for manual loading into laser software. Tenant boundaries, audit records, immutable revision files and existing drawing associations are preserved. This adds file transfer only, with a 20 MB size cap; it does not validate laser suitability or automate machine operation.
