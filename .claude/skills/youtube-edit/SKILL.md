---
name: youtube-edit
description: Personal knowledge for Liz's automated YouTube editing pipeline (eggheadfixers channel) - tool gotchas, real measurements, and her editing taste. Use whenever working in the Video-Editor repo or editing her footage.
---

# youtube-edit

Personal skill for building and operating Liz's local YouTube editing pipeline.
Update this file in the same turn whenever she gives feedback — don't let it go stale.

## Project facts

- Repo: `/home/taytay/Video-Editor` (GitHub: `elizabethdberube/Video-Editor`, renamed
  from `AI-Video-Editor` 2026-10-23). Codebase lives here, version-controlled.
  This deviates from her original spec of putting tools inside
  `[footage_root]\_editor\tools` — we keep the *code* in this git repo instead, and
  only per-video *work files* (transcripts, plans, proxies) go under
  `<footage_root>/_editor/work/<video>`, so raw footage folders stay untouched and
  the tools stay version-controlled. Confirm this split still works for her once
  she's seen it in practice.
- Channel: eggheadfixers (youtube.com/c/eggheadfixers) — DIY tutorials and reviews
  (fix-it / repair content), not interview-style. Spec's "follow the speaker"
  punch-in rules are secondary here; B-roll of the actual repair/device and
  screen-recording inserts likely matter more than speaker tracking.
- Style goal (her words): "quirky, fast moving and very informative" — inspired by
  other creators generally, not her own back-catalog (she has no past raw+final
  footage to learn from; default editing rules + ongoing review notes are the only
  taste signal for now).
