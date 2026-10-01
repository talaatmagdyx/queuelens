"""Browser smoke test for the SPA (runs in CI's e2e job; needs E2E=1 + playwright).

Boots nothing itself — expects the app on E2E_BASE_URL (default localhost:8123)
with auth disabled, talking to a real broker.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("E2E") != "1", reason="set E2E=1 to run")

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8123")
MGMT = os.environ.get("E2E_MGMT_URL", "http://127.0.0.1:15672")
MGMT_AUTH = (
    os.environ.get("E2E_MGMT_USER", "queuelens"),
    os.environ.get("E2E_MGMT_PASSWORD", "queuelens"),
)


@pytest.fixture(scope="module", autouse=True)
def seeded_dlq() -> None:
    """CI's broker starts empty — the wizard test needs at least one DLQ with a message."""
    import httpx

    with httpx.Client(auth=MGMT_AUTH, timeout=10) as client:
        queue_url = f"{MGMT}/api/queues/%2F/e2e.orders.dlq"
        client.put(queue_url, json={"durable": True}).raise_for_status()
        client.post(
            f"{MGMT}/api/exchanges/%2F/amq.default/publish",
            json={
                "properties": {},
                "routing_key": "e2e.orders.dlq",
                "payload": '{"order_id": "e2e-1"}',
                "payload_encoding": "string",
            },
        ).raise_for_status()


@pytest.fixture(scope="module")
def page():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        yield page
        browser.close()


def test_dashboard_renders_live_data(page) -> None:
    page.goto(f"{BASE}/app")
    page.wait_for_selector("text=DLQ Recovery Dashboard", timeout=30_000)
    assert page.evaluate("() => (window.QL.data.queues || []).length") >= 0
    assert page.evaluate("() => !!window.QL.me")
    # the summary cards are computed from the live queue list the page loaded (the
    # Management API lags a few seconds, so compare with the same snapshot, not a refetch)
    dlqs = page.evaluate(
        "() => window.QL.data.queues.filter((q) => q.type !== 'NORMAL').map((q) => q.messages)"
    )
    card = page.locator("text=Total Messages").locator("xpath=..")
    assert card.inner_text().split("\n")[0] == str(sum(dlqs))
    card = page.locator("text=DLQ Queues").first.locator("xpath=..")
    assert card.inner_text().split("\n")[0] == str(len(dlqs))
    assert "payments.retry.dlq" not in page.inner_text("main")  # the old sample value


def test_page_load_never_previews_message_bodies(page) -> None:
    """A preview is a broker read (basic.get + requeue); load + auto-refresh stay metadata-only."""
    previews: list[str] = []
    page.on("request", lambda r: "/messages" in r.url and previews.append(r.url))
    page.goto(f"{BASE}/app")
    page.wait_for_selector("text=DLQ Recovery Dashboard", timeout=30_000)
    page.wait_for_timeout(1000)
    assert previews == []


def test_every_screen_renders(page) -> None:
    page.goto(f"{BASE}/app")
    page.wait_for_selector("text=DLQ Recovery Dashboard", timeout=30_000)
    for screen, marker in [
        ("Queues", "Showing"),
        ("Parking", "Parking Lot"),
        ("Topology", "dead-letter"),
        ("Composer", "Test Message Composer"),
        ("Audit Log", "Total Actions"),
        ("Metrics", "Prometheus integration"),
        ("Alerts", "Delivery Channels"),
        ("Configuration", "Broker Connection"),
        ("Users", "Roles"),
    ]:
        page.click(f"button:has-text('{screen}')")
        page.wait_for_selector(f"text={marker}", timeout=15_000)


def test_wizard_gates_execution_behind_confirmation(page) -> None:
    page.goto(f"{BASE}/app")
    page.wait_for_selector("text=DLQ Recovery Dashboard", timeout=30_000)
    page.click("button:has-text('Open')")
    page.wait_for_selector("text=Browse messages safely", timeout=15_000)
    # open the single-message Park wizard from the details panel
    page.click("main >> button:has-text('Park')")
    page.wait_for_selector("text=Parking Destination", timeout=15_000)
    review = page.locator("button:has-text('Review & Execute')")
    assert review.is_disabled()
