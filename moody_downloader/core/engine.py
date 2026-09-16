import os
import re
import time
import uuid
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Dict, Optional, List
from urllib.parse import urlparse, unquote

import requests
from PyQt6.QtCore import QObject, pyqtSignal

from core.models import DownloadTask, ChunkState, DownloadStatus
from core.state_manager import StateManager

CHUNK_READ_SIZE = 65536            # 64 KB read size per iter_content block
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2
PROGRESS_PERSIST_INTERVAL = 0.5    # seconds between SQLite writes per chunk
USER_AGENT = "MoodyDownloader/1.0 (+https://moody-downloader.local)"


class EngineSignals(QObject):
    status_changed = pyqtSignal(str, str)   # task_id, status value
    task_error = pyqtSignal(str, str)       # task_id, message
    task_added = pyqtSignal(str)            # task_id
    task_removed = pyqtSignal(str)          # task_id


class DownloadEngine:
    """
    Core multi-threaded HTTP download engine. Fully decoupled from the UI:
    the UI only calls public methods and listens to `signals`, or polls
    `get_task` / `all_tasks` on a timer for byte-level progress.
    """

    def __init__(self, state_manager: StateManager):
        self.state = state_manager
        self.signals = EngineSignals()

        self.tasks: Dict[str, DownloadTask] = {}
        self._executors: Dict[str, ThreadPoolExecutor] = {}
        self._futures: Dict[str, List[Future]] = {}
        self._pause_events: Dict[str, threading.Event] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._persist_locks: Dict[str, threading.Lock] = {}
        self._last_persist: Dict[str, float] = {}
        self._is_running: Dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Probing
    # ------------------------------------------------------------------
    @staticmethod
    def probe_url(url: str, timeout: int = 15):
        """
        Checks whether the server supports byte-range requests and returns the
        total content length. Returns (supports_ranges, total_size, file_name).
        total_size == -1 means the length could not be determined.
        """
        headers = {"User-Agent": USER_AGENT}
        file_name = DownloadEngine._filename_from_url(url)
        supports_ranges = False
        total_size = -1

        try:
            resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
            if resp.status_code < 400:
                if resp.headers.get("Accept-Ranges", "").lower() == "bytes":
                    supports_ranges = True
                content_length = resp.headers.get("Content-Length")
                if content_length is not None:
                    total_size = int(content_length)
                disposition = resp.headers.get("Content-Disposition", "")
                if "filename" in disposition:
                    file_name = DownloadEngine._filename_from_disposition(disposition, file_name)
        except requests.RequestException:
            pass

        # HEAD is sometimes blocked or unreliable; confirm with a 1-byte ranged GET.
        if not supports_ranges or total_size == -1:
            try:
                probe_headers = dict(headers)
                probe_headers["Range"] = "bytes=0-0"
                resp = requests.get(url, headers=probe_headers, timeout=timeout, stream=True)
                content_range = resp.headers.get("Content-Range", "")
                if resp.status_code == 206 and content_range:
                    supports_ranges = True
                    total_str = content_range.split("/")[-1]
                    if total_str.isdigit():
                        total_size = int(total_str)
                elif total_size == -1:
                    cl = resp.headers.get("Content-Length")
                    if cl is not None:
                        total_size = int(cl)
                disposition = resp.headers.get("Content-Disposition", "")
                if "filename" in disposition:
                    file_name = DownloadEngine._filename_from_disposition(disposition, file_name)
                resp.close()
            except requests.RequestException:
                pass

        return supports_ranges, total_size, file_name

    @staticmethod
    def _filename_from_url(url: str) -> str:
        path = urlparse(url).path
        name = unquote(os.path.basename(path))
        return name or "download.bin"

    @staticmethod
    def _filename_from_disposition(disposition: str, fallback: str) -> str:
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition)
        if match:
            return match.group(1).strip()
        return fallback

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------
    def create_task(self, url: str, destination: str, file_name: str,
                     total_size: int, supports_ranges: bool, thread_count: int = 4) -> DownloadTask:
        task_id = uuid.uuid4().hex
        effective_threads = thread_count if (supports_ranges and total_size > 0) else 1
        task = DownloadTask(
            id=task_id, url=url, destination=destination, file_name=file_name,
            total_size=max(total_size, 0) if total_size > 0 else -1,
            supports_ranges=supports_ranges, thread_count=effective_threads,
            status=DownloadStatus.QUEUED,
        )
        task.chunks = self._build_chunks(task)
        self._register_task(task)
        self.state.save_download(task)
        self.signals.task_added.emit(task_id)
        return task

    def _build_chunks(self, task: DownloadTask) -> List[ChunkState]:
        if not task.supports_ranges or task.total_size <= 0:
            # Single-connection fallback (also covers unknown Content-Length).
            end = task.total_size - 1 if task.total_size > 0 else 0
            return [ChunkState(index=0, start=0, end=end)]
        n = max(1, task.thread_count)
        size = task.total_size
        base = size // n
        chunks, start = [], 0
        for i in range(n):
            end = start + base - 1 if i < n - 1 else size - 1
            chunks.append(ChunkState(index=i, start=start, end=end))
            start = end + 1
        return chunks

    def _register_task(self, task: DownloadTask):
        self.tasks[task.id] = task
        self._pause_events[task.id] = threading.Event()
        self._pause_events[task.id].set()  # set == running (not paused)
        self._cancel_events[task.id] = threading.Event()
        self._persist_locks[task.id] = threading.Lock()
        self._last_persist[task.id] = 0.0
        self._is_running[task.id] = False

    def load_persisted_tasks(self):
        """Reloads tasks saved from a previous session so they can be resumed."""
        for task in self.state.load_all():
            if task.status in (DownloadStatus.DOWNLOADING, DownloadStatus.CONNECTING, DownloadStatus.ASSEMBLING):
                task.status = DownloadStatus.PAUSED  # was interrupted mid-flight; resumable
            self._register_task(task)
            self.signals.task_added.emit(task.id)

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        return self.tasks.get(task_id)

    def all_tasks(self) -> List[DownloadTask]:
        return list(self.tasks.values())

    def start_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if not task or task.status == DownloadStatus.COMPLETED:
            return

        os.makedirs(task.temp_dir, exist_ok=True)
        os.makedirs(os.path.dirname(task.destination) or ".", exist_ok=True)

        self._pause_events[task_id].set()
        self._cancel_events[task_id].clear()
        self._is_running[task_id] = True
        task.status = DownloadStatus.CONNECTING
        task.error_message = ""
        self._emit_status(task)

        executor = ThreadPoolExecutor(
            max_workers=max(1, task.thread_count), thread_name_prefix=f"dl-{task_id[:6]}"
        )
        self._executors[task_id] = executor

        futures = [
            executor.submit(self._download_chunk, task, chunk)
            for chunk in task.chunks if not chunk.is_complete()
        ]
        self._futures[task_id] = futures

        if not futures:
            self._finalize_task(task)
            return

        task.status = DownloadStatus.DOWNLOADING
        self._emit_status(task)
        threading.Thread(target=self._watch_task, args=(task_id,), daemon=True).start()

    def _watch_task(self, task_id: str):
        for fut in self._futures.get(task_id, []):
            fut.result()
        self._is_running[task_id] = False

        task = self.tasks.get(task_id)
        if not task:
            return
        if self._cancel_events[task_id].is_set():
            return
        if task.status == DownloadStatus.FAILED:
            return
        if not self._pause_events[task_id].is_set():
            return
        if all(c.is_complete() for c in task.chunks):
            self._finalize_task(task)

    def _finalize_task(self, task: DownloadTask):
        task.status = DownloadStatus.ASSEMBLING
        self._emit_status(task)
        try:
            self._assemble(task)
            task.status = DownloadStatus.COMPLETED
            self.state.update_status(task.id, task.status)
            self._cleanup_temp(task)
        except OSError as exc:
            task.status = DownloadStatus.FAILED
            task.error_message = f"Assembly failed: {exc}"
            self.state.update_status(task.id, task.status, task.error_message)
            self.signals.task_error.emit(task.id, task.error_message)
        self._emit_status(task)

    def _assemble(self, task: DownloadTask):
        """Stitches all downloaded chunk part-files into the final destination file, in order."""
        with open(task.destination, "wb") as dest:
            for chunk in sorted(task.chunks, key=lambda c: c.index):
                part_path = task.chunk_part_path(chunk.index)
                with open(part_path, "rb") as part:
                    shutil.copyfileobj(part, dest, length=1024 * 1024)

    def _cleanup_temp(self, task: DownloadTask):
        shutil.rmtree(task.temp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Worker (runs on a background thread pool)
    # ------------------------------------------------------------------
    def _download_chunk(self, task: DownloadTask, chunk: ChunkState):
        pause_event = self._pause_events[task.id]
        cancel_event = self._cancel_events[task.id]
        part_path = task.chunk_part_path(chunk.index)

        # The on-disk part-file size is the ground truth for how many bytes of this
        # chunk actually exist: writes to disk always happen strictly before the
        # (throttled) DB persist, so the DB value can lag behind reality but never
        # exceed it. Trusting disk size here prevents corrupting the chunk with a
        # misaligned Range request if we resume from a slightly stale DB value
        # (e.g. after a pause, or an unclean shutdown/crash).
        if os.path.exists(part_path):
            on_disk = os.path.getsize(part_path)
            if on_disk > chunk.downloaded:
                chunk.downloaded = min(on_disk, chunk.total)

        attempt = 0
        while attempt <= MAX_RETRIES:
            if cancel_event.is_set():
                return
            try:
                headers = {"User-Agent": USER_AGENT}
                range_start = chunk.start + chunk.downloaded
                if task.supports_ranges and task.total_size > 0:
                    headers["Range"] = f"bytes={range_start}-{chunk.end}"
                mode = "ab" if chunk.downloaded > 0 else "wb"

                with requests.get(task.url, headers=headers, stream=True, timeout=30) as resp:
                    if resp.status_code not in (200, 206):
                        raise requests.RequestException(f"Unexpected HTTP status {resp.status_code}")
                    with open(part_path, mode) as f:
                        for block in resp.iter_content(chunk_size=CHUNK_READ_SIZE):
                            if cancel_event.is_set():
                                return
                            pause_event.wait()  # blocks here while paused, resumes instantly on set()
                            if not block:
                                continue
                            f.write(block)
                            chunk.downloaded += len(block)
                            self._maybe_persist_progress(task, chunk)
                return  # chunk finished successfully
            except requests.RequestException as exc:
                attempt += 1
                if attempt > MAX_RETRIES:
                    task.status = DownloadStatus.FAILED
                    task.error_message = f"Thread {chunk.index + 1} failed: {exc}"
                    self.state.update_status(task.id, task.status, task.error_message)
                    self.signals.task_error.emit(task.id, task.error_message)
                    self._emit_status(task)
                    cancel_event.set()  # stop sibling chunks; the task has failed as a whole
                    return
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    def _maybe_persist_progress(self, task: DownloadTask, chunk: ChunkState):
        now = time.time()
        with self._persist_locks[task.id]:
            if now - self._last_persist.get(task.id, 0.0) >= PROGRESS_PERSIST_INTERVAL or chunk.is_complete():
                self._last_persist[task.id] = now
                self.state.update_chunk_progress(task.id, chunk.index, chunk.downloaded)

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------
    def pause_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if not task or task.status != DownloadStatus.DOWNLOADING:
            return
        self._pause_events[task_id].clear()
        task.status = DownloadStatus.PAUSED
        self.state.update_status(task_id, task.status)
        for chunk in task.chunks:
            self.state.update_chunk_progress(task_id, chunk.index, chunk.downloaded)
        self._emit_status(task)

    def resume_task(self, task_id: str):
        task = self.tasks.get(task_id)
        if not task or task.status != DownloadStatus.PAUSED:
            return
        if self._is_running.get(task_id):
            # Worker threads are alive and simply blocked on the pause event.
            self._pause_events[task_id].set()
            task.status = DownloadStatus.DOWNLOADING
            self.state.update_status(task_id, task.status)
            self._emit_status(task)
        else:
            # No live threads (e.g. resumed after an app restart) - rebuild them.
            # Existing chunk.downloaded offsets are used to build correct Range headers.
            self.start_task(task_id)

    def cancel_task(self, task_id: str, delete_partial: bool = True):
        task = self.tasks.get(task_id)
        if not task:
            return
        self._cancel_events[task_id].set()
        self._pause_events[task_id].set()  # unblock any worker paused mid-wait so it can exit
        executor = self._executors.pop(task_id, None)
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)
        task.status = DownloadStatus.CANCELED
        self._is_running[task_id] = False
        self.state.update_status(task_id, task.status)
        self._emit_status(task)
        if delete_partial:
            self._cleanup_temp(task)
            if os.path.exists(task.destination):
                try:
                    os.remove(task.destination)
                except OSError:
                    pass

    def remove_task(self, task_id: str):
        self.cancel_task(task_id, delete_partial=True)
        self.state.delete_download(task_id)
        self.tasks.pop(task_id, None)
        self.signals.task_removed.emit(task_id)

    def shutdown_all(self):
        """
        Called when the application is closing. Pauses every active download
        (preserving all partial chunk data on disk for a later resume), then
        releases the worker threads so the process can exit cleanly instead of
        hanging on Python's non-daemon ThreadPoolExecutor thread join at exit.
        """
        for task_id, task in list(self.tasks.items()):
            if task.status == DownloadStatus.DOWNLOADING:
                self.pause_task(task_id)

        for task_id in list(self._executors.keys()):
            # Unblock any worker parked in pause_event.wait() so it returns immediately.
            # This does NOT delete files or change status - it purely lets threads exit;
            # the task remains PAUSED with all chunk data intact for the next launch.
            self._cancel_events[task_id].set()
            self._pause_events[task_id].set()

        for task_id, executor in list(self._executors.items()):
            executor.shutdown(wait=True, cancel_futures=True)
        self._executors.clear()

    def _emit_status(self, task: DownloadTask):
        self.signals.status_changed.emit(task.id, task.status.value)