- Third machine (set up 2026-10-23): Windows 11 laptop, user `eliza`, i7-1365U,
  Intel Iris Xe, **no NVIDIA GPU**, 31GB RAM, **Smart App Control on**. Repo at
  `C:\Users\eliza\Video-Editor`, venv `.venv` (Python 3.12.10, CPU torch, NeMo
  3.0.0), ffmpeg 9.0.2 via winget. Footage at
  `C:\Users\eliza\OneDrive\Desktop\YouTube` (OneDrive Desktop). Liz chose to run
  the full pipeline CPU-only here. Measured: Parakeet on CPU transcribed the
  32s IMG_0894 in 3.6s, so CPU transcription is fine for her short clips.
  Smart App Control blocks DLLs from brand-new wheels (onnx 1.23.2, pyarrow
  26, torchaudio CPU) with "An Application Control policy has blocked this
  file". Fix by pinning an older release (see requirements-windows.txt), never
  by turning SAC off (it can't be turned back on without reinstalling Windows).
  A newly opened shell may still need ffmpeg on PATH
  (`%LOCALAPPDATA%\Microsoft\WinGet\Links`).
- Dev split: this Linux box has **no NVIDIA GPU, no CUDA** — used for writing code,
  non-GPU testing (ffmpeg logic, OpenCV, librosa) and real ffprobe measurements only.
  GPU/CPU-heavy work (NeMo/Parakeet transcription, TitaNet, renders) is written here
  but actually *run and measured* by Liz elsewhere. **Correction:** the original
  brief's "RTX GPU, 12GB VRAM, OOM above 17min" detail was unfilled template
  boilerplate, not a verified fact about her hardware — don't restate it as
  confirmed. The actual machine in use (see "Third machine" above) has no NVIDIA
  GPU at all and runs everything CPU-only, which has been fine for her short
  clips so far. Loop: I write + reason about a tool here, she runs it elsewhere,
  pastes back real console output/errors, I fix based on that — never assume
  NeMo/CPU/CUDA behavior without real pasted output.
- `config.json` (gitignored) holds `footage_root` (Linux path) and
  `footage_root_windows` (Windows path); `tools/config.py` picks the right one by
  `platform.system()`. `config.example.json` is the committed template.

## Test video footage (first real video, from /home/taytay/Desktop/YouTube)

- `IMG_0894.MOV` (32.4s) + `IMG_0895.MOV` (174.8s / ~2:55): raw clips, 1920x1080
  h264 yuv420p, **variable frame rate** (~30fps nominal but fractional
  avg_frame_rate — classic iPhone VFR). `render.py`'s CFR-conversion step is not
  optional for this footage.
- Audio has **two tracks** per clip: stream `0:a:0` is normal AAC stereo 48kHz
  (`mp4a`, "Core Media Audio" handler) — this is the one to transcribe. Stream
  `0:a:1` is a 4-channel `apac` track (Apple spatial/positional audio capture) —
  ffprobe can't even name its codec; do not feed this to NeMo or treat it as a
  normal audio stream. Always pass `-map 0:a:0` explicitly, never "the first audio
  stream found" blindly.
- 9 HEIC stills (IMG_0882-0892) are B-roll insert photos for this video.
- **Correction:** "Stop Wasting Food! ... Everblog AI Calender (2).mp4" is NOT
  unrelated — it's Liz's own earlier edit of this same product (Everblog
  Calendar fridge tablet), 720p, and she OK'd reusing its clips (2026-10-23).
  It contains her channel branding: "Egghead Reviews" intro sting (0-10.01s),
  opener "Hello my fellow eggheads", sign-off "From one egghead to another
  egghead, this is Liz signing out" (176.8-180.38s on its own shot).
- `IMG_0895(1).MOV` is a byte-different but stream-identical copy of
  IMG_0895 (same video+audio MD5 via `-f streamhash`) — ignore it.
- `RecordIt-1791828181.mp4`: vertical 884x1920 HEVC phone screen recording of
  the Everblog app, **silent audio track (-91 dB)**. **30-99s shows her Gmail,
  a readable typed password and a verification code — never use that range.**
- video1 (2026-10-23) = first rough cut, plan at
  `_editor/work/video1/plan.json`. Phone block uses SCRATCH VO lifted from
  the old video; Liz chose to record a real voiceover for it.
- `extract_audio()` in `transcribe.py` verified against real `IMG_0894.MOV` on the
  Linux box: ffprobe format duration 32.385s vs decoded-audio duration 32.404s
  (normal container padding), peak amplitude 0.39 (healthy level, no clipping).

## Architecture correction: no project timeline, per-clip source time only

Liz confirmed (2026-10-23): a typical video is cut from **many independent
clips and photos**, not one continuous take. So `transcript.json` (and every
downstream file — shots, drops, sections, broll) must never flatten onto one
offset/global timeline. Each clip keeps its own source time, 0 at that file's
start, and every reference to a time must carry the clip id alongside it —
`(clip_id, start, end)`, never a bare float. `transcribe.py` was rewritten
accordingly: `transcript.json` has a `clips` list (`id`, `file`, `duration`)
and `words` tagged with `clip`; `--offset` was removed, replaced by `--clip-id`
(defaults to the filename stem). The `.srt` preview is the one place that
still concatenates clips onto a display timeline, purely for human review —
editing tools must use the per-clip source time in the JSON, never the SRT's
times.

## Rule: edit out personal information, always (Liz, 2026-10-23)

Her words: "edit out any passwords or emails or address any personal
information like that". This applies to every video, without being asked,
and to every edit before it's handed over.

- **What counts:** passwords (typed or visible), email addresses (including
  the Apple ID on App Store install sheets and account/login screens), home
  or mailing addresses, phone numbers, verification/2FA codes, device
  pairing/share codes and anything else that grants access, payment or
  card details, full names of family members, house numbers, street signs
  or mail visible in shots, license plates, and notifications popping up
  on a screen recording. Her first name "Liz" is fine: she says it on camera.
- **Where it hides:** screen recordings above all (sign-up/login, App Store
  sheets, settings, notification banners), device screens filmed in
  footage, paperwork/mail/packages in B-roll, and photos.
- **Before rendering:** scan every screen-recording range and on-screen
  device the plan uses with dense frame sheets (1 fps minimum, 10 fps
  around anything suspicious) and read them. Don't assume a range is clean
  because the obvious part (e.g. login) was skipped -- video1/video2 both
  shipped an App Store sheet with her email until she spotted it.
- **Prefer cutting over blurring.** A cut can't fail; a blur on something
  that moves or animates (sheets sliding in, scrolling) easily misses frames.
  If it must stay visible, blur or box it and check every frame.
- **Leave margin at cuts, then check every frame at each join.** Her screen
  recordings are VFR: seeking to the exact boundary leaked one frame of the
  App Store sheet (start 24.2 showed a frame from before 24.2). Cut ~0.2s
  wide and inspect the finished video frame by frame across the join.
- Tell her what was found and removed. The raw files still contain it, so
  mention that if she shares raw footage.
- **Known ranges in current footage** (RecordIt-1791828181.mp4, never use):
  30-99s login/registration (email, typed password, verification code);
  20.4-24.2s App Store install sheet (Apple ID email; cut 20.0-24.4);
  105-112.9s tablet share code being typed (cut 104.0-113.2).

## Editorial principle: pick and choose, don't dump everything in

Liz confirmed (2026-10-23): a video's raw folder does not have to end up fully
used. Gathering/transcribing a clip or photo is not a commitment to include
it — I (Claude) am expected to pick and choose what actually serves the edit,
same as any editor would, not force every file in the folder into the final
cut. Treat "many clips and photos available" as a pool to select from, not a
checklist to exhaust.

## Editorial principle: prefer graphics for YouTube, but ask first

Liz confirmed (2026-10-23, her call: "update it so it uses graphics where
possible if its a youtube video. have it prompt to ask"):

- Since every video here is for YouTube, **default toward a motion graphic**
  when a moment explains a physical/technical idea (how something works,
  what a part does, a before/after, a number/spec) — graphics are the
  default tool for that job here, not just one option among several. The
  existing guardrail still applies: if an idea genuinely won't draw cleanly,
  cut back to the speaker instead of forcing a bad graphic.
- **Before adding a graphic to a plan, ask her in chat first** — don't
  silently decide a moment gets a graphic and render it. This is specific to
  graphics; it does not change the general workflow (she reviews low-res
  previews and gives notes on the rest of the edit). Surface the candidate
  moment, what the graphic would show, and let her confirm/adjust before
  `graphics.py` gets run for real.

## render.py: speed changes and punch-in zoom (added 2026-10-23, her call: "does it have the ability to zoom in and out on a frame? do slow motion or speed it up?")

- **Speed was already supported but had a real bug**, caught by testing
  before shipping: `atempo={speed}` directly on a single filter -- ffmpeg's
  `atempo` hard-rejects anything outside 0.5-2.0 (confirmed: a real
  slow-mo render at 0.3x failed with "Value 0.300000 ... out of range").
  Fixed with `atempo_chain()`, which decomposes any speed into a chain of
  per-filter factors each within range (e.g. 0.3x -> atempo=0.5,atempo=0.6).
  Verified for real: 0.3x and 3.0x renders of real footage both now succeed.
- **Punch-in zoom is new** (`"zoom": {"factor", "cx", "cy", "ease_in",
  "ease_out"}` on a clip segment). First attempt used `crop` with
  time-varying `w`/`h` expressions referencing `t` and an `eval=frame`
  option copied from memory of other filters (`scale` has that option,
  `crop` does not) -- **wrong on two counts**, caught by testing: `crop`
  has no `eval` option at all, and more importantly **`crop`'s `w`/`h` are
  only evaluated once at filter init in this ffmpeg build, not per-frame**
  (only `x`/`y` re-evaluate per frame) -- a `t`-dependent `h` alone fails
  with "Error when evaluating the expression", isolated with a minimal
  standalone ffmpeg command before touching the real code. Correct
  approach, and what the original brief actually specified: `zoompan`
  (already used for photo push-ins elsewhere in this file), with `d=1` so
  every output frame consumes the next real input frame -- motion keeps
  playing through the zoom instead of freezing like a photo push-in does.
  **Verified visually, not just by exit code**: rendered a zoom on real
  footage, pulled frames at the start/mid/end, confirmed wide -> genuinely
  zoomed-in -> wide, with real hand/body motion visibly continuing at each
  point (proves it isn't frozen on one frame).

## render.py: picture-in-picture, fades, music ducking, captions (added 2026-10-23, her call after asking what free features could be added; she picked my suggested order PiP -> fades/music -> captions)

- `"pip"` on any segment insets another clip (own source start, silent,
  white border, 0.2s alpha fade in/out, holds last frame if the source runs
  out). video1 uses it twice: phone screen recording on the right while she
  says "syncs with my phone" (rec 140s) and "the watermelon" (rec 196s). On
  her fridge shots the tablet sits mid-frame and the right third is empty
  fridge door, so `"position": "right"` is the default for her.
- `"fade_in"`/`"fade_out"` (picture to/from black + audio) and `"volume_db"`
  per segment. Every cut still gets the 20ms anti-click audio fade.
- Plan-level `"music"` loops a track from `music_dir` under the whole video,
  ducked by `sidechaincompress` keyed on the voice, 1.5s fade in / 3s fade
  out. Refused unless `"destination": "youtube"` (see taste log).
- `"captions": true` burns in short (<=4 word) chunks in Arial Black via an
  .ass file + `subtitles` filter. Words map onto the output timeline by clip
  file stem, including block voiceover pieces, so a transcribed vo.wav gets
  captions automatically. Segment offsets come from the rendered files'
  real durations, not plan maths, because frame rounding drifts. libass on
  Windows needs `fontsdir='C\:/Windows/Fonts'`, and ffmpeg runs with cwd =
  work dir so the .ass path has no drive colon to escape.
- `"overlays"` (graphics.py ProRes 4444 or PNG+duration) and `"sfx"` per
  segment; `"at"` is clip SOURCE time on clip segments, seconds-in on
  photo/block segments; out-of-range items warn and skip. Verified: test
  callout absent at 5.0s, present at 6.5s, gone at 8.7s for at=75.0 on cam
  69.4-; transparency correct. Sfx verified by subtracting tracks: the only
  difference sat at 5.45-5.70s (planned 5.4s), rest -73 dB. Test-asset
  gotcha: ffmpeg `drawbox` doesn't write alpha, so a drawbox in a yuva test
  graphic comes out invisible -- not a render.py bug.
- **graphics.py can't run on the Windows laptop yet**: Node there is v20
  (nvm4w), HyperFrames needs >=22, and no fonts/emoji are fetched. Freesound
  sfx also needs `freesound_api_key` in config.json, not set up.
- `"voice_cleanup": true` (plan-level; clip opt-out). Measured on IMG_0895:
  her iPhone audio is already clean (noise floor ~-54 dB), so gains are
  modest: voice-to-pause gap 29.3 -> 37.8 dB, mostly from the gentle gate.
  First version with a compressor did nothing (29.2 dB) because makeup gain
  lifted pauses as much as NR lowered them -- always measure the gap
  (speech RMS minus pause RMS), not absolute levels, since loudnorm rescales.
  Enabled in video1. She hasn't listened to it yet.
- Music ducking verified with a synthetic track by subtracting no-music
  from music mixes: music -34.4 dB alone, -39 to -43 dB under voice (5-8.5
  dB duck), 17-21 dB below her voice. Re-tune volume_db with a real track.
- `--preview`: 960x540, ultrafast, separate render_preview/ dir, and skips
  loudnorm. Profiling showed loudnorm was 30s of every render (it works at
  192kHz internally) and the HEVC phone block ~22s; segments themselves are
  fast. Full render of video1 198s, preview 67s.
- **video2 additions (2026-10-23)**: `"broll"` full-frame cutaways (photo
  push-in or clip, segment audio continues), music playlist (`"files"`,
  crossfaded, gain-matched to `level_lufs` from catalog_music's catalog),
  per-segment `"music": false` (ramped 1s), and `"voice_level_lufs"`
  per-segment speech levelling. Measured on video2: speech spread across
  sections 10 dB -> 1.9 dB; music -33 LUFS sits ~18 dB under her voice.
- **ffmpeg gotcha (real, caught on video2)**: crossfaded mp3s -> `adelay`
  (92.7s) -> `apad` -> `atrim` silently lost the delay (music started at 0
  and ran out ~200s in), though each filter behaves alone and short
  synthetic tests pass. Fixed by concatenating real anullsrc silence in
  front. Also: atrim/trim positional args map to start, then `starti` (an
  alias of start) per `ffmpeg -h filter=atrim`, not start/end -- always
  write `end=` explicitly. Verify music by subtracting the voice-only
  joined.mp4 from the preview mix (preview skips loudnorm, so it's exact).
- Lost once: a `git pull` that reset to a rewritten origin/main wiped this
  work while it was uncommitted, and it was re-merged on top of the
  atempo_chain/zoom version. Commit render.py changes promptly.

## catalog_music.py (new, 2026-10-23, her call: she wants to be able to
provide a music folder eventually and have the pipeline catalog it)

- Reads `config.json`'s `music_dir` (currently unset -- she'll provide a
  path later), measures BPM (`librosa.beat.beat_track`), integrated LUFS
  (`pyloudnorm`), energy swing (90th-10th percentile of short-term RMS in
  dB -- how much a track swings between quiet/loud sections), brightness
  (mean spectral centroid, Hz), and vocals via transcribing each track and
  checking for 50+ words (reuses `transcribe.py`'s functions directly) --
  matches her original spec for this tool exactly.
- Writes `<footage_root>/_editor/assets/music/catalog.json` with an
  absolute `source_path` back into `music_dir` -- **never writes into
  music_dir itself**, since that's her own external folder, not ours to
  modify (same boundary as raw footage folders).
- **Verified for real** (no real music folder exists yet): built a
  synthetic 120 BPM click track (numpy + soundfile) and ran it through the
  non-NeMo measurements -- detected 117.5 BPM (normal beat-tracker
  tolerance, not exact but in the right range), -24.7 LUFS, 436 Hz
  brightness, all sane real numbers from real signal processing, not
  placeholders. Vocals detection depends on NeMo like the other
  NeMo-facing tools -- degrades gracefully (`has_vocals: null` with a note)
  if NeMo isn't installed/working, rather than crashing the whole catalog
  run; not yet verified against a real vocal track.
- `--skip-vocals` flag skips the NeMo step entirely for a fast, GPU-free
  catalog pass (useful for just getting bpm/LUFS/brightness quickly).

## Tool gotchas (confirmed or carried over from Liz's own prior experience)

- **transcribe.py**: pass audio to NeMo as a numpy float32 array, never a path —
  path-based input makes NeMo leave a temp manifest file open, which crashes on
  Windows (her finding, not yet independently re-verified by us this round).
  Switch Parakeet to local attention (`rel_pos_local_attn`, context `[128,128]`)
  above ~10 min of audio as a precaution; the "OOMs around 17min on a 12GB card"
  rationale is unverified boilerplate (see "Dev split" correction above) — revisit
  this threshold once a long clip is actually run somewhere and OOM behavior (or
  lack of it) is observed for real. Always extract the explicit AAC track
  (`-map 0:a:0`), see footage notes above.
- **fetch_assets.py** (new, 2026-10-23): pulls free, no-permission-needed assets
  into `<footage_root>/_editor/assets/<kind>/`, each with a `manifest.json`
  recording source/license/attribution so nothing gets used blind.
  - `emoji` subcommand: bulk-downloads the OpenMoji SVG-color set (one-time,
    skips re-download if already present) — **confirmed working for real**:
    4,495 svgs + a 4,565-entry tag/annotation metadata file, searchable via
    `--search <tags...>` (e.g. `--search tool wrench warning` surfaced wrench,
    screwdriver, toolbox, hammer-and-wrench, warning, check-mark — good fit
    for repair-tutorial callouts). License is **CC-BY-SA 4.0, attribution
    required** (not plain CC-BY as I first assumed — checked the actual
    LICENSE.txt in the repo rather than guessing) — need an attribution line
    in the video description whenever an OpenMoji asset is used in a video.
  - `sfx` subcommand: searches Freesound restricted to CC0 only (no
    attribution ever owed) and downloads the HQ preview mp3 (the full
    original download needs an OAuth login, not just an API key — the
    preview avoids that and is fine for SFX use). **Not yet run for real** —
    needs `freesound_api_key` in `config.json`; Liz has to create that
    Freesound account/key herself (I can't create third-party accounts on
    her behalf) at https://freesound.org/apiv2/apply/.
  - Background **music fetching was explicitly descoped** (2026-10-23, her
    call) — skip it, don't build a Pixabay/music source into this tool.
  - `fonts` subcommand (added 2026-10-23, her call: "add the last two things
    to the plan"): downloads font families straight from the `google/fonts`
    GitHub repo (tries `ofl/` then `apache/` then `ufl/` license folders),
    no API key. **Confirmed working for real** — fetched Montserrat, Oswald,
    Bebas Neue, verified as valid TrueType files with their `OFL.txt`
    alongside. SIL Open Font License: free for commercial use, **no
    attribution required** (OFL only requires the license text travel with
    the font file, not credit in the video) — unlike OpenMoji above, nothing
    needs to go in the description for fonts. Variable fonts download as a
    single `FamilyName[axis].ttf` (e.g. `Montserrat[wght].ttf`) — fine for
    drawtext, but prefer a fixed-weight static file (like Bebas Neue's
    `BebasNeue-Regular.ttf`) for thumbnail/caption work to avoid relying on
    the variable font defaulting to the right weight.
- **thumbnail.py** (new, 2026-10-23, her call: "add the last two things to
  the plan"): auto-picks a few candidate frames from a clip (Laplacian
  sharpness + HSV saturation as a cheap "looks interesting" proxy — a
  starting heuristic, not a substitute for actually looking at the
  candidates), overlays bold title text via ffmpeg `drawtext` using a font
  from `fetch_assets.py fonts`, outputs 1280x720 PNGs (YouTube thumbnail
  spec) to `<work>/thumbnails/`. **Confirmed working for real** against
  `IMG_0895.MOV`: produced 3 real candidates; visually inspected one —
  "IS IT WORTH IT?" in bold white Bebas Neue with a black border, clearly
  legible over a real frame of the Everblog tablet. Not yet in the original
  11-tool list; this is net-new scope she asked to add.
- **graphics.py** (new, 2026-10-23, her call: "add TitaNet and HyperFrames"):
  renders a callout graphic (icon + bold text, animates in/out) to a
  transparent ProRes 4444 MOV via HyperFrames (HTML+GSAP -> video), for
  `render.py` to composite over footage. **Confirmed working for real,
  pixel-checked**: rendered a "CHECK THE FUSE FIRST" callout with a
  warning-triangle OpenMoji icon and Bebas Neue text, `ffprobe` confirms
  `prores`/`yuva444p12le`, and I sampled actual RGBA pixel values —
  background alpha=0 (fully transparent), badge alpha=255 (fully opaque),
  soft partial alpha at the drop-shadow edge. Not a guess from docs; actually
  measured. **Requires Node >=22 on PATH** (`npx hyperframes` fails/needs a
  real Node on this box's default v12 -- used `nvm use 22`; Windows setup
  needs its own Node >=22, e.g. `winget install OpenJS.NodeJS.LTS`, not yet
  verified there). HyperFrames itself is a HeyGen product with an optional
  cloud tier (`hyperframes cloud`/`lambda`/`auth`) — **never use those**,
  only local rendering (`init`, `render`, `check`), to keep this free/local
  per her original requirement.
- **speakers.py** (new, 2026-10-23, her call): TitaNet voiceprints per
  pause-bounded segment, numpy 2-means (no sklearn dependency), labels the
  cluster nearest a `--reference-clip` solo-you clip as "me". **Partially
  verified**: segment-grouping (splits correctly on >0.5s pauses) and the
  k-means math (cleanly separates synthetic well-separated clusters) checked
  locally with real assertions. The NeMo-facing calls
  (`EncDecSpeakerLabelModel.from_pretrained(...)`, `.get_embedding(path)`)
  are **unverified** — written from documentation, same position
  `transcribe.py` was in before Windows corrected some of its assumptions.
  Only makes sense for two-speaker footage — her content is mostly solo
  DIY/review narration (see "Channel" above), so check whether a given video
  actually has two speakers before running this; forcing a 2-way split on
  one voice just clusters noise into a meaningless split.
- **Free source map for future reference** (not yet built into tooling beyond
  emoji/sfx above): YouTube Audio Library is the only source structurally
  immune to Content ID claims (YouTube clears it directly) but has no API —
  can't be automated, only used manually. Pixabay (music/sfx/images, API key,
  no attribution) and Freesound (CC0 filter is the safe subset) are
  API-automatable. OpenMoji/Twemoji are open-source bulk packs, not
  per-query search.
- **Transitions (requested 2026-10-23, not yet built)**: use ffmpeg's built-in
  `xfade` filter for cut transitions in `render.py` — zero licensing risk
  since it's a filter, not a downloaded asset. Deliberately steered away from
  downloaded "transition pack" overlay clips (glitch/light-leak stock
  footage): those read as wedding/vlog template style, which clashes with the
  "quirky, fast, informative" DIY-tutorial feel. Default to plain
  crossfades/cuts via `xfade` unless Liz asks for a specific look. Wire this
  into `render.py`'s join logic when that tool gets built (it doesn't exist
  yet).

- **ffmpeg on Windows**: `drawtext` segfaults without fontconfig — always pass
  `fontfile='C\:/Windows/Fonts/arial.ttf'`. HEIC stills are tile grids that
  ffmpeg 9 stitches as a stream group, so `-vf` on a HEIC input errors
  ("Simple and complex filtering cannot be used together") — decode to PNG
  first (render.py caches them in `<work>/stills`). Windows python can't open
  `/c/...` git-bash paths — use `C:/...`.
- **loudnorm** single-pass missed -14 by 2.6 LU; render.py does two-pass
  (lands -14.6, capped by the -1.5 dBTP ceiling).
- Don't read frame rate from `ffprobe -read_intervals` pts — packets come in
  decode order (B-frames), so 30fps looks like 15. Count packets / duration.

## Editing taste log

- **OpenMoji icons need a credit** (license verified 2026-10-23 on their
  GitHub: CC BY-SA 4.0, not CC-BY as fetch_assets.py's docstring said --
  fixed). Any video using one gets this line in its description: "Emoji
  designed by OpenMoji – the open-source emoji and icon project. License:
  CC BY-SA 4.0". Tell Liz whenever an edit uses one. Fonts (OFL) and
  Freesound CC0 sfx need no credit.
- **Background music: YouTube videos only, never Amazon videos** (Liz's rule,
  2026-10-23). She makes videos for both destinations, so before adding music
  (from `music_dir`) know which one a video is for — if it isn't clear, ask
  her rather than guessing. An Amazon cut gets voice/clip audio only.
  render.py enforces it: a plan with `"music"` must also say
  `"destination": "youtube"` or the render stops.
- **Her older Everblog video has music baked in** (she confirmed,
  2026-10-23). Any segment from it gets `"music": false` so two songs never
  play at once; keep its block voiceover in one continuous piece so its
  baked music doesn't jump at cuts.
- video2 brief (2026-10-23): ~5 min, "talking throughout and background
  music". Result 4:55, all talking except the 10s sting and 4.5s of photos.
  Music: Nico Staf "Sunny Travel" -> Riot "Safety Net" (YouTube Audio
  Library artists). The four `Track*.mp3` in _music have unknown source --
  ask her before using them. All six are instrumental (catalog_music).
- New footage IMG_0942.mov: living-room talking head (AI features
  summary), recorded upside down (rotation=-180, ffmpeg autorotates fine),
  speech -29.5 LUFS (quiet, camera far away). Two Gemini AI images (receipts
  pile, grocery bags) used as B-roll for receipt scanning / food.
- **No captions** (Liz, 2026-10-23, right after I built burned-in
  captions): "I dont want captions". Keep `"captions"` off in plans; the
  feature stays in render.py, off by default, but don't add it unless she
  asks. No reason given; ask before assuming it also covers title text.
- She asked for a ~5 min target on video1 (2026-10-23) even though a tight
  cut was ~4 min; she prefers padding with real content (full-speed screen
  recording) over cutting short. Unconfirmed whether that's a general rule.
- Channel branding to keep using: Egghead Reviews sting at the top, "fellow
  eggheads" greeting, "From one egghead to another egghead" sign-off (all from
  her earlier Everblog video).
- Steer away from flashy downloaded transition packs (glitch/light-leak
  overlays); plain crossfades/cuts fit the DIY-tutorial tone better. Not yet
  directly confirmed by Liz — my own editorial judgment call when she asked
  about free transitions (2026-10-23); flag this one for her to correct if
  she actually wants a punchier look.
- (fill in as Liz gives review notes on real cuts. Log each confirmed
  preference with the reasoning, not just the verdict.)

## Open questions / unconfirmed

- Whether the git-repo-for-code / footage-root-for-work-files split (above) is
  actually fine with her, or she wants tools physically inside
  `_editor\tools` on the footage root too (e.g. for use on a totally different
  machine without this git repo cloned).
- `transcribe(audio=[numpy_array], timestamps=True)` is **confirmed working**
  (NeMo 3.0.0, CPU, real IMG_0894 run 2026-10-23: 43 words, correct per-clip
  times). `change_attention_model(...)` for audio over 10 min has still never
  been run.
