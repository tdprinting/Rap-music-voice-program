"""Ad-lib library: save ad-libs (and producer tags / FX one-shots) once, drop them anywhere.

The library lives in %APPDATA%/SIB Studio Mixer/adlibs as WAV + JSON so both artists can
share it (Export pack / Import pack zips it up). Ad-libs can be "styled" when created:
classic ad-lib chain, phone, pitched up or down (chipmunk / demon), reversed, stuttered,
or with a delay throw printed in.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
import zipfile
import numpy as np

from . import dsp
from .audio_io import save_audio, load_audio, to_stereo
from .vocal_edit import detect_phrases

CATEGORIES = ["Hype", "Yeah / Uh / Ay", "Laughs", "Shouts", "Whispers", "Signature tag", "Producer tag",
              "Phrases", "FX", "Other"]
STYLES = ["As recorded", "Ad-lib chain", "Phone / radio", "Pitch up (chipmunk)", "Pitch down (demon)",
          "Octave stack", "Reverse", "Stutter", "Delay throw", "Big reverb"]


def library_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "SIB Studio Mixer", "adlibs")
    os.makedirs(d, exist_ok=True)
    return d


def _meta_path(aid):
    return os.path.join(library_dir(), f"{aid}.json")


def _wav_path(aid):
    return os.path.join(library_dir(), f"{aid}.wav")


def style_audio(x, sr, style, bpm=90.0, key_ctx=None):
    """Print an ad-lib style into the audio."""
    from .effects import process_chain
    from .presets import chain_adlib
    from .pitch import pitch_shift_vocal
    x = to_stereo(dsp.as2d(x))
    tail = np.zeros((int(1.5 * sr), 2), np.float32)
    if style == "Ad-lib chain":
        chain = [e for e in chain_adlib() if e["id"] not in ("breath_control", "sib_tune")]
        return process_chain(np.vstack([x, tail]), sr, chain, dict(bpm=bpm))
    if style == "Phone / radio":
        return dsp.telephone(x, sr, 450, 3200, 8)
    if style == "Pitch up (chipmunk)":
        return pitch_shift_vocal(x, sr, 7, formant=3)
    if style == "Pitch down (demon)":
        return dsp.saturation(pitch_shift_vocal(x, sr, -7, formant=-3), sr, 6, "tube", 0.4)
    if style == "Octave stack":
        lo = pitch_shift_vocal(x, sr, -12, formant=-1)
        return (x + lo * 0.6).astype(np.float32)
    if style == "Reverse":
        rv = dsp.reverb(np.vstack([x, tail]), sr, 1.8, 10, 0.4, 0.8, 1.2, 0.5)
        return rv[::-1].copy()
    if style == "Stutter":
        n = len(x)
        st = dsp.stutter(x, sr, bpm, "1/16")
        return np.vstack([st[: n // 2], x]).astype(np.float32)
    if style == "Delay throw":
        return dsp.delay(np.vstack([x, tail, tail]), sr, None, bpm, "1/4", 0.5, 0.6, True)
    if style == "Big reverb":
        return dsp.reverb(np.vstack([x, tail, tail]), sr, 3.0, 20, 0.4, 0.9, 1.3, 0.35)
    return x


def prepare(x, sr, trim=True, normalize_db=-3.0):
    x = to_stereo(dsp.as2d(x))
    if trim:
        ph = detect_phrases(x, sr, -45, 400, 40, 15)
        if ph:
            x = x[ph[0][0]: ph[-1][1]]
    x = dsp.apply_fade(x, int(0.003 * sr), int(0.015 * sr), "linear")
    if normalize_db is not None:
        x = dsp.normalize_peak(x, normalize_db)
    return x.astype(np.float32)


def save_adlib(x, sr, name, category="Hype", style="As recorded", tags="", bpm=90.0, author="",
               trim=True, normalize=True):
    aid = uuid.uuid4().hex[:12]
    y = prepare(x, sr, trim, -3.0 if normalize else None)
    y = style_audio(y, sr, style, bpm)
    y = prepare(y, sr, trim=False, normalize_db=-3.0 if normalize else None)
    save_audio(_wav_path(aid), y, sr, 24)
    meta = dict(id=aid, name=name or "Ad-lib", category=category, style=style, tags=tags, author=author,
                sr=sr, length_s=round(len(y) / sr, 3), created=time.strftime("%Y-%m-%d %H:%M"), bpm=bpm,
                favorite=False, uses=0)
    with open(_meta_path(aid), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    return meta


def list_adlibs(search="", category=None):
    out = []
    d = library_dir()
    for fn in os.listdir(d):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as fh:
                m = json.load(fh)
        except Exception:
            continue
        if not os.path.exists(_wav_path(m["id"])):
            continue
        if category and category != "All" and m.get("category") != category:
            continue
        hay = f"{m.get('name', '')} {m.get('tags', '')} {m.get('category', '')} {m.get('author', '')}".lower()
        if search and search.lower() not in hay:
            continue
        out.append(m)
    out.sort(key=lambda m: (not m.get("favorite"), m.get("category", ""), m.get("name", "").lower()))
    return out


def load_adlib(aid, sr):
    a, _ = load_audio(_wav_path(aid), target_sr=sr)
    return a


def update_meta(aid, **kw):
    p = _meta_path(aid)
    with open(p, encoding="utf-8") as fh:
        m = json.load(fh)
    m.update(kw)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1)
    return m


def delete_adlib(aid):
    for p in (_meta_path(aid), _wav_path(aid)):
        if os.path.exists(p):
            os.remove(p)


def export_pack(path, ids=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for m in list_adlibs():
            if ids and m["id"] not in ids:
                continue
            z.write(_meta_path(m["id"]), f"{m['id']}.json")
            z.write(_wav_path(m["id"]), f"{m['id']}.wav")
    return path


def import_pack(path):
    n = 0
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            base = os.path.basename(name)
            if not base or "/" in base or base.startswith("."):
                continue
            if base.endswith((".json", ".wav")):
                with z.open(name) as src, open(os.path.join(library_dir(), base), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                n += base.endswith(".json")
    return n


def insert_into_track(project, track, meta, pos, snap=True, every_bars=0, until=None, alternate_pan=False):
    """Drop an ad-lib onto a track at pos (optionally every N bars up to `until`).
    -> list of clips created."""
    from .project import Clip
    a = load_adlib(meta["id"], project.sr)
    fid = project.pool.add_array(a, meta["name"])
    positions = [pos]
    if every_bars and until and until > pos:
        step = int(project.samples_per_beat() * project.time_sig * every_bars)
        positions = list(range(int(pos), int(until), max(step, 1)))
    clips = []
    for i, p in enumerate(positions):
        s = project.snap(p, 4) if snap else int(p)
        c = Clip(fid, max(0, s), len(a), 0, name=meta["name"])
        if alternate_pan:
            c.gain_db = 0.0
        track.clips.append(c)
        clips.append(c)
    try:
        update_meta(meta["id"], uses=int(meta.get("uses", 0)) + len(clips))
    except Exception:
        pass
    project.invalidate(track)
    return clips


# ---------------------------------------------------------------- starter FX one-shots
def _synth_fx(sr):
    rng = np.random.default_rng(11)
    out = {}
    n = int(sr * 3.5)
    t = np.arange(n) / sr
    noise = rng.standard_normal(n)
    sweep = dsp.filter_sweep(noise * (t / t[-1]) ** 2 * 0.5, sr, "bandpass", 300, 9000, 2.0)
    out["Riser (2 bars @ 135)"] = ("FX", sweep)
    dn = dsp.filter_sweep(noise[: int(sr * 2.5)] * np.linspace(1, 0, int(sr * 2.5)) ** 2 * 0.5, sr, "lowpass", 12000, 200, 0.8)
    out["Downlifter"] = ("FX", dn)
    m = int(sr * 2.0)
    tt = np.arange(m) / sr
    f = 120 * np.exp(-tt * 1.6) + 30
    sub = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-tt * 1.2)
    out["808 sub drop"] = ("FX", sub * 0.9)
    k = int(sr * 2.5)
    tk = np.arange(k) / sr
    boom = np.sin(2 * np.pi * (45 + 80 * np.exp(-tk * 25)) * tk) * np.exp(-tk * 2.5)
    crash = dsp.highpass(rng.standard_normal(k) * np.exp(-tk * 3), sr, 3000, 2)[:, 0] * 0.3
    imp = dsp.reverb(np.stack([boom + crash, boom + crash], 1).astype(np.float32), sr, 2.5, 0, 0.4, 0.9, 1.2, 0.4)
    out["Impact / boom"] = ("FX", imp)
    rv = dsp.reverb(np.stack([crash, crash], 1).astype(np.float32), sr, 2.5, 0, 0.3, 0.9, 1.2, 0.8)[::-1]
    out["Reverse swell"] = ("FX", rv)
    lz = int(sr * 0.6)
    tl = np.arange(lz) / sr
    laser = np.sin(2 * np.pi * np.cumsum(3000 * np.exp(-tl * 8) + 200) / sr) * np.exp(-tl * 4) * 0.5
    out["Laser zap"] = ("FX", laser)
    return out


def ensure_starter_pack(sr=44100):
    """Fill an empty library with a few generated producer FX so it isn't blank on day one."""
    flag = os.path.join(library_dir(), ".starter_done")
    if os.path.exists(flag):
        return 0
    n = 0
    for name, (cat, audio) in _synth_fx(sr).items():
        a = dsp.as2d(np.asarray(audio, np.float32))
        aid = "sibfx_" + "".join(ch for ch in name.lower() if ch.isalnum())[:20]
        y = dsp.normalize_peak(to_stereo(a), -3.0)
        save_audio(_wav_path(aid), y, sr, 24)
        with open(_meta_path(aid), "w", encoding="utf-8") as fh:
            json.dump(dict(id=aid, name=name, category=cat, style="Generated", tags="sib fx starter",
                           author="SIB", sr=sr, length_s=round(len(y) / sr, 2),
                           created=time.strftime("%Y-%m-%d %H:%M"), favorite=False, uses=0), fh, indent=1)
        n += 1
    open(flag, "w").close()
    return n
