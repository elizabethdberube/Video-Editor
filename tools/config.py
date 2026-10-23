"""Loads config.json from the repo root. Never hardcode footage_root elsewhere."""
import json
import platform
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def load():
    with open(_REPO_ROOT / "config.json") as f:
        cfg = json.load(f)
    if platform.system() == "Windows" and cfg.get("footage_root_windows"):
        cfg["footage_root"] = cfg["footage_root_windows"]
    cfg["footage_root"] = Path(cfg["footage_root"])
    return cfg


def work_dir(video_name: str) -> Path:
    cfg = load()
    d = cfg["footage_root"] / "_editor" / "work" / video_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def assets_dir(kind: str) -> Path:
    """Shared, video-independent asset pool, e.g. assets_dir('emoji'), assets_dir('sfx')."""
    cfg = load()
    d = cfg["footage_root"] / "_editor" / "assets" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d
