"""Columbia-1 FastAPI app: one UI and one API over both engine faculties.

Routes fall in four groups:

* ``GET  /``                     the single-page UI
* ``GET  /api/faculties``        per-faculty availability (drives the status dots)
* ``POST /api/{faculty}/generate`` submit a job (multipart: params + any uploads)
* ``GET  /api/job/{id}`` + cancel   poll / stop a running job
* ``/api/library`` …             list, stream, download and delete outputs

The app boots even when neither engine is importable — every faculty just
reports itself offline and its Generate button is disabled in the UI. That makes
the whole thing inspectable on any machine and is the seam a remote worker slots
into later (swap :class:`~columbia.jobs.JobManager`; routes are unchanged).
"""

from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import ColumbiaConfig
from .faculties import build_faculties
from .jobs import JobManager

STATIC = Path(__file__).resolve().parent / "static"

# Library filenames we will serve/delete. Locks out path traversal: a plain
# name, no separators, one of the known media/sidecar extensions.
_NAME_RE = re.compile(r"^[\w.\- ]{1,140}$")
_MEDIA_EXTS = {".mp3", ".mp4", ".m4a", ".wav"}
_SIDECAR_EXTS = (".meta.json", ".words.json", ".txt", ".wav", ".preview.mp3")
_MEDIA_TYPES = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav",
    ".mp4": "video/mp4",
}


def create_app(cfg: Optional[ColumbiaConfig] = None) -> FastAPI:
    cfg = cfg or ColumbiaConfig.load()
    cfg.register_engine_paths()
    cfg.ensure_dirs()

    app = FastAPI(title="Columbia-1")
    manager = JobManager()
    faculties = build_faculties(cfg)
    app.state.cfg = cfg
    app.state.manager = manager
    app.state.faculties = faculties

    # ── UI ────────────────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/faculties")
    def list_faculties() -> JSONResponse:
        return JSONResponse({
            "version": __import__("columbia").__version__,
            "faculties": [f.describe() for f in faculties.values()],
        })

    # ── Speech ────────────────────────────────────────────────────────────────
    @app.post("/api/speech/generate")
    async def speech_generate(
        input_type: str = Form("text"),
        text: str = Form(""),
        url: str = Form(""),
        engine: str = Form("edge"),
        voice: str = Form("en-US-AriaNeural"),
        speed: float = Form(1.0),
        file: Optional[UploadFile] = File(None),
    ) -> JSONResponse:
        fac = _require(faculties, "speech")
        upload = None
        if input_type in ("pdf", "file") and file is not None and (file.filename or "").strip():
            upload = _save_upload(cfg, file)
        params = {"input_type": input_type, "text": text, "url": url,
                  "engine": engine, "voice": voice, "speed": speed}
        job = fac.start(manager, params, upload)
        return JSONResponse({"job_id": job.id})

    # ── Dubbing ───────────────────────────────────────────────────────────────
    @app.post("/api/dubbing/generate")
    async def dubbing_generate(
        video: UploadFile = File(...),
        srt: UploadFile = File(...),
        reference: Optional[UploadFile] = File(None),
        translate: str = Form("false"),
        preview: str = Form("false"),
        max_atempo: float = Form(1.5),
    ) -> JSONResponse:
        fac = _require(faculties, "dubbing")
        if not (video.filename or "").strip():
            raise HTTPException(400, "No video uploaded.")
        if not (srt.filename or "").strip():
            raise HTTPException(400, "No subtitle (.srt) uploaded.")
        if not srt.filename.lower().endswith(".srt"):
            raise HTTPException(400, "Subtitle must be a .srt file.")
        job_dir = cfg.upload_path / uuid.uuid4().hex[:12]
        job_dir.mkdir(parents=True, exist_ok=True)
        vpath = _save_upload(cfg, video, job_dir)
        spath = _save_upload(cfg, srt, job_dir)
        rpath = None
        if reference is not None and (reference.filename or "").strip():
            rpath = _save_upload(cfg, reference, job_dir)
        params = {"translate": _as_bool(translate), "preview": _as_bool(preview),
                  "max_atempo": max_atempo}
        job = fac.start(manager, params, vpath, spath, rpath)
        return JSONResponse({"job_id": job.id})

    # ── Jobs ──────────────────────────────────────────────────────────────────
    @app.get("/api/job/{job_id}")
    def job_status(job_id: str) -> JSONResponse:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return JSONResponse(job.public())

    @app.post("/api/job/{job_id}/cancel")
    def job_cancel(job_id: str) -> JSONResponse:
        if not manager.cancel(job_id):
            raise HTTPException(404, "unknown job")
        return JSONResponse({"ok": True})

    # ── Library ───────────────────────────────────────────────────────────────
    @app.get("/api/library")
    def library() -> JSONResponse:
        items = []
        files = sorted(
            [p for p in cfg.output_path.glob("*") if p.suffix.lower() in {".mp3", ".mp4"}],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )[:300]
        for media in files:
            meta = _read_meta(media)
            st = media.stat()
            kind = meta.get("kind") or ("video" if media.suffix.lower() == ".mp4" else "audio")
            items.append({
                "name": media.name,
                "stem": media.stem,
                "title": meta.get("title") or media.stem,
                "kind": kind,
                "seconds": meta.get("audio_seconds"),
                "created": meta.get("created")
                           or datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "size": st.st_size,
                "url": f"/api/library/file/{media.name}",
            })
        return JSONResponse({"items": items})

    @app.get("/api/library/file/{name}")
    def library_file(name: str):
        path = _safe_media(cfg, name)
        return FileResponse(str(path), media_type=_MEDIA_TYPES.get(path.suffix.lower(),
                                                                   "application/octet-stream"))

    @app.delete("/api/library/{name}")
    def library_delete(name: str) -> JSONResponse:
        path = _safe_media(cfg, name)          # validates name + existence
        stem = path.stem
        for ext in (path.suffix, *_SIDECAR_EXTS):
            try:
                (cfg.output_path / f"{stem}{ext}").unlink(missing_ok=True)
            except OSError:
                pass
        return JSONResponse({"ok": True})

    # Static assets (css/js/favicon) under /static.
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app


