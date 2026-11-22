"""Teleprompter: big lyrics that move with the beat while you record.

Shows the previous line (dim), the current line with a karaoke wipe that fills as the bar
plays, and the next lines. During the count-in it shows a countdown. Drag it to a second
monitor or a tablet screen in the booth; F11 = full screen, +/- = text size, M = mirror
(for a teleprompter glass), Esc = close.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics, QPen, QLinearGradient, QBrush, QTransform
from PySide6.QtWidgets import QWidget

from . import theme
from ..core import lyrics as L


class Teleprompter(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent, Qt.Window)
        self.state = state
        self.setWindowTitle("SIB Teleprompter")
        self.resize(1100, 560)
        self.setStyleSheet("background: #050507;")
        self.times = []          # [(start, end, LyricLine)]
        self.pos = 0
        self.countdown = None    # seconds left in count-in
        self.recording = False
        self.mirror = False
        self.on_top = False
        self.font_size = 34
        self.show_next = 2
        self.key_handler = None   # main window hook for Space / R / T while this window has focus
        self.setFocusPolicy(Qt.StrongFocus)

    def set_lyrics(self, times, font_size=None):
        self.times = times
        if font_size:
            self.font_size = int(font_size)
        self.update()

    def set_position(self, pos, recording=False, countdown=None):
        self.pos = pos
        self.recording = recording
        self.countdown = countdown
        self.update()

    def current_index(self):
        idx = -1
        for i, (s, e, ln) in enumerate(self.times):
            if s <= self.pos:
                idx = i
            else:
                break
        return idx

    # ------------------------------------------------------------ input
    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Plus, Qt.Key_Equal):
            self.font_size = min(120, self.font_size + 4)
        elif k == Qt.Key_Minus:
            self.font_size = max(14, self.font_size - 4)
        elif k == Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif k == Qt.Key_M:
            self.mirror = not self.mirror
        elif k == Qt.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.close()
        else:
            # transport keys (Space, R, T) go back to the studio
            if self.key_handler is not None and self.key_handler(e):
                return
            super().keyPressEvent(e)
            return
        if self.state.project:
            self.state.project.lyrics["font_size"] = self.font_size
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def wheelEvent(self, e):
        self.font_size = max(14, min(120, self.font_size + (2 if e.angleDelta().y() > 0 else -2)))
        self.update()

    # ------------------------------------------------------------ paint
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#050507"))
        if self.mirror:
            p.setTransform(QTransform(-1, 0, 0, 1, w, 0))
        proj = self.state.project
        # status strip
        p.setFont(QFont("Segoe UI", 11, QFont.Bold))
        if self.recording:
            p.setPen(QColor(theme.RED))
            p.drawText(QRectF(16, 8, 300, 24), Qt.AlignLeft | Qt.AlignVCenter, "● REC")
        if proj:
            bar, beat = proj.bar_beat(max(0, self.pos))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(w - 316, 8, 300, 24), Qt.AlignRight | Qt.AlignVCenter,
                       f"bar {bar}.{int(beat)}   ·   {proj.bpm:.0f} BPM")
            # beat flashes
            for i in range(proj.time_sig):
                on = int(beat) - 1 == i
                p.setBrush(QColor(theme.BLUE if on and i else (theme.RED if on else "#22222b")))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(w / 2 - (proj.time_sig - 1) * 14 + i * 28, 20), 7, 7)
        if self.countdown is not None and self.countdown > 0:
            p.setFont(QFont("Segoe UI", max(60, self.font_size * 3), QFont.Black))
            p.setPen(QColor(theme.RED))
            p.drawText(self.rect(), Qt.AlignCenter, str(int(self.countdown) + 1))
            p.end()
            return
        if not self.times:
            p.setFont(QFont("Segoe UI", 18))
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Write lyrics in the Lyrics tab\n(one line per bar)")
            p.end()
            return
        idx = self.current_index()
        big = QFont("Segoe UI", self.font_size, QFont.Bold)
        small = QFont("Segoe UI", max(12, int(self.font_size * 0.62)), QFont.DemiBold)
        fm_big = QFontMetrics(big)
        fm_small = QFontMetrics(small)
        cy = h * 0.42
        margin = 40
        # section label
        cur = self.times[idx][2] if idx >= 0 else self.times[0][2]
        if cur.section:
            p.setFont(QFont("Segoe UI", 12, QFont.Bold))
            p.setPen(QColor(theme.BLUE))
            p.drawText(QRectF(0, 40, w, 24), Qt.AlignHCenter, cur.section.upper())
        # previous line
        if idx >= 1:
            p.setFont(small)
            p.setPen(QColor(90, 90, 104))
            p.drawText(QRectF(margin, cy - fm_big.height() * 1.25 - fm_small.height(), w - 2 * margin, fm_small.height() * 1.2),
                       Qt.AlignHCenter | Qt.TextWordWrap, self.times[idx - 1][2].text)
        # current (or upcoming first) line with karaoke wipe
        if idx >= 0:
            s, e, ln = self.times[idx]
            frac = min(1.0, max(0.0, (self.pos - s) / max(e - s, 1)))
            text = ln.text
        else:
            s, e, ln = self.times[0]
            frac = 0.0
            text = ln.text
            if proj:
                secs = (s - self.pos) / proj.sr
                p.setFont(QFont("Segoe UI", 13))
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRectF(0, cy - fm_big.height() * 1.6, w, 24), Qt.AlignHCenter, f"starts in {secs:.1f}s")
        rect = QRectF(margin, cy - fm_big.height() * 0.6, w - 2 * margin, fm_big.height() * 2.6)
        p.setFont(big)
        p.setPen(QColor(235, 235, 242))
        p.drawText(rect, Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, text)
        # wipe: draw the same text again in colour, clipped to the progress
        br = p.boundingRect(rect, Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, text)
        if frac > 0:
            p.save()
            p.setClipRect(QRectF(br.left(), br.top(), br.width() * frac, br.height()))
            g = QLinearGradient(br.left(), 0, br.right(), 0)
            g.setColorAt(0, QColor(theme.RED))
            g.setColorAt(1, QColor(theme.BLUE))
            p.setPen(QPen(QBrush(g), 1))
            p.drawText(rect, Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, text)
            p.restore()
        # bar progress under the line
        p.fillRect(QRectF(w * 0.2, br.bottom() + 10, w * 0.6, 4), QColor("#1c1c24"))
        p.fillRect(QRectF(w * 0.2, br.bottom() + 10, w * 0.6 * frac, 4), QColor(theme.BLUE))
        # next lines
        y = br.bottom() + 30
        p.setFont(small)
        for k in range(1, self.show_next + 1):
            j = idx + k if idx >= 0 else k
            if j >= len(self.times):
                break
            p.setPen(QColor(170, 170, 184) if k == 1 else QColor(100, 100, 112))
            r2 = QRectF(margin, y, w - 2 * margin, fm_small.height() * 2.4)
            b2 = p.boundingRect(r2, Qt.AlignHCenter | Qt.TextWordWrap, self.times[j][2].text)
            p.drawText(r2, Qt.AlignHCenter | Qt.TextWordWrap, self.times[j][2].text)
            y = b2.bottom() + 8
        p.setFont(QFont("Segoe UI", 9))
        p.setPen(QColor(70, 70, 80))
        p.drawText(QRectF(10, h - 22, w - 20, 18), Qt.AlignLeft,
                   "F11 full screen · +/- text size · M mirror · Esc close")
        p.end()
