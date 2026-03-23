"""Тёмная тема Catppuccin Mocha для PySide6."""

# Цветовая палитра Catppuccin Mocha
COLORS = {
    "base": "#1e1e2e",
    "mantle": "#181825",
    "crust": "#11111b",
    "surface0": "#313244",
    "surface1": "#45475a",
    "surface2": "#585b70",
    "overlay0": "#6c7086",
    "text": "#cdd6f4",
    "subtext0": "#a6adc8",
    "subtext1": "#bac2de",
    "green": "#a6e3a1",
    "yellow": "#f9e2af",
    "red": "#f38ba8",
    "blue": "#89b4fa",
    "mauve": "#cba6f7",
    "teal": "#94e2d5",
    "peach": "#fab387",
}

DARK_THEME_QSS = """
/* ===== Global ===== */
QMainWindow, QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "Segoe UI", "Consolas", sans-serif;
    font-size: 13px;
}

/* ===== Menu Bar ===== */
QMenuBar {
    background-color: #11111b;
    color: #cdd6f4;
    border-bottom: 1px solid #313244;
    padding: 2px;
}
QMenuBar::item:selected {
    background-color: #313244;
    border-radius: 3px;
}
QMenu {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
}
QMenu::item:selected {
    background-color: #313244;
}

/* ===== ComboBox ===== */
QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 13px;
}
QComboBox:hover {
    border-color: #89b4fa;
}
QComboBox::drop-down {
    border: none;
    padding-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #181825;
    color: #cdd6f4;
    selection-background-color: #313244;
    border: 1px solid #45475a;
}

/* ===== LineEdit ===== */
QLineEdit {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 13px;
}
QLineEdit:hover {
    border-color: #89b4fa;
}
QLineEdit:focus {
    border-color: #89b4fa;
}

/* ===== Push Buttons ===== */
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 13px;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #89b4fa;
}
QPushButton:pressed {
    background-color: #585b70;
}

/* ===== Table (SMART Attributes) ===== */
QTableWidget {
    background-color: #181825;
    alternate-background-color: #1e1e2e;
    color: #cdd6f4;
    gridline-color: #313244;
    selection-background-color: #45475a;
    selection-color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 4px;
    font-size: 13px;
}
QTableWidget::item {
    padding: 4px 8px;
}
QHeaderView::section {
    background-color: #11111b;
    color: #cdd6f4;
    padding: 6px 8px;
    border: 1px solid #313244;
    font-weight: bold;
    font-size: 13px;
}
QHeaderView::section:hover {
    background-color: #313244;
}

/* ===== Scroll Bars ===== */
QScrollBar:vertical {
    background-color: #181825;
    width: 10px;
    border: none;
}
QScrollBar::handle:vertical {
    background-color: #45475a;
    min-height: 30px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background-color: #585b70;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    background-color: #181825;
    height: 10px;
    border: none;
}
QScrollBar::handle:horizontal {
    background-color: #45475a;
    min-width: 30px;
    border-radius: 5px;
}

/* ===== Labels ===== */
QLabel {
    color: #cdd6f4;
}

/* ===== Group Box ===== */
QGroupBox {
    color: #89b4fa;
    border: 1px solid #313244;
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 16px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}

/* ===== Status Bar ===== */
QStatusBar {
    background-color: #11111b;
    color: #a6adc8;
    border-top: 1px solid #313244;
    font-size: 12px;
}

/* ===== Frame ===== */
QFrame[frameShape="4"] {  /* HLine */
    color: #313244;
}
QFrame[frameShape="5"] {  /* VLine */
    color: #313244;
}

/* ===== ToolTip ===== */
QToolTip {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #45475a;
    padding: 4px 8px;
    font-size: 12px;
}
"""
