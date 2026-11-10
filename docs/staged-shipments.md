# Produce and ship in stages

Open a production-lot order or Packing & dispatch → the order. Under **Produce & ship in stages**, set the quantity still to ship and required ship date. Choose a reason and save. HOT remains the order's priority; the target makes the next delivery explicit.

For 580 ordered with 50 already shipped, entering 100 means the next 100 (a cumulative departure goal of 150). It does not mean 100 total on the order. One shipment may contain several independently identified production lots. This target does not automatically split lots, assign specific travelers, change the order deadline, or mark anything complete.

The target is shown on order progress, travelers, station work, shipping planning, and the work and packing queues. Accepted → packed → shipped counts come from the existing physical ledger. Inspection, route, ERP, label and packaging gates still apply. Packing progress alone is not departure approval. After the target is met, the planner sets the next quantity/date; the full order remains open until all units depart.

A replacement target starts from current departed quantity. Every target is retained as a tenant-scoped shipment_target_set event with quantity, date, departure baseline, reason, actor and timestamp. Targets are derived from that audited event history; no separate schema or migration is required. Existing ticket version locks prevent conflicting edits. Reversing a departure also recalculates target progress and may reopen a met target. Customer returns retain the existing separate return workflow.

Whole-batch jobs must first be converted to production lots while unstarted. Jobs with recorded work cannot be silently converted. This release plans one next shipment at a time, not a calendar of future deliveries, and does not automatically allocate a particular lot to a shipment or generate labels.
