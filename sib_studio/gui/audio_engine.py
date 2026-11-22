"""Real-time playback and recording (sounddevice / PortAudio).

Tracks are pre-rendered with their effects (in a worker thread); the audio callback only mixes
buffers with live volume/pan/mute/solo, a metronome and optional input monitoring, so playback
is glitch-free even with heavy processing like auto-tune.
"""
from __future__ import annotations

import threading
import numpy as np

try:
    import sounddevice as sd  # type: ignore
except Exception:  # pragma: no cover
    sd = None

from ..core import dsp


def list_devices():
    """-> (inputs, outputs) lists of (index, label, max_channels, default_sr)."""
    if sd is None:
        return [], []
    ins, outs = [], []
    try:
        apis = sd.query_hostapis()
        for i, d in enumerate(sd.query_devices()):
            api = apis[d["hostapi"]]["name"] if d["hostapi"] < len(apis) else ""
            label = f"{d['name']}  [{api}]"
            if d["max_input_channels"] > 0:
                ins.append((i, label, d["max_input_channels"], d["default_samplerate"]))
            if d["max_output_channels"] > 0:
                outs.append((i, label, d["max_output_channels"], d["default_samplerate"]))
    except Exception:
        pass
    return ins, outs


def default_devices():
    if sd is None:
        return None, None
    try:
        d = sd.default.device
        return (d[0] if d[0] is not None and d[0] >= 0 else None,
                d[1] if d[1] is not None and d[1] >= 0 else None)
    except Exception:
        return None, None


def make_click(sr, accent=False):
    n = int(0.03 * sr)
    t = np.arange(n) / sr
    f = 1760.0 if accent else 1175.0
    return (np.sin(2 * np.pi * f * t) * np.exp(-t * 120) * (0.5 if accent else 0.35)).astype(np.float32)


