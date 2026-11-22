"""Vocal chain presets by style, track-role defaults and user preset storage.

Starting settings come from common hip-hop mixing practice: HPF 80-120 Hz, 2-4 dB mud cut at
200-400 Hz, 4:1-6:1 compression with 5-15 ms attack, de-essing at 5-8 kHz (3-6 dB), presence
+2-4 dB at 2-5 kHz, air +2-3 dB at 10-16 kHz, plate reverb 0.8-1.5 s with 20-40 ms pre-delay,
tempo-synced delays, doubles 6-10 dB under the lead, ad-libs 8-12 dB under and panned wide.
"""
from __future__ import annotations

import json
import os
import copy
from .effects import new_instance as fx

ROLES = ["Lead", "Double", "Ad-lib", "Hook", "Harmony", "Beat"]

ROLE_DEFAULTS = {
    "Lead": dict(volume_db=0.0, pan=0.0),
    "Double": dict(volume_db=-8.0, pan=-0.25),
    "Ad-lib": dict(volume_db=-10.0, pan=0.55),
    "Hook": dict(volume_db=-1.0, pan=0.0),
    "Harmony": dict(volume_db=-9.0, pan=0.4),
    "Beat": dict(volume_db=0.0, pan=0.0),
}


def _clean(nr=8.0, breath_db=-12.0):
    return [
        fx("channel_tools"),
        fx("noise_reduction", reduction_db=nr, sensitivity=1.4),
        fx("declick"),
        fx("deplosive"),
        fx("breath_control", reduce_db=breath_db),
    ]


def chain_clean_rap():
    return _clean() + [
        fx("sib_tune", retune_ms=45.0, humanize=30.0, flex_cents=30.0, amount=70.0),
        fx("eq"),
        fx("vocal_rider", target_db=-18.0),
        fx("compressor", threshold_db=-20.0, ratio=4.0, attack_ms=10.0, release_ms=80.0),
        fx("deesser", max_reduction_db=5.0),
        fx("compressor", threshold_db=-30.0, ratio=10.0, attack_ms=2.0, release_ms=60.0, mix=35.0),
        fx("saturation", mode="tape", drive_db=5.0, mix=25.0),
        fx("delay", division="1/8", feedback=15.0, mix=7.0),
        fx("reverb", decay_s=1.0, predelay_ms=30.0, mix=8.0),
    ]


def chain_hard_tune():
    return _clean(breath_db=-15.0) + [
        fx("sib_tune", retune_ms=0.0, humanize=0.0, flex_cents=0.0, amount=100.0, hysteresis=10.0),
        fx("eq", mud_gain=-3.0, presence_gain=3.0, air_gain=3.5),
        fx("compressor", threshold_db=-22.0, ratio=5.0, attack_ms=8.0, release_ms=70.0),
        fx("deesser", max_reduction_db=6.0),
        fx("saturation", mode="tube", drive_db=6.0, mix=30.0),
        fx("delay", division="1/4", feedback=28.0, mix=14.0, ping_pong=True),
        fx("reverb", decay_s=1.4, predelay_ms=25.0, mix=13.0, width=1.2),
    ]


def chain_melodic():
    return _clean() + [
        fx("sib_tune", retune_ms=18.0, humanize=25.0, flex_cents=10.0),
        fx("eq", presence_gain=2.5, air_gain=3.0),
        fx("compressor", threshold_db=-20.0, ratio=4.0, attack_ms=12.0, release_ms=90.0),
        fx("deesser"),
        fx("saturation", mode="tape", drive_db=4.0, mix=25.0),
        fx("doubler", voices=2, detune_cents=7.0, mix=25.0),
        fx("delay", division="1/4D", feedback=25.0, mix=11.0),
        fx("reverb", decay_s=1.5, predelay_ms=35.0, mix=14.0),
    ]


def chain_drill():
    return _clean(breath_db=-14.0) + [
        fx("sib_tune", retune_ms=30.0, humanize=15.0, flex_cents=20.0, amount=60.0),
        fx("eq", hpf_freq=110.0, mud_gain=-3.5, presence_freq=3000.0, presence_gain=3.5, air_gain=2.0),
        fx("compressor", threshold_db=-22.0, ratio=6.0, attack_ms=5.0, release_ms=60.0),
        fx("deesser", max_reduction_db=6.0),
        fx("saturation", mode="tube", drive_db=9.0, mix=35.0),
        fx("exciter", amount=15.0),
        fx("delay", sync=False, time_ms=100.0, feedback=0.0, mix=10.0, ping_pong=False),
        fx("reverb", decay_s=0.7, predelay_ms=20.0, mix=6.0),
    ]


def chain_boom_bap():
    return _clean() + [
        fx("sib_tune", enabled=False),
        fx("eq", hpf_freq=80.0, mud_gain=-1.5, presence_gain=2.0, air_gain=1.0, lpf_on=True, lpf_freq=15000.0),
        fx("compressor", threshold_db=-18.0, ratio=4.0, attack_ms=15.0, release_ms=100.0),
        fx("deesser", max_reduction_db=4.0),
        fx("saturation", mode="tape", drive_db=8.0, mix=40.0),
        fx("reverb", decay_s=0.6, predelay_ms=15.0, size=0.3, mix=8.0),
    ]


