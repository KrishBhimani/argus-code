"""Schema migrations.

Append-only. Never edit a published migration — production users have data
that depends on the exact SQL that already ran. To change the schema, add
a new MIGRATION_N+1 and bump SCHEMA_VERSION in db.py.
"""

MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  agent_version TEXT,
  project_path TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  duration_sec INTEGER,
  total_fresh_input_tokens INTEGER NOT NULL DEFAULT 0,
  total_output_tokens INTEGER NOT NULL DEFAULT 0,
  total_cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  total_cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  total_cost_usd REAL NOT NULL DEFAULT 0,
  primary_model TEXT NOT NULL,
  turn_count INTEGER NOT NULL DEFAULT 0,
  pricing_table_version TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  agent_reported_cost_usd REAL,
  metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent);

CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  timestamp TEXT NOT NULL,
  model TEXT NOT NULL,
  model_raw TEXT NOT NULL,
  fresh_input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  cache_write_5m_tokens INTEGER,
  cache_write_1h_tokens INTEGER,
  tool_calls_count INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, sequence);

CREATE TABLE IF NOT EXISTS file_offsets (
  path TEXT PRIMARY KEY,
  byte_offset INTEGER NOT NULL,
  last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parse_errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file TEXT NOT NULL,
  byte_offset INTEGER NOT NULL,
  reason TEXT NOT NULL,
  raw_line_truncated TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

MIGRATION_002 = """
ALTER TABLE sessions ADD COLUMN started_at_ms INTEGER;
ALTER TABLE sessions ADD COLUMN ended_at_ms INTEGER;
CREATE INDEX IF NOT EXISTS idx_sessions_time ON sessions(project_path, started_at_ms, ended_at_ms);

UPDATE sessions
SET started_at_ms = CAST(strftime('%s', started_at) AS INTEGER) * 1000,
    ended_at_ms = CASE WHEN ended_at IS NULL OR ended_at = '' THEN NULL
                       ELSE CAST(strftime('%s', ended_at) AS INTEGER) * 1000 END
WHERE started_at_ms IS NULL;

CREATE TABLE IF NOT EXISTS tool_calls (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  turn_index INTEGER NOT NULL,
  tool_name TEXT NOT NULL,
  is_error INTEGER NOT NULL DEFAULT 0,
  input_size INTEGER NOT NULL DEFAULT 0,
  subagent_type TEXT,
  timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_name ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_ts ON tool_calls(timestamp);

CREATE TABLE IF NOT EXISTS prompts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp_ms INTEGER NOT NULL,
  project_path TEXT NOT NULL,
  display TEXT NOT NULL,
  pasted_chars INTEGER NOT NULL DEFAULT 0,
  is_slash INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_prompts_ts ON prompts(timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_prompts_project ON prompts(project_path);

CREATE VIRTUAL TABLE IF NOT EXISTS prompts_fts USING fts5(
  display,
  content='prompts',
  content_rowid='id',
  tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS prompts_ai AFTER INSERT ON prompts BEGIN
  INSERT INTO prompts_fts(rowid, display) VALUES (new.id, new.display);
END;
CREATE TRIGGER IF NOT EXISTS prompts_ad AFTER DELETE ON prompts BEGIN
  INSERT INTO prompts_fts(prompts_fts, rowid, display) VALUES('delete', old.id, old.display);
END;
CREATE TRIGGER IF NOT EXISTS prompts_au AFTER UPDATE ON prompts BEGIN
  INSERT INTO prompts_fts(prompts_fts, rowid, display) VALUES('delete', old.id, old.display);
  INSERT INTO prompts_fts(rowid, display) VALUES (new.id, new.display);
END;
"""

MIGRATION_003 = """
CREATE TABLE IF NOT EXISTS transcript_segments (
  rowid INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT UNIQUE NOT NULL,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  timestamp TEXT NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_session ON transcript_segments(session_id);
CREATE INDEX IF NOT EXISTS idx_segments_ts ON transcript_segments(timestamp);

CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
  text,
  content='transcript_segments',
  content_rowid='rowid',
  tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS segments_ai AFTER INSERT ON transcript_segments BEGIN
  INSERT INTO transcript_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS segments_ad AFTER DELETE ON transcript_segments BEGIN
  INSERT INTO transcript_fts(transcript_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS segments_au AFTER UPDATE ON transcript_segments BEGIN
  INSERT INTO transcript_fts(transcript_fts, rowid, text) VALUES('delete', old.rowid, old.text);
  INSERT INTO transcript_fts(rowid, text) VALUES (new.rowid, new.text);
END;
"""

MIGRATION_004 = """
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  detector TEXT NOT NULL,
  dedup_key TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  seen_at TEXT,
  UNIQUE (detector, dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_alerts_unseen ON alerts(seen_at, severity);
CREATE INDEX IF NOT EXISTS idx_alerts_recent ON alerts(last_seen_at DESC);
"""

MIGRATION_005 = """
ALTER TABLE alerts ADD COLUMN resolved_at TEXT;
"""

MIGRATION_006 = """
ALTER TABLE transcript_segments ADD COLUMN tool_use_id TEXT;
CREATE INDEX IF NOT EXISTS idx_segments_tool_use ON transcript_segments(session_id, tool_use_id);
"""

# Turn ids are f"{session_id}:{message.id}", so the message id is the suffix
# after the session prefix. Indexed so fork de-duplication (the same message
# stored under two sessions) is a lookup, not a scan. CREATE INDEX IF NOT
# EXISTS: idempotent, non-destructive.
MIGRATION_007 = """
CREATE INDEX IF NOT EXISTS idx_turns_message ON turns(substr(id, length(session_id) + 2));
"""
