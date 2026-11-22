"""Import Adobe Audition multitrack sessions (.sesx, CC 2015+) into SIB Studio Mixer.

Brought across: tracks (name, volume, pan, mute, solo), every clip (position, source in/out,
clip gain, mute, fades), media files (found by relative path, absolute path or by searching
next to the session), tempo and time signature, markers, track effect racks, clip effect
racks, the master rack, the track EQ, and sends to reverb/delay buses (mapped onto SIB's
shared Reverb/Delay buses).

Audition saves effect settings as normalised 0-1 values, so each one is mapped onto the
matching SIB effect by parameter name; that makes them close starting points rather than
bit-exact copies. Effects SIB has no equivalent for (third-party plug-ins, Doppler shifter…)
are kept as bypassed "Audition effect" placeholders so nothing silently disappears. Every
decision is listed in the import report.
"""
from __future__ import annotations

import math
import os
import re
import xml.etree.ElementTree as ET
import numpy as np

from .project import Project, Clip, Marker, default_projects_dir
from .effects import REGISTRY, new_instance
from . import presets

STRUCTURAL_IDS = {"trackFader", "trackMute", "trackPan", "sendComponent", "sendPan", "clipGain", "clipMute",
                  "clipPan", "trackEQ", "clipFader", "clipPanner", "trackInput", "trackOutput"}
STRUCTURAL_COMPONENTS = ("audition.fader", "audition.mute", "audition.stereopanner", "audition.panner",
                         "audition.surroundpanner", "audition.monopanner", "audition.send")

# (keywords in the Audition effect name/id, SIB effect id) - first match wins, order matters
EFFECT_MAP = [
    (("tubemodeled", "tube"), "tube_comp"),
    (("multiband",), "multiband"),
    (("parametriceq", "parametricequalizer", "simpleparametric"), "eq"),
    (("graphicequalizer", "graphiceq"), "graphic_eq"),
    (("notch",), "notch"),
    (("fftfilter", "scientificfilter", "filter"), "filter"),
    (("speechvolumeleveler", "leveler"), "vocal_rider"),
    (("deesser", "de-esser"), "deesser"),
    (("hardlimiter", "limiter"), "limiter"),
    (("noisegate", "gate"), "gate"),
    (("singleband", "compressor", "dynamicsprocessing", "dynamics"), "compressor"),
    (("adaptivenoise", "noisereduction", "denoise", "hissreduction", "noise"), "noise_reduction"),
    (("dehummer", "hum"), "hum_remover"),
    (("declicker", "click", "crackle"), "declick"),
    (("pitchcorrection", "autotune", "automaticpitch", "manualpitch"), "sib_tune"),
    (("pitchshifter", "pitchbender", "pitch"), "pitch_shift"),
    (("centerchannelextractor", "vocalremov"), "vocal_remover"),
    (("reverb", "convolution"), "reverb"),
    (("delay", "echo", "multitap"), "delay"),
    (("flanger",), "flanger"),
    (("phaser",), "phaser"),
    (("chorus",), "chorus"),
    (("distortion", "guitarsuite", "overdrive", "saturat"), "saturation"),
    (("stereoexpander", "stereoimager", "stereowidth", "expander"), "stereo"),
    (("amplify", "gain"), "gain"),
    (("normalize", "matchloudness", "loudness"), "normalize"),
    (("mastering",), "mastering"),
    (("vocalenhancer",), "vocal_enhancer"),
    (("channelmixer", "invert"), "channel_tools"),
    (("bass", "treble"), "tone"),
]

# well-known third-party plug-ins -> closest SIB effect (their settings are proprietary, so defaults)
PLUGIN_MAP = [
    (("autotune", "melodyne", "wavestune", "graillon", "metatune", "pitchcorrect", "nectar", "revoice"), "sib_tune"),
    (("proq", "eq8", "ssleq", "pultec", "eqp1a", "renaissanceeq", "neutron"), "eq"),
    (("prods", "deess", "sibilance"), "deesser"),
    (("prol", "l1", "l2", "limitless"), "limiter"),
    (("proc", "cla76", "1176", "la2a", "rcomp", "rvox", "vcomp", "ssl", "compressor", "kotelnikov"), "compressor"),
    (("valhalla", "rverb", "abbeyroad", "h-reverb", "hreverb", "reverb", "plate"), "reverb"),
    (("echoboy", "hdelay", "h-delay", "supertap", "timeless", "delay", "echo"), "delay"),
    (("decapitator", "saturn", "kramer", "j37", "tape", "trash", "saturat", "devilloc"), "saturation"),
    (("ozone", "mastering"), "mastering"),
    (("rx", "denoise", "nsx", "clarity", "noise"), "noise_reduction"),
    (("doubler", "microshift", "vocalign"), "doubler"),
    (("soothe", "resonance", "dynamiceq"), "dynamic_eq"),
    (("clipper", "standardclip", "kclip"), "soft_clipper"),
]

