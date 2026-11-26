# Pairing and maintaining TV screens

## ProductionRunner access

Open `/tv/setup` on the dedicated TV browser, using the same HTTPS host as the admin. For staging: `https://productionrunner-staging.onrender.com/tv/setup`.

From another computer, sign in as an organization admin and open **Admin → TV devices**. Enter the code visible on your TV, give the screen a recognizable name, and choose a station or the whole shop. The TV opens its board automatically. Codes expire after ten minutes and renew automatically. Approve only a code from a screen you recognize.

Each pairing creates a dedicated display-only login with an unshared random password. No personal admin account or PIN is saved on the TV. The assigned station is enforced by the server. Layout & text opens the existing screen presets, including rotation, columns, HOT pinning and progress counts.

Keep Chromium's profile and cookies across restarts. Do not use incognito, a temporary profile, cookie clearing on exit, or a profile shared/cloned between Pis. Configure the existing kiosk launch command to open `/tv/setup`; the app redirects a previously paired browser to its assigned board. Active display sessions renew with use, with a one-year cookie lifetime. Revocation, account access changes, clearing cookies, or changing the site's signing secret can require pairing again.

The admin device page supports:

- **Save assignment:** change the screen name or its board.
- **Reload screen:** request a browser-page reload at the next successful feed refresh, normally within 15 seconds. This cannot restart a crashed browser or a Pi.
- **Layout & text:** change the selected display login's presentation settings.
- **Revoke screen access:** invalidate that pairing. The next board request returns the TV to setup. Pair it again to restore access.

Refresh the admin page to update connection status. “Recently connected” means a browser check-in within 45 seconds, not proof of TV panel power or physical visibility. Pending changes remain marked until the newly loaded page checks in. Offline screens receive changes after reconnection. Revocation cannot erase an already displayed offline page until it reconnects.

## Raspberry Pi OS desktop: remote maintenance

On each Pi, install the full Connect client if absent and link it to your authorized Raspberry Pi account:

```sh
sudo apt update
sudo apt install rpi-connect
rpi-connect on
rpi-connect signin
rpi-connect status
```

Open the verification URL on your computer to finish linking. Then use [Raspberry Pi Connect](https://connect.raspberrypi.com/) for remote desktop or shell access. Desktop sharing requires a supported Wayland desktop. Enable desktop automatic login for recovery after reboot; verify both Connect and the kiosk return after a restart. Use a dedicated OS account without customer admin credentials. If connection fails, run `rpi-connect doctor`.

Follow the [official Connect documentation](https://www.raspberrypi.com/documentation/services/connect.html) for supported OS versions, account options, boot behavior and updates. Do not upgrade Connect directly within its own remote shell session; use the documented background update procedure.

## Installation acceptance checks

Before mounting a Pi somewhere inaccessible:

1. Pair it and confirm the correct organization's station and layout.
2. Close and reopen Chromium with the same profile; confirm no password prompt.
3. Reboot the Pi; confirm the desktop, kiosk and remote maintenance access return.
4. From Admin, request a reload and verify its pending status clears.
5. Disconnect the network; confirm the board visibly reports stale data. Reconnect and confirm live updates resume.
6. Revoke access; confirm the screen returns to pairing. Pair again for service.

Connect setup must be performed on each physical Pi. ProductionRunner's device controls do not install or authenticate Raspberry Pi Connect.

## Implementation and database rollout

Apply `20261126175348_tv_device_pairing.sql` with the existing migration runner before deploying application code. The readiness check requires both new tables. Pairing records and devices reside in the private `productionrunner` schema; they have no anonymous Data API grants. Short codes and browser secrets are stored hashed in the database. Approval requires an admin, tenant-scoped active station validation, CSRF and rate limiting. Claiming is browser-bound and single-use under the database write lock. Device management is tenant scoped, version guarded and audited; device validity is checked on each authenticated request.
