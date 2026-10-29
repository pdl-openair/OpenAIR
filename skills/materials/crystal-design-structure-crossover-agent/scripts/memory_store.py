# scripts/memory_store.py
"""Natural-language memory store for the Materials Design Agent.

Memory entries are JSON-serializable, human-auditable records of
(snapshot context, steer action, outcome Δ, confidence). They are
the unit the agent retrieves between generations.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, replace
from typing import Any

MEMORY_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    entry_id: str
    run_id: str
    family: str
    gen: int
    context: str
    action: str
    outcome: dict[str, float]
    confidence: float
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema_version"] = MEMORY_SCHEMA_VERSION
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryEntry":
        # Drop schema_version (we forward-migrate in load() if needed)
        d.pop("schema_version", None)
        return cls(**d)


from pathlib import Path


_WORD_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def score_context(query: str, stored: str) -> float:
    """Jaccard similarity over lowercased word tokens.

    Chosen over TF-IDF because memory contexts are short sentences
    (~10-30 tokens); Jaccard is auditable and dependency-free.
    """
    q, s = _tokenize(query), _tokenize(stored)
    if not q or not s:
        return 0.0
    return len(q & s) / len(q | s)


class MemoryStore:
    """JSON-backed memory store. Single-process; not thread-safe.

    The store is a flat JSON array of MemoryEntry dicts. We trade
    write performance for human readability (a Nature审稿人 should be
    able to open the file and read every entry).
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cache: list[MemoryEntry] | None = None

    def load(self) -> list[MemoryEntry]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = []
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        # Forward migration hook (schema_version check)
        self._cache = [MemoryEntry.from_dict(d) for d in raw]
        return self._cache

    def save(self, entries: list[MemoryEntry]) -> None:
        self._cache = list(entries)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([e.to_dict() for e in entries], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append(self, entry: MemoryEntry) -> None:
        entries = self.load()
        entries.append(entry)
        self.save(entries)

    def __len__(self) -> int:
        return len(self.load())

    def query(
        self,
        context: str,
        family: str | None = None,
        top_k: int = 5,
        min_confidence: float = 0.0,
    ) -> list[MemoryEntry]:
        entries = self.load()
        scored: list[tuple[float, MemoryEntry]] = []
        for e in entries:
            if family is not None and e.family != family:
                continue
            if e.confidence < min_confidence:
                continue
            s = score_context(context, e.context)
            # Combined score: lexical similarity * confidence
            combined = s * e.confidence if e.confidence > 0 else s
            scored.append((combined, e))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def decay(self, factor: float = 0.95) -> None:
        """Periodic decay. Call once per generation (or per run)."""
        entries = self.load()
        updated = []
        for e in entries:
            new_conf = max(0.0, e.confidence * factor)
            updated.append(replace(e, confidence=new_conf))
        self.save(updated)

    def boost(self, entry_id: str, delta: float = 0.05) -> None:
        """Adjust one entry's confidence by `delta` (clamped to [0, 1])."""
        self.apply_outcome(entry_id, improved=delta >= 0, delta=abs(delta))

    def apply_outcome(self, entry_id: str, improved: bool, delta: float = 0.05) -> None:
        """Outcome-driven confidence update (ExpeL-style upvote/downvote).

        Confidence must reflect whether the recorded action actually helped
        (delta_sun up / delta_ehull down), NOT how often the entry was
        retrieved — retrieval-frequency boosts would upvote failures.
        """
        entries = self.load()
        signed = delta if improved else -delta
        updated = []
        for e in entries:
            if e.entry_id == entry_id:
                new_conf = min(1.0, max(0.0, e.confidence + signed))
                updated.append(replace(e, confidence=new_conf))
            else:
                updated.append(e)
        self.save(updated)
