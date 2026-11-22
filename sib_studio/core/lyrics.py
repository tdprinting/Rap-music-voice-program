"""Lyrics engine: syllables, rhymes (perfect / slant / multi), rhyme-scheme colouring,
song structure, bar timing for the teleprompter, and LRC export/import.

Pronunciations come from the CMU Pronouncing Dictionary (134k words, bundled, offline).
Words that aren't in it (slang, names) fall back to a spelling-based guesser, so rhymes
still light up for "drippin'", "skrrt" or a made-up word.
"""
from __future__ import annotations

import gzip
import os
import re
from dataclasses import dataclass, field

_DICT = None
_FREQ = None
VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}
SECTION_RE = re.compile(r"^\s*[\[(]\s*([^\])]+?)\s*[\])]\s*$|^\s*(intro|verse|hook|chorus|pre-?hook|pre-?chorus|bridge|outro|break|interlude|refrain)\b[^a-z]*\d*\s*:?\s*$",
                        re.I)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")

# vowel sound families used for slant rhymes (rappers rhyme by vowel)
SLANT = {"AA": "A", "AO": "A", "AH": "U", "UH": "U", "ER": "R", "IH": "I", "IY": "E", "EH": "e",
         "AE": "a", "EY": "ay", "AY": "ai", "OW": "o", "UW": "oo", "AW": "aw", "OY": "oy"}


def _load():
    global _DICT
    if _DICT is not None:
        return _DICT
    _DICT = {}
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cmudict.dict.gz")
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                parts = line.split("#")[0].split()
                if len(parts) < 2:
                    continue
                w = parts[0].lower()
                if "(" in w:
                    continue  # keep the primary pronunciation
                _DICT[w] = parts[1:]
    except Exception:
        pass
    return _DICT


def _freq():
    """word -> rank (0 = most common), from movie/TV subtitles: everyday + slang words."""
    global _FREQ
    if _FREQ is None:
        _FREQ = {}
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "word_freq.txt.gz")
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for i, line in enumerate(fh):
                    w = line.strip()
                    if w and w not in _FREQ:
                        _FREQ[w] = i
        except Exception:
            pass
    return _FREQ


def dictionary_size():
    return len(_load())


# ------------------------------------------------------------------ pronunciation
_GUESS = [  # (spelling, phoneme) ordered longest-first
    ("igh", "AY"), ("eigh", "EY"), ("augh", "AO"), ("ough", "AO"), ("tion", "AH"),
    ("ee", "IY"), ("ea", "IY"), ("ie", "IY"), ("ey", "IY"), ("oo", "UW"), ("ou", "AW"), ("ow", "OW"),
    ("oi", "OY"), ("oy", "OY"), ("ai", "EY"), ("ay", "EY"), ("au", "AO"), ("aw", "AO"), ("ew", "UW"),
    ("ue", "UW"), ("oa", "OW"), ("er", "ER"), ("ir", "ER"), ("ur", "ER"), ("ar", "AA"), ("or", "AO"),
    ("a", "AE"), ("e", "EH"), ("i", "IH"), ("o", "AA"), ("u", "AH"), ("y", "IY"),
]


def _guess(word):
    w = word.lower().strip("'")
    phones = []
    # magic-e: "a_e" -> EY, "i_e" -> AY, "o_e" -> OW, "u_e" -> UW
    magic = re.match(r".*([aeiou])([^aeiouy])e$", w)
    i = 0
    while i < len(w):
        if magic and i == len(w) - 3:
            phones.append({"a": "EY1", "i": "AY1", "o": "OW1", "u": "UW1", "e": "IY1"}[w[i]])
            phones.append(w[i + 1].upper())
            break
        for sp, ph in _GUESS:
            if w.startswith(sp, i):
                if sp == "y" and (i == 0 or (i < len(w) - 1)):
                    if i == 0:
                        phones.append("Y")
                        i += 1
                        break
                phones.append(ph + "1" if ph in VOWELS else ph)
                i += len(sp)
                break
        else:
            ch = w[i]
            if ch.isalpha():
                phones.append(ch.upper())
            i += 1
    if not any(p[:2] in VOWELS for p in phones):
        phones.append("AH1")
    return phones


def phones(word):
    w = word.lower().strip("'").strip()
    if not w:
        return []
    d = _load()
    if w in d:
        return d[w]
    # rap spellings: drippin' -> dripping, gon' -> gonna, etc.
    for cand in (w + "g" if w.endswith("in") else None, w[:-1] + "ing" if w.endswith("in'") else None,
                 w.replace("'", ""), w.rstrip("z") + "s" if w.endswith("z") else None):
        if cand and cand in d:
            return d[cand]
    return _guess(w)


def syllables(word):
    return sum(1 for p in phones(word) if p[:2] in VOWELS) or (1 if word.strip("'") else 0)


def line_syllables(line):
    return sum(syllables(w) for w in WORD_RE.findall(line))


def _vowel_idx(ph):
    return [i for i, p in enumerate(ph) if p[:2] in VOWELS]


