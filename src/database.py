import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from config import DB_PATH

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: Path | None = None):
        self.db_path = str(db_path or DB_PATH)
        self._conn: sqlite3.Connection | None = None
        self._ensure_tables()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _ensure_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                platform TEXT,
                video_id TEXT,
                title TEXT,
                author TEXT,
                description TEXT,
                duration_seconds INTEGER,
                thumbnail_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subtitles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id_fk INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                source_type TEXT NOT NULL,
                language TEXT NOT NULL,
                start_time REAL,
                end_time REAL,
                text_original TEXT NOT NULL,
                text_translated TEXT,
                annotations_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_subtitles_video ON subtitles(video_id_fk);
        """)
        for col in ("video_summary", "classic_sentences_json", "html_filename", "pipeline_stage"):
            try:
                self.conn.execute(f"ALTER TABLE videos ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def save_video(self, info: dict) -> int:
        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute(
            "SELECT id FROM videos WHERE url = ?", (info["url"],)
        ).fetchone()
        if row:
            self.conn.execute("""
                UPDATE videos SET title=?, author=?, description=?,
                    duration_seconds=?, thumbnail_url=?, updated_at=?,
                    platform=?, video_id=?
                WHERE id=?
            """, (
                info.get("title"), info.get("author"), info.get("description"),
                info.get("duration_seconds"), info.get("thumbnail_url"), now,
                info.get("platform"), info.get("video_id"), row["id"],
            ))
            self.conn.commit()
            logger.info(f"Updated video id={row['id']}: {info.get('title', '')[:40]}")
            return row["id"]

        cur = self.conn.execute("""
            INSERT INTO videos (url, platform, video_id, title, author,
                description, duration_seconds, thumbnail_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            info["url"], info.get("platform"), info.get("video_id"),
            info.get("title"), info.get("author"), info.get("description"),
            info.get("duration_seconds"), info.get("thumbnail_url"), now, now,
        ))
        self.conn.commit()
        logger.info(f"Inserted video id={cur.lastrowid}: {info.get('title', '')[:40]}")
        return cur.lastrowid

    def get_video_by_url(self, url: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM videos WHERE url = ?", (url,)).fetchone()
        return dict(row) if row else None

    def get_video_by_id(self, video_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return dict(row) if row else None

    def save_segments(self, video_id: int, segments: list[dict]) -> int:
        self.conn.execute("DELETE FROM subtitles WHERE video_id_fk = ?", (video_id,))
        count = 0
        for seg in segments:
            self.conn.execute("""
                INSERT INTO subtitles (video_id_fk, source_type, language,
                    start_time, end_time, text_original, text_translated, annotations_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                video_id, seg.get("source_type", "extracted"),
                seg.get("language", "en"), seg.get("start_time"),
                seg.get("end_time"), seg.get("text_original", ""),
                seg.get("text_translated"),
                json.dumps({
                    "word_annotations": seg.get("annotations", []),
                    "phrase_annotations": seg.get("phrase_annotations", []),
                }, ensure_ascii=False),
            ))
            count += 1
        self.conn.commit()
        return count

    def get_segments(self, video_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM subtitles WHERE video_id_fk = ? ORDER BY start_time",
            (video_id,),
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if d.get("annotations_json"):
                parsed = json.loads(d["annotations_json"])
                if isinstance(parsed, dict):
                    d["annotations"] = parsed.get("word_annotations", [])
                    d["phrase_annotations"] = parsed.get("phrase_annotations", [])
                else:
                    d["annotations"] = parsed
                    d["phrase_annotations"] = []
            else:
                d["annotations"] = []
                d["phrase_annotations"] = []
            results.append(d)
        return results

    def get_recent_videos(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM videos ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_video(self, video_id: int, title: str | None = None, url: str | None = None) -> bool:
        updates = []
        params = []
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if url is not None:
            updates.append("url = ?")
            params.append(url)
        if not updates:
            return False
        updates.append("updated_at = datetime('now')")
        params.append(video_id)
        sql = f"UPDATE videos SET {', '.join(updates)} WHERE id = ?"
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.rowcount > 0

    def delete_video(self, video_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def save_video_extras(
        self,
        video_id: int,
        video_summary: str = "",
        classic_sentences: list[dict] | None = None,
    ) -> None:
        csj = json.dumps(classic_sentences, ensure_ascii=False) if classic_sentences else ""
        self.conn.execute(
            "UPDATE videos SET video_summary = ?, classic_sentences_json = ? WHERE id = ?",
            (video_summary, csj, video_id),
        )
        self.conn.commit()

    def get_video_extras(self, video_id: int) -> dict:
        row = self.conn.execute(
            "SELECT video_summary, classic_sentences_json FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if not row:
            return {"video_summary": "", "classic_sentences": []}
        result: dict = {"video_summary": row["video_summary"] or ""}
        csj = row["classic_sentences_json"]
        if csj:
            try:
                result["classic_sentences"] = json.loads(csj)
            except (json.JSONDecodeError, TypeError):
                result["classic_sentences"] = []
        else:
            result["classic_sentences"] = []
        return result

    def save_html_filename(self, video_id: int, filename: str) -> None:
        self.conn.execute(
            "UPDATE videos SET html_filename = ? WHERE id = ?",
            (filename, video_id),
        )
        self.conn.commit()

    def get_html_filename(self, video_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT html_filename FROM videos WHERE id = ?", (video_id,)
        ).fetchone()
        if row and row["html_filename"]:
            return row["html_filename"]
        # Fallback: use video_id column
        row2 = self.conn.execute(
            "SELECT video_id FROM videos WHERE id = ?", (video_id,)
        ).fetchone()
        if row2 and row2["video_id"]:
            return f"{row2['video_id']}.html"
        return None

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.commit()
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception:
                pass
            self._conn.close()
            self._conn = None

    def save_pipeline_stage(self, video_id: int, stage: str) -> None:
        self.conn.execute(
            "UPDATE videos SET pipeline_stage = ? WHERE id = ?",
            (stage, video_id),
        )
        self.conn.commit()

    def get_pipeline_stage(self, video_id: int) -> str:
        row = self.conn.execute(
            "SELECT pipeline_stage FROM videos WHERE id = ?", (video_id,)
        ).fetchone()
        return row["pipeline_stage"] if row and row["pipeline_stage"] else ""
