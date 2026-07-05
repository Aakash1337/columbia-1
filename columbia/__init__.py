"""Columbia-1 — a unified local web app over the TTS and AI-Dubbing engines.

Columbia-1 is a thin orchestration layer. It does not reimplement speech
synthesis or dubbing; it discovers the sibling ``ttscore`` and ``dubbing``
packages, drives their pipelines through a shared job manager, and serves one
web UI over both. Each engine is a *faculty*: a self-describing unit that
reports whether it can run and knows how to execute a single job.

Nothing here imports torch/chatterbox at module load — the server boots (and the
UI loads) even on a machine without the models installed, with each faculty
simply reporting itself offline. That keeps the app inspectable anywhere and is
the seam a remote GPU backend would later plug into.
"""

__version__ = "0.1.0"
