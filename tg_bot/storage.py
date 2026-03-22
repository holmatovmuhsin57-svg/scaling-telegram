import sqlite3
import time
from contextlib import contextmanager

from .config import settings
from .shared import RuntimeConfig, from_json, to_json


DEFAULT_STATS = {
    "total": 0,
    "errors": 0,
    "avg_ms": 0.0,
    "last_ms": 0.0,
    "best_ms": None,
    "worst_ms": None,
    "sum_usdt": 0.0,
    "min_usdt": None,
    "max_usdt": None,
    "last_usdt": None,
    "first_cnt": 0,
    "first_sum": 0.0,
    "seen_usdt": 0,
    "skip_parse": 0,
    "skip_min": 0,
    "skip_max": 0,
    "start_ts": time.time(),
    "last_reply_ts": None,
}


class Storage:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or settings.stats_db_file
        self._init_db()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    running INTEGER NOT NULL,
                    min_limit REAL,
                    max_limit REAL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    orig_id INTEGER NOT NULL,
                    sent_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    dt_ns INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                );

                CREATE INDEX IF NOT EXISTS idx_events_status_id ON events(status, id);

                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO config(id, running, min_limit, max_limit, updated_at) VALUES (1, 0, 15.0, NULL, ?)",
                (time.time(),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO state(key, value) VALUES ('stats', ?)",
                (to_json(DEFAULT_STATS),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO state(key, value) VALUES ('photo', ?)",
                (to_json({"chat_id": None, "msg_id": None}),),
            )

    def load_runtime_config(self) -> RuntimeConfig:
        with self.connect() as conn:
            row = conn.execute("SELECT running, min_limit, max_limit FROM config WHERE id = 1").fetchone()
        return RuntimeConfig(bool(row["running"]), row["min_limit"], row["max_limit"])

    def save_runtime_config(self, cfg: RuntimeConfig):
        with self.connect() as conn:
            conn.execute(
                "UPDATE config SET running = ?, min_limit = ?, max_limit = ?, updated_at = ? WHERE id = 1",
                (1 if cfg.running else 0, cfg.min_limit, cfg.max_limit, time.time()),
            )

    def enqueue_event(self, chat_id: int, orig_id: int, sent_id: int, text: str, dt_ns: int):
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO events(chat_id, orig_id, sent_id, text, dt_ns, created_at, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (chat_id, orig_id, sent_id, text, dt_ns, time.time()),
            )

    def claim_event(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id, chat_id, orig_id, sent_id, text, dt_ns FROM events WHERE status = 'pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            conn.execute("UPDATE events SET status = 'processing' WHERE id = ?", (row["id"],))
            conn.execute("COMMIT")
            return dict(row)

    def ack_event(self, event_id: int):
        with self.connect() as conn:
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))

    def release_event(self, event_id: int):
        with self.connect() as conn:
            conn.execute("UPDATE events SET status = 'pending' WHERE id = ?", (event_id,))

    def load_stats(self) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM state WHERE key = 'stats'").fetchone()
        return from_json(row["value"] if row else None, DEFAULT_STATS.copy())

    def save_stats(self, stats: dict):
        with self.connect() as conn:
            conn.execute("UPDATE state SET value = ? WHERE key = 'stats'", (to_json(stats),))

    def load_photo(self) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM state WHERE key = 'photo'").fetchone()
        return from_json(row["value"] if row else None, {"chat_id": None, "msg_id": None})

    def save_photo(self, photo: dict):
        with self.connect() as conn:
            conn.execute("UPDATE state SET value = ? WHERE key = 'photo'", (to_json(photo),))
