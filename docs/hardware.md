# Hardware and station architecture — initial installation plan

The equipment layout below records the installation decisions agreed on 2026-10-26. It is not a vendor quote or approval of specific models. Final hardware, environmental suitability, hosting configuration and physical acceptance remain to be verified. PR v1.9 reconciliation remains open.

## Agreed installation layout

| Location | Computer and screens | Purpose |
|---|---|---|
| Laser 1 | One mini PC, one TV, one desk monitor, keyboard and mouse | Station queue on TV; operator work, printing and DXF-to-USB transfer at desk |
| Laser 2 | One mini PC, one TV, one desk monitor, keyboard and mouse | Same workflow, assigned to Laser 2 |
| Packing | One mini PC, one TV, one desk monitor, keyboard and mouse | Packing queue on TV; operator work and printing at desk |
| Shop overview | One mini PC and one TV | Whole-shop queue, including HOT work and holds |
| Shared server | One dedicated server computer | Hosts ProductionRunner, database, uploaded drawings/DXFs and audit history |
| Shared printing | One network shop printer | Job tickets, batch travelers, individual serial travelers and drawings |
| Planner/admin | Existing office workstation if suitable | Parts uploads, ticket creation, station/operator assignment and organization administration |

**Installation totals: 4 mini PCs, 4 TVs, 3 desk monitors, 3 keyboard/mouse sets, 1 server and 1 shared printer. No tablets are planned for this installation.** These are equipment requirements, not necessarily new purchases: reuse compatible existing monitors and the planner/admin workstation after checking them. Barcode scanners are optional pilot additions, not included in these totals. Tablet-friendly Shop Floor mode remains available in the software and can also be used on desktop screens.

Existing Windows computers at the lasers have unknown OS/network capabilities and no HDMI. Keep any machine-specific software on them as needed; this plan does not depend on them for ProductionRunner or replace the laser controllers. New DXFs currently reach each laser by USB.

## Dual-screen station setup

- At Laser 1, Laser 2 and Packing, use an extended desktop, not mirrored screens. Select mini PCs that support both screens simultaneously at their intended resolutions and have compatible video ports. Two HDMI outputs are the preferred layout; verify reused monitor inputs before buying cables or adapters.
- Put the station TV board on the TV in a full-screen browser using a dedicated organization account with only the `display` role. Put the interactive ProductionRunner session on the desk monitor in a **separate browser profile or browser**. Two ordinary windows in the same profile share authentication and are insufficient.
- Operators sign in individually at the desk and sign out when handing over. Assigned operator is planning guidance; another authorized operator may cover, with the actual contributor recorded. Independent inspection uses an eligible individual's account, even when sharing a physical workstation.
- Configure browser startup, screen placement, sleep settings and power-loss recovery during deployment. Verify that interactive sign-in/sign-out does not affect the TV. These settings are not automatically provisioned by ProductionRunner.
- Provide keyboard/mouse access and reachable USB ports at laser desks. Mount screens and protect cables for the shop environment. Keep the desk close enough that recording completion is practical.

## Planner/admin uploads and release

1. From the office workstation, sign into the organization as a planner or local admin and open **Parts & drawings**.
2. Add the exact PN/revision with a PDF drawing (up to 10 MB) or an HTTPS drawing link. Optionally upload its DXF (up to 20 MB) at creation, or attach the first DXF later on the revision page.
3. Create a ticket using that PN/revision. Existing exact catalog matches attach automatically. For a ticket created before its catalog entry, use **Attach matching drawing**; an existing ticket reference cannot be replaced.
4. Assign every routing operation to an active station (Laser 1, Laser 2 or Packing as appropriate), optionally assign an operator, then release the ticket. A route need not pass through both lasers.
5. Print the job ticket/cover or the appropriate batch/unit travelers as needed. A serial cover sheet does not replace individual unit travelers.

Uploaded drawing and DXF content is immutable per revision; changes require a new revision. The first DXF added later becomes available to jobs already linked to that revision. External drawing URLs are fixed references, but their destination content can change; use version-specific links and verify the source revision. There is no formal drawing approval/release process in this MVP; ticket release is a separate workflow action.

## Operator, USB and paper workflow

1. Use the TV queue to identify assigned work, then open or scan the live job at the desk monitor under the actual operator's identity.
2. Verify PN, revision, instructions and batch/unit identity. View or print the drawing and print the appropriate traveler through the browser and configured shop printer.
3. At a laser desk, download the job's DXF to the mini PC, copy it to USB and load it through the existing laser workflow. Check the file identity, geometry, units and machine setup in the laser software before cutting. ProductionRunner does not generate toolpaths or control the laser.
4. Keep the batch traveler with its carrier or each serial traveler with its unit through handoffs. Record operation completion in ProductionRunner at the desk so the shared queue updates.
5. Use the independent inspection workflow after production. Printed documents do not change server state, and a download audit event proves only a request, not successful USB transfer or machine loading.

Paper can become stale after priority, assignment or workflow changes; use the live job for current status. Browser printing and PDF printing need physical testing; external drawing sites may require their own authentication and print controls. No unattended print service or printer-driver deployment is included.

## Shared network, server and purchasing checks

