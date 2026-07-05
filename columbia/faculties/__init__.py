"""The faculty registry.

A *faculty* is one engine surfaced through Columbia-1 (speech synthesis,
dubbing). Each is a small object that can (a) probe whether its underlying
package is importable and usable on this machine, and (b) run a single job
through the shared :class:`~columbia.jobs.JobManager`.

New faculties are additive: implement :class:`~columbia.faculties.base.Faculty`
and add it to :func:`build_faculties`. Nothing else in the app needs to change —
the server iterates the registry for status and dispatches by faculty id.
"""

from __future__ import annotations

from ..config import ColumbiaConfig
from .base import Faculty
from .dubbing import DubbingFaculty
from .speech import SpeechFaculty


def build_faculties(cfg: ColumbiaConfig) -> "dict[str, Faculty]":
    """Instantiate every faculty, keyed by id, in display order."""
    faculties: list[Faculty] = [
        SpeechFaculty(cfg),
        DubbingFaculty(cfg),
    ]
    return {f.id: f for f in faculties}


__all__ = ["Faculty", "SpeechFaculty", "DubbingFaculty", "build_faculties"]