# ── helpers ───────────────────────────────────────────────────────────────────
def _require(faculties, fid):
    fac = faculties.get(fid)
    if fac is None:
        raise HTTPException(404, f"unknown faculty {fid!r}")
    av = fac.probe()
    if not av.available:
        raise HTTPException(503, av.reason)
    return fac


def _save_upload(cfg: ColumbiaConfig, file: UploadFile, into: Optional[Path] = None) -> Path:
    into = into or (cfg.upload_path / uuid.uuid4().hex[:12])
    into.mkdir(parents=True, exist_ok=True)
    dest = into / _safe_name(file.filename or "upload")
    with open(dest, "wb") as fh:
        shutil.copyfileobj(file.file, fh)
    return dest


def _safe_media(cfg: ColumbiaConfig, name: str) -> Path:
    if not _NAME_RE.match(name or "") or Path(name).suffix.lower() not in _MEDIA_EXTS:
        raise HTTPException(400, "bad name")
    path = (cfg.output_path / name)
    if path.resolve().parent != cfg.output_path.resolve():   # no traversal, ever
        raise HTTPException(400, "bad name")
    if not path.is_file():
        raise HTTPException(404, "not found")
    return path


def _read_meta(media: Path) -> dict:
    import json
    mpath = media.with_suffix(".meta.json")
    if mpath.is_file():
        try:
            return json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def _safe_name(name: str) -> str:
    cleaned = "".join(c if (c.isalnum() or c in "-_. ") else "_" for c in (name or ""))
    cleaned = cleaned.strip() or "upload"
    return cleaned[:80]


def _as_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "on", "yes")
