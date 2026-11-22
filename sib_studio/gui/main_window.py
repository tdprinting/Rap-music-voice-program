"""SIB Studio Mixer main window."""
from __future__ import annotations

import copy
import os
import numpy as np
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QAction, QKeySequence, QIcon, QActionGroup
from PySide6.QtWidgets import (QMainWindow, QDockWidget, QTabWidget, QToolBar, QLabel, QWidget, QHBoxLayout,
                               QFileDialog, QMessageBox, QInputDialog, QMenu, QComboBox, QApplication,
                               QLineEdit, QAbstractSpinBox, QPlainTextEdit, QTextEdit, QProgressDialog, QToolButton,
                               QSizePolicy)

from .. import APP_NAME, __version__
from ..core import dsp, analysis, presets
from ..core import pitch as pitch_mod
from ..core.audio_io import save_audio, AUDIO_EXTENSIONS
from ..core.project import (SEND_NAMES, Project, PROJECT_EXT, default_projects_dir, delete_range, copy_range, paste,
                            split_clip, split_at, trim_to_range, process_range, bounce_track, FX_TAIL_S, uid)
from ..core.effects import process_chain, new_instance
from ..core import audition
from . import theme
from .state import StudioState
from .timeline import TimelineArea
from .fx_panel import FXRack
from .panels import AutoMatchPanel, BreathPanel, PitchPanel, MixerPanel, MarkersPanel
from .dialogs import AudioSettingsDialog, ExportDialog, AuditionDialog, NewProjectDialog, AboutDialog, settings
from .audio_engine import AudioEngine
from .widgets import LevelMeter, fmt_time
from .workers import run_task
from .features import FeatureMixin

AUDIO_FILTER = "Audio (" + " ".join(f"*{e}" for e in AUDIO_EXTENSIONS) + ")"

QUICK_START = """<h3>Quick start</h3>
<ol>
<li><b>File &gt; Import Beat</b> (Ctrl+B). SIB listens to it and finds the key, tempo and tuning.</li>
<li>Arm a vocal track (●), put on headphones and press <b>R</b> to record over the beat
(count-in and metronome in the transport bar). Press <b>Space</b> to stop. Each pass is a new take;
right-click a take &gt; <i>Use this take</i> to comp.</li>
<li>Or import vocals you already recorded: <b>File &gt; Import Vocal</b> (Ctrl+I) or drag files onto the tracks.</li>
<li>Press <b>Ctrl+M</b> (Auto-Match). SIB tunes the vocals to the beat's key, sets EQ, compression, de-essing,
noise and breath control, syncs delay and reverb to the BPM, levels the vocals against the beat and carves room
in the beat.</li>
<li>Fine-tune anything in the <b>FX Rack</b>: every setting is there. Use the <b>FX</b> button on a track to A/B.</li>
<li><b>Breaths</b> tab: detect, review and turn down breaths without changing timing.</li>
<li><b>File &gt; Export Mixdown</b> (Ctrl+E) for a loud, clean master, or <b>Export to Adobe Audition</b>.</li>
</ol>"""

SHORTCUTS = """<h3>Keyboard shortcuts</h3><table>
<tr><td>Space</td><td>Play / stop</td></tr><tr><td>R</td><td>Record</td></tr>
<tr><td>Home / End</td><td>Go to start / end</td></tr><tr><td>L</td><td>Loop on/off (Shift-drag ruler to set)</td></tr>
<tr><td>S</td><td>Split at playhead</td></tr><tr><td>M</td><td>Add marker</td></tr>
<tr><td>Del / Shift+Del</td><td>Delete / ripple delete</td></tr><tr><td>Ctrl+X C V</td><td>Cut, copy, paste</td></tr>
<tr><td>Ctrl+D</td><td>Duplicate clip</td></tr><tr><td>Alt+← / →</td><td>Nudge clip 10 ms</td></tr>
<tr><td>Ctrl+Z / Ctrl+Y</td><td>Undo / redo</td></tr><tr><td>= / -</td><td>Zoom in / out</td></tr>
<tr><td>F</td><td>Zoom to fit</td></tr><tr><td>Ctrl+wheel</td><td>Zoom at mouse</td></tr>
<tr><td>Shift+wheel</td><td>Scroll sideways</td></tr><tr><td>Alt while dragging</td><td>Move without snapping</td></tr>
<tr><td>Ctrl+M</td><td>Auto-Match vocals to beat</td></tr>
<tr><td>Ctrl+L</td><td>Teleprompter</td></tr><tr><td>T</td><td>Tap-sync the next lyric line</td></tr>
<tr><td>Ctrl+Shift+I</td><td>Import Adobe Audition session</td></tr><tr><td>Ctrl+T</td><td>New vocal track</td></tr>
</table>"""

TIPS = """<h3>Rap vocal tips</h3><ul>
<li>Record peaks around -18 to -12 dBFS, 4-6 inches from the mic, with a pop filter.</li>
<li>Record 3-4 takes of each section and comp the best lines (takes stack on the same track).</li>
<li>Doubles: record the exact same lines again, set the track role to <i>Double</i>, pan them left/right ~25%.
No time? Track &gt; <i>Make instant double</i>.</li>
<li>Ad-libs: role <i>Ad-lib</i> - quieter, panned wide, more delay and reverb.</li>
<li>Most beats are minor keys; if the tuning sounds off, check the beat's real tuning (A4) in Auto-Match - many beats are not at 440 Hz.</li>
<li>Retune 0-10 ms = hard robotic tune, 15-25 ms = melodic rap, 30-80 ms = natural.</li>
<li>Turn breaths down 8-15 dB instead of deleting them - fully silent gaps sound robotic.</li>
<li>Don't EQ in solo: judge the vocal with the beat playing.</li>
<li>Master around -14 LUFS for streaming or -9 to -11 LUFS for a loud rap master, ceiling -1 dBTP.</li></ul>"""


