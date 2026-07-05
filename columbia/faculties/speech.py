"""Speech faculty — Text / URL / document -> narration, via the ``ttscore`` engine.

This adapts the flow proven in the TTS Reader web app (``webapp/server.py``) to
Columbia-1's job manager: it builds a ``ttscore`` Config + Source and runs the
pipeline on a worker thread, bridging the pipeline's ``progress(done, total)``
callback to the live Job so the UI shows chunk-by-chunk progress. Model loading,
caching, assembly and encoding all stay in ``ttscore`` — this file only
translates web params into a pipeline call and back.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from ..jobs import Job, JobManager
from .base import Availability, Faculty

# Engines exposed in the UI. 'edge' (Microsoft online neural voices) needs no GPU
# and is the friendly default; 'chatterbox' is local/offline and can clone a
# voice but wants a GPU.
_ENGINES = ("edge", "chatterbox")


class SpeechFaculty(Faculty):
    id = "speech"
    name = "Speech"
    tagline = "Text, a link, or a document → a clean narration you can play or download."
    requires_package = "ttscore"

    # ── availability ─────────────────────────────────────────────────────────
    def probe(self) -> Availability:
        if not self._package_present():
            return Availability(
                False,
                "TTS Reader engine not found",
                detail="Expected the 'ttscore' package (set tts_repo in columbia.yaml).",
            )
        if self.cfg.mode == "api":
            # GPU-free path: only the online edge voices, which need edge-tts.
            if not self._module_present("edge_tts"):
                return Availability(False, "edge-tts package missing",
                                    detail="pip install edge-tts (see requirements-api.txt)")
            return Availability(True, "Ready", device="api")
        device = self._torch_device("cuda")
        # edge voices run online without a GPU, so the faculty is usable either
        # way; the device line just tells the user what Chatterbox would use.
        return Availability(True, "Ready", device=device)

    # ── job entry ────────────────────────────────────────────────────────────
    def start(self, manager: JobManager, params: dict,
              upload: Optional[Path] = None) -> Job:
        input_type = (params.get("input_type") or "text").strip().lower()
        engine = (params.get("engine") or "edge").strip().lower()
        if engine not in _ENGINES or self.cfg.mode == "api":
            engine = "edge"      # API mode: no local models, ever
        voice = (params.get("voice") or "en-US-AriaNeural").strip()
        try:
            speed = max(0.5, min(2.0, float(params.get("speed") or 1.0)))
        except (TypeError, ValueError):
            speed = 1.0

        label = self._label(input_type, params, upload)
        worker = self._make_worker(input_type, params, upload, engine, voice, speed)
        return manager.submit(self.id, label, worker)

    # ── worker ───────────────────────────────────────────────────────────────
    def _make_worker(self, input_type, params, upload, engine, voice, speed):
        cfg_obj = self.cfg

        def worker(job: Job) -> dict:
            # Imported here (worker thread), so a missing/broken engine surfaces
            # as a failed job rather than a server that won't boot.
            from ttscore.config import Config
            from ttscore.pipeline import Source, run

            out_path = cfg_obj.output_path / f"speech_{job.id}.mp3"
            src, title_fallback, cleanup_dir = _build_source(
                input_type, params, upload, out_path, job.id, cfg_obj)

            cfg = Config(
                output_dir=str(cfg_obj.output_path),
                cache_dir=str(cfg_obj.cache_path / "speech"),
                log_dir=str(cfg_obj.log_path),
                engine=engine,
                edge_voice=voice,
                speed=speed,
                keep_wav=False,          # web jobs: no bulky raw .wav per mp3
                write_transcript=False,
                preview_seconds=25.0,    # let long docs start playing early
            )

            job.set_stage("Reading & preparing text")

            def progress(done: int, total: int) -> None:
                job.set_stage("Synthesizing")
                job.progress(done, total)

            try:
                report = run([src], cfg, progress=progress)
            finally:
                if cleanup_dir is not None:
                    shutil.rmtree(cleanup_dir, ignore_errors=True)

            res = report.jobs[-1]
            if res.status == "failed":
                raise RuntimeError(res.error or "generation failed")

            return {
                "output": res.output,
                "_path": res.output,
                "title": res.title or title_fallback or "Narration",
                "seconds": round(res.audio_seconds, 1),
                "kind": "audio",
                "download": f"/api/library/file/{Path(res.output).stem}.mp3",
                "stream": f"/api/library/file/{Path(res.output).stem}.mp3",
            }

        return worker

    # ── labels ───────────────────────────────────────────────────────────────
    @staticmethod
    def _label(input_type: str, params: dict, upload: Optional[Path]) -> str:
        if input_type == "url":
            return (params.get("url") or "Web page")[:80]
        if input_type in ("pdf", "file") and upload is not None:
            return upload.name
        text = (params.get("text") or "").strip()
        return (" ".join(text.split()[:8])[:60] or "Pasted text")


def _build_source(input_type, params, upload, out_path, job_id, cfg_obj):
    """Return (Source, title_fallback, cleanup_dir). ``cleanup_dir`` is removed
    after the run (the per-job upload folder), or None for text/url."""
    from ttscore.pipeline import Source

    out = str(out_path)
    if input_type in ("pdf", "file"):
        if upload is None:
            raise RuntimeError("No file uploaded.")
        low = upload.name.lower()
        if not low.endswith((".pdf", ".epub", ".txt", ".md")):
            raise RuntimeError("Upload a .pdf, .epub, .txt or .md file.")
        if low.endswith(".epub"):
            kind = "epub"
        elif low.endswith(".pdf"):
            kind = "pdf"
        else:
            kind = "file"
        return Source(kind, str(upload), out), upload.stem, upload.parent

    if input_type == "url":
        url = (params.get("url") or "").strip()
        if not url:
            raise RuntimeError("No URL provided.")
        is_pdf = url.lower().split("?", 1)[0].rstrip("/").endswith(".pdf")
        return Source("pdf" if is_pdf else "url", url, out), url, None

    text = (params.get("text") or "").strip()
    if not text:
        raise RuntimeError("No text provided.")
    return Source("text", text, out), "Pasted text", None
