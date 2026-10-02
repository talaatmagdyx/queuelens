from datetime import UTC, datetime
from typing import Any

from app.application.message_service import BrowseGuard, _no_guard
from app.config import Settings
from app.domain.models import ReplayTarget
from app.infrastructure.rabbitmq.message_operator import MessageOperator


def provenance_headers(action: str, source_queue: str, username: str) -> dict[str, object]:
    """x-queuelens-* headers for a replay ("copy"/"move") or a park — single and bulk
    actions stamp the same set; the original fingerprint is added per message."""
    now = datetime.now(UTC).isoformat()
    if action == "park":
        return {
            "x-queuelens-action": "park",
            "x-queuelens-parked-at": now,
            "x-queuelens-parked-by": username,
            "x-queuelens-source-queue": source_queue,
        }
    return {
        "x-queuelens-replayed": True,
        "x-queuelens-action": f"replay_{action}",
        "x-queuelens-replayed-at": now,
        "x-queuelens-replayed-by": username,
        "x-queuelens-source-queue": source_queue,
    }


class ActionService:
    def __init__(
        self, settings: Settings, operator: MessageOperator, guard: BrowseGuard = _no_guard
    ) -> None:
        self._settings = settings
        self._operator = operator
        self._guard = guard

    async def replay(
        self,
        *,
        source_queue: str,
        fingerprint: str,
        mode: str,
        target: ReplayTarget | None,
        username: str,
        annotate: bool = True,
        extra_headers: dict[str, object] | None = None,
        max_scan: int | None = None,
        depth: int | None = None,
    ) -> dict[str, object]:
        resolved_target = target or self._configured_target(source_queue)
        if resolved_target is None:
            raise ValueError("No replay target configured for this queue")
        headers: dict[str, object] = dict(extra_headers or {})
        if annotate:
            headers = {
                **headers,
                **provenance_headers(mode, source_queue, username),
                "x-queuelens-original-fingerprint": fingerprint,
            }
        result = await self._operator.operate(
            source_queue=source_queue,
            fingerprint=fingerprint,
            action=mode,
            target=resolved_target,
            replay_headers=headers,
            **await self._scan(source_queue, max_scan, depth),
        )
        result["headers_added"] = headers
        return result

    async def park(
        self,
        *,
        source_queue: str,
        fingerprint: str,
        username: str = "",
        extra_headers: dict[str, object] | None = None,
        max_scan: int | None = None,
        depth: int | None = None,
    ) -> dict[str, object]:
        target = ReplayTarget(type="queue", queue=f"{source_queue}.parking")
        headers: dict[str, object] = {
            **(extra_headers or {}),
            **provenance_headers("park", source_queue, username),
            "x-queuelens-original-fingerprint": fingerprint,
        }
        result = await self._operator.operate(
            source_queue=source_queue,
            fingerprint=fingerprint,
            action="park",
            target=target,
            replay_headers=headers,
            **await self._scan(source_queue, max_scan, depth),
        )
        result["headers_added"] = headers
        return result

    async def delete(
        self,
        *,
        source_queue: str,
        fingerprint: str,
        max_scan: int | None = None,
        depth: int | None = None,
    ) -> dict[str, object]:
        return await self._operator.operate(
            source_queue=source_queue,
            fingerprint=fingerprint,
            action="delete",
            **await self._scan(source_queue, max_scan, depth),
        )

    async def _scan(
        self, source_queue: str, max_scan: int | None, depth: int | None
    ) -> dict[str, Any]:
        """Guard the queue, then how far the action scans: its window — or, for a quorum
        queue, which is only ever scanned whole, the browse depth."""
        whole = bool(await self._guard(source_queue))
        window = max_scan or self._settings.refetch_window_size
        return {"max_scan": (depth or window) if whole else window, "whole": whole}

    def _configured_target(self, source_queue: str) -> ReplayTarget | None:
        return configured_target(self._settings, source_queue)


def configured_target(settings: Settings, source_queue: str) -> ReplayTarget | None:
    raw_target = settings.replay_targets.get(source_queue)
    if not raw_target:
        return None
    return ReplayTarget(
        type=str(raw_target.get("type", "")),
        queue=raw_target.get("queue"),
        exchange=raw_target.get("exchange"),
        routing_key=raw_target.get("routing_key"),
    )
