import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class DownloadStatus(Enum):
    QUEUED = "Queued"
    CONNECTING = "Connecting"
    DOWNLOADING = "Downloading"
    PAUSED = "Paused"
    ASSEMBLING = "Assembling"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELED = "Canceled"


@dataclass
class ChunkState:
    """Represents one byte-range slice of a download, handled by one worker thread."""
    index: int
    start: int
    end: int
    downloaded: int = 0

    @property
    def total(self) -> int:
        return max(self.end - self.start + 1, 0)

    @property
    def remaining(self) -> int:
        return max(self.total - self.downloaded, 0)

    def is_complete(self) -> bool:
        return self.total > 0 and self.downloaded >= self.total


@dataclass
class DownloadTask:
    """Represents a single download job tracked by the engine and persisted to disk."""
    id: str
    url: str
    destination: str          # full path to final assembled file
    file_name: str
    total_size: int = 0        # -1 means unknown (no Content-Length available)
    supports_ranges: bool = False
    thread_count: int = 4
    status: DownloadStatus = DownloadStatus.QUEUED
    chunks: List[ChunkState] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    error_message: str = ""

    @property
    def downloaded_bytes(self) -> int:
        return sum(c.downloaded for c in self.chunks)

    @property
    def progress_percent(self) -> float:
        if self.total_size <= 0:
            return 0.0
        return min(100.0, (self.downloaded_bytes / self.total_size) * 100.0)

    @property
    def temp_dir(self) -> str:
        return os.path.join(os.path.dirname(self.destination) or ".", f".{self.id}.mdparts")

    def chunk_part_path(self, index: int) -> str:
        return os.path.join(self.temp_dir, f"part{index}.tmp")
