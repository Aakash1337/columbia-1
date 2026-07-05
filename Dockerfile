# ─────────────────────────────────────────────────────────────────────────────
# Columbia-1 — hosted API mode.
#
# Runs the same unified app as the local install, but GPU-free: both faculties
# speak through Microsoft's online neural voices (edge-tts) instead of local
# Chatterbox. Works on any container host — Cloudflare Containers, Railway,
# Fly.io, Render, a VPS.
#
#   docker build -t columbia-1 .
#   docker run -p 8080:8080 -e COLUMBIA_ACCESS_CODE=your-secret \
#              -e GEMINI_API_KEY=your-key columbia-1
#
# GEMINI_API_KEY (optional) enables Gemma translation + text polish. Always
# pass it at RUN time like above — never as a build arg or baked-in ENV.
#
# The two engine repos are cloned at build time (only their light, CPU-side
# code paths are exercised). For private repos pass a token:
#   docker build --build-arg GIT_TOKEN=ghp_... -t columbia-1 .
# To pin different forks/branches, override TTS_REPO_URL / DUBBING_REPO_URL.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# ffmpeg: mp3 encode (speech) + probe/mux (dubbing). git: engine clone.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Engine repos — sibling layout mirrors the local install, so Columbia-1's
# auto-discovery finds them without any configuration.
ARG TTS_REPO_URL=https://github.com/Aakash1337/TTS.git
ARG DUBBING_REPO_URL=https://github.com/Aakash1337/AI-Dubbing.git
ARG GIT_TOKEN=
RUN set -e; \
    auth() { if [ -n "$GIT_TOKEN" ]; then echo "$1" | sed "s#https://#https://x-access-token:${GIT_TOKEN}@#"; else echo "$1"; fi; }; \
    git clone --depth 1 "$(auth $TTS_REPO_URL)" /engines/TTS; \
    git clone --depth 1 "$(auth $DUBBING_REPO_URL)" /engines/AI-Dubbing; \
    rm -rf /engines/*/.git

# Python deps: web stack + the light API-mode engine subset (no torch).
COPY requirements.txt requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-api.txt

# The app itself.
COPY . .

ENV COLUMBIA_MODE=api \
    COLUMBIA_HOST=0.0.0.0 \
    COLUMBIA_TTS_REPO=/engines/TTS \
    COLUMBIA_DUBBING_REPO=/engines/AI-Dubbing \
    COLUMBIA_OUTPUT_DIR=/data/output

EXPOSE 8080
# Shell form so $PORT (set by Railway/Render/Fly) is honored; default 8080.
CMD python serve.py --no-browser --port ${PORT:-8080}
