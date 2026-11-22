"""Panels: Take Coach, Ad-lib Library + Creator, Analyzer, Spectral view."""
from __future__ import annotations

import math
import os
import numpy as np
from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QImage, QPainterPath, QBrush
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QDialog, QFormLayout,
                               QDialogButtonBox, QCheckBox, QSpinBox, QDoubleSpinBox, QFileDialog, QMessageBox,
                               QGroupBox, QInputDialog, QToolButton, QSplitter)

from . import theme
from .widgets import fmt_time, LevelMeter
from .workers import run_task
from ..core import coach, adlibs, dsp, analysis

try:
    import sounddevice as sd  # type: ignore
except Exception:  # pragma: no cover
    sd = None


def preview_audio(a, sr):
    if sd is None:
        return
    try:
        sd.stop()
        sd.play(np.clip(a, -1, 1), sr)
    except Exception:
        pass


# ============================================================================ Take Coach
class CoachPanel(QWidget):
    use_take = Signal(str, str)   # track id, clip id

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        tip = QLabel("Scores every take on the selected track: level, clipping, noise, timing on the beat, pitch, "
                     "pops, esses and breaths - and tells you how to nail the next one.")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        lay.addWidget(tip)
        row = QHBoxLayout()
        self.btn = QPushButton("🏆  Rate takes on selected track")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(self.run)
        self.best = QPushButton("Use best take")
        self.best.setEnabled(False)
        self.best.clicked.connect(self._use_best)
        row.addWidget(self.btn, 1)
        row.addWidget(self.best)
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        lay.addWidget(self.list, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        lay.addWidget(self.detail, 1)
        self.results = []
        self.track_id = None

    def run(self):
        p = self.state.project
        t = p.track(self.state.current_track) if self.state.current_track else None
        if not t or not t.clips:
            self.detail.setPlainText("Select a vocal track with takes first.")
            return
        self.btn.setEnabled(False)
        self.btn.setText("Listening to every take…")
        self.track_id = t.id

        def job(task):
            return coach.rank_takes(p, t)

        def done(res):
            self.results = res
            self.list.clear()
            for i, (c, rep) in enumerate(res):
                medal = "🥇" if i == 0 else ("🥈" if i == 1 else ("🥉" if i == 2 else "  "))
                it = QListWidgetItem(f"{medal}  {rep['score']:5.1f}   {c.name}   @ {fmt_time(c.start, p.sr)}")
                self.list.addItem(it)
            self.best.setEnabled(bool(res))
            self.btn.setEnabled(True)
            self.btn.setText("🏆  Rate takes on selected track")
            if res:
                self.list.setCurrentRow(0)

        run_task(job, done, lambda tb: (self.btn.setEnabled(True), self.detail.setPlainText(tb[-600:])))

    def _show(self, row):
        if row < 0 or row >= len(self.results):
            return
        c, rep = self.results[row]
        m = rep["metrics"]
        lines = [f"{c.name}: {rep['score']:.0f}/100", ""]
        lines += ["Tips:"] + [f"  • {t}" for t in rep["tips"]] + ["", "Details:"]
        labels = dict(duration_s="Length (s)", peak_db="Peak (dBFS)", clipped_samples="Clipped samples",
                      lufs="Loudness (LUFS)", snr_db="Voice vs noise (dB)", noise_floor_db="Noise floor (dB)",
                      dynamics_db="Level swings (dB)", timing_ms="Timing off-grid (ms)",
                      syllables_per_sec="Syllables / sec", pitch_drift_cents="Pitch drift (cents)",
                      melodic="Sung (0-1)", pops="Pops", sibilance_db="Esses vs voice (dB)",
                      breaths="Breaths", breaths_per_min="Breaths / min")
        for k, lbl in labels.items():
            if k in m:
                v = m[k]
                lines.append(f"  {lbl:22s} {v:.1f}" if isinstance(v, float) else f"  {lbl:22s} {v}")
        self.detail.setPlainText("\n".join(lines))

    def _use_best(self):
        if self.results and self.track_id:
            self.use_take.emit(self.track_id, self.results[0][0].id)


# ============================================================================ Ad-libs
class AdlibCreator(QDialog):
    def __init__(self, parent=None, default_name="", source_label="selection"):
        super().__init__(parent)
        self.setWindowTitle("Ad-lib Creator")
        f = QFormLayout(self)
        f.addRow(QLabel(f"Save the {source_label} to your ad-lib library."))
        self.name = QLineEdit(default_name or "Yeah")
        f.addRow("Name", self.name)
        self.cat = QComboBox(); self.cat.addItems(adlibs.CATEGORIES)
        f.addRow("Category", self.cat)
        self.style = QComboBox(); self.style.addItems(adlibs.STYLES)
        self.style.setToolTip("Prints a classic ad-lib treatment into the saved sound")
        f.addRow("Style", self.style)
        self.tags = QLineEdit(); self.tags.setPlaceholderText("tags: hype, intro, signature…")
        f.addRow("Tags", self.tags)
        self.author = QLineEdit(); self.author.setPlaceholderText("Red Eyes / Blue Eyes")
        f.addRow("Artist", self.author)
        self.trim = QCheckBox("Trim silence"); self.trim.setChecked(True)
        self.norm = QCheckBox("Normalize"); self.norm.setChecked(True)
        f.addRow(self.trim)
        f.addRow(self.norm)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


class AdlibPanel(QWidget):
    insert_requested = Signal(dict, dict)     # meta, options
    create_from_selection = Signal()

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        mk = QHBoxLayout()
        b1 = QPushButton("➕  Save selection as ad-lib")
        b1.setObjectName("primary")
        b1.setToolTip("Select an ad-lib on a vocal track (drag a range or click a clip), then save it here")
        b1.clicked.connect(self.create_from_selection.emit)
        b2 = QPushButton("Import file…")
        b2.clicked.connect(self._import_file)
        mk.addWidget(b1, 1)
        mk.addWidget(b2)
        lay.addLayout(mk)
        fr = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search ad-libs…")
        self.search.textChanged.connect(self.refresh)
        self.cat = QComboBox(); self.cat.addItems(["All"] + adlibs.CATEGORIES)
        self.cat.currentTextChanged.connect(self.refresh)
        fr.addWidget(self.search, 1)
        fr.addWidget(self.cat)
        lay.addLayout(fr)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda *_: self._insert())
        self.list.currentRowChanged.connect(lambda *_: self._preview() if self.auto_prev.isChecked() else None)
        lay.addWidget(self.list, 1)
        o = QGroupBox("Insert")
        of = QFormLayout(o)
        self.snap = QCheckBox("Snap to beat"); self.snap.setChecked(True)
        of.addRow(self.snap)
        self.every = QComboBox(); self.every.addItems(["Once at playhead", "Every bar in selection",
                                                       "Every 2 bars in selection", "Every 4 bars in selection"])
        of.addRow("Where", self.every)
        self.target = QComboBox(); self.target.addItems(["Selected track (or Ad-libs track)", "New ad-lib track"])
        of.addRow("Track", self.target)
        lay.addWidget(o)
        row = QHBoxLayout()
        pv = QPushButton("▶ Preview"); pv.clicked.connect(self._preview)
        self.auto_prev = QCheckBox("auto")
        ins = QPushButton("Insert ⤵"); ins.setObjectName("primary"); ins.clicked.connect(self._insert)
        fav = QToolButton(); fav.setText("★"); fav.setToolTip("Favourite"); fav.clicked.connect(self._fav)
        ren = QToolButton(); ren.setText("✎"); ren.setToolTip("Rename"); ren.clicked.connect(self._rename)
        de = QToolButton(); de.setText("🗑"); de.setToolTip("Delete from library"); de.clicked.connect(self._delete)
        for w in (pv, self.auto_prev, ins, fav, ren, de):
            row.addWidget(w)
        lay.addLayout(row)
        pk = QHBoxLayout()
        ex = QPushButton("Export pack…"); ex.clicked.connect(self._export)
        im = QPushButton("Import pack…"); im.clicked.connect(self._import_pack)
        op = QPushButton("Open folder"); op.clicked.connect(lambda: os.startfile(adlibs.library_dir()) if hasattr(os, "startfile") else None)
        for w in (ex, im, op):
            pk.addWidget(w)
        lay.addLayout(pk)
        self.items = []
        try:
            adlibs.ensure_starter_pack()
        except Exception:
            pass
        self.refresh()

    def refresh(self):
        self.list.clear()
        self.items = adlibs.list_adlibs(self.search.text(), self.cat.currentText())
        for m in self.items:
            star = "★ " if m.get("favorite") else ""
            it = QListWidgetItem(f"{star}{m['name']}   ·  {m.get('category', '')}  ·  {m.get('length_s', 0):.1f}s"
                                 + (f"  ·  {m['author']}" if m.get("author") else ""))
            it.setToolTip(f"Style: {m.get('style', '')}\nTags: {m.get('tags', '')}\nUsed {m.get('uses', 0)}x")
            self.list.addItem(it)

    def _cur(self):
        r = self.list.currentRow()
        return self.items[r] if 0 <= r < len(self.items) else None

    def _preview(self):
        m = self._cur()
        if m:
            sr = self.state.project.sr if self.state.project else 44100
            preview_audio(adlibs.load_adlib(m["id"], sr), sr)

    def _insert(self):
        m = self._cur()
        if not m:
            return
        every = {0: 0, 1: 1, 2: 2, 3: 4}[self.every.currentIndex()]
        self.insert_requested.emit(m, dict(snap=self.snap.isChecked(), every_bars=every,
                                           new_track=self.target.currentIndex() == 1))

    def save_audio_as_adlib(self, audio, sr, default_name="", label="selection"):
        d = AdlibCreator(self, default_name, label)
        if not d.exec():
            return None
        bpm = self.state.project.bpm if self.state.project else 90.0
        meta = adlibs.save_adlib(audio, sr, d.name.text().strip() or "Ad-lib", d.cat.currentText(),
                                 d.style.currentText(), d.tags.text(), bpm, d.author.text(),
                                 d.trim.isChecked(), d.norm.isChecked())
        self.refresh()
        self.state.status.emit(f"Saved ad-lib '{meta['name']}' to the library")
        return meta

    def _import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Ad-lib audio", "", "Audio (*.wav *.mp3 *.flac *.ogg *.aif *.aiff)")
        if not path:
            return
        from ..core.audio_io import load_audio
        sr = self.state.project.sr if self.state.project else 44100
        a, _ = load_audio(path, sr)
        self.save_audio_as_adlib(a, sr, os.path.splitext(os.path.basename(path))[0], "file")

    def _fav(self):
        m = self._cur()
        if m:
            adlibs.update_meta(m["id"], favorite=not m.get("favorite"))
            self.refresh()

    def _rename(self):
        m = self._cur()
        if m:
            n, ok = QInputDialog.getText(self, "Rename ad-lib", "Name:", text=m["name"])
            if ok and n.strip():
                adlibs.update_meta(m["id"], name=n.strip())
                self.refresh()

    def _delete(self):
        m = self._cur()
        if m and QMessageBox.question(self, "Delete", f"Delete '{m['name']}' from the library?") == QMessageBox.Yes:
            adlibs.delete_adlib(m["id"])
            self.refresh()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export ad-lib pack", "SIB ad-libs.zip", "Zip (*.zip)")
        if path:
            adlibs.export_pack(path)
            self.state.status.emit(f"Ad-lib pack saved: {path}")

    def _import_pack(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import ad-lib pack", "", "Zip (*.zip)")
        if path:
            n = adlibs.import_pack(path)
            self.refresh()
            self.state.status.emit(f"Imported {n} ad-libs")


# ============================================================================ Analyzer
class SpectrumView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.live = None          # (freqs, dB)
        self.peak = None
        self.overlays = []        # [(label, color, freqs, dB)]
        self.mask_bands = []      # [(f_lo, f_hi, severity)]

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#0c0c10"))

        def X(f):
            return (math.log10(max(f, 20)) - math.log10(20)) / 3 * w

        def Y(db):
            return h - (max(-90, min(0, db)) + 90) / 90 * h

        p.setPen(QPen(QColor(theme.GRID), 1))
        p.setFont(QFont("Segoe UI", 7))
        for f in (50, 100, 200, 500, 1000, 2000, 5000, 10000):
            p.drawLine(QPointF(X(f), 0), QPointF(X(f), h))
            p.drawText(QPointF(X(f) + 2, h - 3), f"{f // 1000}k" if f >= 1000 else str(f))
        for db in (-12, -24, -36, -48, -60, -72):
            p.drawLine(QPointF(0, Y(db)), QPointF(w, Y(db)))
        for lo, hi, sev in self.mask_bands:
            p.fillRect(QRectF(X(lo), 0, X(hi) - X(lo), h), QColor(224, 72, 72, int(30 + 80 * sev)))
        for label, col, f, d in self.overlays:
            self._curve(p, f, d, QColor(col), X, Y, 1.6)
        if self.live is not None:
            self._curve(p, *self.live, QColor("#e8e8ee"), X, Y, 1.4, fill=True)
        if self.peak is not None:
            self._curve(p, *self.peak, QColor(240, 180, 41, 160), X, Y, 1.0)
        y = 12
        for label, col, *_ in self.overlays:
            p.setPen(QColor(col))
            p.drawText(QPointF(8, y), label)
            y += 13
        p.end()

    @staticmethod
    def _curve(p, f, d, col, X, Y, width, fill=False):
        path = QPainterPath()
        first = True
        for fi, di in zip(f, d):
            if fi < 20:
                continue
            pt = QPointF(X(fi), Y(di))
            if first:
                path.moveTo(pt)
                first = False
            else:
                path.lineTo(pt)
        if fill and not first:
            fp = QPainterPath(path)
            fp.lineTo(QPointF(X(f[-1]), Y(-90)))
            fp.lineTo(QPointF(X(max(f[1], 20)), Y(-90)))
            c2 = QColor(col)
            c2.setAlpha(40)
            p.fillPath(fp, c2)
        p.setPen(QPen(col, width))
        p.drawPath(path)


def _smooth_spectrum(x, sr, n=4096, bins=160):
    mono = dsp.as2d(x).mean(axis=1)
    if len(mono) < n:
        mono = np.pad(mono, (n - len(mono), 0))
    seg = mono[-n:] * np.hanning(n)
    mag = np.abs(np.fft.rfft(seg)) / (n / 4)
    f = np.fft.rfftfreq(n, 1 / sr)
    edges = np.geomspace(20, sr / 2, bins + 1)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (f >= a) & (f < b)
        out.append(20 * np.log10(np.mean(mag[m]) + 1e-9) if np.any(m) else np.nan)
    out = np.array(out)
    ok = ~np.isnan(out)
    out = np.interp(np.arange(bins), np.flatnonzero(ok), out[ok]) if ok.any() else np.full(bins, -90.0)
    return np.sqrt(edges[:-1] * edges[1:]), out


class AnalyzerPanel(QWidget):
    def __init__(self, state, engine, parent=None):
        super().__init__(parent)
        self.state = state
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.spec = SpectrumView()
        lay.addWidget(self.spec, 2)
        mrow = QHBoxLayout()
        self.lbl = {}
        for k, t in (("m", "Momentary"), ("s", "Short-term"), ("i", "Integrated"), ("tp", "Peak"), ("c", "Correlation")):
            box = QVBoxLayout()
            cap = QLabel(t)
            cap.setObjectName("muted")
            v = QLabel("—")
            v.setStyleSheet("font-size: 15pt; font-weight: 700;")
            box.addWidget(cap)
            box.addWidget(v)
            mrow.addLayout(box)
            self.lbl[k] = v
        lay.addLayout(mrow)
        b = QHBoxLayout()
        self.mask_btn = QPushButton("Find where beat and vocals clash (masking)")
        self.mask_btn.clicked.connect(self.masking)
        clr = QPushButton("Clear"); clr.clicked.connect(self._clear)
        rst = QPushButton("Reset meters"); rst.clicked.connect(self._reset)
        b.addWidget(self.mask_btn, 1); b.addWidget(clr); b.addWidget(rst)
        lay.addLayout(b)
        self.info = QLabel("Play the song to see the live spectrum and loudness.")
        self.info.setWordWrap(True)
        self.info.setObjectName("muted")
        lay.addWidget(self.info)
        self._z = []        # integrated loudness blocks
        self._peak = -120.0
        self._peak_spec = None
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def _reset(self):
        self._z = []
        self._peak = -120.0
        self._peak_spec = None

    def tick(self):
        if not self.isVisible() or not self.engine.playing:
            return
        buf = self.engine.recent_output(int(3.0 * self.engine.sr))
        if buf is None or len(buf) < 4096:
            return
        sr = self.engine.sr
        f, d = _smooth_spectrum(buf, sr)
        self.spec.live = (f, d)
        self._peak_spec = d if self._peak_spec is None else np.maximum(self._peak_spec - 0.3, d)
        self.spec.peak = (f, self._peak_spec)
        self.spec.update()
        mom = analysis.short_term_lufs(buf, sr, 0.4)
        st = analysis.short_term_lufs(buf, sr, 3.0)
        self.lbl["m"].setText(f"{mom:.1f}")
        self.lbl["s"].setText(f"{st:.1f}")
        if mom > -70:
            self._z.append(10 ** ((mom + 0.691) / 10))
            gated = [z for z in self._z if -0.691 + 10 * math.log10(z) > -70]
            if gated:
                rel = -0.691 + 10 * math.log10(np.mean(gated)) - 10
                g2 = [z for z in gated if -0.691 + 10 * math.log10(z) > rel]
                if g2:
                    self.lbl["i"].setText(f"{-0.691 + 10 * math.log10(np.mean(g2)):.1f}")
        pk = float(dsp.lin_to_db(np.max(np.abs(buf[-int(0.1 * sr):]))))
        self._peak = max(self._peak, pk)
        self.lbl["tp"].setText(f"{self._peak:.1f}")
        self.lbl["tp"].setStyleSheet(f"font-size: 15pt; font-weight: 700; color: {theme.RED if self._peak > -0.5 else theme.TEXT};")
        c = dsp.correlation(buf[-int(0.5 * sr):])
        self.lbl["c"].setText(f"{c:+.2f}")
        self.lbl["c"].setStyleSheet(f"font-size: 15pt; font-weight: 700; color: {theme.RED if c < 0 else theme.TEXT};")

    def _clear(self):
        self.spec.overlays = []
        self.spec.mask_bands = []
        self.spec.update()

    def masking(self):
        p = self.state.project
        beats = [t for t in p.beat_tracks() if t.clips]
        vocs = [t for t in p.vocal_tracks() if t.clips and not t.mute]
        if not beats or not vocs:
            self.info.setText("Needs a beat and at least one vocal track.")
            return
        self.mask_btn.setEnabled(False)

        def job(task):
            boff, b = p.track_audio(beats[0])
            bus = p.vocal_bus()
            active = np.max(np.abs(bus), axis=1) > 0.01
            n = min(len(b) + boff, len(bus))
            bb = np.zeros((n, 2), np.float32)
            bb[boff:boff + len(b[: n - boff])] = b[: n - boff]
            sel = active[:n]
            if sel.sum() < p.sr:
                sel[:] = True
            from scipy import signal as sg
            fb, pb = sg.welch(bb[sel].mean(axis=1), fs=p.sr, nperseg=4096)
            fv, pv = sg.welch(bus[:n][sel].mean(axis=1), fs=p.sr, nperseg=4096)
            return fb, 10 * np.log10(pb + 1e-20), 10 * np.log10(pv + 1e-20)

        def done(r):
            f, db_b, db_v = r
            off = max(db_b.max(), db_v.max())
            db_b, db_v = db_b - off, db_v - off
            self.spec.overlays = [("Beat", theme.RED, f, db_b), ("Vocals", theme.BLUE, f, db_v)]
            bands = []
            edges = np.geomspace(150, 8000, 18)
            worst = []
            for a, b in zip(edges[:-1], edges[1:]):
                m = (f >= a) & (f < b)
                if not np.any(m):
                    continue
                vb, vv = np.mean(db_b[m]), np.mean(db_v[m])
                if vv > -50 and vb > vv - 3:
                    sev = float(np.clip((vb - vv + 3) / 12, 0, 1))
                    bands.append((a, b, sev))
                    worst.append(((a + b) / 2, sev))
            self.spec.mask_bands = bands
            self.spec.update()
            self.mask_btn.setEnabled(True)
            if worst:
                worst.sort(key=lambda w: -w[1])
                fz = worst[0][0]
                self.info.setText(f"Red zones = the beat is as loud as (or louder than) your vocal there. Biggest clash around "
                                  f"{fz:.0f} Hz. Fix: put Beat Carve on the beat at ~{fz:.0f} Hz, or boost the vocal EQ "
                                  f"presence near it.")
            else:
                self.info.setText("No serious clashes - the vocal has its own space. ✔")

        run_task(job, done, lambda tb: (self.mask_btn.setEnabled(True), self.info.setText(tb[-300:])))


# ============================================================================ Spectral view
class SpectroCanvas(QWidget):
    box_selected = Signal(float, float, float, float)   # t0, t1 (s, relative), f0, f1 (Hz)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.img = None
        self.dur = 1.0
        self.fmax = 22050
        self.box = None
        self._drag = None

    def set_data(self, f, t, db, dur):
        lo, hi = np.percentile(db, 5), np.percentile(db, 99.5)
        z = np.clip((db - lo) / max(hi - lo, 1e-6), 0, 1)
        # log frequency resampling to 256 rows
        rows = 256
        fr = np.geomspace(40, f[-1], rows)
        idx = np.clip(np.searchsorted(f, fr), 0, len(f) - 1)
        z = z[idx][::-1]
        r = (np.clip(z * 2.2 - 0.6, 0, 1) * 255).astype(np.uint8)
        g = (np.clip(z * 1.6 - 0.5, 0, 1) ** 1.5 * 255).astype(np.uint8)
        b = (np.clip(z * 2.0, 0, 1) * 200 * (1 - np.clip(z * 2 - 1, 0, 1) * 0.5)).astype(np.uint8)
        rgb = np.ascontiguousarray(np.dstack([r, g, b]))
        h, w, _ = rgb.shape
        self._buf = rgb.tobytes()
        self.img = QImage(self._buf, w, h, 3 * w, QImage.Format_RGB888)
        self.dur = dur
        self.fmax = f[-1]
        self.flo = 40.0
        self.box = None
        self.update()

    def _f_of(self, y):
        frac = 1 - y / max(self.height(), 1)
        return float(self.flo * (self.fmax / self.flo) ** frac)

    def _y_of(self, f):
        return (1 - math.log(max(f, self.flo) / self.flo) / math.log(self.fmax / self.flo)) * self.height()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#050507"))
        if self.img is not None:
            p.drawImage(QRectF(0, 0, self.width(), self.height()), self.img)
            p.setPen(QColor(200, 200, 210))
            p.setFont(QFont("Segoe UI", 7))
            for f in (100, 500, 1000, 3000, 8000, 15000):
                if f < self.fmax:
                    y = self._y_of(f)
                    p.drawText(QPointF(3, y), f"{f // 1000}k" if f >= 1000 else str(f))
        else:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Select a clip (or a range) and press Show spectrum")
        if self.box:
            x0, y0, x1, y1 = self.box
            p.setPen(QPen(QColor("#ffffff"), 1, Qt.DashLine))
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRect(QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
        p.end()

    def mousePressEvent(self, e):
        if self.img is None:
            return
        self._drag = (e.position().x(), e.position().y())
        self.box = (*self._drag, *self._drag)

    def mouseMoveEvent(self, e):
        if self._drag:
            self.box = (*self._drag, e.position().x(), e.position().y())
            self.update()

    def mouseReleaseEvent(self, e):
        if not self._drag or not self.box:
            return
        self._drag = None
        x0, y0, x1, y1 = self.box
        w = max(self.width(), 1)
        t0, t1 = sorted((x0 / w * self.dur, x1 / w * self.dur))
        f0, f1 = sorted((self._f_of(y0), self._f_of(y1)))
        if t1 - t0 > 0.005:
            self.box_selected.emit(t0, t1, f0, f1)


class SpectralPanel(QWidget):
    apply_box = Signal(object)   # dict(track, start, end, t0, t1, f0, f1, gain_db)

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        tip = QLabel("Spectral repair: see the sound as a picture. Drag a box around a cough, chair squeak, phone buzz "
                     "or harsh 'S' and turn just that part down - the rest of the vocal stays untouched.")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        lay.addWidget(tip)
        self.canvas = SpectroCanvas()
        self.canvas.box_selected.connect(self._box)
        lay.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        sh = QPushButton("Show spectrum"); sh.clicked.connect(self.show_spec)
        row.addWidget(sh)
        row.addWidget(QLabel("Reduce box by"))
        self.gain = QDoubleSpinBox(); self.gain.setRange(-80, 0); self.gain.setValue(-30); self.gain.setSuffix(" dB")
        row.addWidget(self.gain)
        self.apply_btn = QPushButton("Apply to box"); self.apply_btn.setEnabled(False)
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self._apply)
        row.addWidget(self.apply_btn)
        lay.addLayout(row)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        lay.addWidget(self.info)
        self.src = None
        self.sel = None

    def show_spec(self):
        p = self.state.project
        t = p.track(self.state.current_track) if self.state.current_track else None
        if not t:
            self.info.setText("Select a track first")
            return
        if self.state.has_range:
            a, b = self.state.range()
        else:
            sel = [c for c in t.clips if c.id in self.state.selected_clips] or t.clips[:1]
            if not sel:
                self.info.setText("No audio on this track")
                return
            a, b = sel[0].start, sel[0].end
        b = min(b, a + p.sr * 30)
        off, audio = p.render_track_dry(t, a, b)
        self.src = (t.id, a, b)
        f, tt, db = dsp.spectrogram_db(audio, p.sr)
        self.canvas.set_data(f, tt, db, (b - a) / p.sr)
        self.info.setText(f"{t.name}: {fmt_time(a, p.sr)} – {fmt_time(b, p.sr)}. Drag a box over the noise.")
        self.apply_btn.setEnabled(False)

    def _box(self, t0, t1, f0, f1):
        self.sel = (t0, t1, f0, f1)
        self.apply_btn.setEnabled(True)
        self.info.setText(f"Box: {t0:.2f}-{t1:.2f}s, {f0:.0f}-{f1:.0f} Hz")

    def _apply(self):
        if not self.src or not self.sel:
            return
        tid, a, b = self.src
        t0, t1, f0, f1 = self.sel
        self.apply_box.emit(dict(track=tid, start=a, end=b, t0=t0, t1=t1, f0=f0, f1=f1, gain_db=self.gain.value()))
