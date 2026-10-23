#!/usr/bin/env python3
"""Identify who's speaking per transcript segment with TitaNet voiceprints.

Groups transcript.json words into pause-bounded segments per clip, embeds
each segment's audio, 2-means clusters the embeddings, and (if a solo
reference clip of you is given) labels whichever cluster is closest to it
"me". Writes <work_dir>/speakers.json: a list of {clip, start, end, speaker}.

Only makes sense for footage with two speakers. If a video is solo
narration, don't run this -- forcing a 2-way split on one voice just
clusters noise/room-tone variation into a meaningless split. Better still:
record each mic to its own track and skip this tool entirely.

NOT YET VERIFIED END TO END (2026-10-23): this box has no GPU and the NeMo
speaker-verification API calls below (EncDecSpeakerLabelModel.from_pretrained,
.get_embedding) are written from documentation, not a real run -- same
situation transcribe.py was in before its first real Windows run corrected
some assumptions. Treat the NeMo-facing parts as unverified until run for
real; the segment-grouping and k-means parts don't need NeMo and can be
sanity-checked locally.

Usage:
    python tools/speakers.py --name video1
    python tools/speakers.py --name video1 --reference-clip cam --reference-start 2 --reference-end 6
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load, work_dir

SAMPLE_RATE = 16000


def extract_segment_wav(video_path: Path, start: float, end: float, audio_track: int, out_path: Path):
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error",
           "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(video_path),
           "-map", f"0:a:{audio_track}", "-ac", "1", "-ar", str(SAMPLE_RATE), str(out_path)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg segment extraction failed: {proc.stderr.decode()}")


def group_segments(words, clip_id, max_gap=0.5, min_dur=0.8):
    """Pause-bounded segments within one clip, source time (not display time)."""
    clip_words = [w for w in words if w["clip"] == clip_id]
    segments, cur = [], []
    for w in clip_words:
        if cur and w["start"] - cur[-1]["end"] > max_gap:
            segments.append(cur)
            cur = []
        cur.append(w)
    if cur:
        segments.append(cur)
    out = []
    for seg in segments:
        start, end = seg[0]["start"], seg[-1]["end"]
        if end - start >= min_dur:
            out.append({"clip": clip_id, "start": start, "end": end})
    return out


def load_titanet():
    import nemo.collections.asr as nemo_asr
    return nemo_asr.models.EncDecSpeakerLabelModel.from_pretrained("nvidia/speakerverification_en_titanet_large")


def embed_segment(model, video_path: Path, start: float, end: float, audio_track: int) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        extract_segment_wav(video_path, start, end, audio_track, tmp_path)
        emb = model.get_embedding(str(tmp_path))
        return np.asarray(emb).reshape(-1)
    finally:
        tmp_path.unlink(missing_ok=True)


def kmeans2(X: np.ndarray, iters=50, restarts=10, seed=0):
    """Plain-numpy 2-means (no sklearn dependency) -- small N here (segment
    counts per video), so brute-force restarts are cheap and avoid bad
    local optima from a single random init."""
    rng = np.random.default_rng(seed)
    best_labels, best_centroids, best_inertia = None, None, np.inf
    for _ in range(restarts):
        centroids = X[rng.choice(len(X), 2, replace=False)].copy()
        labels = np.zeros(len(X), dtype=int)
        for _ in range(iters):
            dists = np.linalg.norm(X[:, None, :] - centroids[None, :, :], axis=2)
            new_labels = dists.argmin(axis=1)
            if np.array_equal(new_labels, labels) and _ > 0:
                break
            labels = new_labels
            for k in (0, 1):
                if (labels == k).any():
                    centroids[k] = X[labels == k].mean(axis=0)
        inertia = sum(np.linalg.norm(X[labels == k] - centroids[k], axis=1).sum() for k in (0, 1))
        if inertia < best_inertia:
            best_labels, best_centroids, best_inertia = labels, centroids, inertia
    return best_labels, best_centroids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--audio-track", type=int, default=0)
    ap.add_argument("--reference-clip", default=None,
                     help="clip id containing a solo clip of you, to label the matching cluster \"me\"")
    ap.add_argument("--reference-start", type=float, default=None)
    ap.add_argument("--reference-end", type=float, default=None)
    args = ap.parse_args()

    cfg = load()
    wd = work_dir(args.name)
    transcript = json.loads((wd / "transcript.json").read_text())
    clips = {c["id"]: c for c in transcript["clips"]}

    all_segments = []
    for clip_id in clips:
        all_segments.extend(group_segments(transcript["words"], clip_id))
    if len(all_segments) < 2:
        raise SystemExit(f"only {len(all_segments)} segment(s) found -- nothing to cluster")
    print(f"{len(all_segments)} segments across {len(clips)} clip(s), embedding...", file=sys.stderr)

    model = load_titanet()
    X = []
    for i, seg in enumerate(all_segments):
        video_path = cfg["footage_root"] / clips[seg["clip"]]["file"]
        X.append(embed_segment(model, video_path, seg["start"], seg["end"], args.audio_track))
        print(f"  [{i + 1}/{len(all_segments)}] {seg['clip']} {seg['start']:.1f}-{seg['end']:.1f}", file=sys.stderr)
    X = np.stack(X)

    labels, centroids = kmeans2(X)
    speaker_names = {0: "speaker_0", 1: "speaker_1"}

    if args.reference_clip:
        ref_video = cfg["footage_root"] / clips[args.reference_clip]["file"]
        ref_emb = embed_segment(model, ref_video, args.reference_start, args.reference_end, args.audio_track)
        sims = [np.dot(ref_emb, c) / (np.linalg.norm(ref_emb) * np.linalg.norm(c)) for c in centroids]
        me_cluster = int(np.argmax(sims))
        speaker_names = {me_cluster: "me", 1 - me_cluster: "guest"}
        print(f"  reference clip matches cluster {me_cluster} (cosine sim {sims[me_cluster]:.3f} vs {sims[1 - me_cluster]:.3f})",
              file=sys.stderr)
    else:
        print("  no --reference-clip given -- clusters labeled speaker_0/speaker_1, not \"me\"/\"guest\"", file=sys.stderr)

    out = []
    for seg, label in zip(all_segments, labels):
        out.append({**seg, "speaker": speaker_names[int(label)]})

    out_path = wd / "speakers.json"
    out_path.write_text(json.dumps(out, indent=2))
    counts = {name: sum(1 for o in out if o["speaker"] == name) for name in set(speaker_names.values())}
    print(f"wrote {out_path} ({counts})", file=sys.stderr)


if __name__ == "__main__":
    main()
