-- Endmember recalibration batches. New databases bootstrap the same tables from SQLAlchemy metadata.
-- Apply once on an existing installation after backing up.
ALTER TABLE spectral_libraries ADD COLUMN calibration_version INT NOT NULL DEFAULT 1;
ALTER TABLE unmix_runs ADD COLUMN calibration_version INT NOT NULL DEFAULT 1;
ALTER TABLE replication_notes ADD COLUMN result_id INT NULL;

CREATE TABLE calibration_batches (
  id INT PRIMARY KEY AUTO_INCREMENT,
  library_id INT NOT NULL,
  title VARCHAR(160) NOT NULL DEFAULT '',
  version INT NULL,
  base_version INT NOT NULL DEFAULT 1,
  kind VARCHAR(24) NOT NULL DEFAULT 'calibration',
  status VARCHAR(24) NOT NULL DEFAULT 'computing',
  endmembers JSON NOT NULL,
  endmember_hash VARCHAR(64) NOT NULL DEFAULT '',
  item_total INT NOT NULL DEFAULT 0,
  item_completed INT NOT NULL DEFAULT 0,
  item_failed INT NOT NULL DEFAULT 0,
  failure_reason TEXT NOT NULL,
  created_by VARCHAR(100) NOT NULL DEFAULT '',
  created_at VARCHAR(64) NOT NULL,
  published_at VARCHAR(64) NOT NULL DEFAULT '',
  CONSTRAINT uq_batch_library_version UNIQUE (library_id, version),
  CONSTRAINT fk_batch_library FOREIGN KEY (library_id) REFERENCES spectral_libraries(id)
);

CREATE TABLE calibration_items (
  id INT PRIMARY KEY AUTO_INCREMENT,
  batch_id INT NOT NULL,
  run_id INT NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'pending',
  source_hash VARCHAR(64) NOT NULL DEFAULT '',
  result_hash VARCHAR(64) NOT NULL DEFAULT '',
  result JSON NOT NULL,
  changes JSON NOT NULL,
  reused BOOL NOT NULL DEFAULT 0,
  error TEXT NOT NULL,
  created_at VARCHAR(64) NOT NULL,
  updated_at VARCHAR(64) NOT NULL,
  CONSTRAINT uq_item_batch_run UNIQUE (batch_id, run_id),
  CONSTRAINT fk_item_batch FOREIGN KEY (batch_id) REFERENCES calibration_batches(id),
  CONSTRAINT fk_item_run FOREIGN KEY (run_id) REFERENCES unmix_runs(id)
);
CREATE INDEX calibration_item_source_idx ON calibration_items (source_hash, status);

CREATE TABLE run_revisions (
  id INT PRIMARY KEY AUTO_INCREMENT,
  run_id INT NOT NULL,
  batch_id INT NULL,
  kind VARCHAR(24) NOT NULL DEFAULT 'calibration',
  library_version INT NOT NULL DEFAULT 1,
  status VARCHAR(24) NOT NULL DEFAULT 'effective',
  source_hash VARCHAR(64) NOT NULL DEFAULT '',
  input_hash VARCHAR(64) NOT NULL DEFAULT '',
  snapshot JSON NOT NULL,
  result JSON NOT NULL,
  created_at VARCHAR(64) NOT NULL,
  CONSTRAINT uq_revision_batch_run UNIQUE (batch_id, run_id),
  CONSTRAINT fk_revision_run FOREIGN KEY (run_id) REFERENCES unmix_runs(id),
  CONSTRAINT fk_revision_batch FOREIGN KEY (batch_id) REFERENCES calibration_batches(id)
);
CREATE INDEX run_revision_run_status_idx ON run_revisions (run_id, status);
ALTER TABLE replication_notes ADD CONSTRAINT fk_note_result FOREIGN KEY (result_id) REFERENCES run_revisions(id);
