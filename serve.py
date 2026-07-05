#!/usr/bin/env python
"""Launch the Columbia-1 web app.

Picks a free port (unless one is pinned), opens the browser, and serves the
unified UI over both engine faculties. Mirrors the TTS Reader launcher so it
feels the same to run.

    python serve.py                 # auto port, open browser
    python serve.py --port 8000     # fixed port
    python serve.py --no-browser    # don't open a browser (e.g. headless)
    python serve.py --host 0.0.0.0  # expose on the LAN

Engine locations are auto-discovered as sibling folders (../TTS, ../AI-Dubbing)
or set explicitly in columbia.yaml / the COLUMBIA_* environment variables.
"""

from __future__ import annotations

import argparse
import socket
import threading
import webbrowser

import uvicorn

from columbia import __version__
from columbia.config import ColumbiaConfig
from columbia.server import create_app


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def main() -> None:
    cfg = ColumbiaConfig.load()

    ap = argparse.ArgumentParser(description="Columbia-1 web app")
    ap.add_argument("--host", default=cfg.host)
    ap.add_argument("--port", type=int, default=cfg.port)
    ap.add_argument("--no-browser", action="store_true",
                    help="don't open a browser window")
    args = ap.parse_args()

    cfg.host = args.host
    port = args.port or _free_port(args.host)
    open_browser = cfg.open_browser and not args.no_browser

    app = create_app(cfg)

    # Report what each engine resolved to, so a misconfigured path is obvious.
    print(f"Columbia-1 {__version__}")
    print(f"  TTS engine     : {cfg.tts_repo_path or 'NOT FOUND (Speech offline)'}")
    print(f"  Dubbing engine : {cfg.dubbing_repo_path or 'NOT FOUND (Dubbing offline)'}")
    url = f"http://{args.host}:{port}"
    print(f"  Serving at     : {url}")

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
