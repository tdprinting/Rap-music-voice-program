"""Take Coach: scores each vocal take and explains how to make the next one better.

Looks at what engineers listen for when picking takes: level and clipping, background noise,
timing against the beat grid, pitch steadiness (for sung parts), pops, harsh esses,
breath count and how even the performance is.
"""
from __future__ import annotations

import numpy as np

from . import dsp, analysis
from .pitch import detect_pitch, melodic_score
from .breath import detect_breaths


def analyze_take(x, sr, bpm=None, beat_offset_s=0.0, a4=440.0, abs_start=0):
    x = dsp.as2d(x)
    dur = len(x) / sr
    out = dict(duration_s=dur)
    tips = []
    score = 100.0
    if dur < 0.5:
        return dict(score=0, metrics=out, tips=["Take is too short to judge."])
    mono = x.mean(axis=1)
    peak = float(np.max(np.abs(x)))
    clipped = int(np.sum(np.abs(x) >= 0.995))
    out["peak_db"] = float(dsp.lin_to_db(peak))
    out["clipped_samples"] = clipped
    if clipped > max(3, len(x) * 1e-5):
        pen = min(30, 10 + clipped / len(x) * 1e5)
        score -= pen
        tips.append(f"Clipping ({clipped} samples) - turn the mic gain down 3-6 dB or step back from the mic.")
    elif out["peak_db"] < -20:
        score -= 8
        tips.append("Recorded quietly - raise the input gain so peaks land around -12 dBFS.")
    lv = dsp.block_level_db(mono, 1024, "rms")
    act = lv[lv > np.percentile(lv, 30)] if len(lv) > 10 else lv
    floor = float(np.percentile(lv, 5)) if len(lv) else -90
    speech = float(np.percentile(act, 90)) if len(act) else -20
    snr = speech - floor
    out.update(noise_floor_db=floor, snr_db=snr, lufs=analysis.integrated_lufs(x, sr))
    if snr < 35:
        score -= min(20, (35 - snr) * 0.8)
        tips.append(f"Background noise is high (only {snr:.0f} dB under your voice) - kill fans/AC, use a closer mic position.")
    dyn = float(np.percentile(act, 95) - np.percentile(act, 20)) if len(act) else 0
    out["dynamics_db"] = dyn
    if dyn > 24:
        score -= 6
        tips.append("Volume jumps a lot between words - keep a steady distance from the mic.")
    # timing against the grid
    env, fps = analysis.onset_envelope(x, sr)
    if bpm and len(env) > fps:
        thr = np.percentile(env, 90)
        peaks = [i for i in range(1, len(env) - 1) if env[i] > thr and env[i] >= env[i - 1] and env[i] >= env[i + 1]]
        if len(peaks) >= 4:
            times = np.array(peaks) / fps + abs_start / sr
            step = 60.0 / bpm / 4
            ph = ((times - beat_offset_s) / step) % 1.0
            dev = np.minimum(ph, 1 - ph) * step * 1000
            out["timing_ms"] = float(np.median(dev))
            out["syllables_per_sec"] = len(peaks) / dur
            if out["timing_ms"] > 28:
                score -= min(18, (out["timing_ms"] - 28) * 0.6)
                tips.append(f"Timing drifts ~{out['timing_ms']:.0f} ms off the beat - try recording this part in shorter punches, "
                            "or use Edit > Quantize phrases.")
    # pitch
    tr = detect_pitch(x[: sr * 120], sr)
    mel = melodic_score(tr, a4)
    out["melodic"] = mel
    m = tr.midi(a4)
    v = ~np.isnan(m)
    if v.sum() > 20:
        drift = float(np.mean(np.abs(m[v] - np.round(m[v]))) * 100)
        out["pitch_drift_cents"] = drift
        if mel > 0.5 and drift > 22:
            score -= min(15, (drift - 22) * 0.5)
            tips.append(f"Sung notes wander ~{drift:.0f} cents - SIB Tune will fix it, but a tighter take sounds more natural.")
    # pops & esses
    low = dsp.lowpass(x, sr, 120, order=4).mean(axis=1)
    lv_low = dsp.block_level_db(low, 512, "peak")
    lv_all = dsp.block_level_db(mono, 512, "peak")
    pops = int(np.sum((lv_low - lv_all > -3) & (lv_low > speech - 6)))
    out["pops"] = pops
    if pops > dur / 6:
        score -= 6
        tips.append("Lots of P/B pops - use a pop filter or angle the mic slightly off-axis.")
    hi = dsp.highpass(x, sr, 5500, order=4).mean(axis=1)
    sib = float(np.percentile(dsp.block_level_db(hi, 512, "peak"), 98) - speech)
    out["sibilance_db"] = sib
    if sib > -2:
        score -= 5
        tips.append("Esses are sharp - the De-Esser will handle it; for the next take, turn slightly off the mic on S words.")
    br = detect_breaths(x, sr, 0.5, track=tr)
    out["breaths"] = len(br)
    out["breaths_per_min"] = len(br) / (dur / 60)
    if out["breaths_per_min"] > 18:
        tips.append("Loud breathing between bars - Breath Control will tame it; plan breath spots in your lyrics.")
    if not tips:
        tips.append("Clean, steady take. 🔥")
    out["score"] = float(np.clip(score, 0, 100))
    return dict(score=out["score"], metrics=out, tips=tips)


def rank_takes(project, track):
    """Score every clip on a track. -> list of (clip, report), best first."""
    res = []
    for c in track.clips:
        a = project.render_clip(c)
        rep = analyze_take(a, project.sr, project.bpm, project.beat_offset_s, project.a4, c.start)
        res.append((c, rep))
    res.sort(key=lambda r: -r[1]["score"])
    return res


def overlapping(c1, c2):
    return c1.start < c2.end and c2.start < c1.end
