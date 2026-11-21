"""Core DSP building blocks: filters, dynamics, repair, color and space.

All functions take/return float32 arrays shaped (n, ch) unless noted. Pure numpy/scipy;
numba is used automatically for the envelope loops when it is installed.
"""
from __future__ import annotations

import math
import numpy as np
from scipy import signal
from scipy.ndimage import minimum_filter1d, maximum_filter1d, uniform_filter1d

try:
    from numba import njit  # type: ignore
except Exception:  # pragma: no cover
    njit = None

EPS = 1e-12


def db_to_lin(db):
    return np.power(10.0, np.asarray(db) / 20.0)


def lin_to_db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), EPS))


def as2d(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return x[:, None] if x.ndim == 1 else x


# --------------------------------------------------------------------------- filters
def _biquad(kind: str, f0: float, sr: int, q: float = 0.707, gain_db: float = 0.0) -> np.ndarray:
    """RBJ cookbook biquad -> sos row."""
    f0 = float(np.clip(f0, 5.0, sr * 0.49))
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * f0 / sr
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * max(q, 1e-3))
    if kind == "lowpass":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]; a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "highpass":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]; a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "bandpass":
        b = [alpha, 0, -alpha]; a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "notch":
        b = [1, -2 * cw, 1]; a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "peak":
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]; a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    elif kind == "lowshelf":
        sa = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) - (A - 1) * cw + sa), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - sa)]
        a = [(A + 1) + (A - 1) * cw + sa, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sa]
    elif kind == "highshelf":
        sa = 2 * math.sqrt(A) * alpha
        b = [A * ((A + 1) + (A - 1) * cw + sa), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sa)]
        a = [(A + 1) - (A - 1) * cw + sa, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sa]
    else:
        raise ValueError(kind)
    b = np.array(b) / a[0]
    a = np.array(a) / a[0]
    return np.concatenate([b, a])[None, :]


def biquad_sos(kind, f0, sr, q=0.707, gain_db=0.0, order=2):
    """Build sos; for HP/LP 'order' can be 2,4,6,8 (12-48 dB/oct Butterworth-ish cascade)."""
    if kind in ("highpass", "lowpass") and order > 2:
        btype = "highpass" if kind == "highpass" else "lowpass"
        return signal.butter(order, float(np.clip(f0, 5, sr * 0.49)), btype=btype, fs=sr, output="sos")
    return _biquad(kind, f0, sr, q, gain_db)


def sosfilt(sos, x):
    x = as2d(x)
    if sos is None or len(sos) == 0:
        return x
    return signal.sosfilt(sos, x, axis=0).astype(np.float32)


def highpass(x, sr, f, order=2):
    return sosfilt(biquad_sos("highpass", f, sr, order=order), x)


def lowpass(x, sr, f, order=2):
    return sosfilt(biquad_sos("lowpass", f, sr, order=order), x)


def eq_bands(x, sr, bands):
    """bands: list of dicts {type, freq, gain, q, enabled}."""
    rows = []
    for b in bands:
        if not b.get("enabled", True):
            continue
        t = b["type"]
        if t in ("peak", "lowshelf", "highshelf") and abs(b.get("gain", 0.0)) < 0.01:
            continue
        rows.append(biquad_sos(t, b["freq"], sr, b.get("q", 0.707), b.get("gain", 0.0), b.get("order", 2)))
    if not rows:
        return as2d(x)
    return sosfilt(np.vstack(rows), x)


def eq_response(sr, bands, freqs):
    """Magnitude response in dB at freqs (for EQ curve display)."""
    rows = []
    for b in bands:
        if not b.get("enabled", True):
            continue
        rows.append(biquad_sos(b["type"], b["freq"], sr, b.get("q", 0.707), b.get("gain", 0.0), b.get("order", 2)))
    if not rows:
        return np.zeros_like(freqs, dtype=float)
    _, h = signal.sosfreqz(np.vstack(rows), worN=np.asarray(freqs, float), fs=sr)
    return 20 * np.log10(np.maximum(np.abs(h), EPS))


# --------------------------------------------------------------------------- envelopes
def _smooth_py(target, att, rel):
    out = np.empty_like(target)
    g = target[0] if len(target) else 0.0
    for i in range(len(target)):
        t = target[i]
        c = att if t < g else rel  # gain going down (more reduction) = attack
        g = t + c * (g - t)
        out[i] = g
    return out


