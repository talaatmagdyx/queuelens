"""Replay policies: QueueLens retries a dead-letter queue by itself.

A run reads the DLQ once and sorts its messages, oldest first:
- exhausted (`max_deaths` deaths or more): parked, for a person to look at;
- due (dead for at least `backoff_minutes` x 2^(deaths - 1)): replayed, moved, to the
  queue it died in (its x-death history), else to the DLQ's configured replay target;
- waiting (not due yet), or skipped: no death history, or nowhere to replay it to.
At most `cap` messages are acted on per run. A replay target with no consumers is skipped:
the message would only die again. Everything goes through the bulk path a person uses
(dry run, publish-before-ack, the per-queue lock, the audit trail) as user
`policy:<name>`. Only the replica that leads the alert engine runs policies, for the
default environment. Three runs in a row with failed publishes pause the policy and notify.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.action_service import configured_target
from app.application.bulk_runs import execute_audited
from app.domain.models import AuditEntry, MessageRecord, ReplayTarget

logger = logging.getLogger(__name__)
PAUSE_AFTER_FAILED_RUNS = 3


def deaths(record: MessageRecord) -> int:
    return sum(int(entry.get("count") or 0) for entry in record.x_death)


def _last_death(record: MessageRecord) -> tuple[datetime | None, str | None]:
    """When the message last died, and the queue it died in."""
    def at(entry: dict[str, Any]) -> datetime | None:
        value = entry.get("time")
        if not isinstance(value, datetime):
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    timed = [(at(entry), entry) for entry in record.x_death]
    latest = max(timed, key=lambda item: item[0] or datetime.min.replace(tzinfo=UTC))
    return latest[0], latest[1].get("queue")


@dataclass
class Plan:
    replay: dict[ReplayTarget, list[str]] = field(default_factory=dict)
    park: list[str] = field(default_factory=list)
    waiting: int = 0
    no_history: int = 0
    no_target: int = 0
    capped: int = 0


def plan(records: list[MessageRecord], *, dlq: str, now: datetime, max_deaths: int,
         backoff_minutes: int, cap: int, fallback: ReplayTarget | None) -> Plan:
    out = Plan()
    acted = 0
    for record in records:  # queue order: the oldest first
        died = deaths(record)
        if died == 0:
            out.no_history += 1
            continue
        target: ReplayTarget | None = None
        if died < max_deaths:
            died_at, origin = _last_death(record)
            wait = timedelta(minutes=backoff_minutes * 2 ** (died - 1))
            if died_at is not None and now - died_at < wait:
                out.waiting += 1
                continue
            target = (ReplayTarget(type="queue", queue=origin) if origin and origin != dlq
                      else fallback)
            if target is None:
                out.no_target += 1
                continue
        if acted >= cap:
            out.capped += 1
            continue
        acted += 1
        if target is None:
            out.park.append(record.fingerprint)
        else:
            out.replay.setdefault(target, []).append(record.fingerprint)
    return out


def _target_name(target: ReplayTarget) -> str:
    return target.queue or f"{target.exchange} / {target.routing_key or ''}"


class PolicyRunner:
    TICK_SECONDS = 30.0

    def __init__(self, state: Any, *, is_leader: Callable[[], Awaitable[bool]] | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._state = state  # app.state: the default environment's services
        self._is_leader = is_leader
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                if self._is_leader is None or await self._is_leader():
                    await self.run_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive a bad run
                logger.exception("replay policy pass failed")
            await asyncio.sleep(self.TICK_SECONDS)

    async def run_due(self) -> None:
        now = self._clock()
        for policy in await self._state.replay_policies.list():
            last = policy["last_run_at"] and datetime.fromisoformat(policy["last_run_at"])
            if policy["enabled"] and (
                not last or now - last >= timedelta(minutes=policy["interval_minutes"])
            ):
                await self.run(policy)

    async def _limit(self, key: str, ceiling: int) -> int:
        stored = await self._state.settings_store.get_safe("limits", {}) or {}
        return max(1, min(int(stored.get(key) or getattr(self._state.settings, key)), ceiling))

    async def run(self, policy: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
        """One run, or with `preview` only what a run would do now (nothing moves)."""
        state = self._state
        queue = policy["queue"]
        depth = await self._limit("max_browse_depth", 50_000)
        cap = min(policy["cap"], await self._limit("max_bulk_size", 1000))
        now = self._clock()
        scan = await state.message_service.snapshot(queue, depth)
        sorted_ = plan(scan.records, dlq=queue, now=now, max_deaths=policy["max_deaths"],
                       backoff_minutes=policy["backoff_minutes"], cap=cap,
                       fallback=configured_target(state.settings, queue))
        result: dict[str, Any] = {
            "at": now.isoformat(), "scanned": len(scan.records),
            "waiting": sorted_.waiting, "no_history": sorted_.no_history,
            "no_target": sorted_.no_target, "capped": sorted_.capped,
            "replayed": 0, "parked": 0, "failed": 0, "skipped_no_consumers": 0,
            "targets": {}, "errors": [],
        }
        consumers: dict[ReplayTarget, int | None] = {}
        for target in sorted_.replay:
            if target.type == "queue" and target.queue:
                try:
                    info = await state.queue_service.get_queue(target.queue)
                    consumers[target] = info.consumers
                except Exception as error:  # noqa: BLE001 - a missing target is a skip, noted
                    consumers[target] = 0
                    result["errors"].append(f"{target.queue}: {error}")
            else:
                consumers[target] = None  # an exchange: who consumes isn't knowable here
        if preview:
            result["targets"] = {_target_name(t): {"due": len(fps), "consumers": consumers[t]}
                                 for t, fps in sorted_.replay.items()}
            result["to_park"] = len(sorted_.park)
            return result

        username = f"policy:{policy['name']}"
        headers = await _custom_headers(state)
        for target, fingerprints in sorted_.replay.items():
            name = _target_name(target)
            if consumers[target] == 0:
                result["skipped_no_consumers"] += len(fingerprints)
                result["targets"][name] = {"skipped": "no consumers", "due": len(fingerprints)}
                continue
            summary = await self._bulk(policy, "replay", fingerprints, target, depth,
                                       username, headers, result)
            result["replayed"] += summary.get("succeeded", 0)
            result["targets"][name] = summary
        if sorted_.park:
            summary = await self._bulk(policy, "park", sorted_.park, None, depth, username,
                                       headers, result)
            result["parked"] += summary.get("succeeded", 0)
        await self._finish(policy, result, username)
        return result

    async def _bulk(self, policy: dict[str, Any], action: str, fingerprints: list[str],
                    target: ReplayTarget | None, depth: int, username: str,
                    headers: dict[str, object], result: dict[str, Any]) -> dict[str, int]:
        service = self._state.bulk_service
        try:
            preview = await service.dry_run(
                source_queue=policy["queue"], action=action, mode="move", target=target,
                selected_fingerprints=frozenset(fingerprints), max_bulk=len(fingerprints),
                scan_limit=depth, depth=depth,
            )
            _batch, outcome = await execute_audited(
                service, self._state.audit_repository, str(preview["batch_id"]), username,
                headers,
            )
        except Exception as error:  # noqa: BLE001 - counted as failed, the run goes on
            result["failed"] += len(fingerprints)
            result["errors"].append(f"{action}: {error}")
            return {"failed": len(fingerprints)}
        summary = {str(k): int(v) for k, v in dict(outcome["summary"]).items()}  # type: ignore[call-overload]
        result["failed"] += summary.get("failed", 0)
        return summary

    async def _finish(self, policy: dict[str, Any], result: dict[str, Any],
                      username: str) -> None:
        state = self._state
        failed_run = result["failed"] > 0
        failed_runs = await state.replay_policies.record_run(
            policy["id"], self._clock(), result, failed_run)
        if result["replayed"] or result["parked"] or failed_run:
            await state.audit_repository.record(AuditEntry(
                username=username, action="run_replay_policy", timestamp=self._clock(),
                source_queue=policy["queue"], result="partial" if failed_run else "success",
                metadata={"policy": policy["id"], **{k: result[k] for k in (
                    "replayed", "parked", "failed", "waiting", "skipped_no_consumers",
                    "capped")}},
            ))
        if failed_runs >= PAUSE_AFTER_FAILED_RUNS:
            await state.replay_policies.update(policy["id"], enabled=False)
            await self._notify_paused(policy, result, failed_runs, username)

    async def _notify_paused(self, policy: dict[str, Any], result: dict[str, Any],
                             failed_runs: int, username: str) -> None:
        state = self._state
        title = f"Replay policy paused: {policy['name']}"
        message = (f"{failed_runs} runs in a row failed to publish from {policy['queue']}; "
                   f"the policy is disabled until an Admin enables it again. "
                   f"Last errors: {'; '.join(result['errors'][-3:]) or 'see the audit log'}")
        await state.audit_repository.record(AuditEntry(
            username=username, action="pause_replay_policy", timestamp=self._clock(),
            source_queue=policy["queue"], result="success",
            metadata={"policy": policy["id"], "failed_runs": failed_runs},
        ))
        channels = [name for name, config in
                    (await state.settings_store.get("channels", {}) or {}).items()
                    if isinstance(config, dict)
                    and (config.get("url") or config.get("routing_key") or config.get("smtp_host"))]
        delivery = await state.alert_engine.dispatch(channels, title, message, severity="Alert")
        await state.notifications.add(level="Alert", title=title, message=message,
                                      source="Replay policy", delivery=delivery)


async def _custom_headers(state: Any) -> dict[str, object]:
    """The admin-configured headers every published message carries."""
    stored = await state.settings_store.get_safe("custom_headers", []) or []
    return {str(item["key"]): str(item["value"])
            for item in stored if isinstance(item, dict) and item.get("key")}
