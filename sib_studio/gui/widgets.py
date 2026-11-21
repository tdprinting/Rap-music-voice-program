"""Reusable widgets: level meter, parameter editor built from effect schemas, EQ curve."""
from __future__ import annotations

import math
import numpy as np
from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient, QPainterPath, QFont, QBrush
from PySide6.QtWidgets import (QWidget, QSlider, QDoubleSpinBox, QSpinBox, QCheckBox, QComboBox,
                               QHBoxLayout, QFormLayout, QLabel, QSizePolicy, QVBoxLayout)

from . import theme
from ..core import dsp


class LevelMeter(QWidget):
    def __init__(self, channels=2, vertical=True, parent=None):
        super().__init__(parent)
        self.channels = channels
        self.vertical = vertical
        self.levels = [0.0] * channels
        self.holds = [0.0] * channels
        if vertical:
            self.setMinimumSize(10 * channels + 4, 60)
        else:
            self.setMinimumSize(60, 8 * channels + 2)
        if vertical:
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            self.setFixedWidth(9 * channels + 4)
        else:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.setFixedHeight(7 * channels + 2)

    def set_levels(self, peaks):
        for i in range(self.channels):
            v = float(peaks[i] if i < len(peaks) else peaks[-1]) if len(peaks) else 0.0
            db = 20 * math.log10(max(v, 1e-6))
            norm = max(0.0, min(1.0, (db + 60) / 60))
            self.levels[i] = max(norm, self.levels[i] * 0.85)
            self.holds[i] = max(norm, self.holds[i] - 0.01)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0b0b0e"))
        w, h = self.width(), self.height()
        for i in range(self.channels):
            lv = self.levels[i]
            if self.vertical:
                bw = (w - 4) / self.channels - 1
                x = 2 + i * (bw + 1)
                g = QLinearGradient(0, h, 0, 0)
                g.setColorAt(0, QColor("#2ec4b6")); g.setColorAt(0.75, QColor("#f0b429")); g.setColorAt(1, QColor(theme.RED))
                p.fillRect(QRectF(x, h - lv * h, bw, lv * h), QBrush(g))
                p.fillRect(QRectF(x, h - self.holds[i] * h, bw, 2), QColor("#ffffff"))
            else:
                bh = (h - 2) / self.channels - 1
                y = 1 + i * (bh + 1)
                g = QLinearGradient(0, 0, w, 0)
                g.setColorAt(0, QColor("#2ec4b6")); g.setColorAt(0.75, QColor("#f0b429")); g.setColorAt(1, QColor(theme.RED))
                p.fillRect(QRectF(0, y, lv * w, bh), QBrush(g))
                p.fillRect(QRectF(self.holds[i] * w, y, 2, bh), QColor("#ffffff"))
        p.end()


