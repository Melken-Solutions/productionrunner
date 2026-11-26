# Organization-configurable ERP and demand references

## Organization setup

Open **Organization Admin → ERP & demand settings**. Set the authoritative ERP name and which request origins require an ERP path. Create request paths before enabling mandatory origins. Nothing is enabled automatically for other organizations.

Each path has a name, request origin, active/disabled status, configurable reference labels, and a requirement stage (optional, before request approval, or before production release). Reference values remain text so leading zeros are retained. Production order, sales order, sales line, customer PO, supporting sheet/row reference and JobRunner ID remain separate identifiers.

Two optional starting points populate an editable form:

- **Standard ERP order:** production order required before release; an SO is optional, supporting make-to-stock orders without an SO.
- **Supporting-sheet demand:** SO, line and supporting reference required before approval; production order required before release. Suggested checks verify the supporting item against the ERP and confirm clear-to-build status.

For the test organization's intended process, SAP is authoritative and Smartsheet identifies urgent needs originating in SAP. A Smartsheet row is a supporting reference, not production authorization. These are configuration choices, not hard-coded requirements for every customer. A request path does not automatically mark a job HOT, select make-to-stock/order, or select shipping. Existing organization-defined shipping defaults remain separate and should be reviewed if they reflect older assumptions.

Admins can enter clear-to-build checks and select an optional confirmation checkpoint before packing or physical departure. The configured checkpoint requires a recorded reference through the existing fulfillment workflow, including lot-specific confirmation for partial runs. Operation/reason-code mapping notes are retained in the job requirements for planner review; they do not run an ERP integration or automatically translate production transactions.

## Planner workflow

1. Create the request, choose its ERP path, and enter the appropriate references. Selecting a path sets its request origin. Drafts may have missing references.
2. Approval enforces the path and references configured as required before approval. Editing references on a request invalidates document approval, as other request edits do.
3. Conversion to a job snapshots the applicable ERP settings, path and references. Subsequent organization changes do not rewrite that job's requirements.
4. Open **Job Plan → ERP references & clearance**. Complete remaining references, manually check each configured criterion in the authoritative ERP, and confirm the job PN, revision and ordered quantity are clear to build. The actual planner/admin, time, references, item context and checks are retained in immutable job events. Rechecking creates new evidence; it does not overwrite earlier checks.
5. Release readiness links to missing ERP details. Release rejects missing required references or clearance and records a release snapshot. Once released, these references and requirements cannot be edited from this screen.
6. Complete any configured ERP confirmation checkpoint at the selected packing/departure stage, recording the ERP confirmation reference.

Older unreleased jobs with no snapshot must select an active path if the current organization policy requires their request origin. Already released legacy jobs retain their existing workflow. Disabling a path prevents selection for new requests; already converted jobs retain their snapshot.

## Visibility and exports

References appear in request review, job details, printed travelers and station screens. Timing reports and their CSV exports include the ERP production-order number. **Export ERP job summary** provides distinct order identifiers, ordered/accepted/shipped quantities and clearance attribution for planner review. This generic CSV is not a certified SAP import format and does not submit an SAP confirmation.

Possible duplicate production-order references within the organization are warned about with links to other requests; legitimate multiple jobs per order are not blocked. These checks compare current linked-job references when available and preserve leading zeros.

## Boundaries

No automated ERP connection or SAP status lookup is performed. The planner's recorded verification is the evidence. The ERP settings do not change ordered quantities, manufacture extra units, record scrap, or infer yield. Station-level actual production, overproduction, scrap and rework accounting is a separate upcoming change and must be implemented before those quantities can be exported as ERP confirmation facts.
