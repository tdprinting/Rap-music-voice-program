"""Multitrack project model, render engine, save/load, undo and editing operations."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field, asdict
import numpy as np

from . import dsp
from .audio_io import load_audio, save_audio, to_stereo
from .effects import process_chain, sanitize_instance, REGISTRY, new_instance
from . import presets

PROJECT_EXT = ".sibproj"
SEND_NAMES = ("reverb", "delay")


def default_sends():
    return {
        "reverb": dict(fx=[new_instance("reverb", mix=100.0, wet_only=True, decay_s=1.6, predelay_ms=30.0,
                                        width=1.2)], volume_db=-4.0, mute=False),
        "delay": dict(fx=[new_instance("delay", mix=100.0, wet_only=True, feedback=30.0, division="1/4")],
                      volume_db=-6.0, mute=False),
    }


def default_lyrics():
    return dict(text="", start_bar=1.0, bars_per_line=1.0, timings={}, section_bars={}, rhyme_mode="end",
                title="", artist="", font_size=34)
FX_TAIL_S = 3.0
TRACK_COLORS = ["#e04848", "#3f8cff", "#f0b429", "#2ec4b6", "#b06cff", "#ff7aa8", "#7bd389", "#ff9f43"]


def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def uid():
    return uuid.uuid4().hex[:12]


def default_projects_dir():
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    d = os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "SIB Studio Projects")
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class Clip:
    file_id: str
    start: int            # timeline position (samples)
    length: int           # samples
    src_in: int = 0       # offset into the source file
    gain_db: float = 0.0
    fade_in: int = 0
    fade_out: int = 0
    muted: bool = False
    name: str = "Clip"
    id: str = field(default_factory=uid)
    take: int = 1

    @property
    def end(self):
        return self.start + self.length

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        c = Clip(**{k: v for k, v in d.items() if k in Clip.__dataclass_fields__})
        return c


@dataclass
class Track:
    name: str
    kind: str = "vocal"            # vocal | beat
    role: str = "Lead"
    volume_db: float = 0.0
    pan: float = 0.0
    mute: bool = False
    solo: bool = False
    armed: bool = False
    monitor: bool = False
    input_channel: int = 0          # 0-based input channel (mono recording)
    color: str = "#3f8cff"
    fx: list = field(default_factory=list)
    fx_bypass: bool = False
    clips: list = field(default_factory=list)
    id: str = field(default_factory=uid)
    height: int = 96
    breath_regions: list = field(default_factory=list)   # reviewed breaths [(start,end,enabled)]
    sends: dict = field(default_factory=lambda: {"reverb": -60.0, "delay": -60.0})  # dB, <= -59.5 = off
    automation: list = field(default_factory=list)        # volume envelope [[sample, dB], ...]
    show_automation: bool = False
    throws: list = field(default_factory=list)            # [{start,end,kind,amount_db,division}]

    def to_dict(self):
        d = asdict(self)
        d["clips"] = [c.to_dict() for c in self.clips]
        return d

    @staticmethod
    def from_dict(d):
        clips = [Clip.from_dict(c) for c in d.get("clips", [])]
        t = Track(**{k: v for k, v in d.items() if k in Track.__dataclass_fields__ and k != "clips"})
        t.clips = clips
        t.fx = [sanitize_instance(e) for e in t.fx]
        return t

    @property
    def end(self):
        return max((c.end for c in self.clips), default=0)


@dataclass
class Marker:
    pos: int
    name: str = "Marker"
    kind: str = "cue"   # cue | section
    id: str = field(default_factory=uid)


class AudioPool:
    """Owns all source audio. Files live in <project>/Audio and are never modified in place
    (destructive edits write a new file), which is what makes undo cheap and safe."""

    def __init__(self, project):
        self.project = project
        self.files: dict[str, dict] = {}
        self.cache: dict[str, np.ndarray] = {}

    def audio_dir(self):
        d = os.path.join(self.project.folder, "Audio")
        os.makedirs(d, exist_ok=True)
        return d

    def add_array(self, data: np.ndarray, name: str) -> str:
        fid = uid()
        safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in name)[:40].strip() or "audio"
        rel = os.path.join("Audio", f"{safe}_{fid[:6]}.wav")
        data = to_stereo(np.asarray(data, np.float32)) if data.ndim == 2 and data.shape[1] > 1 else dsp.as2d(data)
        save_audio(os.path.join(self.project.folder, rel), data, self.project.sr, "float")
        self.files[fid] = {"path": rel, "name": name, "channels": int(data.shape[1])}
        self.cache[fid] = data
        return fid

    def import_file(self, path: str, name: str | None = None) -> str:
        data, sr = load_audio(path, target_sr=self.project.sr)
        return self.add_array(data, name or os.path.splitext(os.path.basename(path))[0])

    def get(self, fid: str) -> np.ndarray:
        a = self.cache.get(fid)
        if a is None:
            info = self.files[fid]
            a, _ = load_audio(os.path.join(self.project.folder, info["path"]), target_sr=self.project.sr)
            self.cache[fid] = a
        return a

    def path(self, fid):
        return os.path.join(self.project.folder, self.files[fid]["path"])


class Project:
    def __init__(self, name="Untitled", sr=44100, folder=None):
        self.name = name
        self.sr = int(sr)
        self.folder = folder or os.path.join(default_projects_dir(), f"{name}-{time.strftime('%Y%m%d-%H%M%S')}")
        os.makedirs(self.folder, exist_ok=True)
        self.tracks: list[Track] = []
        self.markers: list[Marker] = []
        self.master_fx: list = presets.chain_master()
        self.master_volume_db = 0.0
        self.bpm = 90.0
        self.beat_offset_s = 0.0
        self.time_sig = 4
        self.key_root = 9
        self.key_scale = "Minor"
        self.a4 = 440.0
        self.analysis: dict = {}
        self.loop = None            # (start, end) samples
        self.lyrics: dict = default_lyrics()
        self.sends: dict = default_sends()
        self._final_cache: dict = {}
        self._send_cache: dict = {}
        self.pool = AudioPool(self)
        self.saved_path = None
        self._render_cache: dict = {}
        self.dirty = False

    # ------------------------------------------------------------- tracks
    def add_track(self, name, kind="vocal", role="Lead", style=None, index=None) -> Track:
        color = "#e04848" if kind == "beat" else TRACK_COLORS[(len(self.tracks) + 1) % len(TRACK_COLORS)]
        t = Track(name=name, kind=kind, role=role if kind == "vocal" else "Beat", color=color)
        rd = presets.ROLE_DEFAULTS.get(t.role, {})
        t.volume_db = rd.get("volume_db", 0.0)
        t.pan = rd.get("pan", 0.0)
        if kind == "beat":
            t.fx = presets.chain_beat()
        else:
            t.fx = presets.style_chain(style or presets.ROLE_STYLE.get(role, "Clean Rap Lead"))
        if index is None:
            self.tracks.append(t)
        else:
            self.tracks.insert(index, t)
        self.dirty = True
        return t

    def track(self, tid) -> Track | None:
        return next((t for t in self.tracks if t.id == tid), None)

    def beat_tracks(self):
        return [t for t in self.tracks if t.kind == "beat"]

    def vocal_tracks(self):
        return [t for t in self.tracks if t.kind == "vocal"]

    def reference_tracks(self):
        return [t for t in self.tracks if t.kind == "reference"]

    def mix_tracks(self):
        return [t for t in self.tracks if t.kind != "reference"]

    def find_clip(self, cid):
        for t in self.tracks:
            for c in t.clips:
                if c.id == cid:
                    return t, c
        return None, None

    def import_audio_to_track(self, path, track: Track, start=0, name=None) -> Clip:
        fid = self.pool.import_file(path, name)
        data = self.pool.get(fid)
        c = Clip(fid, int(start), len(data), 0, name=name or os.path.splitext(os.path.basename(path))[0])
        track.clips.append(c)
        self.dirty = True
        return c

    def add_clip_from_array(self, track, data, start, name="Take", take=1) -> Clip:
        fid = self.pool.add_array(data, name)
        c = Clip(fid, int(start), len(data), 0, name=name, take=take)
        track.clips.append(c)
        self.dirty = True
        return c

    @property
    def length(self):
        return max((t.end for t in self.tracks if t.kind != "reference"), default=0) or \
            max((t.end for t in self.tracks), default=0)

    # ------------------------------------------------------------- tempo grid
    def samples_per_beat(self):
        return self.sr * 60.0 / max(self.bpm, 1.0)

    def snap(self, pos, division=4):
        """Snap to 1/division of a beat (4 = sixteenth notes)."""
        step = self.samples_per_beat() / division
        off = self.beat_offset_s * self.sr
        return int(round((pos - off) / step) * step + off)

    def bar_beat(self, pos):
        b = (pos / self.sr - self.beat_offset_s) / (60.0 / self.bpm)
        bar = int(b // self.time_sig) + 1
        beat = b - (bar - 1) * self.time_sig + 1
        return bar, beat

    def ctx(self):
        return dict(bpm=self.bpm, key_root=self.key_root, key_scale=self.key_scale, a4=self.a4)

    # ------------------------------------------------------------- rendering
    def render_clip(self, c: Clip) -> np.ndarray:
        src = self.pool.get(c.file_id)
        seg = src[c.src_in: c.src_in + c.length]
        if len(seg) < c.length:
            seg = np.vstack([seg, np.zeros((c.length - len(seg), seg.shape[1]), np.float32)])
        seg = to_stereo(seg)
        if c.gain_db:
            seg = seg * dsp.db_to_lin(c.gain_db)
        if c.fade_in or c.fade_out:
            seg = dsp.apply_fade(seg, c.fade_in, c.fade_out, "log")
        return seg.astype(np.float32)

    def render_track_dry(self, t: Track, start=None, end=None):
        """-> (offset, stereo array) covering the track's clips."""
        clips = [c for c in t.clips if not c.muted and c.length > 0]
        if not clips:
            return 0, np.zeros((0, 2), np.float32)
        a = min(c.start for c in clips) if start is None else start
        b = max(c.end for c in clips) if end is None else end
        out = np.zeros((max(0, b - a), 2), np.float32)
        # list order is z-order: later clips sit on top of earlier ones (takes / comping),
        # like Audition with "play overlapping clips" off. Fades blend into what is below.
        for c in clips:
            s0, s1 = max(c.start, a), min(c.end, b)
            if s1 <= s0:
                continue
            seg = self.render_clip(c)[s0 - c.start: s1 - c.start]
            dst = out[s0 - a: s0 - a + len(seg)]
            if c.fade_in or c.fade_out:
                w = np.ones(c.length, np.float32)
                w = dsp.apply_fade(w, c.fade_in, c.fade_out, "log")[:, 0][s0 - c.start: s1 - c.start]
                dst *= (1 - w)[:, None]
                dst += seg
            else:
                dst[:] = seg
        return a, out

    def _track_key(self, t: Track, extra=""):
        clips = [(c.file_id, c.start, c.length, c.src_in, c.gain_db, c.fade_in, c.fade_out, c.muted)
                 for c in t.clips]
        fx = [] if t.fx_bypass else t.fx
        blob = json.dumps([clips, fx, self.ctx() if (any(e.get("enabled") for e in fx) or t.throws) else None,
                           t.throws, extra],
                          sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()

    def vocal_bus(self, progress=None, cancel=None):
        n = self.length + int(FX_TAIL_S * self.sr)
        bus = np.zeros((n, 2), np.float32)
        for t in self.vocal_tracks():
            if t.mute:
                continue
            off, a = self.track_audio(t, progress, cancel)
            seg = a[: max(0, n - off)]
            bus[off: off + len(seg)] += seg
        return bus

    def render_track(self, t: Track, progress=None, cancel=None):
        """Processed track (FX applied) -> (offset, stereo array). Cached."""
        needs_sc = (not t.fx_bypass) and any(e.get("enabled") and REGISTRY.get(e["id"]) and
                                              REGISTRY[e["id"]].needs_sidechain for e in t.fx)
        extra = ""
        if needs_sc:
            extra = "|".join(self._track_key(v) for v in self.vocal_tracks() if not v.mute)
        key = self._track_key(t, extra)
        hit = self._render_cache.get(t.id)
        if hit and hit[0] == key:
            return hit[1], hit[2]
        off, dry = self.render_track_dry(t)
        if t.fx_bypass or not t.fx or not len(dry):
            res = (off, dry)
        else:
            ctx = self.ctx()
            if needs_sc:
                bus = self.vocal_bus(None, cancel)
                ctx["vocal_bus"] = bus[off: off + len(dry) + int(FX_TAIL_S * self.sr)]
            padded = np.vstack([dry, np.zeros((int(FX_TAIL_S * self.sr), 2), np.float32)])
            y = process_chain(padded, self.sr, t.fx, ctx,
                              (lambda f, n: progress(t.name, f, n)) if progress else None, cancel)
            # trim silent tail
            nz = np.flatnonzero(np.max(np.abs(y), axis=1) > 1e-5)
            end = (nz[-1] + 1) if len(nz) else len(dry)
            res = (off, to_stereo(y[: max(end, len(dry))]))
        if t.throws and len(res[1]):
            res = (res[0], self._apply_throws(t, res[0], res[1]))
        self._render_cache[t.id] = (key, res[0], res[1])
        return res

    def _apply_throws(self, t: Track, off, y):
        """Delay / reverb throws and reverse-reverb swells on chosen words (non-destructive)."""
        sr = self.sr
        tail = int(4.0 * sr)
        out = np.vstack([y, np.zeros((tail, 2), np.float32)])
        for th in t.throws:
            a, b = int(th["start"]) - off, int(th["end"]) - off
            a, b = max(0, a), min(len(y), b)
            if b - a < 64:
                continue
            seg = dsp.apply_fade(y[a:b], int(0.005 * sr), int(0.02 * sr))
            seg = np.vstack([seg, np.zeros((tail, 2), np.float32)])
            g = float(dsp.db_to_lin(th.get("amount_db", 0.0)))
            kind = th.get("kind", "delay")
            if kind == "delay":
                wet = dsp.delay(seg, sr, None, self.bpm, th.get("division", "1/4"), th.get("feedback", 0.45),
                                1.0, True, 300, 7000, 10, wet_only=True)
                out[a:a + len(wet)] += wet * g
            elif kind == "reverb":
                wet = dsp.reverb(seg, sr, th.get("decay_s", 2.8), 20, 0.4, 0.9, 1.3, 1.0, 300, 9000, wet_only=True)
                out[a:a + len(wet)] += wet * g * 2.0
            elif kind == "reverse":
                rv = dsp.reverb(np.vstack([y[a:b][::-1], np.zeros((tail, 2), np.float32)]), sr, 2.2, 0, 0.4,
                                0.9, 1.2, 1.0, 300, 9000, wet_only=True)[::-1]
                swell = rv[: len(rv) - (b - a)] if len(rv) > (b - a) else rv
                s0 = a - len(swell)
                if s0 < 0:
                    swell = swell[-s0:]
                    s0 = 0
                out[s0:s0 + len(swell)] += swell * g * 2.0
        nz = np.flatnonzero(np.max(np.abs(out), axis=1) > 1e-5)
        end = max(len(y), (nz[-1] + 1) if len(nz) else 0)
        return out[:end].astype(np.float32)

    @staticmethod
    def automation_gain(points, start, n):
        """Volume envelope (dB points) as linear gain for samples [start, start+n)."""
        pts = sorted(points)
        if not pts:
            return None
        xs = np.array([p[0] for p in pts], float)
        ys = np.array([p[1] for p in pts], float)
        idx = np.arange(start, start + n)
        return dsp.db_to_lin(np.interp(idx, xs, ys)).astype(np.float32)

    def track_audio(self, t: Track, progress=None, cancel=None):
        """Final track audio = FX render + volume automation. -> (offset, stereo)"""
        off, a = self.render_track(t, progress, cancel)
        if not t.automation or not len(a):
            return off, a
        key = (self._render_cache.get(t.id, ("",))[0], json.dumps(sorted(t.automation)))
        hit = self._final_cache.get(t.id)
        if hit and hit[0] == key:
            return off, hit[1]
        g = self.automation_gain(t.automation, off, len(a))
        out = a * g[:, None]
        self._final_cache[t.id] = (key, out)
        return off, out

    # ------------------------------------------------------------- send buses
    def send_contributors(self, name):
        return [t for t in self.audible_tracks() if t.sends.get(name, -60.0) > -59.5 and t.clips]

    def _send_key(self, name):
        contrib = [(self._track_key(t), t.sends.get(name), json.dumps(sorted(t.automation)))
                   for t in self.send_contributors(name)]
        return hashlib.sha1(json.dumps([contrib, self.sends[name]["fx"], self.ctx()], sort_keys=True,
                                       default=str).encode()).hexdigest()

    def is_send_fresh(self, name):
        hit = self._send_cache.get(name)
        return bool(hit) and hit[0] == self._send_key(name)

    def render_send(self, name, progress=None, cancel=None):
        """Shared reverb / delay bus fed by every track's send level. -> (offset, stereo)"""
        key = self._send_key(name)
        hit = self._send_cache.get(name)
        if hit and hit[0] == key:
            return 0, hit[1]
        contrib = self.send_contributors(name)
        if not contrib:
            res = np.zeros((0, 2), np.float32)
        else:
            n = self.length + int(FX_TAIL_S * self.sr)
            bus = np.zeros((n, 2), np.float32)
            for t in contrib:
                off, a = self.track_audio(t, progress, cancel)
                seg = a[: max(0, n - off)] * float(dsp.db_to_lin(t.sends[name]))
                bus[off: off + len(seg)] += seg
            res = process_chain(bus, self.sr, self.sends[name]["fx"], self.ctx(), None, cancel)
        self._send_cache[name] = (key, res)
        return 0, res

    def send_strips(self):
        return [dict(id=f"__send_{n}__", name=f"{n.title()} bus", volume_db=self.sends[n]["volume_db"],
                     mute=self.sends[n]["mute"], send=n) for n in SEND_NAMES]

    def is_render_fresh(self, t: Track) -> bool:
        hit = self._render_cache.get(t.id)
        if not hit:
            return False
        needs_sc = any(e.get("enabled") and REGISTRY.get(e["id"]) and REGISTRY[e["id"]].needs_sidechain
                       for e in t.fx) and not t.fx_bypass
        extra = "|".join(self._track_key(v) for v in self.vocal_tracks() if not v.mute) if needs_sc else ""
        return hit[0] == self._track_key(t, extra)

    def invalidate(self, t: Track | None = None):
        if t is None:
            self._render_cache.clear()
            self._final_cache.clear()
            self._send_cache.clear()
        else:
            self._render_cache.pop(t.id, None)
            self._final_cache.pop(t.id, None)

    def audible_tracks(self):
        mix = self.mix_tracks()
        solo = any(t.solo for t in mix)
        return [t for t in mix if (t.solo if solo else not t.mute)]

    def mixdown(self, start=0, end=None, master=True, progress=None, cancel=None, tracks=None):
        end = end or (self.length + int(FX_TAIL_S * self.sr))
        n = max(0, end - start)
        mix = np.zeros((n, 2), np.float32)
        items = []
        for t in tracks if tracks is not None else self.audible_tracks():
            off, a = self.track_audio(t, progress, cancel)
            items.append((off, a, t.volume_db, t.pan))
        if tracks is None:
            for st in self.send_strips():
                if not st["mute"]:
                    off, a = self.render_send(st["send"], progress, cancel)
                    items.append((off, a, st["volume_db"], 0.0))
        for off, a, vol, pan in items:
            gl, gr = dsp.pan_gains(pan)
            g = dsp.db_to_lin(vol)
            s0, s1 = max(off, start), min(off + len(a), end)
            if s1 <= s0:
                continue
            seg = a[s0 - off: s1 - off]
            mix[s0 - start: s1 - start, 0] += seg[:, 0] * g * gl
            mix[s0 - start: s1 - start, 1] += seg[:, 1] * g * gr
        mix *= dsp.db_to_lin(self.master_volume_db)
        if master and self.master_fx:
            mix = process_chain(mix, self.sr, self.master_fx, self.ctx(),
                                (lambda f, nm: progress("Master", f, nm)) if progress else None, cancel)
        return mix

    # ------------------------------------------------------------- persistence
    def to_dict(self):
        return dict(
            app="SIB Studio Mixer", version=1, name=self.name, sr=self.sr,
            tracks=[t.to_dict() for t in self.tracks],
            markers=[asdict(m) for m in self.markers], master_fx=self.master_fx,
            master_volume_db=self.master_volume_db, bpm=self.bpm, beat_offset_s=self.beat_offset_s,
            time_sig=self.time_sig, key_root=self.key_root, key_scale=self.key_scale, a4=self.a4,
            analysis=self.analysis, loop=self.loop, files=self.pool.files,
            lyrics=self.lyrics, sends=self.sends,
        )

    def load_state(self, d):
        self.name = d.get("name", self.name)
        self.tracks = [Track.from_dict(t) for t in d.get("tracks", [])]
        self.markers = [Marker(**m) for m in d.get("markers", [])]
        self.master_fx = [sanitize_instance(e) for e in d.get("master_fx", [])]
        for k in ("master_volume_db", "bpm", "beat_offset_s", "time_sig", "key_root", "key_scale", "a4"):
            if k in d:
                setattr(self, k, d[k])
        self.analysis = d.get("analysis", {})
        self.lyrics = {**default_lyrics(), **d.get("lyrics", {})}
        sends = default_sends()
        for n, v in (d.get("sends") or {}).items():
            if n in sends:
                sends[n].update({k: v[k] for k in ("volume_db", "mute") if k in v})
                if v.get("fx") is not None:
                    sends[n]["fx"] = [sanitize_instance(e) for e in v["fx"]]
        self.sends = sends
        lp = d.get("loop")
        self.loop = tuple(lp) if lp else None
        self.pool.files.update(d.get("files", {}))

    def save(self, path=None):
        path = path or self.saved_path or os.path.join(self.folder, f"{self.name}{PROJECT_EXT}")
        new_folder = os.path.dirname(os.path.abspath(path))
        if os.path.abspath(new_folder) != os.path.abspath(self.folder):
            os.makedirs(os.path.join(new_folder, "Audio"), exist_ok=True)
            for fid, info in self.pool.files.items():
                src = os.path.join(self.folder, info["path"])
                dst = os.path.join(new_folder, info["path"])
                if os.path.exists(src) and not os.path.exists(dst):
                    shutil.copy2(src, dst)
            self.folder = new_folder
        self.name = os.path.splitext(os.path.basename(path))[0]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=1, default=_json_default)
        os.replace(tmp, path)
        self.saved_path = path
        self.dirty = False
        return path

    @staticmethod
    def open(path) -> "Project":
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        p = Project(d.get("name", "Project"), d.get("sr", 44100), folder=os.path.dirname(os.path.abspath(path)))
        p.load_state(d)
        p.saved_path = path
        p.dirty = False
        return p


