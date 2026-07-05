"""The Faculty interface shared by every engine Columbia-1 hosts."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Optional

from ..config import ColumbiaConfig
from ..jobs import Job


@dataclass
class Availability:
    """Whether a faculty can actually run here, and why (shown in the UI)."""

    available: bool
    reason: str                       # short human line: "Ready" / why not
    device: Optional[str] = None      # "cuda" | "cpu" | None (unknown/offline)
    detail: Optional[str] = None      # optional extra (e.g. missing package name)

    def public(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason,
            "device": self.device,
            "detail": self.detail,
        }


class Faculty:
    """Base class: identity, an availability probe, and job description.

    Subclasses implement :meth:`probe` (cheap, import-guarded — never loads a
    model) and the faculty-specific ``start_*`` method the server calls to
    submit a job. The base intentionally knows nothing about FastAPI.
    """

    id: str = ""
    name: str = ""
    tagline: str = ""
    #: package that must be importable for this faculty to run
    requires_package: str = ""

    def __init__(self, cfg: ColumbiaConfig) -> None:
        self.cfg = cfg

    # ── availability ─────────────────────────────────────────────────────────
    def _package_present(self) -> bool:
        """True if the engine package can be located on sys.path (no import of
        its heavy dependencies — just a spec lookup)."""
        try:
            return importlib.util.find_spec(self.requires_package) is not None
        except (ImportError, ValueError):
            return False

    def probe(self) -> Availability:  # pragma: no cover - overridden
        raise NotImplementedError

    # ── UI description ───────────────────────────────────────────────────────
    def describe(self) -> dict:
        av = self.probe()
        return {
            "id": self.id,
            "name": self.name,
            "tagline": self.tagline,
            "status": av.public(),
        }

    # ── helpers shared by faculties ──────────────────────────────────────────
    @staticmethod
    def _torch_device(preferred: str = "cuda") -> str:
        """Resolve the device the engines would actually use, mirroring their
        own auto-fallback: cuda if a GPU is visible, else cpu."""
        try:
            import torch  # local import: absent on a models-free machine
            if preferred == "cuda" and torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        return "cpu"
