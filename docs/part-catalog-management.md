# Parts and drawing management

Planners and organization admins open **Parts & drawings**, select a revision, and choose **Edit / replace / remove**.

- Unused revisions: correct PN, revision or description; upload a replacement PDF or add/replace DXF. Empty file inputs keep existing files. Duplicate identities and stale submissions are rejected.
- Referenced revisions: requests (including historical ones), tickets and saved Job Plans protect the identity and files. Create a replacement revision and select/review it on the appropriate request; existing jobs are not retargeted.
- Remove from catalog: hide the entry from new-work lists without deleting historical files. The Removed entries view supports restoration. This is reversible archival, not physical deletion or storage purging.
- New requests cannot manually select removed identities. Existing requests and jobs retain access to their original documents.

Changes record the actual actor, time, reason, before/after metadata and replacement checksums. Database triggers protect referenced revisions and immutable change history; tenant and role checks apply to every management action. Existing retention controls remain separate from catalog removal. Replaced unused cloud objects are not immediately deleted, avoiding data loss on ambiguous transaction failures.
