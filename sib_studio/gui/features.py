"""v1.1 studio features wired into the main window: lyrics + teleprompter, ad-libs, take coach,
analyzer, spectral repair, punch-in, reference track, match EQ, send buses, vocal editing tools,
creative edits, beat tools and Adobe Audition session import."""
from __future__ import annotations

import os
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QMessageBox, QInputDialog, QFileDialog, QDialog, QVBoxLayout, QPlainTextEdit,
                               QDialogButtonBox, QToolButton, QLineEdit, QApplication)

from ..core import dsp, analysis, lyrics as L, vocal_edit, adlibs, coach
from ..core import audition_import
from ..core.project import process_range, SEND_NAMES, Clip
from ..core.effects import new_instance
from ..core.audio_io import AUDIO_EXTENSIONS, load_audio
from .lyrics_panel import LyricsPanel
from .teleprompter import Teleprompter
from .tool_panels import CoachPanel, AdlibPanel, AnalyzerPanel, SpectralPanel
from .workers import run_task

AUDIO_FILTER = "Audio (" + " ".join(f"*{e}" for e in AUDIO_EXTENSIONS) + ")"


class FeatureMixin:
    # ------------------------------------------------------------------ setup
    def _init_features(self):
        self.ref_mode = False
        self.tele = None
        self.lyrics = LyricsPanel(self.state)
        self.lyrics.open_teleprompter.connect(self.show_teleprompter)
        self.lyrics.lyrics_changed.connect(self._lyrics_changed)
        self.adlib_panel = AdlibPanel(self.state)
        self.adlib_panel.create_from_selection.connect(self.adlib_from_selection)
        self.adlib_panel.insert_requested.connect(self.insert_adlib)
        self.coach = CoachPanel(self.state)
        self.coach.use_take.connect(self._use_take)
        self.analyzer = AnalyzerPanel(self.state, self.engine)
        self.spectral = SpectralPanel(self.state)
        self.spectral.apply_box.connect(self._apply_spectral)
        self.state.loop_bars_requested.connect(self.loop_bars)
        self.state.edit_send_requested.connect(self._edit_send)

    def _add_feature_tabs(self):
        self.tabs.insertTab(2, self.lyrics, "Lyrics")
        self.tabs.insertTab(3, self.adlib_panel, "Ad-libs")
        self.tabs.addTab(self.coach, "Take Coach")
        self.tabs.addTab(self.spectral, "Spectral")
        self.tabs.addTab(self.analyzer, "Analyzer")
        self.tabs.setUsesScrollButtons(True)

    def _feature_actions(self):
        A = self._act
        self.a_tele = A("Teleprompter", self.show_teleprompter, "Ctrl+L", tip="Big scrolling lyrics for the booth")
        self.a_stamp = A("Tap-sync lyric line", self.stamp_lyric, "T")
        self.a_punch = A("Punch in/out on selection", lambda v: None, None, True,
                         "With a selection, recording only replaces that range (1 bar pre-roll)")
        self.a_ref = A("Hear reference track (A/B)", self._ref_toggled, None, True,
                       "Switch to the reference song, loudness-matched to your mix")
        self.a_imp_ref = A("Import reference track…", self.import_reference)
        self.a_imp_ses = A("Import Adobe Audition session (.sesx)…", self.import_audition, "Ctrl+Shift+I")

    def _feature_menus(self, mb):
        # File / Process additions go into the existing menus
        for m in mb.actions():
            if m.text() == "&File":
                fm = m.menu()
                acts = fm.actions()
                anchor = next((a for a in acts if a.text().startswith("Export &Mixdown")), None)
                if anchor is not None:
                    fm.insertAction(anchor, self.a_imp_ses)
                    fm.insertAction(anchor, self.a_imp_ref)
                    fm.insertSeparator(anchor)
                else:
                    fm.addAction(self.a_imp_ses)
                    fm.addAction(self.a_imp_ref)
            if m.text() == "&Process":
                pm = m.menu()
                pm.addSeparator()
                pm.addAction(self._act("Stutter edit (1/16)", lambda: self._creative("stutter16")))
                pm.addAction(self._act("Stutter edit (1/8)", lambda: self._creative("stutter8")))
                pm.addAction(self._act("Tape stop", lambda: self._creative("tapestop")))
                pm.addAction(self._act("Tape start", lambda: self._creative("tapestart")))
                pm.addAction(self._act("Filter sweep down (muffle in)", lambda: self._creative("sweepdown")))
                pm.addAction(self._act("Filter sweep up (open up)", lambda: self._creative("sweepup")))
                pm.addAction(self._act("Spectral repair… (Spectral tab)", lambda: self._show_tab(self.spectral)))
        # Lyrics menu
        lm = mb.addMenu("&Lyrics")
        lm.addAction(self._act("Open lyrics pad", lambda: self._show_tab(self.lyrics)))
        lm.addAction(self.a_tele)
        lm.addAction(self.a_stamp)
        lm.addAction(self._act("Start lyrics at playhead bar", self.lyrics._start_here))
        lm.addSeparator()
        for n in (4, 8, 16):
            lm.addAction(self._act(f"Loop {n} bars for writing", lambda _=False, n=n: self.loop_bars(n)))
        # Vocal menu
        vm = mb.addMenu("&Vocal")
        vm.addAction(self._act("Strip silence (split into phrases)", self.strip_silence))
        vm.addAction(self._act("Align to lead (tighten doubles / stacks)", self.align_to_lead))
        vm.addAction(self._act("Quantize phrases to the beat…", self.quantize))
        vm.addSeparator()
        vm.addAction(self._act("Rate takes (Take Coach)", lambda: (self._show_tab(self.coach), self.coach.run())))
        vm.addSeparator()
        vm.addAction(self._act("Delay throw on selection", lambda: self.add_throw("delay")))
        vm.addAction(self._act("Reverb throw on selection", lambda: self.add_throw("reverb")))
        vm.addAction(self._act("Reverse-reverb swell into selection", lambda: self.add_throw("reverse")))
        vm.addAction(self._act("Remove throws in selection", self.clear_throws))
        vm.addSeparator()
        vm.addAction(self._act("Save selection as ad-lib…", self.adlib_from_selection))
        vm.addAction(self._act("Ad-lib library", lambda: self._show_tab(self.adlib_panel)))
        # Mix menu
        xm = mb.addMenu("&Mix")
        xm.addAction(self._act("Mixer", lambda: self.bottom.show()))
        xm.addAction(self._act("Edit Reverb bus effect", lambda: self._edit_send("reverb")))
        xm.addAction(self._act("Edit Delay bus effect", lambda: self._edit_send("delay")))
        xm.addAction(self._act("Send all vocals to Reverb bus (-12 dB)", lambda: self._quick_sends("reverb", -12.0)))
        xm.addAction(self._act("Send ad-libs to Delay bus (-8 dB)", lambda: self._quick_sends("delay", -8.0, "Ad-lib")))
        xm.addSeparator()
        xm.addAction(self.a_imp_ref)
        xm.addAction(self.a_ref)
        xm.addAction(self._act("Match selected track's tone to reference…", lambda: self.match_eq(False)))
        xm.addAction(self._act("Match master to reference track", lambda: self.match_eq(True)))
        xm.addSeparator()
        xm.addAction(self._act("Transpose beat…", self.transpose_beat))
        xm.addAction(self._act("Change beat tempo…", self.change_tempo))
        xm.addAction(self._act("Beat drop on selection (cut the beat)", self.beat_drop))
        xm.addAction(self._act("Remove vocals from beat (center cancel)", self.add_vocal_remover))
        xm.addSeparator()
        xm.addAction(self._act("Analyzer (spectrum, loudness, masking)", lambda: self._show_tab(self.analyzer)))

    def _feature_transport(self, tb, btn):
        btn("Punch", self.a_punch, "Punch in/out: record only over the selected range (1 bar pre-roll)")
        btn("REF", self.a_ref, "A/B with your reference song (loudness-matched)")
        btn("🎤 Prompter", self.a_tele, "Teleprompter (Ctrl+L)")

    # ------------------------------------------------------------------ lyrics / teleprompter
    def show_teleprompter(self):
        if self.tele is None:
            self.tele = Teleprompter(self.state, self)
            self.tele.key_handler = self._tele_key
        self._lyrics_changed()
        self.tele.show()
        self.tele.raise_()
        self.tele.activateWindow()

    def _tele_key(self, e):
        k = e.key()
        if k == Qt.Key_Space:
            self.toggle_play()
            return True
        if k == Qt.Key_R:
            self.toggle_record()
            return True
        if k == Qt.Key_T:
            self.stamp_lyric()
            return True
        return False

    def _lyric_times(self):
        p = self.state.project
        if not p:
            return []
        sh = self.lyrics.sheet()
        key = (id(sh), p.bpm, p.beat_offset_s, p.sr, p.time_sig)
        if getattr(self, "_lt_key", None) != key:
            self._lt_key = key
            self._lt = L.line_times(sh, p)
        return self._lt

    def _lyrics_changed(self):
        if self.tele is not None:
            self.tele.set_lyrics(self._lyric_times(), self.state.project.lyrics.get("font_size"))

    def stamp_lyric(self):
        w = QApplication.focusWidget()
        if isinstance(w, (QLineEdit, QPlainTextEdit)) and w is not None:
            if isinstance(w, QLineEdit):
                w.insert("t")
            else:
                w.insertPlainText("t")
            return
        if not self.lyrics.sync_mode:
            self.lyrics.sync_btn.setChecked(True)
        if self.engine.playing:
            self.lyrics.stamp(max(0, self.engine.pos))
            self._lyrics_changed()
        else:
            self.state.status.emit("Tap-sync: press play, then T at the start of each line")

    def _feature_tick(self):
        e = self.engine
        p = self.state.project
        if not p:
            return
        pos = e.pos if e.playing else self.state.playhead
        if self.tele is not None and self.tele.isVisible():
            cd = None
            if e.playing and e.recording and pos < e.rec_start:
                cd = (e.rec_start - pos) / p.sr
            self.tele.set_position(pos, e.recording, cd)
        if self.lyrics.isVisible():
            idx = -1
            for s, en, ln in self._lyric_times():
                if s <= pos:
                    idx = ln.index
            self.lyrics.set_active_line(idx)

    def loop_bars(self, n):
        p = self.state.project
        bar = p.samples_per_beat() * p.time_sig
        s = p.snap(self.state.playhead, 1)
        s = int(round((s - p.beat_offset_s * p.sr) / bar) * bar + p.beat_offset_s * p.sr)
        p.loop = (max(0, s), max(0, s) + int(n * bar))
        self.a_loop.setChecked(True)
        self._loop_toggled(True)
        self.state.status.emit(f"Looping {n} bars - press Space to play while you write")

    # ------------------------------------------------------------------ punch-in
    def _punch_args(self):
        p = self.state.project
        if self.a_punch.isChecked() and self.state.has_range:
            a, b = self.state.range()
            return a, b, int(p.samples_per_beat() * p.time_sig)
        return None

    # ------------------------------------------------------------------ engine track list (sends + reference)
    def _stale_work(self):
        p = self.state.project
        tracks = [] if self.ab_dry else [t for t in p.tracks if t.clips and not p.is_render_fresh(t)]
        sends = [] if self.ab_dry else [n for n in SEND_NAMES if p.send_contributors(n) and not p.is_send_fresh(n)]
        return tracks, sends

    def _engine_tracks(self):
        p = self.state.project
        audible = p.audible_tracks()
        out = []
        ref_gain = getattr(self, "_ref_gain_db", 0.0)
        for t in p.tracks:
            if self.ab_dry:
                off, data = p.render_track_dry(t)
            else:
                hit = p._render_cache.get(t.id)
                off, data = p.track_audio(t) if hit else p.render_track_dry(t)
            if t.kind == "reference":
                aud, vol = self.ref_mode, ref_gain
            else:
                aud, vol = (t in audible) and not self.ref_mode, t.volume_db
            out.append(dict(id=t.id, off=off, data=data, volume_db=vol, pan=t.pan, audible=aud))
        if not self.ab_dry:
            for st in p.send_strips():
                hit = p._send_cache.get(st["send"])
                data = hit[1] if hit else np.zeros((0, 2), np.float32)
                out.append(dict(id=st["id"], off=0, data=data, volume_db=st["volume_db"], pan=0.0,
                                audible=(not st["mute"]) and not self.ref_mode))
        return out

    def _send_params_to_engine(self):
        p = self.state.project
        aud = p.audible_tracks()
        for t in p.tracks:
            if t.kind == "reference":
                self.engine.update_track_params(t.id, getattr(self, "_ref_gain_db", 0.0), t.pan, self.ref_mode)
            else:
                self.engine.update_track_params(t.id, t.volume_db, t.pan, (t in aud) and not self.ref_mode)
        for st in p.send_strips():
            self.engine.update_track_params(st["id"], st["volume_db"], 0.0, (not st["mute"]) and not self.ref_mode)

    # ------------------------------------------------------------------ reference
    def import_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "Reference song (a finished track you want to sound like)", "",
                                              AUDIO_FILTER)
        if not path:
            return
        p = self.state.project
        self.state.push_undo("Import reference")
        t = p.add_track("Reference: " + os.path.splitext(os.path.basename(path))[0][:30], "reference",
                        style="Raw (no effects)")
        t.fx = []
        t.mute = True

        def job(task):
            return p.import_audio_to_track(path, t)

        def done(_):
            self.state.tracks_changed.emit()
            self.state.modified([t.id])
            self.state.status.emit("Reference loaded. Press REF to A/B it (loudness-matched) against your mix.")

        run_task(job, done, self._error)

    def _ref_toggled(self, on):
        p = self.state.project
        refs = [t for t in p.reference_tracks() if t.clips]
        if on and not refs:
            self.a_ref.setChecked(False)
            self.state.status.emit("Import a reference track first (Mix > Import reference track)")
            return
        if not on:
            self.ref_mode = False
            self._send_params_to_engine()
            self.state.status.emit("Hearing YOUR mix")
            return

        def job(task):
            off, r = p.render_track_dry(refs[0])
            ref_l = analysis.integrated_lufs(r, p.sr)
            mix = p.mixdown(master=True)
            return ref_l, analysis.integrated_lufs(mix, p.sr)

        def done(res):
            ref_l, mix_l = res
            self._ref_gain_db = float(np.clip(mix_l - ref_l, -24, 12)) if ref_l > -69 and mix_l > -69 else 0.0
            self.ref_mode = True
            if self.engine.playing:
                self.engine.set_tracks(self._engine_tracks())
            self._send_params_to_engine()
            self.state.status.emit(f"Hearing REFERENCE (gain {self._ref_gain_db:+.1f} dB to match your mix at "
                                   f"{mix_l:.1f} LUFS; reference is {ref_l:.1f} LUFS)")

        run_task(job, done, self._error, lambda m, f: self.state.status.emit(m))

    def match_eq(self, master):
        p = self.state.project
        refs = [t for t in p.reference_tracks() if t.clips]
        src_track = None if master else self._cur()
        if not master and (src_track is None or not src_track.clips):
            self.state.status.emit("Select the track you want to match first")
            return
        ref_path = None
        if not refs:
            ref_path, _ = QFileDialog.getOpenFileName(self, "Reference audio (a vocal acapella or a finished song)", "",
                                                      AUDIO_FILTER)
            if not ref_path:
                return
        self.state.status.emit("Matching tone…")

        def job(task):
            if refs:
                _, ref = p.render_track_dry(refs[0])
            else:
                ref, _ = load_audio(ref_path, p.sr)
            if master:
                src = p.mixdown(master=False)
            else:
                _, src = p.track_audio(src_track)
            return dsp.match_eq_curve(src, ref, p.sr)

        def done(curve):
            chain = p.master_fx if master else src_track.fx
            self.state.push_undo("Match EQ")
            inst = next((e for e in chain if e["id"] == "match_eq"), None)
            if inst is None:
                inst = new_instance("match_eq")
                if master:
                    chain.insert(0, inst)
                else:
                    idx = next((i for i, e in enumerate(chain) if e["id"] in ("delay", "reverb")), len(chain))
                    chain.insert(idx, inst)
            inst["params"]["curve"] = curve
            inst["params"]["reference"] = refs[0].name if refs else os.path.basename(ref_path)
            inst["enabled"] = True
            self.state.modified([] if master else [src_track.id])
            if master:
                self.state.track_modified.emit("__master__")
            self.fx.refresh()
            big = sorted(zip(dsp.THIRD_OCTAVE, curve), key=lambda c: -abs(c[1]))[:3]
            self.state.status.emit("Match EQ added (70%). Biggest moves: " +
                                   ", ".join(f"{f:g} Hz {g:+.1f} dB" for f, g in big))

        run_task(job, done, self._error)

    # ------------------------------------------------------------------ send buses
    def _edit_send(self, name):
        self._show_tab(self.fx)
        self.fx.edit_send(name)

    def _quick_sends(self, name, db, role=None):
        p = self.state.project
        self.state.push_undo("Sends")
        n = 0
        for t in p.vocal_tracks():
            if role is None or t.role == role:
                t.sends[name] = db
                n += 1
        self.mixer.rebuild()
        self.state.track_modified.emit("__send__")
        self.state.status.emit(f"{n} track(s) sent to the {name} bus at {db:+.0f} dB")

    # ------------------------------------------------------------------ vocal editing
    def strip_silence(self):
        t = self._cur()
        if not t or not t.clips:
            return
        db, ok = QInputDialog.getDouble(self, "Strip silence", "Silence is anything quieter than (dBFS):", -42, -80, -10, 1)
        if not ok:
            return
        self.state.push_undo("Strip silence")
        n = vocal_edit.strip_silence(self.state.project, t, db)
        self.state.modified([t.id])
        self.state.status.emit(f"{t.name}: split into {n} phrases (silence removed)")

    def align_to_lead(self):
        p = self.state.project
        t = self._cur()
        guide = next((v for v in p.vocal_tracks() if v.role == "Lead" and v.clips and v is not t), None)
        if not t or not t.clips or guide is None:
            self.state.status.emit("Select a double / stack track (and have a Lead track with audio)")
            return
        self.state.push_undo("Align to lead")
        self.state.status.emit(f"Aligning {t.name} to {guide.name}…")

        def job(task):
            return vocal_edit.align_to_guide(p, guide, t)

        def done(shifts):
            self.state.modified([t.id])
            if shifts:
                self.state.status.emit(f"Aligned {len(shifts)} phrases to '{guide.name}' (average move "
                                       f"{np.mean(np.abs(shifts)):.0f} ms)")

        run_task(job, done, self._error)

    def quantize(self):
        t = self._cur()
        if not t or not t.clips:
            return
        div, ok = QInputDialog.getItem(self, "Quantize phrases", "Grid:", ["1/16", "1/8", "1/4", "1/8T"], 0, False)
        if not ok:
            return
        strength, ok = QInputDialog.getInt(self, "Quantize phrases", "Strength % (keep some human feel):", 60, 10, 100)
        if not ok:
            return
        d = {"1/16": 4, "1/8": 2, "1/4": 1, "1/8T": 3}[div]
        self.state.push_undo("Quantize phrases")
        mv = vocal_edit.quantize_phrases(self.state.project, t, d, strength / 100.0)
        self.state.modified([t.id])
        self.state.status.emit(f"Quantized {len(mv)} phrases")

    def _use_take(self, tid, cid):
        p = self.state.project
        t = p.track(tid)
        c = next((c for c in t.clips if c.id == cid), None) if t else None
        if c:
            self._z(t, c, True)
            self.state.status.emit(f"'{c.name}' is now on top")

    def add_throw(self, kind):
        tg = self._targets()
        if not tg:
            self.state.status.emit("Select the word(s) to throw (drag a range on the vocal)")
            return
        self.state.push_undo("Add throw")
        for t, a, b in tg:
            t.throws.append(dict(start=int(a), end=int(b), kind=kind, amount_db=0.0, division="1/4"))
        self.state.modified(list({t.id for t, _, _ in tg}))
        self.state.status.emit({"delay": "Delay throw added - echoes repeat on the beat",
                                "reverb": "Reverb throw added - that word blooms into a big space",
                                "reverse": "Reverse swell added - it sucks in to the word"}[kind])

    def clear_throws(self):
        tg = self._targets()
        if not tg:
            return
        self.state.push_undo("Remove throws")
        for t, a, b in tg:
            t.throws = [th for th in t.throws if th["end"] <= a or th["start"] >= b]
        self.state.modified(list({t.id for t, _, _ in tg}))

    def _creative(self, kind):
        p = self.state.project
        bpm = p.bpm
        fns = {
            "stutter16": lambda a, sr: dsp.stutter(a, sr, bpm, "1/16"),
            "stutter8": lambda a, sr: dsp.stutter(a, sr, bpm, "1/8"),
            "tapestop": lambda a, sr: dsp.tape_stop(a, sr),
            "tapestart": lambda a, sr: dsp.tape_start(a, sr),
            "sweepdown": lambda a, sr: dsp.filter_sweep(a, sr, "lowpass", 18000, 350),
            "sweepup": lambda a, sr: dsp.filter_sweep(a, sr, "lowpass", 350, 18000),
        }
        names = dict(stutter16="Stutter 1/16", stutter8="Stutter 1/8", tapestop="Tape stop", tapestart="Tape start",
                     sweepdown="Filter sweep down", sweepup="Filter sweep up")
        self._process_bg(names[kind], fns[kind])

    def _apply_spectral(self, d):
        p = self.state.project
        t = p.track(d["track"])
        if not t:
            return
        sr = p.sr
        self.state.push_undo("Spectral repair")
        a_abs = d["start"] + d["t0"] * sr
        b_abs = d["start"] + d["t1"] * sr
        pad = int(0.1 * sr)
        process_range(p, t, int(a_abs) - pad, int(b_abs) + pad,
                      lambda seg, s0: dsp.spectral_attenuate(seg, sr, a_abs - s0, b_abs - s0, d["f0"], d["f1"],
                                                             float(d["gain_db"])),
                      "spectral", 0.0, pass_pos=True)
        self.state.modified([t.id])
        self.spectral.show_spec()
        self.state.status.emit("Spectral repair applied")

    # ------------------------------------------------------------------ ad-libs
    def adlib_from_selection(self):
        p = self.state.project
        tg = self._targets()
        if not tg:
            self.state.status.emit("Select the ad-lib first: drag over it on a vocal track (or click its clip)")
            self._show_tab(self.adlib_panel)
            return
        t, a, b = tg[0]
        _, audio = p.render_track_dry(t, a, b)
        self._show_tab(self.adlib_panel)
        self.adlib_panel.save_audio_as_adlib(audio, p.sr)

    def insert_adlib(self, meta, opts):
        p = self.state.project
        cur = self._cur()
        if opts.get("new_track") or cur is None or cur.kind != "vocal":
            cur = next((t for t in p.vocal_tracks() if t.role == "Ad-lib"), None) if not opts.get("new_track") else None
            if cur is None:
                cur = p.add_track("Ad-libs", "vocal", "Ad-lib")
        self.state.push_undo("Insert ad-lib")
        pos, until = self.state.playhead, None
        if opts.get("every_bars") and self.state.has_range:
            pos, until = self.state.range()
        clips = adlibs.insert_into_track(p, cur, meta, pos, opts.get("snap", True), opts.get("every_bars", 0), until)
        self.state.tracks_changed.emit()
        self.state.modified([cur.id])
        self.state.set_current_track(cur.id)
        self.adlib_panel.refresh()
        self.state.status.emit(f"Inserted '{meta['name']}' x{len(clips)} on {cur.name}")

    # ------------------------------------------------------------------ beat tools
    def _beat(self):
        p = self.state.project
        return next((t for t in p.beat_tracks() if t.clips), None)

    def transpose_beat(self):
        b = self._beat()
        if not b:
            return
        st, ok = QInputDialog.getInt(self, "Transpose beat", "Semitones (+ up / - down) to fit your voice:", -1, -6, 6)
        if not ok or st == 0:
            return
        p = self.state.project
        self.state.push_undo("Transpose beat")
        self.state.status.emit("Transposing beat…")

        def job(task):
            vocal_edit.transpose_beat(p, b, st)
            return True

        run_task(job, lambda _: (self.state.modified(), self.auto.sync(), self._update_key_lbl(),
                                 self.state.status.emit(f"Beat moved {st:+d} semitones - key is now "
                                                        f"{analysis.key_name(p.key_root, p.key_scale)}")), self._error)

    def change_tempo(self):
        b = self._beat()
        if not b:
            return
        p = self.state.project
        bpm, ok = QInputDialog.getDouble(self, "Change beat tempo", "New BPM (key stays the same):", p.bpm, 40, 240, 1)
        if not ok or abs(bpm - p.bpm) < 0.01:
            return
        self.state.push_undo("Change tempo")

        def job(task):
            vocal_edit.change_beat_tempo(p, b, bpm)
            return True

        run_task(job, lambda _: (self.state.modified(), self.auto.sync(), self._update_key_lbl(),
                                 self.state.status.emit(f"Beat is now {bpm:.1f} BPM")), self._error)

    def beat_drop(self):
        if not self.state.has_range:
            self.state.status.emit("Select the range where the beat should cut out")
            return
        p = self.state.project
        self.state.push_undo("Beat drop")
        a, b = self.state.range()
        vocal_edit.beat_drop(p, p.beat_tracks(), a, b)
        self.state.modified([t.id for t in p.beat_tracks()])

    def add_vocal_remover(self):
        b = self._beat()
        if not b:
            return
        self.state.push_undo("Vocal remover")
        b.fx.insert(0, new_instance("vocal_remover"))
        self.state.modified([b.id])
        self.fx.refresh()
        self.state.status.emit("Vocal Remover added to the beat (works on vocals panned to the centre)")

    # ------------------------------------------------------------------ Audition import
    def import_audition(self, path=None):
        if not self._maybe_save():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Import Adobe Audition session", "",
                                                  "Audition multitrack session (*.sesx)")
        if not path:
            return
        self.stop()
        self.state.status.emit("Importing Audition session…")

        def job(task):
            return audition_import.import_sesx(path, progress=lambda m: task.report(m))

        def done(res):
            proj, report = res
            try:
                proj.save()
            except Exception:
                pass
            self.state.set_project(proj)
            self._add_recent(proj.saved_path) if proj.saved_path else None
            dlg = QDialog(self)
            dlg.setWindowTitle("Audition import report")
            dlg.resize(720, 520)
            lay = QVBoxLayout(dlg)
            te = QPlainTextEdit("\n".join(report))
            te.setReadOnly(True)
            lay.addWidget(te)
            bb = QDialogButtonBox(QDialogButtonBox.Ok)
            bb.accepted.connect(dlg.accept)
            lay.addWidget(bb)
            dlg.exec()

        run_task(job, done, self._error, lambda m, f: self.state.status.emit(m))
