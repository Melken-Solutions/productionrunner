SET LOCAL search_path TO productionrunner, pg_catalog;
ALTER TABLE part_revisions ADD COLUMN object_key TEXT, ADD COLUMN object_size BIGINT;
ALTER TABLE part_revisions DROP CONSTRAINT part_revisions_check;
ALTER TABLE part_revisions ADD CONSTRAINT part_revision_file_source CHECK (
 (drawing_pdf IS NOT NULL AND drawing_url IS NULL AND sha256 IS NOT NULL AND object_key IS NULL AND object_size IS NULL) OR
 (drawing_pdf IS NULL AND drawing_url IS NOT NULL AND sha256 IS NULL AND object_key IS NULL AND object_size IS NULL) OR
 (drawing_pdf IS NULL AND drawing_url IS NULL AND sha256 IS NOT NULL AND object_key IS NOT NULL AND object_size IS NOT NULL AND object_size>0));
ALTER TABLE part_dxf ADD COLUMN object_key TEXT, ADD COLUMN object_size BIGINT;
ALTER TABLE part_dxf ALTER COLUMN content DROP NOT NULL;
ALTER TABLE part_dxf ADD CONSTRAINT part_dxf_file_source CHECK (
 (content IS NOT NULL AND object_key IS NULL AND object_size IS NULL) OR
 (content IS NULL AND object_key IS NOT NULL AND object_size IS NOT NULL AND object_size>0));