- Wire the server and four mini PCs to the shop network: at least five Ethernet connections, plus capacity for the printer, office workstation, uplink and other equipment. Printer connectivity depends on the selected model. No tablet Wi-Fi requirement remains in this initial plan.
- Use one shared server address for all browsers. `127.0.0.1` refers to each device itself and cannot connect a station to the current Mac-hosted app. Provide stable addressing/name resolution and configure an appropriate production application service and HTTPS before a network pilot. The Flask development server is for local evaluation only.
- Keep the server running, protect its power with a UPS and schedule consistent database backups to a separate destination. Include the application configuration/session secret in protected recovery procedures; uploaded PDFs and DXFs are stored in SQLite, while external linked documents are not backed up by the app. Test restoration. Deployment and automated backups remain to be implemented.
- Mini PC starting specification for evaluation: N100/N150-class CPU, 8 GB RAM, 128–256 GB SSD, Ethernet, sufficient USB ports and required simultaneous display outputs. This is a proposed baseline, not a benchmarked requirement or selected model. Confirm OS support, included RAM/storage/OS, power recovery and remote support before purchase.
- Server sizing, OS, service management, backup tooling, printer model/media and exact screen sizes remain open. Choose these after inventory and a representative pilot; no server capacity or unattended-operation validation is claimed yet.
- Read-only TV sessions must recover safely from expiry, restart and network loss. Test the existing NOT LIVE/STALE indication and establish a sign-in/recovery procedure; unattended session management is not yet provisioned.

## One-laser pilot acceptance

Before buying the remaining equipment, connect one mini PC to one TV and desk monitor, the shared test server and the shop printer. Use non-production test jobs.

1. Upload a PDF and DXF from the planner workstation, create a job with the matching revision, assign Laser 1 and release it. Verify the correct station board and whole-shop board show it with the expected HOT order.
2. Keep the TV board visible while an operator signs in/out on the desk. Verify independent sessions, extended desktop placement and individual attribution when a covering operator completes work.
3. Print/scan a batch traveler and two distinct serial travelers; verify readable PN/revision/identity and barcode output using the printer procedure below. Print a representative drawing and check scale, page size and legibility for its intended use.
4. Download the DXF, copy it to USB and confirm the intended file opens in the laser software. Confirm the correct PN/revision before any authorized machine operation.
5. Record progress and independent inspection, including a hold case; confirm station/whole-shop boards update and independent serial progress remains correct.
6. Run through a normal shift, then test network loss, expired sessions and reboot recovery. Verify stale warnings, correct screens, browser sessions and server backup restoration before broader rollout.

Record hardware models, OS/browser versions, display resolutions, printer settings, observed results and operator sign-off. The existing software tests do not substitute for these physical checks.

## Minimum evaluation setup

| Equipment | Quantity basis | Required capability / purpose |
|---|---|---|
| Application host | One evaluation machine | Python 3.12+, supported browser, persistent local storage; runs app and SQLite locally |
| Planning/print workstation | One; can share evaluation host | Keyboard, browser, printer driver; creates ticket and prints full traveler |
| Production terminal | One per concurrently active station in a later pilot | Browser, reliable network, individual login; readable instructions and scanner input |
| Barcode scanner | One per scanning station | USB HID/keyboard-wedge mode, Code 128 support, configurable Enter suffix; target OS/browser compatibility |
| Traveler printer | One per print point | Legible text plus crisp Code 128 at 100% size; current MVP prints a full page through browser |
| Traveler media and holder | One per active batch or independent serial unit, plus replacements | Paper and durable clear sleeve/attachment compatible with the material container and environment |
| Independent inspection station | One per concurrent inspector; may share a terminal sequentially | Separate individual inspector account; access to acceptance criteria and the material |
| Inspection tools | Per product/control plan | Calibrated tools required by the approved drawing/inspection process; not selected by this software |
| Network and protected power | Per networked pilot layout | Reliable connectivity, managed access, recoverable host power and backup storage |

The local demo can use one computer, one printer and one scanner, with users signing out between roles. A separate physical inspection terminal is useful for workflow, but independence is enforced by the inspector’s identity and production history, not the device.

## Traveler and material handling

- A batch traveler identifies a whole batch; an independent serial traveler identifies exactly one unit. Attach the appropriate traveler to the batch carrier or individual unit so it travels at every handoff. Serial ticket cover sheets do not replace unit travelers.
- Print PN, revision, quantity, stable JobRunner ID, barcode, route and instructions. The barcode contains the ID only; no credentials or production instructions are encoded.
- Scan at each receiving station and review the live next operation. Printed status cannot authorize release.
- Do not split a batch under one traveler in this slice. Controlled parent/child travelers and quantity reconciliation must be designed before split lots.
- Reprint uses the same ID. The application records a request but cannot verify that paper emerged or that an old copy was removed; the shop procedure must control duplicates.
- A failed inspection requires physical segregation/hold identification under the approved quality process. The MVP blocks further software advancement but cannot physically quarantine material.

## Printer/scanner acceptance procedure

1. Create a non-production test ticket, then print its traveler at 100% browser scale. Avoid cropping or scaling the barcode into a small label. This release has no thermal-label/ZPL driver.
2. Scan the paper into a plain input and verify the decoded value exactly matches its `JR-…` ID, including capitalization and hyphens; verify one Enter suffix.
3. Scan into ProductionRunner. Verify the exact PN/revision/quantity and current next step. Test unknown and damaged codes and manual entry fallback.
4. Repeat under expected station distance, lighting, gloves, sleeve glare and material-container placement. Confirm the quiet zones remain clear.
5. Confirm long routes/instructions print across pages legibly; keep the complete traveler together. Reprint and verify the same identity.
6. Verify sign-out/sign-in at shared stations, unauthorized-action denials, independent inspection denial for a production contributor and fail/hold behavior.
7. Document device models/configuration, printer media, observed scan results and operator sign-off before a pilot.

SVG generation is software-tested; physical barcode decoding and environmental durability are **not yet validated**. Compact industrial unit labels (the current unit traveler is a full printable page), mobile camera scanning, offline stations and direct industrial printer integration remain later design work.

For serial qualification, scan two different unit travelers and confirm distinct serial identities, quantity one, independent live progress, stable reprints and correct parent ticket links. Verify a held unit is physically segregated while its siblings retain their own travelers.
