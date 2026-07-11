from datetime import UTC, datetime

from app.config import Settings
from app.domain.models import ReplayTarget
from app.infrastructure.rabbitmq.message_operator import MessageOperator


class ActionService:
    def __init__(self, settings: Settings, operator: MessageOperator) -> None:
        self._settings = settings
        self._operator = operator

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
    ) -> dict[str, object]:
        resolved_target = target or self._configured_target(source_queue)
        if resolved_target is None:
            raise ValueError("No replay target configured for this queue")
        headers: dict[str, object] = dict(extra_headers or {})
        if annotate:
            headers = {
                **headers,
                "x-queuelens-replayed": True,
                "x-queuelens-action": f"replay_{mode}",
                "x-queuelens-replayed-at": datetime.now(UTC).isoformat(),
                "x-queuelens-replayed-by": username,
                "x-queuelens-source-queue": source_queue,
                "x-queuelens-original-fingerprint": fingerprint,
            }
        result = await self._operator.operate(
            source_queue=source_queue,
            fingerprint=fingerprint,
            action=mode,
            target=resolved_target,
            replay_headers=headers,
            max_scan=self._settings.refetch_window_size,
        )
        result["headers_added"] = headers
        return result

    async def park(self, *, source_queue: str, fingerprint: str) -> dict[str, object]:
        target = ReplayTarget(type="queue", queue=f"{source_queue}.parking")
        headers: dict[str, object] = {
            "x-queuelens-action": "park",
            "x-queuelens-parked-at": datetime.now(UTC).isoformat(),
            "x-queuelens-source-queue": source_queue,
            "x-queuelens-original-fingerprint": fingerprint,
        }
        result = await self._operator.operate(
            source_queue=source_queue,
            fingerprint=fingerprint,
            action="park",
            target=target,
            replay_headers=headers,
            max_scan=self._settings.refetch_window_size,
        )
        result["headers_added"] = headers
        return result

    async def delete(self, *, source_queue: str, fingerprint: str) -> dict[str, object]:
        return await self._operator.operate(
            source_queue=source_queue,
            fingerprint=fingerprint,
            action="delete",
            max_scan=self._settings.refetch_window_size,
        )

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
