import os
import time
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QProgressBar, QToolBar, QHeaderView, QSplitter, QMessageBox, QAbstractItemView,
)

from core.engine import DownloadEngine
from core.state_manager import StateManager
from core.models import DownloadStatus
from ui.add_download_dialog import AddDownloadDialog
from ui.thread_monitor import ThreadMonitorWidget
from ui.styles import DARK_STYLESHEET

COLUMNS = ["File Name", "Size", "Progress", "Speed", "ETA", "Status"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Moody Downloader")
        self.resize(1150, 650)
        self.setStyleSheet(DARK_STYLESHEET)

        self.state_manager = StateManager()
        self.engine = DownloadEngine(self.state_manager)
        self.engine.load_persisted_tasks()
        self.engine.signals.status_changed.connect(self._on_status_changed)
        self.engine.signals.task_error.connect(self._on_task_error)
        self.engine.signals.task_added.connect(self._on_task_added)
        self.engine.signals.task_removed.connect(self._on_task_removed)

        self._row_by_task: Dict[str, int] = {}
        self._speed_tracker: Dict[str, Tuple[float, int]] = {}  # task_id -> (timestamp, bytes)

        default_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(default_dir, exist_ok=True)
        self.default_dir = default_dir

        self._build_toolbar()
        self._build_body()

        for task in self.engine.all_tasks():
            self._add_row_for_task(task)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_ui)
        self.refresh_timer.start(500)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        add_action = QAction("+ Add URL", self)
        add_action.triggered.connect(self._open_add_dialog)
        toolbar.addAction(add_action)
        toolbar.addSeparator()

        self.start_action = QAction("Start", self)
        self.start_action.triggered.connect(self._start_selected)
        toolbar.addAction(self.start_action)

        self.pause_action = QAction("Pause", self)
        self.pause_action.triggered.connect(self._pause_selected)
        toolbar.addAction(self.pause_action)

        self.resume_action = QAction("Resume", self)
        self.resume_action.triggered.connect(self._resume_selected)
        toolbar.addAction(self.resume_action)

        self.cancel_action = QAction("Cancel", self)
        self.cancel_action.triggered.connect(self._cancel_selected)
        toolbar.addAction(self.cancel_action)

        self.remove_action = QAction("Remove", self)
        self.remove_action.triggered.connect(self._remove_selected)
        toolbar.addAction(self.remove_action)

    def _build_body(self):
        splitter = QSplitter(Qt.Orientation.Vertical)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.table)

        self.thread_monitor = ThreadMonitorWidget()
        splitter.addWidget(self.thread_monitor)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(splitter)
        self.setCentralWidget(container)

    # ------------------------------------------------------------------
    # Add dialog
    # ------------------------------------------------------------------
    def _open_add_dialog(self):
        dialog = AddDownloadDialog(self.default_dir, self)
        dialog.download_requested.connect(self._on_download_requested)
        dialog.exec()

    def _on_download_requested(self, url, destination, filename, total_size, supports_ranges, thread_count):
        task = self.engine.create_task(url, destination, filename, total_size, supports_ranges, thread_count)
        self.engine.start_task(task.id)

    # ------------------------------------------------------------------
    # Table management
    # ------------------------------------------------------------------
    def _add_row_for_task(self, task):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._row_by_task[task.id] = row

        self.table.setItem(row, 0, QTableWidgetItem(task.file_name))
        self.table.setItem(row, 1, QTableWidgetItem(self._human_size(task.total_size)))

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(task.progress_percent))
        self.table.setCellWidget(row, 2, bar)

        self.table.setItem(row, 3, QTableWidgetItem("—"))
        self.table.setItem(row, 4, QTableWidgetItem("—"))
        self.table.setItem(row, 5, QTableWidgetItem(task.status.value))

    def _row_for_task(self, task_id) -> Optional[int]:
        return self._row_by_task.get(task_id)

    def _selected_task_id(self) -> Optional[str]:
        selected = self.table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        for task_id, r in self._row_by_task.items():
            if r == row:
                return task_id
        return None

    def _on_selection_changed(self):
        task_id = self._selected_task_id()
        task = self.engine.get_task(task_id) if task_id else None
        self.thread_monitor.set_task(task)

    # ------------------------------------------------------------------
    # Engine signal handlers
    # ------------------------------------------------------------------
    def _on_task_added(self, task_id):
        task = self.engine.get_task(task_id)
        if task and task_id not in self._row_by_task:
            self._add_row_for_task(task)

    def _on_task_removed(self, task_id):
        row = self._row_by_task.pop(task_id, None)
        if row is not None:
            self.table.removeRow(row)
            for tid, r in list(self._row_by_task.items()):
                if r > row:
                    self._row_by_task[tid] = r - 1

    def _on_status_changed(self, task_id, status_value):
        row = self._row_for_task(task_id)
        if row is not None:
            self.table.item(row, 5).setText(status_value)

    def _on_task_error(self, task_id, message):
        QMessageBox.warning(self, "Download Error", message)

    # ------------------------------------------------------------------
    # Toolbar controls
    # ------------------------------------------------------------------
    def _start_selected(self):
        task_id = self._selected_task_id()
        if task_id:
            self.engine.start_task(task_id)

    def _pause_selected(self):
        task_id = self._selected_task_id()
        if task_id:
            self.engine.pause_task(task_id)

    def _resume_selected(self):
        task_id = self._selected_task_id()
        if task_id:
            self.engine.resume_task(task_id)

    def _cancel_selected(self):
        task_id = self._selected_task_id()
        if not task_id:
            return
        reply = QMessageBox.question(self, "Cancel Download", "Cancel and delete the partial file?")
        if reply == QMessageBox.StandardButton.Yes:
            self.engine.cancel_task(task_id)

    def _remove_selected(self):
        task_id = self._selected_task_id()
        if task_id:
            self.engine.remove_task(task_id)

    # ------------------------------------------------------------------
    # Periodic refresh (progress, speed, ETA)
    # ------------------------------------------------------------------
    def _refresh_ui(self):
        now = time.time()
        selected_id = self._selected_task_id()

        for task in self.engine.all_tasks():
            row = self._row_for_task(task.id)
            if row is None:
                continue
            downloaded = task.downloaded_bytes

            speed = 0.0
            last = self._speed_tracker.get(task.id)
            if last:
                last_time, last_bytes = last
                dt = now - last_time
                if dt > 0:
                    speed = max(0.0, (downloaded - last_bytes) / dt)
            self._speed_tracker[task.id] = (now, downloaded)

            bar = self.table.cellWidget(row, 2)
            if bar:
                bar.setValue(int(task.progress_percent))

            if task.status == DownloadStatus.DOWNLOADING:
                self.table.item(row, 3).setText(f"{self._human_size(int(speed))}/s")
                if task.total_size > 0 and speed > 1:
                    remaining = max(task.total_size - downloaded, 0)
                    self.table.item(row, 4).setText(self._human_eta(remaining / speed))
                else:
                    self.table.item(row, 4).setText("—")
            else:
                self.table.item(row, 3).setText("—")
                self.table.item(row, 4).setText("—")

            self.table.item(row, 5).setText(task.status.value)

            if selected_id == task.id:
                self.thread_monitor.refresh(task)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _human_size(num: int) -> str:
        if num < 0:
            return "Unknown"
        n = float(num)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if n < 1024.0:
                return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
            n /= 1024.0
        return f"{n:.1f} PB"

    @staticmethod
    def _human_eta(seconds: float) -> str:
        if seconds is None or seconds != seconds or seconds < 0:
            return "—"
        seconds = int(seconds)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"

    def closeEvent(self, event):
        # Gracefully pause active downloads and release worker threads so the
        # process exits promptly; all partial chunk data is preserved on disk
        # and will resume correctly the next time the app is launched.
        self.refresh_timer.stop()
        self.engine.shutdown_all()
        event.accept()
