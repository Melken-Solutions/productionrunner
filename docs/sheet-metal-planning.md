# Sheet-metal planning

Organization Admin → Materials controls active stock choices, metal, gauge, finish,
optional alloy, measured thickness (mm), stock size, handling instructions and a
required outside service. Gauge is not converted automatically into thickness.
Catalog edits require a reason and are audited. Deactivate unused entries rather
than deleting history.

New production request → Sheet material chooses a catalog specification and an
optional planned sheet count and job-specific material requirements. Once an
organization has a catalog, new requests must choose an active entry. Existing
unconverted requests must choose a material before approval/conversion. Editing a
material invalidates its use for pending approvals until the request is edited
and reviewed again. Converted tickets retain immutable material snapshots.
Existing tickets without material snapshots remain unchanged.

Admin → Stations controls the station list. In Define the work, choose a station,
enter instructions and Add station to route. The editable route lists steps in
execution order; repeat a station if needed. Operations named exactly like an
active station are assigned automatically when creating the job, including repair
jobs. Other operation names require explicit station assignment. Vendor steps are
excluded from automatic station assignment. Review all assignments and optional
operators before releasing the ticket. Select Laser 1 OR Laser 2 as appropriate;
not every job needs every available station.

Example: Laser 1 → Timesaver → Drill press → Bending → outside Clear coating
(if required) → independent inspection → packing → departure. Final independent
inspection and packing/departure remain their own gated workflows. A production
operation named Inspection or Packing does not satisfy those final gates. The Job Plan now assigns stations and criteria to these final stages, and station
TV boards show their queues. Final-stage labor timing remains future work; ordinary
production operations retain existing station timing. See job-plan.md.

For satin brass, configure required service `Clear coating`. The request must have
a vendor plan whose service matches (case-insensitive), including vendor,
instructions, expected return and return-inspection criteria. Select the final
production operation as its outside step during conversion. Creation and release
reject a missing or misplaced required service. The required step cannot be
removed or changed to another service. Vendor send/receipt and independent
inspection remain required; no fictitious vendor or completed work is seeded.

Material information is shown on requests, tickets, batch and serial travelers,
and operator work screens. Planned sheets are estimates, not inventory movements.
Use handling and job notes for grain direction, visible face, protective film,
stock size, tooling/finish acceptance and lot requirements. Inventory balances,
remnant tracking, actual sheet consumption, nesting and material certificates are
future capabilities.

The authorized Melken local test catalog contains six materials: 14/20 ga stainless
steel satin, 14/20 ga stainless steel mirror, and 14/20 ga brass satin. Both brass
entries require Clear coating. Alloy, thickness and stock dimensions remain blank
until verified by the shop. The seven test stations are Laser 1, Laser 2,
Timesaver, Drill press, Bending, Inspection and Packing.

## Approved outside vendors

Admin → Outside vendors manages company names and supported services (one per
line). List `Clear coating` and `Powder coating` separately even when one company
does both. Vendor changes require a reason, are version checked, and are audited.
Deactivate a company instead of deleting historical records.

With a configured catalog, request and ticket planners select active vendors and
supported services. The browser filters services by vendor; server-side checks
also enforce the combination at plan save, request approval, conversion and job
release. An inactive vendor or removed service blocks pending approval/release
until the plan is updated. Material-required clear coating remains independently
enforced. Existing plan names are retained; catalog changes do not rewrite ticket
history. Receiving and inspecting already released work remains available.

Organizations without any vendor entries retain manual vendor/service entry for
compatibility. Once even one entry exists, inactive entries do not restore manual
entry. Add the shop's actual approved companies before using the controlled list;
no invented suppliers are added to the local test organization.
