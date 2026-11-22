"""Lyrics studio: write with live rhyme colours + syllable/bar counts, find rhymes,
lay lyrics on the beat grid and drive the teleprompter."""
from __future__ import annotations

import os
from PySide6.QtCore import Qt, Signal, QTimer, QRect, QSize
from PySide6.QtGui import (QSyntaxHighlighter, QTextCharFormat, QColor, QFont, QPainter, QTextCursor,
                           QTextFormat)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QLabel, QPushButton,
                               QComboBox, QDoubleSpinBox, QLineEdit, QListWidget, QListWidgetItem, QSplitter,
                               QToolButton, QMenu, QFileDialog, QMessageBox, QCheckBox, QTabWidget, QTextEdit)

from . import theme
from .widgets import fmt_time
from .workers import run_task
from ..core import lyrics as L

def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


RHYME_COLORS = ["#e04848", "#3f8cff", "#f0b429", "#2ec4b6", "#b06cff", "#ff7aa8", "#7bd389", "#ff9f43",
                "#4dd0e1", "#e57373", "#9575cd", "#aed581", "#ffd54f", "#64b5f6", "#f06292", "#81c784"]


class RhymeHighlighter(QSyntaxHighlighter):
    def __init__(self, doc, panel):
        super().__init__(doc)
        self.panel = panel
        self.groups = {}
        self.current_line = -1

    def set_groups(self, groups):
        self.groups = groups
        self.rehighlight()

    def highlightBlock(self, text):
        bn = self.currentBlock().blockNumber()
        if L.is_section(text.strip()) and text.strip():
            f = QTextCharFormat()
            f.setForeground(QColor(theme.BLUE))
            f.setFontWeight(QFont.Bold)
            self.setFormat(0, len(text), f)
            return
        for (li, a, b), g in self.groups.items():
            if li != bn:
                continue
            f = QTextCharFormat()
            col = QColor(RHYME_COLORS[g % len(RHYME_COLORS)])
            bg = QColor(col)
            bg.setAlpha(70)
            f.setBackground(bg)
            f.setForeground(col.lighter(150))
            f.setFontWeight(QFont.DemiBold)
            self.setFormat(a, b - a, f)


class _Gutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, e):
        self.editor.paint_gutter(e)


