import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,
  event_type TEXT NOT NULL,
  peer_id TEXT,
  peer_id_b TEXT,
  ip TEXT,
  relay_uuid TEXT,
  conn_type TEXT,
  detail TEXT,
  raw TEXT
);

-- SQLite trata NULL como distinto en UNIQUE; IFNULL evita duplicados al re-leer logs.
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_dedup ON events (
  source, event_type, ts,
  IFNULL(peer_id, ''), IFNULL(ip, ''), IFNULL(relay_uuid, ''), IFNULL(detail, '')
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  duration_sec INTEGER,
  controlling_id TEXT,
  controlled_id TEXT,
  conn_type TEXT,
  path TEXT,
  relay_uuid TEXT,
  ip_a TEXT,
  ip_b TEXT,
  source TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def db_session(db_path: str):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
