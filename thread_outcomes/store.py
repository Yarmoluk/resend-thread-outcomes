"""Raw event storage with per-event content hashes.

Every webhook event the graph is built from is kept as its own JSON file with
the SHA-256 of the exact payload bytes. The graph only ever cites those files,
so each node can be traced back to the bytes that produced it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Deterministic serialization used for hashing pulled events.

    A pushed event is hashed over the raw request body; a pulled event arrives
    already parsed, so it is hashed over this canonical form instead. The
    ``verify`` command re-derives the same bytes from the API response.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class StoredEvent:
    id: str
    source_url: str
    source_hash: str
    received_via: str  # "pull" | "push" | "fixture"
    payload: dict[str, Any]

    @property
    def type(self) -> str:
        return self.payload["type"]

    @property
    def created_at(self) -> str:
        return self.payload["created_at"]

    @property
    def data(self) -> dict[str, Any]:
        return self.payload["data"]


class EventStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, event_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in event_id)
        return self.root / f"{safe}.json"

    def has(self, event_id: str) -> bool:
        return self._path(event_id).exists()

    def put(
        self,
        event_id: str,
        payload: dict[str, Any],
        *,
        source_url: str,
        received_via: str,
        raw: bytes | None = None,
    ) -> StoredEvent:
        """Store an event once. A second put with the same id is a no-op."""
        if self.has(event_id):
            return self.get(event_id)
        content = raw if raw is not None else canonical_bytes(payload)
        event = StoredEvent(
            id=event_id,
            source_url=source_url,
            source_hash=sha256(content),
            received_via=received_via,
            payload=payload,
        )
        record = {
            "id": event.id,
            "source_url": event.source_url,
            "source_hash": event.source_hash,
            "received_via": event.received_via,
            "payload": payload,
        }
        self._path(event_id).write_text(json.dumps(record, indent=2, sort_keys=True))
        return event

    def get(self, event_id: str) -> StoredEvent:
        record = json.loads(self._path(event_id).read_text())
        return StoredEvent(**record)

    def __iter__(self) -> Iterator[StoredEvent]:
        for path in sorted(self.root.glob("*.json")):
            record = json.loads(path.read_text())
            yield StoredEvent(**record)

    def __len__(self) -> int:
        return sum(1 for _ in self.root.glob("*.json"))
