# Daily planning workspace

## Today

Planners and organization admins open **Today** from the main navigation. It brings together planned work for today, overdue/today shipments, HOT jobs, held work, first-piece/business checks, requests needing preparation, unreleased jobs, outside returns due back, accepted/packed quantities and open help requests. Items link to the existing controlled action. One job may appear in several groups. Refresh the overview to retrieve new data; connection status does not mean every displayed number has refreshed. Today defaults to the browser’s calendar date and provides a planning-date selector; remote users can choose the shop’s date if different. Without JavaScript, the initial fallback is UTC. Event timestamps remain UTC.

## Production calendar

Open **Calendar**, choose the week start, then expand a job under **Assign or move work**. Choose the whole job or one lot/serial traveler, choose the planned work day, select a reason and save. A whole-job date defaults all travelers without an override. A traveler-specific date overrides that default. Leaving a date blank explicitly marks that selection unscheduled; moving the whole job does not erase traveler overrides. Changes retain actor, timestamp and reason in the job audit. Stale changes are rejected. Shipped travelers and closed/cancelled jobs cannot be scheduled.

Planned work days are shown on operator travelers and in Today. They do not change customer required dates or staged shipment deadlines, reserve machine capacity, change HOT ordering, or authorize production. This is day planning, not an automatic capacity scheduler.

## Quantity progress

Open **Quantity progress** on the job. Ordered, shipped and still owed summarize customer fulfillment. **Where the units are now** counts active quantities once: current operation, inspection, hold, packing or departure. An operation bucket includes waiting and active work; it is not a completed-production claim. Historical scrap is separate and replacements remain active. The expandable completed-through-step table is cumulative and must not be added across steps. All counts come from existing production and reversal-aware fulfillment records. A reconciliation warning appears if active counts do not equal the order.

## Readiness and repeat parts

An unreleased Job Plan shows **Release readiness**, including documents, required DXF, material review, final-stage stations/checks, internal assignments, vendor rules and delivery date review. Links open the appropriate existing editor. Review items are not automatically approval failures: unassigned pickup still requires explicit planner authorization at release. The release action continues to enforce current backend checks and require the planner's review.

On a catalog part, choose **Make this part again**. An approved template loads the manufacturing defaults. Enter order-specific quantity, dates, customer and destination, then choose **Use saved plan — go to review**. The route remains editable and the review highlights missing order details. Saving still creates a draft; document approval and production release remain separate.

## Help ownership

A planner or admin can choose **I'm handling this** in an unresolved job conversation. The owner appears in the inbox, Today and the conversation. Ownership changes are recorded as messages with the actual actor. Another planner cannot silently take an owned request. The owner or an admin can release ownership; stale forms are rejected. Resolution does not erase ownership history. Operators can send and read messages but cannot claim planner ownership.

## Connection and save feedback

Interactive pages show connection status, when the page loaded, unsaved input, submission in progress and server-confirmed completion. An unfinished form warns before navigating away. No browser storage of entered form data is added. If a network interruption prevents confirmation, inspect the job before retrying; a server may have processed the action even if its response was lost. There is no offline production mode. TV boards retain their separate live/stale banner and last successful update time.
