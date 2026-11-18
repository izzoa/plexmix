"""Computed-style check: ``.minisel`` filter placeholders use ``--fg-3`` at AA contrast.

The Library Genre, From/To year and Tag filters and the Tagging Genre and Stale
days filters are ``input.minisel`` fields whose placeholder is their only visible
label (webui-input-responsiveness, task 6.3; spec "Filter placeholders meet text
contrast"). The app stylesheets are loaded in the app's order into headless
Chromium, every request is served through Playwright request interception (no
port is bound, nothing reaches the network), and each placeholder is measured
against its field background in light and dark mode.

Skipped when Playwright or its Chromium build is missing (CI installs neither).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

_ASSETS = Path(__file__).resolve().parents[2] / "assets"
# Same order as plexmix_ui/plexmix_ui.py (rx.App stylesheets).
_STYLESHEETS = ("styles.css", "colors_and_type.css", "redesign.css")
_ORIGIN = "http://contrast.test"

# The filter markup as pages/library.py and pages/tagging.py render it (classes and types).
_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    + "".join(f"<link rel='stylesheet' href='/{name}'>" for name in _STYLESHEETS)
    + """</head><body>
<form class="filterbar" id="library-filters">
  <div class="search"><input id="lib-search" placeholder="Search tracks…"></div>
  <input class="minisel" id="lib-genre" placeholder="Genre" style="max-width:160px">
  <input class="minisel lib-year" id="lib-year-min" type="number" placeholder="From year">
  <input class="minisel lib-year" id="lib-year-max" type="number" placeholder="To year">
  <input class="minisel" id="lib-tag" placeholder="Tag" style="max-width:160px">
</form>
<form class="filterbar" id="tagging-filters">
  <input class="minisel" id="tag-genre" placeholder="Genre" style="max-width:160px">
  <input class="minisel" id="tag-stale" type="number" placeholder="Stale days"
         style="max-width:120px">
  <select class="minisel" id="sync-mode"><option>Incremental sync</option></select>
</form>
<span id="fg3-probe" style="color: var(--fg-3)">muted</span>
</body></html>"""
)

_FIELDS = ("lib-genre", "lib-year-min", "lib-year-max", "lib-tag", "tag-genre", "tag-stale")

_MEASURE_JS = """(ids) => {
  const rgb = (s) => s.match(/[\\d.]+/g).slice(0, 4).map(Number);
  const lum = ([r, g, b]) => {
    const ch = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
  };
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const fg3 = getComputedStyle(document.getElementById('fg3-probe')).color;
  return ids.map((id) => {
    const el = document.getElementById(id);
    const ph = getComputedStyle(el, '::placeholder');
    const bg = getComputedStyle(el).backgroundColor;
    return {
      id, fg3, placeholder: ph.color, background: bg,
      bgAlpha: rgb(bg).length === 4 ? rgb(bg)[3] : 1,
      ratio: ratio(rgb(ph.color), rgb(bg)),
      cursor: getComputedStyle(el).cursor,
      width: el.getBoundingClientRect().width,
      clipped: el.scrollWidth > el.clientWidth,
    };
  });
}"""


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser build not installed
            pytest.skip(f"Chromium for Playwright is unavailable: {type(exc).__name__}")
        yield b
        b.close()


@pytest.fixture()
def page(browser: Any) -> Iterator[Any]:
    pg = browser.new_page()

    def _serve(route: Any) -> None:
        url = route.request.url
        name = url.rsplit("/", 1)[-1]
        if not url.startswith(_ORIGIN):
            route.abort()
        elif name in _STYLESHEETS:
            route.fulfill(
                body=(_ASSETS / name).read_text(encoding="utf-8"), content_type="text/css"
            )
        elif name.endswith(".woff2"):
            route.abort()  # fonts do not affect colours
        else:
            route.fulfill(body=_PAGE, content_type="text/html")

    pg.route("**/*", _serve)
    pg.goto(f"{_ORIGIN}/library")
    yield pg
    pg.close()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_minisel_placeholders_use_fg3_at_aa_contrast(page: Any, mode: str) -> None:
    if mode == "dark":
        page.evaluate("document.documentElement.classList.add('dark')")
    results = page.evaluate(_MEASURE_JS, list(_FIELDS))
    for r in results:
        assert r["placeholder"] == r["fg3"], r  # resolves to --fg-3
        assert r["bgAlpha"] == 1, r  # an opaque field background (--surface-raised)
        assert r["ratio"] >= 4.5, r
        assert r["cursor"] == "text", r  # input.minisel is a text field


def test_dark_and_light_differ(page: Any) -> None:
    """Guard against measuring the same theme twice."""
    light = page.evaluate(_MEASURE_JS, ["lib-genre"])[0]
    page.evaluate("document.documentElement.classList.add('dark')")
    dark = page.evaluate(_MEASURE_JS, ["lib-genre"])[0]
    assert light["background"] != dark["background"]
    assert light["placeholder"] != dark["placeholder"]


def test_select_minisel_keeps_pointer(page: Any) -> None:
    cursor = page.evaluate("getComputedStyle(document.getElementById('sync-mode')).cursor")
    assert cursor == "pointer"


def test_year_fields_are_about_96px_and_fit_their_placeholder(page: Any) -> None:
    results = page.evaluate(_MEASURE_JS, ["lib-year-min", "lib-year-max"])
    for r in results:
        assert r["width"] == 96, r
        assert not r["clipped"], r
