from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

STATUSES = (
    "queued", "discovered", "discovering", "needs_approval", "rendering",
    "ready_for_review", "done", "error", "skipped", "ignored",
)

_COLUMNS = (
    "status", "stage", "plan_path", "confidence_note",
    "sermon_url", "shorts_urls", "error",
    "speaker", "title_override", "options",
)


@dataclass
class Run:
    video_id: str
    status: str
    stage: str | None
    plan_path: str | None
    confidence_note: str | None
    sermon_url: str | None
    shorts_urls: list[str]
    error: str | None
    created_at: str
    updated_at: str
    speaker: str | None
    title_override: str | None
    options: dict


def connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            video_id        TEXT PRIMARY KEY,
            status          TEXT NOT NULL,
            stage           TEXT,
            plan_path       TEXT,
            confidence_note TEXT,
            sermon_url      TEXT,
            shorts_urls     TEXT NOT NULL DEFAULT '[]',
            error           TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            speaker         TEXT,
            title_override  TEXT,
            options         TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.commit()
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    have = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    for col, decl in (("speaker", "TEXT"), ("title_override", "TEXT"),
                      ("options", "TEXT NOT NULL DEFAULT '{}'")):
        if col not in have:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {decl}")
    conn.commit()


def upsert_run(conn: sqlite3.Connection, video_id: str, *, now: str,
               status: str | None = None, stage: str | None = None,
               plan_path: str | None = None, confidence_note: str | None = None,
               sermon_url: str | None = None, shorts_urls: list[str] | None = None,
               error: str | None = None, speaker: str | None = None,
               title_override: str | None = None, options: dict | None = None) -> None:
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    values = {
        "status": status, "stage": stage, "plan_path": plan_path,
        "confidence_note": confidence_note, "sermon_url": sermon_url,
        "shorts_urls": None if shorts_urls is None else json.dumps(shorts_urls),
        "error": error, "speaker": speaker, "title_override": title_override,
        "options": None if options is None else json.dumps(options),
    }
    existing = conn.execute("SELECT 1 FROM runs WHERE video_id = ?", (video_id,)).fetchone()
    if existing is None:
        conn.execute(
            """INSERT INTO runs (video_id, status, stage, plan_path, confidence_note,
                                 sermon_url, shorts_urls, error, speaker, title_override,
                                 options, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (video_id, values["status"] or "discovering", values["stage"],
             values["plan_path"], values["confidence_note"], values["sermon_url"],
             values["shorts_urls"] or "[]", values["error"], values["speaker"],
             values["title_override"], values["options"] or "{}", now, now),
        )
    else:
        sets, params = [], []
        for col in _COLUMNS:
            if values[col] is not None:
                sets.append(f"{col} = ?")
                params.append(values[col])
        sets.append("updated_at = ?")
        params.append(now)
        params.append(video_id)
        conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE video_id = ?", params)
    conn.commit()


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        video_id=row["video_id"], status=row["status"], stage=row["stage"],
        plan_path=row["plan_path"], confidence_note=row["confidence_note"],
        sermon_url=row["sermon_url"], shorts_urls=json.loads(row["shorts_urls"]),
        error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
        speaker=row["speaker"], title_override=row["title_override"],
        options=json.loads(row["options"]) if row["options"] else {},
    )


def get_run(conn: sqlite3.Connection, video_id: str) -> Run | None:
    row = conn.execute("SELECT * FROM runs WHERE video_id = ?", (video_id,)).fetchone()
    return _row_to_run(row) if row else None


def list_runs(conn: sqlite3.Connection) -> list[Run]:
    rows = conn.execute("SELECT * FROM runs ORDER BY updated_at DESC").fetchall()
    return [_row_to_run(r) for r in rows]


def ignore_all_discovered(conn: sqlite3.Connection, *, now: str) -> int:
    """Tombstone every currently-`discovered` run as `ignored`; return the count.

    Ignored rows stay in the table, so `watcher.scan_channel`'s
    already-has-a-row check keeps them from being re-discovered on the next scan.
    """
    cur = conn.execute(
        "UPDATE runs SET status='ignored', updated_at=? WHERE status='discovered'",
        (now,))
    conn.commit()
    return cur.rowcount


def enqueue(conn: sqlite3.Connection, video_id: str, *, now: str,
            speaker: str | None = None, title: str | None = None,
            options: dict | None = None) -> None:
    upsert_run(conn, video_id, now=now, status="queued",
               speaker=speaker, title_override=title,
               options=options if options is not None else {}, error="")


def claim_next(conn: sqlite3.Connection, *, now: str) -> "Run | None":
    # Single-worker system: no concurrent claimer, so a plain read-then-write
    # is safe. `connect()` leaves sqlite3's default isolation_level, so an
    # explicit BEGIN IMMEDIATE here can raise "cannot start a transaction
    # within a transaction" depending on connection state; avoid it.
    row = conn.execute(
        "SELECT video_id FROM runs WHERE status='queued' ORDER BY updated_at, video_id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    vid = row["video_id"]
    conn.execute("UPDATE runs SET status='discovering', updated_at=? WHERE video_id=?",
                 (now, vid))
    conn.commit()
    return get_run(conn, vid)
