"""Computed-layout check: the Library bulk bar stays reachable and never shifts rows.

Spec ``webui-library-selection`` › "Bulk bar stays reachable": while tracks are
selected the bar stays visible as the table scrolls, and its appearance does not
move the rows above it. The bar (``.card.bulkbar``, assets/redesign.css) sits after
the table and is ``position: sticky; bottom: 16px`` inside the shell's ``.scroll``
container. The app stylesheets are loaded in the app's order into headless
Chromium; the markup mirrors the shell and pages/library.py's vstack (classes and
structure). Every request is served through Playwright request interception (no
port is bound, nothing reaches the network).

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
_ORIGIN = "http://bulkbar.test"
_ROWS = 50

_ROW = (
    "<tr><td><input type='checkbox' aria-label='Select Song {i}'></td>"
    "<td>Song {i}</td><td class='fg2'>Artist</td><td class='fg3'>Album</td><td>·</td></tr>"
)

# Shell: .shell > .main > main.scroll > .page > vstack (Radix Flex column, gap 16px,
# align start) holding the action bar, filter form, table, [bulk bar], pager.
_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    + "".join(f"<link rel='stylesheet' href='/{name}'>" for name in _STYLESHEETS)
    + """</head><body>
<div class="shell"><div class="main">
 <main class="scroll" id="main-content" tabindex="-1"><div class="page">
  <div class="rx-Stack" id="stack"
       style="display:flex;flex-direction:column;align-items:flex-start;gap:16px;width:100%">
   <div class="lib-actions"><div class="card" style="height:68px;width:100%"></div>
     <div role="alert"></div><div role="status"></div></div>
   <form class="filterbar" id="lib-filters"><input class="minisel" placeholder="Genre"></form>
   <div class="tbl-wrap" id="table" style="width:100%"><table class="tbl"><thead><tr>
     <th></th><th>Title</th><th>Artist</th><th>Album</th><th>Enrichment</th></tr></thead>
     <tbody>"""
    + "".join(_ROW.format(i=i) for i in range(_ROWS))
    + """</tbody></table></div>
   <div id="pager" style="display:flex;width:100%;height:32px">Page 1</div>
  </div>
 </div></main>
</div></div>
<script>
  // What the bulk bar renders when the selection becomes non-empty (pages/library.py).
  window.showBar = function () {
    if (document.getElementById('bar')) return;
    var bar = document.createElement('div');
    bar.id = 'bar';
    bar.className = 'card bulkbar';
    bar.innerHTML = "<span class='bulkbar-count'>50 selected · 50 on other pages</span>" +
      "<button class='btn btn-sm btn-soft' type='button'>Tag</button>" +
      "<button class='btn btn-sm btn-soft' type='button'>Delete</button>" +
      "<button class='btn btn-sm btn-ghost' type='button' aria-label='Clear selection'>Clear</button>";
    document.getElementById('stack').insertBefore(bar, document.getElementById('pager'));
  };
  window.rect = function (el) {
    var r = el.getBoundingClientRect();
    return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height};
  };
</script>
</body></html>"""
)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser build not installed
            pytest.skip(f"Chromium for Playwright is unavailable: {type(exc).__name__}")
        yield b
        b.close()


@pytest.fixture(params=[(1440, 900), (1024, 640)], ids=["1440x900", "1024x640"])
def page(browser: Any, request: Any) -> Iterator[Any]:
    width, height = request.param
    pg = browser.new_page(viewport={"width": width, "height": height})

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
            route.abort()  # fonts do not affect the sticky behaviour
        else:
            route.fulfill(body=_PAGE, content_type="text/html")

    pg.route("**/*", _serve)
    pg.goto(f"{_ORIGIN}/library")
    yield pg
    pg.close()


def _rect(page: Any, selector: str) -> dict[str, float]:
    return page.evaluate("(s) => window.rect(document.querySelector(s))", selector)


def test_appearing_bar_does_not_move_the_rows(page: Any) -> None:
    first_before = _rect(page, "#table tbody tr:first-child")
    page.evaluate("window.showBar()")
    first_after = _rect(page, "#table tbody tr:first-child")
    assert first_after == first_before


def test_bar_is_in_view_after_selecting_a_low_row(page: Any) -> None:
    inner_height = page.evaluate("innerHeight")
    # Scroll a row near the bottom of the table into view, as a user selecting it would.
    page.evaluate(
        "document.querySelector('#table tbody tr:nth-child(40)').scrollIntoView({block: 'end'})"
    )
    row_before = _rect(page, "#table tbody tr:nth-child(40)")
    page.evaluate("window.showBar()")
    bar = _rect(page, "#bar")
    assert bar["height"] > 0
    assert 0 <= bar["top"] and bar["bottom"] <= inner_height
    # The row (and everything above it) stayed where it was.
    assert _rect(page, "#table tbody tr:nth-child(40)") == row_before


def test_bar_sticks_while_the_table_scrolls(page: Any) -> None:
    inner_height = page.evaluate("innerHeight")
    page.evaluate("window.showBar()")
    for top in (0, 400, 900):
        page.evaluate("(t) => { document.getElementById('main-content').scrollTop = t; }", top)
        table = _rect(page, "#table")
        bar = _rect(page, "#bar")
        assert 0 <= bar["top"] and bar["bottom"] <= inner_height, (top, bar)
        if table["bottom"] > inner_height:
            # Stuck 16px above the viewport bottom while rows continue below it.
            assert abs(bar["bottom"] - (inner_height - 16)) < 1, (top, bar)


def test_at_the_end_the_bar_rests_below_the_last_row(page: Any) -> None:
    page.evaluate("window.showBar()")
    page.evaluate(
        "() => { const s = document.getElementById('main-content'); s.scrollTop = s.scrollHeight; }"
    )
    last = _rect(page, "#table tbody tr:last-child")
    bar = _rect(page, "#bar")
    pager = _rect(page, "#pager")
    assert last["bottom"] <= bar["top"]  # the last row is never hidden behind the bar
    assert bar["bottom"] <= pager["top"]


def test_bar_uses_sticky_positioning_and_tokens(page: Any) -> None:
    page.evaluate("window.showBar()")
    style = page.evaluate(
        "(() => { const c = getComputedStyle(document.getElementById('bar'));"
        " return {position: c.position, bottom: c.bottom, zIndex: c.zIndex,"
        " wrap: c.flexWrap, shadow: c.boxShadow}; })()"
    )
    assert style["position"] == "sticky"
    assert style["bottom"] == "16px"
    assert style["zIndex"] == "2"
    assert style["wrap"] == "wrap"
    assert style["shadow"] != "none"
