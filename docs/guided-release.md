# Guided release and reusable plans

From New production request or Edit request, choose **Save draft** for unfinished work or **Save & continue to release**. The saved request also offers **Release to floor / save reusable template** for planners.

The release page presents the drawing package, ordered Job Plan, material, ERP references and configured manual clearance checks together. Select lot, batch or serial tracking. Staged deliveries require lots or serial units. Enter serial numbers when using serial tracking.

Select **Save this plan as a reusable template** to publish approved manufacturing defaults for this PN and revision. A checkbox on the initial request carries this choice into release review. Quantity, order dates and ERP references are not part of the template. Existing templates receive a new revision; old jobs keep their own snapshots. The separate template action on approved request details is also expanded and visible.

One explicit confirmation authorizes the listed checks and release. JavaScript then invokes the existing guarded server actions in order. Each action retains its own role checks, tenant scope, version checks and audit evidence. ERP verification is manual; this does not submit to SAP. Production inspection and operator checks still happen during execution.

This sequence is not a single transaction. If a later action fails or connectivity is lost, earlier saved actions remain. The page stops and links to the request or created JobRunner. Open it to check actual status before continuing; do not assume release succeeded or submit a duplicate. A template may have been published even if a later release action fails. JavaScript is required for the guided path; individual actions remain available.

The release page identifies incomplete requirements before submission. Server-side checks run again when approving and releasing. Changed request versions, workflow settings, template revisions and ERP policy snapshots stop stale guided actions.

## Scheduled production start

Set the optional **Scheduled production start** beside quantity and required date. On a new or edited request, the start cannot be after the required date. Saving a changed date invalidates prior request approval and records the scheduling change. Creating the JobRunner copies the date into its existing production calendar as the whole-job default. Saved part templates and repeated jobs do not reuse old start dates.

Use Production calendar to reschedule an existing job or an individual traveler. Traveler dates override the whole-job date, including an explicitly cleared traveler date. Calendar recovery dates may be after an overdue deadline; the original required date remains unchanged.

Today shows planned quantities on their scheduled day and earlier unfinished/unshipped quantities under **Earlier scheduled work**. A future date does not appear in Planned today. It can still appear under HOT, readiness, or other action groups. Scheduling does not release work, reserve machine capacity, or prohibit an authorized early start.

Production ticket, shop-floor and TV queues prioritize HOT rank, then ascending scheduled start, then ascending required date. Undated jobs follow dated jobs at the same HOT rank. Travelers use their own effective date; whole-job boards use the job default. Existing ordering breaks exact ties.
