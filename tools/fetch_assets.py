#!/usr/bin/env python3
"""Pull free, no-permission-needed assets into the shared asset pool
(<footage_root>/_editor/assets/<kind>/), with a manifest.json recording
license/attribution so nothing gets used without knowing its terms.

Subcommands:
    emoji   Bulk-download the OpenMoji SVG set (CC-BY 4.0, open source,
            no API key). One-time fetch; searchable by tag afterward.
    sfx     Search and download sound effects from Freesound, restricted
            to CC0 ("no rights reserved") so no attribution is ever owed.
            Needs your own free API key in config.json (freesound_api_key) --
            sign up yourself at https://freesound.org/apiv2/apply/, I can't
            create that account for you.
    fonts   Download a font family straight from the google/fonts GitHub
            repo (SIL Open Font License -- free for commercial use, no
            attribution required), no API key needed.

Usage:
    python tools/fetch_assets.py emoji
    python tools/fetch_assets.py emoji --search tool wrench fix warning
    python tools/fetch_assets.py sfx --query "click" --count 5
    python tools/fetch_assets.py fonts --family Montserrat Oswald
"""
import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import assets_dir

OPENMOJI_RELEASE_API = "https://api.github.com/repos/hfg-gmuend/openmoji/releases/latest"
OPENMOJI_META_URL = "https://raw.githubusercontent.com/hfg-gmuend/openmoji/master/data/openmoji.json"


def _download(url: str, dest: Path, timeout: int = 60):
    req = Request(url, headers={"User-Agent": "video-editor-pipeline"})
    with urlopen(req, timeout=timeout) as resp:
        dest.write_bytes(resp.read())


def _load_manifest(d: Path) -> list:
    p = d / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else []


def _save_manifest(d: Path, manifest: list):
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2))


def fetch_emoji(search_tags=None):
    """Bulk fetch the OpenMoji SVG-color set, once. Re-running is a no-op
    unless the local set is missing, since it's a fixed open-source pack,
    not per-query search results."""
    d = assets_dir("emoji")
    svg_dir = d / "svg"
    meta_path = d / "openmoji_meta.json"

    if not meta_path.exists():
        print("fetching OpenMoji release metadata...", file=sys.stderr)
        req = Request(OPENMOJI_RELEASE_API, headers={"User-Agent": "video-editor-pipeline"})
        with urlopen(req, timeout=30) as resp:
            release = json.loads(resp.read())
        svg_asset = next(a for a in release["assets"] if a["name"] == "openmoji-svg-color.zip")

        zip_path = d / "openmoji-svg-color.zip"
        print(f"  downloading {svg_asset['name']} ({svg_asset['size'] / 1e6:.1f} MB)...", file=sys.stderr)
        _download(svg_asset["browser_download_url"], zip_path, timeout=120)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(svg_dir)
        zip_path.unlink()

        print("  fetching tag/keyword metadata...", file=sys.stderr)
        _download(OPENMOJI_META_URL, meta_path)

        _save_manifest(d, [{
            "source": "openmoji", "version": release["tag_name"],
            "license": "CC-BY-SA-4.0",
            "attribution_required": True,
            "attribution_text": f"Emoji by OpenMoji ({release['tag_name']}), CC BY-SA 4.0 - openmoji.org",
            "local_path": str(svg_dir), "fetched_at": datetime.now(timezone.utc).isoformat(),
        }])
        print(f"  {len(list(svg_dir.glob('*.svg')))} svgs in {svg_dir}", file=sys.stderr)
    else:
        print(f"OpenMoji set already present at {svg_dir}, skipping download", file=sys.stderr)

    if search_tags:
        meta = json.loads(meta_path.read_text())
        search_tags = [t.lower() for t in search_tags]
        matches = []
        for e in meta:
            haystack = f"{e['annotation']} {e['tags']} {e.get('openmoji_tags', '')}".lower()
            if any(t in haystack for t in search_tags):
                matches.append((e["hexcode"], e["annotation"]))
        print(f"\n{len(matches)} matches for {search_tags}:", file=sys.stderr)
        for hexcode, annotation in matches[:40]:
            print(f"  {svg_dir / (hexcode + '.svg')}  -- {annotation}", file=sys.stderr)


