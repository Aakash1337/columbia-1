# Columbia-1

**One local web app over every voice engine you build.** Columbia-1 is a
unified front end for a growing set of speech/audio tools — today the
[TTS Reader](https://github.com/Aakash1337/TTS) and
[AI Dubbing](https://github.com/Aakash1337/AI-Dubbing) pipelines — behind a
single UI, a single library of outputs, and one place to run everything.

> Workshop name after *Columbia*, the machine intelligence in Christopher
> Ruocchio's *Sun Eater*. Each tool is a **faculty** of one system, not a
> separate app.

It is a thin orchestration layer: it does **not** reimplement synthesis or
dubbing. It discovers the sibling engine packages, drives their existing
pipelines through a shared job manager, and serves one web page over both.

---

## What it gives you

| Faculty | What it does | Status before Columbia-1 |
|---|---|---|
| **Speech** | Text / a web link / a PDF·EPUB → a clean narration MP3 you can play or download | had its own web app |
| **Dubbing** | A foreign-language video + its `.srt` → an English voiceover muxed back in sync | **CLI only — this is its first UI** |
| **Library** | Every narration and dubbed video in one list: play, download, delete | — |

New engines are additive: implement one small `Faculty` class and it appears as
another tab. Nothing else changes.

---

## How it fits together

```
        ┌──────────────── Columbia-1 (this repo) ────────────────┐
        │  FastAPI server  +  one web UI  +  JobManager          │
        │      │                                    │            │
        │  faculties/speech.py                faculties/dubbing.py│
        └──────┼─────────────────────────────────────┼───────────┘
               │ import ttscore                       │ import dubbing
        ┌──────▼──────┐                        ┌──────▼──────────┐
        │  ../TTS     │                        │  ../AI-Dubbing  │
        │  (ttscore)  │                        │   (dubbing)     │
        └─────────────┘                        └─────────────────┘
```

Columbia-1 finds the two engine repos on disk (by default as sibling folders),
puts them on `sys.path`, and calls their pipelines directly. If an engine isn't
present, its faculty simply reports **offline** in the UI and the server still
runs — so the app is inspectable anywhere.

`JobManager` runs one engine job at a time (both load Chatterbox; two at once
would fight over VRAM). It is deliberately the whole backend abstraction: today
an in-process worker, tomorrow the seam where a remote GPU queue slots in
without touching the routes above it.

---

## Setup

Columbia-1 needs to `import ttscore` and `import dubbing`, so the simplest setup
is to **run it in a Python environment that already has the engines installed** —
e.g. reuse the TTS repo's `.venv`, which carries torch / chatterbox / edge-tts.

```powershell
# 1. Clone all three side by side:
#      E:\TTS            E:\AI-Dubbing            E:\columbia-1
# 2. Install Columbia-1's own (light) deps into the engine venv:
E:\TTS\.venv\Scripts\python.exe -m pip install -r E:\columbia-1\requirements.txt
```

Columbia-1's own requirements are just the web stack (FastAPI, uvicorn); the
heavy dependencies live in — and are installed by — the two engine repos.

## Run

```powershell
# Windows: double-click "Start Columbia-1.bat"  (prefers a local .venv, else ..\TTS\.venv)
# or explicitly:
E:\TTS\.venv\Scripts\python.exe serve.py
```

```bash
python serve.py                 # auto-pick a free port, open the browser
python serve.py --port 8000     # fixed port
python serve.py --no-browser    # headless
python serve.py --host 0.0.0.0  # expose on the LAN
```

On start it prints where each engine resolved to:

```
Columbia-1 0.1.0
  TTS engine     : E:\TTS
  Dubbing engine : E:\AI-Dubbing
  Serving at     : http://127.0.0.1:53017
```

---

## Configuration

Everything is optional. Copy `columbia.example.yaml` to `columbia.yaml` to
override. Resolution order: **defaults → `columbia.yaml` → `COLUMBIA_*` env vars.**

| Knob | Default | Meaning |
|---|---|---|
| `host` / `port` | `127.0.0.1` / `0` | bind address; `0` = free port |
| `tts_repo` | auto | path to the TTS checkout (`ttscore` package). `null` → find a sibling |
| `dubbing_repo` | auto | path to the AI-Dubbing checkout (`dubbing` package). `null` → find a sibling |
| `output_dir` | `output` | unified library: `.mp3` narrations + `.mp4` dubs |
| `cache_dir` | `.columbia_cache` | engines' per-chunk / per-cue caches (crash-resume) |

Env overrides: `COLUMBIA_HOST`, `COLUMBIA_PORT`, `COLUMBIA_TTS_REPO`,
`COLUMBIA_DUBBING_REPO`, `COLUMBIA_OUTPUT_DIR`.

---

## Project layout

```
serve.py                    launcher (free port, open browser, run uvicorn)
requirements.txt            Columbia-1's own light web deps
columbia.example.yaml       annotated config
Start Columbia-1.bat        Windows launcher (reuses ..\TTS\.venv if present)
columbia/
  config.py                 config + engine-repo discovery / sys.path wiring
  jobs.py                   JobManager: one-at-a-time worker (remote-swappable)
  server.py                 FastAPI app: faculties, generate, jobs, library
  faculties/
    base.py                 Faculty interface + availability probe
    speech.py               adapts ttscore.pipeline (text/url/doc → mp3)
    dubbing.py              drives dubbing.pipeline (video+srt → mp4) — new web flow
  static/
    index.html  styles.css  app.js     the single-page UI
```

---

## Notes

- **Local now, remote later.** The design keeps job execution behind
  `JobManager` so a hosted deployment (queue + separate GPU worker) can replace
  it later without changing the faculties or the UI.
- **GPU.** Both engines auto-fall back to CPU (slow) when no CUDA GPU is present;
  the Speech faculty's `edge` engine needs neither a GPU nor local models.
- **This repo stays thin on purpose.** Fixes to synthesis or dubbing belong in
  the engine repos; they show up here automatically.
