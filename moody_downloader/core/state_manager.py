import sqlite3
import threading
from typing import List

from core.models import DownloadTask, ChunkState, DownloadStatus


class StateManager:
    """
    Persists download and chunk progress to a local SQLite database so that
    downloads can be resumed after a pause, crash, or application restart.
    """

    def __init__(self, db_path: str = "moody_downloader.db"):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    total_size INTEGER NOT NULL DEFAULT 0,
                    supports_ranges INTEGER NOT NULL DEFAULT 0,
                    thread_count INTEGER NOT NULL DEFAULT 4,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    error_message TEXT DEFAULT ''
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    download_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    start INTEGER NOT NULL,
                    end INTEGER NOT NULL,
                    downloaded INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (download_id, idx),
                    FOREIGN KEY (download_id) REFERENCES downloads(id) ON DELETE CASCADE
                )
                """
            )
            self._conn.commit()

    def save_download(self, task: DownloadTask):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO downloads
                    (id, url, destination, file_name, total_size, supports_ranges,
                     thread_count, status, created_at, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    total_size=excluded.total_size,
                    supports_ranges=excluded.supports_ranges,
                    status=excluded.status,
                    error_message=excluded.error_message
                """,
                (
                    task.id, task.url, task.destination, task.file_name, task.total_size,
                    int(task.supports_ranges), task.thread_count, task.status.value,
                    task.created_at, task.error_message,
                ),
            )
            for c in task.chunks:
                self._conn.execute(
                    """
                    INSERT INTO chunks (download_id, idx, start, end, downloaded)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(download_id, idx) DO UPDATE SET downloaded=excluded.downloaded
                    """,
                    (task.id, c.index, c.start, c.end, c.downloaded),
                )
            self._conn.commit()

    def update_chunk_progress(self, download_id: str, chunk_index: int, downloaded: int):
        with self._lock:
            self._conn.execute(
                "UPDATE chunks SET downloaded=? WHERE download_id=? AND idx=?",
                (downloaded, download_id, chunk_index),
            )
            self._conn.commit()

    def update_status(self, download_id: str, status: DownloadStatus, error_message: str = ""):
        with self._lock:
            self._conn.execute(
                "UPDATE downloads SET status=?, error_message=? WHERE id=?",
                (status.value, error_message, download_id),
            )
            self._conn.commit()

    def delete_download(self, download_id: str):
        with self._lock:
            self._conn.execute("DELETE FROM chunks WHERE download_id=?", (download_id,))
            self._conn.execute("DELETE FROM downloads WHERE id=?", (download_id,))
            self._conn.commit()

    def load_all(self) -> List[DownloadTask]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM downloads").fetchall()
            tasks = []
            for row in rows:
                (id_, url, destination, file_name, total_size, supports_ranges,
                 thread_count, status, created_at, error_message) = row
                chunk_rows = self._conn.execute(
                    "SELECT idx, start, end, downloaded FROM chunks WHERE download_id=? ORDER BY idx",
                    (id_,),
                ).fetchall()
                chunks = [ChunkState(index=i, start=s, end=e, downloaded=d) for i, s, e, d in chunk_rows]
                tasks.append(
                    DownloadTask(
                        id=id_, url=url, destination=destination, file_name=file_name,
                        total_size=total_size, supports_ranges=bool(supports_ranges),
                        thread_count=thread_count, status=DownloadStatus(status),
                        chunks=chunks, created_at=created_at, error_message=error_message or "",
                    )
                )
            return tasks
