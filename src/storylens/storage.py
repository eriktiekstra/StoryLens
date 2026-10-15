"""Local catalog. No source writes; successful stages commit independently."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Catalog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        try:
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported catalog schema {version}; upgrade StoryLens")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS media (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, size INTEGER NOT NULL,
                    first_seen TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paths (
                    path TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media(id),
                    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                    ctime_ns INTEGER NOT NULL, birthtime REAL, last_seen TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS paths_media ON paths(media_id);
                CREATE TABLE IF NOT EXISTS stages (
                    media_id TEXT NOT NULL REFERENCES media(id), stage TEXT NOT NULL,
                    cache_key TEXT NOT NULL, result TEXT NOT NULL, analyzed_at TEXT NOT NULL,
                    PRIMARY KEY(media_id, stage, cache_key));
                CREATE TABLE IF NOT EXISTS failures (
                    media_id TEXT NOT NULL REFERENCES media(id), stage TEXT NOT NULL,
                    error TEXT NOT NULL, attempted_at TEXT NOT NULL,
                    PRIMARY KEY(media_id, stage));
                PRAGMA user_version=1;
            """)
        except BaseException:
            self.connection.close()
            raise

    def close(self):
        self.connection.close()

    def known_path(self, path: Path):
        return self.connection.execute("SELECT * FROM paths WHERE path=?", (str(path),)).fetchone()

    def observe(self, media_id, path, media_kind, info):
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO media VALUES(?,?,?,?)",
                                    (media_id, media_kind, info.st_size, now()))
            self.connection.execute("""INSERT INTO paths VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET media_id=excluded.media_id,
                size=excluded.size, mtime_ns=excluded.mtime_ns, ctime_ns=excluded.ctime_ns,
                birthtime=excluded.birthtime, last_seen=excluded.last_seen""",
                (str(path), media_id, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                 getattr(info, "st_birthtime", None), now()))

    def stage_result(self, media_id, stage, key):
        row = self.connection.execute(
            "SELECT result FROM stages WHERE media_id=? AND stage=? AND cache_key=?",
            (media_id, stage, key)).fetchone()
        return json.loads(row["result"]) if row is not None else None

    def save(self, media_id, stage, key, result):
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO stages VALUES(?,?,?,?,?)",
                                    (media_id, stage, key, json.dumps(result, allow_nan=False), now()))
            self.connection.execute("DELETE FROM failures WHERE media_id=? AND stage=?", (media_id, stage))

    def fail(self, media_id, stage, error):
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO failures VALUES(?,?,?,?)",
                                    (media_id, stage, error, now()))

    def image_results(self, cache_key):
        rows = self.connection.execute(
            """SELECT media.id, stages.result FROM media JOIN stages ON media.id=stages.media_id
               WHERE stage='image_technical' AND cache_key=? AND kind='image'
               AND EXISTS (SELECT 1 FROM paths WHERE paths.media_id=media.id) ORDER BY media.id""",
            (cache_key,))
        for row in rows:
            yield {"id": row["id"], "result": json.loads(row["result"])}

    def exact_duplicates(self, limit=50, after=""):
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self.connection.execute(
            """SELECT media_id FROM paths WHERE media_id > ?
               GROUP BY media_id HAVING COUNT(*)>1 ORDER BY media_id""", (after,))
        items = []
        for row in rows:
            paths = [p["path"] for p in self.connection.execute(
                "SELECT path FROM paths WHERE media_id=? ORDER BY path", (row["media_id"],))
                if Path(p["path"]).is_file() and not Path(p["path"]).is_symlink()]
            if len(paths) > 1:
                items.append({"media_id": row["media_id"], "paths": paths})
                if len(items) > limit:
                    break
        return {"items": items[:limit], "next_cursor": items[limit-1]["media_id"] if len(items)>limit else None}

    def page(self, limit=50, after="", media_kind=None):
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self.connection.execute("""SELECT * FROM media WHERE id > ?
            AND (? IS NULL OR kind=?) ORDER BY id LIMIT ?""", (after, media_kind, media_kind, limit + 1)).fetchall()
        return {"items": [dict(row) for row in rows[:limit]],
                "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def get(self, media_id):
        row = self.connection.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown media ID: " + media_id)
        stages = self.connection.execute("""SELECT stage, cache_key, result, analyzed_at FROM stages
            WHERE media_id=? ORDER BY analyzed_at DESC""", (media_id,)).fetchall()
        # Return latest result per stage; cache history stays in storage, not API payloads.
        latest = {}
        for stage in stages:
            latest.setdefault(stage["stage"], {"cache_key": stage["cache_key"],
                              "analyzed_at": stage["analyzed_at"], "result": json.loads(stage["result"])})
        return {**dict(row), "paths": [dict(x) for x in self.connection.execute(
            "SELECT * FROM paths WHERE media_id=? ORDER BY path", (media_id,))],
            "analysis": latest, "failures": [dict(x) for x in self.connection.execute(
                "SELECT stage,error,attempted_at FROM failures WHERE media_id=?", (media_id,))]}
