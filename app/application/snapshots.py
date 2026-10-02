"""Browse snapshots: one scan, many pages.

Every read of a queue is a broker operation (basic.get + requeue) — on a quorum queue it
counts as a delivery for every message read. So a deep browse scans once, keeps what it
saw for a few minutes, and serves pages, search and filters from that copy."""

import json
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.models import MessageRecord

SNAPSHOT_TTL = timedelta(minutes=5)
# ponytail: in process, LRU — per replica like the rest of QueueLens's state; each holds at
# most one scan's byte budget (message_browser.SCAN_BYTES_BUDGET)
MAX_SNAPSHOTS = 4


@dataclass
class Snapshot:
    id: str
    scope: tuple[str, str]  # (environment, vhost)
    queue: str
    records: list[MessageRecord]
    ready: int  # messages ready when the scan started
    stopped: str | None  # "depth" / "memory" when the scan didn't reach the end
    depth: int
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    positions: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for position, record in enumerate(self.records):
            # duplicates share a fingerprint: the deepest copy decides how far to scan
            self.positions[record.fingerprint] = position

    @property
    def expires_at(self) -> datetime:
        return self.created_at + SNAPSHOT_TTL

    def meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scanned": len(self.records),
            "ready": self.ready,
            "complete": self.stopped is None,
            "stopped": self.stopped,
            "depth": self.depth,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    def matching(
        self,
        contains: str | None = None,
        payload_format: str | None = None,
        min_deaths: int | None = None,
    ) -> list[MessageRecord]:
        """Records matching every given filter, in queue order. `contains` is a
        case-insensitive substring of the raw body, message id, fingerprint or headers
        (a compressed body is searched as stored, like bulk's payload filter)."""
        needle = (contains or "").strip().lower()
        out = []
        for record in self.records:
            if payload_format and record.payload_format != payload_format:
                continue
            if min_deaths and _deaths(record) < min_deaths:
                continue
            if needle and needle not in _haystack(record):
                continue
            out.append(record)
        return out


def _deaths(record: MessageRecord) -> int:
    return sum(int(entry.get("count") or 0) for entry in record.x_death)


def _haystack(record: MessageRecord) -> str:
    return " ".join((
        record.body.decode("utf-8", errors="ignore"),
        record.message_id or "",
        record.fingerprint,
        json.dumps(record.headers, default=str),
    )).lower()


class SnapshotStore:
    def __init__(self) -> None:
        self._snapshots: OrderedDict[str, Snapshot] = OrderedDict()

    def add(
        self,
        scope: tuple[str, str],
        queue: str,
        records: list[MessageRecord],
        *,
        ready: int,
        stopped: str | None,
        depth: int,
    ) -> Snapshot:
        self._prune()
        snapshot = Snapshot(
            id=secrets.token_urlsafe(12), scope=scope, queue=queue, records=records,
            ready=ready, stopped=stopped, depth=depth,
        )
        self._snapshots[snapshot.id] = snapshot
        while len(self._snapshots) > MAX_SNAPSHOTS:
            self._snapshots.popitem(last=False)
        return snapshot

    def get(self, snapshot_id: str, scope: tuple[str, str], queue: str) -> Snapshot | None:
        """Only for the scope and queue it was taken of — never another broker's copy."""
        self._prune()
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None or snapshot.scope != scope or snapshot.queue != queue:
            return None
        self._snapshots.move_to_end(snapshot_id)
        return snapshot

    def _prune(self) -> None:
        now = datetime.now(UTC)
        for key in [k for k, s in self._snapshots.items() if s.expires_at <= now]:
            del self._snapshots[key]