class ParamControl(QWidget):
    """One control for an effects.Param (slider+spin / checkbox / combo)."""
    changed = Signal(str, object)

    STEPS = 1000

    def __init__(self, param, value, parent=None):
        super().__init__(parent)
        self.param = param
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.setToolTip(param.tip)
        k = param.kind
        if k == "bool":
            self.w = QCheckBox()
            self.w.setChecked(bool(value))
            self.w.toggled.connect(lambda v: self.changed.emit(param.key, bool(v)))
            lay.addWidget(self.w)
            lay.addStretch(1)
        elif k == "choice":
            self.w = QComboBox()
            self.w.addItems([str(c) for c in param.choices])
            idx = self.w.findText(str(value))
            self.w.setCurrentIndex(max(0, idx))
            self.w.currentTextChanged.connect(lambda v: self.changed.emit(param.key, v))
            lay.addWidget(self.w, 1)
        else:
            self.slider = QSlider(Qt.Horizontal)
            self.slider.setRange(0, self.STEPS)
            if k == "int":
                self.spin = QSpinBox()
                self.spin.setRange(int(param.min), int(param.max))
                self.spin.setSingleStep(int(param.step or 1))
            else:
                self.spin = QDoubleSpinBox()
                self.spin.setRange(float(param.min), float(param.max))
                st = float(param.step or 0.01)
                self.spin.setSingleStep(st)
                self.spin.setDecimals(0 if st >= 1 else (1 if st >= 0.1 else 2))
            if param.unit:
                self.spin.setSuffix(f" {param.unit}")
            self.spin.setFixedWidth(96)
            self.spin.setKeyboardTracking(False)
            value = int(round(float(value))) if k == "int" else float(value)
            self.spin.setValue(value)
            self.slider.setValue(self._to_slider(value))
            self.slider.valueChanged.connect(self._slider_moved)
            self.spin.valueChanged.connect(self._spin_changed)
            self.slider.sliderReleased.connect(lambda: self.changed.emit(param.key, self._value()))
            lay.addWidget(self.slider, 1)
            lay.addWidget(self.spin)
            self._block = False

    def _value(self):
        v = self.spin.value()
        return int(v) if self.param.kind == "int" else float(v)

    def _to_slider(self, v):
        lo, hi = float(self.param.min), float(self.param.max)
        if self.param.log and lo > 0:
            f = (math.log(max(v, lo)) - math.log(lo)) / (math.log(hi) - math.log(lo))
        else:
            f = (v - lo) / (hi - lo) if hi > lo else 0
        return int(round(max(0, min(1, f)) * self.STEPS))

    def _from_slider(self, s):
        lo, hi = float(self.param.min), float(self.param.max)
        f = s / self.STEPS
        if self.param.log and lo > 0:
            v = math.exp(math.log(lo) + f * (math.log(hi) - math.log(lo)))
        else:
            v = lo + f * (hi - lo)
        st = self.param.step or 0
        if st:
            v = round(v / st) * st
        return v

    def _slider_moved(self, s):
        if self._block:
            return
        self._block = True
        self.spin.blockSignals(True)
        v = self._from_slider(s)
        self.spin.setValue(int(round(v)) if self.param.kind == "int" else float(v))
        self.spin.blockSignals(False)
        self._block = False
        if not self.slider.isSliderDown():
            self.changed.emit(self.param.key, self._value())

    def _spin_changed(self, v):
        if self._block:
            return
        self._block = True
        self.slider.setValue(self._to_slider(v))
        self._block = False
        self.changed.emit(self.param.key, self._value())


class ParamEditor(QWidget):
    """Form with a control for every param of an effect instance."""
    changed = Signal(str, object)

    def __init__(self, effect_def, params, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignRight)
        form.setContentsMargins(4, 4, 4, 4)
        self.controls = {}
        for prm in effect_def.params:
            c = ParamControl(prm, params.get(prm.key, prm.default))
            c.changed.connect(self.changed)
            lbl = QLabel(prm.label)
            lbl.setToolTip(prm.tip)
            form.addRow(lbl, c)
            self.controls[prm.key] = c


class EQCurve(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(110)
        self.bands = []
        self.sr = 44100
        self.freqs = np.geomspace(20, 20000, 300)

    def set_bands(self, bands, sr):
        self.bands, self.sr = bands, sr
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0f0f14"))
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(theme.GRID), 1))
        for f in (50, 100, 200, 500, 1000, 2000, 5000, 10000):
            x = (math.log10(f) - math.log10(20)) / 3 * w
            p.drawLine(QPointF(x, 0), QPointF(x, h))
        for db in (-12, -6, 0, 6, 12):
            y = h / 2 - db / 18 * h / 2
            p.drawLine(QPointF(0, y), QPointF(w, y))
        try:
            resp = dsp.eq_response(self.sr, self.bands, self.freqs)
        except Exception:
            resp = np.zeros_like(self.freqs)
        path = QPainterPath()
        for i, (f, r) in enumerate(zip(self.freqs, resp)):
            x = (math.log10(f) - math.log10(20)) / 3 * w
            y = h / 2 - max(-18, min(18, r)) / 18 * h / 2
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(QColor(theme.BLUE), 2))
        p.drawPath(path)
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont("Segoe UI", 7))
        for f, t in ((100, "100"), (1000, "1k"), (10000, "10k")):
            x = (math.log10(f) - math.log10(20)) / 3 * w
            p.drawText(QPointF(x + 2, h - 3), t)
        p.end()


def fmt_time(samples, sr):
    if sr <= 0:
        return "0:00.000"
    s = max(0, samples) / sr
    m, s = divmod(s, 60)
    return f"{int(m)}:{s:06.3f}"