def _last_stressed(ph):
    vi = _vowel_idx(ph)
    if not vi:
        return None
    for i in reversed(vi):
        if ph[i][-1] in "12":
            return i
    return vi[-1]


def perfect_key(word):
    ph = phones(word)
    i = _last_stressed(ph)
    if i is None:
        return None
    return " ".join(p.rstrip("012") for p in ph[i:])


def slant_key(word, n_vowels=2):
    """Vowel-sound pattern of the rhyming tail (what rappers actually match)."""
    ph = phones(word)
    vi = _vowel_idx(ph)
    if not vi:
        return None
    i = _last_stressed(ph)
    tail = [p for p in vi if p >= i] if i is not None else vi[-1:]
    if len(tail) < n_vowels:
        tail = vi[-n_vowels:]
    tail = tail[-n_vowels:]
    return "-".join(SLANT.get(ph[t][:2], ph[t][:2]) for t in tail)


def tail_key(word):
    """Vowel families from the last stressed vowel to the end ("fire"/"higher" -> ai-R)."""
    ph = phones(word)
    i = _last_stressed(ph)
    if i is None:
        return None
    return "-".join(SLANT.get(p[:2], p[:2]) for p in ph[i:] if p[:2] in VOWELS)


def find_rhymes(word, limit=60, common_only=True):
    """-> dict(perfect=[...], near=[...], multi=[...])

    perfect: same sounds from the stressed vowel on (cat/hat)
    multi:   same last two vowel sounds (rapper multis: "fire"/"higher"/"buyer")
    near:    same stressed vowel sound (slant: "time"/"mind"/"right")"""
    d = _load()
    w = word.lower().strip().strip("'")
    if not w:
        return dict(perfect=[], near=[], multi=[])
    my = phones(w)
    pk = perfect_key(w)
    sk1 = slant_key(w, 1)
    sk2 = slant_key(w, 2)
    syl = syllables(w)
    last_cons = my[-1].rstrip("012") if my and my[-1][:2] not in VOWELS else ""
    perfect, near, near_best, multi = [], [], [], []
    if not d or pk is None:
        return dict(perfect=[], near=[], multi=[])
    fr = _freq()
    for cand, ph in d.items():
        if cand == w or not cand.isalpha():
            continue
        if common_only and fr and cand not in fr:
            continue
        i = _last_stressed(ph)
        if i is None:
            continue
        if " ".join(p.rstrip("012") for p in ph[i:]) == pk:
            perfect.append(cand)
            continue
        vi = _vowel_idx(ph)
        tail = [v for v in vi if v >= i] or vi[-1:]
        if syl >= 2 and len(vi) >= 2:
            t2 = (tail if len(tail) >= 2 else vi[-2:])[-2:]
            if "-".join(SLANT.get(ph[t][:2], "") for t in t2) == sk2:
                multi.append(cand)
                continue
        if SLANT.get(ph[tail[-1]][:2]) == sk1:
            end = ph[-1].rstrip("012") if ph[-1][:2] not in VOWELS else ""
            (near_best if end == last_cons else near).append(cand)

    def rank(lst):
        return sorted(lst, key=lambda c: (fr.get(c, 10 ** 6) // 4000, abs(syllables(c) - syl), fr.get(c, 10 ** 6)))[:limit]
    return dict(perfect=rank(perfect), near=(rank(near_best) + rank(near))[:limit], multi=rank(multi))


# ------------------------------------------------------------------ song structure
@dataclass
class LyricLine:
    index: int          # line number in the text
    text: str
    section: str
    syllables: int
    bar: float = 0.0    # bar where this line starts (1-based)
    bars: float = 1.0
    time: int | None = None   # synced sample position (tap-sync), overrides bar


@dataclass
class LyricSheet:
    lines: list = field(default_factory=list)
    sections: list = field(default_factory=list)   # (name, first line index)


def is_section(line):
    return bool(SECTION_RE.match(line)) and len(line) < 40


def parse(text, start_bar=1.0, bars_per_line=1.0, timings=None, section_bars=None):
    """Lay the lyric lines on the bar grid. Blank lines are skipped; section headers are
    labels. timings: {line_index: sample} from tap-sync. section_bars: {section_line_index: bar}
    lets a section start on a specific bar."""
    timings = timings or {}
    section_bars = section_bars or {}
    sheet = LyricSheet()
    bar = float(start_bar)
    section = ""
    for i, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        if is_section(line):
            section = re.sub(r"[\[\]():]", "", line).strip()
            sheet.sections.append((section, i))
            if str(i) in section_bars or i in section_bars:
                bar = float(section_bars.get(str(i), section_bars.get(i)))
            continue
        m = re.match(r"^\s*\{(\d+(?:\.\d+)?)\}\s*(.*)$", line)   # "{2} long line" = spans 2 bars
        nbars = bars_per_line
        if m:
            nbars = float(m.group(1))
            line = m.group(2)
        t = timings.get(str(i), timings.get(i))
        sheet.lines.append(LyricLine(i, line, section, line_syllables(line), bar, nbars,
                                     int(t) if t is not None else None))
        bar += nbars
    return sheet


def line_times(sheet, project):
    """-> list of (start_sample, end_sample, LyricLine) using tap-sync times when present."""
    spb = project.samples_per_beat()
    off = project.beat_offset_s * project.sr
    starts = []
    for ln in sheet.lines:
        s = ln.time if ln.time is not None else int(off + (ln.bar - 1) * project.time_sig * spb)
        starts.append(s)
    out = []
    for k, ln in enumerate(sheet.lines):
        nominal = starts[k] + int(ln.bars * project.time_sig * spb)
        end = min(nominal, starts[k + 1]) if k + 1 < len(starts) and starts[k + 1] > starts[k] else nominal
        out.append((starts[k], max(end, starts[k] + 1), ln))
    return out


def rhyme_groups(text, mode="end", strict=False):
    """Colour groups for the rhyme highlighter.
    mode: 'end' (last word of each line), 'all' (every word with 1+ syllables), 'off'.
    -> {(line_index, start_char, end_char): group_number}"""
    if mode == "off":
        return {}
    keys = {}
    spans = []
    for li, line in enumerate(text.splitlines()):
        if not line.strip() or is_section(line.strip()):
            continue
        words = list(WORD_RE.finditer(line))
        if not words:
            continue
        chosen = words[-1:] if mode == "end" else words
        for m in chosen:
            w = m.group(0)
            if mode == "all" and len(w) < 3:
                continue
            k = perfect_key(w) if strict else tail_key(w)
            if not k:
                continue
            spans.append((li, m.start(), m.end(), k))
            keys[k] = keys.get(k, 0) + 1
    groups = {}
    order = {}
    for li, a, b, k in spans:
        if keys[k] < 2:
            continue
        if k not in order:
            order[k] = len(order)
        groups[(li, a, b)] = order[k]
    return groups


def stats(sheet, bpm, beats_per_bar=4):
    if not sheet.lines:
        return dict(lines=0, words=0, bars=0, avg_syl=0, syl_per_sec=0)
    words = sum(len(WORD_RE.findall(l.text)) for l in sheet.lines)
    bars = sum(l.bars for l in sheet.lines)
    syl = sum(l.syllables for l in sheet.lines)
    secs = bars * beats_per_bar * 60.0 / max(bpm, 1)
    return dict(lines=len(sheet.lines), words=words, bars=bars, avg_syl=syl / max(bars, 1e-9),
                syl_per_sec=syl / max(secs, 1e-9))


def density_label(syl_per_bar):
    if syl_per_bar < 8:
        return "laid back"
    if syl_per_bar < 13:
        return "standard"
    if syl_per_bar < 17:
        return "busy"
    return "double-time"


TEMPLATES = {
    "16-bar verse": "[Verse]\n" + "\n" * 16,
    "8-bar hook": "[Hook]\n" + "\n" * 8,
    "4-bar intro": "[Intro]\n" + "\n" * 4,
    "Full song (Intro-Hook-V1-Hook-V2-Hook-Outro)": (
        "[Intro]\n\n\n\n\n[Hook]\n" + "\n" * 8 + "[Verse 1]\n" + "\n" * 16 + "[Hook]\n" + "\n" * 8 +
        "[Verse 2]\n" + "\n" * 16 + "[Hook]\n" + "\n" * 8 + "[Outro]\n\n\n\n\n"),
    "Hook-first (Hook-V1-Hook-V2-Bridge-Hook)": (
        "[Hook]\n" + "\n" * 8 + "[Verse 1]\n" + "\n" * 16 + "[Hook]\n" + "\n" * 8 + "[Verse 2]\n" +
        "\n" * 16 + "[Bridge]\n" + "\n" * 8 + "[Hook]\n" + "\n" * 8),
}


# ------------------------------------------------------------------ LRC
def _lrc_time(sec):
    m, s = divmod(max(0.0, sec), 60)
    return f"[{int(m):02d}:{s:05.2f}]"


def to_lrc(sheet, project, title="", artist=""):
    out = []
    if title:
        out.append(f"[ti:{title}]")
    if artist:
        out.append(f"[ar:{artist}]")
    out.append("[by:SIB Studio Mixer]")
    for s, e, ln in line_times(sheet, project):
        out.append(f"{_lrc_time(s / project.sr)}{ln.text}")
    return "\n".join(out) + "\n"


def from_lrc(text, sr):
    """-> (plain lyrics text, timings {line_index: sample})"""
    lines, timings = [], {}
    for raw in text.splitlines():
        tags = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", raw)
        body = re.sub(r"\[[^\]]*\]", "", raw).strip()
        if not tags:
            if raw.strip().startswith("[") and ":" in raw:
                continue  # metadata
            if body:
                lines.append(body)
            continue
        for m, s in tags[:1]:
            timings[len(lines)] = int((int(m) * 60 + float(s)) * sr)
        lines.append(body)
    return "\n".join(lines), timings