def fetch_sfx(query: str, count: int = 5):
    """Search Freesound, restricted to CC0 so no attribution is ever owed,
    and download the HQ preview (full originals need an OAuth login, not
    just an API key -- previews avoid that entirely and are fine for SFX)."""
    from config import load
    api_key = load().get("freesound_api_key")
    if not api_key:
        raise SystemExit(
            "config.json is missing \"freesound_api_key\". Get a free one at "
            "https://freesound.org/apiv2/apply/ (sign up yourself, then paste the key in)."
        )

    d = assets_dir("sfx")
    manifest = _load_manifest(d)
    have_ids = {m["id"] for m in manifest if m["source"] == "freesound"}

    url = (
        "https://freesound.org/apiv2/search/text/"
        f"?query={query}&filter=license:\"Creative Commons 0\""
        f"&fields=id,name,license,previews,tags,url"
        f"&page_size={count}&token={api_key}"
    )
    req = Request(url, headers={"User-Agent": "video-editor-pipeline"})
    with urlopen(req, timeout=30) as resp:
        results = json.loads(resp.read())["results"]

    for r in results:
        if r["id"] in have_ids:
            continue
        preview_url = r["previews"]["preview-hq-mp3"]
        local_path = d / f"{r['id']}_{r['name'].replace('/', '_')}"
        if not local_path.suffix:
            local_path = local_path.with_suffix(".mp3")
        print(f"  downloading '{r['name']}' ({r['license']})...", file=sys.stderr)
        _download(preview_url, local_path)
        manifest.append({
            "id": r["id"], "source": "freesound", "license": "CC0",
            "attribution_required": False, "query": query, "tags": r["tags"],
            "source_url": r["url"], "local_path": str(local_path),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })
    _save_manifest(d, manifest)
    print(f"  {len(results)} results, {d / 'manifest.json'} updated", file=sys.stderr)


GOOGLE_FONTS_API = "https://api.github.com/repos/google/fonts/contents/{license_dir}/{slug}"
GOOGLE_FONTS_RAW = "https://raw.githubusercontent.com/google/fonts/main/{license_dir}/{slug}/{filename}"


def fetch_fonts(families):
    """Download font files straight from google/fonts, trying each license
    folder (most fonts are OFL; a few are apache/ufl). No API key, no
    attribution required -- OFL only requires the license text travel with
    the font file itself, not credit in the video."""
    d = assets_dir("fonts")
    manifest = _load_manifest(d)
    have = {m["family"] for m in manifest if m["source"] == "google-fonts"}

    for family in families:
        if family in have:
            print(f"'{family}' already fetched, skipping", file=sys.stderr)
            continue
        slug = family.lower().replace(" ", "")
        entry = None
        for license_dir in ("ofl", "apache", "ufl"):
            req = Request(GOOGLE_FONTS_API.format(license_dir=license_dir, slug=slug),
                           headers={"User-Agent": "video-editor-pipeline"})
            try:
                with urlopen(req, timeout=20) as resp:
                    files = json.loads(resp.read())
            except Exception:
                continue
            ttfs = [f["name"] for f in files if f["name"].endswith(".ttf") and "Italic" not in f["name"]]
            if not ttfs:
                continue
            font_dir = d / slug
            font_dir.mkdir(exist_ok=True)
            for fname in ttfs + ["OFL.txt" if license_dir == "ofl" else "LICENSE.txt"]:
                try:
                    _download(GOOGLE_FONTS_RAW.format(license_dir=license_dir, slug=slug, filename=fname),
                               font_dir / fname)
                except Exception as e:
                    print(f"  warning: couldn't fetch {fname}: {e}", file=sys.stderr)
            entry = {
                "family": family, "source": "google-fonts", "license_dir": license_dir,
                "license": "SIL-OFL-1.1" if license_dir == "ofl" else license_dir,
                "attribution_required": False,
                "local_path": str(font_dir / ttfs[0]),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            print(f"  '{family}' -> {font_dir / ttfs[0]}", file=sys.stderr)
            break
        if entry is None:
            print(f"  could not find '{family}' in google/fonts (tried ofl/apache/ufl)", file=sys.stderr)
            continue
        manifest.append(entry)
    _save_manifest(d, manifest)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_emoji = sub.add_parser("emoji", help="bulk-fetch the OpenMoji set, then optionally search it")
    p_emoji.add_argument("--search", nargs="*", default=None,
                          help="list local matches by tag/annotation, e.g. --search tool wrench warning")

    p_sfx = sub.add_parser("sfx", help="search+download CC0 sound effects from Freesound")
    p_sfx.add_argument("--query", required=True)
    p_sfx.add_argument("--count", type=int, default=5)

    p_fonts = sub.add_parser("fonts", help="download font families from google/fonts (OFL, no API key)")
    p_fonts.add_argument("--family", nargs="+", required=True, help="e.g. --family Montserrat Oswald")

    args = ap.parse_args()
    if args.cmd == "emoji":
        fetch_emoji(search_tags=args.search)
    elif args.cmd == "sfx":
        fetch_sfx(args.query, args.count)
    elif args.cmd == "fonts":
        fetch_fonts(args.family)


if __name__ == "__main__":
    main()
