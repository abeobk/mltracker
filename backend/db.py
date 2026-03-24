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
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    google_id  TEXT UNIQUE NOT NULL,
    email      TEXT NOT NULL,
    name       TEXT,
    picture    TEXT,
    api_key    TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch('now'))
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

CREATE TABLE IF NOT EXISTS metrics (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step   INTEGER NOT NULL,
    key    TEXT NOT NULL,
    value  REAL NOT NULL,
    ts     REAL NOT NULL DEFAULT (unixepoch('now')),
    UNIQUE(run_id, step, key)
);
CREATE INDEX IF NOT EXISTS idx_metrics_run_key ON metrics(run_id, key);

CREATE TABLE IF NOT EXISTS images (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step   INTEGER NOT NULL,
    key    TEXT NOT NULL,
    path   TEXT NOT NULL,
    ts     REAL NOT NULL DEFAULT (unixepoch('now')),
    UNIQUE(run_id, step, key)
);
CREATE INDEX IF NOT EXISTS idx_images_run_key ON images(run_id, key);
"""


def init_db(app):
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        db.commit()
