"""FX Rack: edit the effect chain of the selected track (or the master bus)."""
from __future__ import annotations

import copy
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton,
                               QMenu, QComboBox, QLabel, QScrollArea, QInputDialog, QMessageBox, QToolButton,
                               QAbstractItemView, QSplitter, QCheckBox)

from ..core.effects import REGISTRY, CATEGORIES, new_instance, eq_bands_from_params
from ..core import presets
from .widgets import ParamEditor, EQCurve


class FXRack(QWidget):
    apply_destructive = Signal(dict)   # effect instance to apply to the selection

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.target = "track"   # or "master"
        self.editor = None
        self.eq_curve = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        head = QHBoxLayout()
        self.title = QLabel("No track selected")
        self.title.setObjectName("big")
        head.addWidget(self.title, 1)
        self.master_btn = QToolButton()
        self.master_btn.setText("Master bus")
        self.master_btn.setCheckable(True)
        self.master_btn.setToolTip("Edit the master (mix bus) chain instead of the selected track")
        self.master_btn.toggled.connect(self._toggle_master)
        head.addWidget(self.master_btn)
        lay.addLayout(head)
        prow = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.setToolTip("Load a complete vocal chain")
        prow.addWidget(QLabel("Style:"))
        prow.addWidget(self.preset, 1)
        b_load = QPushButton("Load")
        b_load.clicked.connect(self._load_preset)
        b_save = QPushButton("Save…")
        b_save.setToolTip("Save this chain as your own preset")
        b_save.clicked.connect(self._save_preset)
        prow.addWidget(b_load)
        prow.addWidget(b_save)
        lay.addLayout(prow)
        split = QSplitter(Qt.Vertical)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.model().rowsMoved.connect(self._reordered)
        self.list.currentRowChanged.connect(self._select)
        self.list.itemChanged.connect(self._item_toggled)
        tl.addWidget(self.list, 1)
        brow = QHBoxLayout()
        self.add_btn = QPushButton("+ Add effect")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._add_menu)
        rm = QPushButton("Remove")
        rm.clicked.connect(self._remove)
        up = QToolButton(); up.setText("▲"); up.clicked.connect(lambda: self._move(-1))
        dn = QToolButton(); dn.setText("▼"); dn.clicked.connect(lambda: self._move(1))
        dup = QPushButton("Duplicate")
        dup.clicked.connect(self._duplicate)
        for w in (self.add_btn, rm, dup, up, dn):
            brow.addWidget(w)
        tl.addLayout(brow)
        split.addWidget(top)
        self.param_area = QScrollArea()
        self.param_area.setWidgetResizable(True)
        split.addWidget(self.param_area)
        split.setSizes([220, 420])
        lay.addWidget(split, 1)
        arow = QHBoxLayout()
        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setObjectName("muted")
        lay.addWidget(self.desc)
        self.apply_btn = QPushButton("Apply this effect to selection (destructive)")
        self.apply_btn.setToolTip("Like Audition's Waveform editor: permanently processes the selected range "
                                  "of the selected track with this effect's current settings. Undo-able.")
        self.apply_btn.clicked.connect(self._apply_destructive)
        arow.addWidget(self.apply_btn)
        lay.addLayout(arow)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._emit_modified)
        self._undo_pushed = False
        state.selected_track_changed.connect(lambda *_: self.refresh())
        state.project_replaced.connect(self.refresh)
        state.tracks_changed.connect(self.refresh)
        self._fill_presets()
        self.refresh()

    # ------------------------------------------------------------ data
    def chain(self):
        p = self.state.project
        if not p:
            return None
        if self.target == "master":
            return p.master_fx
        t = p.track(self.state.current_track) if self.state.current_track else None
        return t.fx if t else None

    def _track(self):
        p = self.state.project
        return p.track(self.state.current_track) if p and self.state.current_track else None

    def _fill_presets(self):
        self.preset.clear()
        self.preset.addItems(list(presets.STYLES.keys()))
        users = presets.list_user_presets()
        if users:
            self.preset.insertSeparator(self.preset.count())
            for n in users:
                self.preset.addItem(f"★ {n}")

    def refresh(self):
        self.list.blockSignals(True)
        self.list.clear()
        ch = self.chain()
        t = self._track()
        if self.target == "master":
            self.title.setText("Master bus")
        else:
            self.title.setText(t.name if t else "No track selected")
        if ch is not None:
            for inst in ch:
                d = REGISTRY.get(inst["id"])
                it = QListWidgetItem(d.name if d else inst["id"])
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
                it.setCheckState(Qt.Checked if inst.get("enabled", True) else Qt.Unchecked)
                it.setData(Qt.UserRole, inst["uid"])
                it.setToolTip(d.description if d else "")
                self.list.addItem(it)
        self.list.blockSignals(False)
        self.setEnabled(ch is not None)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._show_params(None)

    def _inst_by_uid(self, u):
        for inst in self.chain() or []:
            if inst["uid"] == u:
                return inst
        return None

    # ------------------------------------------------------------ editing
    def _changed(self, push_label=None, immediate=False):
        if push_label:
            self.state.push_undo(push_label)
        if immediate:
            self._emit_modified()
        else:
            self._debounce.start()

    def _emit_modified(self):
        self._undo_pushed = False
        p = self.state.project
        if not p:
            return
        if self.target == "master":
            p.dirty = True
            self.state.track_modified.emit("__master__")
        else:
            t = self._track()
            if t:
                self.state.modified([t.id])

    def _select(self, row):
        if row < 0:
            self._show_params(None)
            return
        u = self.list.item(row).data(Qt.UserRole)
        self._show_params(self._inst_by_uid(u))

    def _show_params(self, inst):
        self.eq_curve = None
        if inst is None:
            self.param_area.setWidget(QLabel("  Select an effect to edit its settings."))
            self.desc.setText("")
            return
        d = REGISTRY[inst["id"]]
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        if inst["id"] == "eq":
            self.eq_curve = EQCurve()
            self.eq_curve.set_bands(eq_bands_from_params(inst["params"]), self.state.project.sr)
            bl.addWidget(self.eq_curve)
        ed = ParamEditor(d, inst["params"])
        ed.changed.connect(lambda k, v, inst=inst: self._param(inst, k, v))
        bl.addWidget(ed)
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(lambda: self._reset(inst))
        bl.addWidget(reset)
        bl.addStretch(1)
        self.param_area.setWidget(box)
        self.desc.setText(d.description)
        self.apply_btn.setEnabled(self.target != "master" and not d.needs_sidechain)

    def _param(self, inst, k, v):
        if inst["params"].get(k) == v:
            return
        if not self._undo_pushed:
            self.state.push_undo(f"Change {REGISTRY[inst['id']].name}")
            self._undo_pushed = True
        inst["params"][k] = v
        if self.eq_curve is not None and inst["id"] == "eq":
            self.eq_curve.set_bands(eq_bands_from_params(inst["params"]), self.state.project.sr)
        self._changed()

    def _reset(self, inst):
        self.state.push_undo("Reset effect")
        inst["params"] = REGISTRY[inst["id"]].defaults()
        self._show_params(inst)
        self._changed(immediate=True)

    def _item_toggled(self, it):
        inst = self._inst_by_uid(it.data(Qt.UserRole))
        if inst is None:
            return
        on = it.checkState() == Qt.Checked
        if inst.get("enabled", True) != on:
            self.state.push_undo("Toggle effect")
            inst["enabled"] = on
            self._changed(immediate=True)

    def _reordered(self, *a):
        ch = self.chain()
        if ch is None:
            return
        order = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        self.state.push_undo("Reorder effects")
        ch.sort(key=lambda inst: order.index(inst["uid"]) if inst["uid"] in order else 999)
        self._changed(immediate=True)

    def _add_menu(self):
        m = QMenu(self)
        by = {}
        for d in REGISTRY.values():
            by.setdefault(d.category, []).append(d)
        for cat in CATEGORIES:
            if cat not in by:
                continue
            sub = m.addMenu(cat)
            for d in by[cat]:
                a = sub.addAction(d.name)
                a.setToolTip(d.description)
                a.triggered.connect(lambda _=False, eid=d.id: self._add(eid))
        m.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft()))

    def _add(self, eid):
        ch = self.chain()
        if ch is None:
            return
        self.state.push_undo("Add effect")
        row = self.list.currentRow()
        inst = new_instance(eid)
        ch.insert(row + 1 if row >= 0 else len(ch), inst)
        self.refresh()
        self.list.setCurrentRow(ch.index(inst))
        self._changed(immediate=True)

    def _remove(self):
        ch = self.chain()
        row = self.list.currentRow()
        if ch is None or row < 0:
            return
        self.state.push_undo("Remove effect")
        u = self.list.item(row).data(Qt.UserRole)
        ch[:] = [e for e in ch if e["uid"] != u]
        self.refresh()
        self._changed(immediate=True)

    def _duplicate(self):
        ch = self.chain()
        row = self.list.currentRow()
        if ch is None or row < 0:
            return
        inst = copy.deepcopy(self._inst_by_uid(self.list.item(row).data(Qt.UserRole)))
        inst["uid"] = new_instance(inst["id"])["uid"]
        self.state.push_undo("Duplicate effect")
        ch.insert(row + 1, inst)
        self.refresh()
        self.list.setCurrentRow(row + 1)
        self._changed(immediate=True)

    def _move(self, d):
        ch = self.chain()
        row = self.list.currentRow()
        if ch is None or row < 0 or not (0 <= row + d < len(ch)):
            return
        self.state.push_undo("Move effect")
        u = self.list.item(row).data(Qt.UserRole)
        i = next(i for i, e in enumerate(ch) if e["uid"] == u)
        ch[i], ch[i + d] = ch[i + d], ch[i]
        self.refresh()
        self.list.setCurrentRow(row + d)
        self._changed(immediate=True)

    def _load_preset(self):
        ch = self.chain()
        if ch is None:
            return
        name = self.preset.currentText()
        if name.startswith("★ "):
            new = copy.deepcopy(presets.list_user_presets().get(name[2:], []))
            for e in new:
                e["uid"] = new_instance(e["id"])["uid"] if e.get("id") in REGISTRY else e.get("uid")
        else:
            new = presets.style_chain(name)
        if self.target == "master" and not name.startswith("★ "):
            new = presets.chain_master()
        self.state.push_undo(f"Load preset {name}")
        ch[:] = new
        self.refresh()
        self._changed(immediate=True)

    def _save_preset(self):
        ch = self.chain()
        if not ch:
            return
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if ok and name.strip():
            presets.save_user_preset(name.strip(), ch)
            self._fill_presets()
            self.state.status.emit(f"Saved preset '{name}'")

    def _toggle_master(self, on):
        self.target = "master" if on else "track"
        self.refresh()

    def _apply_destructive(self):
        row = self.list.currentRow()
        if row < 0:
            return
        inst = self._inst_by_uid(self.list.item(row).data(Qt.UserRole))
        if inst:
            self.apply_destructive.emit(copy.deepcopy(inst))
