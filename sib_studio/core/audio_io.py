"""Loading, saving and converting audio.

Audio inside SIB Studio is always a float32 numpy array shaped (samples, channels).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import numpy as np
from scipy import signal
from scipy.io import wavfile

try:  # preferred backend (WAV/FLAC/OGG/MP3 via libsndfile >= 1.1)
    import soundfile as sf  # type: ignore
except Exception:  # pragma: no cover - optional
    sf = None

AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff", ".m4a", ".aac", ".wma")


def find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "..", "ffmpeg", "ffmpeg.exe"),
                 os.path.join(here, "..", "..", "ffmpeg.exe")):
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return None


def _as_2d(data: np.ndarray) -> np.ndarray:
    if data.ndim == 1:
        data = data[:, None]
    return data


def _int_to_float(data: np.ndarray) -> np.ndarray:
    if data.dtype == np.uint8:
        return (data.astype(np.float32) - 128.0) / 128.0
    if np.issubdtype(data.dtype, np.integer):
        return data.astype(np.float32) / float(np.iinfo(data.dtype).max)
    return data.astype(np.float32)


def _load_ffmpeg(path: str) -> tuple[np.ndarray, int]:
    exe = find_ffmpeg()
    if not exe:
        raise RuntimeError(
            "This file type needs FFmpeg. Install it (winget install ffmpeg) or convert the file to WAV.")
    probe = subprocess.run([exe, "-i", path], capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    sr, ch = 44100, 2
    for line in probe.stderr.splitlines():
        if "Audio:" in line:
            parts = [p.strip() for p in line.split(",")]
            for p in parts:
                if p.endswith("Hz"):
                    try:
                        sr = int(p.split()[0])
                    except ValueError:
                        pass
                if p.startswith("mono"):
                    ch = 1
            break
    proc = subprocess.run([exe, "-v", "error", "-i", path, "-f", "f32le", "-acodec", "pcm_f32le",
                           "-ac", str(ch), "-ar", str(sr), "-"], capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="ignore") or "FFmpeg could not decode the file")
    data = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, ch)
    return data.copy(), sr


def load_audio(path: str, target_sr: int | None = None) -> tuple[np.ndarray, int]:
    """Load any supported audio file -> (float32 (n, ch), sample_rate)."""
    ext = os.path.splitext(path)[1].lower()
    data = None
    sr = None
    if sf is not None:
        try:
            data, sr = sf.read(path, dtype="float32", always_2d=True)
        except Exception:
            data = None
    if data is None and ext == ".wav":
        try:
            sr, raw = wavfile.read(path)
            data = _as_2d(_int_to_float(raw))
        except Exception:
            data = None
    if data is None:
        data, sr = _load_ffmpeg(path)
    data = _as_2d(np.asarray(data, dtype=np.float32))
    if data.shape[1] > 2:
        data = data[:, :2]
    if target_sr and sr != target_sr:
        data = resample(data, sr, target_sr)
        sr = target_sr
    return np.ascontiguousarray(data, dtype=np.float32), int(sr)


def save_audio(path: str, data: np.ndarray, sr: int, bit_depth: int | str = 24,
               mp3_kbps: int = 320) -> None:
    """Save audio. Format comes from the extension (.wav .flac .mp3 .ogg)."""
    data = _as_2d(np.asarray(data, dtype=np.float32))
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    ext = os.path.splitext(path)[1].lower()
    clipped = np.clip(data, -1.0, 1.0)
    if ext == ".mp3":
        exe = find_ffmpeg()
        if sf is not None:
            try:
                sf.write(path, clipped, sr, format="MP3")
                return
            except Exception:
                pass
        if not exe:
            raise RuntimeError("MP3 export needs FFmpeg (winget install ffmpeg). Export WAV instead.")
        proc = subprocess.run([exe, "-y", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac",
                               str(data.shape[1]), "-i", "-", "-b:a", f"{mp3_kbps}k", path],
                              input=clipped.astype(np.float32).tobytes(), capture_output=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode(errors="ignore"))
        return
    if sf is not None:
        subtype = {16: "PCM_16", 24: "PCM_24", 32: "FLOAT", "float": "FLOAT"}.get(bit_depth, "PCM_24")
        if ext == ".flac" and subtype == "FLOAT":
            subtype = "PCM_24"
        if ext == ".ogg":
            subtype = "VORBIS"
        sf.write(path, data if subtype == "FLOAT" else clipped, sr, subtype=subtype)
        return
    # scipy fallback (WAV only, 16-bit or 32-bit float)
    if bit_depth == 16:
        wavfile.write(path, sr, (clipped * 32767.0).astype(np.int16))
    else:
        wavfile.write(path, sr, data.astype(np.float32))


def resample(data: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    data = _as_2d(np.asarray(data, dtype=np.float32))
    if sr_from == sr_to:
        return data
    from math import gcd
    g = gcd(int(sr_from), int(sr_to))
    up, down = int(sr_to) // g, int(sr_from) // g
    out = signal.resample_poly(data, up, down, axis=0)
    return out.astype(np.float32)


def to_mono(data: np.ndarray) -> np.ndarray:
    data = _as_2d(data)
    return data.mean(axis=1).astype(np.float32)


def to_stereo(data: np.ndarray) -> np.ndarray:
    data = _as_2d(data)
    if data.shape[1] == 1:
        return np.repeat(data, 2, axis=1)
    return data[:, :2]


def compute_peaks(mono: np.ndarray, samples_per_bin: int) -> tuple[np.ndarray, np.ndarray]:
    """Min/max envelope used to draw waveforms quickly."""
    n = len(mono)
    if n == 0:
        return np.zeros(0, np.float32), np.zeros(0, np.float32)
    spb = max(1, int(samples_per_bin))
    bins = int(np.ceil(n / spb))
    pad = bins * spb - n
    m = np.pad(mono, (0, pad)) if pad else mono
    m = m.reshape(bins, spb)
    return m.min(axis=1), m.max(axis=1)
