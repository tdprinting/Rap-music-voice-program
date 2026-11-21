"""Dialogs: audio settings, export, Audition export, new project, about/credits."""
from __future__ import annotations

import os
from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (QDialog, QFormLayout, QComboBox, QSpinBox, QDoubleSpinBox, QDialogButtonBox,
                               QVBoxLayout, QLabel, QCheckBox, QLineEdit, QPushButton, QHBoxLayout, QFileDialog,
                               QGroupBox, QRadioButton, QButtonGroup)

from .. import APP_NAME, APP_LONG_NAME, __version__, CREATORS
from . import theme
from .audio_engine import list_devices, default_devices
from ..core.audition import find_audition


def settings():
    return QSettings("SIB", "SIB Studio Mixer")


class AudioSettingsDialog(QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audio settings")
        self.engine = engine
        f = QFormLayout(self)
        ins, outs = list_devices()
        di, do = default_devices()
        self.inp = QComboBox()
        self.inp.addItem("System default", -1)
        for i, lbl, ch, sr in ins:
            self.inp.addItem(f"{lbl}  ({ch} ch)", i)
        self.out = QComboBox()
        self.out.addItem("System default", -1)
        for i, lbl, ch, sr in outs:
            self.out.addItem(lbl, i)
        self._select(self.inp, engine.in_device)
        self._select(self.out, engine.out_device)
        f.addRow("Microphone / interface input", self.inp)
        self.ch = QSpinBox(); self.ch.setRange(1, 32); self.ch.setValue(engine.input_channel + 1)
        f.addRow("Input channel", self.ch)
        f.addRow("Output (headphones)", self.out)
        self.sr = QComboBox()
        for r in (44100, 48000, 88200, 96000):
            self.sr.addItem(str(r), r)
        self.sr.setCurrentText(str(engine.sr))
        f.addRow("Sample rate (new projects)", self.sr)
        self.bs = QComboBox()
        for b in (64, 128, 256, 512, 1024, 2048):
            self.bs.addItem(f"{b} samples ({b / engine.sr * 1000:.1f} ms)", b)
        self._select(self.bs, engine.blocksize)
        f.addRow("Buffer size", self.bs)
        self.lat = QDoubleSpinBox(); self.lat.setRange(-200, 500); self.lat.setValue(engine.latency_comp_ms)
        self.lat.setSuffix(" ms")
        self.lat.setToolTip("Extra recording offset. If your takes land late, increase this.")
        f.addRow("Recording latency fix", self.lat)
        hint = QLabel("Tip: pick a device marked [Windows WASAPI] or [ASIO] for the lowest latency.\n"
                      "Use headphones while recording so the beat doesn't bleed into the mic.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        f.addRow(hint)
        if not engine.available:
            f.addRow(QLabel("⚠ 'sounddevice' is not installed - playback/recording disabled."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    @staticmethod
    def _select(combo, value):
        for i in range(combo.count()):
            if combo.itemData(i) == (value if value is not None else -1):
                combo.setCurrentIndex(i)
                return

    def apply(self):
        e = self.engine
        e.configure(blocksize=self.bs.currentData(), in_device=self.inp.currentData(),
                    out_device=self.out.currentData(), input_channel=self.ch.value() - 1,
                    latency_comp_ms=self.lat.value())
        s = settings()
        s.setValue("audio/in", self.inp.currentData())
        s.setValue("audio/out", self.out.currentData())
        s.setValue("audio/ch", self.ch.value() - 1)
        s.setValue("audio/bs", self.bs.currentData())
        s.setValue("audio/lat", self.lat.value())
        s.setValue("audio/sr", self.sr.currentData())


class ExportDialog(QDialog):
    def __init__(self, project, has_selection, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export mixdown")
        f = QFormLayout(self)
        row = QHBoxLayout()
        self.path = QLineEdit(os.path.join(project.folder, "Exports", f"{project.name}.wav"))
        b = QPushButton("…")
        b.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(b)
        f.addRow("File", row)
        self.fmt = QComboBox()
        self.fmt.addItems(["WAV 24-bit", "WAV 16-bit", "WAV 32-bit float", "MP3 320 kbps", "FLAC 24-bit"])
        self.fmt.currentTextChanged.connect(self._fmt)
        f.addRow("Format", self.fmt)
        self.range = QComboBox()
        self.range.addItems(["Whole song"] + (["Selection"] if has_selection else []) + (["Loop region"] if project.loop else []))
        f.addRow("Range", self.range)
        self.master = QCheckBox("Apply master chain (glue + loudness)")
        self.master.setChecked(True)
        f.addRow(self.master)
        self.lufs = QDoubleSpinBox(); self.lufs.setRange(-24, -6); self.lufs.setSuffix(" LUFS")
        m = next((e for e in project.master_fx if e["id"] == "mastering"), None)
        self.lufs.setValue(m["params"]["target_lufs"] if m else -10.0)
        self.lufs.setToolTip("-14 for streaming-normalised, -9 to -11 for a loud rap master")
        f.addRow("Loudness target", self.lufs)
        self.stems = QCheckBox("Also export every track as a stem (with its FX)")
        f.addRow(self.stems)
        self.dry_stems = QCheckBox("Also export dry (unprocessed) stems")
        f.addRow(self.dry_stems)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _fmt(self, t):
        ext = ".mp3" if t.startswith("MP3") else ".flac" if t.startswith("FLAC") else ".wav"
        self.path.setText(os.path.splitext(self.path.text())[0] + ext)

    def _browse(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export to", self.path.text(),
                                           "Audio (*.wav *.mp3 *.flac)")
        if p:
            self.path.setText(p)

    def bit_depth(self):
        t = self.fmt.currentText()
        return 16 if "16" in t else "float" if "float" in t else 24


class AuditionDialog(QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export to Adobe Audition")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Creates an Audition multitrack session (.sesx) with all your tracks,\n"
                             "clip positions, fades, volumes, tempo and markers."))
        g = QGroupBox("What to send")
        gl = QVBoxLayout(g)
        self.both = QRadioButton("Processed tracks + dry originals (dry ones muted)  — recommended")
        self.proc = QRadioButton("Processed tracks only (SIB effects printed)")
        self.dry = QRadioButton("Dry clips only (edit everything in Audition)")
        self.both.setChecked(True)
        for r in (self.both, self.proc, self.dry):
            gl.addWidget(r)
        lay.addWidget(g)
        self.breaths = QCheckBox("Add a marker at every detected breath")
        lay.addWidget(self.breaths)
        row = QHBoxLayout()
        self.dir = QLineEdit(os.path.join(project.folder, "Audition"))
        b = QPushButton("…")
        b.clicked.connect(lambda: self.dir.setText(QFileDialog.getExistingDirectory(self, "Folder", self.dir.text()) or self.dir.text()))
        row.addWidget(self.dir, 1)
        row.addWidget(b)
        lay.addLayout(row)
        exe = find_audition()
        self.open_after = QCheckBox("Open in Audition when done" + ("" if exe else " (Audition not found - will use file association)"))
        self.open_after.setChecked(True)
        lay.addWidget(self.open_after)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def mode(self):
        return "both" if self.both.isChecked() else "processed" if self.proc.isChecked() else "dry"


class NewProjectDialog(QDialog):
    def __init__(self, default_dir, default_sr=44100, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New project")
        f = QFormLayout(self)
        self.name = QLineEdit("New Song")
        f.addRow("Song name", self.name)
        row = QHBoxLayout()
        self.dir = QLineEdit(default_dir)
        b = QPushButton("…")
        b.clicked.connect(lambda: self.dir.setText(QFileDialog.getExistingDirectory(self, "Folder", self.dir.text()) or self.dir.text()))
        row.addWidget(self.dir, 1)
        row.addWidget(b)
        f.addRow("Projects folder", row)
        self.sr = QComboBox()
        for r in (44100, 48000):
            self.sr.addItem(str(r), r)
        self.sr.setCurrentText(str(default_sr))
        f.addRow("Sample rate", self.sr)
        self.tracks = QSpinBox(); self.tracks.setRange(1, 12); self.tracks.setValue(3)
        f.addRow("Vocal tracks", self.tracks)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        t = QLabel(f"<div style='text-align:center'>"
                   f"<span style='font-size:22pt;font-weight:800;color:{theme.RED}'>S</span>"
                   f"<span style='font-size:22pt;font-weight:800;color:{theme.TEXT}'>I</span>"
                   f"<span style='font-size:22pt;font-weight:800;color:{theme.BLUE}'>B</span>"
                   f"<br><b style='font-size:14pt'>{APP_NAME}</b><br>{APP_LONG_NAME}<br>"
                   f"<span style='color:{theme.MUTED}'>Version {__version__}</span></div>")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        cols = [theme.RED, theme.BLUE]
        cred = "<br>".join(
            f"<span style='font-size:12pt'><b>{n}</b> &nbsp;<span style='color:{cols[i % 2]}'>({nick})</span></span>"
            for i, (n, nick) in enumerate(CREATORS))
        c = QLabel(f"<div style='text-align:center'><span style='color:{theme.MUTED}'>CREATED BY</span><br>{cred}</div>")
        c.setAlignment(Qt.AlignCenter)
        lay.addWidget(c)
        d = QLabel("Record over beats, auto-match vocals to the beat's key and tempo, tune, de-breath,\n"
                   "clean and polish rap vocals to studio quality — and send it all to Adobe Audition.")
        d.setAlignment(Qt.AlignCenter)
        d.setObjectName("muted")
        lay.addWidget(d)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
