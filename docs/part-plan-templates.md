# Reusable part Job Plans

A manufacturing template belongs to one organization and one exact catalog PN/drawing revision. It is an explicitly approved set of defaults, not an automatic reuse of the most recent job.

## First-time setup

1. Create a production request and complete its Job Plan, material and any vendor plan.
2. Approve the request after reviewing the production documents.
3. On the request, open **Save as approved part template**, review the reusable defaults and enter an approval reason.
4. The new template revision is visible under **Parts & drawings → part → Manufacturing template & history**.

Planner and admin roles can publish and withdraw templates. Publishing creates a new immutable revision, records the approver, reason and source request, and requires the latest revision number to prevent concurrent overwrites. Template withdrawal prevents new reuse; it does not revoke an existing request or released job.

## Repeat requests

Select the catalog PN/revision on **New production request**. Its latest approved template fills the ordered operations, station assignments, instructions, acceptance checks, first-piece requirements, final inspection/packing/shipping checks, DXF requirement, material and handling notes, and outside vendor/service instructions.

Each request keeps a separate copy and records its template origin. Quantity, due dates, request source and references, destination, sheet estimate, vendor PO/return date and operator assignments remain job-specific. Vendor return dates must be entered for the new request. Custom shop fields continue to follow organization configuration.

Choosing a different part asks before replacing edited manufacturing details. Retaining an edited plan makes it a one-off plan for the new part, with template provenance cleared. Existing request edits never automatically load a newer template.

Saving a request is still a preparation draft. Document approval, material/vendor validation, independent inspection and production release remain separate controls. Inactive stations, changed materials and unavailable vendor services block automatic template reuse and require review. A different drawing revision has no template until its own plan is approved.

## Revisions and existing jobs

Changes to a request or JobRunner do not update the template. Publish explicitly from an approved request to create a new standard revision. Publishing from a ticketed request uses the original approved request plan, not subsequent edits on its JobRunner; the UI labels this distinction.

Existing jobs retain their plan and source template ID. Historical template rows cannot be updated or deleted. Tenant-scoped lookups and composite foreign keys prevent cross-organization references. PostgreSQL storage uses the private productionrunner schema and grants the runtime only SELECT/INSERT on template revisions.

The additive migration must run before deploying this release. No templates are inferred or bulk-created from existing requests.
