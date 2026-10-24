# Video-Editor

A local, free YouTube editing pipeline for the eggheadfixers channel (DIY
repair tutorials and reviews). Everything runs on your own PC — no paid
software, no cloud editing services.

## How it works

1. **Transcribe** your raw footage (Parakeet, a free speech-to-text model) to
   get word-level timestamps.
2. **Claude makes the editorial decisions** — what to cut, which B-roll to
   use, where to punch in, what graphics to add — and writes them into a
   `plan.json` file for that video.
3. **Scripts carry out the plan**: `render.py` turns `plan.json` into an
   actual video using ffmpeg. You never hand-edit video directly; you review
   a fast low-res preview and give notes, and the plan gets updated.

Every plan is anchored to *source time* — seconds into the original clip —
so re-ordering or re-cutting never breaks graphics, B-roll, or sound effects
tied to a moment.

## Layout

- `tools/` — the pipeline scripts (this repo, version-controlled).
- `config.json` (not committed — copy `config.example.json`) — points the
  tools at your footage location, which differs per machine.
- Your footage root gets a `_editor/` folder added next to it:
  - `_editor/work/<video>/` — transcripts, plans, rendered output for one video.
  - `_editor/assets/` — shared, reusable stuff: free fonts, emoji/icons, sound
    effects, pulled in by `fetch_assets.py` with license info recorded.
- `.claude/skills/youtube-edit/` — everything Claude has learned so far:
  tool quirks, real measurements, and your editing taste. Read automatically
  whenever Claude works in this repo.

## Setup notes

- Needs **Node >=22** on PATH for `graphics.py` (it shells out to
  `npx hyperframes`). If your default `node` is older, install a current one
  (e.g. `winget install OpenJS.NodeJS.LTS` on Windows, or `nvm install 22`).
  HyperFrames has an optional paid cloud tier — this pipeline only ever uses
  its local rendering, never `hyperframes cloud`/`lambda`/`auth`.

## What's built so far

- `transcribe.py` — speech-to-text with word timestamps
- `render.py` — turns a plan into a finished video
- `fetch_assets.py` — free emoji, fonts, and sound effects (all license-checked)
- `thumbnail.py` — generates thumbnail candidates from your footage
- `graphics.py` — renders a motion-graphic callout (icon + text) to a
  transparent overlay, for `render.py` to composite over footage
- `speakers.py` — identifies who's speaking per segment via voiceprints
  (two-speaker footage only; not yet verified on real hardware)
- `catalog_music.py` — measures BPM/LUFS/energy/brightness/vocals for a
  folder of background music (point `music_dir` in `config.json` at it
  once you have one)

`render.py` also supports, all set in `plan.json` (see its docstring):

- slow-motion/fast-forward (`"speed"`) and punch-in zooms (`"zoom"`)
- **picture-in-picture** (`"pip"`): inset a second clip, e.g. the phone
  screen recording beside you demoing the device
- **fades** to/from black (`"fade_in"`, `"fade_out"`) and per-segment volume
  (`"volume_db"`)
- **background music** (`"music"`) that dips automatically while you talk.
  YouTube videos only: the plan must say `"destination": "youtube"`, and
  Amazon videos never get music.
- burned-in captions (`"captions": true`) built from the transcript. Off
  by default, and not used on this channel's videos.

## Where things go

- **Background music:** put tracks in the folder set as `music_dir` in
  `config.json` (on the Windows laptop:
  `C:\Users\eliza\OneDrive\Desktop\YouTube\_music`). Use only music you're
  allowed to use on YouTube, such as the YouTube Audio Library.
- **Finished videos:** `<footage root>\_editor\work\<video>\<video>.mp4`.

## Windows setup (no NVIDIA GPU)

1. `winget install Python.Python.3.12` and `winget install Gyan.FFmpeg`
2. `python -m venv .venv`
3. `.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu`
   (don't add torchaudio)
4. `.venv\Scripts\python -m pip install -r requirements-windows.txt`
5. Copy `config.example.json` to `config.json` and set `footage_root_windows`
   (and `music_dir`).

If Windows says "An Application Control policy has blocked this file", that's
Smart App Control rejecting a brand-new package build. Pin an older version in
`requirements-windows.txt` rather than turning Smart App Control off.

Still to come: automatic pause-trimming, punch-ins, pacing checks, and a few
other tools — see the skill file for the full list and current status.
