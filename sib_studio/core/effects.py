"""Effect registry: every processor with its full set of settings.

An effect *instance* in a track chain is a plain dict:
    {"uid": "...", "id": "compressor", "enabled": True, "params": {...}}
which serialises straight to the project file. The GUI builds controls automatically
from each effect's Param list, so every setting is exposed.
"""
from __future__ import annotations

import uuid
import copy
from dataclasses import dataclass, field
from typing import Callable
import numpy as np

from . import dsp
from .analysis import SCALES, NOTE_NAMES, integrated_lufs
from . import pitch as pitch_mod
from . import breath as breath_mod

KEY_CHOICES = ["Auto (from beat)"] + NOTE_NAMES
SCALE_CHOICES = ["Auto (from beat)"] + list(SCALES.keys())
DIVISIONS = ["1/1", "1/2", "1/2D", "1/4", "1/4D", "1/4T", "1/8", "1/8D", "1/8T", "1/16", "1/16D", "1/32"]


@dataclass
class Param:
    key: str
    label: str
    kind: str = "float"          # float | int | bool | choice
    default: object = 0.0
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str = ""
    choices: list | None = None
    tip: str = ""
    log: bool = False             # log-scaled slider (frequencies)
    # kind "hidden": stored data (e.g. a Match EQ curve) not shown as a control


@dataclass
class EffectDef:
    id: str
    name: str
    category: str
    params: list
    fn: Callable
    description: str = ""
    stereo_out: bool = False
    needs_sidechain: bool = False

    def defaults(self):
        return {p.key: copy.deepcopy(p.default) for p in self.params}


REGISTRY: dict[str, EffectDef] = {}
CATEGORIES = ["Repair", "Pitch", "EQ & Filter", "Dynamics", "Color", "Space", "Mix", "Utility", "Master"]


def register(id, name, category, params, description="", **kw):
    def deco(fn):
        REGISTRY[id] = EffectDef(id, name, category, params, fn, description, **kw)
        return fn
    return deco


def new_instance(effect_id: str, enabled=True, **overrides) -> dict:
    d = REGISTRY[effect_id]
    p = d.defaults()
    for k, v in overrides.items():
        if k in p:
            p[k] = v
    return {"uid": uuid.uuid4().hex[:10], "id": effect_id, "enabled": enabled, "params": p}


def sanitize_instance(inst: dict) -> dict:
    """Fill in params added in newer versions; drop unknown ones."""
    d = REGISTRY.get(inst.get("id"))
    if d is None:
        return inst
    p = d.defaults()
    for k, v in inst.get("params", {}).items():
        if k in p:
            p[k] = v
    inst["params"] = p
    inst.setdefault("uid", uuid.uuid4().hex[:10])
    inst.setdefault("enabled", True)
    return inst


def resolve_key(params, ctx):
    k = params.get("key", "Auto (from beat)")
    s = params.get("scale", "Auto (from beat)")
    root = ctx.get("key_root", 9) if k == KEY_CHOICES[0] else NOTE_NAMES.index(k)
    scale = ctx.get("key_scale", "Minor") if s == SCALE_CHOICES[0] else s
    if scale not in SCALES:
        scale = "Minor"
    return int(root) % 12, scale