if njit is not None:  # pragma: no cover
    _smooth = njit(cache=True)(_smooth_py)
else:
    _smooth = _smooth_py


def smooth_gain_db(target_db: np.ndarray, rate: float, attack_s: float, release_s: float) -> np.ndarray:
    """Asymmetric one-pole smoothing of a gain curve in dB (negative = reduction)."""
    att = math.exp(-1.0 / max(attack_s * rate, 1e-6))
    rel = math.exp(-1.0 / max(release_s * rate, 1e-6))
    return _smooth(np.ascontiguousarray(target_db, dtype=np.float64), att, rel)


CTRL_BLOCK = 32


def block_level_db(x_mono: np.ndarray, block: int = CTRL_BLOCK, mode: str = "peak") -> np.ndarray:
    n = len(x_mono)
    nb = int(np.ceil(n / block))
    pad = nb * block - n
    m = np.pad(np.abs(x_mono), (0, pad)).reshape(nb, block)
    if mode == "rms":
        lv = np.sqrt(np.mean(m * m, axis=1))
    else:
        lv = m.max(axis=1)
    return lin_to_db(lv)


def expand_ctrl(gain_db_ctrl: np.ndarray, n: int, block: int = CTRL_BLOCK) -> np.ndarray:
    centers = (np.arange(len(gain_db_ctrl)) + 0.5) * block
    return np.interp(np.arange(n), centers, gain_db_ctrl).astype(np.float32)


def _detector(x, sidechain=None):
    sc = as2d(x if sidechain is None else sidechain)
    return np.max(np.abs(sc), axis=1)


# --------------------------------------------------------------------------- dynamics
def compressor(x, sr, threshold_db=-18.0, ratio=4.0, attack_ms=10.0, release_ms=80.0,
               knee_db=6.0, makeup_db=0.0, auto_makeup=False, mix=1.0, sidechain=None,
               detector="peak", return_gr=False):
    x = as2d(x)
    lv = block_level_db(_detector(x, sidechain), mode=detector)
    over = lv - threshold_db
    gr = np.zeros_like(lv)
    k = max(knee_db, 0.0)
    if k > 0:
        mid = (over > -k / 2) & (over < k / 2)
        gr[mid] = (1 / ratio - 1) * (over[mid] + k / 2) ** 2 / (2 * k)
    hi = over >= k / 2
    gr[hi] = (1 / ratio - 1) * over[hi]
    rate = sr / CTRL_BLOCK
    gr = smooth_gain_db(gr, rate, attack_ms / 1000.0, release_ms / 1000.0)
    makeup = makeup_db
    if auto_makeup:
        makeup += -threshold_db * (1 - 1 / ratio) * 0.5
    g = db_to_lin(expand_ctrl(gr, len(x)) + makeup)[:, None]
    wet = x * g
    out = (wet * mix + x * (1 - mix)).astype(np.float32)
    if return_gr:
        return out, gr
    return out


def gate_expander(x, sr, threshold_db=-45.0, ratio=4.0, range_db=-40.0, attack_ms=1.0,
                  hold_ms=60.0, release_ms=120.0):
    """Downward expander/gate (ratio >= 20 behaves like a gate)."""
    x = as2d(x)
    lv = block_level_db(_detector(x))
    rate = sr / CTRL_BLOCK
    hold_blocks = max(1, int(hold_ms / 1000 * rate))
    lv_held = maximum_filter1d(lv, size=hold_blocks * 2 + 1)
    under = np.minimum(lv_held - threshold_db, 0.0)
    gr = np.maximum(under * (ratio - 1), range_db)
    # closing the gate (more reduction) uses release; opening uses attack
    gr = smooth_gain_db(gr, rate, release_ms / 1000.0, attack_ms / 1000.0)
    return (x * db_to_lin(expand_ctrl(gr, len(x)))[:, None]).astype(np.float32)


