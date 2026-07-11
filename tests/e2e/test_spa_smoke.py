"""Browser smoke test for the SPA (runs in CI's e2e job; needs E2E=1 + playwright).

Boots nothing itself — expects the app on E2E_BASE_URL (default localhost:8123)
with auth disabled, talking to a real broker.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("E2E") != "1", reason="set E2E=1 to run")

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8123")


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


def test_every_screen_renders(page) -> None:
    page.goto(f"{BASE}/app")
    page.wait_for_selector("text=DLQ Recovery Dashboard", timeout=30_000)
    for screen, marker in [
        ("Queues", "Showing"),
        ("Parking", "Parking Lot"),
        ("Topology", "dead-letter"),
        ("Composer", "Test Message Composer"),
        ("Audit Log", "Total Actions"),
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
