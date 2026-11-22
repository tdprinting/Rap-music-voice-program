"""Multitrack timeline: ruler, track headers and the clip/waveform lanes."""
from __future__ import annotations

import math
import numpy as np
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QLineF, QObject, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPainterPath, QPolygonF, QAction
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QToolButton,
                               QSlider, QScrollArea, QScrollBar, QMenu, QSizePolicy, QInputDialog,
                               QFrame, QComboBox)

from . import theme
from .widgets import fmt_time
from ..core import dsp
from ..core.presets import ROLES

RULER_H = 34
HEADER_W = 230
EDGE_PX = 6
BASE_SPB = 256


class WaveCache:
    def __init__(self):
        self.data = {}

    def peaks(self, pool, fid):
        hit = self.data.get(fid)
        if hit is None:
            a = pool.get(fid)
            mono = a.mean(axis=1) if a.ndim == 2 else a
            n = int(math.ceil(len(mono) / BASE_SPB))
            pad = n * BASE_SPB - len(mono)
            m = np.pad(mono, (0, pad)).reshape(n, BASE_SPB) if n else np.zeros((0, BASE_SPB))
            hit = (m.min(axis=1), m.max(axis=1), mono)
            self.data[fid] = hit
        return hit

    def clear(self):
        self.data.clear()


class View(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.spp = 400.0      # samples per pixel
        self.x0 = 0           # first visible sample
        self.follow = True

    def x_of(self, s):
        return (s - self.x0) / self.spp

    def s_of(self, x):
        return int(self.x0 + x * self.spp)

    def set(self, spp=None, x0=None):
        if spp is not None:
            self.spp = min(max(float(spp), 1.0), 200000.0)
        if x0 is not None:
            self.x0 = int(max(0, x0))
        self.changed.emit()


def _grid_step(project, spp):
    """Pick a musical grid that is at least ~8 px wide."""
    spb = project.samples_per_beat()
    for div in (8, 4, 2, 1):
        if spb / div / spp >= 8:
            return spb / div, div
    bar = spb * project.time_sig
    m = 1
    while bar * m / spp < 40:
        m *= 2
    return bar * m, 0


class Ruler(QWidget):
    def __init__(self, state, view, parent=None):
        super().__init__(parent)
        self.state, self.view = state, view
        self.setFixedHeight(RULER_H)
        self.setMouseTracking(True)
        self._loop_drag = None
        self._scrub = False
        view.changed.connect(self.update)
        state.playhead_moved.connect(lambda *_: self.update())

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.PANEL))
        proj = self.state.project
        if not proj:
            p.end()
            return
        v = self.view
        w, h = self.width(), self.height()
        if proj.loop:
            a, b = v.x_of(proj.loop[0]), v.x_of(proj.loop[1])
            p.fillRect(QRectF(a, 0, b - a, 10), QColor(63, 140, 255, 160))
        spb = proj.samples_per_beat()
        off = proj.beat_offset_s * proj.sr
        bar = spb * proj.time_sig
        p.setFont(QFont("Segoe UI", 8))
        step_bars = 1
        while bar * step_bars / v.spp < 50:
            step_bars *= 2
        k0 = int(math.floor((v.x0 - off) / bar))
        k1 = int(math.ceil((v.s_of(w) - off) / bar))
        for k in range(k0, k1 + 1):
            x = v.x_of(off + k * bar)
            if k % step_bars == 0:
                p.setPen(QColor(theme.TEXT))
                p.drawLine(QPointF(x, 12), QPointF(x, h))
                p.drawText(QPointF(x + 3, 23), str(k + 1))
            if bar / v.spp > 60:
                p.setPen(QColor(theme.MUTED))
                for bt in range(1, proj.time_sig):
                    xb = v.x_of(off + k * bar + bt * spb)
                    p.drawLine(QPointF(xb, h - 8), QPointF(xb, h))
        # time labels (seconds) bottom-right small
        p.setPen(QColor(theme.MUTED))
        sec_step = 1
        while proj.sr * sec_step / v.spp < 70:
            sec_step *= 2
        s0 = int(v.x0 / proj.sr / sec_step) * sec_step
        t = s0
        while v.x_of(t * proj.sr) < w:
            x = v.x_of(t * proj.sr)
            p.drawText(QPointF(x + 2, h - 2), fmt_time(t * proj.sr, proj.sr)[:-4])
            t += sec_step
        for m in proj.markers:
            x = v.x_of(m.pos)
            p.setPen(QColor("#f0b429"))
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            p.drawText(QPointF(x + 3, 10), m.name[:16])
        x = v.x_of(self.state.playhead)
        p.setBrush(QColor(theme.RED))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 6, 12), QPointF(x + 6, 12), QPointF(x, 22)]))
        p.end()

    def mousePressEvent(self, e):
        if not self.state.project:
            return
        s = self.view.s_of(e.position().x())
        if e.button() == Qt.RightButton:
            self._context(e, s)
            return
        if e.modifiers() & Qt.ShiftModifier or e.position().y() < 12:
            self._loop_drag = s
            self.state.project.loop = (s, s)
        else:
            self._scrub = True
            self.state.set_playhead(s)

    def mouseMoveEvent(self, e):
        s = self.view.s_of(e.position().x())
        if self._loop_drag is not None:
            a, b = sorted((self._loop_drag, s))
            if self.state.snap:
                a, b = self.state.project.snap(a, 1), self.state.project.snap(b, 1)
            self.state.project.loop = (a, b)
            self.update()
            self.state.view_changed.emit()
        elif self._scrub:
            self.state.set_playhead(s)

    def mouseReleaseEvent(self, e):
        if self._loop_drag is not None:
            lp = self.state.project.loop
            if lp and lp[1] - lp[0] < 100:
                self.state.project.loop = None
            self.state.view_changed.emit()
        self._loop_drag = None
        self._scrub = False

    def _context(self, e, s):
        m = QMenu(self)
        a1 = m.addAction("Add marker here")
        a2 = m.addAction("Clear loop")
        a3 = m.addAction("Loop selection")
        act = m.exec(e.globalPosition().toPoint())
        proj = self.state.project
        if act == a1:
            from ..core.project import Marker
            name, ok = QInputDialog.getText(self, "Marker", "Name:", text=f"Marker {len(proj.markers) + 1}")
            if ok:
                self.state.push_undo("Add marker")
                proj.markers.append(Marker(s, name))
                proj.markers.sort(key=lambda m: m.pos)
        elif act == a2:
            proj.loop = None
        elif act == a3 and self.state.has_range:
            proj.loop = self.state.range()
        self.update()
        self.state.view_changed.emit()


