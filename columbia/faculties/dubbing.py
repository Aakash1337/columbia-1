"""Dubbing faculty — a video + its .srt -> an English-dubbed MP4.

AI-Dubbing ships as a batch CLI with no web interface; this faculty gives it
one. It drives the existing single-video path (``dubbing.pipeline.process_video``)
on one uploaded video/subtitle pair rather than a whole folder.

``process_video`` reports progress through ``tqdm`` internally (no callback
argument), so to show live per-cue progress we temporarily swap the pipeline's
``tqdm`` for a counting iterator bound to this job. That is safe because the job
manager serializes engine jobs — only one dub runs at a time — and the original
is always restored in a ``finally``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from ..jobs import Job, JobManager
from .base import Availability, Faculty


class DubbingFaculty(Faculty):
    id = "dubbing"
    name = "Dubbing"
    tagline = "A foreign-language video plus its subtitles → an English voiceover, in sync."
    requires_package = "dubbing"

    # ── availability ─────────────────────────────────────────────────────────
    def probe(self) -> Availability:
        if not self._package_present():
            return Availability(
                False,
                "AI-Dubbing engine not found",
                detail="Expected the 'dubbing' package (set dubbing_repo in columbia.yaml).",
            )
        device = self._torch_device("cuda")
        reason = "Ready" if device == "cuda" else "Ready (CPU — slow without a GPU)"
        return Availability(True, reason, device=device)

    # ── job entry ────────────────────────────────────────────────────────────
    def start(self, manager: JobManager, params: dict, video: Path, srt: Path,
              reference: Optional[Path] = None) -> Job:
        label = video.name
        worker = self._make_worker(params, video, srt, reference)
        return manager.submit(self.id, label, worker)

    def _make_worker(self, params, video: Path, srt: Path, reference: Optional[Path]):
        cfg_obj = self.cfg

        # Parse UI options up front (outside the thread) so bad input fails fast.
        translate = bool(params.get("translate"))
        preview = bool(params.get("preview"))
        try:
            max_atempo = float(params.get("max_atempo") or 1.5)
        except (TypeError, ValueError):
            max_atempo = 1.5
        max_cues = 15 if preview else None

        def worker(job: Job) -> dict:
            from dubbing.config import Config
            from dubbing.logging_setup import RunReport, new_run_id, setup_logging
            from dubbing import ffmpeg_utils, pipeline as dub_pipeline
            from dubbing.pairing import Pair
            from dubbing.tts import Narrator

            cfg = Config(
                output_dir=str(cfg_obj.output_path),
                cache_dir=str(cfg_obj.cache_path / "dubbing"),
                log_dir=str(cfg_obj.log_path),
                reference_wav=str(reference) if reference else None,
                translate=translate,
                max_atempo=max_atempo,
                max_cues=max_cues,
                device=Faculty._torch_device("cuda"),
                overwrite=True,   # on-demand web dub: replace any prior output
            )
            cfg.validate()

            run_id = new_run_id()
            setup_logging(cfg.log_path, run_id)
            ffmpeg_utils.ensure_tools()   # fail clearly if ffmpeg is missing
            cfg.output_path.mkdir(parents=True, exist_ok=True)

            pair = Pair(key=video.stem, video=video, srt=srt)
            report = RunReport(run_id=run_id,
                               started_at=datetime.now().isoformat(timespec="seconds"))

            job.set_stage("Loading voice model")
            narrator = Narrator(cfg)

            original_tqdm = dub_pipeline.tqdm
            dub_pipeline.tqdm = _counting_tqdm(job)
            try:
                result = dub_pipeline.process_video(pair, cfg, narrator, report)
            finally:
                dub_pipeline.tqdm = original_tqdm
                narrator.reset()
                # The uploaded video/srt/reference are one-shot: the dub is
                # written elsewhere (output_dir) and the cache keeps the cues.
                import shutil as _shutil
                _shutil.rmtree(video.parent, ignore_errors=True)

            if result.status == "failed":
                raise RuntimeError(result.error or "dubbing failed")

            out = Path(result.output)
            _write_video_meta(out, video.stem, result)
            return {
                "output": str(out),
                "_path": str(out),
                "title": video.stem,
                "seconds": round(result.duration_s or 0.0, 1),
                "kind": "video",
                "cues": result.n_generated,
                "capped": result.n_capped,
                "download": f"/api/library/file/{out.name}",
                "stream": f"/api/library/file/{out.name}",
            }

        return worker


def _counting_tqdm(job: Job):
    """A ``tqdm``-shaped replacement that reports iteration progress to ``job``
    and honours cancellation between cues."""
    def wrapped(iterable, **_kw):
        items = list(iterable)
        total = len(items)
        job.set_stage("Voicing cues")
        job.progress(0, total)
        for i, item in enumerate(items):
            job.checkpoint()          # raises to abort a cancelled job
            yield item
            job.progress(i + 1, total)
    return wrapped


def _write_video_meta(out: Path, title: str, result) -> None:
    """Sidecar so the unified library shows a clean title/duration for videos
    (mirrors the .meta.json ttscore writes for audio)."""
    import json

    meta = {
        "title": title,
        "kind": "video",
        "audio_seconds": round(result.duration_s or 0.0, 1),
        "cues": result.n_generated,
        "capped": result.n_capped,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        out.with_suffix(".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
