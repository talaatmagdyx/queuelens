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
    ) -> dict[str, object]:
        resolved_target = target or self._configured_target(source_queue)
        if resolved_target is None:
            raise ValueError("No replay target configured for this queue")
        headers = {
            "x-queuelens-replayed": True,
            "x-queuelens-replayed-at": datetime.now(UTC).isoformat(),
            "x-queuelens-replayed-by": username,
            "x-queuelens-source-queue": source_queue,
            "x-queuelens-original-fingerprint": fingerprint,
        }
        return await self._operator.operate(
            source_queue=source_queue,
            fingerprint=fingerprint,
            action=mode,
            target=resolved_target,
            replay_headers=headers,
            max_scan=self._settings.refetch_window_size,
        )

    async def park(self, *, source_queue: str, fingerprint: str) -> dict[str, object]:
        target = ReplayTarget(type="queue", queue=f"{source_queue}.parking")
        return await self._operator.operate(
            source_queue=source_queue,
            fingerprint=fingerprint,
            action="park",
            target=target,
            max_scan=self._settings.refetch_window_size,
        )

    async def delete(self, *, source_queue: str, fingerprint: str) -> dict[str, object]:
        return await self._operator.operate(
            source_queue=source_queue,
            fingerprint=fingerprint,
            action="delete",
            max_scan=self._settings.refetch_window_size,
        )

    def _configured_target(self, source_queue: str) -> ReplayTarget | None:
        raw_target = self._settings.replay_targets.get(source_queue)
        if not raw_target:
            return None
        return ReplayTarget(
            type=str(raw_target.get("type", "")),
            queue=raw_target.get("queue"),
            exchange=raw_target.get("exchange"),
            routing_key=raw_target.get("routing_key"),
        )
