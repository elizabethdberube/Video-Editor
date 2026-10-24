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

Any segment can also take:
    "pip": {"clip": "rec", "start": 140, "speed": 1, "position": "right", "height": 0.75}
        Another clip inset over this one, running from its own source "start"
        for as long as the segment lasts (holds its last frame if it runs out).
        Silent. position: left/right/top-left/top-right/bottom-left/bottom-right.
        height: fraction of the frame (default 0.75 vertical, 0.32 otherwise).
    "fade_in" / "fade_out": seconds, picture from/to black and audio with it.
    "volume_db": gain on the segment's audio.
    "overlays": [{"graphic": "warning1.mov", "at": 37.2}]
        Transparent graphics from graphics.py (<work_dir>/graphics/), or
        any PNG with "duration". Plays once, from "at".
    "sfx": [{"file": "whoosh.wav", "at": 37.0, "volume_db": -6}]
        Sound effects from the shared pool (_editor/assets/sfx/, falling
        back to the work dir), mixed on top of the segment's audio.
    "broll": [{"photo": "receipts.jpg", "at": 59.0, "duration": 4},
              {"clip": "rec", "start": 196, "at": 44.0, "duration": 5}]
        Full-frame cutaways: the picture switches to a photo (slow push-in)
        or another clip (from its own source "start", in that clip's
        layout) while this segment's audio keeps playing.
    "music": false
        Keep the background music silent during this segment (it ramps out
        and back in around it) -- e.g. clips that already have music baked in.
    For overlays, sfx and broll, "at" on a clip segment is that clip's SOURCE time
    (so re-trimming the segment doesn't move the graphic off its moment);
    on a photo or block it's seconds into the segment. Anything that would
    start outside the segment is skipped with a warning.

Plan-level options:
    "destination": "youtube" | "amazon"   -- required if "music" is set.
    "music": {"files": ["a.mp3", "b.mp3"], "level_lufs": -30, "crossfade": 2}
        A playlist under the video, crossfaded track to track and repeated
        if it runs short, ducked whenever someone talks. It starts at the
        first segment that allows music. Each track is gain-matched to
        level_lufs using its loudness from catalog_music.py's catalog
        (tracks not in the catalog fall back to "volume_db", default -20).
        Files are relative to music_dir in config.json. {"file": "x.mp3"}
        still works for a single track. Refused for Amazon videos: Liz's
        rule is music on YouTube videos only.
    "voice_cleanup": true
        Rumble cut, noise reduction, a little presence and gentle
        compression on every clip's own audio and clip voiceover pieces
        (not music or sfx), and on voiceover files in blocks. A clip can opt
        out with "voice_cleanup": false.
    "captions": true, "transcripts": ["transcript.json", "../other/transcript.json"]
        Burn in captions from the word timings. Words are matched to plan
        clips by file stem (transcribe.py's default clip id), so audio from
        any clip (or a transcribed voiceover file) gets captioned wherever
        the plan uses it.

"clips" maps short ids to files under footage_root. Each clip's "layout" is
"fill" (crop to 16:9, default) or "vertical" (phone screen centred over a
blurred copy of itself). "audio_track" picks the audio stream (default 0;
iPhone .MOVs also carry an unusable 4ch apac track as 0:a:1).

Segments render to <work_dir>/render/NNN.mp4 in one uniform format, then get
concatenated, music-mixed, captioned and loudness-normalised to -14 LUFS.

Usage:
    python tools/render.py --name video1
    python tools/render.py --name video1 --only 3      # re-render one segment, then re-join
    python tools/render.py --name video1 --preview     # fast 540p review copy -> <name>_preview.mp4
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import assets_dir, load, work_dir

W, H, FPS = 1920, 1080, 30
VIDEO_OUT = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-r", str(FPS)]
AUDIO_OUT = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
FADE = 0.02  # short audio fades at every cut, so hard cuts don't click
PIP_MARGIN = 40
LOUDNORM = "I=-14:TP=-1.5:LRA=11"
# Rumble cut, noise reduction tuned to her iPhone audio's real floor (~-55
# dB), a little less boxiness, a little more presence, then a gentle gate
# that dips pauses by at most 12 dB. Measured on IMG_0895: voice-to-pause gap
# 29.3 dB -> 37.8 dB with speech level unchanged. A compressor was tried
# first and dropped: its makeup gain raised the pauses as much as the noise
# reduction lowered them (gap 29.2 dB), and loudnorm evens out levels anyway.
VOICE_CLEANUP = ("highpass=f=80,afftdn=nf=-60:nr=15:tn=1,"
                 "equalizer=f=250:t=q:w=1:g=-2,equalizer=f=3500:t=q:w=1.2:g=2,"
                 "agate=threshold=0.01:ratio=3:attack=5:release=250:range=0.25")


def voice_chain(clip: dict, cleanup: bool) -> str:
    return VOICE_CLEANUP + "," if cleanup and clip.get("voice_cleanup", True) else ""


def run(cmd, cwd=None):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd)}\n{proc.stderr.decode(errors='replace')[-3000:]}")
    return proc


