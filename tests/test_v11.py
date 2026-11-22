"""Tests for v1.1 features: lyrics, ad-libs, vocal edit tools, sends/throws/automation, coach,
Audition import."""
import os
import sys
import tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_core import make_vocal, make_beat, SR
from sib_studio.core import lyrics as L, adlibs, vocal_edit, coach, audition, audition_import, dsp
from sib_studio.core.project import Project, Marker
from sib_studio.core.audio_io import save_audio


def test_syllables_and_rhymes():
    assert L.syllables("money") == 2 and L.syllables("fire") >= 1 and L.syllables("booth") == 1
    r = L.find_rhymes("fire")
    assert "higher" in r["perfect"] and "desire" in r["perfect"]
    r = L.find_rhymes("money")
    assert "honey" in r["perfect"] and "funny" in r["perfect"]
    assert L.syllables("drippin") == 2  # slang falls back sensibly


def test_rhyme_groups_and_timing():
    txt = "[Verse]\nlighting up the fire\ntake it even higher\nthey can't deny\nnever asking why\n{2} long line here"
    g = L.rhyme_groups(txt, "end")
    lines = {k[0] for k in g}
    assert lines == {1, 2, 3, 4}
    sh = L.parse(txt, start_bar=5)
    assert [l.bar for l in sh.lines] == [5, 6, 7, 8, 9] and sh.lines[-1].bars == 2

    class P:
        sr, bpm, beat_offset_s, time_sig = SR, 90.0, 0.0, 4

        def samples_per_beat(self):
            return SR * 60 / 90
    lrc = L.to_lrc(sh, P())
    body, tim = L.from_lrc(lrc, SR)
    assert len(tim) == 5 and body.splitlines()[0] == "lighting up the fire"


def test_adlib_library_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        os.environ["APPDATA"] = d
        v, _ = make_vocal()
        for st in adlibs.STYLES:
            m = adlibs.save_adlib(v[: SR], SR, f"ay {st}", "Hype", st)
            assert m["length_s"] > 0.1
        assert len(adlibs.list_adlibs("ay")) == len(adlibs.STYLES)
        assert adlibs.ensure_starter_pack(SR) > 0
        pack = os.path.join(d, "pack.zip")
        adlibs.export_pack(pack)
        p = Project("a", SR, folder=os.path.join(d, "proj"))
        t = p.add_track("Ad-libs", "vocal", "Ad-lib")
        clips = adlibs.insert_into_track(p, t, adlibs.list_adlibs("ay")[0], SR, every_bars=1, until=SR * 12)
        assert len(clips) >= 3


def test_vocal_tools_sends_throws_automation():
    with tempfile.TemporaryDirectory() as d:
        p = Project("v", SR, folder=d)
        b = p.add_track("Beat", "beat")
        p.add_clip_from_array(b, make_beat(), 0, "beat")
        v, _ = make_vocal()
        lead = p.add_track("Lead", "vocal", "Lead")
        p.add_clip_from_array(lead, np.tile(v, 2)[:, None], 0, "lead")
        dbl = p.add_track("Dbl", "vocal", "Double")
        p.add_clip_from_array(dbl, np.concatenate([np.zeros(1323, np.float32), np.tile(v, 2)])[:, None], 0, "dbl")
        shifts = vocal_edit.align_to_guide(p, lead, dbl)
        assert shifts and abs(np.median(shifts) + 30) < 8      # 30 ms late double pulled back
        assert vocal_edit.strip_silence(p, lead) >= 1
        vocal_edit.quantize_phrases(p, lead)
        lead.throws = [dict(start=SR, end=int(SR * 1.4), kind=k) for k in ("delay", "reverb", "reverse")]
        lead.automation = [[0, 0.0], [SR * 2, -12.0]]
        lead.sends["reverb"] = -10.0
        off, a = p.track_audio(lead)
        assert np.isfinite(a).all()
        _, bus = p.render_send("reverb")
        assert len(bus) and np.max(np.abs(bus)) > 1e-4
        mix = p.mixdown()
        assert np.isfinite(mix).all()
        rep = coach.analyze_take(np.tile(v, 2), SR, 90.0)
        assert 0 <= rep["score"] <= 100 and rep["tips"]
        vocal_edit.beat_drop(p, [b], SR * 2, SR * 3)
        assert len(b.clips) == 2
        path = p.save()
        q = Project.open(path)
        assert q.track(lead.id).sends["reverb"] == -10.0 and len(q.track(lead.id).throws) == 3


def test_new_dsp():
    x = (np.random.default_rng(0).standard_normal((SR * 2, 2)) * 0.1).astype(np.float32)
    assert np.max(np.abs(dsp.soft_clipper(x * 8, SR, ceiling_db=-1))) <= dsp.db_to_lin(-1) + 1e-4
    y = dsp.spectral_attenuate(x, SR, SR * 0.5, SR * 1.5, 1000, 4000, -40)
    assert y.shape == x.shape
    c = dsp.match_eq_curve(x, dsp.lowpass(x, SR, 2000, 4), SR)
    assert max(c) <= 8 and min(c) >= -8 and np.mean(c[-6:]) < 0   # reference is darker -> cut highs
    assert dsp.stutter(x, SR, 90).shape == x.shape and dsp.tape_stop(x, SR).shape == x.shape


def test_audition_import_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = Project("rt", SR, folder=os.path.join(d, "p"))
        b = p.add_track("Beat", "beat")
        p.add_clip_from_array(b, make_beat(), 0, "beat")
        lead = p.add_track("Lead Vocal", "vocal", "Lead")
        c = p.add_clip_from_array(lead, make_vocal()[0][:, None], SR, "take")
        c.fade_in, c.gain_db, lead.volume_db = 400, -3.0, -2.5
        p.markers.append(Marker(SR * 2, "Hook"))
        ses = audition.export_session(p, os.path.join(d, "aud"), "dry")
        q, report = audition_import.import_sesx(ses, folder=os.path.join(d, "imp"))
        assert [t.kind for t in q.tracks] == ["beat", "vocal"]
        c2 = q.tracks[1].clips[0]
        assert (c2.start, c2.fade_in) == (SR, 400) and abs(c2.gain_db + 3) < 0.01
        assert abs(q.tracks[1].volume_db + 2.5) < 0.01 and q.markers[0].name == "Hook"
        assert np.isfinite(q.mixdown()).all()
