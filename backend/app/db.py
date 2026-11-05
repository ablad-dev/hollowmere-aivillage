"""SQLite persistence. The village survives restarts."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    persona       TEXT NOT NULL,
    deployment    TEXT NOT NULL,
    model_label   TEXT NOT NULL DEFAULT '',
    color         TEXT NOT NULL DEFAULT '#ffffff',
    sprite        TEXT NOT NULL DEFAULT '',
    job           TEXT,
    home          TEXT,
    x             INTEGER NOT NULL DEFAULT 0,
    y             INTEGER NOT NULL DEFAULT 0,
    coins         INTEGER NOT NULL DEFAULT 20,
    hunger        REAL NOT NULL DEFAULT 80,
    energy        REAL NOT NULL DEFAULT 80,
    social        REAL NOT NULL DEFAULT 80,
    fun           REAL NOT NULL DEFAULT 80,
    action        TEXT NOT NULL DEFAULT 'idle',
    thought       TEXT NOT NULL DEFAULT '',
    target        TEXT,
    plan_json     TEXT NOT NULL DEFAULT '[]',
    plan_day      INTEGER NOT NULL DEFAULT -1,
    unreflected   REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id      TEXT NOT NULL,
    kind          TEXT NOT NULL,
    content       TEXT NOT NULL,
    importance    REAL NOT NULL DEFAULT 3,
    created_tick  INTEGER NOT NULL,
    created_label TEXT NOT NULL DEFAULT '',
    last_access   INTEGER NOT NULL DEFAULT 0,
    embedding     BLOB,
    embed_src     TEXT NOT NULL DEFAULT 'hash'
);
CREATE INDEX IF NOT EXISTS idx_mem_agent ON memories(agent_id, created_tick DESC);

CREATE TABLE IF NOT EXISTS relationships (
    agent_id   TEXT NOT NULL,
    other_id   TEXT NOT NULL,
    affinity   REAL NOT NULL DEFAULT 0,
    summary    TEXT NOT NULL DEFAULT '',
    encounters INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, other_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tick     INTEGER NOT NULL,
    label    TEXT NOT NULL DEFAULT '',
    a_id     TEXT NOT NULL,
    b_id     TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    lines    TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS chronicle (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    tick  INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    kind  TEXT NOT NULL,
    text  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        _migrate(_conn)
        _conn.commit()
    return _conn


def _migrate(c: sqlite3.Connection) -> None:
    """Add columns introduced after a village was already founded."""
    existing = {r["name"] for r in c.execute("PRAGMA table_info(memories)")}
    if "embed_src" not in existing:
        c.execute("ALTER TABLE memories ADD COLUMN embed_src TEXT NOT NULL DEFAULT 'hash'")


def q(sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
    return conn().execute(sql, params).fetchall()


def q1(sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
    return conn().execute(sql, params).fetchone()


def run(sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
    cur = conn().execute(sql, params)
    conn().commit()
    return cur


def get_state(key: str, default: Any = None) -> Any:
    row = q1("SELECT value FROM state WHERE key = ?", (key,))
    return json.loads(row["value"]) if row else default


def set_state(key: str, value: Any) -> None:
    run(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def reset() -> None:
    c = conn()
    for table in ("agents", "memories", "relationships", "conversations", "chronicle", "state"):
        c.execute(f"DELETE FROM {table}")
    c.commit()
