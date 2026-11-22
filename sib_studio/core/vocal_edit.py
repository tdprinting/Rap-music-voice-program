"""Vocal editing tools: strip silence, align doubles to the lead, phrase quantize, beat tools."""
from __future__ import annotations

import copy
import numpy as np
from scipy.ndimage import uniform_filter1d, binary_closing

from . import dsp
from .project import Project, Track, Clip, uid, split_clip, delete_range


def detect_phrases(x, sr, threshold_db=-42.0, min_silence_ms=220.0, min_phrase_ms=100.0, pad_ms=40.0):
    """-> list of (start, end) sample ranges where someone is actually rapping/singing."""
    x = dsp.as2d(x)
    if not len(x):
        return []
    hop = max(1, int(0.005 * sr))
    mono = np.max(np.abs(x), axis=1)
    n = int(np.ceil(len(mono) / hop))
    env = np.pad(mono, (0, n * hop - len(mono))).reshape(n, hop).max(axis=1)
    env_db = dsp.lin_to_db(uniform_filter1d(env, 4))
    peak = np.percentile(env_db, 99) if len(env_db) else 0
    thr = max(threshold_db, peak - 50)
    on = env_db > thr
    gap = max(1, int(min_silence_ms / 5))
    on = binary_closing(on, structure=np.ones(gap)) if len(on) > gap else on
    edges = np.flatnonzero(np.diff(np.concatenate([[0], on.astype(int), [0]])))
    pad = int(pad_ms / 1000 * sr)
    out = []
    for a, b in zip(edges[::2], edges[1::2]):
        if (b - a) * 5 < min_phrase_ms:
            continue
        out.append((int(max(0, a * hop - pad)), int(min(len(x), b * hop + pad))))
    # merge overlaps created by padding
    merged = []
    for a, b in out:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def strip_silence(project: Project, track: Track, threshold_db=-42.0, min_silence_ms=220.0, pad_ms=40.0,
                  fade_ms=8.0):
    """Split every clip on the track into phrase clips and drop the silence between them
    (Audition: Delete Silence / Strip Silence). Non-destructive. -> number of phrases."""
    sr = project.sr
    new = []
    for c in list(track.clips):
        if c.muted:
            new.append(c)
            continue
        audio = project.render_clip(c)
        ph = detect_phrases(audio, sr, threshold_db, min_silence_ms, 100.0, pad_ms)
        if not ph:
            continue
        for a, b in ph:
            nc = copy.deepcopy(c)
            nc.id = uid()
            nc.start = c.start + a
            nc.src_in = c.src_in + a
            nc.length = b - a
            nc.fade_in = nc.fade_out = int(fade_ms / 1000 * sr)
            new.append(nc)
    count = len(new)
    track.clips = new
    project.invalidate(track)
    return count


def _envelope(x, sr, hop_ms=2.0):
    x = dsp.as2d(x)
    hop = max(1, int(hop_ms / 1000 * sr))
    mono = dsp.highpass(x, sr, 150).mean(axis=1)
    n = len(mono) // hop
    if n <= 0:
        return np.zeros(1)
    e = np.sqrt(np.mean(mono[: n * hop].reshape(n, hop) ** 2, axis=1))
    e = np.log1p(e * 50)
    # emphasise onsets (consonants are what the ear hears as timing)
    on = np.maximum(np.diff(e, prepend=e[0]), 0)
    return e + 2 * on


