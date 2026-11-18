"""Browser tests for Settings' Save/Discard bar (headless Chromium).

webui-honest-status-and-first-run 10.4 / spec "One unsaved-changes model": the bar
must be visible without scrolling while the open section is taller than the
viewport. The real stylesheets (``assets/styles.css``, ``colors_and_type.css``,
``redesign.css``, in the app's order) and ``assets/plexmix.js`` are loaded into a
static copy of the shell and the Settings page column (``pages/settings.py``;
``test_settings_changes_render`` pins the compiled markup). Pages are served
through Playwright request interception, so no port is bound. Skipped when
Playwright or its Chromium build is missing. The live step re-checks this against
the running app.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from plexmix.ui.states.settings_state import FOCUS_AFTER_SAVEBAR_JS

sync_api = pytest.importorskip("playwright.sync_api")

_ASSETS = Path(__file__).resolve().parents[2] / "assets"
_ORIGIN = "http://pmsettings.test"
_STYLES = ("styles.css", "colors_and_type.css", "redesign.css")

_SAVEBAR = """<div id="set-savebar" class="savebar animate-slide-up">
<span class="dot dot-warning pulse" aria-hidden="true"></span>
<div class="savebar-text"><p class="savebar-title">Unsaved changes</p>
<p class="savebar-sections">In Embeddings and Audio Analysis</p></div>
<div class="savebar-actions">
<button id="set-discard" type="button" class="btn btn-3 btn-soft">Discard</button>
<button id="set-save" type="button" class="btn btn-3 btn-primary">Save</button></div></div>"""


def _page_html(section_height: int) -> str:
    links = "".join(f'<link rel="stylesheet" href="/{name}">' for name in _STYLES)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{links}
<script src="/plexmix.js"></script></head><body>
<div class="shell"><div class="main">
<main id="main-content" class="scroll" tabindex="-1"><div class="page">
<div id="col" class="rx-Stack animate-fade-in-up"
 style="display:flex;flex-direction:column;align-items:flex-start;gap:24px;width:100%">
<h1 id="main-heading" tabindex="-1">Settings</h1>
<div class="settings-tabs" style="width:100%">
<div class="settings-panel" data-state="active" tabindex="-1" id="panel"
 style="height:{section_height}px"><input id="field" aria-label="Duration"></div></div>
{_SAVEBAR}
<div id="set-save-status" role="status" aria-live="polite" aria-atomic="true"></div>
</div></div></main></div></div></body></html>"""


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser build not installed
            pytest.skip(f"Chromium for Playwright is unavailable: {type(exc).__name__}")
        yield b
        b.close()


def _open(browser: Any, width: int, height: int, section_height: int) -> Any:
    pg = browser.new_page(viewport={"width": width, "height": height}, reduced_motion="reduce")
    html = _page_html(section_height)

    def _serve(route: Any) -> None:
        name = route.request.url.rsplit("/", 1)[-1]
        if name in _STYLES:
            route.fulfill(
                body=(_ASSETS / name).read_text(encoding="utf-8"), content_type="text/css"
            )
        elif name == "plexmix.js":
            route.fulfill(
                body=(_ASSETS / "plexmix.js").read_text(encoding="utf-8"),
                content_type="application/javascript",
            )
        elif name == "settings":
            route.fulfill(body=html, content_type="text/html")
        else:  # fonts and anything else: not needed for layout
            route.fulfill(status=404, body="")

    pg.route(f"{_ORIGIN}/**", _serve)
    pg.goto(f"{_ORIGIN}/settings")
    return pg


def _rect(pg: Any, selector: str) -> dict[str, float]:
    return dict(
        pg.evaluate(f"document.querySelector('{selector}').getBoundingClientRect().toJSON()")
    )


_VIEWPORTS = [(1280, 800), (768, 600), (375, 700)]


class TestStickyBar:
    @pytest.mark.parametrize("width,height", _VIEWPORTS)
    def test_bar_is_visible_while_a_long_section_is_at_the_top(
        self, browser: Any, width: int, height: int
    ) -> None:
        pg = _open(browser, width, height, section_height=3000)
        try:
            inner = pg.evaluate("innerHeight")
            bar = _rect(pg, "#set-savebar")
            assert bar["top"] >= 0 and bar["bottom"] <= inner
            assert inner - bar["bottom"] == pytest.approx(16, abs=1)  # bottom: 16px
            for button in ("#set-save", "#set-discard"):
                rect = _rect(pg, button)
                assert 0 <= rect["top"] and rect["bottom"] <= inner, button
                assert 0 <= rect["left"] and rect["right"] <= width, button
            # The bar spans the column and never makes the page scroll sideways.
            col = _rect(pg, "#col")
            assert bar["width"] == pytest.approx(col["width"], abs=1)
            assert pg.evaluate(
                "document.getElementById('main-content').scrollWidth"
                " <= document.getElementById('main-content').clientWidth"
            )
            # Still in view half-way down the section.
            pg.evaluate("document.getElementById('main-content').scrollTop = 1200")
            bar = _rect(pg, "#set-savebar")
            assert bar["top"] >= 0 and bar["bottom"] <= inner
        finally:
            pg.close()

    def test_short_section_keeps_the_bar_in_flow(self, browser: Any) -> None:
        pg = _open(browser, 1280, 800, section_height=200)
        try:
            panel = _rect(pg, "#panel")
            bar = _rect(pg, "#set-savebar")
            assert bar["top"] == pytest.approx(panel["bottom"] + 24, abs=2)  # right below it
            assert bar["bottom"] <= pg.evaluate("innerHeight")
        finally:
            pg.close()

    def test_bar_floats_above_content_with_the_large_shadow(self, browser: Any) -> None:
        pg = _open(browser, 1280, 800, section_height=3000)
        try:
            style = pg.evaluate(
                "(() => { const s = getComputedStyle(document.getElementById('set-savebar'));"
                " return [s.position, s.zIndex, s.boxShadow, s.backgroundColor]; })()"
            )
            position, z_index, shadow, background = style
            assert position == "sticky" and int(z_index) >= 1
            assert shadow != "none"
            assert background not in ("rgba(0, 0, 0, 0)", "transparent")  # opaque surface
        finally:
            pg.close()


class TestFocusAfterTheBar:
    def test_focus_returns_to_the_open_section(self, browser: Any) -> None:
        pg = _open(browser, 1280, 800, section_height=3000)
        try:
            pg.focus("#set-save")
            # Save/Discard: React unmounts the bar with the focused button in it.
            pg.evaluate("document.getElementById('set-savebar').remove()")
            pg.evaluate(FOCUS_AFTER_SAVEBAR_JS)
            pg.wait_for_function("document.activeElement.id === 'panel'", timeout=2000)
        finally:
            pg.close()

    def test_focus_the_user_moved_elsewhere_is_left_alone(self, browser: Any) -> None:
        pg = _open(browser, 1280, 800, section_height=3000)
        try:
            pg.focus("#field")
            pg.evaluate("document.getElementById('set-savebar').remove()")
            pg.evaluate(FOCUS_AFTER_SAVEBAR_JS)
            pg.wait_for_timeout(300)
            assert pg.evaluate("document.activeElement.id") == "field"
        finally:
            pg.close()