class AudioEngine:
    def __init__(self):
        self.sr = 44100
        self.blocksize = 512
        self.in_device = None
        self.out_device = None
        self.input_channel = 0
        self.latency_comp_ms = 0.0
        self.stream = None
        self._streams = []
        self.playing = False
        self.recording = False
        self.pos = 0                       # current playhead (samples), may be negative in count-in
        self.loop = None                   # (start, end)
        self.loop_enabled = False
        self.metronome = False
        self.metronome_rec_only = True
        self.bpm = 90.0
        self.beat_offset = 0
        self.beats_per_bar = 4
        self.monitor = False
        self.monitor_gain = 1.0
        self.master_gain = 1.0
        self.tracks = []                   # list of dict(off, data, gl, gr, on)
        self.master_buffer = None          # pre-rendered master (when previewing master FX)
        self.peak_out = np.zeros(2)
        self.peak_in = 0.0
        self.rec_blocks = []
        self.rec_start = 0
        self.rec_takes = []                # finished takes [(start, data)] during loop recording
        self.error = None
        self._lock = threading.Lock()
        self._click_hi = make_click(self.sr, True)
        self._click_lo = make_click(self.sr, False)
        self._click_play = None            # (array, index)
        self.stop_at = None
        self.rec_end = None                # punch-out point (samples) or None
        self._ring = np.zeros((int(4 * self.sr), 2), np.float32)
        self._ring_pos = 0

    @property
    def available(self):
        return sd is not None

    # ------------------------------------------------------------ configuration
    def configure(self, sr=None, blocksize=None, in_device=None, out_device=None, input_channel=None,
                  latency_comp_ms=None):
        if sr:
            self.sr = int(sr)
            self._ring = np.zeros((int(4 * self.sr), 2), np.float32)
            self._ring_pos = 0
            self._click_hi = make_click(self.sr, True)
            self._click_lo = make_click(self.sr, False)
        if blocksize:
            self.blocksize = int(blocksize)
        if in_device is not None:
            self.in_device = in_device if in_device >= 0 else None
        if out_device is not None:
            self.out_device = out_device if out_device >= 0 else None
        if input_channel is not None:
            self.input_channel = int(input_channel)
        if latency_comp_ms is not None:
            self.latency_comp_ms = float(latency_comp_ms)

    def _ring_write(self, out):
        n = len(out)
        r = self._ring
        if n >= len(r):
            r[:] = out[-len(r):]
            self._ring_pos = 0
            return
        a = self._ring_pos
        b = a + n
        if b <= len(r):
            r[a:b] = out
        else:
            k = len(r) - a
            r[a:] = out[:k]
            r[: n - k] = out[k:]
        self._ring_pos = b % len(r)

    def recent_output(self, n):
        """Last n samples sent to the speakers (for the analyzer)."""
        r = self._ring
        n = min(n, len(r))
        a = (self._ring_pos - n) % len(r)
        if a + n <= len(r):
            return r[a:a + n].copy()
        return np.vstack([r[a:], r[: (a + n) % len(r)]])

    def set_tracks(self, tracks):
        """tracks: list of dict(id, off, data(n,2), volume_db, pan, audible)."""
        prepared = []
        for t in tracks:
            gl, gr = dsp.pan_gains(t["pan"])
            g = float(dsp.db_to_lin(t["volume_db"]))
            prepared.append(dict(id=t["id"], off=int(t["off"]), data=t["data"], gl=gl * g, gr=gr * g,
                                 on=bool(t["audible"])))
        with self._lock:
            self.tracks = prepared

    def update_track_params(self, tid, volume_db, pan, audible):
        gl, gr = dsp.pan_gains(pan)
        g = float(dsp.db_to_lin(volume_db))
        for t in self.tracks:
            if t["id"] == tid:
                t["gl"], t["gr"], t["on"] = gl * g, gr * g, bool(audible)

    def replace_track_data(self, tid, off, data):
        with self._lock:
            for t in self.tracks:
                if t["id"] == tid:
                    t["off"], t["data"] = int(off), data

    # ------------------------------------------------------------ transport
    def _make(self, cls, **kw):
        """Open a stream; if the device runs at another sample rate (WASAPI shared mode),
        retry with automatic sample-rate conversion."""
        try:
            return cls(**kw)
        except Exception as first:
            try:
                ws = sd.WasapiSettings(auto_convert=True)
            except Exception:
                raise first
            try:
                return cls(extra_settings=ws, **kw)
            except Exception:
                try:
                    return cls(extra_settings=(ws, ws), **kw)
                except Exception:
                    raise first

    def _open(self, need_input):
        if sd is None:
            raise RuntimeError("The 'sounddevice' package is not installed (pip install sounddevice).")
        self.close()
        self.error = None
        if need_input:
            in_info = sd.query_devices(self.in_device if self.in_device is not None else sd.default.device[0])
            in_ch = max(1, min(int(in_info["max_input_channels"]), self.input_channel + 1))
            try:
                self.stream = self._make(sd.Stream, samplerate=self.sr, blocksize=self.blocksize,
                                        device=(self.in_device, self.out_device), channels=(in_ch, 2),
                                        dtype="float32", latency="low", callback=self._duplex_cb)
                self.stream.start()
                self._streams = [self.stream]
                return
            except Exception:
                # devices on different drivers: use two streams
                self._in_ch = in_ch
                ins = self._make(sd.InputStream, samplerate=self.sr, blocksize=self.blocksize, device=self.in_device,
                                     channels=in_ch, dtype="float32", latency="low", callback=self._in_cb)
                outs = self._make(sd.OutputStream, samplerate=self.sr, blocksize=self.blocksize, device=self.out_device,
                                       channels=2, dtype="float32", latency="low", callback=self._out_cb)
                self._streams = [ins, outs]
                self.stream = outs
                ins.start()
                outs.start()
                return
        self.stream = self._make(sd.OutputStream, samplerate=self.sr, blocksize=self.blocksize, device=self.out_device,
                                      channels=2, dtype="float32", latency="low", callback=self._out_cb)
        self.stream.start()
        self._streams = [self.stream]

    def close(self):
        for s in self._streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self._streams = []
        self.stream = None

    def stream_latency_samples(self):
        lat = 0.0
        try:
            if self.stream is not None:
                l = self.stream.latency
                lat = sum(l) if isinstance(l, (tuple, list)) else float(l) * 2
        except Exception:
            pass
        return int((lat + self.latency_comp_ms / 1000.0) * self.sr)

    def play(self, pos):
        self.pos = int(pos)
        self.recording = False
        self._open(need_input=self.monitor)
        self.playing = True

    def record(self, pos, count_in_bars=0, punch_out=None, preroll=None):
        """Record from pos. punch_out = stop capturing there (auto punch). preroll = samples of
        playback before pos (used instead of a count-in for punch-ins)."""
        self.rec_blocks = []
        self.rec_takes = []
        self.rec_start = int(pos)
        self.rec_end = int(punch_out) if punch_out else None
        pre = int(preroll) if preroll is not None else \
            int(count_in_bars * self.beats_per_bar * 60.0 / max(self.bpm, 1) * self.sr)
        self.pos = int(pos) - pre
        self._open(need_input=True)
        self.recording = True
        self.playing = True

    def stop(self):
        self.playing = False
        was_rec = self.recording
        self.recording = False
        lat = self.stream_latency_samples()   # read before the stream is closed
        self.close()
        takes = []
        if was_rec:
            takes = list(self.rec_takes)
            if self.rec_blocks:
                takes.append((self.rec_start, np.concatenate(self.rec_blocks)))
            self.rec_blocks = []
            self.rec_takes = []
            takes = [(max(0, s), d[lat:] if lat < len(d) else d) for s, d in takes]
        return takes

    # ------------------------------------------------------------ callbacks
    def _mix_block(self, frames):
        out = np.zeros((frames, 2), np.float32)
        p0 = self.pos
        p1 = p0 + frames
        if self.master_buffer is not None:
            mb = self.master_buffer
            a, b = max(p0, 0), min(p1, len(mb))
            if b > a:
                out[a - p0: b - p0] += mb[a:b]
        else:
            for t in self.tracks:
                if not t["on"]:
                    continue
                d = t["data"]
                off = t["off"]
                a, b = max(p0, off), min(p1, off + len(d))
                if b <= a:
                    continue
                seg = d[a - off: b - off]
                out[a - p0: b - p0, 0] += seg[:, 0] * t["gl"]
                out[a - p0: b - p0, 1] += seg[:, 1] * t["gr"]
        out *= self.master_gain
        counting_in = self.recording and p0 < self.rec_start
        if counting_in or (self.metronome and (self.recording or not self.metronome_rec_only)):
            self._add_clicks(out, p0, frames)
        return out

    def _add_clicks(self, out, p0, frames):
        spb = self.sr * 60.0 / max(self.bpm, 1)
        if self._click_play is not None:
            arr, i = self._click_play
            n = min(frames, len(arr) - i)
            out[:n] += arr[i:i + n, None]
            self._click_play = (arr, i + n) if i + n < len(arr) else None
        k0 = int(np.ceil((p0 - self.beat_offset) / spb))
        while True:
            bp = int(round(k0 * spb + self.beat_offset))
            if bp >= p0 + frames:
                break
            if bp >= p0:
                arr = self._click_hi if k0 % self.beats_per_bar == 0 else self._click_lo
                i = bp - p0
                n = min(frames - i, len(arr))
                out[i:i + n] += arr[:n, None]
                if n < len(arr):
                    self._click_play = (arr, n)
            k0 += 1

    def _advance(self, frames, inp=None):
        if self.recording and inp is not None:
            ch = min(self.input_channel, inp.shape[1] - 1)
            mono = inp[:, ch].copy()
            p0 = self.pos
            end = self.rec_end if self.rec_end is not None else p0 + frames + 1
            if p0 + frames > self.rec_start and p0 < end:
                cut = max(0, self.rec_start - p0)
                stop = min(frames, end - p0)
                if stop > cut:
                    self.rec_blocks.append(mono[cut:stop])
        self.pos += frames
        if self.loop_enabled and self.loop and self.pos >= self.loop[1] and self.loop[1] > self.loop[0]:
            if self.recording:
                if self.rec_blocks:
                    self.rec_takes.append((self.rec_start, np.concatenate(self.rec_blocks)))
                self.rec_blocks = []
                self.rec_start = self.loop[0]
            self.pos = self.loop[0]
        if self.stop_at is not None and self.pos >= self.stop_at:
            self.playing = False

    def _out_cb(self, outdata, frames, time_info, status):
        if not self.playing:
            outdata[:] = 0
            return
        with self._lock:
            out = self._mix_block(frames)
        if self.monitor and getattr(self, "_last_in", None) is not None and len(self._last_in) == frames:
            out += self._last_in[:, None] * self.monitor_gain
        np.clip(out, -1, 1, out=out)
        outdata[:] = out
        self.peak_out = np.max(np.abs(out), axis=0)
        self._ring_write(out)
        self._advance(frames, None)

    def _in_cb(self, indata, frames, time_info, status):
        ch = min(self.input_channel, indata.shape[1] - 1)
        self._last_in = indata[:, ch].copy()
        self.peak_in = float(np.max(np.abs(indata[:, ch]))) if frames else 0.0
        if self.recording and self.playing:
            p0 = self.pos
            end = self.rec_end if self.rec_end is not None else p0 + frames + 1
            if p0 + frames > self.rec_start and p0 < end:
                cut = max(0, self.rec_start - p0)
                stop = min(frames, end - p0)
                if stop > cut:
                    self.rec_blocks.append(indata[cut:stop, ch].copy())

    def _duplex_cb(self, indata, outdata, frames, time_info, status):
        ch = min(self.input_channel, indata.shape[1] - 1)
        self.peak_in = float(np.max(np.abs(indata[:, ch]))) if frames else 0.0
        if not self.playing:
            outdata[:] = 0
            return
        with self._lock:
            out = self._mix_block(frames)
        if self.monitor:
            out += indata[:, ch:ch + 1] * self.monitor_gain
        np.clip(out, -1, 1, out=out)
        outdata[:] = out
        self.peak_out = np.max(np.abs(out), axis=0)
        self._ring_write(out)
        self._advance(frames, indata)

    # ------------------------------------------------------------ input meter only
    def start_input_meter(self):
        if sd is None or self.playing:
            return
        try:
            self.close()
            in_info = sd.query_devices(self.in_device if self.in_device is not None else sd.default.device[0])
            in_ch = max(1, min(int(in_info["max_input_channels"]), self.input_channel + 1))
            s = self._make(sd.InputStream, samplerate=self.sr, blocksize=self.blocksize, device=self.in_device,
                               channels=in_ch, dtype="float32", callback=self._in_cb)
            s.start()
            self._streams = [s]
        except Exception as e:
            self.error = str(e)

    def stop_input_meter(self):
        if not self.playing:
            self.close()