def align_to_guide(project: Project, guide: Track, target: Track, max_shift_ms=80.0, split_first=True):
    """Line a double / harmony / ad-lib stack up with the lead, phrase by phrase
    (the job VocAlign does). -> list of shifts in ms."""
    sr = project.sr
    if split_first:
        strip_silence(project, target)
    goff, gaudio = project.render_track_dry(guide)
    if not len(gaudio):
        return []
    hop_ms = 2.0
    hop = int(hop_ms / 1000 * sr)
    genv = _envelope(gaudio, sr, hop_ms)
    maxs = int(max_shift_ms / hop_ms)
    shifts = []
    for c in target.clips:
        if c.muted:
            continue
        cenv = _envelope(project.render_clip(c), sr, hop_ms)
        base = (c.start - goff) // hop
        best, best_s = 0, -1e18
        for s in range(-maxs, maxs + 1):
            a = base + s
            if a < 0 or a + len(cenv) > len(genv):
                seg = np.zeros(len(cenv))
                lo, hi = max(0, a), min(len(genv), a + len(cenv))
                if hi > lo:
                    seg[lo - a: hi - a] = genv[lo:hi]
            else:
                seg = genv[a: a + len(cenv)]
            sc = float(np.dot(seg - seg.mean(), cenv - cenv.mean()))
            if sc > best_s:
                best_s, best = sc, s
        c.start = int(max(0, c.start + best * hop))
        shifts.append(best * hop_ms)
    project.invalidate(target)
    return shifts


def quantize_phrases(project: Project, track: Track, division=4, strength=0.6, max_ms=120.0, split_first=True):
    """Snap the start of each phrase toward the beat grid (division=4 -> 1/16 notes).
    strength 0..1 keeps some human feel. -> list of moves in ms."""
    sr = project.sr
    if split_first:
        strip_silence(project, track)
    step = project.samples_per_beat() / division
    off = project.beat_offset_s * sr
    moves = []
    for c in track.clips:
        if c.muted:
            continue
        a = project.render_clip(c)
        env = np.max(np.abs(a), axis=1)
        thr = max(np.max(env) * 0.15, 1e-4)
        idx = np.flatnonzero(env > thr)
        if not len(idx):
            continue
        onset = c.start + int(idx[0])
        target = round((onset - off) / step) * step + off
        mv = (target - onset) * strength
        if abs(mv) / sr * 1000 > max_ms:
            continue
        c.start = max(0, int(c.start + mv))
        moves.append(float(mv / sr * 1000))
    project.invalidate(track)
    return moves


def change_beat_tempo(project: Project, track: Track, new_bpm: float):
    """Time-stretch the beat clips so the song runs at new_bpm (key stays the same)."""
    ratio = new_bpm / max(project.bpm, 1e-6)
    for c in track.clips:
        seg = project.pool.get(c.file_id)[c.src_in: c.src_in + c.length]
        y = dsp.time_stretch(seg, project.sr, ratio)
        c.file_id = project.pool.add_array(y, f"{c.name}_{new_bpm:.0f}bpm")
        c.src_in, c.length = 0, len(y)
        c.start = int(c.start / ratio)
        c.fade_in = min(c.fade_in, c.length // 2)
        c.fade_out = min(c.fade_out, c.length // 2)
    project.beat_offset_s /= ratio
    project.bpm = float(new_bpm)
    project.invalidate()


def transpose_beat(project: Project, track: Track, semitones: float):
    """Pitch-shift the beat (keeps tempo) and move the project key with it."""
    for c in track.clips:
        seg = project.pool.get(c.file_id)[c.src_in: c.src_in + c.length]
        y = dsp.pitch_shift_general(seg, project.sr, semitones)
        c.file_id = project.pool.add_array(y, f"{c.name}_{semitones:+.0f}st")
        c.src_in, c.length = 0, len(y)
    project.key_root = int(round(project.key_root + semitones)) % 12
    project.invalidate()


def beat_drop(project: Project, beat_tracks, start, end):
    """Cut the beat out for the selected range (vocals keep going) - the classic drop."""
    sr = project.sr
    for t in beat_tracks:
        delete_range(project, [t], start, end)
        for c in t.clips:
            if c.end == start:
                c.fade_out = max(c.fade_out, int(0.01 * sr))
            if c.start == end:
                c.fade_in = max(c.fade_in, int(0.005 * sr))
        project.invalidate(t)