# ============================================================================ undo
class UndoStack:
    def __init__(self, project: Project, limit=60):
        self.project = project
        self.undo: list = []
        self.redo: list = []
        self.limit = limit

    def _snap(self):
        d = self.project.to_dict()
        d.pop("files", None)
        return json.loads(json.dumps(d, default=_json_default))

    def push(self, label="Edit"):
        self.undo.append((label, self._snap()))
        if len(self.undo) > self.limit:
            self.undo.pop(0)
        self.redo.clear()
        self.project.dirty = True

    def _restore(self, state):
        files = self.project.pool.files
        lyrics = self.project.lyrics          # lyrics are edited live; undo never rewinds your writing
        self.project.load_state(state)
        self.project.pool.files = files
        self.project.lyrics = lyrics
        self.project.invalidate()

    def do_undo(self):
        if not self.undo:
            return None
        label, st = self.undo.pop()
        self.redo.append((label, self._snap()))
        self._restore(st)
        return label

    def do_redo(self):
        if not self.redo:
            return None
        label, st = self.redo.pop()
        self.undo.append((label, self._snap()))
        self._restore(st)
        return label

    def can_undo(self):
        return bool(self.undo)

    def can_redo(self):
        return bool(self.redo)


# ============================================================================ editing ops
def split_clip(project: Project, track: Track, clip: Clip, pos: int):
    if not (clip.start < pos < clip.end):
        return None
    cut = pos - clip.start
    right = Clip(clip.file_id, pos, clip.length - cut, clip.src_in + cut, clip.gain_db, 0, clip.fade_out,
                 clip.muted, clip.name, take=clip.take)
    clip.length = cut
    clip.fade_out = 0
    clip.fade_in = min(clip.fade_in, clip.length)
    track.clips.append(right)
    return right


