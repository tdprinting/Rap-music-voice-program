"""Launch the SIB Studio Mixer desktop app."""
from __future__ import annotations

import os
import sys


def main():
    if os.name == "nt":
        try:  # crisp text on high-DPI Windows displays + own taskbar icon
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SIB.StudioMixer")
        except Exception:
            pass
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from . import APP_NAME
    from .gui.theme import STYLESHEET
    from .gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("SIB")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    icon = os.path.join(os.path.dirname(__file__), "..", "assets", "sib.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    w = MainWindow()
    w.show()
    # open a project passed on the command line (double-clicked .sibproj)
    for a in sys.argv[1:]:
        if a.lower().endswith(".sibproj") and os.path.exists(a):
            w.open_project(a)
            break
    if os.environ.get("SIB_SMOKE_TEST"):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
