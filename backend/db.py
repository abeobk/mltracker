import sqlite3
import os
from flask import g, current_app


def get_db():
    if 'db' not in g:
        path = current_app.config['DB_PATH']
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        g.db = conn
    return g.db


def close_db(exc=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    google_id        TEXT UNIQUE,
    email            TEXT NOT NULL,
    name             TEXT,
    picture          TEXT,
    api_key          TEXT UNIQUE NOT NULL,
    password_hash    TEXT,
    status           TEXT NOT NULL DEFAULT 'pending_approval',
    created_at       REAL NOT NULL DEFAULT (unixepoch('now'))
);

CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch('now')),
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    config      TEXT,
    created_at  REAL NOT NULL DEFAULT (unixepoch('now')),
    finished_at REAL,
    UNIQUE(project_id, name)
);

-- Images are stored as files in mltracker/<project_name>/<run_name>/images/
-- and referenced inline in metrics.jsonl as {"type": "image", "name": "<filename>"}
-- No SQLite table needed for images.
"""

# Columns added after initial deploy — ALTER TABLE is idempotent (catches OperationalError).
_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN password_hash TEXT",
    "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'pending_approval'",
]


def migrate_db(app):
    """Apply incremental schema changes to existing databases."""
    with app.app_context():
        db = get_db()
        for stmt in _MIGRATIONS:
            try:
                db.execute(stmt)
                db.commit()
            except sqlite3.OperationalError:
                pass  # column already exists


def init_db(app):
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        db.commit()
