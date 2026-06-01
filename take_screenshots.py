"""Capture screenshots of the PlexMix UI for README documentation.

The UI is light-first; theme is forced per-shot via localStorage('theme', ...)
before load (Reflex/next-themes reads it on init).
"""

import time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:3000"
OUT = "docs/screenshots"
WIDTH = 1440
HEIGHT = 900

# (filename, theme, route)
SHOTS = [
    ("dashboard-light", "light", "/dashboard"),
    ("dashboard-dark", "dark", "/dashboard"),
    ("generator", "light", "/generator"),
    ("library", "light", "/library"),
    ("settings", "light", "/settings"),
]


def wait_for_shell(page, timeout=30):
    # Wait for the authenticated shell (.rail) — avoids the brief login flash
    # before on_load flips is_authenticated. Then settle for on_load data
    # (dashboard stats, library tracks) to populate.
    try:
        page.wait_for_selector(".rail", state="visible", timeout=timeout * 1000)
    except Exception:
        time.sleep(5)
    time.sleep(4)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, theme, route in SHOTS:
            ctx = browser.new_context(
                viewport={"width": WIDTH, "height": HEIGHT},
                device_scale_factor=2,
            )
            ctx.add_init_script(
                "try{localStorage.setItem('theme','%s');}catch(e){}" % theme
            )
            page = ctx.new_page()
            print(f"Capturing {name} ({theme}) {route} ...")
            page.goto(f"{BASE}{route}", wait_until="networkidle")
            page.reload(wait_until="networkidle")  # apply theme from localStorage
            wait_for_shell(page)
            if route == "/settings":
                try:
                    page.get_by_text("Appearance", exact=True).first.click(timeout=3000)
                    time.sleep(1.5)
                except Exception:
                    pass
            page.screenshot(path=f"{OUT}/{name}.png", full_page=False)
            ctx.close()
        browser.close()
    print(f"Done! Screenshots saved to {OUT}/")


if __name__ == "__main__":
    main()
