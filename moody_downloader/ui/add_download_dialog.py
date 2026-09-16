import os
import threading

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QSpinBox, QMessageBox,
)

from core.engine import DownloadEngine


class ProbeSignals(QObject):
    finished = pyqtSignal(bool, int, str)  # supports_ranges, total_size, filename


class AddDownloadDialog(QDialog):
    """Modal dialog: paste a URL, probe server capabilities, choose destination + threads."""

    download_requested = pyqtSignal(str, str, str, int, bool, int)
    # url, destination, filename, total_size, supports_ranges, thread_count

    def __init__(self, default_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Download")
        self.setMinimumWidth(480)
        self.default_dir = default_dir
        self._probe_result = None

        self._probe_signals = ProbeSignals()
        self._probe_signals.finished.connect(self._on_probe_finished)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("File URL:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com/file.zip")
        layout.addWidget(self.url_input)

        fetch_row = QHBoxLayout()
        self.fetch_btn = QPushButton("Fetch Info")
        self.fetch_btn.clicked.connect(self._fetch_info)
        fetch_row.addWidget(self.fetch_btn)
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        fetch_row.addWidget(self.info_label, 1)
        layout.addLayout(fetch_row)

        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Save to:"))
        self.dest_input = QLineEdit(default_dir)
        dest_row.addWidget(self.dest_input, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_destination)
        dest_row.addWidget(browse_btn)
        layout.addLayout(dest_row)

        threads_row = QHBoxLayout()
        threads_row.addWidget(QLabel("Worker Threads:"))
        self.thread_spin = QSpinBox()
        self.thread_spin.setRange(1, 16)
        self.thread_spin.setValue(6)
        threads_row.addWidget(self.thread_spin)
        threads_row.addStretch(1)
        layout.addLayout(threads_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self.start_btn = QPushButton("Start Download")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self.start_btn)
        layout.addLayout(btn_row)

    def _browse_destination(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Destination Folder", self.dest_input.text())
        if directory:
            self.dest_input.setText(directory)

    def _fetch_info(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a URL first.")
            return
        self.fetch_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.info_label.setText("Probing server capabilities...")

        def worker():
            supports_ranges, total_size, filename = DownloadEngine.probe_url(url)
            self._probe_signals.finished.emit(supports_ranges, total_size, filename)

        threading.Thread(target=worker, daemon=True).start()

    def _on_probe_finished(self, supports_ranges: bool, total_size: int, filename: str):
        self.fetch_btn.setEnabled(True)
        self._probe_result = (supports_ranges, total_size, filename)
        size_str = self._human_size(total_size) if total_size > 0 else "Unknown size"
        mode = "Multi-threaded (Range supported)" if supports_ranges else "Single-connection (no Range support)"
        self.info_label.setText(f"{filename} — {size_str} — {mode}")
        self.start_btn.setEnabled(True)
        if not supports_ranges:
            self.thread_spin.setValue(1)
            self.thread_spin.setEnabled(False)
        else:
            self.thread_spin.setEnabled(True)

    @staticmethod
    def _human_size(num: int) -> str:
        n = float(num)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if n < 1024.0:
                return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
            n /= 1024.0
        return f"{n:.1f} PB"

    def _on_start(self):
        url = self.url_input.text().strip()
        directory = self.dest_input.text().strip()
        if not url or not directory:
            QMessageBox.warning(self, "Missing Info", "URL and destination folder are required.")
            return
        if not self._probe_result:
            QMessageBox.warning(self, "Not Probed", "Please click 'Fetch Info' first.")
            return
        supports_ranges, total_size, filename = self._probe_result
        destination = os.path.join(directory, filename)
        if os.path.exists(destination):
            reply = QMessageBox.question(
                self, "File Exists",
                f"'{filename}' already exists in that folder. Overwrite when the download completes?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        thread_count = self.thread_spin.value()
        self.download_requested.emit(url, destination, filename, max(total_size, 0), supports_ranges, thread_count)
        self.accept()
