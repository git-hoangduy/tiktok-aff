"""Tiny SQLite layer (no ORM to keep dependencies small)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Any

from .config import DB_PATH

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    price TEXT DEFAULT '',
    link TEXT DEFAULT '',
    user_info TEXT DEFAULT '',
    category TEXT DEFAULT 'auto',
    analysis_json TEXT,
    analysis_source TEXT,
    status TEXT DEFAULT 'new',
    message TEXT DEFAULT '',
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS product_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    cutout_path TEXT,
    position INTEGER DEFAULT 0,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    style TEXT, duration INTEGER,
    data_json TEXT NOT NULL,
    source TEXT,
    created_at TEXT, updated_at TEXT,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    script_id INTEGER,
    status TEXT DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    message TEXT DEFAULT '',
    config_json TEXT,
    scenes_json TEXT,
    output_path TEXT, thumb_path TEXT, srt_path TEXT,
    duration REAL,
    created_at TEXT, updated_at TEXT,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT,
    FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT, kind TEXT, success INTEGER, note TEXT,
    cost REAL DEFAULT 0,
    created_at TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    with _lock, connect() as c:
        c.executescript(SCHEMA)
        # lightweight migrations for databases created by older versions
        cols = {r[1] for r in c.execute("PRAGMA table_info(products)")}
        if "ref_links" not in cols:
            c.execute("ALTER TABLE products ADD COLUMN ref_links TEXT DEFAULT ''")
        if "last_config" not in cols:  # settings chosen last time, pre-selected in the forms
            c.execute("ALTER TABLE products ADD COLUMN last_config TEXT")
        icols = {r[1] for r in c.execute("PRAGMA table_info(product_images)")}
        for col in ("crop_path", "mode", "reason"):
            if col not in icols:
                c.execute(f"ALTER TABLE product_images ADD COLUMN {col} TEXT")
        ucols = {r[1] for r in c.execute("PRAGMA table_info(api_usage)")}
        for col in ("tokens_in", "tokens_out"):
            if col not in ucols:
                c.execute(f"ALTER TABLE api_usage ADD COLUMN {col} INTEGER DEFAULT 0")
        if "photo_ok" not in icols:
            c.execute("ALTER TABLE product_images ADD COLUMN photo_ok INTEGER DEFAULT 0")
        # Videos interrupted by a restart can never finish — mark them.
        c.execute("UPDATE videos SET status='error', message='Bị gián đoạn do khởi động lại server' "
                  "WHERE status IN ('queued','running')")
        c.execute("UPDATE products SET status='error', message='Bị gián đoạn do khởi động lại server' "
                  "WHERE status IN ('queued','running')")


def query(sql: str, params: tuple | list = ()) -> list[dict]:
    with _lock, connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def one(sql: str, params: tuple | list = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | list = ()) -> int:
    with _lock, connect() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def insert(table: str, **fields: Any) -> int:
    fields = {k: _enc(v) for k, v in fields.items()}
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    return execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(fields.values()))


def update(table: str, row_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields = {k: _enc(v) for k, v in fields.items()}
    sets = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE {table} SET {sets} WHERE id=?", [*fields.values(), row_id])


def _enc(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def loads(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return default
