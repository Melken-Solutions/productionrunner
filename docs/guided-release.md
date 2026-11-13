# Guided release and reusable plans

From New production request or Edit request, choose **Save draft** for unfinished work or **Save & continue to release**. The saved request also offers **Release to floor / save reusable template** for planners.

The release page presents the drawing package, ordered Job Plan, material, ERP references and configured manual clearance checks together. Select lot, batch or serial tracking. Staged deliveries require lots or serial units. Enter serial numbers when using serial tracking.

Select **Save this plan as a reusable template** to publish approved manufacturing defaults for this PN and revision. A checkbox on the initial request carries this choice into release review. Quantity, order dates and ERP references are not part of the template. Existing templates receive a new revision; old jobs keep their own snapshots. The separate template action on approved request details is also expanded and visible.

One explicit confirmation authorizes the listed checks and release. JavaScript then invokes the existing guarded server actions in order. Each action retains its own role checks, tenant scope, version checks and audit evidence. ERP verification is manual; this does not submit to SAP. Production inspection and operator checks still happen during execution.

This sequence is not a single transaction. If a later action fails or connectivity is lost, earlier saved actions remain. The page stops and links to the request or created JobRunner. Open it to check actual status before continuing; do not assume release succeeded or submit a duplicate. A template may have been published even if a later release action fails. JavaScript is required for the guided path; individual actions remain available.

The release page identifies incomplete requirements before submission. Server-side checks run again when approving and releasing. Changed request versions, workflow settings, template revisions and ERP policy snapshots stop stale guided actions.