class TrackHeader(QFrame):
    def __init__(self, state, track, parent=None):
        super().__init__(parent)
        self.state, self.track = state, track
        self.setFixedHeight(track.height)
        self.setFixedWidth(HEADER_W)
        self.setObjectName("trackHeader")
        self._apply_style()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 6, 4)
        lay.setSpacing(3)
        top = QHBoxLayout()
        self.name = QLineEdit(track.name)
        self.name.setFrame(False)
        self.name.editingFinished.connect(self._rename)
        top.addWidget(self.name, 1)
        self.kind = QLabel({"beat": "BEAT", "reference": "REF"}.get(track.kind, track.role.upper()))
        self.kind.setObjectName("muted")
        top.addWidget(self.kind)
        lay.addLayout(top)
        btns = QHBoxLayout()
        btns.setSpacing(3)
        self.m = self._btn("M", "Mute", track.mute, self._mute)
        self.s = self._btn("S", "Solo", track.solo, self._solo)
        btns.addWidget(self.m)
        btns.addWidget(self.s)
        if track.kind == "vocal":
            self.r = self._btn("●", "Arm for recording", track.armed, self._arm)
            self.r.setObjectName("rec")
            btns.addWidget(self.r)
            self.role = QComboBox()
            self.role.addItems([r for r in ROLES if r != "Beat"])
            self.role.setCurrentText(track.role)
            self.role.currentTextChanged.connect(self._role)
            self.role.setToolTip("Track role (sets level, pan and style defaults)")
            btns.addWidget(self.role, 1)
        self.fx = self._btn("FX", "Bypass all effects on this track (A/B)", not track.fx_bypass, self._fx)
        btns.addWidget(self.fx)
        self.au = self._btn("A", "Volume automation: show/edit the envelope on this track\n"
                                 "(click the lane to add points, drag to move, right-click a point to delete)",
                            track.show_automation, self._auto)
        btns.addWidget(self.au)
        btns.addStretch(0)
        lay.addLayout(btns)
        row = QHBoxLayout()
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(-600, 120)
        self.vol.setValue(int(track.volume_db * 10))
        self.vol.setToolTip("Volume")
        self.vol.valueChanged.connect(self._vol)
        self.vol_lbl = QLabel(f"{track.volume_db:+.1f}")
        self.vol_lbl.setFixedWidth(36)
        self.pan = QSlider(Qt.Horizontal)
        self.pan.setRange(-100, 100)
        self.pan.setValue(int(track.pan * 100))
        self.pan.setFixedWidth(54)
        self.pan.setToolTip("Pan (double-click a slider to reset)")
        self.pan.valueChanged.connect(self._pan)
        row.addWidget(self.vol, 1)
        row.addWidget(self.vol_lbl)
        row.addWidget(self.pan)
        lay.addLayout(row)

    def _apply_style(self):
        cur = self.state.current_track == self.track.id
        self.setStyleSheet(
            f"#trackHeader {{ background: {'#262633' if cur else theme.PANEL}; border-left: 5px solid {self.track.color};"
            f" border-bottom: 1px solid #2a2a35; }}")

    def _btn(self, text, tip, checked, slot):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(True)
        b.setChecked(checked)
        b.setFixedSize(28, 22)
        b.toggled.connect(slot)
        return b

    def mousePressEvent(self, e):
        self.state.set_current_track(self.track.id)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.vol.setValue(0)

    def _rename(self):
        if self.name.text() != self.track.name:
            self.state.push_undo("Rename track")
            self.track.name = self.name.text()
            self.state.tracks_changed.emit()

    def _mute(self, v):
        self.track.mute = v
        self.state.track_params.emit(self.track.id)

    def _solo(self, v):
        self.track.solo = v
        self.state.track_params.emit(self.track.id)

    def _arm(self, v):
        if v:
            for t in self.state.project.tracks:
                if t is not self.track and t.armed:
                    t.armed = False
            self.state.tracks_changed.emit()
        self.track.armed = v
        self.state.set_current_track(self.track.id)
        self.state.track_params.emit(self.track.id)

    def _role(self, r):
        from ..core.presets import ROLE_DEFAULTS
        self.state.push_undo("Change role")
        self.track.role = r
        d = ROLE_DEFAULTS.get(r, {})
        self.vol.setValue(int(d.get("volume_db", 0) * 10))
        self.pan.setValue(int(d.get("pan", 0) * 100))
        self.kind.setText(r.upper())

    def _auto(self, v):
        self.track.show_automation = v
        if v:
            self.state.status.emit(f"Automation on '{self.track.name}': click the lane to add volume points")
        self.state.view_changed.emit()

    def _fx(self, v):
        self.track.fx_bypass = not v
        self.state.modified([self.track.id])

    def _vol(self, v):
        self.track.volume_db = v / 10.0
        self.vol_lbl.setText(f"{self.track.volume_db:+.1f}")
        self.state.track_params.emit(self.track.id)

    def _pan(self, v):
        self.track.pan = v / 100.0
        self.state.track_params.emit(self.track.id)


