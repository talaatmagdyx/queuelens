"""Each request names its environment/vhost — nothing is instance-global."""

from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.domain.models import QueueInfo
from app.main import create_app

STAGING = {"X-QueueLens-Environment": "staging", "X-QueueLens-Vhost": "/"}


class Queues:
    def __init__(self, name: str) -> None:
        self.name = name

    async def list_queues(self, dlq_only: bool = False) -> list[QueueInfo]:
        return [QueueInfo(name=self.name, vhost="/", messages=1, messages_ready=1,
                          messages_unacked=0, consumers=0, durable=True, is_dlq=True)]


class Actions:
    async def park(self, **_kwargs: object) -> dict[str, object]:
        return {"status": "success", "action": "park"}


class Closable:
    is_connected = True
    closed = False

    async def close(self) -> None:
        self.closed = True


def _bundle(queue: str) -> SimpleNamespace:
    """Stands in for a started environment bundle (no broker needed)."""
    return SimpleNamespace(started=True, queue_service=Queues(queue), action_service=Actions(),
                           rabbitmq_connection=Closable(), management_client=Closable())


def _app(tmp_path):
    app = create_app(Settings(auth_enabled=False, database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db",
                              environments_json='{"staging": {"vhosts": ["/", "reports"]}}'))
    app.state.queue_service = Queues("dev.dlq")
    app.state.environment_manager._bundles[("staging", "/")] = _bundle("staging.dlq")
    return app


async def _names(client, headers=None) -> list[str]:
    response = await client.get("/api/queues", headers=headers or {})
    return [q["name"] for q in response.json()["queues"]]


@pytest.mark.asyncio
async def test_two_scopes_at_once_and_switching_changes_nothing_for_others(tmp_path) -> None:
    app = _app(tmp_path)
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        default, staging = await _names(client), await _names(client, STAGING)
        activated = await client.post("/api/environments/activate", json={"environment": "staging"})
        after = await _names(client)  # another tab, no headers: still the default
        listing = (await client.get("/api/environments", headers=STAGING)).json()["environments"]
        notices = (await client.get("/api/notifications")).json()["notifications"]
    await app.state.database.close()

    assert (default, staging, after) == (["dev.dlq"], ["staging.dlq"], ["dev.dlq"])
    assert activated.json() == {"environment": "staging", "vhost": "/"}
    envs = {e["id"]: e for e in listing}
    assert envs["staging"]["active"] and envs["staging"]["active_vhost"] == "/"
    assert envs["development"]["default"] and not envs["development"]["active"]
    assert not any("switched" in n["title"].lower() for n in notices)  # nothing global to announce


@pytest.mark.asyncio
async def test_an_unknown_scope_is_refused_never_defaulted(tmp_path) -> None:
    app = _app(tmp_path)
    await app.state.database.start()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        env = await client.get("/api/queues", headers={"X-QueueLens-Environment": "prod"})
        vhost = await client.post(
            "/api/messages/park",
            headers={"X-QueueLens-Environment": "staging", "X-QueueLens-Vhost": "rogue"},
            json={"source_queue": "q", "fingerprint": "a" * 64, "confirm": True},
        )
    await app.state.database.close()

    assert env.status_code == 404 and "Unknown environment: prod" in env.json()["detail"]
    assert vhost.status_code == 404 and "rogue" in vhost.json()["detail"]


@pytest.mark.asyncio
async def test_audit_rows_name_the_environment_and_vhost(tmp_path) -> None:
    app = _app(tmp_path)
    await app.state.database.start()
    body = {"source_queue": "orders.dlq", "fingerprint": "a" * 64, "confirm": True}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        parked = await client.post("/api/messages/park", headers=STAGING, json=body)
    events = await app.state.audit_repository.list(action="park")
    await app.state.database.close()

    assert parked.status_code == 200
    assert events and all(
        (e["metadata"]["environment"], e["metadata"]["vhost"]) == ("staging", "/") for e in events
    )


@pytest.mark.asyncio
async def test_removing_an_environment_closes_it_and_its_scope_stops_resolving(tmp_path) -> None:
    app = _app(tmp_path)
    await app.state.database.start()
    manager = app.state.environment_manager
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/api/environments", json={"name": "scratch", "vhosts": ["/"]})
        manager._bundles[("scratch", "/")] = bundle = _bundle("scratch.dlq")
        scoped = {"X-QueueLens-Environment": "scratch"}
        before = await _names(client, scoped)
        removed = await client.delete("/api/environments/scratch")
        after = await client.get("/api/queues", headers=scoped)
    await app.state.database.close()

    assert before == ["scratch.dlq"] and removed.status_code == 200
    assert bundle.rabbitmq_connection.closed and bundle.management_client.closed
    assert after.status_code == 404
