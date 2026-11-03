"""Real-browser test of the Django admin against the running Compose stack.

Run (stack up, an admin user created, see README):
    E2E_ADMIN_USER=... E2E_ADMIN_PASSWORD=... uv run --env-file .env pytest e2e
Each run leaves one new user behind in that stack's database; `docker compose down -v` clears it.
"""

import uuid

from playwright.sync_api import Page, expect


def log_in(page: Page, base_url: str, user: str, password: str) -> None:
    page.goto(f"{base_url}/admin/login/")
    page.get_by_label("Username:").fill(user)
    page.get_by_label("Password:").fill(password)
    page.get_by_role("button", name="Log in").click()


def test_login_page_is_styled(page: Page, base_url: str):
    page.goto(f"{base_url}/admin/login/")
    assert page.evaluate("document.styleSheets.length") > 0
    # Django's admin header is blue only when its CSS was actually served.
    header_bg = page.locator("#header").evaluate("el => getComputedStyle(el).backgroundColor")
    assert header_bg != "rgba(0, 0, 0, 0)", "admin header has no background: CSS was not loaded"


def test_wrong_password_shows_error(page: Page, base_url: str, admin_credentials):
    user, _ = admin_credentials
    log_in(page, base_url, user, "definitely-not-the-password")
    expect(page.locator(".errornote")).to_be_visible()
    expect(page).to_have_url(f"{base_url}/admin/login/")  # stays on the login page


def test_login_add_user_and_see_in_list(page: Page, base_url: str, admin_credentials):
    user, password = admin_credentials
    log_in(page, base_url, user, password)
    expect(page.locator("#site-name")).to_be_visible()

    new_name = f"e2e-{uuid.uuid4().hex[:8]}"
    page.goto(f"{base_url}/admin/accounts/user/add/")
    page.locator("#id_username").fill(new_name)
    page.locator("#id_password1").fill("E2e-pass-12345")
    page.locator("#id_password2").fill("E2e-pass-12345")
    page.get_by_role("button", name="Save", exact=True).click()

    page.goto(f"{base_url}/admin/accounts/user/")
    expect(page.locator("th.field-username a", has_text=new_name)).to_be_visible()