def chain_lofi():
    return _clean() + [
        fx("sib_tune", retune_ms=40.0, amount=60.0),
        fx("eq", hpf_freq=150.0, lpf_on=True, lpf_freq=9000.0, air_gain=0.0),
        fx("compressor", threshold_db=-20.0, ratio=5.0),
        fx("telephone", low=300.0, high=5000.0, drive_db=4.0, mix=60.0),
        fx("lofi", bits=11, downsample=2, mix=35.0),
        fx("reverb", decay_s=1.2, mix=14.0),
    ]


def chain_adlib():
    return _clean(breath_db=-18.0) + [
        fx("sib_tune", retune_ms=10.0, humanize=0.0, flex_cents=0.0),
        fx("eq", hpf_freq=200.0, mud_gain=-4.0, presence_freq=4000.0, presence_gain=4.0, air_gain=3.0),
        fx("compressor", threshold_db=-24.0, ratio=8.0, attack_ms=3.0, release_ms=60.0),
        fx("deesser", max_reduction_db=8.0),
        fx("delay", division="1/4", feedback=35.0, mix=22.0),
        fx("reverb", decay_s=1.8, predelay_ms=20.0, mix=22.0),
    ]


def chain_double():
    return _clean(breath_db=-24.0) + [
        fx("sib_tune", retune_ms=20.0, humanize=10.0),
        fx("eq", hpf_freq=180.0, mud_gain=-4.0, presence_gain=1.5, air_gain=2.0),
        fx("compressor", threshold_db=-26.0, ratio=8.0, attack_ms=3.0, release_ms=70.0),
        fx("deesser", max_reduction_db=9.0),
        fx("reverb", decay_s=1.3, mix=10.0),
    ]


def chain_hook():
    return _clean() + [
        fx("sib_tune", retune_ms=12.0, humanize=15.0, flex_cents=5.0),
        fx("eq", presence_gain=3.0, air_gain=3.5),
        fx("compressor", threshold_db=-22.0, ratio=4.5, attack_ms=8.0, release_ms=80.0),
        fx("deesser"),
        fx("doubler", voices=4, detune_cents=10.0, delay_ms=20.0, mix=40.0),
        fx("harmonizer", enabled=False),
        fx("saturation", mode="tape", drive_db=4.0, mix=25.0),
        fx("delay", division="1/4", feedback=25.0, mix=12.0),
        fx("reverb", decay_s=1.6, predelay_ms=30.0, mix=16.0, width=1.3),
    ]


def chain_whisper():
    return _clean(nr=12.0, breath_db=-8.0) + [
        fx("eq", hpf_freq=100.0, presence_gain=1.5, air_gain=4.0),
        fx("vocal_rider", target_db=-16.0, max_boost_db=10.0),
        fx("compressor", threshold_db=-28.0, ratio=6.0, attack_ms=5.0),
        fx("deesser", max_reduction_db=7.0),
        fx("exciter", amount=25.0),
        fx("reverb", decay_s=0.9, mix=7.0),
    ]


def chain_beat():
    return [fx("beat_carve"), fx("eq", enabled=False, hpf_on=False, mud_gain=0.0, nasal_gain=0.0,
                                  presence_gain=0.0, air_gain=0.0)]


def chain_master():
    return [fx("mastering")]


STYLES = {
    "Clean Rap Lead": chain_clean_rap,
    "Hard Auto-Tune (Trap)": chain_hard_tune,
    "Melodic Rap": chain_melodic,
    "Drill / Aggressive": chain_drill,
    "Boom Bap / Old School": chain_boom_bap,
    "Lo-Fi / Phone": chain_lofi,
    "Ad-libs": chain_adlib,
    "Doubles / Stacks": chain_double,
    "Hook / Chorus": chain_hook,
    "Whisper / Intimate": chain_whisper,
    "Raw (no effects)": lambda: [],
}

ROLE_STYLE = {
    "Lead": "Clean Rap Lead", "Double": "Doubles / Stacks", "Ad-lib": "Ad-libs",
    "Hook": "Hook / Chorus", "Harmony": "Doubles / Stacks",
}


def style_chain(name):
    return STYLES.get(name, chain_clean_rap)()


# ------------------------------------------------------------- user presets on disk
def user_preset_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "SIB Studio Mixer", "presets")
    os.makedirs(d, exist_ok=True)
    return d


def save_user_preset(name, chain):
    path = os.path.join(user_preset_dir(), f"{_safe(name)}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"name": name, "chain": copy.deepcopy(chain)}, fh, indent=2)
    return path


def list_user_presets():
    out = {}
    d = user_preset_dir()
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as fh:
                    data = json.load(fh)
                out[data.get("name", fn[:-5])] = data["chain"]
            except Exception:
                pass
    return out


def delete_user_preset(name):
    p = os.path.join(user_preset_dir(), f"{_safe(name)}.json")
    if os.path.exists(p):
        os.remove(p)


def _safe(name):
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "preset"
