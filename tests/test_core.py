"""Engine tests on synthetic audio (run: python -m pytest -q tests)."""
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
import numpy as np
from scipy import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sib_studio.core import analysis, pitch, breath, dsp, automatch, audition, presets
from sib_studio.core.audio_io import save_audio, load_audio, resample
from sib_studio.core.effects import REGISTRY, process_chain, new_instance
from sib_studio.core.project import Project, UndoStack, delete_range, split_at, process_range

SR = 44100


def tone(f0):
    ph = 2 * np.pi * np.cumsum(f0) / SR
    return sum((0.6 / k) * np.sin(k * ph) for k in range(1, 12))


def make_vocal(notes=(57.3, 60.4, 59.7, 62.2, 64.35), seed=0):
    rng = np.random.default_rng(seed)
    parts, breaths = [], []
    sos = signal.butter(2, [800, 4000], "bandpass", fs=SR, output="sos")
    for m in notes:
        n = int(0.5 * SR)
        t = np.arange(n) / SR
        f = 440 * 2 ** ((m - 69) / 12) * (1 + 0.004 * np.sin(2 * np.pi * 5 * t))
        env = np.minimum(1, np.minimum(t / 0.02, (0.5 - t) / 0.03))
        parts.append(0.3 * tone(f) * env)
        gap = np.zeros(int(0.45 * SR))
        bn = signal.sosfilt(sos, rng.standard_normal(int(0.3 * SR))) * np.hanning(int(0.3 * SR)) * 0.03
        s = sum(len(p) for p in parts) + int(0.07 * SR)
        gap[int(0.07 * SR): int(0.07 * SR) + len(bn)] = bn
        breaths.append((s, s + len(bn)))
        parts.append(gap)
    y = np.concatenate(parts)
    return (y + rng.standard_normal(len(y)) * 5e-4).astype(np.float32), breaths


def make_beat(bpm=90, bars=8):
    beat = 60 / bpm
    n = int(bars * 4 * beat * SR)
    y = np.zeros(n)
    t = np.arange(n) / SR
    chords = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]
    for b in range(bars):
        ch = chords[b % 4]
        s, e = int(b * 4 * beat * SR), int((b + 1) * 4 * beat * SR)
        tt = t[s:e] - t[s]
        for m in ch:
            f = 440 * 2 ** ((m - 69) / 12)
            y[s:e] += 0.08 * np.sin(2 * np.pi * f * tt)
        y[s:e] += 0.25 * np.sin(2 * np.pi * 440 * 2 ** ((ch[0] - 93) / 12) * tt) * np.exp(-tt * 1.5)
    for k in range(bars * 4):
        s = int(k * beat * SR)
        L = int(0.15 * SR)
        tt = np.arange(L) / SR
        y[s:s + L] += 0.6 * np.sin(2 * np.pi * (50 + 100 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 20)
    return np.stack([y, y], 1).astype(np.float32)


def test_key_tempo_tuning():
    b = make_beat()
    r = analysis.analyze_beat(b, SR)
    assert r.key["name"] in ("A Minor", "C Major")
    assert abs(r.bpm - 90) < 2 or abs(r.bpm - 180) < 4 or abs(r.bpm - 45) < 2
    detuned = resample(b, SR, int(SR / 2 ** (25 / 1200)))
    assert abs(analysis.detect_tuning(detuned, SR) - 25) < 6


def test_autotune_snaps_to_scale():
    v, _ = make_vocal()
    y = pitch.autotune(v, SR, root=9, scale="Minor", retune_ms=5, humanize=0, flex_cents=0)
    tr = pitch.detect_pitch(y, SR)
    m = tr.midi()
    for i, target in enumerate([57, 60, 60, 62, 64]):
        seg = m[(tr.times > i * 0.95 + 0.15) & (tr.times < i * 0.95 + 0.4)]
        assert abs(np.nanmedian(seg) - target) < 0.12, (i, np.nanmedian(seg))


def test_breath_detection():
    v, truth = make_vocal()
    regs = breath.detect_breaths(v, SR)
    assert len(regs) == len(truth)
    for r, (a, b) in zip(regs, truth):
        assert abs(r.start - a) < 0.05 * SR and abs(r.end - b) < 0.05 * SR
    y = breath.apply_breath_control(v, SR, regs, "reduce", -20)
    a, b = truth[0]
    assert np.sqrt(np.mean(y[a + 2000:b - 2000] ** 2)) < np.sqrt(np.mean(v[a + 2000:b - 2000] ** 2)) * 0.2


def test_every_effect_runs():
    v, _ = make_vocal()
    x = np.stack([v, v], 1)[: SR * 3]
    ctx = dict(bpm=90, key_root=9, key_scale="Minor", a4=440.0, vocal_bus=x)
    for eid, d in REGISTRY.items():
        y = process_chain(x, SR, [new_instance(eid)], ctx)
        assert y.shape == x.shape and np.isfinite(y).all(), eid


def test_presets_and_loudness():
    v, _ = make_vocal()
    x = np.stack([v, v], 1)
    for name in presets.STYLES:
        y = process_chain(x, SR, presets.style_chain(name), dict(bpm=90))
        assert np.isfinite(y).all(), name
    m = process_chain(np.tile(x, (3, 1)), SR, presets.chain_master())
    assert abs(analysis.integrated_lufs(m, SR) + 10) < 1.0
    assert np.max(np.abs(m)) <= dsp.db_to_lin(-1.0) + 1e-3


def test_project_roundtrip_automatch_and_audition():
    with tempfile.TemporaryDirectory() as d:
        save_audio(os.path.join(d, "beat.wav"), make_beat(), SR)
        v, _ = make_vocal()
        save_audio(os.path.join(d, "v.wav"), np.tile(v, 3), SR)
        p = Project("Test", SR, folder=os.path.join(d, "proj"))
        bt = p.add_track("Beat", "beat")
        p.import_audio_to_track(os.path.join(d, "beat.wav"), bt)
        lead = p.add_track("Lead", "vocal", "Lead")
        p.import_audio_to_track(os.path.join(d, "v.wav"), lead)
        rep = automatch.auto_match(p)
        assert p.key_scale in ("Minor", "Major") and rep["vocals"]
        assert "SIB Auto-Match" in automatch.format_report(rep)
        undo = UndoStack(p)
        undo.push("edit")
        n = len(p.track(lead.id).clips)
        split_at(p, [lead], SR)
        delete_range(p, [lead], SR // 2, SR)
        process_range(p, lead, 0, SR // 4, lambda a: a * 0)
        undo.do_undo()
        assert len(p.track(lead.id).clips) == n
        mix = p.mixdown()
        assert np.isfinite(mix).all() and mix.shape[1] == 2
        path = p.save()
        q = Project.open(path)
        assert len(q.tracks) == 2 and q.tracks[1].fx
        ses = audition.export_session(q, os.path.join(d, "aud"), "both", True)
        root = ET.parse(ses).getroot()
        assert root.tag == "sesx" and len(root.findall(".//audioTrack")) == 4
        for f in root.findall(".//file"):
            assert os.path.exists(f.get("absolutePath"))


def test_audio_io_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        x = (np.random.default_rng(1).standard_normal((SR, 2)) * 0.1).astype(np.float32)
        for bd in (16, 24, "float"):
            pth = os.path.join(d, f"t{bd}.wav")
            save_audio(pth, x, SR, bd)
            y, sr = load_audio(pth)
            assert sr == SR and y.shape == x.shape and np.max(np.abs(y - x)) < 1e-3


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