def split_at(project, tracks, pos):
    out = []
    for t in tracks:
        for c in list(t.clips):
            r = split_clip(project, t, c, pos)
            if r:
                out.append(r)
    return out


def delete_range(project: Project, tracks, start, end, ripple=False):
    for t in tracks:
        for c in list(t.clips):
            split_clip(project, t, c, start)
        for c in list(t.clips):
            split_clip(project, t, c, end)
        t.clips = [c for c in t.clips if not (c.start >= start and c.end <= end)]
        if ripple:
            for c in t.clips:
                if c.start >= end:
                    c.start -= (end - start)


def copy_range(project: Project, track: Track, start, end):
    """Returns clipboard data: list of (relative_start, Clip-copy)."""
    out = []
    for c in track.clips:
        s0, s1 = max(c.start, start), min(c.end, end)
        if s1 <= s0:
            continue
        nc = Clip(c.file_id, s0 - start, s1 - s0, c.src_in + (s0 - c.start), c.gain_db, 0, 0, c.muted, c.name, take=c.take)
        out.append(nc)
    return out


def paste(project: Project, track: Track, clips, pos, overwrite=True):
    if not clips:
        return
    span = max(c.start + c.length for c in clips)
    if overwrite:
        delete_range(project, [track], pos, pos + span)
    for c in clips:
        nc = copy.deepcopy(c)
        nc.id = uid()
        nc.start = pos + c.start
        track.clips.append(nc)