class Lanes(QWidget):
    """Draws and edits clips for all tracks."""

    def __init__(self, state, view, waves, parent=None):
        super().__init__(parent)
        self.state, self.view, self.waves = state, view, waves
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._drag = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._cache = None
        self._dirty = True
        view.changed.connect(self.invalidate)
        state.view_changed.connect(self.invalidate)
        state.playhead_moved.connect(lambda *_: self.update())
        state.selection_changed.connect(self.invalidate)
        self.context_menu_cb = None

    def invalidate(self):
        self._dirty = True
        self.update()

    def refresh_height(self):
        p = self.state.project
        h = sum(t.height for t in p.tracks) if p else 0
        self.setFixedHeight(max(h, 10))

    # ---------------------------------------------------------- geometry
    def rows(self):
        y = 0
        for t in self.state.project.tracks:
            yield t, y, t.height
            y += t.height

    def track_at(self, y):
        for t, ty, th in self.rows():
            if ty <= y < ty + th:
                return t, ty, th
        return None, 0, 0

    def hit(self, x, y):
        t, ty, th = self.track_at(y)
        if t is None:
            return None, None, None
        s = self.view.s_of(x)
        for c in reversed(t.clips):
            x0, x1 = self.view.x_of(c.start), self.view.x_of(c.end)
            if x0 - 2 <= x <= x1 + 2:
                local_y = y - ty
                if local_y < 14 and abs(x - (x0 + c.fade_in / self.view.spp)) < 7:
                    return t, c, "fadein"
                if local_y < 14 and abs(x - (x1 - c.fade_out / self.view.spp)) < 7:
                    return t, c, "fadeout"
                if x - x0 < EDGE_PX:
                    return t, c, "left"
                if x1 - x < EDGE_PX:
                    return t, c, "right"
                return t, c, "body"
        return t, None, None

    def _snap(self, s, force=False):
        p = self.state.project
        if not (self.state.snap or force):
            return s
        step, div = _grid_step(p, self.view.spp)
        off = p.beat_offset_s * p.sr
        return int(round((s - off) / step) * step + off)

    # ---------------------------------------------------------- painting
    def paintEvent(self, e):
        from PySide6.QtGui import QPixmap
        dpr = self.devicePixelRatioF()
        size = self.size()
        if (self._dirty or self._cache is None or self._cache.size() != size * dpr):
            pm = QPixmap(size * dpr)
            pm.setDevicePixelRatio(dpr)
            cp = QPainter(pm)
            self._paint_body(cp)
            cp.end()
            self._cache = pm
            self._dirty = False
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        proj = self.state.project
        if proj:
            v = self.view
            rp = self.state.record_preview
            if rp:
                for t, ty, th in self.rows():
                    if t.id == rp[0]:
                        p.fillRect(QRectF(v.x_of(rp[1]), ty + 2, rp[2] / v.spp, th - 4), QColor(224, 72, 72, 110))
            x = v.x_of(self.state.playhead)
            p.setPen(QPen(QColor(theme.RED), 1.5))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.end()

    def _paint_body(self, p):
        proj = self.state.project
        p.fillRect(self.rect(), QColor(theme.BG))
        if not proj:
            return
        v = self.view
        w = self.width()
        clip_rect = self.rect()
        step, div = _grid_step(proj, v.spp)
        off = proj.beat_offset_s * proj.sr
        bar = proj.samples_per_beat() * proj.time_sig
        for i, (t, ty, th) in enumerate(self.rows()):
            if ty > clip_rect.bottom() or ty + th < clip_rect.top():
                continue
            cur = t.id == self.state.current_track
            p.fillRect(QRectF(0, ty, w, th), QColor("#17171d" if i % 2 else "#141419") if not cur else QColor("#1c1c26"))
            # grid
            k0 = int(math.floor((v.x0 - off) / step))
            k1 = int(math.ceil((v.s_of(w) - off) / step))
            if k1 - k0 < 2000:
                for k in range(k0, k1 + 1):
                    s = off + k * step
                    x = v.x_of(s)
                    is_bar = abs(((s - off) / bar) - round((s - off) / bar)) < 1e-6
                    p.setPen(QPen(QColor("#30303d" if is_bar else "#1f1f28"), 1))
                    p.drawLine(QPointF(x, ty), QPointF(x, ty + th))
            p.setPen(QPen(QColor("#2a2a35"), 1))
            p.drawLine(QPointF(0, ty + th - 1), QPointF(w, ty + th - 1))
            for c in t.clips:
                self._paint_clip(p, t, c, ty, th, w)
            if self.state.show_breaths and t.breath_regions:
                for r in t.breath_regions:
                    x0, x1 = v.x_of(r[0]), v.x_of(r[1])
                    if x1 < 0 or x0 > w:
                        continue
                    on = r[2] if len(r) > 2 else True
                    p.fillRect(QRectF(x0, ty + th - 14, max(2, x1 - x0), 10),
                               QColor(255, 159, 67, 200) if on else QColor(120, 120, 120, 120))
            for th in t.throws:
                x0, x1 = v.x_of(th["start"]), v.x_of(th["end"])
                if x1 < 0 or x0 > w:
                    continue
                col = {"delay": QColor(176, 108, 255, 210), "reverb": QColor(46, 196, 182, 210),
                       "reverse": QColor(255, 122, 168, 210)}.get(th.get("kind"), QColor(176, 108, 255, 210))
                p.fillRect(QRectF(x0, ty + 16, max(3, x1 - x0), 6), col)
                p.setPen(col)
                p.setFont(QFont("Segoe UI", 7, QFont.Bold))
                p.drawText(QPointF(x0 + 2, ty + 31), {"delay": "DLY THROW", "reverb": "VERB THROW",
                                                      "reverse": "REV SWELL"}.get(th.get("kind"), "THROW"))
            if t.kind == "reference":
                p.fillRect(QRectF(0, ty, w, th), QColor(240, 180, 41, 18))
            if t.automation or t.show_automation:
                self._paint_automation(p, t, ty, th, w)
            # selection
            if self.state.has_range and (not self.state.sel_tracks or t.id in self.state.sel_tracks):
                a, b = self.state.range()
                p.fillRect(QRectF(v.x_of(a), ty, (b - a) / v.spp, th), QColor(63, 140, 255, 55))
        if proj.loop:
            a, b = v.x_of(proj.loop[0]), v.x_of(proj.loop[1])
            p.setPen(QPen(QColor(63, 140, 255, 160), 1, Qt.DashLine))
            p.drawLine(QPointF(a, 0), QPointF(a, self.height()))
            p.drawLine(QPointF(b, 0), QPointF(b, self.height()))
        for m in proj.markers:
            x = v.x_of(m.pos)
            p.setPen(QPen(QColor(240, 180, 41, 140), 1, Qt.DotLine))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))

    @staticmethod
    def _db_to_y(db, ty, th):
        return ty + 6 + (th - 12) * (1 - (min(max(db, -40.0), 6.0) + 40.0) / 46.0)

    @staticmethod
    def _y_to_db(y, ty, th):
        return float(np.clip((1 - (y - ty - 6) / max(th - 12, 1)) * 46.0 - 40.0, -40.0, 6.0))

    def _paint_automation(self, p, t, ty, th, w):
        v = self.view
        pts = sorted(t.automation)
        col = QColor(240, 180, 41, 230 if t.show_automation else 110)
        p.setPen(QPen(col, 1.6))
        if not pts:
            y = self._db_to_y(0.0, ty, th)
            p.setPen(QPen(col, 1, Qt.DashLine))
            p.drawLine(QPointF(0, y), QPointF(w, y))
            return
        path = QPainterPath()
        path.moveTo(0, self._db_to_y(pts[0][1], ty, th))
        for s_, db in pts:
            path.lineTo(v.x_of(s_), self._db_to_y(db, ty, th))
        path.lineTo(w, self._db_to_y(pts[-1][1], ty, th))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        if t.show_automation:
            p.setBrush(col)
            for s_, db in pts:
                p.drawEllipse(QPointF(v.x_of(s_), self._db_to_y(db, ty, th)), 4, 4)
            p.setBrush(Qt.NoBrush)

    def _auto_point_at(self, t, ty, th, x, y):
        for i, (s_, db) in enumerate(sorted(t.automation)):
            if abs(self.view.x_of(s_) - x) < 7 and abs(self._db_to_y(db, ty, th) - y) < 7:
                return i
        return None

    def _paint_clip(self, p, t, c, ty, th, w):
        v = self.view
        x0, x1 = v.x_of(c.start), v.x_of(c.end)
        if x1 < 0 or x0 > w:
            return
        sel = c.id in self.state.selected_clips
        base = QColor(t.color)
        body = QColor(base)
        body.setAlpha(70 if not c.muted else 25)
        r = QRectF(x0, ty + 2, max(2.0, x1 - x0), th - 5)
        p.setPen(QPen(QColor("#ffffff") if sel else base.darker(130), 1.5 if sel else 1))
        p.setBrush(body)
        p.drawRoundedRect(r, 4, 4)
        # waveform
        try:
            mins, maxs, mono = self.waves.peaks(self.state.project.pool, c.file_id)
        except Exception:
            return
        vx0 = max(0, int(x0))
        vx1 = min(w, int(math.ceil(x1)))
        if vx1 <= vx0:
            return
        xs = np.arange(vx0, vx1)
        src = (v.x0 + xs * v.spp - c.start + c.src_in)
        g = float(dsp.db_to_lin(c.gain_db))
        mid = ty + 2 + (th - 5) / 2
        amp = (th - 18) / 2
        if v.spp >= BASE_SPB:
            if len(mins) == 0:
                return
            b0 = np.clip((src / BASE_SPB).astype(np.int64), 0, len(mins) - 1)
            lo = np.minimum.reduceat(mins, b0)
            hi = np.maximum.reduceat(maxs, b0)
        else:
            si = np.clip(src.astype(np.int64), 0, len(mono) - 1)
            se = np.clip((src + max(v.spp, 1)).astype(np.int64), 1, len(mono))
            lo = np.array([mono[a:b].min() if b > a else mono[a] for a, b in zip(si, se)])
            hi = np.array([mono[a:b].max() if b > a else mono[a] for a, b in zip(si, se)])
        lo = np.clip(lo * g, -1, 1)
        hi = np.clip(hi * g, -1, 1)
        wave = QColor(base.lighter(150))
        if c.muted:
            wave = QColor("#555")
        p.setPen(QPen(wave, 1))
        lines = [QLineF(float(x), mid - float(h_) * amp, float(x), mid - float(l_) * amp)
                 for x, l_, h_ in zip(xs, lo, hi)]
        p.drawLines(lines)
        # fades
        p.setPen(QPen(QColor("#ffffff"), 1))
        if c.fade_in:
            fx = x0 + c.fade_in / v.spp
            p.drawLine(QPointF(x0, ty + th - 4), QPointF(fx, ty + 3))
        if c.fade_out:
            fx = x1 - c.fade_out / v.spp
            p.drawLine(QPointF(fx, ty + 3), QPointF(x1, ty + th - 4))
        p.fillRect(QRectF(x0 + c.fade_in / v.spp - 3, ty + 3, 6, 6), QColor("#ffffff"))
        p.fillRect(QRectF(x1 - c.fade_out / v.spp - 3, ty + 3, 6, 6), QColor("#ffffff"))
        if x1 - x0 > 40:
            p.setPen(QColor("#ffffff"))
            p.setFont(QFont("Segoe UI", 8))
            label = c.name + (f"  (take {c.take})" if c.take > 1 else "") + ("  [muted]" if c.muted else "")
            p.drawText(QRectF(max(x0, 0) + 6, ty + 3, x1 - x0 - 10, 14), Qt.AlignLeft | Qt.AlignVCenter, label)

    # ---------------------------------------------------------- mouse
    def mousePressEvent(self, e):
        proj = self.state.project
        if not proj:
            return
        x, y = e.position().x(), e.position().y()
        t, c, zone = self.hit(x, y)
        if t is not None:
            self.state.set_current_track(t.id)
        if t is not None and t.show_automation and not (e.modifiers() & Qt.ControlModifier):
            _, ty, th = self.track_at(y)
            t.automation = sorted([list(pt) for pt in t.automation])
            i = self._auto_point_at(t, ty, th, x, y)
            if e.button() == Qt.RightButton:
                if i is not None:
                    self.state.push_undo("Delete automation point")
                    t.automation.pop(i)
                    self.state.modified([t.id])
                return
            if e.button() == Qt.LeftButton:
                self.state.push_undo("Automation")
                if i is None:
                    t.automation.append([int(self.view.s_of(x)), self._y_to_db(y, ty, th)])
                    t.automation.sort()
                    i = self._auto_point_at(t, ty, th, x, y)
                self._drag = dict(mode="auto", track=t, idx=i, ty=ty, th=th, started=True, x0=x, moving=[])
                self.invalidate()
                return
        if e.button() == Qt.RightButton:
            if self.context_menu_cb:
                self.context_menu_cb(e.globalPosition().toPoint(), t, c, self.view.s_of(x))
            return
        if e.button() != Qt.LeftButton:
            return
        s = self.view.s_of(x)
        ctrl = bool(e.modifiers() & Qt.ControlModifier)
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        if c is not None:
            if ctrl:
                self.state.selected_clips ^= {c.id}
            elif c.id not in self.state.selected_clips:
                self.state.selected_clips = {c.id}
            moving = []
            for tr in proj.tracks:
                for cc in tr.clips:
                    if cc.id in self.state.selected_clips:
                        moving.append((tr, cc, cc.start))
            self._drag = dict(mode=zone, track=t, clip=c, s0=s, x0=x, started=False,
                              orig=(c.start, c.length, c.src_in, c.fade_in, c.fade_out), moving=moving)
            self.state.selection_changed.emit()
        else:
            self.state.selected_clips = set()
            start = self._snap(s) if self.state.snap else s
            if shift and self.state.has_range:
                if t:
                    self.state.sel_tracks.add(t.id)
                a, b = self.state.range()
                anchor = a if abs(s - b) < abs(s - a) else b
                self._drag = dict(mode="select", anchor=anchor, started=True, x0=x)
                self.state.sel_start, self.state.sel_end = anchor, start
            else:
                self.state.sel_tracks = {t.id} if t else set()
                self._drag = dict(mode="select", anchor=start, started=False, x0=x)
                self.state.sel_start = self.state.sel_end = start
            self.state.set_playhead(start)
            self.state.selection_changed.emit()
        self.invalidate()

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        d = self._drag
        if d is None:
            t, c, zone = self.hit(x, y)
            cur = {"left": Qt.SizeHorCursor, "right": Qt.SizeHorCursor, "fadein": Qt.PointingHandCursor,
                   "fadeout": Qt.PointingHandCursor, "body": Qt.OpenHandCursor}.get(zone, Qt.IBeamCursor)
            self.setCursor(cur)
            return
        if not d["started"]:
            if abs(x - d["x0"]) < 3:
                return
            d["started"] = True
            if d["mode"] != "select":
                self.state.push_undo({"body": "Move clip", "left": "Trim clip", "right": "Trim clip"}.get(d["mode"], "Fade"))
        s = self.view.s_of(x)
        proj = self.state.project
        if d["mode"] == "auto":
            t = d["track"]
            if d["idx"] is not None and d["idx"] < len(t.automation):
                t.automation[d["idx"]] = [max(0, int(s)), self._y_to_db(y, d["ty"], d["th"])]
                self.invalidate()
            return
        if d["mode"] == "select":
            self.state.sel_end = self._snap(s) if self.state.snap else s
            t, _, _ = self.track_at(y)
            if t is not None and (e.modifiers() & Qt.ShiftModifier or len(self.state.sel_tracks) > 0):
                self.state.sel_tracks.add(t.id)
            self.state.selection_changed.emit()
            return
        c = d["clip"]
        st, ln, si, fi, fo = d["orig"]
        delta = s - d["s0"]
        src_len = len(proj.pool.get(c.file_id))
        if d["mode"] == "body":
            new_start = max(0, st + delta)
            if self.state.snap and not (e.modifiers() & Qt.AltModifier):
                new_start = max(0, self._snap(new_start))
            dd = new_start - st
            for tr, cc, s_orig in d["moving"]:
                cc.start = max(0, s_orig + dd)
            # vertical move to another track of the same kind
            t2, _, _ = self.track_at(y)
            if t2 is not None and t2 is not d["track"] and t2.kind == d["track"].kind and len(d["moving"]) == 1:
                d["track"].clips.remove(c)
                t2.clips.append(c)
                self.state.modified([d["track"].id])
                d["track"] = t2
                self.state.set_current_track(t2.id)
        elif d["mode"] == "left":
            ns = self._snap(st + delta) if self.state.snap else st + delta
            dd = int(np.clip(ns - st, -si, ln - 64))
            c.start, c.src_in, c.length = st + dd, si + dd, ln - dd
        elif d["mode"] == "right":
            ne = self._snap(st + ln + delta) if self.state.snap else st + ln + delta
            c.length = int(np.clip(ne - st, 64, src_len - si))
        elif d["mode"] == "fadein":
            c.fade_in = int(np.clip(s - c.start, 0, c.length - c.fade_out))
        elif d["mode"] == "fadeout":
            c.fade_out = int(np.clip(c.end - s, 0, c.length - c.fade_in))
        self.invalidate()

    def mouseReleaseEvent(self, e):
        d = self._drag
        self._drag = None
        if d is None:
            return
        if d["mode"] == "auto":
            d["track"].automation.sort()
            self.state.modified([d["track"].id])
            return
        if d["mode"] == "select":
            if not d["started"] and not (e.modifiers() & Qt.ShiftModifier):
                self.state.sel_end = self.state.sel_start
            a, b = self.state.range()
            self.state.sel_start, self.state.sel_end = a, b
            self.state.selection_changed.emit()
        elif d["started"]:
            ids = {d["track"].id} | {tr.id for tr, _, _ in d["moving"]}
            self.state.modified(list(ids))

    def mouseDoubleClickEvent(self, e):
        t, c, zone = self.hit(e.position().x(), e.position().y())
        if c is not None:
            self.state.sel_start, self.state.sel_end = c.start, c.end
            self.state.sel_tracks = {t.id}
            self.state.selection_changed.emit()

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        dx = e.angleDelta().x()
        if e.modifiers() & Qt.ControlModifier:
            anchor = self.view.s_of(e.position().x())
            f = 0.8 if dy > 0 else 1.25
            new_spp = self.view.spp * f
            self.view.set(spp=new_spp, x0=anchor - e.position().x() * max(1.0, min(new_spp, 200000.0)))
            e.accept()
        elif e.modifiers() & (Qt.ShiftModifier | Qt.AltModifier) or abs(dx) > abs(dy):
            amt = dx if abs(dx) > abs(dy) else dy
            self.view.set(x0=self.view.x0 - amt / 120 * 80 * self.view.spp)
            e.accept()
        else:
            e.ignore()


