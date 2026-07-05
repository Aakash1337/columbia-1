"""The job manager: how Columbia-1 runs one engine task at a time.

A *faculty* (speech, dubbing) hands the manager a callable that does the real,
blocking work and reports progress. The manager runs it on a background thread,
**serialized behind a single lock** so only one job ever touches the GPU at once
(both engines load Chatterbox; two at a time would fight over VRAM), and exposes
a small status dict the web UI polls.

This is deliberately the whole "backend" abstraction. Today it is an in-process
thread pool of size one. "Remote later" (the second half of the brief) means
replacing :class:`JobManager` with one that enqueues onto a real queue and lets
a separate GPU worker pick jobs up — the faculties and routes above it call the
same ``submit`` / ``get`` / ``cancel`` surface and don't change.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# The signature every faculty's worker implements. It receives the live Job (so
# it can report progress and check for cancellation) and returns a result dict
# that is merged into the job's public payload (e.g. {"output": ..., "title":
# ...}). Raising is fine — the manager records it as a failure.
Worker = Callable[["Job"], dict]


class JobCancelled(RuntimeError):
    """Raised inside a worker (via ``job.checkpoint()``) to abort cleanly."""


@dataclass
class Job:
    id: str
    faculty: str
    label: str
    status: str = "queued"          # queued | running | done | failed
    done: int = 0                   # progress numerator (e.g. chunks/cues voiced)
    total: int = 0                  # progress denominator (0 = indeterminate)
    stage: str = "Queued"           # human-readable phase for the UI
    error: Optional[str] = None
    result: dict = field(default_factory=dict)   # faculty output (output path, ...)
    created: float = field(default_factory=time.time)
    _cancel: bool = False

    # ── worker-facing helpers ────────────────────────────────────────────────
    def progress(self, done: int, total: int) -> None:
        self.done, self.total = int(done), int(total)
        self.checkpoint()

    def set_stage(self, stage: str) -> None:
        self.stage = stage

    def checkpoint(self) -> None:
        """Cooperative cancellation point — workers call this in their loop."""
        if self._cancel:
            raise JobCancelled("Cancelled.")

    # ── UI-facing view ───────────────────────────────────────────────────────
    def public(self) -> dict:
        return {
            "id": self.id,
            "faculty": self.faculty,
            "label": self.label,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "stage": self.stage,
            "error": self.error,
            "result": {k: v for k, v in self.result.items() if k != "_path"},
        }


class JobManager:
    """Runs faculty workers one at a time; keeps a bounded registry for polling."""

    def __init__(self, max_registry: int = 60) -> None:
        self._jobs: dict[str, Job] = {}
        self._registry_lock = threading.Lock()   # guards the _jobs dict
        self._gpu_lock = threading.Lock()         # one engine job at a time
        self._max_registry = max_registry

    def submit(self, faculty: str, label: str, worker: Worker) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], faculty=faculty, label=label)
        with self._registry_lock:
            self._jobs[job.id] = job
            self._prune()
        threading.Thread(target=self._run, args=(job, worker), daemon=True).start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._registry_lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job._cancel = True
        return True

    # ── internals ────────────────────────────────────────────────────────────
    def _run(self, job: Job, worker: Worker) -> None:
        with self._gpu_lock:                     # serialize GPU access
            if job._cancel:
                job.status, job.error, job.stage = "failed", "Cancelled.", "Cancelled"
                return
            job.status, job.stage = "running", "Starting"
            try:
                out = worker(job) or {}
                job.result.update(out)
                job.status, job.stage = "done", "Complete"
            except JobCancelled:
                job.status, job.error, job.stage = "failed", "Cancelled.", "Cancelled"
            except Exception as exc:  # noqa: BLE001 — surface any engine error to the UI
                job.status = "failed"
                job.error = str(exc) or exc.__class__.__name__
                job.stage = "Failed"
                traceback.print_exc()

    def _prune(self) -> None:
        """Drop the oldest finished jobs beyond the registry cap (files on disk
        are untouched — the library reads those independently)."""
        finished = [k for k, v in self._jobs.items() if v.status in ("done", "failed")]
        overflow = len(self._jobs) - self._max_registry
        for k in finished[: max(0, overflow)]:
            self._jobs.pop(k, None)
