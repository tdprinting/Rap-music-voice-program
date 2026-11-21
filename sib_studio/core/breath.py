"""Breath detection and control.

Breaths are found as un-pitched, noisy, mid-level events between phrases. Each detected
breath is returned as a region the user can review; the reduction itself is applied with
smooth fades so the timing/pocket of the verse never changes.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter1d, binary_closing

from .audio_io import to_mono, resample
from . import dsp
from .pitch import detect_pitch, PitchTrack


@dataclass
class BreathRegion:
    start: int   # samples (relative to the audio passed in)
    end: int
    score: float
    level_db: float
    enabled: bool = True

    def to_dict(self):
        return asdict(self)


def detect_breaths(x, sr, sensitivity=0.5, min_ms=120, max_ms=1200,
                   track: PitchTrack | None = None) -> list[BreathRegion]:
    """sensitivity 0..1 (higher finds more)."""
    mono = to_mono(x)
    tsr = 16000
    y = resample(mono, sr, tsr)[:, 0]
    hop = 160  # 10 ms
    n_fft = 512
    f, t, Z = signal.stft(y, fs=tsr, nperseg=n_fft, noverlap=n_fft - hop, boundary=None, padded=False)
    mag = np.abs(Z) + 1e-10
    pw = mag ** 2
    nfr = mag.shape[1]
    frame_t = (np.arange(nfr) * hop + n_fft / 2) / tsr
    rms = 10 * np.log10(pw.mean(axis=0) + 1e-20)
    flat = np.exp(np.mean(np.log(mag), axis=0)) / np.mean(mag, axis=0)
    centroid = (f[:, None] * pw).sum(axis=0) / pw.sum(axis=0)
    low = pw[f < 300].sum(axis=0)
    mid = pw[(f >= 600) & (f < 4000)].sum(axis=0)
    high = pw[f >= 4500].sum(axis=0)
    tot = pw.sum(axis=0)
    track = track or detect_pitch(x, sr)
    voiced = np.interp(frame_t, track.times, (track.f0 > 0).astype(float)) > 0.3
    # dilate voicing a little: breaths are never in the middle of a pitched syllable
    voiced = uniform_filter1d(voiced.astype(float), size=5) > 0.01
    active = rms[rms > np.percentile(rms, 20)] if nfr > 10 else rms
    speech_level = np.percentile(active, 90) if len(active) else -20
    floor = np.percentile(rms, 10)
    rel = rms - speech_level
    # score components
    s_level = np.clip(1 - np.abs(rel + 22) / 16, 0, 1)        # breaths sit ~10-35 dB under speech
    s_above_floor = np.clip((rms - floor - 4) / 8, 0, 1)
    s_flat = np.clip((flat - 0.12) / 0.3, 0, 1)
    s_centroid = np.clip(1 - np.abs(centroid - 2200) / 2600, 0, 1)
    s_spread = np.clip(mid / (tot + 1e-20) * 1.6, 0, 1)
    not_sib = np.clip(1 - (high / (tot + 1e-20) - 0.45) / 0.3, 0, 1)  # 's' sounds are hissier
    not_plosive = np.clip(1 - low / (tot + 1e-20) * 1.5, 0, 1)
    score = (0.30 * s_level + 0.15 * s_above_floor + 0.2 * s_flat + 0.15 * s_centroid
             + 0.1 * s_spread + 0.1 * not_plosive) * not_sib
    score[voiced] = 0
    score = uniform_filter1d(score, size=5)
    thr = 0.62 - 0.3 * float(np.clip(sensitivity, 0, 1))
    cand = binary_closing(score > thr, structure=np.ones(4))
    regions = []
    edges = np.flatnonzero(np.diff(np.concatenate([[0], cand.astype(int), [0]])))
    scale = sr / tsr
    for a, b in zip(edges[::2], edges[1::2]):
        dur = (b - a) * hop / tsr * 1000
        if dur < min_ms or dur > max_ms:
            continue
        s0 = int(a * hop * scale)
        s1 = int(min(len(mono), (b * hop + n_fft) * scale))
        seg = mono[s0:s1]
        lvl = float(dsp.lin_to_db(np.sqrt(np.mean(seg ** 2) + 1e-20)))
        regions.append(BreathRegion(s0, s1, float(score[a:b].mean()), lvl))
    return regions


def apply_breath_control(x, sr, regions, mode="reduce", reduce_db=-12.0, target_db=-45.0,
                         fade_ms=15.0):
    """mode: 'reduce' (by reduce_db), 'target' (bring each breath to target_db RMS),
    'silence' (mute). Timing never changes."""
    x = dsp.as2d(x).copy()
    n = len(x)
    gain = np.ones(n, np.float32)
    fade = max(1, int(fade_ms / 1000 * sr))
    for r in regions:
        if not getattr(r, "enabled", True):
            continue
        a, b = max(0, int(r.start)), min(n, int(r.end))
        if b <= a:
            continue
        if mode == "silence":
            g = 0.0
        elif mode == "target":
            lvl = dsp.lin_to_db(np.sqrt(np.mean(x[a:b] ** 2) + 1e-20))
            g = float(min(1.0, dsp.db_to_lin(target_db - lvl)))
        else:
            g = float(dsp.db_to_lin(reduce_db))
        seg = np.full(b - a, g, np.float32)
        f = min(fade, (b - a) // 2)
        if f > 0:
            ramp = np.linspace(1.0, g, f, dtype=np.float32)
            seg[:f] = ramp
            seg[-f:] = ramp[::-1]
        gain[a:b] = np.minimum(gain[a:b], seg)
    return (x * gain[:, None]).astype(np.float32)


def remove_breaths(x, sr, sensitivity=0.5, mode="reduce", reduce_db=-12.0, target_db=-45.0,
                   fade_ms=15.0, track=None):
    regions = detect_breaths(x, sr, sensitivity, track=track)
    return apply_breath_control(x, sr, regions, mode, reduce_db, target_db, fade_ms), regions
