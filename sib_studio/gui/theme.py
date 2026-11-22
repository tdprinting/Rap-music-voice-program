"""Dark studio theme - red eyes / blue eyes."""

RED = "#e04848"
BLUE = "#3f8cff"
BG = "#121216"
PANEL = "#1b1b22"
PANEL2 = "#23232c"
TEXT = "#e8e8ee"
MUTED = "#8a8a99"
ACCENT = BLUE
GRID = "#2c2c38"

STYLESHEET = f"""
* {{ color: {TEXT}; font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 10pt; }}
QMainWindow, QWidget {{ background: {BG}; }}
QDockWidget::title {{ background: {PANEL}; padding: 6px; font-weight: 600; }}
QMenuBar, QMenu {{ background: {PANEL}; }}
QMenu::item:selected, QMenuBar::item:selected {{ background: {BLUE}; }}
QToolBar {{ background: {PANEL}; border: none; spacing: 4px; padding: 4px; }}
QPushButton, QToolButton {{ background: {PANEL2}; border: 1px solid #33333f; border-radius: 6px; padding: 5px 10px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {BLUE}; }}
QPushButton:checked, QToolButton:checked {{ background: {BLUE}; border-color: {BLUE}; }}
QPushButton#primary {{ background: {BLUE}; border-color: {BLUE}; font-weight: 600; }}
QPushButton#danger, QToolButton#rec:checked {{ background: {RED}; border-color: {RED}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QListWidget, QTreeWidget {{
    background: {PANEL2}; border: 1px solid #33333f; border-radius: 5px; padding: 3px; selection-background-color: {BLUE}; }}
QComboBox QAbstractItemView {{ background: {PANEL2}; }}
QTabWidget::pane {{ border: 1px solid #2a2a35; }}
QTabBar::tab {{ background: {PANEL}; padding: 6px 12px; border-top-left-radius: 6px; border-top-right-radius: 6px; }}
QTabBar::tab:selected {{ background: {PANEL2}; border-bottom: 2px solid {BLUE}; }}
QSlider::groove:horizontal {{ height: 4px; background: #33333f; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {BLUE}; width: 12px; margin: -6px 0; border-radius: 6px; }}
QSlider::groove:vertical {{ width: 4px; background: #33333f; border-radius: 2px; }}
QSlider::handle:vertical {{ background: {BLUE}; height: 12px; margin: 0 -6px; border-radius: 6px; }}
QScrollBar:horizontal, QScrollBar:vertical {{ background: {PANEL}; }}
QScrollBar::handle {{ background: #3a3a48; border-radius: 4px; }}
QGroupBox {{ border: 1px solid #2c2c38; border-radius: 6px; margin-top: 10px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {MUTED}; }}
QStatusBar {{ background: {PANEL}; }}
QLabel#big {{ font-size: 16pt; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; }}
"""
