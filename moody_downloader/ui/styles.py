DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1e1f26;
    color: #e6e6e6;
    font-family: 'Segoe UI', sans-serif;
    font-size: 10.5pt;
}
QToolBar {
    background-color: #262832;
    border: none;
    padding: 6px;
    spacing: 8px;
}
QToolBar QToolButton {
    background-color: #33364a;
    color: #e6e6e6;
    padding: 6px 14px;
    border-radius: 6px;
}
QToolBar QToolButton:hover {
    background-color: #4a4e6a;
}
QTableWidget {
    background-color: #23252e;
    gridline-color: #34364240;
    border: 1px solid #34364240;
    border-radius: 6px;
}
QHeaderView::section {
    background-color: #2b2e3a;
    color: #b8bacb;
    padding: 6px;
    border: none;
    font-weight: 600;
}
QTableWidget::item {
    padding: 4px;
}
QTableWidget::item:selected {
    background-color: #3d5afe55;
}
QProgressBar {
    background-color: #2b2e3a;
    border-radius: 4px;
    text-align: center;
    color: #ffffff;
    min-height: 16px;
}
QProgressBar::chunk {
    background-color: #7c4dff;
    border-radius: 4px;
}
QDialog {
    background-color: #1e1f26;
}
QLineEdit, QSpinBox {
    background-color: #2b2e3a;
    border: 1px solid #3d3f4f;
    border-radius: 4px;
    padding: 5px;
    color: #e6e6e6;
}
QPushButton {
    background-color: #3d5afe;
    color: white;
    border-radius: 6px;
    padding: 6px 16px;
}
QPushButton:hover {
    background-color: #536dfe;
}
QPushButton:disabled {
    background-color: #444656;
    color: #8a8c9c;
}
QLabel {
    color: #cfd1e0;
}
QScrollArea {
    border: none;
}
QSplitter::handle {
    background-color: #2b2e3a;
}
"""
