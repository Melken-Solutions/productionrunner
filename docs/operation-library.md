# Reusable operation library

Organization admins use **Operation library** in Production setup to customize starter defaults or add templates. Templates have a type (production, inspection, packing, shipping), instructions/checks, optional suggested station, first-piece requirement and DXF requirement. Changes create immutable versions with an actor, reason and timestamp. Retiring a template hides it from selection; restoring makes it available again.

Starter defaults cover Laser cut, Deburr — Timesaver, Stamping, independent inspection, packing and shipping. They are starting guidance, not a substitute for the drawing's dimensions, tolerances, tooling and measurement methods. Admins should review them for their shop. Stations remain independently selected; there is no assumption that stamping must run at a station called Bending.

In a request Job Plan, choose an operation template to copy its instructions and acceptance checks into the step. Replacing nonempty text asks for confirmation. For a new request, final-stage checks fill automatically when exactly one active template exists for that stage; otherwise the planner chooses one. Existing saved requests and loaded part-specific Job Plans retain their text. Retired stations are not silently substituted; the planner must choose an active station before approval.

The request stores the copied text and source template version. Template changes do not change released jobs or existing request snapshots. Choosing templates never records actual work, inspection acceptance, quantity confirmation or physical departure. Laser quantity checks refer to the production lot rather than requiring the entire customer order to be completed together.
