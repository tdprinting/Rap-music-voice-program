"""Shared UI state: the open project, selection, playhead and change notifications."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..core.project import Project, UndoStack


class StudioState(QObject):
    project_replaced = Signal()
    tracks_changed = Signal()            # tracks added/removed/reordered/renamed
    track_modified = Signal(str)         # audio or FX of a track changed (track id) -> re-render
    track_params = Signal(str)           # volume/pan/mute/solo changed (no re-render)
    selection_changed = Signal()
    selected_track_changed = Signal(str)
    playhead_moved = Signal(int)
    view_changed = Signal()
    status = Signal(str)
    undo_changed = Signal()

    def __init__(self):
        super().__init__()
        self.project: Project | None = None
        self.undo: UndoStack | None = None
        self.sel_start = 0
        self.sel_end = 0
        self.sel_tracks: set[str] = set()
        self.selected_clips: set[str] = set()
        self.current_track: str | None = None
        self.playhead = 0
        self.snap = True
        self.clipboard = []
        self.record_preview = None        # (track_id, start, length)
        self.show_breaths = True

    def set_project(self, p: Project):
        self.project = p
        self.undo = UndoStack(p)
        self.sel_start = self.sel_end = 0
        self.sel_tracks.clear()
        self.selected_clips.clear()
        self.current_track = p.tracks[0].id if p.tracks else None
        self.playhead = 0
        self.project_replaced.emit()
        self.undo_changed.emit()

    # selection helpers
    @property
    def has_range(self):
        return self.sel_end > self.sel_start

    def range(self):
        return (min(self.sel_start, self.sel_end), max(self.sel_start, self.sel_end))

    def target_tracks(self):
        p = self.project
        if not p:
            return []
        if self.sel_tracks:
            return [t for t in p.tracks if t.id in self.sel_tracks]
        t = p.track(self.current_track) if self.current_track else None
        return [t] if t else []

    def set_current_track(self, tid):
        if tid != self.current_track:
            self.current_track = tid
            self.selected_track_changed.emit(tid or "")

    def push_undo(self, label):
        if self.undo:
            self.undo.push(label)
            self.undo_changed.emit()

    def modified(self, track_ids=None):
        p = self.project
        if not p:
            return
        p.dirty = True
        ids = track_ids if track_ids is not None else [t.id for t in p.tracks]
        for tid in ids:
            t = p.track(tid)
            if t:
                p.invalidate(t)
            self.track_modified.emit(tid)
        # beat carve depends on vocals
        for b in p.beat_tracks():
            if b.id not in ids:
                p.invalidate(b)
                self.track_modified.emit(b.id)
        self.view_changed.emit()

    def set_playhead(self, pos):
        self.playhead = max(0, int(pos))
        self.playhead_moved.emit(self.playhead)
