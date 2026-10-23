#!/usr/bin/env python3
"""Generate YouTube thumbnail candidates: pull a few frames from a clip
(auto-picked by visual interest, or a specific timestamp), overlay bold
title text, output 1280x720 PNGs to choose from.

Uses a font from fetch_assets.py's font manifest (fetch one first:
`python tools/fetch_assets.py fonts --family "Bebas Neue"`).

Usage:
    python tools/thumbnail.py --video /path/IMG_0895.MOV --text "IS IT WORTH IT?" --name video1
    python tools/thumbnail.py --video /path/IMG_0895.MOV --text "FIXED!" --name video1 --time 42.5
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import assets_dir, work_dir

W, H = 1280, 720


def font_path(family: str) -> Path:
    manifest = json.loads((assets_dir("fonts") / "manifest.json").read_text())
    for m in manifest:
        if m["family"].lower() == family.lower():
            return Path(m["local_path"])
    raise SystemExit(f"font '{family}' not fetched -- run: python tools/fetch_assets.py fonts --family \"{family}\"")


def probe_duration(video_path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)]
    return float(subprocess.run(cmd, stdout=subprocess.PIPE, check=True).stdout)


def grab_frame(video_path: Path, t: float) -> np.ndarray:
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video_path),
           "-vframes", "1", "-f", "image2pipe", "-vcodec", "bmp", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, check=True)
    return cv2.imdecode(np.frombuffer(proc.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)


def interest_score(frame: np.ndarray) -> float:
    """Sharpness (Laplacian variance) + color saturation -- cheap proxy for
    'looks good as a thumbnail', not a substitute for actually looking at
    the candidates."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
    sat = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 1].mean()
    return sharpness + sat * 5


def pick_candidate_times(video_path: Path, count: int) -> list:
    duration = probe_duration(video_path)
    # sample a grid, skip the very start/end (titles/black frames), score, take the best `count`
    grid = np.linspace(duration * 0.08, duration * 0.92, max(count * 4, 12))
    scored = [(t, interest_score(grab_frame(video_path, t))) for t in grid]
    scored.sort(key=lambda x: -x[1])
    return sorted(t for t, _ in scored[:count])


def render_thumbnail(frame: np.ndarray, text: str, font: Path, out_path: Path):
    frame = cv2.resize(frame, (W, H))
    tmp_in = out_path.with_suffix(".src.png")
    cv2.imwrite(str(tmp_in), frame)

    escaped_font = str(font).replace("\\", "/").replace(":", r"\:")
    escaped_text = text.replace("'", r"\'").replace(":", r"\:")
    fc = (
        f"drawtext=fontfile='{escaped_font}':text='{escaped_text}':"
        f"fontsize=96:fontcolor=white:borderw=8:bordercolor=black:"
        f"x=(w-text_w)/2:y=h-text_h-60"
    )
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(tmp_in),
           "-vf", fc, str(out_path)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    tmp_in.unlink()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg drawtext failed: {proc.stderr.decode(errors='replace')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--font", default="Bebas Neue")
    ap.add_argument("--time", type=float, default=None,
                     help="grab this exact timestamp instead of auto-picking candidates")
    ap.add_argument("--candidates", type=int, default=4)
    args = ap.parse_args()

    out_dir = work_dir(args.name) / "thumbnails"
    out_dir.mkdir(exist_ok=True)
    font = font_path(args.font)

    times = [args.time] if args.time is not None else pick_candidate_times(args.video, args.candidates)
    for i, t in enumerate(times):
        frame = grab_frame(args.video, t)
        out_path = out_dir / f"candidate_{i}_{t:.1f}s.png"
        render_thumbnail(frame, args.text, font, out_path)
        print(f"  {out_path}  (t={t:.1f}s)", file=sys.stderr)


if __name__ == "__main__":
    main()
