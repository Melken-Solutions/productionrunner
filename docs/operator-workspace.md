# Operator workspace on tablets

Operators without planner/admin roles land on Shop Floor after sign-in. Users with combined roles can choose Shop Floor from the standard navigation. The station selection is remembered on this browser for the organization, including after signing out; it is a view preference, not a permission change. HOT jobs remain first. Station queues distinguish ready work from upcoming work; selecting a station does not bypass the production route.

Choose the station, then match a ready lot/unit traveler to the material. The tablet and station TV use the same readiness calculation. Working, ready, upcoming and waiting/held work are separated. Each tablet group shows up to 50 travelers; scan a traveler to find additional work. Counts describe units at each operation, not unique units across the entire route. Scanning a traveler is available in the collapsed scan section. Released, assigned internal operations open directly in station controls. Unreleased work, outside operations, holds, completed work and legacy unassigned jobs retain their appropriate status/handoff views.

The focused station screen shows the station, operation, PN/revision, traveler quantity, current operator and instructions. The drawing stays one tap away. Material identity and handling instructions appear with the work instructions when configured. Full material details, plan/first-piece records, history and split controls are under **Material, full plan & other actions**.

- **Start work** records the selected work type. Attended production is the default when first-piece approval is satisfied; otherwise setup is selected and the approval requirement is visible.
- **Finish & confirm quantity** opens the required good-quantity and check-evidence fields. It does not silently prefill a completed quantity or mark checks passed.
- **Pause or stop work** opens the controlled reason selection. Pause reserves the station; Stop releases it without completing the operation.
- **Resume work** is shown for paused work. Authorized covering operators are recorded under their own identities. A different operator cannot pause or stop someone else's running session.

Completion still enforces quantity, route order, first-piece approval, release, version checks, authorization and independent final inspection. If fewer pieces are ready, stop the session and use the controlled lot split before completing the smaller quantity. This UI update does not introduce per-piece tallying, offline completion or automatic split/inspection.

The shared header is reduced to shop identity and sign-out. Jobs returns to the station queue; More contains Help, Account security and Standard view. Packing, inspection and outside processing retain their existing detailed workflows; they are not redesigned by this station-work pass.

Validated with touch emulation at 1024×768 and portrait at 768×1024. Physical iPad/Safari, glove, stylus, scanner and printer acceptance remains a shop test.

Station queues refresh every 15 seconds while idle. Refresh pauses while scanning or using controls and does not refresh an open work form. A failed refresh displays a stale/offline warning. After completing an operation, the handoff identifies the next assigned station. For example, finishing one 50-piece lot from a 100-piece order makes 50 ready at the next station and leaves the other 50 upcoming. Upcoming work cannot be started through its station card. Station selection never changes user permissions.
