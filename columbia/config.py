"""Columbia-1 configuration: where the sibling engines live and where output goes.

Values resolve in three layers, lowest precedence first::

    dataclass defaults  <  YAML file (columbia.yaml)  <  environment variables

Repo discovery is deliberate: Columbia-1 needs to find the ``ttscore`` and
``dubbing`` packages that live in *separate* repositories on disk. By default it
looks for sibling folders next to the Columbia-1 checkout (the layout you get
from cloning all three side by side), but either path can be pinned explicitly
via ``columbia.yaml`` or the ``COLUMBIA_TTS_REPO`` / ``COLUMBIA_DUBBING_REPO``
environment variables.

Nothing here imports torch/chatterbox/ttscore/dubbing, so the config can be read
on any machine.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Optional

# Repo root = the directory that contains the ``columbia`` package.
ROOT = Path(__file__).resolve().parents[1]

# Folder-name candidates for each sibling engine, tried in order. The user's
# machine may name the dubbing checkout any of these; the first that exists and
# actually contains the package wins.
_TTS_CANDIDATES = ("TTS", "tts", "TTS-Reader", "tts-reader")
_DUB_CANDIDATES = ("AI-Dubbing", "AI Dubbing", "ai-dubbing", "AIDubbing", "dubbing")


def _discover(base: Path, candidates: tuple[str, ...], package: str) -> Optional[str]:
    """Return the first sibling folder under ``base`` that holds ``package``."""
    for name in candidates:
        cand = base / name
        if (cand / package / "__init__.py").is_file():
            return str(cand)
    return None


@dataclass
class ColumbiaConfig:
    """Every knob for the Columbia-1 server. See ``columbia.example.yaml``."""

    # ── Network ──────────────────────────────────────────────────────────────
    host: str = "127.0.0.1"
    """Bind address. Localhost-only by default; set to 0.0.0.0 to expose on LAN."""

    port: int = 0
    """0 = pick a free port automatically (like the TTS app does)."""

    open_browser: bool = True
    """Open the default browser at the served URL once the server is up."""

    # ── Engine locations (the two sibling repos) ─────────────────────────────
    tts_repo: Optional[str] = None
    """Path to the TTS Reader checkout (contains the ``ttscore`` package).
    None -> auto-discover a sibling folder next to this repo."""

    dubbing_repo: Optional[str] = None
    """Path to the AI-Dubbing checkout (contains the ``dubbing`` package).
    None -> auto-discover a sibling folder next to this repo."""

    # ── Storage ──────────────────────────────────────────────────────────────
    output_dir: str = "output"
    """Unified library: narrations (.mp3) and dubbed videos (.mp4) land here."""

    upload_dir: str = ".uploads"
    """Scratch space for uploaded PDFs/EPUBs/videos/subtitles (per-job, cleaned)."""

    cache_dir: str = ".columbia_cache"
    """Root for the engines' per-chunk / per-cue caches (crash-resume)."""

    log_dir: str = "logs"
    """Per-run engine logs and summaries."""

    # ── Normalisation ────────────────────────────────────────────────────────
    def __post_init__(self) -> None:
        self.host = str(self.host).strip()
        self.port = int(self.port)

    # ── Resolved paths (always absolute, under the repo root) ────────────────
    def _abs(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (ROOT / p)

    @property
    def output_path(self) -> Path:
        return self._abs(self.output_dir)

    @property
    def upload_path(self) -> Path:
        return self._abs(self.upload_dir)

    @property
    def cache_path(self) -> Path:
        return self._abs(self.cache_dir)

    @property
    def log_path(self) -> Path:
        return self._abs(self.log_dir)

    @property
    def tts_repo_path(self) -> Optional[Path]:
        p = self.tts_repo or _discover(ROOT.parent, _TTS_CANDIDATES, "ttscore")
        return Path(p).resolve() if p else None

    @property
    def dubbing_repo_path(self) -> Optional[Path]:
        p = self.dubbing_repo or _discover(ROOT.parent, _DUB_CANDIDATES, "dubbing")
        return Path(p).resolve() if p else None

    def ensure_dirs(self) -> None:
        for p in (self.output_path, self.upload_path, self.cache_path, self.log_path):
            p.mkdir(parents=True, exist_ok=True)

    def register_engine_paths(self) -> None:
        """Put the two engine repos on ``sys.path`` so ``import ttscore`` /
        ``import dubbing`` resolve. Safe to call more than once; missing repos
        are simply skipped (the faculty then reports itself offline)."""
        for repo in (self.tts_repo_path, self.dubbing_repo_path):
            if repo is not None:
                s = str(repo)
                if s not in sys.path:
                    sys.path.insert(0, s)

    # ── Construction helpers ─────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ColumbiaConfig":
        known = {f.name for f in fields(cls)}
        clean = {}
        for k, v in (data or {}).items():
            if k not in known:
                raise KeyError(f"Unknown config key: {k!r}")
            clean[k] = v
        return cls(**clean)

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "ColumbiaConfig":
        """Build a config from an optional YAML file, then overlay env vars."""
        data: dict[str, Any] = {}
        yaml_path = Path(path) if path else (ROOT / "columbia.yaml")
        if yaml_path.is_file():
            import yaml
            with open(yaml_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        cfg = cls.from_dict(data)
        cfg._apply_env()
        return cfg

    def _apply_env(self) -> None:
        env_map = {
            "COLUMBIA_HOST": ("host", str),
            "COLUMBIA_PORT": ("port", int),
            "COLUMBIA_TTS_REPO": ("tts_repo", str),
            "COLUMBIA_DUBBING_REPO": ("dubbing_repo", str),
            "COLUMBIA_OUTPUT_DIR": ("output_dir", str),
        }
        for env, (attr, cast) in env_map.items():
            raw = os.environ.get(env)
            if raw:
                setattr(self, attr, cast(raw))
        self.__post_init__()