class LyricsEdit(QPlainTextEdit):
    """Text editor with a gutter showing bar number and syllable count for each line."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(QFont("Segoe UI", 12))
        self.gutter = _Gutter(self)
        self.line_info = {}      # line index -> (bar, syllables, synced)
        self.active_line = -1
        self.blockCountChanged.connect(lambda *_: self._update_margin())
        self.updateRequest.connect(self._update_gutter)
        self._update_margin()
        self.setPlaceholderText("[Verse 1]\nWrite your bars here - one line per bar.\n"
                                "Rhymes light up as you type. Syllables and bar numbers show on the left.\n"
                                "Start a line with {2} to make it last 2 bars.")

    def gutter_width(self):
        return 92

    def _update_margin(self):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, e):
        p = QPainter(self.gutter)
        p.fillRect(e.rect(), QColor(theme.PANEL))
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        p.setFont(QFont("Consolas", 9))
        while block.isValid() and top <= e.rect().bottom():
            bottom = top + round(self.blockBoundingRect(block).height())
            n = block.blockNumber()
            info = self.line_info.get(n)
            if info and block.isVisible():
                bar, syl, synced = info
                if n == self.active_line:
                    p.fillRect(0, top, self.gutter_width(), bottom - top, QColor(63, 140, 255, 90))
                col = theme.MUTED
                if syl >= 17:
                    col = theme.RED
                elif syl >= 13:
                    col = "#f0b429"
                p.setPen(QColor(theme.MUTED))
                p.drawText(4, top, 44, bottom - top, Qt.AlignLeft | Qt.AlignVCenter, f"{'⏱' if synced else 'bar'} {bar:g}")
                p.setPen(QColor(col))
                p.drawText(48, top, 40, bottom - top, Qt.AlignRight | Qt.AlignVCenter, f"{syl} syl")
            block = block.next()
            top = bottom
        p.end()


class LyricsPanel(QWidget):
    lyrics_changed = Signal()
    open_teleprompter = Signal()

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.sync_mode = False
        self.sync_next = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        top = QHBoxLayout()
        self.tele_btn = QPushButton("🎤  Teleprompter")
        self.tele_btn.setObjectName("primary")
        self.tele_btn.setToolTip("Big scrolling lyrics that follow the beat while you record (T to tap-sync)")
        self.tele_btn.clicked.connect(self.open_teleprompter.emit)
        top.addWidget(self.tele_btn)
        tmpl = QToolButton()
        tmpl.setText("Templates ▾")
        tmpl.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(tmpl)
        for name in L.TEMPLATES:
            m.addAction(name, lambda _=False, n=name: self._template(n))
        tmpl.setMenu(m)
        top.addWidget(tmpl)
        more = QToolButton()
        more.setText("File ▾")
        more.setPopupMode(QToolButton.InstantPopup)
        fm = QMenu(more)
        fm.addAction("Import lyrics (.txt / .lrc)…", self._import)
        fm.addAction("Export lyrics (.txt)…", lambda: self._export("txt"))
        fm.addAction("Export synced lyrics (.lrc)…", lambda: self._export("lrc"))
        fm.addSeparator()
        fm.addAction("Add section markers to the timeline", self._sections_to_markers)
        more.setMenu(fm)
        top.addWidget(more)
        top.addStretch(1)
        lay.addLayout(top)

        grid = QHBoxLayout()
        grid.addWidget(QLabel("Start bar"))
        self.start_bar = QDoubleSpinBox(); self.start_bar.setRange(1, 999); self.start_bar.setDecimals(0)
        self.start_bar.setToolTip("Bar where the first lyric line starts")
        grid.addWidget(self.start_bar)
        here = QToolButton(); here.setText("= playhead"); here.setToolTip("Start the lyrics on the bar at the playhead")
        here.clicked.connect(self._start_here)
        grid.addWidget(here)
        grid.addWidget(QLabel("Bars/line"))
        self.bpl = QDoubleSpinBox(); self.bpl.setRange(0.25, 8); self.bpl.setSingleStep(0.5); self.bpl.setDecimals(2)
        grid.addWidget(self.bpl)
        grid.addWidget(QLabel("Rhymes"))
        self.mode = QComboBox(); self.mode.addItems(["end", "all", "strict end", "off"])
        self.mode.setToolTip("end = last word of each line, all = internal rhymes too, strict = perfect rhymes only")
        grid.addWidget(self.mode)
        lay.addLayout(grid)
        srow = QHBoxLayout()
        self.sync_btn = QPushButton("⏱ Tap-sync")
        self.sync_btn.setCheckable(True)
        self.sync_btn.setToolTip("Play the beat and press T at the start of each line to time it exactly")
        self.sync_btn.toggled.connect(self._sync_toggled)
        clr = QPushButton("Clear timing"); clr.clicked.connect(self._clear_sync)
        srow.addWidget(self.sync_btn); srow.addWidget(clr)
        for n in (4, 8, 16):
            b = QPushButton(f"Loop {n}")
            b.setToolTip(f"Loop {n} bars from the playhead while you write")
            b.clicked.connect(lambda _=False, n=n: self.state.loop_bars_requested.emit(n))
            srow.addWidget(b)
        lay.addLayout(srow)

        split = QSplitter(Qt.Vertical)
        self.edit = LyricsEdit()
        self.hl = RhymeHighlighter(self.edit.document(), self)
        split.addWidget(self.edit)
        # rhyme finder
        rf = QWidget()
        rl = QVBoxLayout(rf)
        rl.setContentsMargins(0, 4, 0, 0)
        row = QHBoxLayout()
        self.word = QLineEdit()
        self.word.setPlaceholderText("Rhymes for… (or click a word in your lyrics)")
        self.word.returnPressed.connect(self.lookup)
        go = QPushButton("Find"); go.clicked.connect(self.lookup)
        row.addWidget(self.word, 1); row.addWidget(go)
        rl.addLayout(row)
        self.rtabs = QTabWidget()
        self.lists = {}
        for k, lbl in (("perfect", "Perfect"), ("multi", "Multis"), ("near", "Slant")):
            lw = QListWidget()
            lw.setFlow(QListWidget.LeftToRight)
            lw.setWrapping(True)
            lw.setResizeMode(QListWidget.Adjust)
            lw.setSpacing(3)
            lw.itemDoubleClicked.connect(self._insert_word)
            self.lists[k] = lw
            self.rtabs.addTab(lw, lbl)
        rl.addWidget(self.rtabs, 1)
        split.addWidget(rf)
        split.setSizes([520, 200])
        lay.addWidget(split, 1)
        self.stats = QLabel("")
        self.stats.setObjectName("muted")
        self.stats.setWordWrap(True)
        lay.addWidget(self.stats)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._reanalyze)
        self.edit.textChanged.connect(self._timer.start)
        self.edit.cursorPositionChanged.connect(self._cursor_word)
        for w in (self.start_bar, self.bpl):
            w.valueChanged.connect(lambda *_: self._timer.start())
        self.mode.currentTextChanged.connect(lambda *_: self._timer.start())
        self._loading = False
        self._last_word = ""
        state.project_replaced.connect(self.load)
        self.load()

    # ------------------------------------------------------------ data
    def data(self):
        return self.state.project.lyrics if self.state.project else None

    def load(self):
        d = self.data()
        if d is None:
            return
        self._loading = True
        self.edit.setPlainText(d.get("text", ""))
        self.start_bar.setValue(_num(d.get("start_bar"), 1.0))
        self.bpl.setValue(_num(d.get("bars_per_line"), 1.0))
        mode = d.get("rhyme_mode", "end")
        self.mode.setCurrentText(mode if mode in ("end", "all", "strict end", "off") else "end")
        self._loading = False
        self._reanalyze()

    def sheet(self):
        d = self.data()
        if d is None:
            return L.LyricSheet()
        key = (d.get("text"), str(d.get("start_bar")), str(d.get("bars_per_line")), repr(sorted(d.get("timings", {}).items())),
               repr(d.get("section_bars")))
        if getattr(self, "_sheet_key", None) == key:
            return self._sheet
        self._sheet_key = key
        self._sheet = self._parse(d)
        return self._sheet

    def _parse(self, d):
        return L.parse(d.get("text", "") or "", _num(d.get("start_bar"), 1.0), _num(d.get("bars_per_line"), 1.0) or 1.0,
                       d.get("timings", {}), d.get("section_bars", {}))

    def _reanalyze(self):
        d = self.data()
        if d is None:
            return
        text = self.edit.toPlainText()
        if not self._loading:
            if text != d.get("text") or self.start_bar.value() != d.get("start_bar") or self.bpl.value() != d.get("bars_per_line") \
                    or self.mode.currentText() != d.get("rhyme_mode"):
                self.state.project.dirty = True
            d["text"] = text if isinstance(text, str) else ""
            d["start_bar"] = _num(self.start_bar.value(), 1.0)
            d["bars_per_line"] = _num(self.bpl.value(), 1.0)
            d["rhyme_mode"] = self.mode.currentText()
        mode = self.mode.currentText()
        groups = L.rhyme_groups(text, "all" if mode == "all" else ("off" if mode == "off" else "end"),
                                strict=(mode == "strict end"))
        self.hl.set_groups(groups)
        sh = self.sheet()
        self.edit.line_info = {ln.index: (ln.bar, ln.syllables, ln.time is not None) for ln in sh.lines}
        self.edit.gutter.update()
        p = self.state.project
        st = L.stats(sh, p.bpm, p.time_sig)
        if st["lines"]:
            self.stats.setText(f"{st['lines']} lines · {st['bars']:g} bars · {st['words']} words · "
                               f"{st['avg_syl']:.1f} syllables/bar ({L.density_label(st['avg_syl'])}) · "
                               f"{st['syl_per_sec']:.1f} syl/sec at {p.bpm:.0f} BPM")
        else:
            self.stats.setText(f"Rhyme dictionary: {L.dictionary_size():,} words · offline")
        self.lyrics_changed.emit()

    # ------------------------------------------------------------ rhymes
    def _cursor_word(self):
        c = self.edit.textCursor()
        c.select(QTextCursor.WordUnderCursor)
        w = c.selectedText().strip("'")
        if w and w.isalpha() and len(w) > 1 and w.lower() != self._last_word:
            self._last_word = w.lower()
            self.word.setText(w)
            self.lookup()

    def lookup(self):
        w = self.word.text().strip()
        if not w:
            return

        def job(task):
            return L.find_rhymes(w, 80)

        def done(r):
            for k, lw in self.lists.items():
                lw.clear()
                for cand in r.get(k, []):
                    it = QListWidgetItem(f"{cand}  ·{L.syllables(cand)}")
                    it.setData(Qt.UserRole, cand)
                    lw.addItem(it)
            self.rtabs.setTabText(0, f"Perfect ({len(r['perfect'])})")
            self.rtabs.setTabText(1, f"Multis ({len(r['multi'])})")
            self.rtabs.setTabText(2, f"Slant ({len(r['near'])})")

        run_task(job, done, lambda tb: None)

    def _insert_word(self, it):
        self.edit.insertPlainText(it.data(Qt.UserRole) + " ")
        self.edit.setFocus()

    # ------------------------------------------------------------ actions
    def _template(self, name):
        if self.edit.toPlainText().strip() and \
                QMessageBox.question(self, "Template", "Add the template at the end of your lyrics?") != QMessageBox.Yes:
            return
        self.edit.moveCursor(QTextCursor.End)
        self.edit.insertPlainText(("\n" if self.edit.toPlainText().strip() else "") + L.TEMPLATES[name])

    def _start_here(self):
        p = self.state.project
        bar, _ = p.bar_beat(self.state.playhead)
        self.start_bar.setValue(max(1, bar))

    def _sync_toggled(self, on):
        self.sync_mode = on
        self.sync_next = 0
        self.state.status.emit("Tap-sync ON: play the beat and press T at the start of every line." if on
                               else "Tap-sync off")

    def stamp(self, pos):
        """Called when the user presses T during playback in tap-sync mode."""
        sh = self.sheet()
        if not sh.lines:
            return
        if self.sync_next >= len(sh.lines):
            self.state.status.emit("All lines are synced")
            return
        ln = sh.lines[self.sync_next]
        self.data().setdefault("timings", {})[str(ln.index)] = int(pos)
        self.sync_next += 1
        self.state.project.dirty = True
        self.state.status.emit(f"Line {self.sync_next}/{len(sh.lines)} synced: {ln.text[:40]}")
        self._reanalyze()

    def _clear_sync(self):
        d = self.data()
        d["timings"] = {}
        self.sync_next = 0
        self._reanalyze()

    def set_active_line(self, idx):
        if idx != self.edit.active_line:
            self.edit.active_line = idx
            self.edit.gutter.update()

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import lyrics", "", "Lyrics (*.txt *.lrc)")
        if not path:
            return
        with open(path, encoding="utf-8", errors="ignore") as fh:
            txt = fh.read()
        if path.lower().endswith(".lrc"):
            body, tim = L.from_lrc(txt, self.state.project.sr)
            self.edit.setPlainText(body)
            self.data()["timings"] = {str(k): v for k, v in tim.items()}
        else:
            self.edit.setPlainText(txt)
        self._reanalyze()

    def _export(self, kind):
        p = self.state.project
        path, _ = QFileDialog.getSaveFileName(self, "Export lyrics", os.path.join(p.folder, f"{p.name}.{kind}"),
                                              "Synced lyrics (*.lrc)" if kind == "lrc" else "Text (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            if kind == "lrc":
                fh.write(L.to_lrc(self.sheet(), p, p.lyrics.get("title") or p.name, p.lyrics.get("artist", "")))
            else:
                fh.write(self.edit.toPlainText())
        self.state.status.emit(f"Saved {path}")

    def _sections_to_markers(self):
        from ..core.project import Marker
        p = self.state.project
        sh = self.sheet()
        times = L.line_times(sh, p)
        firsts = {}
        for s, e, ln in times:
            firsts.setdefault(ln.section or "Lyrics", s)
        self.state.push_undo("Lyric markers")
        for name, pos in firsts.items():
            p.markers.append(Marker(int(pos), name, "section"))
        p.markers.sort(key=lambda m: m.pos)
        self.state.view_changed.emit()
        self.state.status.emit(f"Added {len(firsts)} section markers")
