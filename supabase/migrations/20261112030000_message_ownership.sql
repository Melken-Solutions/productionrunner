SET LOCAL search_path TO productionrunner, pg_catalog;
ALTER TABLE job_threads ADD COLUMN owner_id INTEGER REFERENCES users(id);
