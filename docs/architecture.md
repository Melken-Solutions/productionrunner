# Product and software architecture — provisional MVP

## Product boundary

ProductionRunner manages a ticket for one part number and revision. A JobRunner is the physical identity and instruction traveler for that ticket's material. The traveler remains with the material; the live ticket is authoritative for status. Printing does not advance production or authorize release.

A planner establishes quantity, instructions, acceptance criteria and an ordered route. Operators record full-batch operation completion. An independent inspector records evidence and pass/fail. Pass accepts the batch; fail holds it. No endpoint bypasses these transitions.

```mermaid
flowchart LR
    P[Planner: one PN + revision + route] --> T[Ticket / JobRunner identity]
    T --> L[Print traveler; attach to material]
    L --> S[Operator scans at station]
    S --> O[Complete next operation for whole batch]
    O -->|More operations| S
    O -->|All complete| I[Independent inspection]
    I -->|Pass| A[Accepted]
    I -->|Fail| H[Held; no release in MVP]
```

## Implementation and deployment boundary

```mermaid
flowchart LR
    Scanner[USB HID scanner] --> Browser[Authenticated browser]
    Browser -->|HTTP on loopback for evaluation| Flask[Flask application]
    Flask --> DB[(Local SQLite)]
    Flask --> SVG[Code 128 SVG]
    Browser --> Printer[Browser print / traveler]
```

Server-rendered HTML keeps the first workflow small and inspectable. Flask handles sessions, validation, rendering and role checks; python-barcode produces Code 128 SVG; SQLite stores durable records. No external service, AI API or ERP integration is required. This is a proposed evaluation stack, not an assumed PR v1.9 mandate.

For a future station network: browser clients on the shop-floor network reach a centrally managed HTTPS application service; only that service accesses its database. Use managed application processes, restricted database/storage access, individual identity, backups and monitoring. Do not share SQLite via a network filesystem or expose the Flask development server. Decide whether to migrate the database after confirming multi-station load and the approved stack.

## Domain model

| Entity | Responsibility |
|---|---|
| User | Unique username, password hash, explicit role set |
| Ticket | Stable `JR-` identity, single PN/revision, quantity, tracking mode, serial inventory, instructions, state, version, creator/time |
| Operation | Ticket, unique sequence, instructions, completion actor/time |
| Inspection | One final outcome per ticket, inspector, result, evidence/time |
| Event | Ordered audit entry, ticket, actor, action, detail, UTC time |

Serials are inventory identifiers in a JSON list in this slice. They do not have separate routes or production events. A production model supporting individual serialized movement should normalize units and their histories once the specification establishes uniqueness and genealogy rules.

## State and consistency

`ready → in_progress → awaiting_inspection → accepted | held`. A one-step route moves directly from `ready` to `awaiting_inspection` on completion. Ticket creation and route insertion share one transaction. Operation and inspection writes acquire `BEGIN IMMEDIATE`, compare the submitted version to the current ticket version and commit their event in the same transaction. Duplicate/stale forms return HTTP 409; they do not create duplicate events. Operation sequence is checked server-side, not trusted from the form. Inspection checks every completed operation's actor. Held and accepted states have no outgoing workflow actions.

Event updates/deletes are blocked by SQLite triggers. This is application audit history, not cryptographic tamper evidence: a database owner can alter schema or data. Administrative controls, retention and external audit export are production work.

## Permission matrix

| Action | Planner | Operator | Inspector | Viewer | Admin |
|---|---|---|---|---|---|
| View/scan ticket and traveler | Yes | Yes | Yes | Yes | Yes |
| Create ticket/route | Yes | No | No | No | No |
| Request print/reprint | Yes | Yes | No | No | No |
| Complete next operation | No | Yes | No | No | No |
| Inspect completed batch | No | No | Yes, if independent | No | No |
| Bypass inspection / release hold | No | No | No | No | No |

Roles combine explicitly, but independence is based on actor identity. All authenticated users may view/print a traveler through the browser; the print-request action records intent, not a physical print guarantee. User provisioning is an operator-controlled CLI, outside the web role matrix.

## Security and operating limits

Passwords are hashed with Werkzeug; accounts are never seeded. Signed HttpOnly/SameSite session cookies expire after eight hours. Forms use CSRF tokens; templates escape user text; CSP disallows external resources and framing. The session key is generated in ignored `instance/` with restrictive file mode, or supplied through `PR_SECRET_KEY`. Set `PR_HTTPS=1` only behind HTTPS to enable Secure cookies.

Local evaluation still lacks login throttling, centralized identity/MFA, credential reset/revocation, user management/auditing, session revocation, operational alerting and comprehensive backup/restore tooling. Add these before a networked pilot. No background processing, offline writes or ERP credentials are included. During connectivity loss, do not infer authorization from an old paper status.

## Sources used for implementation

- [Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/): session-cookie and CSRF considerations.
- [python-barcode supported formats](https://python-barcode.readthedocs.io/en/stable/supported-formats.html): Code 128 generation and checksum handling.
- [python-barcode writers](https://python-barcode.readthedocs.io/en/stable/writers.html): SVG sizing and quiet-zone options.
