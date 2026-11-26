# Mini PC installation — Windows shop workstations

**Cloud direction update:** Melken is the software provider and customer shops subscribe. See [cloud pilot](cloud-pilot.md). A hosted shop needs no dedicated local app server. Local-server instructions below are an alternative; workstation instructions apply to either hosted or local HTTPS addresses. Existing `melken` development data is not automatically a customer.

Status: installation runbook for the agreed pilot, not a record of hardware already installed. Windows 11 is the proposed client baseline; validate the exact PC, display, printer and scanner combination on one laser station before repeating it. See [hardware layout](hardware.md) and [server installation](server-installation.md).

## Equipment and software inventory

| Device | Screens | Software/configuration |
|---|---|---|
| Laser 1 mini PC | Desk monitor + station TV | Supported Windows 11, current Edge, shop printer driver, USB storage access |
| Laser 2 mini PC | Desk monitor + station TV | Same as Laser 1 |
| Packing mini PC | Desk monitor + station TV | Windows 11, Edge, shop printer driver |
| Whole-shop mini PC | Overview TV | Windows 11, Edge; keyboard/mouse available for setup and recovery |
| Planner/admin office PC | Existing office monitor | Supported browser, shop printer driver, access to source PDFs/DXFs |

**Do not install Python, the repository, Flask or a database on mini PCs.** They use the shared server through a browser. No tablet is required. Existing laser-controller computers and their machine-specific software remain separate. A browser download is not a CAD/toolpath or laser-control application.

Record each PC name, serial number, Windows edition/build, browser version, physical station, display resolutions, network address/reservation and responsible support person. Suggested names: `PR-LASER-1`, `PR-LASER-2`, `PR-PACKING`, `PR-SHOP-TV`.

## 1. Prepare Windows and network access

1. Complete Windows setup using the shop's licensing and account policies. Use a standard Windows account for daily operation and a separate administrator account for installation/support. A shared Windows workstation account does not replace individual ProductionRunner identities.
2. Apply Windows, firmware and graphics/network driver updates from Microsoft or the PC manufacturer. Restart and verify all devices. Configure maintenance windows so updates do not interrupt active production.
3. Connect wired Ethernet. Ask the network administrator for the server's final HTTPS address, trusted certificate setup and DNS resolution. Use the same hostname everywhere; `127.0.0.1` and `localhost` point to the mini PC, not the server.
4. Open the server `/login` page and verify a trusted HTTPS connection. If the server uses an internal CA, have IT install its verified public root certificate in the appropriate Windows trust store. Never distribute the CA private key or click through certificate errors as the installation method.
5. Confirm USB storage is allowed on laser workstations under shop policy. No server administrative access or repository credentials belong on these PCs.

## 2. Configure the displays

1. Connect the TV and desk monitor to independent video outputs. In **Settings → System → Display**, use **Identify** and arrange the displays to match their physical positions.
2. Select **Extend these displays**. Make the desk monitor the primary display. Choose each screen's native resolution and readable scaling; verify the TV has no overscan/cropped edges.
3. Place the operator browser on the desk and the TV browser on the TV. Enable Windows' window-location remembering option if available, then test disconnect/reconnect and restart behavior. Do not assume it always restores the correct screen.
4. Set plugged-in sleep/display timers for the intended shift schedule. Decide workstation locking with the shop administrator: locking a shared dual-screen Windows session also interrupts the TV. Do not disable security policy just to keep the board visible.
5. For the overview PC, configure its single TV at native resolution. Keep a keyboard/mouse accessible for sign-in and recovery.

