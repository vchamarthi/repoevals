"""Starter configurations shipped with repoevals (examples/starter-configs in the source tree)."""
from __future__ import annotations

from pathlib import Path


def _dirs() -> list[Path]:
    here = Path(__file__).resolve().parent
    return [here / "data" / "presets", here.parent.parent / "examples" / "starter-configs"]


def presets() -> dict[str, Path]:
    for d in _dirs():
        if d.is_dir():
            return {p.name.removesuffix(".repoevals.toml"): p for p in sorted(d.glob("*.repoevals.toml"))}
    return {}


def preset_text(name: str) -> str:
    found = presets()
    if name not in found:
        raise SystemExit(f"unknown preset {name!r}; available: {', '.join(found) or 'none'}")
    return found[name].read_text()