def limiter(x, sr, ceiling_db=-1.0, release_ms=60.0, lookahead_ms=3.0, input_gain_db=0.0):
    x = as2d(x) * db_to_lin(input_gain_db)
    ceil = db_to_lin(ceiling_db)
    peak = np.max(np.abs(x), axis=1)
    need = np.minimum(1.0, ceil / np.maximum(peak, EPS))
    la = max(1, int(lookahead_ms / 1000 * sr))
    need = minimum_filter1d(need, size=2 * la + 1)
    nb = int(np.ceil(len(need) / CTRL_BLOCK))
    pad = nb * CTRL_BLOCK - len(need)
    blk = np.pad(need, (0, pad), constant_values=1.0).reshape(nb, CTRL_BLOCK).min(axis=1)
    gdb = lin_to_db(blk)
    gdb = smooth_gain_db(gdb, sr / CTRL_BLOCK, 0.0001, release_ms / 1000.0)
    gdb = minimum_filter1d(gdb, size=3)
    g = db_to_lin(expand_ctrl(gdb, len(x)))
    g = np.minimum(g, need)  # guarantee the ceiling
    out = x * g[:, None]
    return np.clip(out, -ceil, ceil).astype(np.float32)


def deesser(x, sr, freq=6500.0, threshold_db=-28.0, max_reduction_db=8.0, mode="split",
            attack_ms=0.5, release_ms=40.0, listen=False, auto=True):
    """Split-band de-esser. auto=True sets the threshold from the material itself."""
    x = as2d(x)
    sc_sos = biquad_sos("highpass", freq, sr, order=4)
    high = sosfilt(sc_sos, x)
    lv = block_level_db(np.max(np.abs(high), axis=1))
    full = block_level_db(np.max(np.abs(x), axis=1))
    if auto:
        active = lv[full > np.percentile(full, 40)] if np.any(full > -60) else lv
        thr = float(np.percentile(active, 85)) if len(active) else threshold_db
        thr = thr + (threshold_db + 28.0)  # user threshold acts as an offset in auto mode
    else:
        thr = threshold_db
    # only treat frames where highs dominate (sibilant) vs. normal bright frames
    sib_ratio = lv - full
    over = np.maximum(lv - thr, 0.0) * np.clip((sib_ratio + 12.0) / 8.0, 0.0, 1.0)
    gr = -np.minimum(over, max_reduction_db)
    gr = smooth_gain_db(gr, sr / CTRL_BLOCK, attack_ms / 1000, release_ms / 1000)
    g = db_to_lin(expand_ctrl(gr, len(x)))[:, None]
    if listen:
        return (high * (1 - g)).astype(np.float32)
    if mode == "wide":
        return (x * g).astype(np.float32)
    low = x - high
    return (low + high * g).astype(np.float32)


def vocal_rider(x, sr, target_db=-18.0, max_boost_db=6.0, max_cut_db=6.0, speed_ms=150.0,
                gate_db=-50.0):
    """Automatic gain riding: pushes quiet words up and loud words down toward a target."""
    x = as2d(x)
    lv = block_level_db(np.max(np.abs(x), axis=1), mode="rms")
    rate = sr / CTRL_BLOCK
    w = max(1, int(0.05 * rate))
    lv = 10 * np.log10(np.maximum(uniform_filter1d(10 ** (lv / 10), size=w), EPS))
    gain = np.clip(target_db - lv, -max_cut_db, max_boost_db)
    gain[lv < gate_db] = 0.0
    gain = smooth_gain_db(gain, rate, speed_ms / 1000, speed_ms / 1000)
    return (x * db_to_lin(expand_ctrl(gain, len(x)))[:, None]).astype(np.float32)


def multiband_compressor(x, sr, low_xover=200.0, high_xover=4000.0, bands=None):
    """3-band compressor. bands = [dict(threshold_db, ratio, gain_db) * 3]."""
    x = as2d(x)
    bands = bands or [dict(threshold_db=-24, ratio=3, gain_db=0)] * 3
    lp1 = sosfilt(signal.butter(4, low_xover, "lowpass", fs=sr, output="sos"), x)
    rest = x - lp1
    hp2 = sosfilt(signal.butter(4, high_xover, "highpass", fs=sr, output="sos"), rest)
    mid = rest - hp2
    out = np.zeros_like(x)
    for part, b in zip((lp1, mid, hp2), bands):
        out += compressor(part, sr, b["threshold_db"], b["ratio"], b.get("attack_ms", 10),
                          b.get("release_ms", 100)) * db_to_lin(b.get("gain_db", 0.0))
    return out.astype(np.float32)