class MainWindow(FeatureMixin, QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1500, 900)
        self.state = StudioState()
        self.engine = AudioEngine()
        self._load_audio_settings()
        self.rec_track = None
        self.play_start = 0
        self.ab_dry = False
        self.noise_profile = None
        self._was_playing = False
        self._busy = False
        self._last_sesx = None

        self.timeline = TimelineArea(self.state)
        self.timeline.lanes.context_menu_cb = self._clip_menu
        self.timeline.drop_cb = self._dropped
        self.setCentralWidget(self.timeline)

        self.fx = FXRack(self.state)
        self.fx.apply_destructive.connect(self._apply_effect_destructive)
        self.auto = AutoMatchPanel(self.state)
        self.breaths = BreathPanel(self.state)
        self.pitch = PitchPanel(self.state)
        self.markers = MarkersPanel(self.state)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.auto, "Auto-Match")
        self.tabs.addTab(self.fx, "FX Rack")
        self.tabs.addTab(self.breaths, "Breaths")
        self.tabs.addTab(self.pitch, "Pitch / Key")
        self.tabs.addTab(self.markers, "Markers")
        self._init_features()
        self._add_feature_tabs()
        self.right = QDockWidget("Inspector", self)
        self.right.setObjectName("inspector")
        self.right.setWidget(self.tabs)
        self.right.setMinimumWidth(400)
        self.addDockWidget(Qt.RightDockWidgetArea, self.right)
        self.mixer = MixerPanel(self.state)
        self.bottom = QDockWidget("Mixer", self)
        self.bottom.setObjectName("mixer")
        self.bottom.setWidget(self.mixer)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.bottom)
        self.bottom.setVisible(False)

        self._build_actions()
        self._build_menus()
        self._build_transport()
        self.status_lbl = QLabel("Ready")
        self.sel_lbl = QLabel("")
        self.statusBar().addWidget(self.status_lbl, 1)
        self.statusBar().addPermanentWidget(self.sel_lbl)

        self.state.status.connect(self.status_lbl.setText)
        self.state.track_params.connect(self._on_params)
        self.state.track_modified.connect(self._on_modified)
        self.state.undo_changed.connect(self._update_undo)
        self.state.selection_changed.connect(self._update_sel)
        self.state.playhead_moved.connect(self._playhead_moved)
        self.state.project_replaced.connect(self._project_loaded)
        self.state.tracks_changed.connect(self._update_title)

        self._rerender_timer = QTimer(self)
        self._rerender_timer.setSingleShot(True)
        self._rerender_timer.setInterval(250)
        self._rerender_timer.timeout.connect(self._rerender_live)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(33)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()
        self._autosave = QTimer(self)
        self._autosave.setInterval(180000)
        self._autosave.timeout.connect(self._do_autosave)
        self._autosave.start()

        st = settings()
        geo = st.value("ui/geometry")
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass
        self._new_default()

    # ================================================================== setup
    def _load_audio_settings(self):
        s = settings()
        def g(k, d, typ):
            v = s.value(k, d)
            try:
                return typ(v)
            except Exception:
                return d
        self.engine.configure(blocksize=g("audio/bs", 512, int), in_device=g("audio/in", -1, int),
                              out_device=g("audio/out", -1, int), input_channel=g("audio/ch", 0, int),
                              latency_comp_ms=g("audio/lat", 0.0, float))
        self.default_sr = g("audio/sr", 44100, int)
        if self.default_sr not in (44100, 48000, 88200, 96000):
            self.default_sr = 44100
        if self.engine.blocksize not in (64, 128, 256, 512, 1024, 2048):
            self.engine.blocksize = 512

    def _act(self, text, slot, shortcut=None, checkable=False, tip=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.triggered.connect(slot)
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.a_new = A("&New project…", self.new_project, "Ctrl+N")
        self.a_open = A("&Open project…", self.open_project, "Ctrl+O")
        self.a_save = A("&Save", self.save, "Ctrl+S")
        self.a_saveas = A("Save &As…", self.save_as, "Ctrl+Shift+S")
        self.a_imp_beat = A("Import &Beat…", self.import_beat, "Ctrl+B")
        self.a_imp_voc = A("Import &Vocal…", self.import_vocal, "Ctrl+I")
        self.a_export = A("&Export Mixdown…", self.export_mix, "Ctrl+E")
        self.a_audition = A("Export to Adobe &Audition…", self.export_audition, "Ctrl+Shift+A")
        self.a_quit = A("E&xit", self.close, "Ctrl+Q")
        self.a_undo = A("Undo", self.undo, "Ctrl+Z")
        self.a_redo = A("Redo", self.redo, "Ctrl+Y")
        self.a_redo2 = A("Redo", self.redo, "Ctrl+Shift+Z")
        self.a_cut = A("Cut", self.cut, "Ctrl+X")
        self.a_copy = A("Copy", self.copy, "Ctrl+C")
        self.a_paste = A("Paste at playhead", self.paste, "Ctrl+V")
        self.a_del = A("Delete", self.delete, "Delete")
        self.a_ripple = A("Ripple delete (close the gap)", lambda: self.delete(ripple=True), "Shift+Delete")
        self.a_split = A("Split at playhead", self.split, "S")
        self.a_trim = A("Trim to selection", self.trim)
        self.a_dup = A("Duplicate clip", self.duplicate_clip, "Ctrl+D")
        self.a_selall = A("Select all", self.select_all, "Ctrl+A")
        self.a_desel = A("Deselect", self.deselect, "Escape")
        self.a_nudge_l = A("Nudge left", lambda: self.nudge(-10), "Alt+Left")
        self.a_nudge_r = A("Nudge right", lambda: self.nudge(10), "Alt+Right")
        self.a_snap = A("Snap to beat grid", self._snap_toggled, None, True)
        self.a_snap.setChecked(True)
        self.a_play = A("Play / Stop", self.toggle_play, "Space")
        self.a_rec = A("Record", self.toggle_record, "R")
        self.a_home = A("Go to start", lambda: self._seek(0), "Home")
        self.a_end = A("Go to end", lambda: self._seek(self.state.project.length if self.state.project else 0), "End")
        self.a_loop = A("Loop", self._loop_toggled, "L", True)
        self.a_metro = A("Metronome", lambda v: setattr(self.engine, "metronome", v), None, True)
        self.a_metro_play = A("Metronome during playback too", lambda v: setattr(self.engine, "metronome_rec_only", not v), None, True)
        self.a_monitor = A("Input monitoring (hear yourself)", self._monitor_toggled, None, True)
        self.a_ab = A("A/B: hear dry (no effects)", self._ab_toggled, None, True,
                      "Compare the raw vocals with the processed sound")
        self.a_master_prev = A("Preview master chain", self._master_prev_toggled, None, True,
                               "Hear the master/loudness chain while playing (renders the full mix first)")
        self.a_follow = A("Follow playhead", lambda v: setattr(self.timeline.view, "follow", v), None, True)
        self.a_follow.setChecked(True)
        self.a_marker = A("Add marker", lambda: self.markers.add(), "M")
        self.a_zoom_in = A("Zoom in", lambda: self.timeline.zoom(0.7), "=")
        self.a_zoom_out = A("Zoom out", lambda: self.timeline.zoom(1.4), "-")
        self.a_zoom_fit = A("Zoom to fit", self.timeline.zoom_fit, "F")
        self.a_add_voc = A("Add vocal track", lambda: self.add_track("vocal", "Lead"), "Ctrl+T")
        self.a_add_beat = A("Add beat track", lambda: self.add_track("beat"))
        self.a_del_track = A("Delete track", self.delete_track)
        self.a_dup_track = A("Duplicate track", self.duplicate_track)
        self.a_bounce = A("Bounce track (print effects)", self.bounce)
        self.a_double = A("Make instant double", self.instant_double)
        self.a_up = A("Move track up", lambda: self.move_track(-1))
        self.a_down = A("Move track down", lambda: self.move_track(1))
        self.a_automatch = A("Auto-Match vocals to beat", self._automatch, "Ctrl+M")
        self.a_analyze = A("Listen to beat (analyze key/BPM)", lambda: (self._show_tab(self.auto), self.auto.analyze()))
        self.a_settings = A("Audio settings…", self.audio_settings, "Ctrl+,")
        self._feature_actions()

    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        for a in (self.a_new, self.a_open):
            f.addAction(a)
        self.recent_menu = f.addMenu("Open recent")
        self._fill_recent()
        f.addSeparator()
        for a in (self.a_save, self.a_saveas):
            f.addAction(a)
        f.addSeparator()
        f.addAction(self.a_imp_beat)
        f.addAction(self.a_imp_voc)
        f.addSeparator()
        f.addAction(self.a_export)
        f.addAction(self.a_audition)
        f.addAction(self._act("Open last session in Audition", self.open_in_audition))
        f.addSeparator()
        f.addAction(self.a_quit)
        e = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo, None, self.a_cut, self.a_copy, self.a_paste, self.a_del, self.a_ripple,
                  None, self.a_split, self.a_trim, self.a_dup, self.a_nudge_l, self.a_nudge_r, None,
                  self.a_selall, self.a_desel, None, self.a_snap):
            e.addSeparator() if a is None else e.addAction(a)
        pr = mb.addMenu("&Process")
        hint = pr.addAction("Works on the selection, or the selected clip(s)")
        hint.setEnabled(False)
        pr.addAction(self._act("Amplify…", self.p_amplify))
        pr.addAction(self._act("Normalize (peak -1 dB)", lambda: self._process("Normalize", lambda a: dsp.normalize_peak(a, -1.0))))
        pr.addAction(self._act("Normalize to loudness…", self.p_lufs))
        pr.addAction(self._act("Fade in", lambda: self._process("Fade in", lambda a: dsp.apply_fade(a, len(a), 0, "log"), xf=0)))
        pr.addAction(self._act("Fade out", lambda: self._process("Fade out", lambda a: dsp.apply_fade(a, 0, len(a), "log"), xf=0)))
        pr.addAction(self._act("Silence", lambda: self._process("Silence", lambda a: np.zeros_like(a))))
        pr.addAction(self._act("Reverse", lambda: self._process("Reverse", lambda a: a[::-1].copy())))
        pr.addAction(self._act("Invert polarity", lambda: self._process("Invert", lambda a: -a)))
        pr.addSeparator()
        pr.addAction(self._act("Capture noise print (from selection)", self.p_capture_noise))
        pr.addAction(self._act("Noise reduction…", self.p_noise))
        pr.addAction(self._act("Remove mouth clicks", lambda: self._process_bg("De-click", lambda a, sr: dsp.declick(a, sr))))
        pr.addAction(self._act("Remove pops (plosives)", lambda: self._process_bg("De-plosive", lambda a, sr: dsp.deplosive(a, sr))))
        pr.addAction(self._act("Remove hum (60 Hz)", lambda: self._process_bg("De-hum", lambda a, sr: dsp.hum_remover(a, sr, 60))))
        pr.addAction(self._act("Breath control… (Breaths tab)", lambda: self._show_tab(self.breaths)))
        pr.addSeparator()
        pr.addAction(self._act("Pitch shift…", self.p_pitch))
        pr.addAction(self._act("Time stretch clip…", self.p_stretch))
        pr.addAction(self._act("Hard auto-tune selection", lambda: self._apply_effect_destructive(
            new_instance("sib_tune", retune_ms=0.0, humanize=0.0, flex_cents=0.0))))
        tr = mb.addMenu("&Track")
        add = tr.addMenu("Add vocal track")
        for role in presets.ROLES[:-1]:
            add.addAction(self._act(role, lambda _=False, r=role: self.add_track("vocal", r)))
        for a in (self.a_add_beat, None, self.a_dup_track, self.a_double, self.a_bounce, None, self.a_up, self.a_down,
                  None, self.a_del_track):
            tr.addSeparator() if a is None else tr.addAction(a)
        tp = mb.addMenu("T&ransport")
        for a in (self.a_play, self.a_rec, self.a_home, self.a_end, None, self.a_loop, self.a_metro,
                  self.a_metro_play, self.a_monitor, None, self.a_ab, self.a_master_prev):
            tp.addSeparator() if a is None else tp.addAction(a)
        tl = mb.addMenu("&Tools")
        for a in (self.a_analyze, self.a_automatch, None, self.a_marker, None, self.a_settings):
            tl.addSeparator() if a is None else tl.addAction(a)
        v = mb.addMenu("&View")
        for a in (self.a_zoom_in, self.a_zoom_out, self.a_zoom_fit, self.a_follow):
            v.addAction(a)
        v.addSeparator()
        v.addAction(self.right.toggleViewAction())
        v.addAction(self.bottom.toggleViewAction())
        sb = self._act("Show breath markers", lambda on: (setattr(self.state, "show_breaths", on), self.timeline.lanes.invalidate()), None, True)
        sb.setChecked(True)
        v.addAction(sb)
        self._feature_menus(mb)
        h = mb.addMenu("&Help")
        h.addAction(self._act("Quick start", lambda: self._html("Quick start", QUICK_START), "F1"))
        h.addAction(self._act("Rap vocal tips", lambda: self._html("Tips", TIPS)))
        h.addAction(self._act("Keyboard shortcuts", lambda: self._html("Shortcuts", SHORTCUTS)))
        h.addSeparator()
        h.addAction(self._act(f"About {APP_NAME} / Credits", lambda: AboutDialog(self).exec()))

    def _build_transport(self):
        tb = QToolBar("Transport")
        tb.setObjectName("transport")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(Qt.TopToolBarArea, tb)
        logo = QLabel(f"<b><span style='color:{theme.RED}'>S</span>I<span style='color:{theme.BLUE}'>B</span></b>  ")
        logo.setStyleSheet("font-size: 15pt;")
        tb.addWidget(logo)

        def btn(text, act, tip, name=None, w=None):
            act.setIconText(text)
            act.setToolTip(tip)
            b = QToolButton()
            b.setDefaultAction(act)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            if name:
                b.setObjectName(name)
            if w:
                b.setFixedWidth(w)
            tb.addWidget(b)
            return b

        btn("⏮", self.a_home, "Go to start (Home)", w=36)
        self.play_btn = btn("▶", self.a_play, "Play / stop (Space)", w=44)
        self.rec_btn = QToolButton()
        self.rec_btn.setText("●  REC")
        self.rec_btn.setObjectName("rec")
        self.rec_btn.setCheckable(True)
        self.rec_btn.setToolTip("Record onto the armed vocal track (R)")
        self.rec_btn.clicked.connect(lambda: (self.rec_btn.setChecked(self.engine.recording), self.toggle_record()))
        tb.addWidget(self.rec_btn)
        btn("⟲ Loop", self.a_loop, "Loop playback / loop-record takes (L). Shift-drag the ruler to set the loop.")
        btn("♩ Click", self.a_metro, "Metronome")
        tb.addWidget(QLabel(" Count-in "))
        self.countin = QComboBox()
        self.countin.addItems(["Off", "1 bar", "2 bars"])
        self.countin.setCurrentIndex(1)
        tb.addWidget(self.countin)
        btn("🎧 Monitor", self.a_monitor, "Hear your mic in the headphones (dry)")
        tb.addSeparator()
        self.time_lbl = QLabel("0:00.000")
        self.time_lbl.setStyleSheet(f"font-family: Consolas, monospace; font-size: 17pt; color: {theme.TEXT}; padding: 0 10px;")
        tb.addWidget(self.time_lbl)
        self.bar_lbl = QLabel("1.1")
        self.bar_lbl.setStyleSheet(f"font-family: Consolas, monospace; font-size: 12pt; color: {theme.MUTED};")
        tb.addWidget(self.bar_lbl)
        self.key_lbl = QLabel("")
        self.key_lbl.setStyleSheet(f"font-size: 11pt; color: {theme.BLUE}; padding: 0 12px; font-weight: 600;")
        tb.addWidget(self.key_lbl)
        tb.addSeparator()
        btn("Snap", self.a_snap, "Snap to the beat grid")
        btn("A/B Dry", self.a_ab, "Hear the raw vocals without effects")
        btn("Master", self.a_master_prev, "Preview the master chain")
        self._feature_transport(tb, btn)
        am = QToolButton()
        am.setText("✨ Auto-Match")
        am.setObjectName("primary")
        am.setToolTip("Listen to the beat and match the vocals to it (Ctrl+M)")
        am.clicked.connect(self._automatch)
        tb.addWidget(am)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addWidget(QLabel("IN "))
        self.in_meter = LevelMeter(1, vertical=False)
        self.in_meter.setFixedWidth(110)
        tb.addWidget(self.in_meter)
        tb.addWidget(QLabel("  OUT "))
        self.out_meter = LevelMeter(2, vertical=False)
        self.out_meter.setFixedWidth(140)
        tb.addWidget(self.out_meter)

    # ================================================================== project lifecycle
    def _new_default(self):
        p = Project("Untitled", self.default_sr)
        p.add_track("Beat", "beat")
        p.add_track("Lead Vocal", "vocal", "Lead")
        p.add_track("Double", "vocal", "Double")
        p.add_track("Ad-libs", "vocal", "Ad-lib")
        p.dirty = False
        self.state.set_project(p)

    def _project_loaded(self):
        p = self.state.project
        self.engine.configure(sr=p.sr)
        self.timeline.waves.clear()
        self.timeline.rebuild()
        QTimer.singleShot(50, self.timeline.zoom_fit)
        self._update_title()
        self._update_key_lbl()

    def _update_title(self):
        p = self.state.project
        self.setWindowTitle(f"{p.name}{' *' if p.dirty else ''} — {APP_NAME}" if p else APP_NAME)

    def _update_key_lbl(self):
        p = self.state.project
        if p:
            self.key_lbl.setText(f"{analysis.key_name(p.key_root, p.key_scale)}  ·  {p.bpm:.1f} BPM  ·  A{p.a4:.1f}")

    def _maybe_save(self):
        p = self.state.project
        if not p or not p.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, f"Save changes to '{p.name}'?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save()
        return True

    def new_project(self):
        if not self._maybe_save():
            return
        self.stop()
        d = NewProjectDialog(default_projects_dir(), self.default_sr, self)
        if not d.exec():
            return
        name = d.name.text().strip() or "New Song"
        folder = os.path.join(d.dir.text(), name)
        if os.path.exists(os.path.join(folder, name + PROJECT_EXT)):
            QMessageBox.warning(self, APP_NAME, "A project with that name already exists there.")
            return
        p = Project(name, d.sr.currentData(), folder=folder)
        p.add_track("Beat", "beat")
        roles = ["Lead", "Double", "Ad-lib", "Hook", "Double", "Ad-lib"]
        for i in range(d.tracks.value()):
            r = roles[i % len(roles)]
            t = p.add_track(f"{r} {i + 1}" if i else "Lead Vocal", "vocal", r)
            if r == "Double" and i >= 4:
                t.pan = -t.pan
        p.save(os.path.join(folder, name + PROJECT_EXT))
        self.state.set_project(p)
        self._add_recent(p.saved_path)

    def open_project(self, path=None):
        if not self._maybe_save():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open project", default_projects_dir(),
                                                  f"SIB project (*{PROJECT_EXT})")
        if not path:
            return
        self.stop()
        try:
            p = Project.open(path)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Could not open project:\n{e}")
            return
        self.state.set_project(p)
        self._add_recent(path)

    def save(self):
        p = self.state.project
        if not p.saved_path:
            return self.save_as()
        try:
            p.save()
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Save failed:\n{e}")
            return False
        self.state.status.emit(f"Saved {p.saved_path}")
        self._update_title()
        self._add_recent(p.saved_path)
        return True

    def save_as(self):
        p = self.state.project
        base = os.path.join(default_projects_dir(), p.name if p.name != "Untitled" else "My Song")
        path, _ = QFileDialog.getSaveFileName(self, "Save project as", os.path.join(base, os.path.basename(base) + PROJECT_EXT),
                                              f"SIB project (*{PROJECT_EXT})")
        if not path:
            return False
        if not path.endswith(PROJECT_EXT):
            path += PROJECT_EXT
        os.makedirs(os.path.dirname(path), exist_ok=True)
        p.saved_path = None
        try:
            p.save(path)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Save failed:\n{e}")
            return False
        self._update_title()
        self._add_recent(path)
        self.state.status.emit(f"Saved {path}")
        return True

    def _do_autosave(self):
        p = self.state.project
        if p and p.dirty and p.saved_path and not self.engine.recording:
            try:
                p.save()
                self.state.status.emit("Auto-saved")
                self._update_title()
            except Exception:
                pass

    def _fill_recent(self):
        self.recent_menu.clear()
        files = settings().value("recent", []) or []
        if isinstance(files, str):
            files = [files]
        for f in files[:10]:
            self.recent_menu.addAction(f, lambda _=False, f=f: self.open_project(f))
        if not files:
            a = self.recent_menu.addAction("(none)")
            a.setEnabled(False)

    def _add_recent(self, path):
        s = settings()
        files = s.value("recent", []) or []
        if isinstance(files, str):
            files = [files]
        files = [path] + [f for f in files if f != path]
        s.setValue("recent", files[:10])
        self._fill_recent()

    def closeEvent(self, e):
        if not self._maybe_save():
            e.ignore()
            return
        self.stop()
        settings().setValue("ui/geometry", self.saveGeometry())
        e.accept()

    # ================================================================== import
    def import_beat(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import beat", "", AUDIO_FILTER)
        if path:
            self._import_beat_file(path)

    def _import_beat_file(self, path):
        p = self.state.project
        self.state.push_undo("Import beat")
        bt = next((t for t in p.beat_tracks() if not t.clips), None) or (p.beat_tracks()[0] if p.beat_tracks() else None)
        if bt is None:
            bt = p.add_track("Beat", "beat", index=0)
        if bt.clips:
            bt = p.add_track("Beat 2", "beat", index=p.tracks.index(bt) + 1)
        self.state.status.emit("Loading beat…")

        def job(task):
            return p.pool.import_file(path)

        def done(fid):
            from ..core.project import Clip
            data = p.pool.get(fid)
            bt.clips.append(Clip(fid, 0, len(data), 0, name=os.path.splitext(os.path.basename(path))[0]))
            if bt.name in ("Beat", "Beat 2"):
                bt.name = os.path.splitext(os.path.basename(path))[0][:40]
            self.state.tracks_changed.emit()
            self.state.modified([bt.id])
            self.timeline.zoom_fit()
            self.state.status.emit("Beat loaded - listening for key and tempo…")
            self._show_tab(self.auto)
            self.auto.analyze()

        run_task(job, done, self._error)

    def import_vocal(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import vocal takes", "", AUDIO_FILTER)
        if paths:
            self._import_vocals(paths, None, 0)

    def _import_vocals(self, paths, track, start):
        p = self.state.project
        self.state.push_undo("Import vocal")
        targets = []
        for i, path in enumerate(paths):
            t = track if (track is not None and track.kind == "vocal" and i == 0) else None
            if t is None:
                t = next((x for x in p.vocal_tracks() if not x.clips and x not in targets), None)
            if t is None:
                t = p.add_track(os.path.splitext(os.path.basename(path))[0][:30], "vocal", "Lead")
            targets.append(t)
        self.state.status.emit("Importing vocals…")

        def job(task):
            return [p.pool.import_file(path) for path in paths]

        def done(fids):
            from ..core.project import Clip
            for fid, t, path in zip(fids, targets, paths):
                data = p.pool.get(fid)
                t.clips.append(Clip(fid, int(start), len(data), 0, name=os.path.splitext(os.path.basename(path))[0]))
            self.state.tracks_changed.emit()
            self.state.modified([t.id for t in targets])
            self.state.set_current_track(targets[0].id)
            self.state.status.emit(f"Imported {len(fids)} vocal file(s). Press Ctrl+M to Auto-Match them to the beat.")

        run_task(job, done, self._error)

    def _dropped(self, files, track, pos):
        files = [f for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS + (PROJECT_EXT, ".sesx")]
        if not files:
            return
        if files[0].lower().endswith(".sesx"):
            self.import_audition(files[0])
            return
        if files[0].endswith(PROJECT_EXT):
            self.open_project(files[0])
            return
        if track is not None and track.kind == "beat":
            self._import_beat_file(files[0])
            return
        if pos < self.state.project.sr:
            pos = 0
        elif self.state.snap:
            pos = self.state.project.snap(pos, 1)
        self._import_vocals(files, track, pos)

    # ================================================================== transport
    def _typing(self, text=None):
        w = QApplication.focusWidget()
        if isinstance(w, (QLineEdit, QAbstractSpinBox, QPlainTextEdit, QTextEdit)):
            if text and isinstance(w, QLineEdit):
                w.insert(text)
            return True
        return False

    def _seek(self, pos):
        if self.engine.playing and not self.engine.recording:
            self.engine.pos = int(pos)
        self.state.set_playhead(pos)
        self.timeline.follow(pos)

    def _playhead_moved(self, pos):
        if not self.engine.playing:
            self.play_start = pos

    def toggle_play(self):
        if self._typing(" "):
            return
        if self.engine.playing:
            self.stop()
        else:
            self._start(False)

    def toggle_record(self):
        if self._typing("r"):
            return
        if self.engine.recording:
            self.stop()
        else:
            if self.engine.playing:
                self.stop()
            self._start(True)

    def _start(self, record):
        p = self.state.project
        if not self.engine.available:
            QMessageBox.warning(self, APP_NAME, "Audio playback needs the 'sounddevice' package:\n\npip install sounddevice")
            return
        if self._busy:
            return
        if record:
            t = next((t for t in p.vocal_tracks() if t.armed), None)
            if t is None:
                cur = p.track(self.state.current_track) if self.state.current_track else None
                t = cur if cur is not None and cur.kind == "vocal" else None
                if t is None:
                    t = next((v for v in p.vocal_tracks()), None) or p.add_track("Lead Vocal", "vocal", "Lead")
                t.armed = True
                self.state.tracks_changed.emit()
            self.rec_track = t
        self._prepare(lambda mix=None: self._go(record, mix))

    def _prepare(self, then):
        p = self.state.project
        want_master = self.a_master_prev.isChecked() and not self.ab_dry
        stale, stale_sends = self._stale_work()
        if not stale and not stale_sends and not want_master:
            self._load_engine()
            then()
            return
        self._busy = True
        self.state.status.emit("Rendering effects…")

        def job(task):
            for i, t in enumerate(stale):
                task.report(f"Rendering {t.name} ({i + 1}/{len(stale)})…", i / max(len(stale), 1))
                p.render_track(t, lambda n, f, s: task.report(f"Rendering {n}: {s}", f))
            for n in stale_sends:
                task.report(f"Rendering {n} bus…", 1.0)
                p.render_send(n)
            mix = None
            if want_master:
                task.report("Rendering master…", 1.0)
                mix = p.mixdown(master=True)
            return mix

        def done(mix):
            self._busy = False
            self.state.status.emit("Ready")
            self._load_engine(mix)
            then(mix)

        def err(tb):
            self._busy = False
            self._error(tb)

        run_task(job, done, err, lambda m, f: self.state.status.emit(m))

    def _load_engine(self, master_mix=None):
        p = self.state.project
        self.engine.set_tracks(self._engine_tracks())
        self.engine.master_buffer = master_mix
        self.engine.master_gain = 1.0 if master_mix is not None else float(dsp.db_to_lin(p.master_volume_db))

    def _go(self, record, mix=None):
        p = self.state.project
        e = self.engine
        e.bpm = p.bpm
        e.beat_offset = int(p.beat_offset_s * p.sr)
        e.beats_per_bar = p.time_sig
        e.loop = p.loop
        e.loop_enabled = self.a_loop.isChecked() and p.loop is not None
        e.metronome = self.a_metro.isChecked()
        e.monitor = self.a_monitor.isChecked()
        self.play_start = self.state.playhead
        try:
            punch = self._punch_args() if record else None
            if record and punch:
                a, b, pre = punch
                e.stop_at = b + int(0.5 * p.sr)
                self.play_start = a
                e.record(a, 0, punch_out=b, preroll=pre)
                self.rec_btn.setChecked(True)
                self.state.status.emit(f"Punch-in on {self.rec_track.name}: {fmt_time(a, p.sr)} - {fmt_time(b, p.sr)}")
            elif record:
                e.stop_at = None
                e.rec_end = None
                e.record(self.state.playhead, self.countin.currentIndex())
                self.rec_btn.setChecked(True)
                self.state.status.emit(f"Recording on {self.rec_track.name}…  (Space or R to stop)")
            else:
                e.stop_at = None if e.loop_enabled else p.length + int(FX_TAIL_S * p.sr)
                e.play(self.state.playhead)
            self.a_play.setIconText("■")
            self._was_playing = True
        except Exception as ex:
            self.rec_track = None
            self.rec_btn.setChecked(False)
            QMessageBox.critical(self, APP_NAME, f"Could not start audio:\n{ex}\n\nCheck Tools > Audio settings.")

    def stop(self):
        e = self.engine
        if not e.playing and not e.recording and not e._streams:
            return
        takes = e.stop()
        self.a_play.setIconText("▶")
        self.rec_btn.setChecked(False)
        self._was_playing = False
        p = self.state.project
        if self.rec_track is not None and takes:
            t = self.rec_track
            self.state.push_undo("Record")
            existing = max((c.take for c in t.clips), default=0)
            n_added = 0
            for i, (start, data) in enumerate(takes):
                if len(data) < p.sr * 0.2:
                    continue
                n = existing + i + 1
                p.add_clip_from_array(t, data[:, None] if data.ndim == 1 else data, start, f"{t.name} take {n}", n)
                n_added += 1
            self.state.modified([t.id])
            self.timeline.rebuild()
            peak = max((float(np.max(np.abs(d))) for _, d in takes if len(d)), default=0)
            warn = "  ⚠ Clipping! Turn the mic gain down." if peak >= 0.99 else (
                "  (quiet - turn the mic gain up a little)" if peak < 0.1 else "")
            self.state.status.emit(f"Recorded {n_added} take(s) on {t.name}.{warn}")
        self.rec_track = None
        self.state.record_preview = None
        self.state.set_playhead(self.play_start)
        QTimer.singleShot(200, self._update_input_meter)
        self.timeline.lanes.invalidate()

    def _tick(self):
        e = self.engine
        if e.playing:
            pos = e.pos
            self.state.playhead = max(0, pos)
            self.state.playhead_moved.emit(self.state.playhead)
            self.timeline.follow(max(0, pos))
            if e.recording and self.rec_track is not None:
                self.state.record_preview = (self.rec_track.id, e.rec_start, max(0, pos - e.rec_start))
            self.out_meter.set_levels(e.peak_out)
            self.mixer.set_master_level(e.peak_out)
        elif self._was_playing:
            # engine stopped by itself (end of song)
            self.stop()
        else:
            self.out_meter.set_levels([0, 0])
        self.in_meter.set_levels([e.peak_in])
        p = self.state.project
        self._feature_tick()
        if p:
            self.time_lbl.setText(fmt_time(self.state.playhead, p.sr))
            bar, beat = p.bar_beat(self.state.playhead)
            self.bar_lbl.setText(f"bar {bar}.{int(beat)}")

    def _loop_toggled(self, on):
        p = self.state.project
        if on and p and not p.loop:
            if self.state.has_range:
                p.loop = self.state.range()
            else:
                bar = int(p.samples_per_beat() * p.time_sig)
                s = p.snap(self.state.playhead, 1)
                p.loop = (s, s + 4 * bar)
        self.engine.loop = p.loop if p else None
        self.engine.loop_enabled = on and p is not None and p.loop is not None
        self.timeline.ruler.update()
        self.timeline.lanes.invalidate()

    def _monitor_toggled(self, on):
        if self.engine.playing:
            self.state.status.emit("Monitoring change applies next time you press play")
        self.engine.monitor = on

    def _ab_toggled(self, on):
        self.ab_dry = on
        self.state.status.emit("A/B: hearing DRY vocals" if on else "A/B: hearing PROCESSED vocals")
        if self.engine.playing:
            if on:
                self.engine.master_buffer = None
                self.engine.set_tracks(self._engine_tracks())
            else:
                self._rerender_live()

    def _master_prev_toggled(self, on):
        if self.engine.playing:
            pos = self.engine.pos
            self.stop()
            self.state.set_playhead(pos)
            self._start(False)

    def _snap_toggled(self, on):
        self.state.snap = on

    def _update_input_meter(self):
        p = self.state.project
        if self.engine.playing or not self.engine.available or not p:
            return
        if any(t.armed for t in p.vocal_tracks()):
            if not self.engine._streams:
                self.engine.start_input_meter()
        else:
            self.engine.stop_input_meter()

    def _on_params(self, tid):
        p = self.state.project
        if not p:
            return
        self._update_input_meter()
        self.timeline.sync_headers()
        self.mixer.sync()
        if self.engine.playing:
            self._send_params_to_engine()
            if any(p.send_contributors(n) and not p.is_send_fresh(n) for n in SEND_NAMES):
                self._rerender_timer.start()
            if self.engine.master_buffer is None:
                self.engine.master_gain = float(dsp.db_to_lin(p.master_volume_db))
        p.dirty = True
        self._update_title()

    def _on_modified(self, tid):
        self._update_title()
        self._update_key_lbl()
        if self.engine.playing and not self.ab_dry:
            self._rerender_timer.start()

    def _rerender_live(self):
        p = self.state.project
        if not self.engine.playing or self.ab_dry:
            return
        if self.engine.master_buffer is not None:
            self.state.status.emit("Master preview is on - stop and play again to hear changes")
            return
        stale, stale_sends = self._stale_work()
        if not stale and not stale_sends:
            self.engine.set_tracks(self._engine_tracks())
            return
        if self._busy:
            self._rerender_timer.start()
            return
        self._busy = True

        def job(task):
            for t in stale:
                task.report(f"Updating {t.name}…", 0)
                p.render_track(t)
            for n in stale_sends:
                task.report(f"Updating {n} bus…", 0)
                p.render_send(n)
            return True

        def done(_):
            self._busy = False
            if self.engine.playing:
                self.engine.set_tracks(self._engine_tracks())
            self.state.status.emit("Updated")

        def err(tb):
            self._busy = False
            self._error(tb)

        run_task(job, done, err, lambda m, f: self.state.status.emit(m))

    # ================================================================== editing
    def _update_undo(self):
        u = self.state.undo
        self.a_undo.setEnabled(bool(u and u.can_undo()))
        self.a_redo.setEnabled(bool(u and u.can_redo()))
        self.a_undo.setText(f"Undo {u.undo[-1][0]}" if u and u.undo else "Undo")
        self.a_redo.setText(f"Redo {u.redo[-1][0]}" if u and u.redo else "Redo")
        self._update_title()

    def _after_undo(self, label):
        if label:
            self.state.status.emit(f"{label} undone/redone")
        self.timeline.rebuild()
        self.fx.refresh()
        self.breaths.refresh()
        self.auto.sync()
        self.mixer.rebuild()
        self.state.modified()
        self._update_undo()

    def undo(self):
        if self._typing():
            w = QApplication.focusWidget()
            if hasattr(w, "undo"):
                w.undo()
            return
        self._after_undo(self.state.undo.do_undo())

    def redo(self):
        self._after_undo(self.state.undo.do_redo())

    def _sel_clips(self):
        p = self.state.project
        out = []
        for t in p.tracks:
            for c in t.clips:
                if c.id in self.state.selected_clips:
                    out.append((t, c))
        return out

    def copy(self):
        if self._typing():
            w = QApplication.focusWidget()
            if hasattr(w, "copy"):
                w.copy()
            return
        p = self.state.project
        cur = p.track(self.state.current_track) if self.state.current_track else None
        if self.state.has_range and cur:
            a, b = self.state.range()
            self.state.clipboard = copy_range(p, cur, a, b)
        elif self.state.selected_clips:
            cl = self._sel_clips()
            base = min(c.start for _, c in cl)
            clips = []
            for _, c in cl:
                nc = copy.deepcopy(c)
                nc.start = c.start - base
                clips.append(nc)
            self.state.clipboard = clips
        self.state.status.emit(f"Copied {len(self.state.clipboard)} clip piece(s)")

    def cut(self):
        if self._typing():
            w = QApplication.focusWidget()
            if hasattr(w, "cut"):
                w.cut()
            return
        self.copy()
        self.delete()

    def paste(self):
        if self._typing():
            w = QApplication.focusWidget()
            if hasattr(w, "paste"):
                w.paste()
            return
        p = self.state.project
        cur = p.track(self.state.current_track) if self.state.current_track else None
        if not cur or not self.state.clipboard:
            return
        self.state.push_undo("Paste")
        paste(p, cur, self.state.clipboard, self.state.playhead)
        self.state.modified([cur.id])

    def delete(self, ripple=False):
        if self._typing():
            w = QApplication.focusWidget()
            if isinstance(w, QLineEdit):
                w.del_()
            return
        p = self.state.project
        if self.state.has_range:
            tracks = self.state.target_tracks()
            a, b = self.state.range()
            self.state.push_undo("Ripple delete" if ripple else "Delete")
            delete_range(p, tracks, a, b, ripple)
            self.state.sel_end = self.state.sel_start
            self.state.modified([t.id for t in tracks])
        elif self.state.selected_clips:
            self.state.push_undo("Delete clip")
            ids = set()
            for t, c in self._sel_clips():
                t.clips.remove(c)
                ids.add(t.id)
            self.state.selected_clips.clear()
            self.state.modified(list(ids))
        self.state.selection_changed.emit()

    def split(self):
        if self._typing("s"):
            return
        p = self.state.project
        pos = self.state.playhead
        self.state.push_undo("Split")
        if self.state.selected_clips:
            ids = set()
            for t, c in self._sel_clips():
                if split_clip(p, t, c, pos):
                    ids.add(t.id)
            self.state.modified(list(ids))
        else:
            tracks = self.state.target_tracks() or p.tracks
            split_at(p, tracks, pos)
            self.state.modified([t.id for t in tracks])

    def trim(self):
        if not self.state.has_range:
            return
        p = self.state.project
        tracks = self.state.target_tracks()
        self.state.push_undo("Trim")
        a, b = self.state.range()
        trim_to_range(p, tracks, a, b)
        self.state.modified([t.id for t in tracks])

    def duplicate_clip(self):
        cl = self._sel_clips()
        if not cl:
            return
        self.state.push_undo("Duplicate clip")
        new_ids = set()
        for t, c in cl:
            nc = copy.deepcopy(c)
            nc.id = uid()
            nc.start = c.end
            t.clips.append(nc)
            new_ids.add(nc.id)
        self.state.selected_clips = new_ids
        self.state.modified(list({t.id for t, _ in cl}))

    def nudge(self, ms):
        cl = self._sel_clips()
        if not cl:
            return
        p = self.state.project
        self.state.push_undo("Nudge")
        for t, c in cl:
            c.start = max(0, c.start + int(ms / 1000 * p.sr))
        self.state.modified(list({t.id for t, _ in cl}))

    def select_all(self):
        if self._typing():
            w = QApplication.focusWidget()
            if hasattr(w, "selectAll"):
                w.selectAll()
            return
        p = self.state.project
        self.state.sel_start, self.state.sel_end = 0, p.length
        self.state.sel_tracks = set()
        self.state.selection_changed.emit()

    def deselect(self):
        self.state.sel_end = self.state.sel_start
        self.state.selected_clips.clear()
        self.state.sel_tracks.clear()
        self.state.selection_changed.emit()

    def _update_sel(self):
        p = self.state.project
        if p and self.state.has_range:
            a, b = self.state.range()
            self.sel_lbl.setText(f"Selection {fmt_time(a, p.sr)} – {fmt_time(b, p.sr)}  ({(b - a) / p.sr:.2f}s)")
        elif self.state.selected_clips:
            self.sel_lbl.setText(f"{len(self.state.selected_clips)} clip(s) selected")
        else:
            self.sel_lbl.setText("")
        self.timeline.lanes.invalidate()

    # ================================================================== process (destructive)
    def _targets(self):
        """-> list of (track, start, end) to process."""
        p = self.state.project
        if self.state.has_range:
            a, b = self.state.range()
            return [(t, a, b) for t in self.state.target_tracks()]
        if self.state.selected_clips:
            return [(t, c.start, c.end) for t, c in self._sel_clips()]
        return []

    def _process(self, label, fn, xf=5.0):
        tg = self._targets()
        if not tg:
            self.state.status.emit("Select a range (drag on a track) or a clip first")
            return
        p = self.state.project
        self.state.push_undo(label)
        for t, a, b in tg:
            process_range(p, t, a, b, fn, label.replace(" ", "_").lower(), xf)
        self.state.modified(list({t.id for t, _, _ in tg}))
        self.state.status.emit(f"{label} applied")

    def _process_bg(self, label, fn2):
        """fn2(audio, sr) - heavier processing run in a background thread."""
        tg = self._targets()
        if not tg:
            self.state.status.emit("Select a range (drag on a track) or a clip first")
            return
        p = self.state.project
        self.state.push_undo(label)
        self.state.status.emit(f"{label}…")

        def job(task):
            for t, a, b in tg:
                process_range(p, t, a, b, lambda seg: fn2(seg, p.sr), label.replace(" ", "_").lower())
            return True

        run_task(job, lambda _: (self.state.modified(list({t.id for t, _, _ in tg})),
                                 self.state.status.emit(f"{label} applied")), self._error)

    def _apply_effect_destructive(self, inst):
        from ..core.effects import REGISTRY
        p = self.state.project
        name = REGISTRY[inst["id"]].name
        ctx = p.ctx()
        self._process_bg(name, lambda seg, sr: process_chain(seg, sr, [inst], ctx))

    def p_amplify(self):
        db, ok = QInputDialog.getDouble(self, "Amplify", "Gain (dB):", 3.0, -48, 24, 1)
        if ok:
            g = float(dsp.db_to_lin(db))
            self._process(f"Amplify {db:+.1f} dB", lambda a: a * g)

    def p_lufs(self):
        v, ok = QInputDialog.getDouble(self, "Normalize to loudness", "Target (LUFS):", -16.0, -36, -5, 1)
        if ok:
            self._process_bg("Match loudness", lambda a, sr: a * dsp.db_to_lin(v - analysis.integrated_lufs(a, sr))
                             if analysis.integrated_lufs(a, sr) > -69 else a)

    def p_capture_noise(self):
        tg = self._targets()
        if not tg:
            self.state.status.emit("Select a bit of background noise (no voice) first")
            return
        t, a, b = tg[0]
        p = self.state.project
        off, dry = p.render_track_dry(t, a, b)
        self.noise_profile = dsp.capture_noise_profile(dry, p.sr)
        self.state.status.emit("Noise print captured - now select the vocal and run Noise reduction")

    def p_noise(self):
        amt, ok = QInputDialog.getDouble(self, "Noise reduction", "Reduction (dB):", 12.0, 1, 40, 1)
        if not ok:
            return
        prof = self.noise_profile
        self._process_bg("Noise reduction", lambda a, sr: dsp.noise_reduction(a, sr, amt, 1.5, prof))

    def p_pitch(self):
        st, ok = QInputDialog.getDouble(self, "Pitch shift", "Semitones:", 0.0, -12, 12, 2)
        if ok and st:
            self._process_bg(f"Pitch {st:+.2f} st", lambda a, sr: pitch_mod.pitch_shift_vocal(a, sr, st))

    def p_stretch(self):
        cl = self._sel_clips()
        if not cl:
            self.state.status.emit("Select a clip to time-stretch")
            return
        pct, ok = QInputDialog.getDouble(self, "Time stretch", "New length (% of original):", 100.0, 25, 400, 1)
        if not ok or pct == 100:
            return
        p = self.state.project
        self.state.push_undo("Time stretch")

        def job(task):
            out = []
            for t, c in cl:
                seg = p.pool.get(c.file_id)[c.src_in: c.src_in + c.length]
                y = dsp.time_stretch(seg, p.sr, 100.0 / pct)
                out.append((t, c, y))
            return out

        def done(res):
            for t, c, y in res:
                c.file_id = p.pool.add_array(y, f"{c.name}_stretch")
                c.src_in, c.length = 0, len(y)
                c.fade_in = min(c.fade_in, c.length // 2)
                c.fade_out = min(c.fade_out, c.length // 2)
            self.state.modified(list({t.id for t, _, _ in res}))

        run_task(job, done, self._error)

    # ================================================================== tracks
    def add_track(self, kind, role="Lead"):
        p = self.state.project
        self.state.push_undo("Add track")
        n = sum(1 for t in p.tracks if t.kind == kind) + 1
        name = "Beat" if kind == "beat" else (f"{role} {n}" if role != "Lead" else f"Lead Vocal {n}")
        t = p.add_track(name, kind, role)
        self.state.tracks_changed.emit()
        self.state.set_current_track(t.id)

    def _cur(self):
        p = self.state.project
        return p.track(self.state.current_track) if p and self.state.current_track else None

    def delete_track(self):
        t = self._cur()
        if not t:
            return
        if t.clips and QMessageBox.question(self, APP_NAME, f"Delete track '{t.name}'?") != QMessageBox.Yes:
            return
        self.state.push_undo("Delete track")
        p = self.state.project
        i = p.tracks.index(t)
        p.tracks.remove(t)
        self.state.current_track = p.tracks[min(i, len(p.tracks) - 1)].id if p.tracks else None
        self.state.tracks_changed.emit()
        self.state.modified()

    def duplicate_track(self, role=None):
        t = self._cur()
        if not t:
            return None
        p = self.state.project
        self.state.push_undo("Duplicate track")
        from ..core.project import Track
        nt = Track.from_dict(copy.deepcopy(t.to_dict()))
        nt.id = uid()
        for c in nt.clips:
            c.id = uid()
        for e in nt.fx:
            e["uid"] = uid()[:10]
        nt.name = f"{t.name} copy"
        nt.armed = False
        p.tracks.insert(p.tracks.index(t) + 1, nt)
        self.state.tracks_changed.emit()
        self.state.set_current_track(nt.id)
        self.state.modified([nt.id])
        return nt

    def instant_double(self):
        t = self._cur()
        if not t or t.kind != "vocal" or not t.clips:
            self.state.status.emit("Select a vocal track with a take first")
            return
        nt = self.duplicate_track()
        if nt is None:
            return
        nt.name = f"{t.name} (double)"
        nt.role = "Double"
        nt.volume_db = t.volume_db - 8.0
        nt.pan = -0.3 if t.pan >= 0 else 0.3
        nt.fx = presets.style_chain("Doubles / Stacks")
        nt.fx.insert(len(nt.fx) - 1, new_instance("pitch_shift", cents=-7.0, semitones=0.0))
        sr = self.state.project.sr
        for c in nt.clips:
            c.start += int(0.012 * sr)
        self.state.tracks_changed.emit()
        self.state.modified([nt.id])
        self.state.status.emit("Instant double made. A real second take always sounds better if you have time!")

    def bounce(self):
        t = self._cur()
        if not t:
            return
        p = self.state.project
        self.state.push_undo("Bounce")
        self.state.status.emit(f"Bouncing {t.name}…")

        def job(task):
            return p.render_track(t)

        def done(_):
            nt = bounce_track(p, t)
            t.mute = True
            self.state.tracks_changed.emit()
            self.state.modified([nt.id, t.id])
            self.state.status.emit(f"Bounced to '{nt.name}' (original muted)")

        run_task(job, done, self._error)

    def move_track(self, d):
        t = self._cur()
        p = self.state.project
        if not t:
            return
        i = p.tracks.index(t)
        j = i + d
        if 0 <= j < len(p.tracks):
            self.state.push_undo("Move track")
            p.tracks[i], p.tracks[j] = p.tracks[j], p.tracks[i]
            self.state.tracks_changed.emit()

    # ================================================================== clip context menu
    def _clip_menu(self, gpos, track, clip, s):
        m = QMenu(self)
        p = self.state.project
        if clip is not None:
            m.addAction("Split here", lambda: (self.state.push_undo("Split"), split_clip(p, track, clip, s),
                                               self.state.modified([track.id])))
            m.addAction("Use this take (bring to front)", lambda: self._z(track, clip, True))
            m.addAction("Send to back", lambda: self._z(track, clip, False))
            m.addAction("Unmute clip" if clip.muted else "Mute clip", lambda: self._clip_set(track, clip, muted=not clip.muted))
            m.addAction("Clip gain…", lambda: self._clip_gain(track, clip))
            m.addAction("Rename…", lambda: self._clip_rename(track, clip))
            m.addAction("Duplicate", lambda: (self.state.selected_clips.clear(), self.state.selected_clips.add(clip.id),
                                              self.duplicate_clip()))
            m.addAction("Quick fades (10 ms)", lambda: self._clip_set(track, clip, fade_in=int(0.01 * p.sr),
                                                                       fade_out=int(0.01 * p.sr)))
            m.addSeparator()
            m.addAction("Find breaths in this track", lambda: (self._show_tab(self.breaths), self.breaths.detect()))
            m.addAction("Analyze pitch / key of this clip", lambda: (self.state.selected_clips.clear(),
                                                                     self.state.selected_clips.add(clip.id),
                                                                     self._show_tab(self.pitch), self.pitch.analyze()))
            m.addSeparator()
            m.addAction("Delete clip", lambda: (self.state.push_undo("Delete clip"), track.clips.remove(clip),
                                                self.state.modified([track.id])))
        else:
            m.addAction("Paste here", lambda: (self.state.set_playhead(s), self.paste()))
            m.addAction("Import audio here…", lambda: self._import_here(track, s))
            m.addAction("Add marker here", lambda: (self.state.set_playhead(s), self.markers.add()))
        if track is not None:
            m.addSeparator()
            m.addAction("Edit effects (FX Rack)", lambda: self._show_tab(self.fx))
            m.addAction("Bounce track (print FX)", self.bounce)
            if track.kind == "vocal":
                m.addAction("Make instant double", self.instant_double)
        m.exec(gpos)

    def _import_here(self, track, s):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import audio", "", AUDIO_FILTER)
        if paths:
            if track is not None and track.kind == "beat":
                self._import_beat_file(paths[0])
            else:
                self._import_vocals(paths, track, s)

    def _z(self, track, clip, front):
        self.state.push_undo("Change take")
        track.clips.remove(clip)
        if front:
            track.clips.append(clip)
        else:
            track.clips.insert(0, clip)
        self.state.modified([track.id])

    def _clip_set(self, track, clip, **kw):
        self.state.push_undo("Edit clip")
        for k, v in kw.items():
            setattr(clip, k, v)
        self.state.modified([track.id])

    def _clip_gain(self, track, clip):
        v, ok = QInputDialog.getDouble(self, "Clip gain", "Gain (dB):", clip.gain_db, -48, 24, 1)
        if ok:
            self._clip_set(track, clip, gain_db=v)

    def _clip_rename(self, track, clip):
        v, ok = QInputDialog.getText(self, "Rename clip", "Name:", text=clip.name)
        if ok:
            self._clip_set(track, clip, name=v)

    # ================================================================== export
    def export_mix(self):
        p = self.state.project
        if not p.length:
            QMessageBox.information(self, APP_NAME, "Nothing to export yet.")
            return
        d = ExportDialog(p, self.state.has_range, self)
        if not d.exec():
            return
        path = d.path.text()
        rng = d.range.currentText()
        start, end = 0, p.length + int(FX_TAIL_S * p.sr)
        if rng == "Selection":
            start, end = self.state.range()
        elif rng == "Loop region" and p.loop:
            start, end = p.loop
        master = d.master.isChecked()
        m = next((e for e in p.master_fx if e["id"] == "mastering"), None)
        if m:
            m["params"]["target_lufs"] = d.lufs.value()
        bd = d.bit_depth()
        stems, dry_stems = d.stems.isChecked(), d.dry_stems.isChecked()
        prog = QProgressDialog("Rendering…", "Cancel", 0, 100, self)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setValue(1)

        def job(task):
            cancel = lambda: task.cancelled
            mix = p.mixdown(start, end, master, lambda n, f, s: task.report(f"{n}: {s}", f), cancel)
            if master is False:
                pk = float(np.max(np.abs(mix))) if len(mix) else 0
                if pk > 0.999:
                    mix = dsp.limiter(mix, p.sr, -1.0)
            save_audio(path, mix, p.sr, bd)
            out = [path]
            if stems or dry_stems:
                sd_ = os.path.splitext(path)[0] + "_stems"
                for t in p.tracks:
                    for dry in ([False] if stems else []) + ([True] if dry_stems else []):
                        task.report(f"Stem {t.name}", 0.9)
                        off, a = p.render_track_dry(t) if dry else p.track_audio(t)
                        full = np.zeros((max(0, end - start), 2), np.float32)
                        s0, s1 = max(off, start), min(off + len(a), end)
                        if s1 > s0:
                            gl, gr = dsp.pan_gains(t.pan)
                            g = dsp.db_to_lin(t.volume_db)
                            full[s0 - start:s1 - start] = a[s0 - off:s1 - off] * np.array([gl * g, gr * g], np.float32)
                        sp = os.path.join(sd_, f"{t.name}{' (dry)' if dry else ''}.wav")
                        save_audio(sp, full, p.sr, 24)
                        out.append(sp)
            return out, analysis.integrated_lufs(mix, p.sr), analysis.true_peak_db(mix[: p.sr * 600], p.sr)

        task_ref = {}

        def done(res):
            prog.close()
            files, lufs, tp = res
            QMessageBox.information(self, APP_NAME, f"Exported:\n{files[0]}\n\nLoudness {lufs:.1f} LUFS, true peak {tp:.1f} dBTP"
                                    + (f"\n+ {len(files) - 1} stem file(s)" if len(files) > 1 else ""))
            if hasattr(os, "startfile"):
                try:
                    os.startfile(os.path.dirname(os.path.abspath(path)))  # type: ignore[attr-defined]
                except Exception:
                    pass

        def progress(msg, f):
            prog.setLabelText(msg)
            prog.setValue(int(5 + 90 * f))
            if prog.wasCanceled() and task_ref.get("t"):
                task_ref["t"].cancelled = True

        def err(tb):
            prog.close()
            self._error(tb)

        task_ref["t"] = run_task(job, done, err, progress)

    def export_audition(self):
        p = self.state.project
        if not p.length:
            QMessageBox.information(self, APP_NAME, "Nothing to export yet.")
            return
        d = AuditionDialog(p, self)
        if not d.exec():
            return
        out, mode, br, open_after = d.dir.text(), d.mode(), d.breaths.isChecked(), d.open_after.isChecked()
        self.state.status.emit("Writing Audition session…")

        def job(task):
            return audition.export_session(p, out, mode, br, lambda m: task.report(m))

        def done(path):
            self._last_sesx = path
            self.state.status.emit(f"Audition session written: {path}")
            if open_after and not audition.open_in_audition(path):
                QMessageBox.information(self, APP_NAME, f"Session saved to:\n{path}")

        run_task(job, done, self._error, lambda m, f: self.state.status.emit(m))

    def open_in_audition(self):
        path = getattr(self, "_last_sesx", None)
        if not path:
            self.export_audition()
            return
        audition.open_in_audition(path)

    # ================================================================== misc
    def _automatch(self):
        self._show_tab(self.auto)
        p = self.state.project
        if not any(t.clips for t in p.vocal_tracks()):
            self.state.status.emit("Record or import some vocals first")
            return
        self.auto.match()

    def _show_tab(self, w):
        self.right.show()
        self.tabs.setCurrentWidget(w)

    def audio_settings(self):
        self.stop()
        d = AudioSettingsDialog(self.engine, self)
        if d.exec():
            d.apply()
            self.default_sr = d.sr.currentData()
            self.state.status.emit("Audio settings saved")

    def _html(self, title, html):
        mb = QMessageBox(self)
        mb.setWindowTitle(title)
        mb.setTextFormat(Qt.RichText)
        mb.setText(html)
        mb.exec()

    def _error(self, tb):
        self.state.status.emit("Error - see details")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(APP_NAME)
        last = tb.strip().splitlines()[-1] if tb else "Unknown error"
        box.setText(last)
        box.setDetailedText(tb)
        box.exec()
