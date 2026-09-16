import sys
import os
import threading
import requests
import yt_dlp
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QProgressBar, QLabel, QComboBox, QCheckBox, QTextEdit
)
from PyQt6.QtCore import pyqtSignal, QObject


class DownloadWorker(QObject):
    progress_signal = pyqtSignal(int, str)
    finished_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def download_file(self, url, output_folder="downloads"):
        """Multi-threaded direct file download"""
        os.makedirs(output_folder, exist_ok=True)
        local_filename = os.path.join(output_folder, url.split('/')[-1].split('?')[0] or "downloaded_file")

        try:
            response = requests.get(url, stream=True)
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0

            with open(local_filename, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = int((downloaded / total_size) * 100)
                            self.progress_signal.emit(percent, f"Downloading: {percent}%")

            self.finished_signal.emit(f"File saved to {local_filename}")
        except Exception as e:
            self.error_signal.emit(str(e))

    def download_stream(self, url, extract_mp3=False, output_folder="downloads"):
        """HLS / MPEG-DASH / Streaming Media & MP3 Downloader"""
        os.makedirs(output_folder, exist_ok=True)

        ydl_opts = {
            'outtmpl': os.path.join(output_folder, '%(title)s.%(ext)s'),
            'progress_hooks': [self._yt_dlp_hook],
        }

        if extract_mp3:
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
        else:
            ydl_opts['format'] = 'bestvideo+bestaudio/best'

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            self.finished_signal.emit("Media processing complete!")
        except Exception as e:
            self.error_signal.emit(str(e))

    def _yt_dlp_hook(self, d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                percent = int((downloaded / total) * 100)
                self.progress_signal.emit(percent, f"Processing Media: {percent}%")


class MoodyDownloader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Moody Downloader - High-Speed Media & File Downloader")
        self.setGeometry(100, 100, 650, 400)
        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        layout = QVBoxLayout()

        # Title Header
        title = QLabel("Moody Downloader")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #2C3E50;")
        layout.addWidget(title)

        # URL Input Field
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste URL here (Direct link, HLS, DASH, Video, or Audio)...")
        layout.addWidget(self.url_input)

        # Download Options
        options_layout = QHBoxLayout()
        self.mode_select = QComboBox()
        self.mode_select.addItems(["Auto-Detect Streaming Media", "Direct File Download"])
        options_layout.addWidget(self.mode_select)

        self.mp3_checkbox = QCheckBox("Extract MP3 Audio Only")
        options_layout.addWidget(self.mp3_checkbox)
        layout.addLayout(options_layout)

        # Action Button
        self.download_btn = QPushButton("Start Download")
        self.download_btn.setStyleSheet("background-color: #27AE60; color: white; font-weight: bold; padding: 8px;")
        self.download_btn.clicked.connect(self.start_download)
        layout.addWidget(self.download_btn)

        # Progress Bar & Logs
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box)

        main_widget.setLayout(layout)
        self.setCentralWidget(main_widget)

    def log(self, text):
        self.log_box.append(text)

    def start_download(self):
        url = self.url_input.text().strip()
        if not url:
            self.log("Please enter a valid URL.")
            return

        self.download_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log(f"Starting job for: {url}")

        worker = DownloadWorker()
        worker.progress_signal.connect(self.update_progress)
        worker.finished_signal.connect(self.download_complete)
        worker.error_signal.connect(self.download_error)

        mode = self.mode_select.currentText()
        extract_mp3 = self.mp3_checkbox.isChecked()

        if mode == "Direct File Download":
            thread = threading.Thread(target=worker.download_file, args=(url,))
        else:
            thread = threading.Thread(target=worker.download_stream, args=(url, extract_mp3))

        thread.daemon = True
        thread.start()

    def update_progress(self, percent, message):
        self.progress_bar.setValue(percent)

    def download_complete(self, msg):
        self.log(f"Success: {msg}")
        self.progress_bar.setValue(100)
        self.download_btn.setEnabled(True)

    def download_error(self, err):
        self.log(f"Error: {err}")
        self.download_btn.setEnabled(True)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MoodyDownloader()
    window.show()
    sys.exit(app.exec())