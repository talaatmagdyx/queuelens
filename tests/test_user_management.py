"""Admins change roles, deactivate, reactivate and remove accounts."""

import json

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests import cred

PW = {name: cred() for name in ("admin", "ops", "sam", "viewer")}
PROXY = ("10.0.0.2", 4000)


def _app(url, **overrides):
    return create_app(Settings(
        auth_enabled=True, admin_password=PW["admin"], database_url=url,
        users_json=json.dumps({"ops": PW["ops"]}), **overrides,
    ))


@pytest.fixture
async def app(tmp_path):
    app = _app(f"sqlite+aiosqlite:///{tmp_path}/u.db")
    await app.state.database.start()
    await app.state.users.seed_env_users(app.state.settings.users, "admin")
    await app.state.users.create(username="sam", password=PW["sam"], role="Viewer",
                                 email=None, invited_by="admin", must_change_password=False)
    yield app
    await app.state.database.close()


async def _call(app, method, path, auth, json_body=None, client=None, headers=None):
    transport = httpx.ASGITransport(app=app, **({"client": client} if client else {}))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.request(method, path, auth=auth, json=json_body, headers=headers)


ADMIN = ("admin", PW["admin"])
SAM = ("sam", PW["sam"])


async def test_an_admin_changes_a_role_and_it_applies_on_the_next_request(app) -> None:
    changed = await _call(app, "PATCH", "/api/users/sam", ADMIN, {"role": "Operator"})
    assert changed.json() == {"username": "sam", "role": "Operator"}
    assert (await _call(app, "GET", "/api/me", SAM)).json()["role"] == "Operator"
    row = (await app.state.audit_repository.list(action="update_user"))[0]
    assert row["username"] == "admin"
    assert row["metadata"] == {"user": "sam", "new_role": "Operator", "role": "Admin"}


async def test_a_deactivated_account_is_refused_until_reactivated(app) -> None:
    assert (await _call(app, "GET", "/api/me", SAM)).status_code == 200  # cached login
    await _call(app, "PATCH", "/api/users/sam", ADMIN, {"active": False})
    assert (await _call(app, "GET", "/api/me", SAM)).status_code == 401
    await _call(app, "PATCH", "/api/users/sam", ADMIN, {"active": True})
    assert (await _call(app, "GET", "/api/me", SAM)).status_code == 200


async def test_a_removed_account_can_no_longer_sign_in(app) -> None:
    removed = await _call(app, "DELETE", "/api/users/sam", ADMIN)
    assert removed.json() == {"deleted": "sam"}
    assert (await _call(app, "GET", "/api/me", SAM)).status_code == 401
    assert (await app.state.audit_repository.list(action="delete_user"))[0]["metadata"] == {
        "user": "sam", "role": "Admin"}  # the acting Admin's role is stamped on every row
    assert (await _call(app, "DELETE", "/api/users/sam", ADMIN)).status_code == 404


async def test_the_guards(app) -> None:
    await app.state.users.create(username="dana", password=PW["viewer"], role="Admin",
                                 email=None, invited_by="admin", must_change_password=False)
    dana = ("dana", PW["viewer"])  # an Admin with a local account, not one from env vars
    cases = [
        ("PATCH", "/api/users/dana", dana, {"role": "Viewer"}, 400),  # not yourself
        ("DELETE", "/api/users/dana", dana, None, 400),
        ("PATCH", "/api/users/sam", dana, {"role": "Operator"}, 200),  # others, yes
        ("PATCH", "/api/users/admin", ADMIN, {"active": False}, 400),  # yourself
        ("DELETE", "/api/users/admin", ADMIN, None, 400),
        ("PATCH", "/api/users/ops", ADMIN, {"role": "Viewer"}, 400),  # set by env vars
        ("DELETE", "/api/users/ops", ADMIN, None, 400),
        ("PATCH", "/api/users/nobody", ADMIN, {"active": False}, 404),
        ("PATCH", "/api/users/sam", ADMIN, {}, 400),  # nothing to change
        ("PATCH", "/api/users/sam", ADMIN, {"role": "Root"}, 422),
        ("PATCH", "/api/users/sam", ("ops", PW["ops"]), {"active": False}, 403),  # Operator
    ]
    for method, path, auth, body, status in cases:
        got = (await _call(app, method, path, auth, body)).status_code
        assert (method, path, got) == (method, path, status)


async def test_the_users_list_says_which_accounts_can_be_changed(app) -> None:
    await _call(app, "PATCH", "/api/users/sam", ADMIN, {"active": False})
    accounts = {a["username"]: a for a in (await _call(app, "GET", "/api/users", ADMIN))
                .json()["accounts"]}
    assert (accounts["admin"]["managed"], accounts["ops"]["managed"]) == ("env", "env")
    assert (accounts["sam"]["managed"], accounts["sam"]["active"]) == ("local", False)


async def test_a_deactivated_account_is_refused_through_sso_too(tmp_path) -> None:
    app = _app(f"sqlite+aiosqlite:///{tmp_path}/sso.db", auth_proxy_header="X-Forwarded-User",
               trusted_proxies="10.0.0.0/24", auth_proxy_default_role="Operator")
    await app.state.database.start()
    try:
        await app.state.users.create(username="sam", password=PW["sam"], role="Viewer",
                                     email=None, invited_by="admin")
        via_proxy = {"X-Forwarded-User": "sam"}
        assert (await _call(app, "GET", "/api/me", None, client=PROXY, headers=via_proxy)
                ).json()["role"] == "Viewer"
        await _call(app, "PATCH", "/api/users/sam", ADMIN, {"active": False})
        assert (await _call(app, "GET", "/api/me", None, client=PROXY, headers=via_proxy)
                ).status_code == 403
    finally:
        await app.state.database.close()


async def test_a_deactivation_applies_on_every_replica_at_once(tmp_path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path}/shared.db"
    a, b = _app(url), _app(url)
    for replica in (a, b):
        await replica.state.database.start()
    try:
        await a.state.users.create(username="sam", password=PW["sam"], role="Viewer",
                                   email=None, invited_by="admin", must_change_password=False)
        assert (await _call(b, "GET", "/api/me", SAM)).status_code == 200  # cached on b
        await _call(a, "PATCH", "/api/users/sam", ADMIN, {"active": False})
        assert (await _call(b, "GET", "/api/me", SAM)).status_code == 401
    finally:
        for replica in (a, b):
            await replica.state.database.close()


async def test_invites_and_password_changes_are_audited(app) -> None:
    invited = (await _call(app, "POST", "/api/users/invite", ADMIN,
                           {"username": "rita", "role": "Viewer"})).json()
    new = cred() + cred()  # 10+ characters
    changed = await _call(app, "POST", "/api/users/me/password", ("rita", invited["password"]),
                          {"current_password": invited["password"], "new_password": new})
    assert changed.status_code == 200
    rows = {row["action"]: row for row in await app.state.audit_repository.list()}
    assert (rows["invite_user"]["username"], rows["invite_user"]["metadata"]["user"],
            rows["invite_user"]["metadata"]["new_role"]) == ("admin", "rita", "Viewer")
    assert (rows["change_password"]["username"],
            rows["change_password"]["metadata"]["user"]) == ("rita", "rita")
    assert new not in str(rows) and invited["password"] not in str(rows)  # never a password
