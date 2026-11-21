"""Run heavy processing off the UI thread."""
from __future__ import annotations

import traceback
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    progress = Signal(str, float)


class Task(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()
        self.cancelled = False
        self.setAutoDelete(True)

    def report(self, text, frac=0.0):
        try:
            self.signals.progress.emit(str(text), float(frac))
        except RuntimeError:
            pass

    @Slot()
    def run(self):
        try:
            res = self.fn(self, *self.args, **self.kwargs)
        except InterruptedError:
            _KEEP.discard(self)
            return
        except Exception:
            try:
                self.signals.error.emit(traceback.format_exc())
            except RuntimeError:
                pass
            return
        try:
            self.signals.done.emit(res)
        except RuntimeError:
            pass


_KEEP = set()


def run_task(fn, on_done=None, on_error=None, on_progress=None, *args, **kwargs) -> Task:
    """fn(task, *args) runs in a worker thread; callbacks fire on the UI thread."""
    t = Task(fn, *args, **kwargs)
    t.setAutoDelete(False)
    _KEEP.add(t)
    if on_done:
        t.signals.done.connect(on_done)
    if on_error:
        t.signals.error.connect(on_error)
    if on_progress:
        t.signals.progress.connect(on_progress)
    t.signals.done.connect(lambda *_: _KEEP.discard(t))
    t.signals.error.connect(lambda *_: _KEEP.discard(t))
    QThreadPool.globalInstance().start(t)
    return t
