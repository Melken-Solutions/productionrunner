# Hardware and station architecture — provisional requirements

This is a functional equipment list for evaluating the slice, not an approved purchasing list. No make, model, price or environmental rating is assumed. Select actual equipment after the PR v1.9 source, shop conditions, throughput and existing inventory are available.

## Minimum evaluation setup

| Equipment | Quantity basis | Required capability / purpose |
|---|---|---|
| Application host | One evaluation machine | Python 3.12+, supported browser, persistent local storage; runs app and SQLite locally |
| Planning/print workstation | One; can share evaluation host | Keyboard, browser, printer driver; creates ticket and prints full traveler |
| Production terminal | One per concurrently active station in a later pilot | Browser, reliable network, individual login; readable instructions and scanner input |
| Barcode scanner | One per scanning station | USB HID/keyboard-wedge mode, Code 128 support, configurable Enter suffix; target OS/browser compatibility |
| Traveler printer | One per print point | Legible text plus crisp Code 128 at 100% size; current MVP prints a full page through browser |
| Traveler media and holder | One per active material batch, plus replacements | Paper and durable clear sleeve/attachment compatible with the material container and environment |
| Independent inspection station | One per concurrent inspector; may share a terminal sequentially | Separate individual inspector account; access to acceptance criteria and the material |
| Inspection tools | Per product/control plan | Calibrated tools required by the approved drawing/inspection process; not selected by this software |
| Network and protected power | Per networked pilot layout | Reliable connectivity, managed access, recoverable host power and backup storage |

The local demo can use one computer, one printer and one scanner, with users signing out between roles. A separate physical inspection terminal is useful for workflow, but independence is enforced by the inspector’s identity and production history, not the device.

## Traveler and material handling

- A traveler belongs to one ticket and whole batch. Attach it to the carrier/tote or material bundle so it travels at every handoff.
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

SVG generation is software-tested; physical barcode decoding and environmental durability are **not yet validated**. Individual-unit labels, mobile camera scanning, offline stations and direct industrial printer integration remain later design work.
