from playwright.sync_api import sync_playwright

from vtc.pw import type_like_user


def test_login_input_does_not_trigger_domain_completion_mid_typing():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content('''<input id="account" type="email" oninput="if (!this.value.includes('@')) this.value += '@vtc.edu.hk'">''')
        account = page.locator("#account")
        expected = "testuser@stu.vtc.edu.hk"
        type_like_user(page, account, expected)
        assert account.input_value() == expected
        browser.close()
