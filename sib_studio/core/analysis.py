"""Musical analysis: key/scale, tuning reference, tempo/beat grid, loudness, spectrum."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter1d

from .audio_io import to_mono, resample
from . import dsp

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = {"C#": "Db", "D#": "Eb", "F#": "Gb", "G#": "Ab", "A#": "Bb"}

# Krumhansl-Kessler key profiles
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

SCALES = {
    "Chromatic": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    "Major": [0, 2, 4, 5, 7, 9, 11],
    "Minor": [0, 2, 3, 5, 7, 8, 10],
    "Harmonic Minor": [0, 2, 3, 5, 7, 8, 11],
    "Melodic Minor": [0, 2, 3, 5, 7, 9, 11],
    "Minor Pentatonic": [0, 3, 5, 7, 10],
    "Major Pentatonic": [0, 2, 4, 7, 9],
    "Blues": [0, 3, 5, 6, 7, 10],
    "Dorian": [0, 2, 3, 5, 7, 9, 10],
    "Phrygian": [0, 1, 3, 5, 7, 8, 10],
    "Mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "Phrygian Dominant": [0, 1, 4, 5, 7, 8, 10],
}


def key_name(root: int, scale: str) -> str:
    return f"{NOTE_NAMES[root % 12]} {scale}"


@dataclass
class KeyResult:
    root: int
    scale: str
    confidence: float
    candidates: list = field(default_factory=list)  # [(root, scale, score)]

    @property
    def name(self):
        return key_name(self.root, self.scale)

    @property
    def relative(self):
        if self.scale == "Minor":
            return key_name((self.root + 3) % 12, "Major")
        if self.scale == "Major":
            return key_name((self.root + 9) % 12, "Minor")
        return ""


@dataclass
class BeatAnalysis:
    bpm: float = 0.0
    bpm_alternatives: list = field(default_factory=list)
    beat_offset_s: float = 0.0
    key: dict = field(default_factory=dict)
    tuning_cents: float = 0.0
    a4_hz: float = 440.0
    lufs: float = -70.0
    peak_db: float = -120.0
    duration_s: float = 0.0
    band_energy_db: dict = field(default_factory=dict)
    vocal_pocket_density: float = 0.0

    def to_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------- loudness
def _k_weight_sos(sr):
    # BS.1770 pre-filter (shelf) + RLB high-pass, re-derived for any sample rate
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a = [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    a0 = 1 + K / Q + K * K
    b2 = [1.0, -2.0, 1.0]
    a2 = [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    return np.array([b + a, b2 + a2])


def integrated_lufs(x, sr) -> float:
    x = dsp.as2d(x)
    if len(x) < int(0.4 * sr):
        x = np.vstack([x, np.zeros((int(0.4 * sr) - len(x) + 1, x.shape[1]), np.float32)])
    y = signal.sosfilt(_k_weight_sos(sr), x, axis=0)
    blk = int(0.4 * sr)
    hop = int(0.1 * sr)
    n = 1 + (len(y) - blk) // hop
    if n <= 0:
        return -70.0
    sq = np.cumsum(np.vstack([np.zeros((1, y.shape[1])), y * y]), axis=0)
    starts = np.arange(n) * hop
    ms = (sq[starts + blk] - sq[starts]) / blk
    z = ms.sum(axis=1)  # channel weights 1.0 for L/R
    lk = -0.691 + 10 * np.log10(np.maximum(z, 1e-12))
    gated = z[lk > -70]
    if len(gated) == 0:
        return -70.0
    rel = -0.691 + 10 * np.log10(np.mean(gated)) - 10
    final = z[(lk > -70) & (lk > rel)]
    if len(final) == 0:
        return -70.0
    return float(-0.691 + 10 * np.log10(np.mean(final)))


def short_term_lufs(x, sr, window_s=3.0) -> float:
    x = dsp.as2d(x)
    seg = x[-int(window_s * sr):]
    y = signal.sosfilt(_k_weight_sos(sr), seg, axis=0)
    z = np.mean(y * y, axis=0).sum()
    return float(-0.691 + 10 * np.log10(max(z, 1e-12)))


def true_peak_db(x, sr) -> float:
    x = dsp.as2d(x)
    if len(x) == 0:
        return -120.0
    up = signal.resample_poly(x[: sr * 600], 4, 1, axis=0)
    return float(dsp.lin_to_db(np.max(np.abs(up))))


def rms_db(x) -> float:
    x = dsp.as2d(x)
    return float(dsp.lin_to_db(np.sqrt(np.mean(x * x) + 1e-20)))


# --------------------------------------------------------------------------- spectrum
def average_spectrum(x, sr, n_fft=4096):
    mono = to_mono(x)
    if len(mono) < n_fft:
        mono = np.pad(mono, (0, n_fft - len(mono)))
    f, p = signal.welch(mono, fs=sr, nperseg=n_fft)
    return f, 10 * np.log10(np.maximum(p, 1e-20))


BANDS = {
    "sub (20-60)": (20, 60), "bass (60-250)": (60, 250), "low-mid (250-500)": (250, 500),
    "mid (500-2k)": (500, 2000), "presence (2k-5k)": (2000, 5000), "brilliance (5k-10k)": (5000, 10000),
    "air (10k-20k)": (10000, 20000),
}


def band_energies(x, sr):
    f, p = average_spectrum(x, sr)
    lin = 10 ** (p / 10)
    total = lin.sum() + 1e-20
    out = {}
    for name, (lo, hi) in BANDS.items():
        m = (f >= lo) & (f < hi)
        out[name] = float(10 * np.log10(lin[m].sum() / total + 1e-20)) if np.any(m) else -120.0
    return out


# --------------------------------------------------------------------------- tuning & key
def detect_tuning(x, sr, max_seconds=90) -> float:
    """Return tuning offset in cents relative to A440 (e.g. +12 means A4 = 443 Hz)."""
    mono = to_mono(x)[: int(sr * max_seconds)]
    mono = resample(mono, sr, 22050)[:, 0]
    sr = 22050
    n_fft = 8192
    f, t, Z = signal.stft(mono, fs=sr, nperseg=n_fft, noverlap=n_fft // 2)
    mag = np.abs(Z)
    band = (f > 80) & (f < 2000)
    fb = f[band]
    mb = mag[band]
    devs, weights = [], []
    for j in range(mb.shape[1]):
        col = mb[:, j]
        if col.max() < 1e-6:
            continue
        thr = col.max() * 0.1
        pk, _ = signal.find_peaks(col, height=thr, distance=3)
        for k in pk[:12]:
            if 0 < k < len(col) - 1:
                a, b, c = np.log(col[k - 1] + 1e-12), np.log(col[k] + 1e-12), np.log(col[k + 1] + 1e-12)
                d = 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
                freq = fb[k] + d * (fb[1] - fb[0])
                if freq <= 0:
                    continue
                midi = 69 + 12 * np.log2(freq / 440.0)
                devs.append((midi - np.round(midi)) * 100.0)
                weights.append(col[k])
    if not devs:
        return 0.0
    ang = np.asarray(devs) / 100.0 * 2 * np.pi
    w = np.asarray(weights)
    c = np.sum(w * np.cos(ang)), np.sum(w * np.sin(ang))
    cents = math.atan2(c[1], c[0]) / (2 * np.pi) * 100.0
    return float(np.clip(cents, -50, 50))


def chroma(x, sr, tuning_cents=0.0, fmin=55.0, fmax=5000.0, bass_weight=1.5, max_seconds=180):
    mono = to_mono(x)[: int(sr * max_seconds)]
    tsr = 11025
    m = resample(mono, sr, tsr)[:, 0]
    n_fft = 4096
    f, t, Z = signal.stft(m, fs=tsr, nperseg=n_fft, noverlap=n_fft * 3 // 4)
    mag = np.log1p(100 * np.abs(Z))
    ref = 440.0 * 2 ** (tuning_cents / 1200)
    ch = np.zeros((12, mag.shape[1]))
    bass = np.zeros((12, mag.shape[1]))
    valid = (f >= fmin) & (f <= fmax)
    midi = 69 + 12 * np.log2(np.maximum(f, 1e-6) / ref)
    pcs = np.mod(np.round(midi), 12).astype(int)
    for pc in range(12):
        sel = valid & (pcs == pc)
        ch[pc] = mag[sel].sum(axis=0)
        selb = sel & (f < 260)
        bass[pc] = mag[selb].sum(axis=0)
    total = ch.sum(axis=1) + bass_weight * bass.sum(axis=1) * (ch.sum() / (bass.sum() + 1e-9)) * 0.5
    return total / (total.max() + 1e-9), ch


def detect_key(x, sr, tuning_cents=None) -> KeyResult:
    if tuning_cents is None:
        tuning_cents = detect_tuning(x, sr)
    prof, _ = chroma(x, sr, tuning_cents)
    scores = []
    for root in range(12):
        for scale, kp in (("Major", KK_MAJOR), ("Minor", KK_MINOR)):
            r = np.corrcoef(prof, np.roll(kp, root))[0, 1]
            scores.append((root, scale, float(r)))
    scores.sort(key=lambda s: -s[2])
    best = scores[0]
    conf = float(np.clip((best[2] - scores[1][2]) * 5 + best[2] * 0.5, 0, 1))
    return KeyResult(best[0], best[1], conf, scores[:5])


# --------------------------------------------------------------------------- tempo
def onset_envelope(x, sr, hop=256):
    mono = to_mono(x)
    tsr = 22050
    m = resample(mono, sr, tsr)[:, 0]
    n_fft = 1024
    f, t, Z = signal.stft(m, fs=tsr, nperseg=n_fft, noverlap=n_fft - hop)
    mag = np.log1p(10 * np.abs(Z))
    # weight low end a bit more (kicks/808) and highs (hats)
    flux = np.maximum(np.diff(mag, axis=1), 0)
    w = np.ones(len(f))
    w[f < 150] = 2.0
    env = (flux * w[:, None]).sum(axis=0)
    env = env - uniform_filter1d(env, size=int(tsr / hop * 0.5) + 1)
    env = np.maximum(env, 0)
    return env / (env.max() + 1e-9), tsr / hop


def detect_tempo(x, sr, bpm_min=60, bpm_max=180):
    env, fps = onset_envelope(x, sr)
    if len(env) < fps * 4:
        return 0.0, [], 0.0
    n = len(env)
    ac = np.correlate(env, env, mode="full")[n - 1:]
    ac /= ac[0] + 1e-9
    lags = np.arange(len(ac))
    bpms = 60 * fps / np.maximum(lags, 1)
    valid = (bpms >= bpm_min) & (bpms <= bpm_max)
    # log-gaussian prior centred on 100 BPM (common hip-hop zone), wide
    prior = np.exp(-0.5 * (np.log2(np.maximum(bpms, 1) / 100.0) / 0.9) ** 2)
    score = np.where(valid, ac * prior, 0)
    # reinforce with harmonics (2x lag)
    for mult in (2, 4):
        idx = np.clip(lags * mult, 0, len(ac) - 1)
        score += np.where(valid, ac[idx] * prior * 0.5 / mult, 0)
    best_lag = int(np.argmax(score))
    if best_lag <= 0:
        return 0.0, [], 0.0
    # refine with parabolic interpolation
    if 1 <= best_lag < len(score) - 1:
        a, b, c = score[best_lag - 1], score[best_lag], score[best_lag + 1]
        d = 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
    else:
        d = 0.0
    bpm = 60 * fps / (best_lag + d)
    # phase: align a comb to the onset envelope
    period = fps * 60 / bpm
    best_off, best_val = 0.0, -1
    for off in np.linspace(0, period, 48, endpoint=False):
        idx = np.round(np.arange(off, n, period)).astype(int)
        idx = idx[idx < n]
        v = env[idx].sum()
        if v > best_val:
            best_val, best_off = v, off
    alts = sorted({round(bpm / 2, 2), round(bpm * 2, 2)} - {round(bpm, 2)})
    return float(round(bpm, 2)), [a for a in alts if 40 <= a <= 240], float(best_off / fps)


def analyze_beat(x, sr) -> BeatAnalysis:
    res = BeatAnalysis()
    res.duration_s = len(x) / sr
    res.tuning_cents = detect_tuning(x, sr)
    res.a4_hz = round(440.0 * 2 ** (res.tuning_cents / 1200), 2)
    k = detect_key(x, sr, res.tuning_cents)
    res.key = dict(root=k.root, scale=k.scale, name=k.name, confidence=k.confidence,
                   relative=k.relative, candidates=[(key_name(r, s), round(v, 3)) for r, s, v in k.candidates])
    res.bpm, res.bpm_alternatives, res.beat_offset_s = detect_tempo(x, sr)
    res.lufs = integrated_lufs(x, sr)
    res.peak_db = float(dsp.lin_to_db(np.max(np.abs(x)))) if len(x) else -120
    res.band_energy_db = band_energies(x, sr)
    be = res.band_energy_db
    res.vocal_pocket_density = float(be.get("presence (2k-5k)", -30) - be.get("bass (60-250)", -10))
    return res