class TimelineArea(QWidget):
    """Ruler + (headers | lanes) in a vertical scroll area + horizontal scrollbar."""

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.view = View()
        self.waves = WaveCache()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(0)
        corner = QLabel("  TRACKS")
        corner.setFixedSize(HEADER_W, RULER_H)
        corner.setObjectName("muted")
        corner.setStyleSheet(f"background:{theme.PANEL};")
        top.addWidget(corner)
        self.ruler = Ruler(state, self.view)
        top.addWidget(self.ruler, 1)
        lay.addLayout(top)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.inner = QWidget()
        h = QHBoxLayout(self.inner)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.header_col = QWidget()
        self.header_col.setFixedWidth(HEADER_W)
        self.header_lay = QVBoxLayout(self.header_col)
        self.header_lay.setContentsMargins(0, 0, 0, 0)
        self.header_lay.setSpacing(0)
        self.lanes = Lanes(state, self.view, self.waves)
        h.addWidget(self.header_col, 0, Qt.AlignTop)
        h.addWidget(self.lanes, 1, Qt.AlignTop)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self.hbar = QScrollBar(Qt.Horizontal)
        self.hbar.valueChanged.connect(self._hbar)
        lay.addWidget(self.hbar)
        self.empty_hint = QLabel(
            "Drop a beat and vocals here, or use  File > Import Beat  /  Import Vocal.\n"
            "Arm a vocal track (●) and press R to record over the beat.", self)
        self.empty_hint.setObjectName("muted")
        self.empty_hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.empty_hint.move(HEADER_W + 30, RULER_H + 40)
        self.empty_hint.raise_()
        self.setAcceptDrops(True)
        self.view.changed.connect(self._sync_bar)
        state.tracks_changed.connect(self.rebuild)
        state.project_replaced.connect(self.rebuild)
        state.selected_track_changed.connect(lambda *_: self._restyle_headers())
        self.headers = []
        self.drop_cb = None

    def rebuild(self):
        for hd in self.headers:
            hd.setParent(None)
            hd.deleteLater()
        self.headers = []
        while self.header_lay.count():
            it = self.header_lay.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        p = self.state.project
        if p:
            for t in p.tracks:
                hd = TrackHeader(self.state, t)
                self.header_lay.addWidget(hd)
                self.headers.append(hd)
        self.header_lay.addStretch(1)
        self.lanes.refresh_height()
        self.header_col.setFixedHeight(max(self.lanes.height(), 10))
        self.empty_hint.setVisible(not p or not any(t.clips for t in p.tracks))
        self.empty_hint.raise_()
        self._sync_bar()
        self.lanes.invalidate()
        self.ruler.update()

    def _restyle_headers(self):
        for hd in self.headers:
            hd._apply_style()
        self.lanes.invalidate()

    def sync_headers(self):
        """Reflect volume/pan/mute/solo changes made elsewhere (e.g. the mixer)."""
        for hd in self.headers:
            t = hd.track
            for w, val in ((hd.vol, int(t.volume_db * 10)), (hd.pan, int(t.pan * 100))):
                if w.value() != val:
                    w.blockSignals(True)
                    w.setValue(val)
                    w.blockSignals(False)
            hd.vol_lbl.setText(f"{t.volume_db:+.1f}")
            for b, val in ((hd.m, t.mute), (hd.s, t.solo)):
                if b.isChecked() != val:
                    b.blockSignals(True)
                    b.setChecked(val)
                    b.blockSignals(False)

    def _sync_bar(self):
        p = self.state.project
        total = (p.length if p else 0) + (p.sr * 30 if p else 0)
        vis = self.lanes.width() * self.view.spp
        self.hbar.blockSignals(True)
        self.hbar.setRange(0, int(max(0, total - vis) / 100))
        self.hbar.setPageStep(int(vis / 100))
        self.hbar.setValue(int(self.view.x0 / 100))
        self.hbar.blockSignals(False)
        if p:
            self.empty_hint.setVisible(not any(t.clips for t in p.tracks))

    def _hbar(self, v):
        self.view.set(x0=v * 100)

    def zoom(self, factor, anchor=None):
        anchor = self.state.playhead if anchor is None else anchor
        ax = self.view.x_of(anchor)
        new = self.view.spp * factor
        new = min(max(new, 1.0), 200000.0)
        self.view.set(spp=new, x0=anchor - ax * new)

    def zoom_fit(self):
        p = self.state.project
        if not p:
            return
        L = max(p.length, p.sr * 10)
        self.view.set(spp=L / max(self.lanes.width() - 20, 100), x0=0)

    def follow(self, pos):
        if not self.view.follow:
            return
        w = self.lanes.width()
        x = self.view.x_of(pos)
        if x > w * 0.9 or x < 0:
            self.view.set(x0=pos - w * 0.1 * self.view.spp)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_bar()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if files and self.drop_cb:
            pos = self.lanes.mapFrom(self, e.position().toPoint())
            t, _, _ = self.lanes.track_at(pos.y())
            self.drop_cb(files, t, max(0, self.view.s_of(pos.x())))