# --------------------------------------------------------------------------- repair
def noise_reduction(x, sr, reduction_db=12.0, sensitivity=1.5, profile=None, smoothing=0.5,
                    n_fft=2048, return_profile=False):
    """Spectral gating noise reduction (like Audition's Noise Reduction).

    profile: optional (n_fft//2+1,) magnitude noise print; otherwise learned from the
    quietest 10% of frames.
    """
    x = as2d(x)
    hop = n_fft // 4
    win = "hann"
    out = np.zeros_like(x)
    floor = db_to_lin(-abs(reduction_db))
    learned = None
    for c in range(x.shape[1]):
        f, t, Z = signal.stft(x[:, c], fs=sr, window=win, nperseg=n_fft, noverlap=n_fft - hop,
                              boundary="even", padded=True)
        mag = np.abs(Z)
        if profile is None:
            energy = mag.sum(axis=0)
            if mag.shape[1] > 10:
                idx = energy <= np.percentile(energy, 10)
                prof = np.mean(mag[:, idx], axis=1)
            else:
                prof = np.mean(mag, axis=1)
        else:
            prof = np.asarray(profile)
        learned = prof
        ratio = mag / (prof[:, None] * sensitivity + EPS)
        mask = np.clip((ratio - 1.0) / 1.0, 0.0, 1.0)
        # smooth mask across time & frequency to avoid musical noise
        k_t = max(1, int(1 + smoothing * 6))
        mask = uniform_filter1d(mask, size=k_t, axis=1)
        mask = uniform_filter1d(mask, size=3, axis=0)
        gain = floor + (1 - floor) * mask
        _, y = signal.istft(Z * gain, fs=sr, window=win, nperseg=n_fft, noverlap=n_fft - hop,
                            boundary=True)
        y = y[: len(x)]
        out[: len(y), c] = y
    if return_profile:
        return out.astype(np.float32), learned
    return out.astype(np.float32)


