#!/usr/bin/env python3
"""Render a motion-graphic callout (icon + bold text, animates in/out) to a
transparent ProRes 4444 MOV via HyperFrames (HTML + GSAP -> video), for
render.py to composite over footage.

Needs Node >=22 and `npx` on PATH (HyperFrames' own requirement -- this repo
doesn't vendor a specific Node, see README for how to get one). Uses a font
from fetch_assets.py fonts and (optionally) an OpenMoji icon from
fetch_assets.py emoji; fetch those first.

Confirmed working end-to-end (2026-10-23): a callout composition rendered
through `hyperframes render --format mov` produces real ProRes 4444 with a
genuine alpha channel (yuva444p12le) -- verified pixel-by-pixel, not just
assumed from the docs.

Usage:
    python tools/graphics.py --name video1 --out-id warning1 \
        --text "CHECK THE FUSE FIRST" --emoji 26A0 --duration 4
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import assets_dir, work_dir

W, H = 1920, 1080

TEMPLATE = """<!doctype html>
<html lang="en" data-resolution="landscape">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={w}, height={h}" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      @font-face {{ font-family: "callout-font"; src: url("{font_file}"); }}
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: {w}px; height: {h}px; overflow: hidden; background: transparent; }}
      #root {{ width: 100%; height: 100%; position: relative; }}
      #callout {{
        position: absolute; left: {x}px; bottom: {bottom}px;
        display: flex; align-items: center; gap: 24px;
        background: {bg}; padding: 24px 40px; border-radius: 12px;
        box-shadow: 0 12px 32px rgba(0,0,0,0.4);
      }}
      #callout img {{ width: 64px; height: 64px; }}
      #callout span {{ font-family: "callout-font"; font-size: {fontsize}px; letter-spacing: 0.02em; color: {fg}; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{duration}" data-width="{w}" data-height="{h}">
      <div id="callout" class="clip" data-start="0" data-duration="{duration}" data-track-index="0">
        {icon_tag}
        <span>{text}</span>
      </div>
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      tl.fromTo("#callout",
        {{ opacity: 0, x: -40, scale: 0.9 }},
        {{ opacity: 1, x: 0, scale: 1, duration: 0.35, ease: "back.out(1.7)" }}, 0);
      tl.to("#callout", {{ opacity: 0, x: -40, duration: 0.25, ease: "power2.in" }}, {fade_out_at});
      window.__timelines["main"] = tl;
      tl.seek(0);
    </script>
  </body>
</html>
"""


def font_path(family: str) -> Path:
    manifest = json.loads((assets_dir("fonts") / "manifest.json").read_text())
    for m in manifest:
        if m["family"].lower() == family.lower():
            return Path(m["local_path"])
    raise SystemExit(f"font '{family}' not fetched -- run: python tools/fetch_assets.py fonts --family \"{family}\"")


def emoji_path(hexcode: str) -> Path:
    p = assets_dir("emoji") / "svg" / f"{hexcode.upper()}.svg"
    if not p.exists():
        raise SystemExit(f"emoji {hexcode} not fetched -- run: python tools/fetch_assets.py emoji")
    return p


def check_hyperframes():
    if shutil.which("npx") is None:
        raise SystemExit("npx not found -- HyperFrames needs Node >=22 on PATH (see README)")


def render_callout(project_dir: Path, text: str, out_path: Path, *, emoji_hex=None, font="Bebas Neue",
                    duration=4.0, x=120, bottom=160, fontsize=56, bg="#e8491d", fg="white"):
    project_dir.mkdir(parents=True, exist_ok=True)
    font_file = font_path(font)
    shutil.copy(font_file, project_dir / font_file.name)

    icon_tag = ""
    if emoji_hex:
        icon_file = emoji_path(emoji_hex)
        shutil.copy(icon_file, project_dir / icon_file.name)
        icon_tag = f'<img src="{icon_file.name}" />'

    html = TEMPLATE.format(
        w=W, h=H, font_file=font_file.name, x=x, bottom=bottom, bg=bg, fg=fg,
        fontsize=fontsize, duration=duration, icon_tag=icon_tag,
        text=text.replace("<", "&lt;").replace(">", "&gt;"),
        fade_out_at=max(duration - 0.4, 0),
    )
    (project_dir / "index.html").write_text(html)

    check_hyperframes()
    cmd = ["npx", "--yes", "hyperframes", "render", str(project_dir),
           "--format", "mov", "-o", str(out_path)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"hyperframes render failed:\n{proc.stdout.decode(errors='replace')[-4000:]}")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="video name (work dir under footage_root/_editor/work)")
    ap.add_argument("--out-id", required=True, help="graphic id, e.g. warning1 -> <work>/graphics/warning1.mov")
    ap.add_argument("--text", required=True)
    ap.add_argument("--emoji", default=None, help="OpenMoji hexcode, e.g. 26A0 for warning (see fetch_assets.py emoji --search)")
    ap.add_argument("--font", default="Bebas Neue")
    ap.add_argument("--duration", type=float, default=4.0)
    ap.add_argument("--x", type=int, default=120)
    ap.add_argument("--bottom", type=int, default=160, help="distance from bottom edge, px")
    ap.add_argument("--bg", default="#e8491d")
    ap.add_argument("--fg", default="white")
    args = ap.parse_args()

    wd = work_dir(args.name)
    graphics_dir = wd / "graphics"
    graphics_dir.mkdir(exist_ok=True)
    project_dir = graphics_dir / f".{args.out_id}_src"
    out_path = graphics_dir / f"{args.out_id}.mov"

    print(f"rendering graphic '{args.out_id}'...", file=sys.stderr)
    render_callout(project_dir, args.text, out_path, emoji_hex=args.emoji, font=args.font,
                    duration=args.duration, x=args.x, bottom=args.bottom, bg=args.bg, fg=args.fg)
    print(f"wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
