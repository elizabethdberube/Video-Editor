#!/usr/bin/env python3
"""Render an edit plan (plan.json) into a finished 1920x1080 30fps CFR video.

Every time in the plan is a per-clip source time (0 at that file's start),
never a project-timeline time -- see transcribe.py. The plan is a list of
segments, played in order:

    {"clip": "cam", "start": 4.1, "end": 10.9}
        A piece of one clip, its own audio. Optional "speed" (default 1,
        slow-mo below 1, fast-forward above -- audio tempo is chained across
        multiple atempo filters since ffmpeg rejects a single atempo outside
        0.5-2.0). Optional "zoom": {"factor": 1.3, "cx": 0.5, "cy": 0.4,
        "ease_in": 0.3, "ease_out": 0.3} for a punch-in -- eases from 1x to
        "factor" over ease_in seconds of the segment's own (post-speed)
        output duration, holds, eases back out over the last ease_out
        seconds. cx/cy (0-1, default centered) is the zoom target point.
    {"photo": "IMG_0887.HEIC", "duration": 1.5}
        A still with a slow push-in, silent.
    {"block": "phone", "video": [{"clip": "rec", "start": .., "end": .., "speed": ..}, ...],
     "audio": [{"clip": "old", "start": .., "end": .., "at": 0.0} | {"file": "vo.wav", "at": 0.0}]}
        Several video pieces played back to back, with separately placed
        audio on top (voiceover over a silent screen recording). "at" is the
        offset inside the block. The block's own clip audio is dropped.

"clips" maps short ids to files under footage_root. Each clip's "layout" is
"fill" (crop to 16:9, default) or "vertical" (phone screen centred over a
blurred copy of itself). "audio_track" picks the audio stream (default 0;
iPhone .MOVs also carry an unusable 4ch apac track as 0:a:1).

Segments render to <work_dir>/render/NNN.mp4 in one uniform format, then get
concatenated and loudness-normalised to -14 LUFS (YouTube's target).

Usage:
    python tools/render.py --name video1
    python tools/render.py --name video1 --only 3      # re-render one segment, then re-join
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import load, work_dir

W, H, FPS = 1920, 1080, 30
VIDEO_OUT = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-r", str(FPS)]
AUDIO_OUT = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
FADE = 0.02  # short audio fades at every cut, so hard cuts don't click


def run(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd)}\n{proc.stderr.decode(errors='replace')[-3000:]}")


def has_audio(path: Path, track: int) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"a:{track}",
                          "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                         stdout=subprocess.PIPE).stdout.strip()
    return bool(out)


def layout_filter(layout: str, src: str, out: str) -> str:
    if layout == "vertical":
        return (f"[{src}]split[bg][fg];"
                f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=40:2,eq=brightness=-0.12[bgb];"
                f"[fg]scale=-2:{H - 40}[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]")
    return (f"[{src}]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1[{out}]")


def audio_fades(dur: float) -> str:
    return f"afade=t=in:d={FADE},afade=t=out:st={max(dur - FADE, 0):.3f}:d={FADE}"


def atempo_chain(speed: float) -> str:
    """atempo rejects a single value outside 0.5-2.0 (confirmed: ffmpeg
    errors 'out of range' even though its own docs claim up to 100) -- chain
    multiple instances to cover real slow-mo/fast-forward speeds."""
    filters, remaining = [], speed
    while remaining < 0.5:
        filters.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        filters.append(2.0)
        remaining /= 2.0
    filters.append(remaining)
    return ",".join(f"atempo={f:.6f}" for f in filters)


def zoom_filter(zoom: dict, dur: float, src: str, out: str) -> str:
    """Punch-in via zoompan (crop's w/h are only evaluated once at filter
    init in this ffmpeg build -- confirmed by testing, so a time-varying
    crop size doesn't work; zoompan is the filter actually built for this,
    and render_photo already relies on it for push-ins). d=1 so every
    output frame consumes the next real input frame -- motion keeps playing
    instead of freezing on one frame like a photo push-in does. Expressions
    use 'on' (output frame number), eased in/held/eased out in frames."""
    factor = zoom.get("factor", 1.3)
    cx, cy = zoom.get("cx", 0.5), zoom.get("cy", 0.5)
    ease_in = min(zoom.get("ease_in", 0.3), dur / 2)
    ease_out = min(zoom.get("ease_out", 0.3), dur / 2)
    total_f = max(round(dur * FPS), 1)
    in_f = max(round(ease_in * FPS), 1)
    out_f = max(total_f - round(ease_out * FPS), in_f)
    z = (f"if(lt(on,{in_f}),1+({factor}-1)*on/{in_f},"
         f"if(lt(on,{out_f}),{factor},"
         f"{factor}-({factor}-1)*(on-{out_f})/{max(total_f - out_f, 1)}))")
    x = f"min(max(iw*{cx}-(iw/zoom/2),0),iw-iw/zoom)"
    y = f"min(max(ih*{cy}-(ih/zoom/2),0),ih-ih/zoom)"
    return f"[{src}]zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={FPS}[{out}]"


def render_clip(seg, clips, root, out: Path):
    c = clips[seg["clip"]]
    path = root / c["file"]
    speed = seg.get("speed", 1.0)
    dur = (seg["end"] - seg["start"]) / speed
    track = c.get("audio_track", 0)
    fc = (f"[0:v]setpts=(PTS-STARTPTS)/{speed},fps={FPS}[v0];"
          + layout_filter(c.get("layout", "fill"), "v0", "v1"))
    if "zoom" in seg:
        fc += ";" + zoom_filter(seg["zoom"], dur, "v1", "v")
    else:
        fc += ";[v1]null[v]"
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error",
           "-ss", f"{seg['start']:.3f}", "-to", f"{seg['end']:.3f}", "-i", str(path)]
    if has_audio(path, track):
        fc += f";[0:a:{track}]{atempo_chain(speed)},{audio_fades(dur)}[a]"
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        fc += ";[1:a]anull[a]"
    run(cmd + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
               "-t", f"{dur:.3f}", *VIDEO_OUT, *AUDIO_OUT, str(out)])


def still_png(name: str, root: Path, wd: Path) -> Path:
    """HEIC photos are tiled grids that ffmpeg reassembles itself, so they
    can't take a -vf in the same command; decode to PNG once and reuse."""
    src = root / name
    if src.suffix.lower() not in (".heic", ".heif"):
        return src
    png = wd / "stills" / (src.stem + ".png")
    if not png.exists():
        png.parent.mkdir(exist_ok=True)
        run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-frames:v", "1", str(png)])
    return png


