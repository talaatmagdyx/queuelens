import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.application.action_service import configured_target
from app.config import Settings
from app.domain.models import ReplayTarget
from app.infrastructure.rabbitmq.message_browser import MessageBrowser
from app.infrastructure.rabbitmq.message_operator import MessageOperator


class UnknownBulkBatch(LookupError):
    """The dry-run batch id is unknown, expired, or already executed."""


@dataclass(frozen=True, slots=True)
class BulkBatch:
    id: str
    source_queue: str
    action: str  # replay | park | delete (display)
    operator_action: str  # copy | move | park | delete
    target: ReplayTarget | None
    fingerprints: frozenset[str]
    message_count: int
    duplicate_fingerprints: int
    sample_fingerprints: list[str] = field(default_factory=list)
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class BulkActionService:
    """Two-phase bulk actions: a dry-run captures exactly which messages were
    seen (a fingerprint set behind a one-shot token); execute acts only on that
    approved set. Batches live in process memory — a restart voids pending
    dry-runs, which fail safe with "run the dry-run again"."""

    def __init__(
        self, settings: Settings, browser: MessageBrowser, operator: MessageOperator
    ) -> None:
        self._settings = settings
        self._browser = browser
        self._operator = operator
        self._batches: dict[str, BulkBatch] = {}
        self._lock = asyncio.Lock()  # one bulk execution at a time

    async def dry_run(
        self,
        *,
        source_queue: str,
        action: str,
        mode: str = "copy",
        target: ReplayTarget | None = None,
        payload_contains: str | None = None,
        selected_fingerprints: frozenset[str] | None = None,
    ) -> dict[str, object]:
        if action == "replay":
            operator_action = mode
            resolved_target = target or configured_target(self._settings, source_queue)
            if resolved_target is None:
                raise ValueError("No replay target configured for this queue")
        elif action == "park":
            operator_action = "park"
            resolved_target = ReplayTarget(type="queue", queue=f"{source_queue}.parking")
        elif action == "delete":
            operator_action = "delete"
            resolved_target = None
        else:
            raise ValueError(f"Unsupported bulk action: {action}")

        records = await self._browser.list_messages(
            source_queue, self._settings.max_bulk_size
        )
        if payload_contains:
            needle = payload_contains.encode("utf-8")
            records = [record for record in records if needle in record.body]
        not_seen = 0
        if selected_fingerprints is not None:
            records = [
                record for record in records if record.fingerprint in selected_fingerprints
            ]
            not_seen = len(selected_fingerprints - {r.fingerprint for r in records})

        fingerprints = [record.fingerprint for record in records]
        unique = frozenset(fingerprints)
        duplicates = sum(1 for fp in unique if fingerprints.count(fp) > 1)
        batch = BulkBatch(
            id=secrets.token_urlsafe(16),
            source_queue=source_queue,
            action=action,
            operator_action=operator_action,
            target=resolved_target,
            fingerprints=unique,
            message_count=len(records),
            duplicate_fingerprints=duplicates,
            sample_fingerprints=sorted(unique)[:10],
            expires_at=datetime.now(UTC)
            + timedelta(seconds=self._settings.bulk_dry_run_ttl_seconds),
        )
        self._prune_expired()
        self._batches[batch.id] = batch
        return {
            "batch_id": batch.id,
            "source_queue": source_queue,
            "action": action,
            "mode": mode if action == "replay" else None,
            "target": _target_to_dict(resolved_target),
            "message_count": batch.message_count,
            "unique_fingerprints": len(unique),
            "duplicate_fingerprints": duplicates,
            "selected_not_seen": not_seen,
            "sample_fingerprints": batch.sample_fingerprints,
            "expires_at": batch.expires_at.isoformat(),
            "scan_limit": self._settings.max_bulk_size,
        }

    async def execute(
        self, batch_id: str, *, replay_headers: dict[str, object] | None = None
    ) -> tuple[BulkBatch, dict[str, object]]:
        async with self._lock:
            self._prune_expired()
            batch = self._batches.pop(batch_id, None)  # one-shot token
            if batch is None:
                raise UnknownBulkBatch(
                    "Unknown or expired dry-run batch; run the dry-run again"
                )
            results = await self._operator.operate_bulk(
                source_queue=batch.source_queue,
                fingerprints=batch.fingerprints,
                action=batch.operator_action,
                target=batch.target,
                replay_headers=dict(replay_headers or {}),
                max_scan=self._settings.max_bulk_size,
            )
        statuses = [str(result["status"]) for result in results]
        summary = {
            "fingerprints_requested": len(batch.fingerprints),
            "succeeded": statuses.count("success"),
            "failed": statuses.count("failed"),
            "skipped_duplicates": statuses.count("skipped_duplicate"),
            "not_found": statuses.count("not_found"),
        }
        return batch, {
            "batch_id": batch_id,
            "action": batch.action,
            "source_queue": batch.source_queue,
            "target": _target_to_dict(batch.target),
            "summary": summary,
            "results": results,
        }

    def _prune_expired(self) -> None:
        now = datetime.now(UTC)
        for key in [key for key, batch in self._batches.items() if batch.expires_at < now]:
            del self._batches[key]


def _target_to_dict(target: ReplayTarget | None) -> dict[str, str | None] | None:
    if target is None:
        return None
    return {
        "type": target.type,
        "queue": target.queue,
        "exchange": target.exchange,
        "routing_key": target.routing_key,
    }