# SIB param key -> keywords that identify the Audition parameter (English + common French)
PARAM_WORDS = {
    "threshold_db": ("threshold", "seuil"), "ratio": ("ratio",), "attack_ms": ("attack", "attaque"),
    "release_ms": ("release", "relâchement", "rel"), "makeup_db": ("output gain", "makeup", "gain de sortie"),
    "gain_db": ("gain", "amplif"), "freq": ("frequency", "freq", "fréquence", "center"),
    "mix": ("mix", "dry/wet", "wet", "mélange"), "feedback": ("feedback", "réinjection"),
    "decay_s": ("decay", "time", "durée", "rt60"), "predelay_ms": ("pre-delay", "predelay", "pré-délai"),
    "time_ms": ("delay time", "time", "durée", "délai"), "ceiling_db": ("ceiling", "maximum amplitude", "plafond"),
    "input_db": ("input boost", "input gain"), "reduction_db": ("reduction", "réduction", "amount"),
    "semitones": ("semitone", "demi-ton"), "cents": ("cent",), "width": ("width", "expand", "largeur"),
    "depth": ("depth", "profondeur"), "rate_hz": ("rate", "speed", "vitesse"), "drive_db": ("drive", "gain"),
    "max_reduction_db": ("max", "reduction"), "retune_ms": ("speed", "attack", "vitesse"),
    "target_lufs": ("target", "loudness"), "lufs": ("target", "loudness"), "peak_db": ("peak", "normalize to"),
    "q": ("q", "width", "bandwidth"), "damping": ("damping", "amortissement"),
}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _float(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _params(comp):
    out = {}
    for p in comp.findall("parameter"):
        name = p.get("name") or f"p{p.get('index')}"
        out[name] = _float(p.get("parameterValue"))
    return out


def _lin_to_db(v):
    return 20 * math.log10(v) if v > 1e-6 else -60.0


def _map_value(param, raw):
    """Audition values may be real units or normalised 0..1 - place them in SIB's range."""
    lo, hi = param.min, param.max
    if param.kind == "bool":
        return bool(raw >= 0.5)
    if param.kind == "choice":
        return param.default
    if lo is None or hi is None:
        return param.default
    if lo <= raw <= hi and not (0 < raw < 1 and hi > 2):
        v = raw
    elif 0.0 <= raw <= 1.0:
        if param.log and lo > 0:
            v = math.exp(math.log(lo) + raw * (math.log(hi) - math.log(lo)))
        else:
            v = lo + raw * (hi - lo)
    else:
        v = min(max(raw, lo), hi)
    if param.kind == "int":
        return int(round(v))
    return float(v)


def map_effect(comp):
    """-> (sib effect instance, note) for an Audition effect component."""
    label = f"{comp.get('name', '')} {comp.get('componentID', '')}"
    key = _norm(label)
    raw = _params(comp)
    powered = (comp.get("powered", "true").lower() != "false")
    sib_id = None
    for words, eid in EFFECT_MAP:
        if any(_norm(w) in key for w in words):
            sib_id = eid
            break
    is_plugin = any(t in key for t in ("vst", "aax", "audiounit"))
    nice = comp.get("name") or comp.get("componentID") or "effect"
    if is_plugin:
        for words, eid in PLUGIN_MAP:
            if any(_norm(w) in key for w in words):
                inst = new_instance(eid, enabled=powered)
                return inst, (f"plug-in '{nice}' replaced with {REGISTRY[eid].name} (default settings - "
                              f"plug-in settings can't be read)")
    if sib_id is None or is_plugin:
        inst = new_instance("audition_fx", enabled=False, name=nice, raw=raw)
        why = "third-party plug-in" if is_plugin else "no SIB equivalent yet"
        return inst, f"'{nice}' kept as a bypassed placeholder ({why})"
    d = REGISTRY[sib_id]
    params = d.defaults()
    mapped = []
    for prm in d.params:
        if prm.kind == "hidden":
            continue
        words = PARAM_WORDS.get(prm.key, (prm.label.lower(),))
        for aname, aval in raw.items():
            an = aname.lower()
            if any(w in an for w in words):
                params[prm.key] = _map_value(prm, aval)
                mapped.append(prm.label)
                break
    if sib_id in ("delay", "reverb", "chorus", "flanger", "phaser") and "mix" not in [p.lower() for p in mapped]:
        params["mix"] = 25.0 if sib_id != "reverb" else 20.0
    inst = new_instance(sib_id, enabled=powered, **params)
    note = f"'{nice}' -> {d.name}" + (f" (settings mapped: {', '.join(mapped[:6])})" if mapped else " (default settings)")
    if not powered:
        note += " [was switched off in Audition]"
    return inst, note


def _track_eq(comp):
    """Audition's built-in track EQ (normalised: gain 0.5 = 0 dB, +-15 dB range)."""
    raw = list(_params(comp).values())
    inst = new_instance("eq", hpf_on=False, mud_gain=0.0, nasal_gain=0.0, presence_gain=0.0, air_gain=0.0)
    if len(raw) >= 4:
        inst["params"]["low_shelf_gain"] = round((raw[1] - 0.5) * 30, 2)
        inst["params"]["air_gain"] = round((raw[3] - 0.5) * 30, 2)
    bands = [raw[i] for i in (6, 10, 14) if i < len(raw)]
    for key, v in zip(("mud_gain", "nasal_gain", "presence_gain"), bands):
        inst["params"][key] = round((v - 0.5) * 30, 2)
    return inst


def _guess_role(name):
    n = name.lower()
    if re.search(r"beat|instrumental|\binst\b|music|track \(beat\)|808|drums", n):
        return "beat", "Beat"
    if re.search(r"ad.?lib", n):
        return "vocal", "Ad-lib"
    if re.search(r"double|dbl|\bdub|stack", n):
        return "vocal", "Double"
    if re.search(r"hook|chorus", n):
        return "vocal", "Hook"
    if re.search(r"harm", n):
        return "vocal", "Harmony"
    return "vocal", "Lead"


def _find_media(info, base_dir, cache):
    rel = (info.get("relativePath") or "").replace("\\", os.sep).replace("/", os.sep)
    ab = info.get("absolutePath") or ""
    cands = []
    if rel:
        cands.append(os.path.join(base_dir, rel))
    if ab:
        cands.append(ab)
        cands.append(ab.replace("\\", os.sep))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    name = os.path.basename((rel or ab).replace("\\", "/"))
    if not name:
        return None
    if not cache:
        for root, _, files in os.walk(base_dir):
            for f in files:
                cache.setdefault(f.lower(), os.path.join(root, f))
            if len(cache) > 20000:
                break
    return cache.get(name.lower())


def _markers(session, sr):
    out = []
    xmp = session.find("xmpMetadata")
    if xmp is None or not (xmp.text or "").strip():
        return out
    txt = xmp.text
    for m in re.finditer(r"<rdf:li[^>]*>(.*?)</rdf:li>", txt, re.S):
        blk = m.group(1)
        st = re.search(r"<xmpDM:startTime>([^<]+)</xmpDM:startTime>", blk)
        nm = re.search(r"<xmpDM:name>([^<]*)</xmpDM:name>", blk)
        if not st:
            continue
        val = st.group(1).strip()
        try:
            if "f" in val:  # "12345f44100" style
                a, b = val.split("f", 1)
                pos = int(float(a) / float(b or sr) * sr)
            else:
                pos = int(float(val))
        except ValueError:
            continue
        out.append((pos, (nm.group(1) if nm else "Marker") or "Marker"))
    return out


def import_sesx(path, folder=None, progress=None):
    """-> (Project, report_lines)"""
    say = progress or (lambda m: None)
    tree = ET.parse(path)
    root = tree.getroot()
    session = root.find("session")
    if session is None:
        raise ValueError("Not an Audition multitrack session (.sesx)")
    sr = int(_float(session.get("sampleRate"), 44100))
    name = os.path.splitext(os.path.basename(path))[0]
    base_dir = os.path.dirname(os.path.abspath(path))
    folder = folder or os.path.join(default_projects_dir(), f"{name} (from Audition)")
    p = Project(name, sr, folder=folder)
    p.master_fx = []
    report = [f"Imported Audition session '{name}' ({sr} Hz)"]
    files = {f.get("id"): f.attrib for f in root.iter("file")}
    cache = {}
    fid_map = {}
    missing = []

    def media(file_id):
        if file_id in fid_map:
            return fid_map[file_id]
        info = files.get(file_id)
        src = _find_media(info, base_dir, cache) if info else None
        if not src:
            missing.append((info or {}).get("relativePath") or (info or {}).get("absolutePath") or file_id)
            fid_map[file_id] = None
            return None
        say(f"Loading {os.path.basename(src)}…")
        try:
            fid_map[file_id] = p.pool.import_file(src, os.path.splitext(os.path.basename(src))[0])
        except Exception as e:
            missing.append(f"{src} ({e})")
            fid_map[file_id] = None
        return fid_map[file_id]

    ts = session.find(".//timeFormatState")
    if ts is not None:
        p.bpm = _float(ts.get("beatsPerMinute"), 90.0) or 90.0
        p.time_sig = int(_float(ts.get("beatsPerBar"), 4)) or 4
        report.append(f"Tempo {p.bpm:g} BPM, {p.time_sig}/4")
    tracks_el = session.find("tracks")
    buses = {}
    send_targets = []   # (track, bus_id, level_db)
    children = {}       # track id -> clip-FX tracks split from it
    for el in list(tracks_el) if tracks_el is not None else []:
        tag = el.tag
        tname_el = el.find("trackParameters/name")
        tname = (tname_el.text if tname_el is not None and tname_el.text else tag)
        tap = el.find("trackAudioParameters")
        clip_comps = {id(c) for ce in el.iter("audioClip") for c in ce.iter("component")}
        # effects: any component on the track that isn't a fader/mute/pan/send
        rack = []
        eq_inst = None
        vol_db, pan, mute = 0.0, 0.0, False
        for c in el.iter("component"):
            cid = c.get("id", "")
            comp_id = (c.get("componentID") or "").lower()
            if id(c) in clip_comps:
                continue
            if cid == "trackFader" or comp_id == "audition.fader":
                vals = list(_params(c).values())
                if vals:
                    vol_db = _lin_to_db(vals[0])
            elif cid == "trackMute" or comp_id == "audition.mute":
                vals = list(_params(c).values())
                mute = bool(vals and max(vals) >= 0.5)
            elif cid == "trackPan" or comp_id.endswith("panner"):
                vals = list(_params(c).values())
                if vals:
                    v = vals[0]
                    pan = v if abs(v) <= 1 else max(-1.0, min(1.0, v / 100.0))
            elif cid == "trackEQ" or comp_id == "audition.eq":
                if c.get("powered", "false").lower() == "true":
                    eq_inst = _track_eq(c)
            elif cid in STRUCTURAL_IDS or comp_id in STRUCTURAL_COMPONENTS or not (c.get("componentID") or c.get("name")):
                continue
            else:
                inst, note = map_effect(c)
                rack.append((inst, note))
        if tag == "masterTrack":
            p.master_fx = [i for i, _ in rack]
            if eq_inst:
                p.master_fx.insert(0, eq_inst)
            m = presets.chain_master()[0]
            m["enabled"] = False
            p.master_fx.append(m)
            p.master_volume_db = vol_db
            report.append(f"Master: {len(rack)} effect(s)" + "".join(f"\n   - {n}" for _, n in rack) +
                          "\n   + SIB Mastering added (switched off - turn it on for a loud master)")
            continue
        if tag == "busTrack":
            buses[el.get("id")] = dict(name=tname, rack=rack, vol=vol_db)
            continue
        if tag != "audioTrack":
            continue
        kind, role = _guess_role(tname)
        t = p.add_track(tname, kind, role, style="Raw (no effects)")
        t.fx = [i for i, _ in rack]
        if eq_inst:
            t.fx.insert(0, eq_inst)
        t.volume_db, t.pan, t.mute = vol_db, pan, mute
        t.solo = (tap is not None and tap.get("solo", "false").lower() == "true")
        notes = [n for _, n in rack]
        # sends
        for slot in el.iter("sendSlot"):
            sc = next((c for c in slot.iter("component") if c.get("id") == "sendComponent"), None)
            if sc is None or sc.get("powered", "false").lower() != "true":
                continue
            vals = list(_params(sc).values())
            lvl = vals[1] if len(vals) > 1 else (vals[0] if vals else 0)
            if lvl <= 0:
                continue
            level_db = _lin_to_db(lvl) if lvl <= 4 else 0.0
            send_targets.append((t, slot.get("sendOutputID"), level_db))
        # clips
        split_tracks = {}
        children[t.id] = []
        for ce in el.iter("audioClip"):
            fid = media(ce.get("fileID"))
            start = int(_float(ce.get("startPoint")))
            end = int(_float(ce.get("endPoint")))
            sin = int(_float(ce.get("sourceInPoint")))
            length = max(0, end - start)
            if fid is None or length <= 0:
                continue
            gain_db, cmute = 0.0, False
            clip_fx = []
            for c in ce.iter("component"):
                cid = c.get("id", "")
                comp_id = (c.get("componentID") or "").lower()
                if cid in ("clipGain", "clipFader") or comp_id == "audition.fader":
                    vals = list(_params(c).values())
                    gain_db = _lin_to_db(vals[0]) if vals else 0.0
                elif cid == "clipMute" or comp_id == "audition.mute":
                    vals = list(_params(c).values())
                    cmute = bool(vals and max(vals) >= 0.5)
                elif cid in STRUCTURAL_IDS or comp_id in STRUCTURAL_COMPONENTS or not c.get("componentID"):
                    continue
                else:
                    clip_fx.append(map_effect(c))
            fi = ce.find("fadeIn")
            fo = ce.find("fadeOut")
            fade_in = int(_float(fi.get("endPoint")) - _float(fi.get("startPoint"))) if fi is not None else 0
            fade_out = int(_float(fo.get("endPoint")) - _float(fo.get("startPoint"))) if fo is not None else 0
            src_len = len(p.pool.get(fid))
            length = min(length, max(0, src_len - sin))
            clip = Clip(fid, start, length, sin, gain_db, max(0, min(fade_in, length)), max(0, min(fade_out, length)),
                        cmute, ce.get("name") or "Clip")
            if clip_fx:
                # SIB has effects per track: give the clip its own track with its effects + the track's
                key = tuple(i["id"] for i, _ in clip_fx)
                if key not in split_tracks:
                    st = p.add_track(f"{tname} (clip FX)", kind, role, style="Raw (no effects)")
                    st.fx = [i for i, _ in clip_fx] + [dict(e, uid=e["uid"] + "c") for e in t.fx]
                    st.volume_db, st.pan, st.mute = t.volume_db, t.pan, t.mute
                    split_tracks[key] = st
                    children[t.id].append(st)
                    notes.append(f"clip effects on '{clip.name}' -> moved to track '{st.name}': "
                                 + "; ".join(n for _, n in clip_fx))
                split_tracks[key].clips.append(clip)
            else:
                t.clips.append(clip)
        report.append(f"Track '{tname}' -> {kind}{'' if kind == 'beat' else ' (' + role + ')'}: {len(t.clips)} clip(s), "
                      f"vol {t.volume_db:+.1f} dB, pan {t.pan:+.2f}{', muted' if t.mute else ''}"
                      + "".join(f"\n   - {n}" for n in notes))
    # buses -> SIB send buses
    for t, bus_id, lvl in send_targets:
        bus = buses.get(bus_id)
        if not bus:
            continue
        ids = [i["id"] for i, _ in bus["rack"]]
        target = "reverb" if "reverb" in ids else ("delay" if "delay" in ids else None)
        if target is None:
            report.append(f"Send from '{t.name}' to bus '{bus['name']}' skipped (bus has no reverb/delay)")
            continue
        t.sends[target] = max(-59.0, min(6.0, lvl))
        for ch in children.get(t.id, []):
            ch.sends[target] = t.sends[target]
        fx = [dict(i) for i, _ in bus["rack"] if i["id"] == target]
        if fx:
            fx[0]["params"]["wet_only"] = True
            fx[0]["params"]["mix"] = 100.0
            p.sends[target]["fx"] = fx[:1]
            p.sends[target]["volume_db"] = bus["vol"]
        report.append(f"Send '{t.name}' -> bus '{bus['name']}' mapped to SIB {target.title()} bus at {lvl:+.1f} dB")
    for bid, bus in buses.items():
        if bus["rack"] and not any(b == bid for _, b, _ in send_targets):
            report.append(f"Bus '{bus['name']}' ({len(bus['rack'])} effects) had no sends feeding it - not imported")
    for pos, nm in _markers(session, sr):
        p.markers.append(Marker(int(pos), nm))
    if p.markers:
        report.append(f"{len(p.markers)} marker(s)")
    if missing:
        report.append("MISSING MEDIA (clips skipped): " + ", ".join(sorted(set(map(str, missing)))[:20]))
    p.dirty = True
    return p, report
