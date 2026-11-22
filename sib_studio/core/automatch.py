"""Auto-Match: listen to the beat, analyse each vocal, and set up every vocal chain to fit.

What it does:
  1. Beat analysis -> key/scale, real tuning (A4 Hz), BPM + grid, loudness, spectral density.
  2. Vocal analysis -> pitch range, rapped vs sung, sibilance frequency, noise floor,
     mud/boom, brightness, level.
  3. Writes settings: SIB Tune key/scale/A4 + retune style, HPF, EQ moves relative to a
     reference rap-vocal tone curve and to how crowded the beat is in the vocal range,
     de-esser frequency, gate/noise settings, compressor threshold, BPM-synced delay/reverb,
     Beat Carve on the beat, vocal level vs. beat loudness, optional timing nudge.
"""
from __future__ import annotations

import numpy as np

from . import analysis, dsp
from .pitch import detect_pitch, vocal_range, melodic_score, detect_key_from_vocal
from .analysis import key_name
from . import presets
from .effects import new_instance, REGISTRY
from .audio_io import to_mono

# Rough tonal balance of a finished, radio-ready rap lead (dB relative to total, Welch PSD bands)
REFERENCE_VOCAL_BANDS = {
    "bass (60-250)": -9.0, "low-mid (250-500)": -8.0, "mid (500-2k)": -4.0,
    "presence (2k-5k)": -10.0, "brilliance (5k-10k)": -17.0, "air (10k-20k)": -26.0,
}


def analyze_vocal(x, sr, a4=440.0):
    tr = detect_pitch(x, sr)
    rng = vocal_range(tr)
    mel = melodic_score(tr, a4)
    bands = analysis.band_energies(x, sr)
    mono = to_mono(x)
    lv = dsp.block_level_db(mono, 512, "rms")
    active = lv[lv > -60]
    noise_floor = float(np.percentile(lv, 8)) if len(lv) else -80
    speech = float(np.percentile(active, 90)) if len(active) else -20
    # sibilance peak: spectrum of the hissiest frames between 4 and 11 kHz
    sib_freq = 6500.0
    try:
        from scipy import signal
        f, t, Z = signal.stft(mono[: sr * 120], fs=sr, nperseg=2048, noverlap=1024)
        mag = np.abs(Z)
        hi = (f > 4000) & (f < 11000)
        ratio = mag[hi].sum(axis=0) / (mag.sum(axis=0) + 1e-9)
        top = ratio > np.percentile(ratio, 95)
        if top.any():
            spec = mag[hi][:, top].mean(axis=1)
            sib_freq = float(f[hi][int(np.argmax(spec))])
    except Exception:
        pass
    vk = detect_key_from_vocal(tr, a4)
    return dict(track=tr, range=rng, melodic=mel, bands=bands, noise_floor_db=noise_floor,
                speech_level_db=speech, sibilance_hz=sib_freq, lufs=analysis.integrated_lufs(x, sr),
                vocal_key=(key_name(vk[0], vk[1]) if vk else None))


def timing_offset(vocal, beat_bpm, beat_offset_s, sr, max_ms=60.0):
    """Best global nudge (ms) to line the vocal's attacks up with the 1/16 grid."""
    env, fps = analysis.onset_envelope(vocal, sr)
    if len(env) < fps * 2 or beat_bpm <= 0:
        return 0.0, 0.0
    step = 60.0 / beat_bpm / 4
    times = np.arange(len(env)) / fps
    best, best_s, base = 0.0, -1, None
    for ms in np.arange(-max_ms, max_ms + 1, 2.0):
        o = ms / 1000.0
        ph = ((times + o - beat_offset_s) / step) % 1.0
        dist = np.minimum(ph, 1 - ph)
        s = float(np.sum(env * np.exp(-(dist / 0.12) ** 2)))
        if abs(ms) < 1e-9:
            base = s
        if s > best_s:
            best_s, best = s, ms
    gain = (best_s - base) / (base + 1e-9) if base else 0.0
    return float(best), float(gain)


def _get(chain, eid):
    for e in chain:
        if e["id"] == eid:
            return e
    return None


def _ensure(chain, eid, after=None, **params):
    e = _get(chain, eid)
    if e is None:
        e = new_instance(eid, **params)
        idx = len(chain)
        if after:
            for i, x in enumerate(chain):
                if x["id"] == after:
                    idx = i + 1
        chain.insert(idx, e)
    else:
        e["params"].update({k: v for k, v in params.items() if k in e["params"]})
    return e