def has_audio(path: Path, track: int) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"a:{track}",
                          "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                         stdout=subprocess.PIPE).stdout.strip()
    return bool(out)


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], stdout=subprocess.PIPE).stdout
    return float(out.strip())


def n_inputs(cmd) -> int:
    return cmd.count("-i")


def layout_filter(layout: str, src: str, out: str) -> str:
    if layout == "vertical":
        return (f"[{src}]split[bg][fg];"
                f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=40:2,eq=brightness=-0.12[bgb];"
                f"[fg]scale=-2:{H - 40}[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]")
    return (f"[{src}]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1[{out}]")


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
    and photo_graph already relies on it for push-ins). d=1 so every
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


def segment_duration(seg) -> float:
    if "photo" in seg:
        return seg["duration"]
    if "block" in seg:
        return sum((p["end"] - p["start"]) / p.get("speed", 1.0) for p in seg["video"])
    return (seg["end"] - seg["start"]) / seg.get("speed", 1.0)


# --- per-segment graphs: each appends inputs to cmd and returns a filtergraph
# --- that ends in [vb] (picture) and [ab] (audio), both exactly dur long.

def silence(cmd, dur) -> str:
    i = n_inputs(cmd)
    cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    return f"[{i}:a]anull[ab]"


def clip_graph(seg, clips, root, cmd, dur, cleanup):
    c = clips[seg["clip"]]
    path = root / c["file"]
    speed = seg.get("speed", 1.0)
    track = c.get("audio_track", 0)
    i = n_inputs(cmd)
    cmd += ["-ss", f"{seg['start']:.3f}", "-to", f"{seg['end']:.3f}", "-i", str(path)]
    fc = (f"[{i}:v]setpts=(PTS-STARTPTS)/{speed},fps={FPS}[v0];"
          + layout_filter(c.get("layout", "fill"), "v0", "v1"))
    if "zoom" in seg:
        fc += ";" + zoom_filter(seg["zoom"], dur, "v1", "vb")
    else:
        fc += ";[v1]null[vb]"
    if has_audio(path, track):
        fc += f";[{i}:a:{track}]{voice_chain(c, cleanup)}{atempo_chain(speed)},apad,atrim=0:{dur:.3f}[ab]"
    else:
        fc += ";" + silence(cmd, dur)
    return fc


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


def photo_graph(seg, root, wd, cmd, dur):
    frames = round(dur * FPS)
    i = n_inputs(cmd)
    cmd += ["-i", str(still_png(seg["photo"], root, wd))]
    fc = (f"[{i}:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
          f"zoompan=z='1+0.06*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
          f"d={frames}:s={W}x{H}:fps={FPS},setsar=1[vb]")
    return fc + ";" + silence(cmd, dur)