def render_photo(seg, root, wd, out: Path):
    dur = seg["duration"]
    frames = round(dur * FPS)
    fc = (f"[0:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
          f"zoompan=z='1+0.06*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
          f"d={frames}:s={W}x{H}:fps={FPS},setsar=1[v]")
    run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(still_png(seg["photo"], root, wd)),
         "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
         "-filter_complex", fc, "-map", "[v]", "-map", "1:a", "-t", f"{dur:.3f}",
         *VIDEO_OUT, *AUDIO_OUT, str(out)])


def render_block(seg, clips, root, wd, out: Path):
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error"]
    parts, n, dur = [], 0, 0.0
    layout = None
    for piece in seg["video"]:
        c = clips[piece["clip"]]
        layout = layout or c.get("layout", "fill")
        speed = piece.get("speed", 1.0)
        cmd += ["-ss", f"{piece['start']:.3f}", "-to", f"{piece['end']:.3f}", "-i", str(root / c["file"])]
        parts.append(f"[{n}:v]setpts=(PTS-STARTPTS)/{speed},fps={FPS},setsar=1[p{n}]")
        dur += (piece["end"] - piece["start"]) / speed
        n += 1
    fc = ";".join(parts) + ";" + "".join(f"[p{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cat];"
    fc += layout_filter(layout, "cat", "v")

    # Silent bed for the whole block, then every audio piece mixed in at its offset.
    cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    bed, n = n, n + 1
    mix = [f"[{bed}:a]"]
    for k, a in enumerate(seg.get("audio", [])):
        if "file" in a:
            src = wd / a["file"]
            cmd += ["-i", str(src)]
            track, adur = 0, None
        else:
            c = clips[a["clip"]]
            src = root / c["file"]
            cmd += ["-ss", f"{a['start']:.3f}", "-to", f"{a['end']:.3f}", "-i", str(src)]
            track, adur = c.get("audio_track", 0), a["end"] - a["start"]
        delay = int(a.get("at", 0) * 1000)
        fades = audio_fades(adur) + "," if adur else ""
        fc += (f";[{n}:a:{track}]aresample=48000,aformat=channel_layouts=stereo,{fades}"
               f"adelay={delay}:all=1[a{k}]")
        mix.append(f"[a{k}]")
        n += 1
    fc += f";{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[a]"
    run(cmd + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
               *VIDEO_OUT, *AUDIO_OUT, str(out)])


def seg_label(seg) -> str:
    if "photo" in seg:
        return f"photo {seg['photo']} {seg['duration']}s"
    if "block" in seg:
        return f"block {seg['block']} ({len(seg['video'])} pieces, {len(seg.get('audio', []))} audio)"
    return f"{seg['clip']} {seg['start']:.2f}-{seg['end']:.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="video name (work dir under footage_root/_editor/work)")
    ap.add_argument("--plan", default="plan.json")
    ap.add_argument("--only", type=int, action="append", help="re-render only these segment numbers")
    ap.add_argument("--out", help="output file (default <work_dir>/<name>.mp4)")
    args = ap.parse_args()

    cfg = load()
    root = cfg["footage_root"]
    wd = work_dir(args.name)
    plan = json.loads((wd / args.plan).read_text(encoding="utf-8"))
    clips = plan["clips"]
    rdir = wd / "render"
    rdir.mkdir(exist_ok=True)

    seg_files = []
    for i, seg in enumerate(plan["segments"]):
        out = rdir / f"{i:03d}.mp4"
        seg_files.append(out)
        if args.only and i not in args.only and out.exists():
            continue
        print(f"[{i + 1}/{len(plan['segments'])}] {seg_label(seg)}", file=sys.stderr)
        if "photo" in seg:
            render_photo(seg, root, wd, out)
        elif "block" in seg:
            render_block(seg, clips, root, wd, out)
        else:
            render_clip(seg, clips, root, out)

    listing = rdir / "concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in seg_files), encoding="utf-8")
    joined = rdir / "joined.mp4"
    run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(joined)])

    final = Path(args.out) if args.out else wd / f"{args.name}.mp4"
    print("normalising loudness to -14 LUFS", file=sys.stderr)
    run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(joined),
         "-af", loudnorm_filter(joined), "-c:v", "copy", *AUDIO_OUT,
         "-movflags", "+faststart", str(final)])
    print(f"wrote {final}")


def loudnorm_filter(path: Path) -> str:
    """Two-pass loudnorm. Single-pass only approaches the target (measured
    -16.6 LUFS for a -14 target on video1); measuring first and applying a
    linear gain hits it."""
    target = "I=-14:TP=-1.5:LRA=11"
    proc = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-vn",
                           "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    err = proc.stderr.decode(errors="replace")
    m = json.loads(err[err.rindex("{"):err.rindex("}") + 1])
    return (f"loudnorm={target}:linear=true:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
            f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}")


if __name__ == "__main__":
    main()
