"""Gemma over the Gemini API — the LLM the hosted app thinks with.

Two jobs, both optional and both off unless a key is configured:

* **translate** — subtitle cues for the Dubbing faculty (Korean lecture line in,
  natural spoken-English voiceover line out).
* **polish** — the Speech faculty's text cleanup pass, the hosted counterpart
  of the local app's Ollama/Gemma step (rules always run first; this smooths
  what rules can't).

Gemma models on this API reason in plain text before answering and don't
support ``thinkingConfig``, so both prompts use a marker protocol: the model
may think out loud, then must emit ``FINAL:`` (one line) or ``===BEGIN===``
(rest of output) — we parse from the marker and throw the musings away. Any
failure (no key, HTTP error, missing marker) falls back to the input text:
an unpolished narration or untranslated cue is always better than a dead job.

Plain REST via ``requests`` — no SDK dependency.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

log = logging.getLogger("columbia.llm")

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Kept in sync with what the Gemini API actually serves (checked 2026-07).
DEFAULT_MODEL = "gemma-4-31b-it"

# Gemma 4 reasons in plain text before (and around) its answer, and follows
# ad-hoc markers unreliably — it will even assert "Marker: checked" without
# emitting one. XML-style tags + a worked example in the prompt are what it
# actually honors. Line-anchored FINAL: stays for the short translate case
# (where it has proven reliable); tags carry the long polish case. We always
# take the LAST match, since the model may quote the format while thinking.
_FINAL_RE = re.compile(r"^\s*FINAL:\s*(.+?)\s*$", re.MULTILINE)
_EDITED_RE = re.compile(r"<edited>(.*?)</edited>", re.DOTALL)

# Soft cap per polish request; paragraphs are batched up to this size so a long
# article becomes a handful of calls instead of one oversized one.
_POLISH_BATCH_CHARS = 3000


class GemmaClient:
    """Minimal generateContent client with retries and marker parsing."""

    def __init__(self, api_key: Optional[str], model: Optional[str] = None,
                 timeout: float = 90.0) -> None:
        self.api_key = (api_key or "").strip() or None
        self.model = (model or "").strip() or DEFAULT_MODEL
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return self.api_key is not None

    # ── raw call ─────────────────────────────────────────────────────────────
    def _generate(self, prompt: str, max_tokens: int = 1024,
                  temperature: float = 0.2) -> str:
        """One generateContent call; returns the model text. Raises on failure."""
        import requests

        url = _ENDPOINT.format(model=self.model)
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature,
                                 "maxOutputTokens": max_tokens},
        }
        last: Exception = RuntimeError("no attempt made")
        for attempt in (1, 2):
            try:
                r = requests.post(url, params={"key": self.api_key}, json=body,
                                  timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                parts = data["candidates"][0]["content"]["parts"]
                # Gemma 4 replies in multiple parts: reasoning parts are
                # flagged "thought": true and the real answer is the rest.
                # Only if EVERY part is thought (it happens) fall back to all.
                answer = "".join(p.get("text", "") for p in parts
                                 if not p.get("thought"))
                if not answer.strip():
                    answer = "".join(p.get("text", "") for p in parts)
                return answer
            except Exception as exc:  # noqa: BLE001 — network/shape errors alike
                last = exc
                log.warning("Gemma call failed (attempt %d): %s", attempt, exc)
                time.sleep(1.5 * attempt)
        raise last

    # ── translation (dubbing cues) ───────────────────────────────────────────
    def translate(self, text: str, target_language: str = "en",
                  source_language: Optional[str] = None) -> str:
        """Translate one subtitle cue. Falls back to the original on failure."""
        text = (text or "").strip()
        if not text or not self.available:
            return text
        src = f" from {source_language}" if source_language else ""
        prompt = (
            f"Translate the subtitle line below{src} into natural spoken "
            f"{_lang_name(target_language)} for a voiceover. Keep it about as "
            "long as the original so it fits the same screen time. Think if "
            "you need to, then give your answer as a single line starting "
            "with the marker FINAL: followed by only the translation.\n\n"
            f"Subtitle: {text}"
        )
        # Gemma thinks in plain text before answering; a tight token cap can
        # truncate the musings before the FINAL: line ever appears. Give it
        # room, and retry once if the marker still didn't materialize.
        for attempt in (1, 2):
            try:
                out = self._generate(prompt, max_tokens=8192, temperature=0.1)
            except Exception:
                return text
            hits = _FINAL_RE.findall(out)
            if hits:
                return _clean_line(hits[-1])
            log.warning("translate: no FINAL marker (attempt %d)", attempt)
        return text

    # ── polish (speech text cleanup) ─────────────────────────────────────────
    def polish(self, text: str, progress=None) -> str:
        """Smooth rule-cleaned text for narration, paragraph batch by batch.

        ``progress(done, total)`` is called per batch so long documents show
        movement in the UI. Any batch that fails keeps its original text."""
        text = (text or "").strip()
        if not text or not self.available:
            return text
        batches = _batch_paragraphs(text, _POLISH_BATCH_CHARS)
        out: list[str] = []
        for i, batch in enumerate(batches):
            if progress:
                progress(i, len(batches))
            out.append(self._polish_batch(batch))
        if progress:
            progress(len(batches), len(batches))
        return "\n\n".join(out)

    def _polish_batch(self, batch: str) -> str:
        prompt = (
            "You are preparing text to be read aloud by a text-to-speech "
            "narrator. Lightly edit the text so it flows naturally when "
            "spoken: fix garbled words and broken sentences, drop leftover "
            "citations, footnote markers and web cruft, and spell out symbols "
            "that read badly. Do NOT summarize, reorder, or drop content — "
            "keep every sentence's meaning and roughly its length. Keep "
            "paragraph breaks. Put the edited text inside <edited></edited> "
            "tags, like this example:\n\n"
            "Input: The mission was a suc-\ncess [3], with 90% of tar- gets met.\n"
            "<edited>The mission was a success, with ninety percent of targets "
            "met.</edited>\n\n"
            f"Input: {batch}\n"
        )
        for attempt in (1, 2):
            try:
                out = self._generate(prompt, max_tokens=8192, temperature=0.2)
            except Exception:
                return batch
            hits = _EDITED_RE.findall(out)
            if hits:
                cleaned = hits[-1].strip()
                # Sanity bounds: an edit that loses most of the text, or that
                # balloons past any plausible symbol spell-out, is leaked
                # reasoning or a misfire — keep the rules-only text instead.
                if 0.5 * len(batch) <= len(cleaned) <= 3.0 * len(batch):
                    return cleaned
            log.warning("polish: no usable <edited> block (attempt %d)", attempt)
        return batch


# ── helpers ───────────────────────────────────────────────────────────────────
def _clean_line(line: str) -> str:
    line = line.strip()
    if len(line) >= 2 and line[0] in "\"'“‘" and line[-1] in "\"'”’":
        line = line[1:-1].strip()
    return line


def _batch_paragraphs(text: str, cap: int) -> list[str]:
    """Group paragraphs into batches of at most ~cap chars (a single paragraph
    longer than cap stays whole — never split mid-paragraph)."""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    batches: list[str] = []
    cur: list[str] = []
    size = 0
    for p in paras:
        if cur and size + len(p) > cap:
            batches.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(p)
        size += len(p)
    if cur:
        batches.append("\n\n".join(cur))
    return batches or [text]


_LANG_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German",
               "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "hi": "Hindi",
               "pt": "Portuguese", "it": "Italian", "ru": "Russian", "ar": "Arabic"}


def _lang_name(code: str) -> str:
    return _LANG_NAMES.get((code or "en").strip().lower(), code or "English")