def block_graph(seg, clips, root, wd, cmd, dur, cleanup):
    parts, labels, layout = [], [], None
    for k, piece in enumerate(seg["video"]):
        c = clips[piece["clip"]]
        layout = layout or c.get("layout", "fill")
        speed = piece.get("speed", 1.0)
        i = n_inputs(cmd)
        cmd += ["-ss", f"{piece['start']:.3f}", "-to", f"{piece['end']:.3f}", "-i", str(root / c["file"])]
        parts.append(f"[{i}:v]setpts=(PTS-STARTPTS)/{speed},fps={FPS},setsar=1[p{k}]")
        labels.append(f"[p{k}]")
    fc = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=1:a=0[cat];"
    fc += layout_filter(layout, "cat", "vb")

    # Silent bed for the whole block, then every audio piece mixed in at its offset.
    bed = n_inputs(cmd)
    cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    mix = [f"[{bed}:a]"]
    for k, a in enumerate(seg.get("audio", [])):
        i = n_inputs(cmd)
        if "file" in a:
            cmd += ["-i", str(wd / a["file"])]
            track, fades, voice = 0, "", voice_chain({}, cleanup)
        else:
            c = clips[a["clip"]]
            cmd += ["-ss", f"{a['start']:.3f}", "-to", f"{a['end']:.3f}", "-i", str(root / c["file"])]
            adur = a["end"] - a["start"]
            track, voice = c.get("audio_track", 0), voice_chain(c, cleanup)
            fades = f"afade=t=in:d={FADE},afade=t=out:st={adur - FADE:.3f}:d={FADE},"
        delay = int(a.get("at", 0) * 1000)
        fc += (f";[{i}:a:{track}]aresample=48000,aformat=channel_layouts=stereo,{voice}{fades}"
               f"adelay={delay}:all=1[a{k}]")
        mix.append(f"[a{k}]")
    fc += f";{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[ab]"
    return fc


PIP_POS = {
    "right": ("W-w-{m}", "(H-h)/2"), "left": ("{m}", "(H-h)/2"),
    "top-right": ("W-w-{m}", "{m}"), "top-left": ("{m}", "{m}"),
    "bottom-right": ("W-w-{m}", "H-h-{m}"), "bottom-left": ("{m}", "H-h-{m}"),
}


def pip_graph(pip, clips, root, cmd, dur, under="vb") -> str:
    """Inset another clip over [under], output [vp]."""
    c = clips[pip["clip"]]
    speed = pip.get("speed", 1.0)
    vertical = c.get("layout") == "vertical"
    height = int(H * pip.get("height", 0.75 if vertical else 0.32)) // 2 * 2
    end = pip["start"] + dur * speed
    i = n_inputs(cmd)
    cmd += ["-ss", f"{pip['start']:.3f}", "-to", f"{end:.3f}", "-i", str(root / c["file"])]
    x, y = (s.format(m=PIP_MARGIN) for s in PIP_POS[pip.get("position", "right")])
    border = 6
    return (f";[{i}:v]setpts=(PTS-STARTPTS)/{speed},fps={FPS},"
            f"tpad=stop_mode=clone:stop_duration={dur:.3f},trim=0:{dur:.3f},"
            f"scale=-2:{height - 2 * border},pad=iw+{2 * border}:ih+{2 * border}:{border}:{border}:white,"
            f"format=yuva420p,fade=t=in:d=0.2:alpha=1,fade=t=out:st={dur - 0.2:.3f}:d=0.2:alpha=1[pip];"
            f"[{under}][pip]overlay={x}:{y}:shortest=0:eof_action=pass[vp]")


def seg_time(seg, at: float) -> float:
    """Where "at" lands inside this segment's output (see docstring)."""
    if "clip" in seg:
        return (at - seg["start"]) / seg.get("speed", 1.0)
    return at


def placed_items(seg, key, dur, label):
    for item in seg.get(key, []):
        t = seg_time(seg, item["at"])
        if 0 <= t < dur:
            yield item, t
        else:
            print(f"  warning: {key} {item.get(label) or item.get('clip')} at {item['at']} falls outside "
                  f"{seg_label(seg)}, skipped", file=sys.stderr)


