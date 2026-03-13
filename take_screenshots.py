"""Capture screenshots of the PlexMix UI for README documentation."""
import time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:3000"
OUT = "docs/screenshots"
WIDTH = 1440
HEIGHT = 900


def wait_for_app(page, timeout=10):
    """Wait until the Reflex app hydrates and renders content."""
    # Wait for any heading or main content to appear
    try:
        page.wait_for_selector("h1, h2, h3, .rt-Heading", timeout=timeout * 1000)
    except Exception:
        pass
    # Extra settle time for animations
    time.sleep(2)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ── Dark mode screenshots ──────────────────────────────────
        ctx_dark = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=2,
            color_scheme="dark",
        )
        page = ctx_dark.new_page()

        # Dashboard (dark)
        print("Capturing dashboard (dark)...")
        page.goto(f"{BASE}/dashboard", wait_until="networkidle")
        wait_for_app(page)
        page.screenshot(path=f"{OUT}/dashboard-dark.png", full_page=False)

        # Generator
        print("Capturing generator...")
        page.goto(f"{BASE}/generator", wait_until="networkidle")
        wait_for_app(page)
        page.screenshot(path=f"{OUT}/generator.png", full_page=False)

        # Library
        print("Capturing library...")
        page.goto(f"{BASE}/library", wait_until="networkidle")
        wait_for_app(page)
        page.screenshot(path=f"{OUT}/library.png", full_page=False)

        # Settings
        print("Capturing settings...")
        page.goto(f"{BASE}/settings", wait_until="networkidle")
        wait_for_app(page)
        page.screenshot(path=f"{OUT}/settings.png", full_page=False)

        ctx_dark.close()

        # ── Light mode screenshot ──────────────────────────────────
        # Reflex reads theme from localStorage("theme") on init, not CSS prefers-color-scheme.
        # Set localStorage before page JS hydrates by using addInitScript.
        ctx_light = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=2,
            color_scheme="light",
        )
        page = ctx_light.new_page()
        # This script runs before every page load in this context
        page.add_init_script("""
            localStorage.setItem('theme', 'light');
        """)

        # Dashboard (light)
        print("Capturing dashboard (light)...")
        # First navigate to set the origin for localStorage, then reload so the
        # init script's localStorage write is available when Reflex hydrates.
        page.goto(f"{BASE}/dashboard", wait_until="networkidle")
        page.reload(wait_until="networkidle")
        wait_for_app(page)
        page.screenshot(path=f"{OUT}/dashboard-light.png", full_page=False)

        ctx_light.close()
        browser.close()

    print(f"Done! Screenshots saved to {OUT}/")


if __name__ == "__main__":
    main()
