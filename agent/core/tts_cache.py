"""Pre-rendered TTS audio cache for repeat-prone responses.

The Hamsa TTS provider has ~900–1100 ms time-to-first-byte on every
call. For responses that don't change between calls (the menu, the
post-completion ack, the greeter opening) that latency is pure waste:
the audio bytes are identical every single call.

This module caches the *rendered audio bytes* keyed by the exact text
+ TTS model. The first time a phrase is requested the synthesis runs
normally; every later request answers from the cache in microseconds.

Design choices:

- **Process-local first**, optional disk fallback. Disk write is
  best-effort so a read-only deployment still works.
- **Keyed by (model, text)** — same string with a different TTS model
  gets re-rendered once and cached separately.
- **No magic eviction.** The set of cacheable phrases is small and
  stable (menu / post-completion / opening); we keep them all.

Usage from a TTS provider wrapper::

    cached = tts_cache.get(model="hamsa", text=line)
    if cached is not None:
        emit(cached)
    else:
        audio = synthesize(line)
        tts_cache.put(model="hamsa", text=line, audio=audio)
        emit(audio)
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("restaurant.tts_cache")


@dataclass
class TTSEntry:
    audio: bytes
    sample_rate: int
    text: str = ""
    model: str = ""


@dataclass
class TTSCache:
    entries: dict[str, TTSEntry] = field(default_factory=dict)
    cacheable_texts: set[str] = field(default_factory=set)
    disk_dir: Path | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "TTSCache":
        enabled = os.getenv("TTS_CACHE_ENABLED", "1") != "0"
        disk_raw = os.getenv("TTS_CACHE_DIR", "").strip()
        disk_dir: Path | None = None
        if disk_raw:
            disk_dir = Path(disk_raw)
        return cls(disk_dir=disk_dir, enabled=enabled)

    def register_cacheable(self, text: str) -> None:
        """Mark a literal reply as safe to cache.

        Caching is opt-in per text — replies that include call-specific
        data (the customer's name, their order list, the live total,
        etc.) must never be cached because the next call would hear the
        previous customer's data. The flow code calls this on startup
        with the static replies it knows are safe.
        """
        if text:
            with self.lock:
                self.cacheable_texts.add(text.strip())

    def is_cacheable(self, text: str) -> bool:
        if not text:
            return False
        with self.lock:
            return text.strip() in self.cacheable_texts

    def _key(self, model: str, text: str) -> str:
        h = hashlib.sha256(f"{model}\n{text}".encode("utf-8")).hexdigest()[:24]
        return f"{model}-{h}"

    def get(self, *, model: str, text: str) -> TTSEntry | None:
        if not self.enabled or not text or not self.is_cacheable(text):
            return None
        key = self._key(model, text)
        with self.lock:
            entry = self.entries.get(key)
        if entry is not None:
            return entry
        return self._read_from_disk(key)

    def put(
        self,
        *,
        model: str,
        text: str,
        audio: bytes,
        sample_rate: int = 16000,
    ) -> None:
        if not self.enabled or not text or not audio:
            return
        if not self.is_cacheable(text):
            return
        key = self._key(model, text)
        entry = TTSEntry(audio=audio, sample_rate=sample_rate, text=text, model=model)
        with self.lock:
            self.entries[key] = entry
        self._write_to_disk(key, entry)

    def _read_from_disk(self, key: str) -> TTSEntry | None:
        if self.disk_dir is None:
            return None
        path = self.disk_dir / f"{key}.pcm"
        if not path.exists():
            return None
        try:
            audio = path.read_bytes()
        except OSError:
            return None
        meta_path = self.disk_dir / f"{key}.meta"
        sample_rate = 16000
        if meta_path.exists():
            try:
                line = meta_path.read_text(encoding="utf-8").strip()
                if line.isdigit():
                    sample_rate = int(line)
            except OSError:
                pass
        entry = TTSEntry(audio=audio, sample_rate=sample_rate)
        with self.lock:
            self.entries[key] = entry
        return entry

    def _write_to_disk(self, key: str, entry: TTSEntry) -> None:
        if self.disk_dir is None:
            return
        try:
            self.disk_dir.mkdir(parents=True, exist_ok=True)
            (self.disk_dir / f"{key}.pcm").write_bytes(entry.audio)
            (self.disk_dir / f"{key}.meta").write_text(
                str(entry.sample_rate), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("tts cache | disk write failed | key=%s | err=%s", key, exc)

    def stats(self) -> dict[str, int]:
        with self.lock:
            return {"entries": len(self.entries), "enabled": int(self.enabled)}

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()


# Module-level singleton — flow code calls ``GLOBAL_CACHE.get`` /
# ``GLOBAL_CACHE.put`` directly. Tests can swap by reassigning.
GLOBAL_CACHE = TTSCache.from_env()


# Identifies replies that are safe to cache — text that never includes
# call-specific data (no name, no phone, no order). The flow code
# decides whether a given line is "common" by matching against this
# tag set; the cache itself doesn't care.
CACHEABLE_TAGS: frozenset[str] = frozenset(
    {
        "menu",
        "menu_unavailable",
        "delivery_zones",
        "delivery_unavailable",
        "post_completion_thanks",
        "post_completion_ack",
        "post_completion_generic",
        "noise_reprompt",
    }
)


__all__ = [
    "CACHEABLE_TAGS",
    "GLOBAL_CACHE",
    "TTSCache",
    "TTSEntry",
]
