# Controlled Job Plan

The planner creates a JobRunner from an approved request, then opens **Job Plan**.
Production operations remain in their creation order, with station/operator
assignments managed independently. Each operation has explicit acceptance criteria
and can require independent first-piece approval. Inspection, packing and physical
departure appear as the final three stages, each with an admin-defined station and
instructions/acceptance criteria. Packing and shipping may share one station.

Save a draft revision with a reason. Release is available on the Job Plan screen;
unsaved form edits disable its Release button until saved. Every revision is append-only and audited.
The release action requires a saved Job Plan, complete production assignments,
active final-stage stations, and explicit acknowledgement of the complete plan,
document revisions and material. If the plan requires DXF, release is blocked
without it. Release audit evidence captures the plan, route, assignments, vendor
plans, drawing identity/checksum/link, DXF checksum and material snapshot. External
drawing links still require human verification; a URL is not an immutable file.

Released plans cannot be silently edited. Previously released legacy jobs without
a plan retain their existing workflow. Unreleased existing jobs must complete a
plan before release. Existing records are not backfilled with invented approvals.

For first-piece-controlled internal operations, setup sessions are permitted but
production/machine starts, resumes and completion are blocked until an independent
inspector records a pass at the assigned station. Setup contributors cannot approve
their own first piece. Failed results and later rechecks remain in immutable history.
Station reassignment invalidates prior first-piece approval. Approval is shared by
units using the same operation/setup; completion evidence is per serial unit.
Outside steps instead use independent vendor-return inspection.

Operation completion, vendor-return inspection, final inspection, packing and
shipping require actual evidence against the saved criteria. Serial evidence is
linked to its unit; packing/shipping evidence accompanies the corresponding physical
movement records. Inspection failure retains the existing hold/rework controls.
Required first-piece failure blocks production; it does not fabricate a completed
operation or accepted quantity. Do not continue physical work after a failed check.

Final-stage TV boards use planned stations rather than attributing final inspection
to the last production machine. Packing/shipping counts follow actual accepted,
packed and departed quantities and pending business checkpoints. Stations referenced
by open plans cannot be deactivated. Printed batch and serial travelers include the
plan's criteria and final stages.

## Remaining roadmap gaps

This is the controlled-plan foundation, not a complete ISO quality-management system
or a claim of ISO certification. Evidence currently uses criterion text and recorded
results; structured dimensional measurements, automatic tolerance evaluation,
calibration records, stock/heat-lot genealogy, formal in-process nonconformance and
scrap disposition, and reusable approved PN/revision route templates remain future
work. Final-stage station assignments are implemented; their labor timing is not.
Released-route change authorization and resuming after a mid-route defect need a
separate controlled workflow; the software deliberately locks released plans today.
The whole job closes only after all units physically depart.

## Door-strike pilot acceptance

Use an approved PN/revision drawing and matching DXF when required, select 14 ga SS
satin, then route Laser 1 → Drill press → Timesaver → Bending/Brake press. Enter the
actual drawing criteria (no invented dimensions), require first-piece approval for
risk-sensitive operations, assign Inspection and Packing stations, and use Packing
or a dedicated Shipping station for departure. Review the full plan before release.
Test missing plan, missing DXF, unassigned station, missing check evidence,
first-piece failure, self-approval, a station change, partial serial progress,
inspection failure, packing before acceptance and departure before packing.


## Plan at initial request
New Request and Edit Request include an ordered Job Plan editor. Planners add, remove and move production steps; choose active admin-configured stations; record instructions, acceptance criteria and first-piece requirements; identify the outside-processing step; and assign inspection, packing and shipping stations and checks. A DXF-required option gates approval as well as release.

Incomplete plans can be saved as drafts. Approval and conversion validate completeness, active tenant-scoped stations, outside-plan correspondence and material-required final outside processing. Changes to a plan invalidate request approval and are captured in request history. Conversion copies the approved sequence, station assignments and checks into the JobRunner in the same transaction. Submitted replacement route values are ignored for a request with a saved plan. Release remains an explicit separate review. Converted/cancelled request plans are immutable.

Existing requests without a saved plan retain the earlier conversion route editor; edit an open request to add its plan before conversion. The editor requires JavaScript. The current outside plan supports one vendor operation per request. Operator coverage and actual-person recording retain the existing assignment/work workflow.

Outside processing is positioned after internal production and before inbound inspection, final inspection, packing and shipping. The request editor keeps outside steps last; approval rejects a route with internal work after its outside step. Vendor details appear below the route and above the final stages. Inbound vendor-return inspection remains distinct from final independent inspection.

## Planner experience
New and edited requests use three stages: item, process, and review. Search filters the existing approved part/material choices. Part selection links the matching drawing and supplies the catalog description when the request description is empty. Choosing a station supplies an operation name only when that name is empty. Instructions and quality checks expand on demand; vendor details appear only when outside work is selected. The review screen summarizes the request and route, with edit links and incomplete-plan guidance. Saving is explicit, not automatic; it creates a preparation draft, not a release. Existing backend approval and release checks remain in force. PN routing templates, automatic gauge-to-laser rules and separate admin property catalogs are subsequent work; this UI does not invent those rules.
