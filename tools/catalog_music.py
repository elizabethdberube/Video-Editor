#!/usr/bin/env python3
"""Catalog a folder of background-music tracks: BPM, LUFS, energy swing,
brightness, and (best-effort) vocals detection, so audio.py can pick a bed
that fits a section's tempo/energy and never puts a vocal track under
dialogue.

Reads from config.json's "music_dir" (set it once you have a folder to
point at). Writes <footage_root>/_editor/assets/music/catalog.json, one
entry per track, with an absolute "source_path" back into music_dir --
music_dir itself is never written to.

Vocals detection transcribes each track (real lyrics give 50+ words) and
needs NeMo; if NeMo isn't installed/working on this machine, every other
measurement still runs and "has_vocals" is left null rather than guessed.

Usage:
    python tools/catalog_music.py
    python tools/catalog_music.py --music-dir /path/to/music
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import librosa
import numpy as np
import pyloudnorm as pyln
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import assets_dir, load

EXTS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}


def load_audio_mono(path: Path, sr=22050):
    y, file_sr = librosa.load(str(path), sr=sr, mono=True)
    return y, sr


def measure_bpm(y: np.ndarray, sr: int) -> float:
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    return round(float(np.asarray(tempo).reshape(-1)[0]), 1)


def measure_lufs(path: Path) -> float:
    data, sr = sf.read(str(path))
    meter = pyln.Meter(sr)
    return round(float(meter.integrated_loudness(data)), 1)


def measure_energy_swing(y: np.ndarray, sr: int) -> float:
    """dB range between loud and quiet sections (90th - 10th percentile of
    short-term RMS in dB) -- how much a track swings, so a bed that gets
    loud in its chorus can be flagged before it fights dialogue."""
    rms = librosa.feature.rms(y=y, frame_length=sr, hop_length=sr // 2)[0]
    rms_db = librosa.amplitude_to_db(rms + 1e-9)
    return round(float(np.percentile(rms_db, 90) - np.percentile(rms_db, 10)), 1)


def measure_brightness(y: np.ndarray, sr: int) -> float:
    """Mean spectral centroid in Hz -- higher = brighter/treble-heavy."""
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    return round(float(centroid.mean()), 0)


def detect_vocals(path: Path) -> dict:
    try:
        import transcribe as transcribe_mod
    except ImportError as e:
        return {"has_vocals": None, "word_count": None, "note": f"transcribe.py unavailable: {e}"}
    try:
        audio = transcribe_mod.extract_audio(path, audio_track=0)
        words, _ = transcribe_mod.transcribe(audio, clip_id=path.stem)
        return {"has_vocals": len(words) >= 50, "word_count": len(words)}
    except Exception as e:
        return {"has_vocals": None, "word_count": None, "note": f"NeMo transcription failed: {e}"}


def catalog_track(path: Path, skip_vocals: bool) -> dict:
    y, sr = load_audio_mono(path)
    entry = {
        "file": path.name,
        "source_path": str(path),
        "duration": round(len(y) / sr, 1),
        "bpm": measure_bpm(y, sr),
        "lufs": measure_lufs(path),
        "energy_swing_db": measure_energy_swing(y, sr),
        "brightness_hz": measure_brightness(y, sr),
        "cataloged_at": datetime.now(timezone.utc).isoformat(),
    }
    if not skip_vocals:
        entry.update(detect_vocals(path))
    else:
        entry.update({"has_vocals": None, "word_count": None, "note": "--skip-vocals"})
    return entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--music-dir", type=Path, default=None,
                     help="defaults to config.json's music_dir")
    ap.add_argument("--skip-vocals", action="store_true",
                     help="skip NeMo transcription-based vocals detection (faster, no GPU needed)")
    args = ap.parse_args()

    cfg = load()
    music_dir = args.music_dir or (Path(cfg["music_dir"]) if cfg.get("music_dir") else None)
    if not music_dir:
        raise SystemExit("no music_dir set -- pass --music-dir or set \"music_dir\" in config.json")
    if not music_dir.is_dir():
        raise SystemExit(f"music_dir does not exist: {music_dir}")

    tracks = sorted(p for p in music_dir.iterdir() if p.suffix.lower() in EXTS)
    if not tracks:
        raise SystemExit(f"no audio files found in {music_dir} (looked for {sorted(EXTS)})")

    out_dir = assets_dir("music")
    catalog = []
    for i, path in enumerate(tracks):
        print(f"[{i + 1}/{len(tracks)}] {path.name}...", file=sys.stderr)
        try:
            entry = catalog_track(path, args.skip_vocals)
            catalog.append(entry)
            vocals = "vocals" if entry.get("has_vocals") else ("instrumental" if entry.get("has_vocals") is False else "vocals unknown")
            print(f"    {entry['bpm']} bpm, {entry['lufs']} LUFS, {vocals}", file=sys.stderr)
        except Exception as e:
            print(f"    FAILED: {e}", file=sys.stderr)

    out_path = out_dir / "catalog.json"
    out_path.write_text(json.dumps(catalog, indent=2))
    print(f"wrote {out_path} ({len(catalog)} tracks)", file=sys.stderr)


if __name__ == "__main__":
    main()
