"""SQLite registry: grows, camera bindings, settings. Series data lives in InfluxDB, not here."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from typing import Any

from .config import DATA_DIR

DB_PATH = DATA_DIR / "growscope.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS grows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    room TEXT DEFAULT '',
    start_date TEXT NOT NULL,
    flip_date TEXT,
    chop_date TEXT,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS cameras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grow_id INTEGER NOT NULL REFERENCES grows(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL,
    interval_min INTEGER NOT NULL DEFAULT 10,
    lights_entity TEXT DEFAULT '',
    window_start TEXT DEFAULT '',
    window_end TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_capture_ts TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grow_id INTEGER NOT NULL REFERENCES grows(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'note',
    title TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    photo TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grow_id INTEGER NOT NULL REFERENCES grows(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    path TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'upload'
);
"""

# Column additions for databases created by older versions. Errors mean the
# column already exists - fine, ignore.
_MIGRATIONS = (
    "ALTER TABLE grows ADD COLUMN recipe_id INTEGER",
    "ALTER TABLE cameras ADD COLUMN source_url TEXT DEFAULT ''",
    "ALTER TABLE cameras ADD COLUMN watch_dir TEXT DEFAULT ''",
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA)
        for migration in _MIGRATIONS:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # column already there


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "grow"


def _grow_days(row: dict) -> dict:
    today = date.today()
    try:
        start = date.fromisoformat(row["start_date"])
        end = date.fromisoformat(row["chop_date"]) if row.get("chop_date") else today
        row["day"] = max((min(today, end) - start).days + 1, 0)
    except ValueError:
        row["day"] = None
    if row.get("flip_date"):
        try:
            flip = date.fromisoformat(row["flip_date"])
            row["flower_day"] = (today - flip).days + 1 if today >= flip else None
        except ValueError:
            row["flower_day"] = None
    else:
        row["flower_day"] = None
    if row.get("chop_date") and row["status"] == "active":
        row["stage"] = "finished"
    elif row.get("flower_day"):
        row["stage"] = "flower"
    elif row["status"] == "active":
        row["stage"] = "veg"
    else:
        row["stage"] = row["status"]
    row["slug"] = slugify(row["name"])
    return row


def grows(include_archived: bool = True) -> list[dict]:
    with _conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM grows ORDER BY start_date DESC")]
    rows = [_grow_days(r) for r in rows]
    if not include_archived:
        rows = [r for r in rows if r["status"] == "active"]
    return rows


def grow(grow_id: int) -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM grows WHERE id=?", (grow_id,)).fetchone()
    return _grow_days(dict(r)) if r else None


def add_grow(name: str, room: str, start_date: str, flip_date: str | None) -> dict:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO grows (name, room, start_date, flip_date) VALUES (?,?,?,?)",
            (name, room, start_date, flip_date or None))
    return grow(cur.lastrowid)


def update_grow(grow_id: int, fields: dict[str, Any]) -> dict | None:
    allowed = {"name", "room", "start_date", "flip_date", "chop_date", "status", "recipe_id"}
    sets = {k: (v or None) if k in ("flip_date", "chop_date") else v
            for k, v in fields.items() if k in allowed}
    if sets:
        cols = ", ".join(f"{k}=?" for k in sets)
        with _conn() as conn:
            conn.execute(f"UPDATE grows SET {cols} WHERE id=?", (*sets.values(), grow_id))
    return grow(grow_id)


