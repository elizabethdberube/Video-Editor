#!/usr/bin/env python3
"""Transcribe raw footage with Parakeet TDT 0.6B v2 (NeMo), word-level timestamps.

A video is usually cut from many independent clips (and photos), not one
continuous take, so transcript.json keeps each clip's own source time (0 at
that file's start) rather than flattening everything onto one offset timeline.
Every word is tagged with the clip id it came from; downstream tools (shots,
drops, sections, broll) must always carry (clip, time) together, never a bare
time.

Writes/updates <work_dir>/transcript.json (clip-tagged words) and
<work_dir>/transcript.srt (a human-readable preview, clips concatenated in the
order transcribed -- for review only, not an editing anchor).

Usage:
    python tools/transcribe.py --video "/path/to/raw/IMG_0894.MOV" --name my_video
    python tools/transcribe.py --video "...IMG_0895.MOV" --name my_video --append
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import work_dir

SAMPLE_RATE = 16000
# Parakeet on a 12GB card runs out of memory with full attention above ~17 min;
# switch to local attention past this to leave headroom.
LOCAL_ATTENTION_THRESHOLD_SEC = 600


def extract_audio(video_path: Path, audio_track: int = 0) -> np.ndarray:
    """Decode one audio track to mono 16kHz float32 via ffmpeg, no temp files."""
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error",
        "-i", str(video_path),
        "-map", f"0:a:{audio_track}",
        "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {proc.stderr.decode()}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def load_model(duration_sec: float):
    import nemo.collections.asr as nemo_asr

    model = nemo_asr.models.ASRModel.from_pretrained("nvidia/parakeet-tdt-0.6b-v2")
    if duration_sec > LOCAL_ATTENTION_THRESHOLD_SEC:
        print(f"  audio is {duration_sec:.0f}s > {LOCAL_ATTENTION_THRESHOLD_SEC}s, "
              f"switching to local attention to avoid OOM", file=sys.stderr)
        model.change_attention_model(self_attention_model="rel_pos_local_attn",
                                      att_context_size=[128, 128])
    return model


def transcribe(audio: np.ndarray, clip_id: str):
    duration_sec = len(audio) / SAMPLE_RATE
    model = load_model(duration_sec)
    # NeMo takes the array directly (not a path) -- a path makes NeMo write a
    # temp manifest file that is left open and crashes on Windows.
    output = model.transcribe(audio=[audio], timestamps=True)
    hyp = output[0]
    words = []
    for w in hyp.timestamp["word"]:
        words.append({
            "clip": clip_id,
            "word": w["word"],
            "start": round(w["start"], 3),
            "end": round(w["end"], 3),
        })
    return words, hyp.text


def words_to_srt(clips, words, max_chars=42, max_gap=0.6):
    """Human-readable preview only: clips concatenated in list order, each
    clip's own source times shifted by a running display offset. Editing
    tools must use transcript.json's (clip, start/end) instead."""
    clip_display_offset = {}
    running = 0.0
    for c in clips:
        clip_display_offset[c["id"]] = running
        running += c["duration"]

    lines = []
    cur = []
    cur_len = 0
    for w in words:
        disp_start = w["start"] + clip_display_offset[w["clip"]]
        disp_end = w["end"] + clip_display_offset[w["clip"]]
        w = {**w, "disp_start": disp_start, "disp_end": disp_end}
        if cur and (w["disp_start"] - cur[-1]["disp_end"] > max_gap
                    or cur[-1]["clip"] != w["clip"]
                    or cur_len + len(w["word"]) + 1 > max_chars):
            lines.append(cur)
            cur, cur_len = [], 0
        cur.append(w)
        cur_len += len(w["word"]) + 1
    if cur:
        lines.append(cur)

    def fmt(t):
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        ms = (t - int(t)) * 1000
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(ms):03d}"

    out = []
    for i, line in enumerate(lines, 1):
        start, end = line[0]["disp_start"], line[-1]["disp_end"]
        text = " ".join(w["word"] for w in line)
        out.append(f"{i}\n{fmt(start)} --> {fmt(end)}\n{text}\n")
    return "\n".join(out)


def probe_duration(video_path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)]
    return float(subprocess.run(cmd, stdout=subprocess.PIPE, check=True).stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--name", required=True, help="video identifier, used for the work dir")
    ap.add_argument("--clip-id", default=None,
                     help="id this clip's words are tagged with (default: filename stem)")
    ap.add_argument("--audio-track", type=int, default=0,
                     help="index of the audio stream to use, e.g. 0 for the main AAC "
                          "mic track on iPhone footage (skip spatial/apac tracks)")
    ap.add_argument("--append", action="store_true",
                     help="add this clip to an existing transcript.json instead of overwriting it")
    args = ap.parse_args()

    clip_id = args.clip_id or args.video.stem
    out_dir = work_dir(args.name)
    transcript_path = out_dir / "transcript.json"

    print(f"extracting audio track {args.audio_track} from {args.video.name}...", file=sys.stderr)
    audio = extract_audio(args.video, args.audio_track)
    duration = probe_duration(args.video)
    print(f"  {len(audio) / SAMPLE_RATE:.1f}s of audio, transcribing clip '{clip_id}'...", file=sys.stderr)
    words, text = transcribe(audio, clip_id)
    print(f"  {len(words)} words", file=sys.stderr)

    clip_entry = {"id": clip_id, "file": args.video.name, "duration": round(duration, 3)}
    if args.append and transcript_path.exists():
        data = json.loads(transcript_path.read_text())
        if any(c["id"] == clip_id for c in data["clips"]):
            raise SystemExit(f"clip id '{clip_id}' already in transcript.json -- pass --clip-id to disambiguate")
        data["clips"].append(clip_entry)
        data["words"].extend(words)
    else:
        data = {"clips": [clip_entry], "words": words}

    transcript_path.write_text(json.dumps(data, indent=2))
    (out_dir / "transcript.srt").write_text(words_to_srt(data["clips"], data["words"]))
    print(f"wrote {transcript_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
