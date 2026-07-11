"""Alert rule evaluation loop: match queues, hold for duration, fire, notify, deliver."""

import asyncio
import fnmatch
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.infrastructure.mailer import send_email
from app.infrastructure.persistence.store import (
    AlertRuleRepository,
    NotificationRepository,
    SettingsRepository,
)

logger = logging.getLogger(__name__)

METRICS = ("messages_ready", "messages", "consumers", "publish_rate")
WEBHOOK_RETRY_DELAYS = (0.5, 2.0, 8.0)


def condition_holds(value: float, operator: str, threshold: float) -> bool:
    if operator == ">":
        return value > threshold
    if operator in (">=", "≥"):
        return value >= threshold
    if operator in ("=", "=="):
        return value == threshold
    if operator == "<":
        return value < threshold
    return False


async def post_webhook(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    for attempt, delay in enumerate(WEBHOOK_RETRY_DELAYS, start=1):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
            return {"ok": True, "attempts": attempt, "errors": errors}
        except Exception as error:  # noqa: BLE001 - any delivery failure is retryable
            errors.append(str(error))
            if attempt < len(WEBHOOK_RETRY_DELAYS):
                await asyncio.sleep(delay)
    return {"ok": False, "attempts": len(WEBHOOK_RETRY_DELAYS), "errors": errors}


class AlertEngine:
    """Evaluates enabled alert rules against live queue stats on an interval."""

    def __init__(
        self,
        *,
        rules: AlertRuleRepository,
        notifications: NotificationRepository,
        settings_store: SettingsRepository,
        get_queue_service: Any,  # callable returning the active env's queue service
        interval_seconds: float = 15.0,
    ) -> None:
        self._rules = rules
        self._notifications = notifications
        self._settings_store = settings_store
        self._get_queue_service = get_queue_service
        self._interval = interval_seconds
        # (rule_id, queue) -> first time the condition was observed true.
        # Fired-state itself lives on the rule row so restarts don't re-notify.
        self._pending: dict[tuple[int, str], datetime] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            try:
                await self.evaluate_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive broker hiccups
                logger.exception("alert evaluation pass failed")
            await asyncio.sleep(self._interval)

    async def evaluate_once(self) -> list[dict[str, Any]]:
        """One evaluation pass; returns notifications created (for tests)."""
        rules = [r for r in await self._rules.list() if r["enabled"]]
        if not rules:
            self._pending.clear()
            return []
        queue_service = self._get_queue_service()
        queues = await queue_service.list_queues(dlq_only=False)
        now = datetime.now(UTC)
        created: list[dict[str, Any]] = []

        for rule in rules:
            matched = [q for q in queues if fnmatch.fnmatch(q.name, rule["pattern"])]
            offenders = []
            for q in matched:
                value = {
                    "messages_ready": q.messages_ready,
                    "messages": q.messages,
                    "consumers": q.consumers,
                    "publish_rate": getattr(q, "publish_rate", None) or 0,
                }.get(rule["metric"], 0)
                if condition_holds(float(value), rule["operator"], float(rule["threshold"])):
                    offenders.append((q.name, value))

            key_base = rule["id"]
            if offenders:
                first = self._pending.setdefault((key_base, "*"), now)
                held = (now - first).total_seconds()
                if held >= rule["duration_seconds"] and not rule["fired"]:
                    await self._rules.mark_fired(key_base, now)
                    detail = ", ".join(f"{name}={value}" for name, value in offenders[:5])
                    notification = await self._fire(
                        rule,
                        level=rule["severity"],
                        title=f"Rule fired: {rule['name']}",
                        message=(
                            f"{rule['pattern']} · {rule['metric']} {rule['operator']} "
                            f"{rule['threshold']} — {detail}"
                        ),
                    )
                    created.append(notification)
            else:
                self._pending.pop((key_base, "*"), None)
                if rule["fired"]:
                    await self._rules.set_fired(key_base, False)
                    notification = await self._fire(
                        rule,
                        level="Success",
                        title=f"Recovered: {rule['name']}",
                        message=f"{rule['pattern']} · condition no longer holds",
                    )
                    created.append(notification)
        return created

    async def _fire(
        self, rule: dict[str, Any], *, level: str, title: str, message: str
    ) -> dict[str, Any]:
        delivery = await self.dispatch(rule["channels"], title, message, severity=level)
        return await self._notifications.add(
            level=level,
            title=title,
            message=message,
            rule_id=rule["id"],
            delivery=delivery,
        )

    @staticmethod
    def _in_quiet_hours(ui: dict[str, Any], now_hhmm: str) -> bool:
        """True when quiet hours are on and `now` falls inside the window (UTC)."""
        if not ui.get("quiet_hours"):
            return False
        start = str(ui.get("quiet_from") or "22:00")
        end = str(ui.get("quiet_until") or "07:00")
        if start <= end:
            return start <= now_hhmm < end
        return now_hhmm >= start or now_hhmm < end  # window crosses midnight

    async def dispatch(
        self,
        channels: list[str],
        title: str,
        message: str,
        severity: str = "Alert",
    ) -> dict[str, Any]:
        """Deliver to each configured channel; returns per-channel outcomes.
        Quiet hours mute Info/Warning deliveries — Alert severity always sends."""
        config = await self._settings_store.get("channels", {}) or {}
        ui = await self._settings_store.get("ui", {}) or {}
        now_hhmm = datetime.now(UTC).strftime("%H:%M")
        if severity != "Alert" and self._in_quiet_hours(ui, now_hhmm):
            return {
                channel: {"ok": False, "skipped": "quiet_hours", "errors": []}
                for channel in channels
            }
        outcomes: dict[str, Any] = {}
        for channel in channels:
            channel_config = config.get(channel) or {}
            if channel == "email":
                if not channel_config.get("smtp_host"):
                    outcomes[channel] = {"ok": False, "errors": ["email channel not configured"]}
                    continue
                outcomes[channel] = await send_email(
                    channel_config, f"[QueueLens] {title}", f"{title}\n\n{message}"
                )
            elif channel == "pagerduty" and channel_config.get("routing_key"):
                # native PagerDuty Events API v2
                outcomes[channel] = await post_webhook(
                    "https://events.pagerduty.com/v2/enqueue",
                    {
                        "routing_key": channel_config["routing_key"],
                        "event_action": "trigger",
                        "payload": {
                            "summary": f"{title} — {message}"[:1024],
                            "source": "queuelens",
                            "severity": "critical" if severity == "Alert" else "warning",
                        },
                    },
                )
            elif channel in ("webhook", "slack", "pagerduty"):
                url = channel_config.get("url")
                if not url:
                    outcomes[channel] = {
                        "ok": False,
                        "errors": [f"{channel} channel not configured"],
                    }
                    continue
                payload = (
                    {"text": f"{title} — {message}"}
                    if channel == "slack"
                    else {"title": title, "message": message, "source": "queuelens"}
                )
                outcomes[channel] = await post_webhook(url, payload)
            else:
                outcomes[channel] = {"ok": False, "errors": [f"unknown channel {channel}"]}
        return outcomes
