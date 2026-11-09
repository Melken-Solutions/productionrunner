# Quantity-based production lots

One production request and approved Job Plan can cover many production runs and partial deliveries. The order quantity and deadline remain on the original JobRunner.

## Planning

Create a JobRunner from the approved request. Production lots is the default UI tracking choice; choose a typical lot quantity (default 50, limited to the order quantity). The app creates lot travelers automatically, including a smaller final remainder. Whole-batch and individually serialized tracking remain available.

For example, an order for 580 creates eleven 50-piece lots and one 30-piece lot. Each shares the approved route, material, documents and station assignments. No repeated requests or manually reentered routes are needed. An existing unstarted batch can be enabled from its Production lots link. Jobs with existing production, timing, vendor custody or delivery history cannot be converted automatically.

## Operator flow

Open a lot, start/pause/resume/stop work as usual, then explicitly confirm the completed good quantity and acceptance evidence. Completing a lot makes only that quantity eligible for the next station. Route ordering, first-piece requirements and actual-operator attribution remain enforced.

If fewer pieces are ready, stop the session and split the lot. A new traveler identifies the separated quantity. Both retain prior completed operations and original contributors. Independent inspection also checks ancestor work-session contributors.

Hold discrepant material separately. An admin can authorize scrap of a held lot with a reason; a replacement lot starts at the first operation. Scrapped quantity is displayed separately and never counts as accepted or delivered. Early-stage rework returns a lot to the unfinished route; it does not bypass the remaining operations or final inspection.

## Inspection and shipping

Inspectors confirm the lot quantity and evidence. Production, custody and repair contributors cannot inspect their own material. Accepted lots can be packed and shipped while the rest of the order is still running. Box and shipment weights use the existing workflows; piece counts sum the actual quantities attached to selected lot travelers.

ERP checkpoints can be recorded per accepted lot with a transaction reference. Only the covered lots are cleared for their checkpoint stage. A confirmation for the first 50 never clears the remaining 530.

The order closes only when all ordered units have physically departed. For 580 ordered and 50 departed, 530 remain owed regardless of today's production count.

## Progress and audit

The order dashboard shows ordered, delivered, still owed, accepted, held, scrapped, deadline, and current good quantity through each step. HOT priority remains attached to the order and TV boards count pieces rather than travelers.

Splits, holds, scrap, replacements, station confirmations, inspection and physical movements are audited. Split-derived completions preserve original operator/time attribution. Current good-through-step counts exclude later scrapped units; the original quantities remain in the immutable event history.

## Deliberate boundaries

- A lot is a physically identified container/group, not a product serial number.
- Split before inspection, packaging or vendor movement when smaller quantities need independent handling.
- Lots with packaging, vendor custody or rework history cannot be split here. Vendor-related scrap disposition requires a future dedicated workflow that preserves custody balances.
- Partial customer returns within a delivered lot require a future lot-return split workflow; current return selection covers whole lot travelers. Returned whole lots preserve their quantities in repair jobs.
- The system does not backdate or invent last Friday's production/delivery evidence. Recorded actions use the actual recording time.
- No existing job is automatically converted or marked delivered by this release.

Apply the additive migration before deploying. Local SQLite databases are upgraded through init-db after backup; cloud PostgreSQL uses the private productionrunner schema.