def trim_to_range(project, tracks, start, end):
    for t in tracks:
        delete_range(project, [t], 0, start)
        delete_range(project, [t], end, max(end, t.end) + 1)


def process_range(project: Project, track: Track, start, end, fn, label="Process", xfade_ms=5.0, pass_pos=False):
    """Destructively apply fn(audio)->audio to every clip region inside [start, end).

    A new source file is written per affected clip, so the original stays for undo.
    """
    sr = project.sr
    xf = int(xfade_ms / 1000 * sr)
    changed = 0
    for c in list(track.clips):
        s0, s1 = max(c.start, start), min(c.end, end)
        if s1 <= s0:
            continue
        src = project.pool.get(c.file_id)
        a = c.src_in + (s0 - c.start)
        b = c.src_in + (s1 - c.start)
        new_src = src.copy()
        seg = src[a:b]
        out = dsp.as2d(fn(seg, s0) if pass_pos else fn(seg))
        if out.shape[1] != new_src.shape[1]:
            if new_src.shape[1] == 1:
                new_src = np.repeat(new_src, 2, axis=1)
                seg = np.repeat(seg, 2, axis=1)
            else:
                out = np.repeat(out, 2, axis=1) if out.shape[1] == 1 else out[:, :new_src.shape[1]]
        out = out[: len(seg)] if len(out) >= len(seg) else np.vstack(
            [out, np.zeros((len(seg) - len(out), out.shape[1]), np.float32)])
        # crossfade the edges so edits never click (only where the range is inside the clip)
        f = min(xf, len(seg) // 4)
        if f > 0:
            ramp = np.linspace(0, 1, f, dtype=np.float32)[:, None]
            if a > c.src_in:
                out[:f] = seg[:f] * (1 - ramp) + out[:f] * ramp
            if b < c.src_in + c.length:
                out[-f:] = out[-f:] * (1 - ramp) + seg[-f:] * ramp
        new_src[a:b] = out
        c.file_id = project.pool.add_array(new_src, f"{c.name}_{label}")
        changed += 1
    project.invalidate(track)
    return changed


def bounce_track(project: Project, track: Track, name=None):
    """Print the track's FX into a new audio clip (like Audition's Bounce to New Track)."""
    off, a = project.track_audio(track)
    t = project.add_track(name or f"{track.name} (bounced)", "vocal", track.role, style="Raw (no effects)",
                          index=project.tracks.index(track) + 1)
    t.volume_db, t.pan = track.volume_db, track.pan
    if len(a):
        project.add_clip_from_array(t, a, off, t.name)
    return t
