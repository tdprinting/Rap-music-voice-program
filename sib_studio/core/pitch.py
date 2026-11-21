"""Pitch tracking (YIN) and SIB Tune pitch correction (TD-PSOLA, formant preserving)."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy import signal
from scipy.ndimage import median_filter

from .audio_io import to_mono, resample
from .analysis import SCALES, NOTE_NAMES
from . import dsp

DET_SR = 16000


@dataclass
class PitchTrack:
    times: np.ndarray      # seconds (frame centres)
    f0: np.ndarray         # Hz, 0 = unvoiced
    confidence: np.ndarray  # 0..1
    hop_s: float

    @property
    def voiced(self):
        return self.f0 > 0

    def midi(self, a4=440.0):
        m = np.full(len(self.f0), np.nan)
        v = self.voiced
        m[v] = 69 + 12 * np.log2(self.f0[v] / a4)
        return m


def detect_pitch(x, sr, fmin=60.0, fmax=900.0, hop_s=0.005, threshold=0.15,
                 silence_db=-50.0) -> PitchTrack:
    mono = to_mono(x)
    y = resample(mono, sr, DET_SR)[:, 0].astype(np.float64)
    y = signal.sosfilt(signal.butter(2, [fmin * 0.8, 2000], "bandpass", fs=DET_SR, output="sos"), y)
    tau_max = int(DET_SR / fmin) + 2
    tau_min = max(2, int(DET_SR / fmax))
    W = 512
    N = W + tau_max
    hop = max(1, int(hop_s * DET_SR))
    pad = np.pad(y, (N // 2, N))
    n_frames = max(1, (len(y)) // hop)
    f0 = np.zeros(n_frames)
    conf = np.zeros(n_frames)
    nfft = 1
    while nfft < 2 * N:
        nfft *= 2
    chunk = 2048
    gate = 10 ** (silence_db / 20)
    for c0 in range(0, n_frames, chunk):
        idx = np.arange(c0, min(n_frames, c0 + chunk))
        starts = idx * hop
        fr = np.stack([pad[s:s + N] for s in starts])
        rms = np.sqrt(np.mean(fr[:, :W] ** 2, axis=1))
        A = np.fft.rfft(fr[:, :W], nfft, axis=1)
        B = np.fft.rfft(fr, nfft, axis=1)
        corr = np.fft.irfft(np.conj(A) * B, nfft, axis=1)[:, :tau_max]
        cs = np.cumsum(np.pad(fr ** 2, ((0, 0), (1, 0))), axis=1)
        e0 = cs[:, W] - cs[:, 0]
        taus = np.arange(tau_max)
        et = cs[:, taus + W] - cs[:, taus]
        d = e0[:, None] + et - 2 * corr
        d[:, 0] = 0
        cm = np.cumsum(d[:, 1:], axis=1)
        dn = np.ones_like(d)
        dn[:, 1:] = d[:, 1:] * taus[1:] / np.maximum(cm, 1e-12)
        for r, fi in enumerate(idx):
            if rms[r] < gate:
                continue
            row = dn[r]
            below = np.flatnonzero(row[tau_min:] < threshold)
            if len(below):
                t = below[0] + tau_min
                while t + 1 < tau_max and row[t + 1] < row[t]:
                    t += 1
            else:
                t = int(np.argmin(row[tau_min:])) + tau_min
                if row[t] > 0.35:
                    continue
            if 1 <= t < tau_max - 1:
                a, b, c = row[t - 1], row[t], row[t + 1]
                den = a - 2 * b + c
                dt = 0.5 * (a - c) / den if abs(den) > 1e-12 else 0.0
            else:
                dt = 0.0
            tau = t + float(np.clip(dt, -1, 1))
            f0[fi] = DET_SR / tau
            conf[fi] = float(np.clip(1 - row[t], 0, 1))
    # clean-up: median filter, octave-jump repair, drop tiny voiced islands
    v = f0 > 0
    if v.any():
        lm = np.where(v, np.log2(np.maximum(f0, 1)), 0)
        med = median_filter(lm, size=7)
        jump = v & (np.abs(lm - med) > 0.6) & (med > 0)
        lm[jump] = med[jump]
        f0 = np.where(v, 2 ** lm, 0)
    min_len = int(0.03 / hop_s)
    v = f0 > 0
    edges = np.flatnonzero(np.diff(np.concatenate([[0], v.astype(int), [0]])))
    for a, b in zip(edges[::2], edges[1::2]):
        if b - a < min_len:
            f0[a:b] = 0
            conf[a:b] = 0
    times = (np.arange(n_frames) * hop) / DET_SR
    return PitchTrack(times, f0, conf, hop / DET_SR)


# --------------------------------------------------------------------------- correction curve
def _allowed_mask(root, scale):
    pcs = SCALES.get(scale, SCALES["Chromatic"])
    m = np.zeros(12, bool)
    for p in pcs:
        m[(root + p) % 12] = True
    return m


def _nearest_allowed(midi, allowed, bias=0.0):
    best = np.round(midi)
    best_d = np.full(len(midi), np.inf)
    base = np.round(midi)
    for off in range(-6, 7):
        cand = base + off
        ok = allowed[np.mod(cand, 12).astype(int)]
        d = np.abs(midi + bias - cand)
        upd = ok & (d < best_d)
        best[upd] = cand[upd]
        best_d[upd] = d[upd]
    return best


def _shift_degrees(notes, degrees, allowed):
    if degrees == 0:
        return notes
    out = notes.copy()
    step = 1 if degrees > 0 else -1
    for i in range(len(out)):
        n = out[i]
        k = 0
        while k < abs(degrees):
            n += step
            if allowed[int(n) % 12]:
                k += 1
        out[i] = n
    return out


@dataclass
class TuneResult:
    times: np.ndarray
    midi_in: np.ndarray
    midi_out: np.ndarray
    target: np.ndarray
    shift_semitones: np.ndarray


def correction_curve(track: PitchTrack, root=0, scale="Minor", a4=440.0, retune_ms=20.0,
                     humanize=0.0, flex_cents=0.0, amount=1.0, transpose=0.0,
                     hysteresis_cents=20.0, degrees=0, note_bias_cents=0.0,
                     frozen_notes=None) -> TuneResult:
    midi = track.midi(a4)
    v = ~np.isnan(midi)
    n = len(midi)
    allowed = _allowed_mask(root, scale)
    target = np.full(n, np.nan)
    if v.any():
        target[v] = _nearest_allowed(midi[v], allowed, note_bias_cents / 100.0)
    # hysteresis (avoid flipping between two notes on a wobble)
    hyst = 0.5 + hysteresis_cents / 100.0
    prev = np.nan
    for i in range(n):
        if not v[i]:
            prev = np.nan
            continue
        if not np.isnan(prev) and abs(midi[i] - prev) < hyst:
            target[i] = prev
        prev = target[i]
    if frozen_notes is not None:  # manual note overrides from the pitch editor
        fn = np.asarray(frozen_notes, float)
        m = v & ~np.isnan(fn[:n])
        target[m] = fn[:n][m]
    if degrees:
        target[v] = _shift_degrees(target[v], degrees, allowed)
    desired = np.where(v, target - midi, 0.0)
    if flex_cents > 0 and degrees == 0:
        dev = np.abs(desired) * 100.0
        desired = desired * np.clip(dev / flex_cents, 0, 1) ** 1.5
    corr = np.zeros(n)
    hop = track.hop_s
    held = 0.0
    c = 0.0
    last_t = np.nan
    for i in range(n):
        if not v[i]:
            held = 0.0
            last_t = np.nan
            c = 0.0
            continue
        if np.isnan(last_t) or target[i] != last_t:
            if np.isnan(last_t):
                c = desired[i] if retune_ms < 40 else desired[i] * 0.5
            held = 0.0
            last_t = target[i]
        else:
            held += hop
        rt = retune_ms + (humanize / 100.0) * 400.0 * min(held / 0.35, 1.0)
        a = np.exp(-hop / max(rt / 1000.0, 1e-4))
        c = desired[i] + a * (c - desired[i])
        corr[i] = c
    shift = corr * amount + np.where(v, transpose, 0.0) + np.where(v, 0, 0)
    # unvoiced frames still get the transpose (keeps consonants consistent if transposing)
    shift[~v] = transpose
    out = np.where(v, midi + corr * amount + transpose, np.nan)
    return TuneResult(track.times, midi, out, target, shift)


# --------------------------------------------------------------------------- PSOLA resynthesis
def _half_windows():
    cache = {}

    def get(L, rising):
        key = (L, rising)
        w = cache.get(key)
        if w is None:
            i = np.arange(L, dtype=np.float32)
            w = (0.5 - 0.5 * np.cos(np.pi * i / max(L, 1))).astype(np.float32)
            if not rising:
                w = w[::-1]
            cache[key] = w
        return w
    return get


def _analysis_marks(mono, sr, f0_samples, voiced):
    """Pitch-synchronous marks in voiced zones, 5 ms grid elsewhere."""
    lp = signal.sosfilt(signal.butter(2, 900, "lowpass", fs=sr, output="sos"), mono)
    n = len(mono)
    marks = []
    unv_step = max(16, int(0.005 * sr))
    i = 0
    while i < n:
        if voiced[i]:
            T = int(sr / f0_samples[i])
            seg = lp[i:i + T]
            j = i + int(np.argmax(seg)) if len(seg) else i
            # walk the voiced segment
            while j < n and voiced[j]:
                marks.append(j)
                T = max(16, int(sr / max(f0_samples[j], 40)))
                c = j + T
                w = max(1, int(T * 0.15))
                a, b = max(c - w, j + 8), min(c + w, n)
                if a >= b:
                    j = c
                    continue
                j = a + int(np.argmax(lp[a:b]))
            i = j if j > i else i + 1
            while i < n and voiced[i]:
                i += 1
        else:
            marks.append(i)
            i += unv_step
    marks = np.unique(np.asarray(marks, dtype=np.int64))
    marks = marks[(marks >= 0) & (marks < n)]
    return marks


def psola_shift(x, sr, track: PitchTrack, shift_semitones_frames: np.ndarray,
                formant_semitones: float = 0.0) -> np.ndarray:
    """Time-varying pitch shift of a monophonic vocal, formants preserved."""
    x = dsp.as2d(x)
    mono = x.mean(axis=1).astype(np.float64)
    n = len(mono)
    if n == 0:
        return x
    st = np.arange(n) / sr
    f0f = track.f0
    vf = f0f > 0
    # interpolate f0 over samples (hold inside voiced segments)
    if vf.any():
        f0_interp = np.interp(st, track.times[vf], f0f[vf])
    else:
        f0_interp = np.full(n, 150.0)
    voiced = np.interp(st, track.times, vf.astype(float)) > 0.5
    shift = np.interp(st, track.times, shift_semitones_frames)
    ratio = 2 ** (shift / 12.0)
    marks = _analysis_marks(mono, sr, f0_interp, voiced)
    if len(marks) < 3:
        return x
    left = np.diff(np.concatenate([[marks[0] - 64], marks]))
    right = np.diff(np.concatenate([marks, [marks[-1] + 64]]))
    left = np.clip(left, 16, int(0.025 * sr))
    right = np.clip(right, 16, int(0.025 * sr))
    period = (left + right) / 2.0
    win = _half_windows()
    C = x.shape[1]
    out = np.zeros((n + 4096, C), np.float64)
    wsum = np.zeros(n + 4096, np.float64)
    xd = x.astype(np.float64)
    fm = 2 ** (formant_semitones / 12.0)
    ts = float(marks[0])
    k = 0
    nm = len(marks)
    while ts < n:
        # nearest analysis mark to synthesis time
        while k + 1 < nm and abs(marks[k + 1] - ts) <= abs(marks[k] - ts):
            k += 1
        m = int(marks[k])
        L, R = int(left[k]), int(right[k])
        a, b = m - L, m + R
        if a < 0 or b > n:
            ts += period[k]
            continue
        g = xd[a:b]
        w = np.concatenate([win(L, True), win(R, False)])
        if fm != 1.0 and voiced[m]:
            src = np.arange(len(g))
            cen = L
            pos = cen + (src - cen) / fm
            g = np.stack([np.interp(pos, src, g[:, c]) for c in range(C)], axis=1)
        pos0 = int(round(ts)) - L
        if pos0 >= 0 and pos0 + len(w) <= len(out):
            out[pos0:pos0 + len(w)] += g * w[:, None]
            wsum[pos0:pos0 + len(w)] += w
        r = ratio[min(int(ts), n - 1)] if voiced[min(int(ts), n - 1)] else 1.0
        ts += max(period[k] / r, 8.0)
    out = out[:n]
    wsum = wsum[:n]
    good = wsum > 1e-3
    out[good] /= wsum[good, None]
    # fall back to dry where nothing was synthesised
    out[~good] = xd[~good]
    return out.astype(np.float32)


def autotune(x, sr, root=0, scale="Minor", a4=440.0, retune_ms=20.0, humanize=0.0,
             flex_cents=0.0, amount=1.0, transpose=0.0, formant=0.0, degrees=0,
             fmin=60.0, fmax=900.0, track: PitchTrack | None = None, return_info=False,
             frozen_notes=None, hysteresis_cents=20.0):
    track = track or detect_pitch(x, sr, fmin, fmax)
    res = correction_curve(track, root, scale, a4, retune_ms, humanize, flex_cents, amount,
                           transpose, hysteresis_cents, degrees, frozen_notes=frozen_notes)
    y = psola_shift(x, sr, track, res.shift_semitones, formant)
    if return_info:
        return y, res, track
    return y


def pitch_shift_vocal(x, sr, semitones, formant=0.0, track=None):
    track = track or detect_pitch(x, sr)
    shift = np.full(len(track.f0), float(semitones))
    return psola_shift(x, sr, track, shift, formant)


def vocal_range(track: PitchTrack):
    f = track.f0[track.f0 > 0]
    if len(f) < 10:
        return None
    lo, med, hi = np.percentile(f, [5, 50, 95])
    return dict(low_hz=float(lo), median_hz=float(med), high_hz=float(hi),
                low_note=hz_to_note(lo), median_note=hz_to_note(med), high_note=hz_to_note(hi))


def hz_to_note(f, a4=440.0):
    if f <= 0:
        return "-"
    m = int(round(69 + 12 * np.log2(f / a4)))
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def melodic_score(track: PitchTrack, a4=440.0) -> float:
    """0 = spoken/rapped, 1 = sung. Based on how long and steady voiced notes are."""
    m = track.midi(a4)
    v = ~np.isnan(m)
    if v.sum() < 20:
        return 0.0
    edges = np.flatnonzero(np.diff(np.concatenate([[0], v.astype(int), [0]])))
    lens = (edges[1::2] - edges[::2]) * track.hop_s
    long_frac = lens[lens > 0.25].sum() / max(lens.sum(), 1e-9)
    d = np.abs(np.diff(m))
    steady = np.nanmean(d[~np.isnan(d)] < 0.15) if np.any(~np.isnan(d)) else 0
    voiced_frac = v.mean()
    return float(np.clip(0.5 * long_frac + 0.3 * steady + 0.4 * voiced_frac - 0.2, 0, 1))


def detect_key_from_vocal(track: PitchTrack, a4=440.0):
    """Key guess from the vocal melody alone (pitch-class histogram)."""
    from .analysis import KK_MAJOR, KK_MINOR
    m = track.midi(a4)
    v = ~np.isnan(m)
    if v.sum() < 20:
        return None
    pcs = np.mod(np.round(m[v]), 12).astype(int)
    hist = np.bincount(pcs, minlength=12).astype(float)
    best = None
    for r in range(12):
        for s, prof in (("Major", KK_MAJOR), ("Minor", KK_MINOR)):
            c = np.corrcoef(hist, np.roll(prof, r))[0, 1]
            if best is None or c > best[2]:
                best = (r, s, float(c))
    return best
