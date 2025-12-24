PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS files (
  file_id            INTEGER PRIMARY KEY AUTOINCREMENT,
  path               TEXT NOT NULL UNIQUE,
  size_bytes         INTEGER NOT NULL,
  mtime_epoch        INTEGER NOT NULL,
  slide_count        INTEGER,
  slide_aspect       TEXT,
  last_scanned_at    INTEGER,
  scan_error         TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime_epoch);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);

CREATE TABLE IF NOT EXISTS pages (
  page_id            INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id            INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
  page_no            INTEGER NOT NULL,
  aspect             TEXT NOT NULL DEFAULT 'unknown',
  source_size_bytes  INTEGER NOT NULL,
  source_mtime_epoch INTEGER NOT NULL,
  created_at         INTEGER NOT NULL,
  UNIQUE(file_id, page_no)
);

CREATE INDEX IF NOT EXISTS idx_pages_file ON pages(file_id);
CREATE INDEX IF NOT EXISTS idx_pages_file_page ON pages(file_id, page_no);

CREATE TABLE IF NOT EXISTS artifacts (
  page_id            INTEGER NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
  kind               TEXT NOT NULL,
  status             TEXT NOT NULL DEFAULT 'missing',
  updated_at         INTEGER NOT NULL,
  params_json        TEXT,
  error_code         TEXT,
  error_message      TEXT,
  attempts           INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(page_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_kind_status ON artifacts(kind, status);
CREATE INDEX IF NOT EXISTS idx_artifacts_page ON artifacts(page_id);

CREATE TABLE IF NOT EXISTS page_text (
  page_id            INTEGER PRIMARY KEY REFERENCES pages(page_id) ON DELETE CASCADE,
  raw_text           TEXT NOT NULL DEFAULT '',
  norm_text          TEXT NOT NULL DEFAULT '',
  text_sig           TEXT NOT NULL DEFAULT '',
  updated_at         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_page_text_sig ON page_text(text_sig);

CREATE TABLE IF NOT EXISTS thumbnails (
  page_id            INTEGER NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
  aspect             TEXT NOT NULL,
  width              INTEGER NOT NULL,
  height             INTEGER NOT NULL,
  image_path         TEXT NOT NULL,
  updated_at         INTEGER NOT NULL,
  PRIMARY KEY(page_id, aspect, width, height)
);

CREATE INDEX IF NOT EXISTS idx_thumbs_page ON thumbnails(page_id);

CREATE TABLE IF NOT EXISTS embedding_cache_text (
  model              TEXT NOT NULL,
  text_sig           TEXT NOT NULL,
  dim                INTEGER NOT NULL,
  vector_blob        BLOB NOT NULL,
  created_at         INTEGER NOT NULL,
  PRIMARY KEY(model, text_sig)
);

CREATE TABLE IF NOT EXISTS page_text_embedding (
  page_id            INTEGER NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
  model              TEXT NOT NULL,
  text_sig           TEXT NOT NULL,
  updated_at         INTEGER NOT NULL,
  PRIMARY KEY(page_id, model),
  FOREIGN KEY(model, text_sig) REFERENCES embedding_cache_text(model, text_sig)
);

CREATE TABLE IF NOT EXISTS page_image_embedding (
  page_id            INTEGER NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
  model              TEXT NOT NULL,
  dim                INTEGER NOT NULL,
  vector_blob        BLOB NOT NULL,
  updated_at         INTEGER NOT NULL,
  PRIMARY KEY(page_id, model)
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_pages
USING fts5(page_id UNINDEXED, norm_text);

CREATE TABLE IF NOT EXISTS jobs (
  job_id             TEXT PRIMARY KEY,
  library_root       TEXT NOT NULL,
  created_at         INTEGER NOT NULL,
  started_at         INTEGER,
  finished_at        INTEGER,
  status             TEXT NOT NULL,
  options_json       TEXT NOT NULL,
  summary_json       TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id            INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id             TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  page_id            INTEGER REFERENCES pages(page_id) ON DELETE CASCADE,
  file_id            INTEGER REFERENCES files(file_id) ON DELETE CASCADE,
  kind               TEXT NOT NULL,
  status             TEXT NOT NULL,
  priority           INTEGER NOT NULL DEFAULT 0,
  depends_on_task_id INTEGER,
  started_at         INTEGER,
  heartbeat_at       INTEGER,
  finished_at        INTEGER,
  progress           REAL NOT NULL DEFAULT 0.0,
  message            TEXT,
  error_code         TEXT,
  error_message      TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_job_status ON tasks(job_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_job_kind ON tasks(job_id, kind);
CREATE INDEX IF NOT EXISTS idx_tasks_page_kind ON tasks(page_id, kind);

CREATE TABLE IF NOT EXISTS events (
  event_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id             TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  ts                 INTEGER NOT NULL,
  seq                INTEGER NOT NULL,
  type               TEXT NOT NULL,
  payload_json       TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_job_seq ON events(job_id, seq);
