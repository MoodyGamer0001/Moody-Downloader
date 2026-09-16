from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar, QScrollArea


class ThreadMonitorWidget(QWidget):
    """Live view of each worker thread's byte range and progress for the selected download."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_task_id = None
        self._bars = {}

        outer = QVBoxLayout(self)
        self.title_label = QLabel("Thread Monitor — no download selected")
        self.title_label.setStyleSheet("font-weight: 600; padding: 4px;")
        outer.addWidget(self.title_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.addStretch(1)
        scroll.setWidget(self.container)
        outer.addWidget(scroll, 1)

    def set_task(self, task):
        self._current_task_id = task.id if task else None
        self._rebuild(task)

    def _rebuild(self, task):
        while self.container_layout.count() > 1:
            item = self.container_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._bars = {}
        if not task:
            self.title_label.setText("Thread Monitor — no download selected")
            return
        self.title_label.setText(f"Thread Monitor — {task.file_name} ({len(task.chunks)} thread(s))")
        for chunk in sorted(task.chunks, key=lambda c: c.index):
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(2, 2, 2, 6)
            label = QLabel(f"Thread #{chunk.index + 1}   bytes {chunk.start:,}–{chunk.end:,}")
            bar = QProgressBar()
            bar.setRange(0, max(chunk.total, 1))
            bar.setValue(min(chunk.downloaded, chunk.total))
            bar.setFormat("%p%")
            row_layout.addWidget(label)
            row_layout.addWidget(bar)
            self.container_layout.insertWidget(self.container_layout.count() - 1, row)
            self._bars[chunk.index] = bar

    def refresh(self, task):
        """Called periodically by the main window's refresh timer."""
        if not task or task.id != self._current_task_id:
            return
        if set(self._bars.keys()) != {c.index for c in task.chunks}:
            self._rebuild(task)
            return
        for chunk in task.chunks:
            bar = self._bars.get(chunk.index)
            if bar:
                bar.setValue(min(chunk.downloaded, chunk.total))
