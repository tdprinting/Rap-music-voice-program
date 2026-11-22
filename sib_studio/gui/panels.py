"""Dock panels: Auto-Match, Breaths, Pitch / Key finder, Mixer, Markers."""
from __future__ import annotations

import time
import numpy as np
from PySide6.QtCore import Qt, Signal, QTimer, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QSlider,
                               QCheckBox, QPlainTextEdit, QDoubleSpinBox, QSpinBox, QGroupBox, QFormLayout,
                               QListWidget, QListWidgetItem, QScrollArea, QInputDialog, QFrame, QGridLayout,
                               QSizePolicy, QToolButton)

from . import theme
from .widgets import LevelMeter, fmt_time
from .workers import run_task
from ..core import automatch, analysis, presets, breath as breath_mod, pitch as pitch_mod, dsp
from ..core.analysis import NOTE_NAMES, SCALES, key_name
from ..core.effects import REGISTRY, resolve_key


# ============================================================================ Auto-Match
class AutoMatchPanel(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        info = QGroupBox("Beat")
        g = QGridLayout(info)
        self.key_lbl = QLabel("—"); self.key_lbl.setObjectName("big")
        self.bpm_lbl = QLabel("—"); self.bpm_lbl.setObjectName("big")
        self.tune_lbl = QLabel("A4 = 440.0 Hz"); self.tune_lbl.setObjectName("muted")
        self.alt_lbl = QLabel(""); self.alt_lbl.setObjectName("muted"); self.alt_lbl.setWordWrap(True)
        g.addWidget(QLabel("Key"), 0, 0); g.addWidget(self.key_lbl, 1, 0)
        g.addWidget(QLabel("Tempo"), 0, 1); g.addWidget(self.bpm_lbl, 1, 1)
        g.addWidget(self.tune_lbl, 2, 0, 1, 2)
        g.addWidget(self.alt_lbl, 3, 0, 1, 2)
        self.analyze_btn = QPushButton("🎧  Listen to beat (analyze)")
        self.analyze_btn.clicked.connect(self.analyze)
        g.addWidget(self.analyze_btn, 4, 0, 1, 2)
        lay.addWidget(info)

        ov = QGroupBox("Manual override")
        f = QFormLayout(ov)
        self.root = QComboBox(); self.root.addItems(NOTE_NAMES)
        self.scale = QComboBox(); self.scale.addItems(list(SCALES.keys()))
        kr = QHBoxLayout(); kr.addWidget(self.root); kr.addWidget(self.scale, 1)
        f.addRow("Key", kr)
        self.bpm = QDoubleSpinBox(); self.bpm.setRange(40, 240); self.bpm.setDecimals(2); self.bpm.setSuffix(" BPM")
        tap = QPushButton("Tap"); tap.setToolTip("Tap along to the beat to set the tempo")
        tap.clicked.connect(self._tap)
        br = QHBoxLayout(); br.addWidget(self.bpm, 1); br.addWidget(tap)
        half = QToolButton(); half.setText("½"); half.clicked.connect(lambda: self.bpm.setValue(self.bpm.value() / 2))
        dbl = QToolButton(); dbl.setText("×2"); dbl.clicked.connect(lambda: self.bpm.setValue(self.bpm.value() * 2))
        br.addWidget(half); br.addWidget(dbl)
        f.addRow("Tempo", br)
        self.a4 = QDoubleSpinBox(); self.a4.setRange(415, 466); self.a4.setDecimals(2); self.a4.setSuffix(" Hz")
        f.addRow("Tuning A4", self.a4)
        ap = QPushButton("Apply override"); ap.clicked.connect(self._apply_override)
        f.addRow(ap)
        lay.addWidget(ov)

        mg = QGroupBox("Match vocals to the beat")
        mf = QFormLayout(mg)
        self.style = QComboBox()
        self.style.addItem("Keep each track's chain (by role)")
        self.style.addItems(list(presets.STYLES.keys()))
        mf.addRow("Style", self.style)
        self.tune_mode = QComboBox()
        self.tune_mode.addItems(["auto", "rap", "melodic", "hard", "off"])
        self.tune_mode.setToolTip("auto picks natural correction for rapping and tighter correction for singing.\n"
                                  "hard = the classic robotic Auto-Tune sound.")
        mf.addRow("Auto-Tune", self.tune_mode)
        self.prom = QDoubleSpinBox(); self.prom.setRange(-6, 8); self.prom.setValue(1.5); self.prom.setSuffix(" LU")
        self.prom.setToolTip("How loud the lead vocal sits compared with the beat.")
        mf.addRow("Vocal level vs beat", self.prom)
        self.carve = QCheckBox("Carve space in the beat (sidechain dynamic EQ)"); self.carve.setChecked(True)
        mf.addRow(self.carve)
        self.nudge = QCheckBox("Nudge vocal timing onto the beat grid"); self.nudge.setChecked(False)
        mf.addRow(self.nudge)
        self.only_sel = QCheckBox("Selected track only"); mf.addRow(self.only_sel)
        self.match_btn = QPushButton("✨  Auto-Match vocals")
        self.match_btn.setObjectName("primary")
        self.match_btn.clicked.connect(self.match)
        mf.addRow(self.match_btn)
        lay.addWidget(mg)
        self.report = QPlainTextEdit(); self.report.setReadOnly(True)
        self.report.setPlaceholderText("The Auto-Match report appears here.")
        lay.addWidget(self.report, 1)
        self._taps = []
        state.project_replaced.connect(self.sync)
        self.sync()

    def sync(self):
        p = self.state.project
        if not p:
            return
        self.key_lbl.setText(key_name(p.key_root, p.key_scale))
        self.bpm_lbl.setText(f"{p.bpm:.1f}")
        self.tune_lbl.setText(f"Beat tuning: A4 = {p.a4:.2f} Hz")
        a = p.analysis or {}
        if a.get("key"):
            self.alt_lbl.setText(f"Confidence {a['key'].get('confidence', 0):.0%}. Also possible: "
                                 + ", ".join(n for n, _ in a["key"].get("candidates", [])[1:4])
                                 + (f". Tempo could also be {', '.join(f'{b:.1f}' for b in a.get('bpm_alternatives', []))}"
                                    if a.get("bpm_alternatives") else ""))
        self.root.setCurrentIndex(p.key_root % 12)
        self.scale.setCurrentText(p.key_scale)
        self.bpm.setValue(p.bpm)
        self.a4.setValue(p.a4)

    def _beat(self):
        p = self.state.project
        bt = [t for t in p.beat_tracks() if t.clips] if p else []
        return bt[0] if bt else None

    def analyze(self):
        p = self.state.project
        bt = self._beat()
        if not bt:
            self.report.setPlainText("Import a beat first (File > Import Beat).")
            return
        self.analyze_btn.setEnabled(False)
        self.analyze_btn.setText("Listening…")

        def job(task):
            off, a = p.render_track_dry(bt)
            r = analysis.analyze_beat(a, p.sr)
            return off, r

        def done(res):
            off, r = res
            self.state.push_undo("Analyze beat")
            p.bpm = r.bpm or p.bpm
            p.beat_offset_s = r.beat_offset_s + off / p.sr
            p.key_root, p.key_scale, p.a4 = r.key["root"], r.key["scale"], r.a4_hz
            p.analysis = r.to_dict()
            self.sync()
            self.report.setPlainText(
                f"Key {r.key['name']} (relative {r.key['relative']}), confidence {r.key['confidence']:.0%}\n"
                f"Tempo {r.bpm:.1f} BPM - grid starts at {p.beat_offset_s:.3f}s\n"
                f"Tuning A4 = {r.a4_hz:.2f} Hz ({r.tuning_cents:+.1f} cents from standard)\n"
                f"Loudness {r.lufs:.1f} LUFS, peak {r.peak_db:.1f} dBFS\n\n"
                "Every SIB Tune set to 'Auto' now follows this key and tuning.")
            self._restore_btn()
            self.state.modified()

        run_task(job, done, self._err)

    def _restore_btn(self):
        self.analyze_btn.setEnabled(True)
        self.analyze_btn.setText("🎧  Listen to beat (analyze)")
        self.match_btn.setEnabled(True)
        self.match_btn.setText("✨  Auto-Match vocals")

    def _err(self, tb):
        self.report.setPlainText("Something went wrong:\n" + tb)
        self._restore_btn()

    def _apply_override(self):
        p = self.state.project
        self.state.push_undo("Change key/tempo")
        p.key_root, p.key_scale = self.root.currentIndex(), self.scale.currentText()
        p.bpm, p.a4 = self.bpm.value(), self.a4.value()
        self.sync()
        self.state.modified()

    def _tap(self):
        now = time.time()
        self._taps = [t for t in self._taps if now - t < 3.0] + [now]
        if len(self._taps) >= 3:
            d = np.diff(self._taps)
            self.bpm.setValue(round(60.0 / float(np.median(d)), 2))

    def match(self):
        p = self.state.project
        style = None if self.style.currentIndex() == 0 else self.style.currentText()
        ids = [self.state.current_track] if self.only_sel.isChecked() and self.state.current_track else None
        self.state.push_undo("Auto-Match")
        self.match_btn.setEnabled(False)
        self.match_btn.setText("Matching…")
        msgs = []

        def job(task):
            return automatch.auto_match(p, style=style, vocal_track_ids=ids, prominence_lu=self.prom.value(),
                                        tune_mode=self.tune_mode.currentText(), apply_timing=self.nudge.isChecked(),
                                        carve=self.carve.isChecked(), log=lambda m: task.report(m))

        def done(rep):
            self.report.setPlainText(automatch.format_report(rep))
            self.sync()
            self._restore_btn()
            self.state.tracks_changed.emit()
            self.state.modified()
            self.state.status.emit("Auto-Match done - press Space to hear it")

        run_task(job, done, self._err, lambda m, f: self.state.status.emit(m))


# ============================================================================ Breaths
class BreathPanel(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        tip = QLabel("Find every breath on the selected vocal track, review them (click to jump, untick to keep), "
                     "then turn them down. Timing never changes, so you stay in the pocket.")
        tip.setWordWrap(True); tip.setObjectName("muted")
        lay.addWidget(tip)
        f = QFormLayout()
        self.sens = QSlider(Qt.Horizontal); self.sens.setRange(0, 100); self.sens.setValue(50)
        f.addRow("Sensitivity", self.sens)
        self.min_ms = QSpinBox(); self.min_ms.setRange(40, 600); self.min_ms.setValue(120); self.min_ms.setSuffix(" ms")
        self.max_ms = QSpinBox(); self.max_ms.setRange(300, 3000); self.max_ms.setValue(1200); self.max_ms.setSuffix(" ms")
        r = QHBoxLayout(); r.addWidget(self.min_ms); r.addWidget(QLabel("to")); r.addWidget(self.max_ms)
        f.addRow("Length", r)
        lay.addLayout(f)
        self.detect_btn = QPushButton("🔎  Detect breaths")
        self.detect_btn.clicked.connect(self.detect)
        lay.addWidget(self.detect_btn)
        self.list = QListWidget()
        self.list.itemClicked.connect(self._jump)
        self.list.itemChanged.connect(self._toggle)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        a = QPushButton("All"); a.clicked.connect(lambda: self._set_all(True))
        n = QPushButton("None"); n.clicked.connect(lambda: self._set_all(False))
        clr = QPushButton("Clear"); clr.clicked.connect(self._clear)
        row.addWidget(a); row.addWidget(n); row.addWidget(clr)
        lay.addLayout(row)
        g = QFormLayout()
        self.mode = QComboBox(); self.mode.addItems(["reduce", "target", "silence"])
        self.mode.setToolTip("reduce = lower by an amount, target = make every breath the same quiet level, silence = mute")
        self.red = QDoubleSpinBox(); self.red.setRange(-60, 0); self.red.setValue(-12); self.red.setSuffix(" dB")
        self.tgt = QDoubleSpinBox(); self.tgt.setRange(-80, -20); self.tgt.setValue(-45); self.tgt.setSuffix(" dBFS")
        self.fade = QSpinBox(); self.fade.setRange(1, 80); self.fade.setValue(15); self.fade.setSuffix(" ms")
        g.addRow("Mode", self.mode); g.addRow("Reduce by", self.red); g.addRow("Target", self.tgt); g.addRow("Fade", self.fade)
        lay.addLayout(g)
        self.apply_btn = QPushButton("Apply to ticked breaths")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.apply)
        lay.addWidget(self.apply_btn)
        self.apply_btn_cb = None
        state.selected_track_changed.connect(lambda *_: self.refresh())
        state.project_replaced.connect(self.refresh)

    def _track(self):
        p = self.state.project
        t = p.track(self.state.current_track) if p and self.state.current_track else None
        return t if t and t.kind == "vocal" else None

    def refresh(self):
        self.list.blockSignals(True)
        self.list.clear()
        t = self._track()
        p = self.state.project
        if t:
            for i, r in enumerate(t.breath_regions):
                it = QListWidgetItem(f"{fmt_time(r[0], p.sr)}   ({(r[1] - r[0]) / p.sr * 1000:.0f} ms)")
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if (r[2] if len(r) > 2 else True) else Qt.Unchecked)
                it.setData(Qt.UserRole, i)
                self.list.addItem(it)
        self.list.blockSignals(False)
        self.detect_btn.setText(f"🔎  Detect breaths{' on ' + t.name if t else ''}")

    def detect(self):
        t = self._track()
        p = self.state.project
        if not t:
            self.state.status.emit("Select a vocal track first")
            return
        sens = self.sens.value() / 100.0
        mn, mx = self.min_ms.value(), self.max_ms.value()
        self.detect_btn.setEnabled(False)

        def job(task):
            out = []
            for c in t.clips:
                if c.muted:
                    continue
                a = p.render_clip(c)
                for r in breath_mod.detect_breaths(a, p.sr, sens, mn, mx):
                    out.append([c.start + r.start, c.start + r.end, True])
            return out

        def done(regs):
            t.breath_regions = sorted(regs)
            self.refresh()
            self.detect_btn.setEnabled(True)
            self.state.view_changed.emit()
            self.state.status.emit(f"Found {len(regs)} breaths on {t.name}")

        run_task(job, done, lambda tb: (self.detect_btn.setEnabled(True), self.state.status.emit("Breath detection failed")))

    def _jump(self, it):
        t = self._track()
        if t:
            r = t.breath_regions[it.data(Qt.UserRole)]
            self.state.sel_start, self.state.sel_end = int(r[0]), int(r[1])
            self.state.sel_tracks = {t.id}
            self.state.set_playhead(max(0, int(r[0]) - self.state.project.sr // 2))
            self.state.selection_changed.emit()

    def _toggle(self, it):
        t = self._track()
        if t:
            r = t.breath_regions[it.data(Qt.UserRole)]
            on = it.checkState() == Qt.Checked
            if len(r) > 2:
                r[2] = on
            else:
                r.append(on)
            self.state.view_changed.emit()

    def _set_all(self, on):
        t = self._track()
        if t:
            for r in t.breath_regions:
                if len(r) > 2:
                    r[2] = on
                else:
                    r.append(on)
            self.refresh()
            self.state.view_changed.emit()

    def _clear(self):
        t = self._track()
        if t:
            t.breath_regions = []
            self.refresh()
            self.state.view_changed.emit()

    def apply(self):
        t = self._track()
        p = self.state.project
        if not t or not t.breath_regions:
            return
        regs = [r for r in t.breath_regions if (r[2] if len(r) > 2 else True)]
        if not regs:
            return
        from ..core.project import process_range
        self.state.push_undo("Breath control")
        mode, red, tgt, fade = self.mode.currentText(), self.red.value(), self.tgt.value(), self.fade.value()
        for c in list(t.clips):
            local = [breath_mod.BreathRegion(int(r[0] - c.start), int(r[1] - c.start), 1.0, 0.0)
                     for r in regs if r[1] > c.start and r[0] < c.end]
            if not local:
                continue
            c0 = c.start

            def fn(seg, local=local):
                return breath_mod.apply_breath_control(seg, p.sr, local, mode, red, tgt, fade)
            process_range(p, t, c0, c.end, fn, "debreath", xfade_ms=0.0)
        t.breath_regions = [r for r in t.breath_regions if not (r[2] if len(r) > 2 else True)]
        self.refresh()
        self.state.modified([t.id])
        self.state.status.emit(f"Turned down {len(regs)} breaths")


# ============================================================================ Pitch / key finder
class PitchGraph(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.data = None   # (times, midi_in, midi_out, allowed_mask, offset_s)

    def set_data(self, d):
        self.data = d
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0f0f14"))
        w, h = self.width(), self.height()
        if not self.data:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Select a vocal clip and press Analyze pitch")
            p.end()
            return
        t, mi, mo, allowed = self.data
        v = ~np.isnan(mi)
        if not v.any():
            p.end()
            return
        lo = int(np.floor(np.nanpercentile(mi[v], 1))) - 2
        hi = int(np.ceil(np.nanpercentile(mi[v], 99))) + 2
        hi = max(hi, lo + 6)
        t0, t1 = float(t[0]), float(t[-1]) if len(t) > 1 else float(t[0]) + 1

        def X(tt):
            return (tt - t0) / max(t1 - t0, 1e-6) * (w - 40) + 36

        def Y(m):
            return h - (m - lo) / (hi - lo) * (h - 10) - 5

        p.setFont(QFont("Segoe UI", 7))
        for m in range(lo, hi + 1):
            pc = m % 12
            y = Y(m)
            on = allowed[pc]
            p.setPen(QPen(QColor("#2b3a55" if on else "#1c1c24"), 1))
            p.drawLine(QPointF(36, y), QPointF(w, y))
            if on:
                p.setPen(QColor(theme.MUTED))
                p.drawText(QPointF(2, y + 3), f"{NOTE_NAMES[pc]}{m // 12 - 1}")
        for arr, col, wd in ((mi, "#8a8a99", 1.2), (mo, theme.RED, 1.8)):
            path = QPainterPath()
            pen_down = False
            for tt, m in zip(t, arr):
                if np.isnan(m):
                    pen_down = False
                    continue
                pt = QPointF(X(tt), Y(m))
                if pen_down:
                    path.lineTo(pt)
                else:
                    path.moveTo(pt)
                    pen_down = True
            p.setPen(QPen(QColor(col), wd))
            p.drawPath(path)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QPointF(40, 12), "grey = your voice   red = after SIB Tune   blue lines = notes in key")
        p.end()


class PitchPanel(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.graph = PitchGraph()
        lay.addWidget(self.graph, 1)
        self.info = QLabel(""); self.info.setWordWrap(True)
        lay.addWidget(self.info)
        row = QHBoxLayout()
        self.btn = QPushButton("🎼  Analyze pitch of selected clip")
        self.btn.clicked.connect(self.analyze)
        self.use_btn = QPushButton("Use the vocal's key")
        self.use_btn.setToolTip("Set the project key from the melody you actually sang/rapped")
        self.use_btn.clicked.connect(self._use_key)
        self.use_btn.setEnabled(False)
        row.addWidget(self.btn, 1); row.addWidget(self.use_btn)
        lay.addLayout(row)
        self._vkey = None

    def _clip(self):
        p = self.state.project
        if not p:
            return None, None
        for t in p.tracks:
            for c in t.clips:
                if c.id in self.state.selected_clips:
                    return t, c
        t = p.track(self.state.current_track) if self.state.current_track else None
        if t and t.clips:
            if self.state.has_range:
                a, b = self.state.range()
                for c in t.clips:
                    if c.start < b and c.end > a:
                        return t, c
            return t, t.clips[0]
        return None, None

    def analyze(self):
        t, c = self._clip()
        p = self.state.project
        if c is None:
            self.info.setText("Select a vocal clip first.")
            return
        tune = next((e for e in t.fx if e["id"] == "sib_tune"), None)
        params = {**REGISTRY["sib_tune"].defaults(), **(tune["params"] if tune else {})}
        root, scale = resolve_key(params, p.ctx())
        a4 = params["a4"] if params["a4"] > 0 else p.a4
        audio = p.render_clip(c)[: p.sr * 90]
        self.btn.setEnabled(False)

        def job(task):
            tr = pitch_mod.detect_pitch(audio, p.sr, params["fmin"], params["fmax"])
            res = pitch_mod.correction_curve(tr, root, scale, a4, params["retune_ms"], params["humanize"],
                                             params["flex_cents"], params["amount"] / 100, params["transpose"],
                                             params["hysteresis"])
            return tr, res, pitch_mod.vocal_range(tr), pitch_mod.detect_key_from_vocal(tr, a4), pitch_mod.melodic_score(tr, a4)

        def done(r):
            tr, res, rng, vk, mel = r
            allowed = np.zeros(12, bool)
            for s in SCALES.get(scale, SCALES["Chromatic"]):
                allowed[(root + s) % 12] = True
            self.graph.set_data((res.times, res.midi_in, res.midi_out, allowed))
            dev = np.nanmean(np.abs(res.midi_in - np.round(res.midi_in))) * 100 if np.any(~np.isnan(res.midi_in)) else 0
            txt = f"Tuning to {key_name(root, scale)} at A4={a4:.1f} Hz.  "
            if rng:
                txt += f"Range {rng['low_note']}–{rng['high_note']}.  "
            txt += f"Average pitch drift {dev:.0f} cents.  Style: {'melodic/sung' if mel > 0.55 else 'rapped'}.  "
            if vk:
                self._vkey = vk
                txt += f"Your melody fits {key_name(vk[0], vk[1])}."
                self.use_btn.setEnabled(True)
            self.info.setText(txt)
            self.btn.setEnabled(True)

        run_task(job, done, lambda tb: (self.btn.setEnabled(True), self.info.setText(tb[-400:])))

    def _use_key(self):
        if self._vkey:
            p = self.state.project
            self.state.push_undo("Set key from vocal")
            p.key_root, p.key_scale = self._vkey[0], self._vkey[1]
            self.state.modified()
            self.state.status.emit(f"Project key set to {key_name(p.key_root, p.key_scale)}")


# ============================================================================ Mixer
class ChannelStrip(QFrame):
    def __init__(self, state, track=None, parent=None):
        super().__init__(parent)
        self.state, self.track = state, track
        self.setFixedWidth(92)
        self.setStyleSheet(f"QFrame {{ background: {theme.PANEL}; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        name = QLabel(track.name if track else "MASTER")
        name.setAlignment(Qt.AlignCenter)
        name.setStyleSheet(f"color: {track.color if track else theme.TEXT}; font-weight: 600;")
        lay.addWidget(name)
        self.pan = QSlider(Qt.Horizontal)
        self.pan.setRange(-100, 100)
        if track:
            self.pan.setValue(int(track.pan * 100))
            self.pan.valueChanged.connect(self._pan)
            lay.addWidget(self.pan)
        mid = QHBoxLayout()
        self.fader = QSlider(Qt.Vertical)
        self.fader.setRange(-600, 120)
        self.fader.setValue(int((track.volume_db if track else state.project.master_volume_db) * 10))
        self.fader.valueChanged.connect(self._vol)
        mid.addWidget(self.fader)
        self.meter = LevelMeter(2)
        mid.addWidget(self.meter)
        lay.addLayout(mid, 1)
        self.lbl = QLabel(f"{self.fader.value() / 10:+.1f} dB")
        self.lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.lbl)
        self.send_sl = {}
        if track and track.kind != "reference":
            for nm, lbl in (("reverb", "Rev"), ("delay", "Dly")):
                r = QHBoxLayout()
                cap = QLabel(lbl)
                cap.setObjectName("muted")
                cap.setFixedWidth(22)
                sl = QSlider(Qt.Horizontal)
                sl.setRange(-600, 60)
                sl.setValue(int(track.sends.get(nm, -60.0) * 10))
                sl.setToolTip(f"Send to the shared {nm} bus (far left = off)")
                sl.valueChanged.connect(lambda v, nm=nm: self._send(nm, v))
                sl.sliderReleased.connect(lambda: self.state.track_modified.emit("__send__"))
                r.addWidget(cap)
                r.addWidget(sl)
                lay.addLayout(r)
                self.send_sl[nm] = sl
        if track:
            b = QHBoxLayout()
            self.m = QToolButton(); self.m.setText("M"); self.m.setCheckable(True); self.m.setChecked(track.mute)
            self.s = QToolButton(); self.s.setText("S"); self.s.setCheckable(True); self.s.setChecked(track.solo)
            self.m.toggled.connect(self._mute); self.s.toggled.connect(self._solo)
            b.addWidget(self.m); b.addWidget(self.s)
            lay.addLayout(b)

    def _send(self, nm, v):
        self.track.sends[nm] = v / 10.0
        self.state.project.dirty = True
        if not any(sl.isSliderDown() for sl in self.send_sl.values()):
            self.state.track_modified.emit("__send__")

    def _vol(self, v):
        self.lbl.setText(f"{v / 10:+.1f} dB")
        if self.track:
            self.track.volume_db = v / 10
            self.state.track_params.emit(self.track.id)
        else:
            self.state.project.master_volume_db = v / 10
            self.state.track_params.emit("__master__")

    def _pan(self, v):
        self.track.pan = v / 100
        self.state.track_params.emit(self.track.id)

    def _mute(self, v):
        self.track.mute = v
        self.state.track_params.emit(self.track.id)

    def _solo(self, v):
        self.track.solo = v
        self.state.track_params.emit(self.track.id)


class ReturnStrip(QFrame):
    """Fader for a shared send bus (Reverb bus / Delay bus)."""

    def __init__(self, state, name, parent=None):
        super().__init__(parent)
        self.state, self.name = state, name
        self.setFixedWidth(92)
        self.setStyleSheet(f"QFrame {{ background: #1f1a2b; border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lbl = QLabel(f"{name.upper()}\nBUS")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("color: #b06cff; font-weight: 600;")
        lay.addWidget(lbl)
        cfg = state.project.sends[name]
        self.fader = QSlider(Qt.Vertical)
        self.fader.setRange(-600, 120)
        self.fader.setValue(int(cfg["volume_db"] * 10))
        self.fader.valueChanged.connect(self._vol)
        lay.addWidget(self.fader, 1, Qt.AlignHCenter)
        self.lbl = QLabel(f"{cfg['volume_db']:+.1f} dB")
        self.lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.lbl)
        self.m = QToolButton(); self.m.setText("M"); self.m.setCheckable(True); self.m.setChecked(cfg["mute"])
        self.m.toggled.connect(self._mute)
        ed = QToolButton(); ed.setText("FX"); ed.setToolTip(f"Edit the {name} bus effect in the FX Rack")
        ed.clicked.connect(lambda: self.state.edit_send_requested.emit(name))
        b = QHBoxLayout(); b.addWidget(self.m); b.addWidget(ed)
        lay.addLayout(b)

    def _vol(self, v):
        self.state.project.sends[self.name]["volume_db"] = v / 10
        self.lbl.setText(f"{v / 10:+.1f} dB")
        self.state.track_params.emit(f"__send_{self.name}__")

    def _mute(self, v):
        self.state.project.sends[self.name]["mute"] = v
        self.state.track_params.emit(f"__send_{self.name}__")


class MixerPanel(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        outer.addWidget(self.scroll)
        self.strips = []
        state.tracks_changed.connect(self.rebuild)
        state.project_replaced.connect(self.rebuild)
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self.rebuild)

    def sync(self):
        for s in self.strips:
            t = s.track
            if t is None:
                continue
            for w, val in ((s.fader, int(t.volume_db * 10)), (s.pan, int(t.pan * 100))):
                if w.value() != val:
                    w.blockSignals(True)
                    w.setValue(val)
                    w.blockSignals(False)
            s.lbl.setText(f"{t.volume_db:+.1f} dB")
            for b, val in ((s.m, t.mute), (s.s, t.solo)):
                if b.isChecked() != val:
                    b.blockSignals(True)
                    b.setChecked(val)
                    b.blockSignals(False)

    def rebuild(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(2, 2, 2, 2)
        self.strips = []
        p = self.state.project
        if p:
            for t in p.tracks:
                s = ChannelStrip(self.state, t)
                h.addWidget(s)
                self.strips.append(s)
            h.addStretch(1)
            for nm in ("reverb", "delay"):
                h.addWidget(ReturnStrip(self.state, nm))
            self.master = ChannelStrip(self.state, None)
            h.addWidget(self.master)
        self.scroll.setWidget(w)

    def set_master_level(self, peaks):
        if getattr(self, "master", None) is not None:
            try:
                self.master.meter.set_levels(peaks)
            except RuntimeError:
                pass


# ============================================================================ Markers
class MarkersPanel(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._jump)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add at playhead (M)"); add.clicked.connect(self.add)
        ren = QPushButton("Rename"); ren.clicked.connect(self._rename)
        dl = QPushButton("Delete"); dl.clicked.connect(self._delete)
        row.addWidget(add); row.addWidget(ren); row.addWidget(dl)
        lay.addLayout(row)
        sec = QHBoxLayout()
        for name in ("Intro", "Verse", "Hook", "Bridge", "Outro"):
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, n=name: self.add(n))
            sec.addWidget(b)
        lay.addLayout(sec)
        state.project_replaced.connect(self.refresh)
        state.view_changed.connect(self.refresh)

    def refresh(self):
        p = self.state.project
        self.list.clear()
        if not p:
            return
        for m in p.markers:
            it = QListWidgetItem(f"{fmt_time(m.pos, p.sr)}   {m.name}")
            it.setData(Qt.UserRole, m.id)
            self.list.addItem(it)

    def add(self, name=None):
        from ..core.project import Marker
        p = self.state.project
        if not p:
            return
        self.state.push_undo("Add marker")
        p.markers.append(Marker(self.state.playhead, name or f"Marker {len(p.markers) + 1}",
                                "section" if name else "cue"))
        p.markers.sort(key=lambda m: m.pos)
        self.state.view_changed.emit()

    def _sel(self):
        it = self.list.currentItem()
        if not it:
            return None
        return next((m for m in self.state.project.markers if m.id == it.data(Qt.UserRole)), None)

    def _jump(self, it):
        m = self._sel()
        if m:
            self.state.set_playhead(m.pos)

    def _rename(self):
        m = self._sel()
        if m:
            n, ok = QInputDialog.getText(self, "Rename marker", "Name:", text=m.name)
            if ok:
                self.state.push_undo("Rename marker")
                m.name = n
                self.state.view_changed.emit()

    def _delete(self):
        m = self._sel()
        if m:
            self.state.push_undo("Delete marker")
            self.state.project.markers.remove(m)
            self.state.view_changed.emit()
