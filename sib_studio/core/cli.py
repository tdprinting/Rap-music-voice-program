"""Command line: process vocals over a beat without opening the studio.

Examples
  sib-cli analyze beat.mp3
  sib-cli mix --beat beat.mp3 --vocal verse.wav --adlib adlibs.wav --style "Hard Auto-Tune (Trap)" \
          --out song.wav --stems --audition
  sib-cli styles
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from .audio_io import load_audio, save_audio
from . import analysis, automatch, presets
from .project import Project
from . import audition


def _print_progress(name, frac, step):
    sys.stdout.write(f"\r  {name}: {step:<38s} {frac * 100:5.1f}%")
    sys.stdout.flush()


def cmd_analyze(a):
    x, sr = load_audio(a.file)
    r = analysis.analyze_beat(x, sr)
    print(f"Key:      {r.key['name']}  (confidence {r.key['confidence']:.0%}, relative {r.key['relative']})")
    print(f"Also:     {', '.join(n for n, _ in r.key['candidates'][1:4])}")
    print(f"Tempo:    {r.bpm:.1f} BPM  (or {', '.join(f'{b:.1f}' for b in r.bpm_alternatives)})")
    print(f"Tuning:   A4 = {r.a4_hz:.2f} Hz ({r.tuning_cents:+.1f} cents)")
    print(f"Loudness: {r.lufs:.1f} LUFS  peak {r.peak_db:.1f} dBFS  length {r.duration_s:.1f}s")


def cmd_mix(a):
    proj_dir = a.project or os.path.join(os.path.dirname(os.path.abspath(a.out)), "SIB_project")
    p = Project(os.path.splitext(os.path.basename(a.out))[0], a.sr, folder=proj_dir)
    bt = p.add_track("Beat", "beat")
    p.import_audio_to_track(a.beat, bt)
    for role, files in (("Lead", a.vocal), ("Double", a.double), ("Ad-lib", a.adlib), ("Hook", a.hook)):
        for i, f in enumerate(files or []):
            t = p.add_track(f"{role} {i + 1}", "vocal", role)
            if role == "Double" and i % 2 == 1:
                t.pan = -t.pan
            p.import_audio_to_track(f, t, int(a.offset * p.sr))
    style = a.style if a.style != "auto" else None
    rep = automatch.auto_match(p, style=style if style else None, tune_mode=a.tune,
                               prominence_lu=a.prominence, apply_timing=a.nudge, log=print)
    print(automatch.format_report(rep))
    if a.no_master:
        p.master_fx = []
    else:
        p.master_fx[0]["params"]["target_lufs"] = a.lufs
    t0 = time.time()
    mix = p.mixdown(progress=_print_progress)
    print()
    save_audio(a.out, mix, p.sr, 24)
    print(f"Wrote {a.out}  ({analysis.integrated_lufs(mix, p.sr):.1f} LUFS, {time.time() - t0:.1f}s)")
    if a.stems:
        d = os.path.splitext(a.out)[0] + "_stems"
        for t in p.tracks:
            off, y = p.render_track(t)
            full = __import__("numpy").zeros((p.length + len(y), 2), "float32")
            full[off:off + len(y)] = y
            save_audio(os.path.join(d, f"{t.name}.wav"), full[: max(p.length, off + len(y))], p.sr, 24)
        print(f"Stems in {d}")
    p.save()
    print(f"Project saved: {p.saved_path}")
    if a.audition:
        s = audition.export_session(p, os.path.join(proj_dir, "Audition"), "both")
        print(f"Audition session: {s}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sib-cli", description="SIB Studio Mixer command line")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("analyze", help="detect key / BPM / tuning / loudness of a beat")
    s.add_argument("file")
    s.set_defaults(fn=cmd_analyze)
    m = sub.add_parser("mix", help="auto-match vocals to a beat and export a mix")
    m.add_argument("--beat", required=True)
    m.add_argument("--vocal", nargs="*", default=[])
    m.add_argument("--double", nargs="*", default=[])
    m.add_argument("--adlib", nargs="*", default=[])
    m.add_argument("--hook", nargs="*", default=[])
    m.add_argument("--style", default="auto", help="style preset name or 'auto'")
    m.add_argument("--tune", default="auto", choices=["auto", "rap", "melodic", "hard", "off"])
    m.add_argument("--prominence", type=float, default=1.5, help="vocal loudness vs beat (LU)")
    m.add_argument("--lufs", type=float, default=-10.0, help="master loudness target")
    m.add_argument("--offset", type=float, default=0.0, help="vocal start time in seconds")
    m.add_argument("--nudge", action="store_true", help="auto-nudge vocal timing to the beat grid")
    m.add_argument("--sr", type=int, default=44100)
    m.add_argument("--out", required=True)
    m.add_argument("--project")
    m.add_argument("--stems", action="store_true")
    m.add_argument("--no-master", action="store_true")
    m.add_argument("--audition", action="store_true", help="also write an Adobe Audition .sesx session")
    m.set_defaults(fn=cmd_mix)
    st = sub.add_parser("styles", help="list style presets")
    st.set_defaults(fn=lambda a: print("\n".join(presets.STYLES)))
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
