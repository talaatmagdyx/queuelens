"""Configuration changes are audited: which settings changed (values only where they hold
no secrets), and every alert-rule change."""

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests import cred

SECRET_URL = f"https://example.invalid/{cred()}"  # stands in for a webhook URL with a token


@pytest.fixture
async def app(tmp_path):
    app = create_app(Settings(auth_enabled=False,
                              database_url=f"sqlite+aiosqlite:///{tmp_path}/c.db"))
    await app.state.database.start()
    yield app
    await app.state.database.close()


async def _call(app, method, path, body=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.request(method, path, json=body)
        assert response.status_code == 200, response.text
        return response.json()


async def _rows(app, action):
    return await app.state.audit_repository.list(action=action)


async def test_a_settings_change_records_its_keys_and_never_a_secret(app) -> None:
    change = {"values": {"retention": {"days": 30}, "channels": {"slack": {"url": SECRET_URL}}}}
    await _call(app, "PUT", "/api/settings", change)
    await _call(app, "PUT", "/api/settings", change)  # the same again changes nothing
    rows = await _rows(app, "update_settings")
    assert len(rows) == 1
    assert rows[0]["metadata"]["keys"] == ["channels", "retention"]
    assert rows[0]["metadata"]["retention"] == {"days": 30}  # numbers only: safe to keep
    assert "channels" not in {k for k in rows[0]["metadata"] if k != "keys"}
    assert SECRET_URL not in str(rows)


async def test_every_alert_rule_change_is_audited(app) -> None:
    rule = {"name": "backlog", "pattern": "*.dlq", "threshold": 100}
    created = await _call(app, "POST", "/api/alerts", rule)
    await _call(app, "PUT", f"/api/alerts/{created['id']}", {**rule, "threshold": 200})
    await _call(app, "PATCH", f"/api/alerts/{created['id']}", {"enabled": False})
    await _call(app, "DELETE", f"/api/alerts/{created['id']}")
    trail = [(row["action"], row["metadata"]["name"], row["metadata"].get("enabled"))
             for row in reversed(await app.state.audit_repository.list())]
    assert trail == [
        ("create_alert_rule", "backlog", None),
        ("update_alert_rule", "backlog", None),
        ("update_alert_rule", "backlog", False),
        ("delete_alert_rule", "backlog", None),
    ]