def auto_match(project, style=None, vocal_track_ids=None, prominence_lu=1.5, tune_mode="auto",
               apply_timing=False, carve=True, log=None):
    """Configure the project so the vocals blend with the beat. Returns a report dict."""
    say = log or (lambda m: None)
    sr = project.sr
    report = {"beat": None, "vocals": [], "changes": []}
    beats = [t for t in project.beat_tracks() if t.clips]
    beat_lufs = -14.0
    if beats:
        say("Listening to the beat…")
        off, beat = project.render_track_dry(beats[0])
        ba = analysis.analyze_beat(beat, sr)
        project.bpm = ba.bpm or project.bpm
        project.beat_offset_s = ba.beat_offset_s + off / sr
        project.key_root = ba.key["root"]
        project.key_scale = ba.key["scale"]
        project.a4 = ba.a4_hz
        project.analysis = ba.to_dict()
        beat_lufs = ba.lufs
        report["beat"] = ba.to_dict()
        report["changes"].append(
            f"Beat: {ba.key['name']} (confidence {ba.key['confidence']:.0%}), {ba.bpm:.1f} BPM, "
            f"tuned to A4 = {ba.a4_hz:.1f} Hz ({ba.tuning_cents:+.0f} cents), {ba.lufs:.1f} LUFS")
        if carve:
            for b in beats:
                carve_e = _ensure(b.fx, "beat_carve")
                carve_e["enabled"] = True
                dens = ba.vocal_pocket_density
                depth = float(np.clip(-3.0 - (dens + 20) * 0.15, -7.0, -2.0))
                carve_e["params"].update(depth_db=round(depth, 1))
                project.invalidate(b)
            report["changes"].append("Beat Carve enabled on the beat so the vocal pocket opens when you rap.")
    else:
        report["changes"].append("No beat found - add a beat track first for key/BPM matching.")

    targets = [t for t in project.vocal_tracks() if t.clips and (vocal_track_ids is None or t.id in vocal_track_ids)]
    for t in targets:
        say(f"Analysing {t.name}…")
        off, v = project.render_track_dry(t)
        if not len(v):
            continue
        va = analyze_vocal(v, sr, project.a4)
        tr = va.pop("track")
        info = {k: v_ for k, v_ in va.items()}
        info["name"] = t.name
        changes = []
        chain_style = style or presets.ROLE_STYLE.get(t.role, "Clean Rap Lead")
        if style is not None or not t.fx:
            t.fx = presets.style_chain(chain_style)
            changes.append(f"Loaded '{chain_style}' chain")
        # --- tuning
        tune = _get(t.fx, "sib_tune")
        if tune is not None:
            tp = tune["params"]
            tp.update(key="Auto (from beat)", scale="Auto (from beat)", a4=0.0)
            mode = tune_mode
            if mode == "auto" and style is None:
                mode = "melodic" if va["melodic"] > 0.55 else "rap"
            if mode == "hard":
                tp.update(retune_ms=0.0, humanize=0.0, flex_cents=0.0, amount=100.0)
            elif mode == "melodic":
                tp.update(retune_ms=18.0, humanize=25.0, flex_cents=10.0, amount=100.0)
            elif mode == "rap":
                tp.update(retune_ms=45.0, humanize=30.0, flex_cents=30.0, amount=70.0)
            if mode == "off":
                tune["enabled"] = False
            elif mode in ("hard", "melodic", "rap"):
                tune["enabled"] = True
            if va["range"]:
                tp["fmin"] = float(np.clip(va["range"]["low_hz"] * 0.7, 50, 250))
                tp["fmax"] = float(np.clip(va["range"]["high_hz"] * 1.4, 400, 1500))
            changes.append(f"SIB Tune -> {key_name(project.key_root, project.key_scale)} @ A4={project.a4:.1f} Hz, "
                           f"{mode} style (vocal is {'sung' if va['melodic'] > 0.55 else 'rapped'})")
        # --- EQ relative to reference + beat masking
        eq = _get(t.fx, "eq")
        if eq is not None:
            ep = eq["params"]
            b = va["bands"]
            if va["range"]:
                ep["hpf_freq"] = float(np.clip(va["range"]["low_hz"] * 0.75, 70, 140 if t.role == "Lead" else 220))
            mud = b.get("low-mid (250-500)", -8) - REFERENCE_VOCAL_BANDS["low-mid (250-500)"]
            ep["mud_gain"] = float(np.clip(-1.5 - mud * 0.6, -6, 0))
            pres = b.get("presence (2k-5k)", -10) - REFERENCE_VOCAL_BANDS["presence (2k-5k)"]
            push = 0.0
            if report["beat"]:
                dens = report["beat"]["vocal_pocket_density"]
                push = float(np.clip((dens + 18) * 0.12, 0, 2.0))
            ep["presence_gain"] = float(np.clip(2.0 - pres * 0.6 + push, -2, 6))
            air = b.get("air (10k-20k)", -26) - REFERENCE_VOCAL_BANDS["air (10k-20k)"]
            ep["air_gain"] = float(np.clip(2.5 - air * 0.4, 0, 6))
            boom = b.get("bass (60-250)", -9) - REFERENCE_VOCAL_BANDS["bass (60-250)"]
            ep["low_shelf_gain"] = float(np.clip(-boom * 0.5, -6, 2)) if boom > 2 else 0.0
            changes.append(f"EQ: HPF {ep['hpf_freq']:.0f} Hz, mud {ep['mud_gain']:+.1f} dB, presence "
                           f"{ep['presence_gain']:+.1f} dB, air {ep['air_gain']:+.1f} dB")
        # --- de-esser at the singer's own sibilance
        de = _get(t.fx, "deesser")
        if de is not None:
            de["params"]["freq"] = float(np.clip(va["sibilance_hz"] * 0.85, 4500, 9500))
            changes.append(f"De-esser tuned to {de['params']['freq']:.0f} Hz")
        # --- noise
        nr = _get(t.fx, "noise_reduction")
        if nr is not None:
            snr = va["speech_level_db"] - va["noise_floor_db"]
            nr["enabled"] = snr < 55
            nr["params"]["reduction_db"] = float(np.clip(18 - (snr - 30) * 0.3, 4, 18))
            changes.append(f"Noise floor {va['noise_floor_db']:.0f} dB (SNR {snr:.0f} dB) -> "
                           + (f"noise reduction {nr['params']['reduction_db']:.0f} dB" if nr["enabled"] else "noise reduction off"))
        # --- compressor threshold relative to performance level
        comps = [e for e in t.fx if e["id"] == "compressor"]
        if comps:
            comps[0]["params"]["threshold_db"] = float(np.clip(va["speech_level_db"] - 8, -40, -6))
            if len(comps) > 1:
                comps[1]["params"]["threshold_db"] = float(np.clip(va["speech_level_db"] - 16, -50, -10))
            changes.append(f"Compressor threshold {comps[0]['params']['threshold_db']:.1f} dB (~6 dB GR on loud words)")
        rider = _get(t.fx, "vocal_rider")
        if rider is not None:
            rider["params"]["target_db"] = float(np.clip(va["speech_level_db"] - 4, -30, -10))
        # --- time-based FX locked to tempo
        rev = _get(t.fx, "reverb")
        if rev is not None and project.bpm:
            beat_s = 60.0 / project.bpm
            rev["params"]["decay_s"] = float(np.clip(beat_s * (1.6 if t.role == "Lead" else 2.4), 0.5, 3.0))
            rev["params"]["predelay_ms"] = float(np.clip(beat_s / 16 * 1000, 10, 60))
            changes.append(f"Reverb decay {rev['params']['decay_s']:.2f}s / pre-delay {rev['params']['predelay_ms']:.0f} ms (from {project.bpm:.0f} BPM)")
        dl = _get(t.fx, "delay")
        if dl is not None:
            dl["params"]["sync"] = dl["params"].get("sync", True)
        # --- level vs beat
        processed_guess = va["lufs"]
        role_off = {"Lead": 0.0, "Hook": -0.5, "Double": -8.0, "Ad-lib": -10.0, "Harmony": -9.0}.get(t.role, 0.0)
        target_lufs = beat_lufs + prominence_lu + role_off
        if processed_guess > -60:
            # compression/limiting raises loudness ~3 LU; account for that
            t.volume_db = float(np.clip(target_lufs - (processed_guess + 3.0), -24, 12))
            changes.append(f"Track volume {t.volume_db:+.1f} dB so the vocal sits {prominence_lu + role_off:+.1f} LU vs the beat")
        # --- timing
        if report["beat"]:
            ms, gain = timing_offset(v, project.bpm, project.beat_offset_s - off / sr, sr)
            info["timing_offset_ms"] = ms
            if abs(ms) >= 6 and gain > 0.08:
                if apply_timing:
                    shift = int(ms / 1000 * sr)
                    for c in t.clips:
                        c.start = max(0, c.start + shift)
                    changes.append(f"Timing: nudged the take {ms:+.0f} ms to lock to the grid")
                else:
                    changes.append(f"Timing: take is about {ms:+.0f} ms off the grid (enable 'Nudge timing' to fix)")
        info["changes"] = changes
        report["vocals"].append(info)
        report["changes"].extend(f"{t.name}: {c}" for c in changes)
        project.invalidate(t)
    project.invalidate()
    project.dirty = True
    return report


