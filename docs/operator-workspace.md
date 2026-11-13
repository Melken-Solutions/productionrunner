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

## Dedicated TV and touch-only operation

Use a separate **display-only** account on each shared TV. Its cookie renews on successful requests, including live board refreshes, so normal daily use and overnight shutdowns do not require another sign-in. It has a one-year inactivity allowance; cookie deletion, explicit sign-out, revoked membership, credential changes or a signing-key change still require sign-in. Interactive accounts retain the existing eight-hour session lifetime. Existing display accounts gain the renewed device session on their next authenticated request; an already expired login must sign in once.

TV columns now shrink to the viewport even with long part numbers and branding. Full-screen mode uses the same responsive layout. If the TV itself crops the browser edges, use its Just Scan / Screen Fit / 1:1 picture setting.

For a released internal operation without a planner-assigned station, open the traveler and choose **Pick up at station**. Your selected station is preselected, and Start records the actual station and person for that traveler. Other stations stop showing that traveler as available while its session is open. Assigned operations retain their planned station. Route order, holds, outside processing and first-piece requirements still apply. First-piece checks require a planned station and independent approval. Planners can explicitly authorize unassigned station pickup when releasing the Job Plan; inactive assigned stations cannot be bypassed.

On completion, select **All N units completed** and a **Verification result**. No quantity or verification result is preconfirmed. Routine checks with no measured values require no typing. If measurements are required, choose measured results or an identified completed check sheet and supply those results/reference. Other verification requires details. A failed check must use stop/hold controls. The selected statement and details are saved with the plan criteria and operator audit trail.

## Ask a remote planner or admin

On the station traveler choose **Ask planner / admin**, choose a predefined message, review its wording, and send. The job, lot/unit, station and sender are attached automatically when opening from station work. Built-in snippets cover drawing, DXF, material, quality, quantity and assistance questions without typing. Choose **Other / type a note** when you need your own message. Planner/admin **Messages** shows the organization inbox; operator Messages shows their requests and the selected station's conversations.

Planners/admins can acknowledge, choose a predefined reply or type an Other message, and mark a conversation resolved. A new reply to a resolved conversation reopens it. The Messages unread count updates approximately every 15 seconds while the app is visible. Open the conversation to read the reply; refresh an already-open conversation to load new replies. Opening it marks the currently displayed messages read for that person. These are in-app alerts only: no email or background notification is sent when the app is closed. Never rely on messaging for emergency shutdown.

Messages are independent of station timers and quality holds. The operator must use Pause, Stop or the existing hold process when production needs to stop. Messages and status changes retain the actual sender and time. Ordinary users cannot edit or erase individual messages.

## Admin view switching

Organization admins have a **Switch view** menu in the standard app header, Shop Floor header and full-screen TV board. It opens Organization Admin, Planning/requests, Production jobs, Shop Floor, TV boards, Packing/dispatch, Shipping, Parts/drawings, Messages and Station reports directly without signing out.

Admin-only accounts can view station queues and open travelers in **Admin overview**. This is read-only for station production: starting, pausing, resuming and confirming work still require the Operator role. Admin accounts that also have Operator retain their normal production controls. The switcher does not change roles or organizations. Dedicated display-only accounts keep their restricted TV navigation.

### Message snippets

Organization Admin → **Message snippets** lets admins add, edit or disable organization-specific messages for new requests, replies or both. Built-in messages remain available. Operators select from the catalog and review the full text before sending. Editing a snippet never changes a message already sent; an open form with old wording must be reloaded before that snippet can be sent. Changes are audited.

New unread messages show a visible notification banner with **Open messages** and **Dismiss**. Dismiss hides the banner without marking messages read; another incoming message shows it again. The unread badge remains until conversations are opened. Notifications check every 15 seconds while the app is visible and immediately when returning to the app. They do not require browser notification permission or interrupt the work form.

### Dedicated TV screen logins

In Organization Admin → People & access, choose **Add TV screen**. Enter a unique screen login name (for example `laser-1-tv`), password and access reason. No person or email is required. Read-only display access is assigned automatically. Use those credentials with the organization on the TV, then choose its station board or whole-shop queue. Create a separate login for each screen so access can be revoked individually.

## Primary station and moving between stations

Admins assign an operator's primary station in **People & access → Manage → Primary station**. Only active stations in the current organization can be chosen. Save a reason; changes are audited and apply on the next sign-in without interrupting current work. Choose “No primary station” to let the operator choose at sign-in. TV accounts use their separate display workflow.

On sign-in the operator's Shop Floor opens the primary station. **Change station** provides large touch buttons for any active station, marks the primary, and includes All stations. The choice lasts for this browser session; signing out and back in returns to the primary. Station choices are not stored in shared browser storage, so another operator does not inherit a previous operator's station. An inactive primary is ignored; an inactive current selection falls back to an active primary or the chooser.

Switching changes the queue, not permissions, planner assignments or running work. Pause or stop work with the existing controls before moving if appropriate. Actual production attribution continues to come from guarded work sessions and completion records, not the user's primary station. Station-specific qualification restrictions are not introduced by this preference.
