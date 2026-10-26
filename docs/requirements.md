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
| Optional individual serial tracking | Optional per-ticket serial inventory, exact quantity and uniqueness validation | **Partial:** independent serial events/routing, serial labels, cross-ticket serial uniqueness remain unresolved |
| Role permissions | Planner creates; operator completes; inspector inspects; viewer/admin read; planner/operator request traveler | Server-side role checks and denial tests |
| Independent inspection | Inspector may have performed none of the ticket’s production steps; all steps must be complete | Dual-role self-inspection blocked; early inspection blocked |
| Evidence/history | Attributed UTC events; inspection notes; immutable event rows | Persistence, rollback and immutability tests |
| Full PR v1.9 | Not available | **Blocked on source specification; do not approve as compliant** |

## Provisional decisions requiring reconciliation

- Local Flask/SQLite monolith, not a prescribed target production stack.
- Roles above and their exact permission mapping. `admin` does not silently grant production or inspection powers; accounts are provisioned by a trusted local CLI.
- Planner enters the released PN/revision, work instructions, criteria and route. No approved drawing registry or master-data lookup yet.
- Final whole-batch inspection after all operations. Whether inspection gates are required between operations remains unknown.
- Any production contributor is disqualified from final inspection, regardless of role. Ticket creator is not automatically disqualified unless they also perform production.
- Fail holds the entire batch with no override. Rework, concessions, scrap, split/merge, cancellation and hold release need approved rules.
- No inferred SAP interface, scheduling, work-center assignments, inventory moves, electronic signatures, regulatory compliance, offline queue, or externally visible deployment.
- Stable traveler ID is an opaque identifier, never an authorization credential. Scans require a signed-in account.

## Reconciliation checklist

Obtain PR v1.9 source; version it in the repository; map every clause to implementation/test/backlog; confirm role matrix, route/inspection gates, status names, identifier format, serial semantics and equipment constraints; update this document and code together. Keep the initial implementation in a draft PR until that review is complete.