def delete_grow(grow_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM grows WHERE id=?", (grow_id,))


def cameras(grow_id: int | None = None) -> list[dict]:
    q = "SELECT * FROM cameras"
    args: tuple = ()
    if grow_id is not None:
        q += " WHERE grow_id=?"
        args = (grow_id,)
    with _conn() as conn:
        return [dict(r) for r in conn.execute(q, args)]


def camera(camera_id: int) -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM cameras WHERE id=?", (camera_id,)).fetchone()
    return dict(r) if r else None


def add_camera(grow_id: int, entity_id: str, interval_min: int,
               lights_entity: str, window_start: str, window_end: str,
               source_url: str = "", watch_dir: str = "") -> dict:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO cameras (grow_id, entity_id, interval_min, lights_entity, window_start,"
            " window_end, source_url, watch_dir) VALUES (?,?,?,?,?,?,?,?)",
            (grow_id, entity_id, interval_min, lights_entity, window_start, window_end,
             source_url, watch_dir))
    return camera(cur.lastrowid)


def update_camera(camera_id: int, fields: dict[str, Any]) -> dict | None:
    allowed = {"entity_id", "interval_min", "lights_entity", "window_start", "window_end",
               "enabled", "source_url", "watch_dir"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if sets:
        cols = ", ".join(f"{k}=?" for k in sets)
        with _conn() as conn:
            conn.execute(f"UPDATE cameras SET {cols} WHERE id=?", (*sets.values(), camera_id))
    return camera(camera_id)


def delete_camera(camera_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM cameras WHERE id=?", (camera_id,))


def mark_captured(camera_id: int) -> None:
    with _conn() as conn:
        conn.execute("UPDATE cameras SET last_capture_ts=? WHERE id=?",
                     (datetime.now().isoformat(timespec="seconds"), camera_id))


def get_setting(key: str, default: Any = None) -> Any:
    with _conn() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if r is None:
        return default
    try:
        return json.loads(r["value"])
    except ValueError:
        return r["value"]


def set_setting(key: str, value: Any) -> None:
    with _conn() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?,?)"
                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value)))


# --- journal ---

def journal(grow_id: int) -> list[dict]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM journal WHERE grow_id=? ORDER BY ts", (grow_id,))]


def add_journal(grow_id: int, ts: str, kind: str, title: str, note: str = "",
                photo: str = "") -> dict:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO journal (grow_id, ts, kind, title, note, photo) VALUES (?,?,?,?,?,?)",
            (grow_id, ts, kind, title, note, photo))
        r = conn.execute("SELECT * FROM journal WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(r)


def delete_journal(entry_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM journal WHERE id=?", (entry_id,))


# --- recipes ---

def recipes() -> list[dict]:
    with _conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM recipes ORDER BY name")]
    for r in rows:
        r["params"] = json.loads(r["params"] or "{}")
    return rows


def recipe(recipe_id: int) -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
    if not r:
        return None
    out = dict(r)
    out["params"] = json.loads(out["params"] or "{}")
    return out


def save_recipe(name: str, params: dict, recipe_id: int | None = None) -> dict:
    blob = json.dumps(params)
    with _conn() as conn:
        if recipe_id:
            conn.execute("UPDATE recipes SET name=?, params=? WHERE id=?", (name, blob, recipe_id))
        else:
            recipe_id = conn.execute("INSERT INTO recipes (name, params) VALUES (?,?)",
                                     (name, blob)).lastrowid
    return recipe(recipe_id)


def delete_recipe(recipe_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))


# --- photos ---

def photos(grow_id: int) -> list[dict]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM photos WHERE grow_id=? ORDER BY ts", (grow_id,))]


def add_photo(grow_id: int, ts: str, path: str, source: str = "upload") -> dict:
    with _conn() as conn:
        cur = conn.execute("INSERT INTO photos (grow_id, ts, path, source) VALUES (?,?,?,?)",
                           (grow_id, ts, path, source))
        r = conn.execute("SELECT * FROM photos WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(r)


def photo_exists(grow_id: int, path: str) -> bool:
    with _conn() as conn:
        return conn.execute("SELECT 1 FROM photos WHERE grow_id=? AND path=?",
                            (grow_id, path)).fetchone() is not None


def delete_photo(photo_id: int) -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
        conn.execute("DELETE FROM photos WHERE id=?", (photo_id,))
    return dict(r) if r else None