Microsoft documents [multiple-display setup](https://support.microsoft.com/en-us/windows/hardware/display-graphics/how-to-use-multiple-monitors-in-windows).

## 3. Separate TV and operator browser sessions

Use two Edge profiles on each dual-screen PC, named **ProductionRunner TV** and **ProductionRunner Operator**. Profiles separate cookies and authentication; two ordinary windows in one profile do not. Browser cloud sign-in/sync is not required for the app. Disable automatic profile switching for this site if it causes sessions to open in the wrong profile, and verify actual behavior after browser updates. See [Microsoft's profile instructions](https://support.microsoft.com/en-us/edge/sign-in-and-create-multiple-profiles-in-microsoft-edge).

1. In the TV profile, open the server login and use a dedicated organization account with only the `display` role. Recommended separate accounts: `tv-laser-1`, `tv-laser-2`, `tv-packing`, `tv-shop`. These are display identities, never production actors.
2. Open **TV boards**, choose the intended station or whole-shop board, and bookmark the resulting URL. Use the URL generated by the app; do not assume station IDs or reuse a different organization's URL.
3. Move that window to the TV, then use the board's **Full screen** control. The board refreshes itself; do not add a browser extension to repeatedly reload it.
4. In the Operator profile, bookmark the server `/shop-floor` and `/login` pages. Each operator signs in using their own organization username and password or enrolled PIN. Do not save a shared operator credential or embed any credential in a shortcut.
5. Confirm that signing out of the Operator profile leaves the TV signed in, and vice versa. Verify that the TV account cannot open tickets, documents or Admin, while an authorized operator can open the job and its DXF.
6. Give an inspector access through their own eligible identity; sharing a desk does not waive independent-inspection rules.

For the office PC, use an individual planner and/or local-admin account with the required roles. An admin role alone does not imply planner or operator permission. Upload PDFs/DXFs through **Parts & drawings**; manage organization branding and stations in Admin. The separate developer account is for platform administration, not everyday planning or production.

## 4. Startup and recovery

For the first pilot, use saved profile-specific shortcuts/bookmarks and a documented shift-start sequence: sign into Windows, open the TV profile, authenticate if needed, choose the correct board, move it to the TV, enter full screen, then open the Operator profile on the desk. Create desktop shortcuts through the installed browser's profile/shortcut UI where supported and verify each launches the intended profile. Record any actual profile identifier used in a managed shortcut; do not guess it.

IT can place verified shortcuts in the user's Startup folder (`shell:startup`) to launch after Windows sign-in. This does not automatically log into Windows, restore an expired app session, guarantee screen placement or guarantee full-screen state. Test those separately before claiming unattended startup. Do not configure saved plaintext passwords or automatic operator sign-in.

**Do not use Windows single-app Assigned Access on the shared desk/TV PCs:** operators also need the other browser profile, file manager and printing. A normal desktop with a full-screen TV window is the initial configuration. A separate overview-only PC may later use managed kiosk mode, but Edge kiosk sessions have different persistence/restart behavior and require a tested authentication recovery design. See [Microsoft's kiosk documentation](https://learn.microsoft.com/deployedge/microsoft-edge-configure-kiosk-mode).

The app uses an eight-hour session lifetime with normal Flask session refresh behavior; regular TV requests may renew it. It is neither a permanent device token nor a guaranteed fixed eight-hour sign-out. Browser restarts, cleared cookies, revoked access or other session invalidation can still require sign-in. A NOT LIVE/STALE warning requires recovery, not trusting the last displayed status.

## 5. Install and test the shop printer

1. Add the shared network printer using the manufacturer's supported driver or approved Windows driver. Choose the correct paper size, tray and orientation. Print a Windows test page from each workstation and the office PC.
2. In ProductionRunner, open a test job and **Prepare print / reprint** or **Prepare unit print / reprint**. Use browser Print, select the shop printer, start at 100% scale and disable browser-added headers/footers if they crowd the document.
3. Verify job ID, PN/revision, quantity/serial, complete route/instructions and branding are legible, with no cropped barcode. Scan the physical barcode if scanners will be used.
4. Print a representative drawing from its PDF viewer or external source. Use its required paper size and scale; a traveler print setting is not necessarily suitable for an engineering drawing. Confirm physical scale where required by the work instruction.
5. Check multi-page routes, serial cover sheets versus unit travelers and reprints. The app records a print request, not printer delivery. See [physical qualification](hardware.md#printerscanner-acceptance-procedure).

## 6. Configure DXF-to-USB transfer at both lasers

1. In the Operator profile, enable the browser's download-location prompt or choose a clearly identified local download folder. Use the job's **Download DXF for laser** action and verify PN/revision in the filename and job.
2. Copy the downloaded file to the USB drive using File Explorer. Use the USB format and filename conventions required by the laser controller; do not reformat an existing drive without checking its contents and machine requirements.
3. Safely eject the drive, insert it at the laser and verify the file, units, geometry and setup in the existing machine software. Avoid selecting an older duplicate left on the drive.
4. Follow the shop's cleanup procedure for downloaded/USB copies after use. A cached file does not automatically update when a new part revision is released.
5. Record operation completion in the live job under the actual person's identity. Downloading or copying the DXF never marks production complete.

## 7. Optional barcode scanner

Install a USB HID/keyboard-mode scanner with Code 128 support and one Enter suffix. Focus the scan field, scan a printed traveler and verify the complete `JR-…` or `JR-U-…` identity. Confirm manual entry still works. No camera-scanning or special scanner middleware is supplied by ProductionRunner.

## Acceptance and troubleshooting

| Check or symptom | Action / required result |
|---|---|
| TV and desk show the same thing | Select extended desktop instead of duplication |
| Desk sign-out logs out TV | Correct the browser profile separation |
| TV opens on the desk after restart | Reposition it; correct startup/profile/display configuration and retest |
| Certificate error | IT checks hostname, clock and trust chain; do not bypass it |
| Server cannot be reached | Check Ethernet, DNS, server service and firewall; do not substitute localhost |
| Login returns 429 | Stop retries; shared reverse-proxy rate limiting may affect other stations; contact support |
| TV says NOT LIVE/STALE | Restore network/session; verify a new successful update timestamp |
| Missing DXF | Planner/admin checks exact PN/revision attachment; a display account cannot download it |
| Printer crops barcode/drawing | Check paper, driver, scale and margins; repeat physical qualification |
| USB rejected by laser | Follow controller's documented storage/format requirements |

Before repeating the installation, run a complete non-production job through upload, station assignment/release, TV display, operator sign-in, print, USB transfer, completion and independent inspection. Include one covering operator, a held job and two independently tracked serial units. Test a normal shift, reboot, sign-out, network interruption and recovery. Record results and sign-off; do not infer success from software tests alone.
