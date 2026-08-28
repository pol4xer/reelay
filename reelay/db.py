import sqlite3
from pathlib import Path


class QueueDB:
    def __init__(self, path):
        self.path = Path(path)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_url TEXT NOT NULL,
                    shortcode TEXT NOT NULL UNIQUE,
                    caption TEXT NOT NULL DEFAULT '',
                    video_path TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'downloading',
                            'queued',
                            'publishing',
                            'published',
                            'failed'
                        )
                    ),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    published_at TEXT,
                    instagram_media_id TEXT,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS jobs_status_id
                ON jobs(status, id);

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

    def get_setting(self, key, default=None):
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default
        finally:
            connection.close()

    def set_setting(self, key, value):
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO settings(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )
            connection.commit()
        finally:
            connection.close()

    def create_job(self, source_url, shortcode, caption):
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO jobs(source_url, shortcode, caption, status)
                VALUES (?, ?, ?, 'downloading')
                """,
                (source_url, shortcode, caption or ""),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def get_job(self, job_id):
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    def get_job_by_shortcode(self, shortcode):
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM jobs WHERE shortcode = ?", (shortcode,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    def list_jobs(self, limit=20):
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def next_queued(self):
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued'
                ORDER BY id
                LIMIT 1
                """
            ).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    def set_downloaded(self, job_id, path):
        return self._update(
            """
            UPDATE jobs
            SET video_path = ?, status = 'queued', error = NULL
            WHERE id = ?
            """,
            (str(path), job_id),
        )

    def set_failed(self, job_id, error):
        return self._update(
            """
            UPDATE jobs SET status = 'failed', error = ? WHERE id = ?
            """,
            (str(error), job_id),
        )

    def mark_downloading(self, job_id):
        return self._update(
            """
            UPDATE jobs
            SET status = 'downloading', error = NULL
            WHERE id = ? AND status = 'failed'
            """,
            (job_id,),
        )

    def mark_publishing(self, job_id):
        return self._update(
            """
            UPDATE jobs
            SET status = 'publishing', error = NULL
            WHERE id = ? AND status = 'queued'
            """,
            (job_id,),
        )

    def mark_published(self, job_id, media_id):
        return self._update(
            """
            UPDATE jobs
            SET status = 'published',
                published_at = CURRENT_TIMESTAMP,
                instagram_media_id = ?,
                error = NULL
            WHERE id = ? AND status = 'publishing'
            """,
            (str(media_id), job_id),
        )

    def retry(self, job_id):
        job = self.get_job(job_id)
        if (
            not job
            or job["status"] != "failed"
            or not job["video_path"]
            or not Path(job["video_path"]).is_file()
        ):
            return False
        return self._update(
            """
            UPDATE jobs
            SET status = 'queued', error = NULL
            WHERE id = ? AND status = 'failed'
            """,
            (job_id,),
        )

    def delete_job(self, job_id):
        return self._update("DELETE FROM jobs WHERE id = ?", (job_id,))

    def set_paused(self, paused):
        self.set_setting("paused", "1" if paused else "0")

    def is_paused(self):
        return self.get_setting("paused", "0") == "1"

    def _update(self, query, parameters):
        connection = self._connect()
        try:
            cursor = connection.execute(query, parameters)
            connection.commit()
            return cursor.rowcount > 0
        finally:
            connection.close()
