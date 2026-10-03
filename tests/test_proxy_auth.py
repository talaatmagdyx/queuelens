"""SSO behind an authenticating proxy: the identity header counts only from a trusted peer."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import Depends
from sqlalchemy import update

from app.auth.basic import CurrentUser, get_current_user
from app.config import Settings
from app.domain.models import AuditEntry
from app.infrastructure.persistence.models import UserModel
from app.main import create_app
from tests import cred

PW = {name: cred() for name in ("admin", "ops")}
PROXY, OUTSIDER = ("10.0.0.2", 4000), ("203.0.113.9", 4000)


def _app(tmp_path, **overrides):
    return create_app(Settings(
        auth_enabled=True, admin_password=PW["admin"],
        database_url=f"sqlite+aiosqlite:///{tmp_path}/p.db",
        auth_proxy_header="X-Forwarded-User", auth_proxy_groups_header="X-Forwarded-Groups",
        auth_proxy_roles_json=json.dumps({"sre": "Operator", "platform-admins": "Admin"}),
        trusted_proxies="10.0.0.0/24", **overrides,
    ))


async def _get(app, path, client=PROXY, **headers):
    transport = httpx.ASGITransport(app=app, client=client)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.get(path, headers=headers)


@pytest.fixture
async def app(tmp_path):
    app = _app(tmp_path)
    await app.state.database.start()
    yield app
    await app.state.database.close()


async def test_trusted_proxy_names_the_user_and_groups_pick_the_role(app) -> None:
    me = await _get(app, "/api/me", **{"X-Forwarded-User": "alice@example.com"})
    assert me.json() == {"username": "alice@example.com", "role": "Viewer",
                         "must_change_password": False}
    me = await _get(app, "/api/me", **{"X-Forwarded-User": "bob",
                                       "X-Forwarded-Groups": "devs, sre,platform-admins"})
    assert me.json()["role"] == "Admin"  # the highest mapped group wins


async def test_identity_header_from_anyone_else_is_ignored(app) -> None:
    forged = await _get(app, "/api/me", client=OUTSIDER, **{"X-Forwarded-User": "admin"})
    assert forged.status_code == 401
    # nor can a forwarded address pass for the proxy
    forged = await _get(app, "/api/me", client=OUTSIDER, **{
        "X-Forwarded-User": "admin", "X-Forwarded-For": "10.0.0.2"})
    assert forged.status_code == 401


async def test_basic_auth_still_works_for_break_glass_and_scripts(app) -> None:
    transport = httpx.ASGITransport(app=app, client=OUTSIDER)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        me = await http.get("/api/me", auth=("admin", PW["admin"]))
    assert me.json()["role"] == "Admin"


async def test_local_accounts_keep_their_role_and_deactivation(app) -> None:
    await app.state.users.create(username="carol", password=PW["ops"], role="Viewer",
                                 email=None, invited_by="admin")
    me = await _get(app, "/api/me", **{"X-Forwarded-User": "carol",
                                       "X-Forwarded-Groups": "platform-admins"})
    assert me.json()["role"] == "Viewer"  # set on the Users page, not by the group
    async with app.state.database.session() as session:
        await session.execute(update(UserModel).where(UserModel.username == "carol")
                              .values(active=False))
        await session.commit()
    assert (await _get(app, "/api/me", **{"X-Forwarded-User": "carol"})).status_code == 403


async def test_no_default_role_refuses_unmapped_users(tmp_path) -> None:
    app = _app(tmp_path, auth_proxy_default_role="")
    await app.state.database.start()
    try:
        assert (await _get(app, "/api/me", **{"X-Forwarded-User": "eve"})).status_code == 403
        sre = await _get(app, "/api/me", **{"X-Forwarded-User": "sam", "X-Forwarded-Groups": "sre"})
        assert sre.json()["role"] == "Operator"
    finally:
        await app.state.database.close()


async def _audited(app, client, auth=None, **headers) -> dict:
    """An audited request through the whole middleware stack; returns its audit row."""
    async def probe(user: CurrentUser = Depends(get_current_user)) -> None:
        await app.state.audit_repository.record(AuditEntry(
            username=user.username, action="probe", timestamp=datetime.now(UTC),
            result="success"))
    app.add_api_route("/probe", probe, methods=["POST"])
    transport = httpx.ASGITransport(app=app, client=client)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        assert (await http.post("/probe", auth=auth, headers=headers)).status_code == 200
    return (await app.state.audit_repository.list(action="probe"))[0]


async def test_audit_names_the_proxy_user_and_their_forwarded_address(app) -> None:
    row = await _audited(app, PROXY, **{"X-Forwarded-User": "dana",
                                         "X-Forwarded-For": "198.51.100.7"})
    assert (row["username"], row["request_ip"]) == ("dana", "198.51.100.7")


async def test_forwarded_for_from_an_untrusted_peer_is_not_believed(app) -> None:
    row = await _audited(app, OUTSIDER, auth=("admin", PW["admin"]),
                         **{"X-Forwarded-For": "10.0.0.2"})
    assert row["request_ip"] == OUTSIDER[0]


def test_unsafe_proxy_settings_fail_at_startup() -> None:
    for bad in ({"trusted_proxies": "*"}, {"trusted_proxies": "proxy.internal"},
                {"auth_proxy_roles_json": '{"sre": "root"}'},
                {"auth_proxy_default_role": "admin"}):
        with pytest.raises(ValueError):
            Settings(**bad)
