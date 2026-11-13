# Permanent job deletion

Admins and planners can open a job ticket, expand **Delete job**, and choose **Review permanent deletion**. The review identifies the JobRunner, part/revision, quantity, state and affected record counts. Choose a reason, enter the displayed JobRunner ID, check the confirmation and submit. A changed job must be reviewed again.

This permanently removes the job, its lots/serial travelers, operation/inspection/rework history, station time records, job-specific fulfillment records, plans and conversations. It disappears from queues, reports and direct URLs. It cannot be restored through the app. Existing database backups retain their normal retention; this is not a backup-erasure feature.

Shared parts, drawings, DXFs, material/station catalogs and saved part templates remain. The original production request remains cancelled with a job-deleted history entry. Security history retains the job identity, actor and reason for the deletion.

Stop active/paused work first. Jobs connected to shipping loads must have their boxes removed from those loads before deletion. Departed-load jobs and linked return/repair jobs cannot be purged through this action because their records also support other workflows. No other job or shared shipment is automatically removed.

## Database behavior

Apply `20261112005000_job_messages.sql` and `20261112010000_job_deletion.sql` before deploying this version. The PostgreSQL purge is a server-only SECURITY DEFINER function with a fixed search path, role/membership-epoch/version validation and explicit tenant-scoped statements. PUBLIC execution is revoked. It uses the existing application write lock. Only the reviewed job's dependency tables have their user triggers suspended within that transaction; foreign keys remain active. Any failure rolls back the deletion and trigger changes. Normal runtime callers still cannot disable triggers or erase history directly.

SQLite uses the same reviewed dependency list under BEGIN IMMEDIATE and restores the original trigger definitions before commit. Failure rolls back records and trigger changes. A new foreign-key dependency omitted from the purge list fails closed. When adding job-owned tables, update the purge scope and add an immutable migration for the PostgreSQL function.
