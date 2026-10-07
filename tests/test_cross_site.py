"""CSRF: a page on another site can make a signed-in browser send requests here, with its
cached Basic credentials attached. Browsers mark those requests with Sec-Fetch-Site, and
nothing from another site may change anything."""

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests import cred

PW = cred()
ADMIN = ("admin", PW)


@pytest.fixture
async def app(tmp_path):
    app = create_app(Settings(auth_enabled=True, admin_password=PW,
                              database_url=f"sqlite+aiosqlite:///{tmp_path}/x.db"))
    await app.state.database.start()
    ran: list[int] = []

    class Runner:
        async def run(self, policy, preview=False):
            ran.append(policy["id"])
            return {"replayed": 0}

    app.state.policy_runner = Runner()
    app.state.ran = ran
    await app.state.replay_policies.create("admin", name="orders", queue="orders.dlq",
                                           environment=None, vhost=None)
    yield app
    await app.state.database.close()


async def _send(app, method, path, site=None, **kw):
    headers = {"Sec-Fetch-Site": site} if site else {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://queuelens.test") as http:
        return await http.request(method, path, auth=ADMIN, headers=headers, **kw)


async def test_a_form_on_another_site_cannot_run_a_policy(app) -> None:
    # what <form method=post action=.../run> on a page elsewhere makes the browser send
    r = await _send(app, "POST", "/api/policies/1/run", site="cross-site")
    assert r.status_code == 403
    assert app.state.ran == []  # never reached the runner


@pytest.mark.parametrize(("method", "path", "body"), [
    ("POST", "/api/alerts", {"name": "x"}),
    ("DELETE", "/api/policies/1", None),
])
@pytest.mark.parametrize("site", ["cross-site", "same-site"])
async def test_nothing_from_another_site_changes_anything(app, method, path, body, site) -> None:
    r = await _send(app, method, path, site=site, json=body)
    assert r.status_code == 403
    assert await app.state.replay_policies.get(1) is not None
    assert await app.state.alert_rules.list() == []


@pytest.mark.parametrize("site", ["same-origin", None])  # the console; curl and scripts
async def test_the_console_and_api_clients_still_change_things(app, site) -> None:
    assert (await _send(app, "POST", "/api/policies/1/run", site=site)).status_code == 200
    assert (await _send(app, "POST", "/api/alerts", site=site,
                        json={"name": "backlog"})).status_code == 200
    assert app.state.ran == [1]


async def test_reads_from_another_site_are_left_to_the_browser(app) -> None:
    # a cross-site GET can't read the answer (no CORS), and reads change nothing
    assert (await _send(app, "GET", "/api/policies", site="cross-site")).status_code == 200