def broll_graph(seg, clips, root, wd, cmd, dur, vlabel) -> tuple[str, str]:
    """Full-frame cutaways over the picture; the segment's audio carries on."""
    fc = ""
    for k, (b, t) in enumerate(placed_items(seg, "broll", dur, "photo")):
        d = min(b["duration"], dur - t)
        i = n_inputs(cmd)
        if "photo" in b:
            frames = round(d * FPS)
            cmd += ["-i", str(still_png(b["photo"], root, wd))]
            fc += (f";[{i}:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                   f"zoompan=z='1+0.05*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
                   f"d={frames}:s={W}x{H}:fps={FPS},setsar=1[by{k}]")
        else:
            c = clips[b["clip"]]
            speed = b.get("speed", 1.0)
            cmd += ["-ss", f"{b['start']:.3f}", "-to", f"{b['start'] + d * speed:.3f}",
                    "-i", str(root / c["file"])]
            fc += (f";[{i}:v]setpts=(PTS-STARTPTS)/{speed},fps={FPS}[bx{k}];"
                   + layout_filter(c.get("layout", "fill"), f"bx{k}", f"by{k}"))
        fc += (f";[by{k}]trim=0:{d:.3f},setpts=PTS-STARTPTS+{t:.3f}/TB[bz{k}];"
               f"[{vlabel}][bz{k}]overlay=0:0:eof_action=pass[vB{k}]")
        vlabel = f"vB{k}"
    return fc, vlabel


def overlays_graph(seg, wd, cmd, dur, vlabel) -> tuple[str, str]:
    fc = ""
    for k, (o, t) in enumerate(placed_items(seg, "overlays", dur, "graphic")):
        src = wd / "graphics" / o["graphic"]
        i = n_inputs(cmd)
        if src.suffix.lower() == ".png":
            cmd += ["-loop", "1", "-t", f"{o['duration']:.3f}", "-i", str(src)]
        else:
            cmd += ["-i", str(src)]
        fc += (f";[{i}:v]scale={W}:{H},format=yuva420p,setpts=PTS-STARTPTS+{t:.3f}/TB[g{k}];"
               f"[{vlabel}][g{k}]overlay=0:0:eof_action=pass[vo{k}]")
        vlabel = f"vo{k}"
    return fc, vlabel


def sfx_graph(seg, cfg_sfx_dir: Path, wd, cmd, dur) -> tuple[str, list]:
    fc, labels = "", []
    for k, (s, t) in enumerate(placed_items(seg, "sfx", dur, "file")):
        src = cfg_sfx_dir / s["file"]
        if not src.exists():
            src = wd / s["file"]
        i = n_inputs(cmd)
        cmd += ["-i", str(src)]
        fc += (f";[{i}:a]aresample=48000,aformat=channel_layouts=stereo,"
               f"volume={s.get('volume_db', 0)}dB,adelay={int(t * 1000)}:all=1[s{k}]")
        labels.append(f"[s{k}]")
    return fc, labels