def process_chain(x, sr, chain, ctx=None, progress=None, cancel=None):
    ctx = ctx or {}
    y = dsp.as2d(x)
    active = [e for e in chain if e.get("enabled", True) and e.get("id") in REGISTRY]
    for i, inst in enumerate(active):
        if cancel is not None and cancel():
            raise InterruptedError()
        d = REGISTRY[inst["id"]]
        params = {**d.defaults(), **inst.get("params", {})}
        if progress:
            progress(i / max(len(active), 1), d.name)
        out = d.fn(y, sr, params, ctx)
        out = dsp.as2d(out)
        if out.shape[1] == 1 and y.shape[1] == 2:
            out = np.repeat(out, 2, axis=1)
        if len(out) != len(y):
            out = out[: len(y)] if len(out) > len(y) else np.vstack(
                [out, np.zeros((len(y) - len(out), out.shape[1]), np.float32)])
        y = np.nan_to_num(out.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    if progress:
        progress(1.0, "done")
    return y


def _mix(dry, wet, mix):
    if mix >= 0.999:
        return wet
    if dry.shape[1] != wet.shape[1]:
        dry = np.repeat(dry, wet.shape[1], axis=1) if dry.shape[1] == 1 else dry.mean(axis=1, keepdims=True)
    return dry * (1 - mix) + wet * mix


P = Param

# ============================================================================ Repair
@register("noise_reduction", "Noise Reduction", "Repair", [
    P("reduction_db", "Reduction", default=12.0, min=0, max=40, step=0.5, unit="dB",
      tip="How much background noise (fans, hiss, room) is pulled down. 6-12 dB sounds natural."),
    P("sensitivity", "Sensitivity", default=1.5, min=1.0, max=4.0, step=0.05,
      tip="Higher treats more of the signal as noise. Too high eats vocal detail."),
    P("smoothing", "Smoothing", default=0.5, min=0, max=1, step=0.05, tip="Reduces watery 'musical noise' artifacts."),
], "Spectral noise reduction that learns the noise print from the quietest parts of the take.")
def _fx_nr(x, sr, p, ctx):
    prof = ctx.get("noise_profile")
    return dsp.noise_reduction(x, sr, p["reduction_db"], p["sensitivity"], prof, p["smoothing"])


@register("hum_remover", "Hum Remover", "Repair", [
    P("base_hz", "Mains frequency", "choice", "60", choices=["50", "60"], tip="60 Hz in the US."),
    P("harmonics", "Harmonics", "int", 6, 1, 12, 1),
    P("q", "Notch narrowness", default=30.0, min=5, max=80, step=1),
], "Removes electrical hum and its harmonics.")
def _fx_hum(x, sr, p, ctx):
    return dsp.hum_remover(x, sr, float(p["base_hz"]), int(p["harmonics"]), p["q"])


@register("declick", "De-Click (mouth clicks)", "Repair", [
    P("sensitivity", "Threshold", default=6.0, min=2, max=20, step=0.5, tip="Lower finds more clicks."),
    P("max_click_ms", "Max click length", default=2.0, min=0.5, max=6, step=0.1, unit="ms"),
], "Finds lip smacks, mouth clicks and digital ticks and repairs them.")
def _fx_declick(x, sr, p, ctx):
    return dsp.declick(x, sr, p["sensitivity"], p["max_click_ms"])


@register("deplosive", "De-Plosive (pops)", "Repair", [
    P("freq", "Pop band", default=150.0, min=60, max=300, step=5, unit="Hz", log=True),
    P("sensitivity_db", "Sensitivity", default=10.0, min=0, max=20, step=0.5, unit="dB"),
    P("max_reduction_db", "Max reduction", default=12.0, min=0, max=30, step=0.5, unit="dB"),
], "Tames P and B pops from getting close to the mic.")
def _fx_deplosive(x, sr, p, ctx):
    return dsp.deplosive(x, sr, p["freq"], p["sensitivity_db"], p["max_reduction_db"])


@register("breath_control", "Breath Control", "Repair", [
    P("sensitivity", "Detection sensitivity", default=0.5, min=0, max=1, step=0.05,
      tip="Higher finds more (and quieter) breaths."),
    P("mode", "Mode", "choice", "reduce", choices=["reduce", "target", "silence"],
      tip="reduce = lower by a fixed amount, target = bring every breath to the same level, silence = mute."),
    P("reduce_db", "Reduce by", default=-12.0, min=-60, max=0, step=0.5, unit="dB",
      tip="-8 to -15 dB keeps the vocal human. Full removal can sound robotic."),
    P("target_db", "Target level", default=-45.0, min=-80, max=-20, step=0.5, unit="dBFS"),
    P("fade_ms", "Fade", default=15.0, min=1, max=60, step=1, unit="ms"),
    P("min_ms", "Shortest breath", default=120.0, min=40, max=400, step=10, unit="ms"),
    P("max_ms", "Longest breath", default=1200.0, min=300, max=3000, step=50, unit="ms"),
], "Automatically finds breaths between bars and turns them down without changing timing.")
def _fx_breath(x, sr, p, ctx):
    regions = breath_mod.detect_breaths(x, sr, p["sensitivity"], p["min_ms"], p["max_ms"])
    return breath_mod.apply_breath_control(x, sr, regions, p["mode"], p["reduce_db"], p["target_db"], p["fade_ms"])


# ============================================================================ Pitch
_TUNE_PARAMS = [
    P("key", "Key", "choice", KEY_CHOICES[0], choices=KEY_CHOICES,
      tip="Auto uses the key detected from the beat (Tools > Analyze Beat)."),
    P("scale", "Scale", "choice", SCALE_CHOICES[0], choices=SCALE_CHOICES),
    P("a4", "Tuning ref (A4)", default=0.0, min=0, max=466, step=0.1, unit="Hz",
      tip="0 = auto: match the beat's real tuning (many beats are not at A440)."),
]


@register("sib_tune", "SIB Tune (Auto-Tune)", "Pitch", _TUNE_PARAMS + [
    P("retune_ms", "Retune speed", default=25.0, min=0, max=400, step=1, unit="ms",
      tip="0-10 ms = hard robotic T-Pain/Travis effect. 15-25 = melodic rap. 25-80 = natural."),
    P("humanize", "Humanize", default=20.0, min=0, max=100, step=1, unit="%",
      tip="Loosens correction on held notes so they breathe while short notes stay tight."),
    P("flex_cents", "Flex-Tune", default=15.0, min=0, max=100, step=1, unit="cents",
      tip="Notes already close to pitch are left alone - keeps slides and expression. 0 for hard tune."),
    P("amount", "Correction amount", default=100.0, min=0, max=100, step=1, unit="%"),
    P("hysteresis", "Note stickiness", default=20.0, min=0, max=50, step=1, unit="cents",
      tip="Stops the tuner flipping between two notes on a wobbly pitch."),
    P("transpose", "Transpose", "int", 0, -12, 12, 1, unit="st"),
    P("formant", "Throat / formant", default=0.0, min=-6, max=6, step=0.1, unit="st",
      tip="Changes voice character without changing pitch."),
    P("fmin", "Lowest note", default=60.0, min=40, max=300, step=1, unit="Hz", log=True),
    P("fmax", "Highest note", default=900.0, min=300, max=1600, step=10, unit="Hz", log=True),
    P("mix", "Mix", default=100.0, min=0, max=100, step=1, unit="%"),
], "Real-time-style pitch correction locked to the beat's key and tuning (formant-preserving).")
def _fx_tune(x, sr, p, ctx):
    root, scale = resolve_key(p, ctx)
    a4 = p["a4"] if p["a4"] > 0 else ctx.get("a4", 440.0)
    y = pitch_mod.autotune(x, sr, root, scale, a4, p["retune_ms"], p["humanize"], p["flex_cents"],
                           p["amount"] / 100.0, p["transpose"], p["formant"], 0, p["fmin"], p["fmax"],
                           hysteresis_cents=p["hysteresis"])
    return _mix(x, y, p["mix"] / 100.0)


@register("harmonizer", "Harmonizer", "Pitch", _TUNE_PARAMS + [
    P("voice1", "Voice 1 interval", "int", 2, -7, 7, 1, unit="deg",
      tip="Scale degrees: 2 = a third up, 4 = a fifth up, -3 = a fourth down, 7 = octave."),
    P("voice1_db", "Voice 1 level", default=-8.0, min=-40, max=0, step=0.5, unit="dB"),
    P("voice1_pan", "Voice 1 pan", default=-0.5, min=-1, max=1, step=0.05),
    P("voice2_on", "Voice 2 on", "bool", False),
    P("voice2", "Voice 2 interval", "int", -3, -7, 7, 1, unit="deg"),
    P("voice2_db", "Voice 2 level", default=-10.0, min=-40, max=0, step=0.5, unit="dB"),
    P("voice2_pan", "Voice 2 pan", default=0.5, min=-1, max=1, step=0.05),
    P("formant", "Formant shift", default=0.0, min=-6, max=6, step=0.1, unit="st"),
    P("retune_ms", "Retune speed", default=10.0, min=0, max=200, step=1, unit="ms"),
], "Generates in-key harmony voices from the vocal - instant hook stacks.", stereo_out=True)
def _fx_harm(x, sr, p, ctx):
    root, scale = resolve_key(p, ctx)
    a4 = p["a4"] if p["a4"] > 0 else ctx.get("a4", 440.0)
    tr = pitch_mod.detect_pitch(x, sr)
    out = dsp.as2d(x)
    out = np.repeat(out, 2, axis=1) if out.shape[1] == 1 else out.copy()
    voices = [(p["voice1"], p["voice1_db"], p["voice1_pan"])]
    if p["voice2_on"]:
        voices.append((p["voice2"], p["voice2_db"], p["voice2_pan"]))
    for deg, lvl, pan in voices:
        if deg == 0:
            continue
        res = pitch_mod.correction_curve(tr, root, scale, a4, p["retune_ms"], 0, 0, 1.0, 0, 20, int(deg))
        h = pitch_mod.psola_shift(x, sr, tr, res.shift_semitones, p["formant"]).mean(axis=1)
        gl, gr = dsp.pan_gains(pan)
        g = dsp.db_to_lin(lvl)
        out[:, 0] += h * gl * g
        out[:, 1] += h * gr * g
    return out


@register("pitch_shift", "Pitch Shifter", "Pitch", [
    P("semitones", "Semitones", default=0.0, min=-12, max=12, step=1, unit="st"),
    P("cents", "Fine", default=0.0, min=-100, max=100, step=1, unit="cents"),
    P("formant", "Formant", default=0.0, min=-6, max=6, step=0.1, unit="st"),
    P("mode", "Mode", "choice", "vocal", choices=["vocal", "full mix / beat"],
      tip="Use 'full mix / beat' to transpose a beat to fit your voice."),
], "Shifts pitch without changing length.")
def _fx_pshift(x, sr, p, ctx):
    st = p["semitones"] + p["cents"] / 100.0
    if abs(st) < 1e-3 and abs(p["formant"]) < 1e-3:
        return x
    if p["mode"] == "vocal":
        return pitch_mod.pitch_shift_vocal(x, sr, st, p["formant"])
    return dsp.pitch_shift_general(x, sr, st)


@register("doubler", "Vocal Doubler", "Pitch", [
    P("voices", "Voices", "int", 2, 1, 6, 1),
    P("detune_cents", "Detune", default=8.0, min=0, max=30, step=0.5, unit="cents"),
    P("delay_ms", "Delay", default=18.0, min=2, max=60, step=0.5, unit="ms"),
    P("spread", "Stereo spread", default=0.8, min=0, max=1, step=0.05),
    P("mix", "Double level", default=50.0, min=0, max=100, step=1, unit="%"),
], "Fakes a double-tracked vocal for a thicker, wider sound.", stereo_out=True)
def _fx_doubler(x, sr, p, ctx):
    return dsp.doubler(x, sr, int(p["voices"]), p["detune_cents"], p["delay_ms"], p["spread"], p["mix"] / 100.0)


# ============================================================================ EQ
@register("eq", "Parametric EQ", "EQ & Filter", [
    P("hpf_on", "High-pass on", "bool", True),
    P("hpf_freq", "High-pass", default=90.0, min=20, max=400, step=1, unit="Hz", log=True,
      tip="Removes rumble and boom. 80-120 Hz for rap leads, 150-200 for doubles/ad-libs."),
    P("hpf_slope", "HP slope", "choice", "24 dB/oct", choices=["12 dB/oct", "24 dB/oct", "48 dB/oct"]),
    P("low_shelf_freq", "Low shelf", default=150.0, min=40, max=500, step=1, unit="Hz", log=True),
    P("low_shelf_gain", "Low shelf gain", default=0.0, min=-12, max=12, step=0.25, unit="dB"),
    P("mud_freq", "Mud band", default=300.0, min=150, max=600, step=1, unit="Hz", log=True),
    P("mud_gain", "Mud gain", default=-2.5, min=-12, max=12, step=0.25, unit="dB", tip="Cut 2-4 dB at 200-400 Hz to remove boxiness."),
    P("mud_q", "Mud Q", default=1.0, min=0.2, max=8, step=0.05),
    P("nasal_freq", "Nasal band", default=900.0, min=500, max=2000, step=5, unit="Hz", log=True),
    P("nasal_gain", "Nasal gain", default=-1.0, min=-12, max=12, step=0.25, unit="dB"),
    P("nasal_q", "Nasal Q", default=1.5, min=0.2, max=8, step=0.05),
    P("presence_freq", "Presence", default=3500.0, min=1500, max=8000, step=10, unit="Hz", log=True,
      tip="Boost 2-4 dB at 2-5 kHz so the words cut through the beat."),
    P("presence_gain", "Presence gain", default=2.5, min=-12, max=12, step=0.25, unit="dB"),
    P("presence_q", "Presence Q", default=0.8, min=0.2, max=8, step=0.05),
    P("air_freq", "Air shelf", default=12000.0, min=6000, max=18000, step=50, unit="Hz", log=True),
    P("air_gain", "Air gain", default=2.5, min=-12, max=12, step=0.25, unit="dB"),
    P("lpf_on", "Low-pass on", "bool", False),
    P("lpf_freq", "Low-pass", default=18000.0, min=2000, max=20000, step=50, unit="Hz", log=True),
], "Studio vocal EQ: rumble filter, mud/nasal cuts, presence and air.")
def _fx_eq(x, sr, p, ctx):
    return dsp.eq_bands(x, sr, eq_bands_from_params(p))


def eq_bands_from_params(p):
    slope = {"12 dB/oct": 2, "24 dB/oct": 4, "48 dB/oct": 8}.get(p.get("hpf_slope"), 4)
    bands = []
    if p["hpf_on"]:
        bands.append(dict(type="highpass", freq=p["hpf_freq"], order=slope))
    bands += [
        dict(type="lowshelf", freq=p["low_shelf_freq"], gain=p["low_shelf_gain"], q=0.7),
        dict(type="peak", freq=p["mud_freq"], gain=p["mud_gain"], q=p["mud_q"]),
        dict(type="peak", freq=p["nasal_freq"], gain=p["nasal_gain"], q=p["nasal_q"]),
        dict(type="peak", freq=p["presence_freq"], gain=p["presence_gain"], q=p["presence_q"]),
        dict(type="highshelf", freq=p["air_freq"], gain=p["air_gain"], q=0.7),
    ]
    if p["lpf_on"]:
        bands.append(dict(type="lowpass", freq=p["lpf_freq"], order=4))
    return bands


@register("filter", "Filter (HP/LP/Band)", "EQ & Filter", [
    P("type", "Type", "choice", "highpass", choices=["highpass", "lowpass", "bandpass", "notch"]),
    P("freq", "Frequency", default=200.0, min=20, max=20000, step=1, unit="Hz", log=True),
    P("q", "Q", default=0.707, min=0.1, max=20, step=0.01),
    P("order", "Steepness", "choice", "2", choices=["2", "4", "8"]),
], "Simple filter - good for intros, telephone sections and beat drops.")
def _fx_filter(x, sr, p, ctx):
    return dsp.sosfilt(dsp.biquad_sos(p["type"], p["freq"], sr, p["q"], 0, int(p["order"])), x)


# ============================================================================ Dynamics
@register("gate", "Noise Gate / Expander", "Dynamics", [
    P("threshold_db", "Threshold", default=-45.0, min=-80, max=0, step=0.5, unit="dB"),
    P("ratio", "Ratio", default=4.0, min=1, max=50, step=0.5, unit=":1", tip="20+ acts as a hard gate."),
    P("range_db", "Range", default=-30.0, min=-80, max=0, step=0.5, unit="dB"),
    P("attack_ms", "Attack", default=1.0, min=0.1, max=50, step=0.1, unit="ms"),
    P("hold_ms", "Hold", default=60.0, min=0, max=500, step=1, unit="ms"),
    P("release_ms", "Release", default=120.0, min=5, max=1000, step=1, unit="ms"),
], "Quiets the noise between words.")
def _fx_gate(x, sr, p, ctx):
    return dsp.gate_expander(x, sr, p["threshold_db"], p["ratio"], p["range_db"], p["attack_ms"], p["hold_ms"], p["release_ms"])


@register("compressor", "Compressor", "Dynamics", [
    P("threshold_db", "Threshold", default=-20.0, min=-60, max=0, step=0.5, unit="dB"),
    P("ratio", "Ratio", default=4.0, min=1, max=20, step=0.1, unit=":1", tip="4:1 laid back, 6:1 aggressive."),
    P("attack_ms", "Attack", default=10.0, min=0.1, max=100, step=0.1, unit="ms",
      tip="5-15 ms lets consonants punch through."),
    P("release_ms", "Release", default=80.0, min=5, max=1000, step=1, unit="ms"),
    P("knee_db", "Knee", default=6.0, min=0, max=24, step=0.5, unit="dB"),
    P("makeup_db", "Makeup gain", default=0.0, min=-12, max=24, step=0.25, unit="dB"),
    P("auto_makeup", "Auto makeup", "bool", True),
    P("detector", "Detector", "choice", "peak", choices=["peak", "rms"]),
    P("mix", "Mix (parallel)", default=100.0, min=0, max=100, step=1, unit="%",
      tip="Below 100% = parallel/New York compression."),
], "Evens out the performance so every bar sits at the same level.")
def _fx_comp(x, sr, p, ctx):
    return dsp.compressor(x, sr, p["threshold_db"], p["ratio"], p["attack_ms"], p["release_ms"], p["knee_db"],
                          p["makeup_db"], p["auto_makeup"], p["mix"] / 100.0, detector=p["detector"])


@register("deesser", "De-Esser", "Dynamics", [
    P("freq", "Frequency", default=6500.0, min=3000, max=12000, step=50, unit="Hz", log=True,
      tip="5-8 kHz for most voices."),
    P("threshold_db", "Threshold offset", default=0.0, min=-20, max=20, step=0.5, unit="dB",
      tip="Lower = more de-essing."),
    P("max_reduction_db", "Max reduction", default=6.0, min=0, max=20, step=0.5, unit="dB",
      tip="3-6 dB. Too much makes the rapper sound lispy."),
    P("mode", "Mode", "choice", "split", choices=["split", "wide"]),
    P("auto", "Auto threshold", "bool", True),
    P("listen", "Listen (solo the esses)", "bool", False),
], "Tames harsh S, SH and T sounds.")
def _fx_deess(x, sr, p, ctx):
    thr = p["threshold_db"] - 28.0
    return dsp.deesser(x, sr, p["freq"], thr, p["max_reduction_db"], p["mode"], listen=p["listen"], auto=p["auto"])


@register("vocal_rider", "Vocal Rider (auto level)", "Dynamics", [
    P("target_db", "Target level", default=-18.0, min=-40, max=-6, step=0.5, unit="dB"),
    P("max_boost_db", "Max boost", default=6.0, min=0, max=18, step=0.5, unit="dB"),
    P("max_cut_db", "Max cut", default=6.0, min=0, max=18, step=0.5, unit="dB"),
    P("speed_ms", "Speed", default=150.0, min=20, max=1000, step=5, unit="ms"),
    P("gate_db", "Ignore below", default=-50.0, min=-80, max=-20, step=1, unit="dB"),
], "Rides the fader for you - quiet words come up, shouted words come down.")
def _fx_rider(x, sr, p, ctx):
    return dsp.vocal_rider(x, sr, p["target_db"], p["max_boost_db"], p["max_cut_db"], p["speed_ms"], p["gate_db"])


@register("multiband", "Multiband Compressor", "Dynamics", [
    P("low_x", "Low/Mid split", default=200.0, min=60, max=800, step=5, unit="Hz", log=True),
    P("high_x", "Mid/High split", default=4000.0, min=1500, max=10000, step=50, unit="Hz", log=True),
    P("low_thr", "Low threshold", default=-24.0, min=-60, max=0, step=0.5, unit="dB"),
    P("low_ratio", "Low ratio", default=3.0, min=1, max=20, step=0.1),
    P("low_gain", "Low gain", default=0.0, min=-12, max=12, step=0.25, unit="dB"),
    P("mid_thr", "Mid threshold", default=-24.0, min=-60, max=0, step=0.5, unit="dB"),
    P("mid_ratio", "Mid ratio", default=2.0, min=1, max=20, step=0.1),
    P("mid_gain", "Mid gain", default=0.0, min=-12, max=12, step=0.25, unit="dB"),
    P("high_thr", "High threshold", default=-28.0, min=-60, max=0, step=0.5, unit="dB"),
    P("high_ratio", "High ratio", default=3.0, min=1, max=20, step=0.1),
    P("high_gain", "High gain", default=0.0, min=-12, max=12, step=0.25, unit="dB"),
], "Controls lows, mids and highs separately.")
def _fx_mb(x, sr, p, ctx):
    bands = [dict(threshold_db=p["low_thr"], ratio=p["low_ratio"], gain_db=p["low_gain"]),
             dict(threshold_db=p["mid_thr"], ratio=p["mid_ratio"], gain_db=p["mid_gain"]),
             dict(threshold_db=p["high_thr"], ratio=p["high_ratio"], gain_db=p["high_gain"])]
    return dsp.multiband_compressor(x, sr, p["low_x"], p["high_x"], bands)


@register("limiter", "Hard Limiter", "Dynamics", [
    P("input_db", "Input gain", default=0.0, min=-12, max=24, step=0.25, unit="dB"),
    P("ceiling_db", "Ceiling", default=-1.0, min=-12, max=0, step=0.1, unit="dBFS"),
    P("release_ms", "Release", default=60.0, min=5, max=500, step=1, unit="ms"),
    P("lookahead_ms", "Look-ahead", default=3.0, min=0.5, max=10, step=0.1, unit="ms"),
], "Brick-wall limiter that stops clipping.")
def _fx_lim(x, sr, p, ctx):
    return dsp.limiter(x, sr, p["ceiling_db"], p["release_ms"], p["lookahead_ms"], p["input_db"])


# ============================================================================ Color
@register("saturation", "Saturation", "Color", [
    P("mode", "Type", "choice", "tape", choices=["tape", "tube", "warm", "hard"],
      tip="Tape for warmth, tube for grit (trap/drill)."),
    P("drive_db", "Drive", default=6.0, min=0, max=30, step=0.25, unit="dB"),
    P("mix", "Mix", default=35.0, min=0, max=100, step=1, unit="%"),
    P("tone", "Tone", default=0.0, min=-6, max=6, step=0.25, unit="dB"),
    P("output_db", "Output", default=0.0, min=-12, max=12, step=0.25, unit="dB"),
], "Harmonic warmth and grit that helps the vocal glue to the beat.")
def _fx_sat(x, sr, p, ctx):
    return dsp.saturation(x, sr, p["drive_db"], p["mode"], p["mix"] / 100.0, p["tone"], p["output_db"])


@register("exciter", "Exciter (clarity)", "Color", [
    P("freq", "Frequency", default=3500.0, min=1500, max=10000, step=50, unit="Hz", log=True),
    P("drive_db", "Drive", default=12.0, min=0, max=30, step=0.5, unit="dB"),
    P("amount", "Amount", default=20.0, min=0, max=100, step=1, unit="%"),
], "Adds crisp upper harmonics - great for super-clear rap vocals.")
def _fx_exc(x, sr, p, ctx):
    return dsp.exciter(x, sr, p["freq"], p["drive_db"], p["amount"] / 100.0)


@register("lofi", "Lo-Fi / Bitcrusher", "Color", [
    P("bits", "Bits", "int", 10, 3, 16, 1),
    P("downsample", "Downsample", "int", 2, 1, 16, 1),
    P("mix", "Mix", default=50.0, min=0, max=100, step=1, unit="%"),
], "Crunchy lo-fi texture.")
def _fx_lofi(x, sr, p, ctx):
    return dsp.bitcrush(x, sr, int(p["bits"]), int(p["downsample"]), p["mix"] / 100.0)


@register("telephone", "Telephone / Radio", "Color", [
    P("low", "Low cut", default=400.0, min=100, max=1500, step=10, unit="Hz", log=True),
    P("high", "High cut", default=3400.0, min=1500, max=8000, step=50, unit="Hz", log=True),
    P("drive_db", "Drive", default=6.0, min=0, max=24, step=0.5, unit="dB"),
    P("mix", "Mix", default=100.0, min=0, max=100, step=1, unit="%"),
], "Phone / megaphone vocal for intros and hooks.")
def _fx_tel(x, sr, p, ctx):
    return _mix(dsp.as2d(x), dsp.telephone(x, sr, p["low"], p["high"], p["drive_db"]), p["mix"] / 100.0)


# ============================================================================ Space
@register("delay", "Delay / Echo", "Space", [
    P("sync", "Sync to beat BPM", "bool", True),
    P("division", "Note", "choice", "1/4", choices=DIVISIONS),
    P("time_ms", "Time (unsynced)", default=250.0, min=10, max=2000, step=1, unit="ms",
      tip="80-120 ms with 0 feedback = slapback."),
    P("feedback", "Feedback", default=25.0, min=0, max=95, step=1, unit="%"),
    P("mix", "Mix", default=12.0, min=0, max=100, step=0.5, unit="%"),
    P("ping_pong", "Ping-pong", "bool", True),
    P("low_cut", "Low cut", default=300.0, min=20, max=2000, step=5, unit="Hz", log=True),
    P("high_cut", "High cut", default=6000.0, min=1000, max=20000, step=50, unit="Hz", log=True),
    P("wet_only", "Wet only (for send buses)", "bool", False),
], "Tempo-synced echoes, locked to the beat's BPM.", stereo_out=True)
def _fx_delay(x, sr, p, ctx):
    bpm = ctx.get("bpm") or 90.0
    t = None if p["sync"] else p["time_ms"]
    return dsp.delay(x, sr, t, bpm, p["division"], p["feedback"] / 100.0, p["mix"] / 100.0,
                     p["ping_pong"], p["low_cut"], p["high_cut"], wet_only=p.get("wet_only", False))


@register("reverb", "Reverb", "Space", [
    P("decay_s", "Decay", default=1.1, min=0.1, max=8, step=0.05, unit="s", tip="0.8-1.5 s plate for rap."),
    P("predelay_ms", "Pre-delay", default=30.0, min=0, max=200, step=1, unit="ms",
      tip="20-40 ms keeps the words up front."),
    P("sync_predelay", "Sync pre-delay to BPM (1/64)", "bool", False),
    P("damping", "Damping", default=0.5, min=0, max=1, step=0.05),
    P("size", "Size", default=0.6, min=0, max=1, step=0.05),
    P("width", "Width", default=1.0, min=0, max=1.5, step=0.05),
    P("early", "Early reflections", default=0.3, min=0, max=1, step=0.05),
    P("mix", "Mix", default=12.0, min=0, max=100, step=0.5, unit="%"),
    P("low_cut", "Low cut", default=250.0, min=20, max=1000, step=5, unit="Hz", log=True),
    P("high_cut", "High cut", default=9000.0, min=2000, max=20000, step=50, unit="Hz", log=True),
    P("wet_only", "Wet only (for send buses)", "bool", False),
], "Plate/room/hall space for the vocal.", stereo_out=True)
def _fx_rev(x, sr, p, ctx):
    pd = p["predelay_ms"]
    if p["sync_predelay"] and ctx.get("bpm"):
        pd = dsp.note_to_seconds(ctx["bpm"], "1/16") * 250.0  # 1/64 note in ms
    return dsp.reverb(x, sr, p["decay_s"], pd, p["damping"], p["size"], p["width"], p["mix"] / 100.0,
                      p["low_cut"], p["high_cut"], p["early"], wet_only=p.get("wet_only", False))


@register("chorus", "Chorus", "Space", [
    P("rate_hz", "Rate", default=0.8, min=0.05, max=8, step=0.05, unit="Hz"),
    P("depth_ms", "Depth", default=3.0, min=0, max=15, step=0.1, unit="ms"),
    P("delay_ms", "Delay", default=12.0, min=1, max=40, step=0.5, unit="ms"),
    P("mix", "Mix", default=30.0, min=0, max=100, step=1, unit="%"),
], "Shimmering width.", stereo_out=True)
def _fx_chorus(x, sr, p, ctx):
    return dsp.chorus(x, sr, p["rate_hz"], p["depth_ms"], p["delay_ms"], p["mix"] / 100.0)


@register("stereo", "Stereo Width", "Space", [
    P("width", "Width", default=1.0, min=0, max=2, step=0.05, tip="0 = mono, 1 = unchanged, 2 = extra wide."),
], "Narrows or widens the stereo image.")
def _fx_width(x, sr, p, ctx):
    return dsp.stereo_width(x, p["width"])


# ============================================================================ Mix
@register("beat_carve", "Beat Carve (make room for vocals)", "Mix", [
    P("freq", "Carve frequency", default=3000.0, min=500, max=8000, step=10, unit="Hz", log=True,
      tip="The vocal's clarity zone. 2-4 kHz for most rappers."),
    P("depth_db", "Carve depth", default=-4.0, min=-15, max=0, step=0.25, unit="dB"),
    P("q", "Width (Q)", default=0.8, min=0.2, max=4, step=0.05),
    P("duck_db", "Overall duck", default=-1.0, min=-12, max=0, step=0.25, unit="dB",
      tip="Turns the whole beat down a little while someone is rapping."),
    P("sensitivity_db", "Vocal threshold", default=-40.0, min=-70, max=-10, step=1, unit="dB"),
    P("attack_ms", "Attack", default=15.0, min=1, max=200, step=1, unit="ms"),
    P("release_ms", "Release", default=250.0, min=20, max=2000, step=5, unit="ms"),
], "Put this on the BEAT. It dips the beat's vocal range only while vocals play (sidechain dynamic EQ).",
   needs_sidechain=True)
def _fx_carve(x, sr, p, ctx):
    sc = ctx.get("vocal_bus")
    x = dsp.as2d(x)
    if sc is None or not len(sc):
        return x
    sc = dsp.as2d(sc)
    if len(sc) < len(x):
        sc = np.vstack([sc, np.zeros((len(x) - len(sc), sc.shape[1]), np.float32)])
    lv = dsp.block_level_db(np.max(np.abs(sc[: len(x)]), axis=1), mode="rms")
    env = np.clip((lv - p["sensitivity_db"]) / 12.0, 0, 1)
    env_db = (env - 1) * 40  # 0 when vocal present, -40 when absent
    # falling (vocal stops) uses release, rising (vocal starts) uses attack
    env_db = dsp.smooth_gain_db(env_db, sr / dsp.CTRL_BLOCK, p["release_ms"] / 1000, p["attack_ms"] / 1000)
    env = np.clip(1 + env_db / 40, 0, 1)
    e = dsp.expand_ctrl(env, len(x))[:, None]
    cut = dsp.eq_bands(x, sr, [dict(type="peak", freq=p["freq"], gain=p["depth_db"], q=p["q"])])
    y = x + (cut - x) * e
    y = y * dsp.db_to_lin(p["duck_db"] * e)
    return y.astype(np.float32)


@register("sidechain_duck", "Sidechain Duck (pump)", "Mix", [
    P("amount_db", "Duck amount", default=-6.0, min=-24, max=0, step=0.5, unit="dB"),
    P("threshold_db", "Vocal threshold", default=-35.0, min=-70, max=-5, step=1, unit="dB"),
    P("attack_ms", "Attack", default=5.0, min=0.5, max=100, step=0.5, unit="ms"),
    P("release_ms", "Release", default=200.0, min=10, max=2000, step=5, unit="ms"),
], "Ducks the whole track under the vocals.", needs_sidechain=True)
def _fx_duck(x, sr, p, ctx):
    sc = ctx.get("vocal_bus")
    x = dsp.as2d(x)
    if sc is None or not len(sc):
        return x
    sc = dsp.as2d(sc)
    if len(sc) < len(x):
        sc = np.vstack([sc, np.zeros((len(x) - len(sc), sc.shape[1]), np.float32)])
    lv = dsp.block_level_db(np.max(np.abs(sc[: len(x)]), axis=1), mode="rms")
    on = np.clip((lv - p["threshold_db"]) / 6.0, 0, 1)
    gr = on * p["amount_db"]
    gr = dsp.smooth_gain_db(gr, sr / dsp.CTRL_BLOCK, p["attack_ms"] / 1000, p["release_ms"] / 1000)
    return (x * dsp.db_to_lin(dsp.expand_ctrl(gr, len(x)))[:, None]).astype(np.float32)


# ============================================================================ Utility
@register("gain", "Gain / Amplify", "Utility", [
    P("gain_db", "Gain", default=0.0, min=-48, max=24, step=0.1, unit="dB"),
], "Turns it up or down.")
def _fx_gain(x, sr, p, ctx):
    return dsp.as2d(x) * dsp.db_to_lin(p["gain_db"])


@register("normalize", "Normalize / Match Loudness", "Utility", [
    P("mode", "Mode", "choice", "peak", choices=["peak", "loudness (LUFS)"]),
    P("peak_db", "Peak target", default=-1.0, min=-24, max=0, step=0.1, unit="dBFS"),
    P("lufs", "Loudness target", default=-16.0, min=-36, max=-5, step=0.5, unit="LUFS"),
], "Brings the level to an exact peak or loudness target.")
def _fx_norm(x, sr, p, ctx):
    x = dsp.as2d(x)
    if p["mode"] == "peak":
        return dsp.normalize_peak(x, p["peak_db"])
    cur = integrated_lufs(x, sr)
    if cur <= -69:
        return x
    return x * dsp.db_to_lin(p["lufs"] - cur)


@register("channel_tools", "Channel Tools", "Utility", [
    P("invert", "Invert polarity", "bool", False),
    P("mono", "Sum to mono", "bool", False),
    P("swap", "Swap L/R", "bool", False),
    P("dc", "Remove DC offset", "bool", True),
], "Polarity, mono, channel swap, DC removal.")
def _fx_chan(x, sr, p, ctx):
    y = dsp.as2d(x)
    if p["dc"]:
        y = dsp.remove_dc(y, sr)
    if p["invert"]:
        y = -y
    if p["mono"] and y.shape[1] == 2:
        m = y.mean(axis=1, keepdims=True)
        y = np.repeat(m, 2, axis=1)
    if p["swap"] and y.shape[1] == 2:
        y = y[:, ::-1]
    return y


# ============================================================================ Master
@register("mastering", "Mastering (glue + loudness)", "Master", [
    P("glue_on", "Glue compressor", "bool", True),
    P("glue_thr", "Glue threshold", default=-14.0, min=-40, max=0, step=0.5, unit="dB"),
    P("glue_ratio", "Glue ratio", default=2.0, min=1, max=6, step=0.1),
    P("low_db", "Low end", default=0.0, min=-6, max=6, step=0.25, unit="dB"),
    P("high_db", "Top end", default=0.5, min=-6, max=6, step=0.25, unit="dB"),
    P("width", "Width", default=1.0, min=0.5, max=1.5, step=0.05),
    P("target_lufs", "Loudness target", default=-10.0, min=-20, max=-6, step=0.5, unit="LUFS",
      tip="-14 = streaming normalised, -9 to -11 = loud rap master."),
    P("ceiling_db", "Ceiling", default=-1.0, min=-3, max=0, step=0.1, unit="dBTP"),
], "Final polish: glue compression, tone, width and loud-but-clean limiting.")
def _fx_master(x, sr, p, ctx):
    y = dsp.as2d(x)
    if p["glue_on"]:
        y = dsp.compressor(y, sr, p["glue_thr"], p["glue_ratio"], 25.0, 150.0, 6.0, 0, True)
    y = dsp.eq_bands(y, sr, [dict(type="lowshelf", freq=100, gain=p["low_db"], q=0.7),
                             dict(type="highshelf", freq=9000, gain=p["high_db"], q=0.7)])
    y = dsp.stereo_width(y, p["width"])
    cur = integrated_lufs(y, sr)
    if cur > -69:
        g = p["target_lufs"] - cur
        for _ in range(2):  # limiter reduces loudness; iterate toward the target
            out = dsp.limiter(y * dsp.db_to_lin(g), sr, p["ceiling_db"] - 0.3, 80.0)
            got = integrated_lufs(out, sr)
            g += (p["target_lufs"] - got)
        y = dsp.limiter(y * dsp.db_to_lin(g), sr, p["ceiling_db"] - 0.3, 80.0)
    return y


# ============================================================================ v1.1 effects
@register("graphic_eq", "Graphic EQ (10-band)", "EQ & Filter",
          [P(f"g{f}", f"{f if f < 1000 else str(f // 1000) + 'k'} Hz", default=0.0, min=-15, max=15, step=0.5, unit="dB")
           for f in dsp.GRAPHIC_FREQS] + [P("q", "Band width (Q)", default=1.4, min=0.5, max=4, step=0.1)],
          "Classic 10-band graphic EQ (31 Hz - 16 kHz).")
def _fx_geq(x, sr, p, ctx):
    return dsp.graphic_eq(x, sr, dsp.GRAPHIC_FREQS, [p[f"g{f}"] for f in dsp.GRAPHIC_FREQS], p["q"])


@register("match_eq", "Match EQ (reference)", "EQ & Filter", [
    P("amount", "Amount", default=70.0, min=0, max=100, step=1, unit="%",
      tip="How far to move toward the reference's tone. 50-80% is usually plenty."),
    P("curve", "curve", "hidden", []),
    P("reference", "Reference", "hidden", ""),
], "Moves the tone of this track toward a reference (set it from Mix > Match EQ to reference).")
def _fx_match(x, sr, p, ctx):
    curve = p.get("curve") or []
    if not curve:
        return x
    g = [c * p["amount"] / 100.0 for c in curve]
    return dsp.graphic_eq(x, sr, dsp.THIRD_OCTAVE[: len(g)], g, q=4.3)


@register("dynamic_eq", "Dynamic EQ (harshness tamer)", "Dynamics", [
    P("freq", "Frequency", default=3000.0, min=100, max=12000, step=10, unit="Hz", log=True,
      tip="2.5-5 kHz tames harsh/shouty words; 200-400 Hz tames boomy ones."),
    P("q", "Width (Q)", default=1.5, min=0.3, max=8, step=0.1),
    P("threshold_db", "Threshold", default=-30.0, min=-60, max=0, step=0.5, unit="dB"),
    P("max_cut_db", "Max cut", default=6.0, min=0, max=18, step=0.5, unit="dB"),
    P("attack_ms", "Attack", default=5.0, min=0.5, max=100, step=0.5, unit="ms"),
    P("release_ms", "Release", default=80.0, min=10, max=1000, step=5, unit="ms"),
], "Cuts a frequency only when it gets too loud - keeps the vocal bright but never harsh.")
def _fx_dyneq(x, sr, p, ctx):
    return dsp.dynamic_eq(x, sr, p["freq"], p["q"], p["threshold_db"], p["max_cut_db"], p["attack_ms"], p["release_ms"])


@register("transient", "Transient Shaper (punch)", "Dynamics", [
    P("attack_db", "Attack", default=4.0, min=-12, max=12, step=0.5, unit="dB", tip="More = punchier consonants."),
    P("sustain_db", "Sustain", default=0.0, min=-12, max=12, step=0.5, unit="dB", tip="Less = tighter, drier vocal."),
    P("speed", "Speed", default=1.0, min=0.25, max=4, step=0.05),
], "Adds punch to the start of words or tightens their tails.")
def _fx_trans(x, sr, p, ctx):
    return dsp.transient_shaper(x, sr, p["attack_db"], p["sustain_db"], p["speed"])


@register("tube_comp", "Tube Compressor", "Dynamics", [
    P("threshold_db", "Threshold", default=-18.0, min=-50, max=0, step=0.5, unit="dB"),
    P("ratio", "Ratio", default=3.0, min=1, max=10, step=0.1),
    P("attack_ms", "Attack", default=15.0, min=0.5, max=100, step=0.5, unit="ms"),
    P("release_ms", "Release", default=120.0, min=10, max=1000, step=5, unit="ms"),
    P("drive_db", "Tube drive", default=6.0, min=0, max=24, step=0.5, unit="dB"),
    P("mix", "Mix", default=100.0, min=0, max=100, step=1, unit="%"),
], "Warm compression with tube colour (like Audition's Tube-Modeled Compressor).")
def _fx_tube(x, sr, p, ctx):
    y = dsp.compressor(x, sr, p["threshold_db"], p["ratio"], p["attack_ms"], p["release_ms"], 8.0, 0, True)
    y = dsp.saturation(y, sr, p["drive_db"], "tube", 0.5)
    return _mix(dsp.as2d(x), y, p["mix"] / 100.0)


@register("soft_clipper", "Soft Clipper (loudness)", "Master", [
    P("drive_db", "Drive", default=3.0, min=0, max=18, step=0.25, unit="dB"),
    P("ceiling_db", "Ceiling", default=-0.5, min=-6, max=0, step=0.1, unit="dBFS"),
    P("mix", "Mix", default=100.0, min=0, max=100, step=1, unit="%"),
], "Shaves peaks before the limiter for louder, punchier rap masters (and 808s that hit harder).")
def _fx_clip(x, sr, p, ctx):
    return dsp.soft_clipper(x, sr, p["drive_db"], p["ceiling_db"], p["mix"] / 100.0)


@register("phaser", "Phaser", "Space", [
    P("rate_hz", "Rate", default=0.5, min=0.05, max=8, step=0.05, unit="Hz"),
    P("depth", "Depth", default=0.8, min=0, max=1, step=0.05),
    P("stages", "Stages", "int", 4, 2, 12, 2),
    P("fmin", "Low freq", default=300.0, min=50, max=2000, step=10, unit="Hz", log=True),
    P("fmax", "High freq", default=3000.0, min=500, max=12000, step=50, unit="Hz", log=True),
    P("feedback", "Feedback", default=0.3, min=0, max=0.9, step=0.05),
    P("mix", "Mix", default=50.0, min=0, max=100, step=1, unit="%"),
], "Sweeping, swirling phase effect - spacey ad-libs and intros.", stereo_out=True)
def _fx_phaser(x, sr, p, ctx):
    return dsp.phaser(x, sr, p["rate_hz"], p["depth"], int(p["stages"]), p["fmin"], p["fmax"], p["feedback"], p["mix"] / 100.0)


@register("flanger", "Flanger", "Space", [
    P("rate_hz", "Rate", default=0.25, min=0.02, max=5, step=0.01, unit="Hz"),
    P("depth_ms", "Depth", default=2.0, min=0.1, max=10, step=0.1, unit="ms"),
    P("delay_ms", "Delay", default=1.5, min=0.1, max=10, step=0.1, unit="ms"),
    P("feedback", "Feedback", default=0.4, min=0, max=0.9, step=0.05),
    P("mix", "Mix", default=50.0, min=0, max=100, step=1, unit="%"),
], "Jet-plane whoosh.", stereo_out=True)
def _fx_flanger(x, sr, p, ctx):
    return dsp.flanger(x, sr, p["rate_hz"], p["depth_ms"], p["delay_ms"], p["feedback"], p["mix"] / 100.0)


@register("tremolo", "Tremolo / Gate Stutter / Auto-Pan", "Space", [
    P("mode", "Mode", "choice", "tremolo", choices=["tremolo", "auto-pan"]),
    P("shape", "Shape", "choice", "square", choices=["sine", "triangle", "square"],
      tip="Square + sync = the trap 'gate stutter' chop."),
    P("sync", "Sync to BPM", "bool", True),
    P("division", "Note", "choice", "1/16", choices=DIVISIONS),
    P("rate_hz", "Rate (unsynced)", default=4.0, min=0.1, max=20, step=0.1, unit="Hz"),
    P("depth", "Depth", default=0.8, min=0, max=1, step=0.05),
], "Rhythmic volume chops or side-to-side movement locked to the beat.", stereo_out=True)
def _fx_trem(x, sr, p, ctx):
    bpm = ctx.get("bpm") if p["sync"] else None
    return dsp.tremolo(x, sr, p["rate_hz"], p["depth"], p["shape"], p["mode"] == "auto-pan", bpm,
                       p["division"] if p["sync"] else None)


@register("vocal_enhancer", "Vocal Enhancer", "EQ & Filter", [
    P("mode", "Voice", "choice", "low voice", choices=["low voice", "high voice", "music / beat"]),
    P("amount", "Amount", default=60.0, min=0, max=100, step=1, unit="%"),
], "One-knob clarity: removes boom and adds presence (like Audition's Vocal Enhancer).")
def _fx_venh(x, sr, p, ctx):
    a = p["amount"] / 100.0
    if p["mode"] == "low voice":
        b = [dict(type="highpass", freq=80, order=2), dict(type="peak", freq=250, gain=-4 * a, q=1.0),
             dict(type="peak", freq=3200, gain=3 * a, q=0.9), dict(type="highshelf", freq=10000, gain=2 * a, q=0.7)]
    elif p["mode"] == "high voice":
        b = [dict(type="highpass", freq=120, order=2), dict(type="peak", freq=400, gain=-3 * a, q=1.0),
             dict(type="peak", freq=5000, gain=-2 * a, q=2.0), dict(type="highshelf", freq=12000, gain=2.5 * a, q=0.7)]
    else:
        b = [dict(type="peak", freq=2500, gain=-3 * a, q=0.8)]
    return dsp.eq_bands(x, sr, b)


@register("tone", "Bass & Treble", "EQ & Filter", [
    P("bass_db", "Bass", default=0.0, min=-15, max=15, step=0.5, unit="dB"),
    P("bass_freq", "Bass freq", default=120.0, min=40, max=500, step=5, unit="Hz", log=True),
    P("treble_db", "Treble", default=0.0, min=-15, max=15, step=0.5, unit="dB"),
    P("treble_freq", "Treble freq", default=6000.0, min=1500, max=16000, step=50, unit="Hz", log=True),
], "Simple tone controls.")
def _fx_tone(x, sr, p, ctx):
    return dsp.eq_bands(x, sr, [dict(type="lowshelf", freq=p["bass_freq"], gain=p["bass_db"], q=0.7),
                                dict(type="highshelf", freq=p["treble_freq"], gain=p["treble_db"], q=0.7)])


@register("notch", "Notch Filter", "EQ & Filter", [
    P("f1", "Frequency 1", default=1000.0, min=20, max=20000, step=1, unit="Hz", log=True),
    P("f2_on", "Notch 2 on", "bool", False),
    P("f2", "Frequency 2", default=2000.0, min=20, max=20000, step=1, unit="Hz", log=True),
    P("f3_on", "Notch 3 on", "bool", False),
    P("f3", "Frequency 3", default=4000.0, min=20, max=20000, step=1, unit="Hz", log=True),
    P("q", "Narrowness (Q)", default=12.0, min=1, max=60, step=0.5),
], "Surgically removes whistles, ringing or a resonant room tone.")
def _fx_notch(x, sr, p, ctx):
    fs = [p["f1"]] + ([p["f2"]] if p["f2_on"] else []) + ([p["f3"]] if p["f3_on"] else [])
    return dsp.sosfilt(np.vstack([dsp.biquad_sos("notch", f, sr, p["q"]) for f in fs]), x)


@register("bass_enhancer", "808 / Bass Enhancer", "Color", [
    P("freq", "Bass below", default=100.0, min=40, max=250, step=1, unit="Hz", log=True),
    P("drive_db", "Drive", default=12.0, min=0, max=30, step=0.5, unit="dB"),
    P("amount", "Amount", default=30.0, min=0, max=100, step=1, unit="%"),
], "Adds harmonics to 808s and sub bass so they're heard on phones and laptop speakers.")
def _fx_bass(x, sr, p, ctx):
    return dsp.bass_enhancer(x, sr, p["freq"], p["drive_db"], p["amount"] / 100.0)


@register("vocal_remover", "Vocal Remover (beat)", "Mix", [
    P("keep_below_hz", "Keep bass below", default=150.0, min=40, max=400, step=5, unit="Hz", log=True),
    P("amount", "Amount", default=100.0, min=0, max=100, step=1, unit="%"),
], "Cancels a centred vocal in a beat that still has one (keeps the 808 and kick).")
def _fx_vrem(x, sr, p, ctx):
    return dsp.vocal_remover(x, sr, p["keep_below_hz"], p["amount"] / 100.0)


@register("audition_fx", "Audition effect (imported)", "Utility", [
    P("name", "Audition effect", "hidden", ""),
    P("raw", "raw", "hidden", {}),
], "An Adobe Audition effect with no SIB equivalent yet. Kept (bypassed) so the setting isn't lost.")
def _fx_aud(x, sr, p, ctx):
    return x


def effect_names_by_category():
    out = {c: [] for c in CATEGORIES}
    for d in REGISTRY.values():
        out.setdefault(d.category, []).append(d)
    return out