def format_report(rep) -> str:
    lines = ["SIB Auto-Match report", "=" * 24]
    b = rep.get("beat")
    if b:
        lines += [f"Key: {b['key']['name']}  (relative: {b['key']['relative']})",
                  f"Other likely keys: {', '.join(n for n, _ in b['key']['candidates'][1:4])}",
                  f"Tempo: {b['bpm']:.1f} BPM  (alternatives {', '.join(f'{a:.1f}' for a in b['bpm_alternatives'])})",
                  f"Beat tuning: A4 = {b['a4_hz']:.1f} Hz ({b['tuning_cents']:+.1f} cents)",
                  f"Beat loudness: {b['lufs']:.1f} LUFS, peak {b['peak_db']:.1f} dBFS", ""]
    for v in rep.get("vocals", []):
        r = v.get("range")
        lines.append(f"[{v['name']}]")
        if r:
            lines.append(f"  Range {r['low_note']} - {r['high_note']} (centre {r['median_note']}), "
                         f"{'sung/melodic' if v['melodic'] > 0.55 else 'rapped'} ({v['melodic']:.2f})")
        if v.get("vocal_key"):
            lines.append(f"  Melody alone suggests: {v['vocal_key']}")
        for c in v.get("changes", []):
            lines.append(f"  - {c}")
        lines.append("")
    if not rep.get("vocals"):
        lines.extend(c for c in rep.get("changes", []))
    return "\n".join(lines)