def render_segment(seg, clips, root, wd, out: Path, cleanup: bool, sfx_dir: Path):
    dur = segment_duration(seg)
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error"]
    if "photo" in seg:
        fc = photo_graph(seg, root, wd, cmd, dur)
    elif "block" in seg:
        fc = block_graph(seg, clips, root, wd, cmd, dur, cleanup)
    else:
        fc = clip_graph(seg, clips, root, cmd, dur, cleanup)

    # Layers, bottom to top: segment picture, B-roll cutaways, PiP, graphics.
    bfc, vlabel = broll_graph(seg, clips, root, wd, cmd, dur, "vb")
    fc += bfc
    if "pip" in seg:
        fc += pip_graph(seg["pip"], clips, root, cmd, dur, vlabel)
        vlabel = "vp"
    ofc, vlabel = overlays_graph(seg, wd, cmd, dur, vlabel)
    fc += ofc

    fin, fout = seg.get("fade_in", 0), seg.get("fade_out", 0)
    vf = ["format=yuv420p"]
    if fin:
        vf.append(f"fade=t=in:d={fin}")
    if fout:
        vf.append(f"fade=t=out:st={dur - fout:.3f}:d={fout}")
    fc += f";[{vlabel}]{','.join(vf)}[v]"

    fc += f";[ab]volume={seg.get('volume_db', 0)}dB[abv]"
    sfc, sfx_labels = sfx_graph(seg, sfx_dir, wd, cmd, dur)
    fc += sfc
    alabel = "abv"
    if sfx_labels:
        fc += f";[abv]{''.join(sfx_labels)}amix=inputs={len(sfx_labels) + 1}:duration=first:normalize=0[abs]"
        alabel = "abs"
    fc += (f";[{alabel}]afade=t=in:d={max(fin, FADE)},"
           f"afade=t=out:st={dur - max(fout, FADE):.3f}:d={max(fout, FADE)}[a]")
    run(cmd + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
               *VIDEO_OUT, *AUDIO_OUT, str(out)])


def seg_label(seg) -> str:
    if "photo" in seg:
        s = f"photo {seg['photo']} {seg['duration']}s"
    elif "block" in seg:
        s = f"block {seg['block']} ({len(seg['video'])} pieces, {len(seg.get('audio', []))} audio)"
    else:
        s = f"{seg['clip']} {seg['start']:.2f}-{seg['end']:.2f}"
    return s + (f" + pip {seg['pip']['clip']}" if "pip" in seg else "")


# --- captions

def load_words(plan, wd: Path) -> dict:
    """All transcript words, grouped by clip id (= source file stem)."""
    by_clip = {}
    for t in plan.get("transcripts", ["transcript.json"]):
        data = json.loads((wd / t).read_text(encoding="utf-8"))
        for w in data["words"]:
            by_clip.setdefault(w["clip"], []).append(w)
    return by_clip


def caption_words(plan, wd, offsets):
    """Every spoken word that survives the edit, placed on the output timeline."""
    words = load_words(plan, wd)
    clips = plan["clips"]
    placed = []

    def place(stem, src_start, src_end, out_start, speed):
        for w in words.get(stem, []):
            mid = (w["start"] + w["end"]) / 2
            if src_start <= mid < src_end:
                placed.append((out_start + (w["start"] - src_start) / speed,
                               out_start + (w["end"] - src_start) / speed, w["word"]))

    for seg, off in zip(plan["segments"], offsets):
        if "clip" in seg:
            place(Path(clips[seg["clip"]]["file"]).stem, seg["start"], seg["end"], off, seg.get("speed", 1.0))
        elif "block" in seg:
            for a in seg.get("audio", []):
                if "file" in a:
                    place(Path(a["file"]).stem, 0, 1e9, off + a.get("at", 0), 1.0)
                else:
                    place(Path(clips[a["clip"]]["file"]).stem, a["start"], a["end"], off + a.get("at", 0), 1.0)
    return sorted(placed)


def chunk_captions(words, max_words=4, max_chars=24, max_gap=0.35):
    """Short punchy chunks: break on punctuation, pauses, or length."""
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w[0] - cur[-1][1] > max_gap
                    or len(" ".join(x[2] for x in cur + [w])) > max_chars):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w[2][-1:] in ".,?!":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    out = []
    for k, ch in enumerate(chunks):
        start, end = ch[0][0], ch[-1][1]
        if k + 1 < len(chunks) and chunks[k + 1][0][0] - end < 0.25:
            end = chunks[k + 1][0][0]
        out.append((start, end, " ".join(x[2] for x in ch)))
    return out