def capture_noise_profile(x, sr, n_fft=2048):
    x = as2d(x).mean(axis=1)
    _, _, Z = signal.stft(x, fs=sr, nperseg=n_fft, noverlap=n_fft * 3 // 4)
    return np.mean(np.abs(Z), axis=1)


def hum_remover(x, sr, base_hz=60.0, harmonics=6, q=30.0):
    rows = [biquad_sos("notch", base_hz * (k + 1), sr, q=q) for k in range(harmonics)
            if base_hz * (k + 1) < sr / 2.2]
    return sosfilt(np.vstack(rows), x) if rows else as2d(x)


def declick(x, sr, sensitivity=6.0, max_click_ms=2.0):
    """Removes mouth clicks / digital ticks by interpolating over impulsive outliers."""
    x = as2d(x).copy()
    hp = highpass(x, sr, 3000, order=4)
    mono = np.max(np.abs(hp), axis=1)
    med = uniform_filter1d(mono, size=int(0.02 * sr) + 1) + 1e-6
    hits = mono > med * sensitivity
    if not np.any(hits):
        return x
    max_len = max(1, int(max_click_ms / 1000 * sr))
    idx = np.flatnonzero(hits)
    groups = np.split(idx, np.flatnonzero(np.diff(idx) > max_len) + 1)
    n = len(x)
    for g in groups:
        if len(g) == 0 or (g[-1] - g[0]) > max_len * 3:
            continue
        a = max(1, g[0] - max_len // 2)
        b = min(n - 2, g[-1] + max_len // 2)
        if b <= a:
            continue
        t = np.linspace(0, 1, b - a + 2)[1:-1]
        for c in range(x.shape[1]):
            x[a:b, c] = x[a - 1, c] * (1 - t) + x[b, c] * t + (hp[a:b, c] * 0.0)
    return x.astype(np.float32)


def deplosive(x, sr, freq=150.0, sensitivity_db=10.0, max_reduction_db=12.0):
    """Tames 'p'/'b' pops: ducks the sub band when it bursts relative to the mids."""
    x = as2d(x)
    sos = signal.butter(4, freq, "lowpass", fs=sr, output="sos")
    low = sosfilt(sos, x)
    rest = x - low
    lv_low = block_level_db(np.max(np.abs(low), axis=1), mode="rms")
    lv_mid = block_level_db(np.max(np.abs(rest), axis=1), mode="rms")
    excess = (lv_low - lv_mid) - (-sensitivity_db + 10.0)
    gr = -np.clip(excess, 0.0, max_reduction_db)
    gr = smooth_gain_db(gr, sr / CTRL_BLOCK, 0.001, 0.06)
    g = db_to_lin(expand_ctrl(gr, len(x)))[:, None]
    return (rest + low * g).astype(np.float32)


# --------------------------------------------------------------------------- color
def _oversampled(fn, x, factor=2):
    up = signal.resample_poly(x, factor, 1, axis=0)
    y = fn(up)
    return signal.resample_poly(y, 1, factor, axis=0)[: len(x)].astype(np.float32)


def saturation(x, sr, drive_db=6.0, mode="tape", mix=0.4, tone=0.0, output_db=0.0):
    x = as2d(x)
    d = db_to_lin(drive_db)

    def shaper(s):
        s = s * d
        if mode == "tube":
            y = np.where(s >= 0, np.tanh(s), np.tanh(s * 0.7) / 0.7 * 0.9)
            y = y + 0.05 * s * s * np.sign(s) * 0  # even harmonics come from asymmetry above
        elif mode == "hard":
            y = np.clip(s, -1, 1)
        elif mode == "warm":
            y = s / (1 + np.abs(s))
        else:  # tape
            y = np.tanh(s)
        return y / max(np.tanh(d), 1e-3)

    wet = _oversampled(shaper, x)
    if tone:
        wet = eq_bands(wet, sr, [dict(type="highshelf", freq=4000, gain=tone, q=0.7)])
    y = x * (1 - mix) + wet * mix
    return (y * db_to_lin(output_db)).astype(np.float32)


def exciter(x, sr, freq=3500.0, drive_db=12.0, amount=0.25):
    x = as2d(x)
    hp = highpass(x, sr, freq, order=4)
    harm = np.tanh(hp * db_to_lin(drive_db)) - hp * 0.0
    harm = highpass(harm, sr, freq, order=2)
    return (x + harm * amount).astype(np.float32)


def bitcrush(x, sr, bits=10, downsample=2, mix=1.0):
    x = as2d(x)
    q = 2 ** (bits - 1)
    y = np.round(x * q) / q
    if downsample > 1:
        y = np.repeat(y[::downsample], downsample, axis=0)[: len(x)]
    return (x * (1 - mix) + y * mix).astype(np.float32)


def telephone(x, sr, low=400.0, high=3400.0, drive_db=6.0):
    y = highpass(lowpass(x, sr, high, order=4), sr, low, order=4)
    return saturation(y, sr, drive_db, "hard", 0.5)


# --------------------------------------------------------------------------- space
def make_reverb_ir(sr, decay_s=1.2, predelay_ms=25.0, damping=0.5, size=0.7, width=1.0,
                   early=0.3, seed=7):
    rng = np.random.default_rng(seed)
    length = int(sr * min(decay_s * 1.5 + 0.1, 8.0))
    t = np.arange(length) / sr
    ir = np.zeros((length, 2), np.float32)
    # late tail: noise with exponential decay (-60 dB at decay_s)
    env = np.exp(-6.9078 * t / max(decay_s, 0.05))
    fade_in = np.clip(t / (0.02 + 0.06 * size), 0, 1)
    for c in range(2):
        n = rng.standard_normal(length) * env * fade_in
        # frequency-dependent decay: highs die faster
        hi = highpass(n, sr, 3000)[:, 0]
        lo = n - hi
        hi_env = np.exp(-6.9078 * t / max(decay_s * (1.0 - 0.8 * damping), 0.05))
        ir[:, c] = lo + hi * (hi_env / np.maximum(env, EPS))
    # decorrelation / width
    mid = ir.mean(axis=1, keepdims=True)
    ir = mid + (ir - mid) * width
    # early reflections
    taps = rng.uniform(0.005, 0.06 * (0.5 + size), 12)
    for i, tap in enumerate(sorted(taps)):
        k = int(tap * sr)
        if k < length:
            ir[k, i % 2] += early * (0.8 ** i) * (1 if i % 3 else -1)
    pd = int(predelay_ms / 1000 * sr)
    ir = np.vstack([np.zeros((pd, 2), np.float32), ir])
    ir /= np.sqrt(np.sum(ir ** 2) / 2 + EPS)
    return ir.astype(np.float32)


def reverb(x, sr, decay_s=1.2, predelay_ms=25.0, damping=0.5, size=0.7, width=1.0,
           mix=0.15, low_cut=250.0, high_cut=9000.0, early=0.3, ir=None):
    x = as2d(x)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    ir = make_reverb_ir(sr, decay_s, predelay_ms, damping, size, width, early) if ir is None else ir
    src = highpass(lowpass(x, sr, high_cut), sr, low_cut)
    wet = np.zeros_like(x)
    for c in range(2):
        wet[:, c] = signal.fftconvolve(src[:, c], ir[:, c])[: len(x)]
    wet *= 0.25
    return (x * (1 - mix * 0.5) + wet * mix).astype(np.float32)


def note_to_seconds(bpm: float, division: str) -> float:
    beat = 60.0 / max(bpm, 1.0)
    table = {"1/1": 4, "1/2": 2, "1/2D": 3, "1/4": 1, "1/4D": 1.5, "1/4T": 2 / 3, "1/8": 0.5,
             "1/8D": 0.75, "1/8T": 1 / 3, "1/16": 0.25, "1/16D": 0.375, "1/32": 0.125}
    return beat * table.get(division, 1.0)


def delay(x, sr, time_ms=None, bpm=None, division="1/4", feedback=0.3, mix=0.15,
          ping_pong=True, low_cut=300.0, high_cut=6000.0, repeats=8):
    x = as2d(x)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    t = (time_ms / 1000.0) if time_ms else note_to_seconds(bpm or 90, division)
    D = max(1, int(t * sr))
    n = len(x)
    wet = np.zeros_like(x)
    tap = highpass(lowpass(x, sr, high_cut), sr, low_cut)
    g = 1.0
    for k in range(1, repeats + 1):
        off = D * k
        if off >= n or g < 1e-3:
            break
        seg = tap[: n - off]
        if ping_pong:
            ch = (k - 1) % 2
            wet[off:, ch] += seg.mean(axis=1) * g
        else:
            wet[off:] += seg * g
        g *= feedback
        tap = lowpass(tap, sr, high_cut * 0.9) if k < 4 else tap
    return (x + wet * mix).astype(np.float32)


def _fractional_delay(x1d, delay_samples):
    n = len(x1d)
    idx = np.arange(n) - delay_samples
    return np.interp(idx, np.arange(n), x1d, left=0.0, right=0.0).astype(np.float32)


def doubler(x, sr, voices=2, detune_cents=8.0, delay_ms=18.0, spread=0.8, mix=0.5, seed=3):
    """Artificial double-tracking: slightly delayed, detuned, wandering copies panned wide."""
    x = as2d(x)
    mono = x.mean(axis=1)
    n = len(mono)
    rng = np.random.default_rng(seed)
    out = np.zeros((n, 2), np.float32)
    t = np.arange(n) / sr
    for v in range(voices):
        rate = rng.uniform(0.15, 0.4)
        phase = rng.uniform(0, 2 * np.pi)
        # pitch deviation ~ derivative of delay: d(delay)/dt = cents -> ratio
        depth_s = (2 ** (detune_cents / 1200) - 1) / (2 * np.pi * rate)
        base = (delay_ms / 1000) * rng.uniform(0.7, 1.3)
        d = (base + depth_s * np.sin(2 * np.pi * rate * t + phase)) * sr
        cp = _fractional_delay(mono, d)
        pan = spread * (1 if v % 2 == 0 else -1) * (1.0 - 0.3 * (v // 2))
        l = math.cos((pan + 1) * math.pi / 4)
        r = math.sin((pan + 1) * math.pi / 4)
        out[:, 0] += cp * l
        out[:, 1] += cp * r
    out /= max(1, voices) ** 0.5
    dry = x if x.shape[1] == 2 else np.repeat(x, 2, axis=1)
    return (dry + out * mix).astype(np.float32)


def chorus(x, sr, rate_hz=0.8, depth_ms=3.0, delay_ms=12.0, mix=0.3):
    x = as2d(x)
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    t = np.arange(len(x)) / sr
    out = x.copy()
    for c, ph in ((0, 0.0), (1, np.pi / 2)):
        d = (delay_ms + depth_ms * np.sin(2 * np.pi * rate_hz * t + ph)) / 1000 * sr
        out[:, c] = x[:, c] * (1 - mix) + _fractional_delay(x[:, c], d) * mix
    return out.astype(np.float32)


def stereo_width(x, width=1.0):
    x = as2d(x)
    if x.shape[1] == 1:
        return x
    m = (x[:, 0] + x[:, 1]) / 2
    s = (x[:, 0] - x[:, 1]) / 2 * width
    return np.stack([m + s, m - s], axis=1).astype(np.float32)


def pan_gains(pan: float) -> tuple[float, float]:
    """Constant-power pan law (-3 dB centre), pan in [-1, 1]."""
    p = (np.clip(pan, -1, 1) + 1) * math.pi / 4
    return math.cos(p) * math.sqrt(2), math.sin(p) * math.sqrt(2)


# --------------------------------------------------------------------------- general pitch / time
def phase_vocoder_stretch(x1d, rate, n_fft=2048, hop=512):
    """Time-stretch by 'rate' (>1 = faster/shorter) with identity phase locking."""
    f, t, Z = signal.stft(x1d, nperseg=n_fft, noverlap=n_fft - hop, boundary="even")
    n_frames = Z.shape[1]
    steps = np.arange(0, n_frames - 1, rate)
    omega = 2 * np.pi * hop * np.arange(Z.shape[0]) / n_fft
    phase = np.angle(Z[:, 0])
    out = np.zeros((Z.shape[0], len(steps)), complex)
    for i, s in enumerate(steps):
        k = int(s)
        frac = s - k
        a, b = Z[:, k], Z[:, min(k + 1, n_frames - 1)]
        mag = (1 - frac) * np.abs(a) + frac * np.abs(b)
        out[:, i] = mag * np.exp(1j * phase)
        dphi = np.angle(b) - np.angle(a) - omega
        dphi -= 2 * np.pi * np.round(dphi / (2 * np.pi))
        phase = phase + omega + dphi
    _, y = signal.istft(out, nperseg=n_fft, noverlap=n_fft - hop, boundary=True)
    return y.astype(np.float32)


def time_stretch(x, sr, rate):
    x = as2d(x)
    if abs(rate - 1.0) < 1e-4:
        return x
    chans = [phase_vocoder_stretch(x[:, c], rate) for c in range(x.shape[1])]
    n = min(len(c) for c in chans)
    return np.stack([c[:n] for c in chans], axis=1)


def pitch_shift_general(x, sr, semitones):
    """Pitch shift for full mixes/beats (phase vocoder + resample, keeps length)."""
    x = as2d(x)
    if abs(semitones) < 1e-3:
        return x
    ratio = 2 ** (semitones / 12.0)
    st = time_stretch(x, sr, 1.0 / ratio)
    from math import gcd
    up, down = 1000, int(round(1000 * ratio))
    g = gcd(up, down)
    y = signal.resample_poly(st, up // g, down // g, axis=0)
    n = len(x)
    if len(y) < n:
        y = np.vstack([y, np.zeros((n - len(y), y.shape[1]))])
    return y[:n].astype(np.float32)


# --------------------------------------------------------------------------- utility
def normalize_peak(x, target_db=-1.0):
    x = as2d(x)
    pk = float(np.max(np.abs(x))) if x.size else 0.0
    if pk < 1e-9:
        return x
    return (x * (db_to_lin(target_db) / pk)).astype(np.float32)


def remove_dc(x, sr):
    return highpass(x, sr, 10.0)


def apply_fade(x, fade_in=0, fade_out=0, shape="log"):
    x = as2d(x).copy()
    n = len(x)

    def curve(m):
        t = np.linspace(0, 1, m, dtype=np.float32)
        if shape == "linear":
            return t
        if shape == "exp":
            return t ** 2
        if shape == "scurve":
            return 0.5 - 0.5 * np.cos(np.pi * t)
        return np.sqrt(t)  # "log"-ish / equal power

    if fade_in > 0:
        m = min(int(fade_in), n)
        x[:m] *= curve(m)[:, None]
    if fade_out > 0:
        m = min(int(fade_out), n)
        x[n - m:] *= curve(m)[::-1, None]
    return x