def write_ass(chunks, path: Path):
    def ts(t):
        cs = int(round(t * 100))
        return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"

    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Cap,Arial Black,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,1,5,2,2,80,80,90,1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, end, text in chunks:
        lines.append(f"Dialogue: 0,{ts(start)},{ts(end)},Cap,,0,0,0,,{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- final mix

MUSIC_RAMP = 1.0  # seconds the music takes to duck out/in around "music": false segments


def music_catalog() -> dict:
    p = assets_dir("music") / "catalog.json"
    if not p.exists():
        return {}
    return {t["file"]: t for t in json.loads(p.read_text(encoding="utf-8"))}


def audio_graph(plan, cfg, inputs, spans) -> str:
    """Joined voice audio, plus ducked music for YouTube videos. Ends in [mix].
    spans: (start, end, music_allowed) per segment on the output timeline."""
    music = plan.get("music")
    allowed = [s for s in spans if s[2]]
    if not music or not allowed:
        return "[0:a]anull[mix]"
    if not cfg.get("music_dir"):
        sys.exit("plan has music but music_dir isn't set in config.json")
    total = spans[-1][1]
    start = allowed[0][0]
    needed = total - start
    xf = music.get("crossfade", 2.0)
    catalog = music_catalog()
    files = music.get("files") or [music["file"]]

    # Repeat the playlist until it covers the video, then crossfade it together.
    playlist, length = [], -xf
    while length < needed:
        f = files[len(playlist) % len(files)]
        path = Path(cfg["music_dir"]) / f
        playlist.append((f, path))
        length += probe_duration(path) - xf
    fc = "[0:a]asplit[voice][key]"
    labels = []
    for k, (f, path) in enumerate(playlist):
        i = n_inputs(inputs)
        inputs += ["-i", str(path)]
        lufs = catalog.get(f, {}).get("lufs")
        gain = music.get("level_lufs", -30) - lufs if lufs is not None else music.get("volume_db", -20)
        fc += f";[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={gain:.2f}dB[m{k}]"
        labels.append(f"m{k}")
    cur = labels[0]
    for k, nxt in enumerate(labels[1:], 1):
        fc += f";[{cur}][{nxt}]acrossfade=d={xf}:c1=tri:c2=tri[x{k}]"
        cur = f"x{k}"

    # Silence the music through "music": false segments, ramping around them.
    gates = [f"max(0,min(1,max(({a:.3f}-t)/{MUSIC_RAMP},(t-{b:.3f})/{MUSIC_RAMP})))"
             for a, b, ok in spans if not ok and a > start]
    gate = f",volume='{'*'.join(gates)}':eval=frame" if gates else ""
    fc += (f";[{cur}]atrim=0:{needed:.3f},afade=t=in:d=1.5,afade=t=out:st={needed - 3:.3f}:d=3,"
           f"adelay={int(start * 1000)}:all=1{gate},apad,atrim=0:{total:.3f}[music];"
           f"[music][key]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[ducked];"
           f"[voice][ducked]amix=inputs=2:duration=first:normalize=0[mix]")
    print(f"music: {' -> '.join(f for f, _ in playlist)} from {start:.1f}s"
          + (f", silent through {len(gates)} segment(s)" if gates else ""), file=sys.stderr)
    return fc


def finalise(plan, cfg, joined: Path, wd: Path, final: Path, captions: Path | None, spans, normalise=True):
    inputs = ["-i", str(joined)]
    agraph = audio_graph(plan, cfg, inputs, spans)

    if normalise:
        # Two-pass loudnorm on the final mix. Single-pass only approaches the
        # target (measured -16.6 LUFS for a -14 target on video1); measuring
        # first and applying one linear gain hits it. Costs ~30s on a 5 min
        # video (loudnorm works at 192kHz internally), so previews skip it.
        proc = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", *inputs, "-filter_complex",
                               f"{agraph};[mix]loudnorm={LOUDNORM}:print_format=json[out]",
                               "-map", "[out]", "-f", "null", "-"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        err = proc.stderr.decode(errors="replace")
        m = json.loads(err[err.rindex("{"):err.rindex("}") + 1])
        level = (f"loudnorm={LOUDNORM}:linear=true:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
                 f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
                 f"offset={m['target_offset']},aresample=48000")
    else:
        level = "anull"

    fc = f"{agraph};[mix]{level}[a]"
    if captions:
        # Relative filename (ffmpeg runs in wd) avoids escaping the drive colon;
        # fontsdir points libass at Windows fonts since there's no fontconfig.
        fc += f";[0:v]subtitles={captions.name}:fontsdir='C\\:/Windows/Fonts'[v]"
        vmap, vcodec = "[v]", VIDEO_OUT
    else:
        vmap, vcodec = "0:v", ["-c:v", "copy"]
    run(["ffmpeg", "-nostdin", "-y", "-v", "error", *inputs, "-filter_complex", fc,
         "-map", vmap, "-map", "[a]", *vcodec, *AUDIO_OUT, "-movflags", "+faststart", str(final)],
        cwd=wd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="video name (work dir under footage_root/_editor/work)")
    ap.add_argument("--plan", default="plan.json")
    ap.add_argument("--only", type=int, action="append", help="re-render only these segment numbers")
    ap.add_argument("--out", help="output file (default <work_dir>/<name>.mp4)")
    ap.add_argument("--preview", action="store_true",
                    help="fast 960x540 review render into render_preview/, output <name>_preview.mp4")
    args = ap.parse_args()

    global W, H, VIDEO_OUT, PIP_MARGIN
    if args.preview:
        W, H, PIP_MARGIN = 960, 540, 20
        VIDEO_OUT = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                     "-pix_fmt", "yuv420p", "-r", str(FPS)]

    cfg = load()
    root = cfg["footage_root"]
    wd = work_dir(args.name)
    plan = json.loads((wd / args.plan).read_text(encoding="utf-8"))
    if plan.get("music") and plan.get("destination") != "youtube":
        sys.exit(f"plan has music but destination is {plan.get('destination')!r}: background music "
                 f"is only for YouTube videos, never Amazon. Set \"destination\": \"youtube\" or drop \"music\".")
    clips = plan["clips"]
    cleanup = plan.get("voice_cleanup", False)
    sfx_dir = assets_dir("sfx")
    rdir = wd / ("render_preview" if args.preview else "render")
    rdir.mkdir(exist_ok=True)

    seg_files = []
    for i, seg in enumerate(plan["segments"]):
        out = rdir / f"{i:03d}.mp4"
        seg_files.append(out)
        if args.only and i not in args.only and out.exists():
            continue
        print(f"[{i + 1}/{len(plan['segments'])}] {seg_label(seg)}", file=sys.stderr)
        render_segment(seg, clips, root, wd, out, cleanup, sfx_dir)

    listing = rdir / "concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in seg_files), encoding="utf-8")
    joined = rdir / "joined.mp4"
    run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(joined)])

    # Segment positions from the rendered files, not the plan: frame rounding adds up.
    offsets, spans, t = [], [], 0.0
    for f, seg in zip(seg_files, plan["segments"]):
        d = probe_duration(f)
        offsets.append(t)
        spans.append((t, t + d, seg.get("music", True)))
        t += d

    captions = None
    if plan.get("captions"):
        chunks = chunk_captions(caption_words(plan, wd, offsets))
        captions = wd / "captions.ass"
        write_ass(chunks, captions)
        print(f"{len(chunks)} caption chunks", file=sys.stderr)

    final = Path(args.out) if args.out else wd / f"{args.name}{'_preview' if args.preview else ''}.mp4"
    steps = [x for x in ["music" if plan.get("music") else "", "captions" if captions else "",
                         "" if args.preview else "loudness -14 LUFS"] if x]
    print("final mix: " + (", ".join(steps) or "join only"), file=sys.stderr)
    finalise(plan, cfg, joined, wd, final.resolve(), captions, spans, normalise=not args.preview)
    print(f"wrote {final}")


if __name__ == "__main__":
    main()
