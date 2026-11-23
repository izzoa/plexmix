"""Browser tests for the Generator's lost-connection notice (headless Chromium, no port bound).

Spec ``webui-generator-showpiece`` › "Lost-connection notice during generation". When the page
cannot reach the server, the thinking stage (pages/generator.py) withdraws its progress row and
shows a quiet "Reconnecting" line, then an alert. This module loads the real
``assets/plexmix.js`` and the three real stylesheets, in the app's order, into a static page that
holds the stage's markup, and simulates what React does to the DOM: ``goRetrying()`` (the row
leaves, the quiet line appears), ``goLost()`` (the stage is marked offline, the alert appears) and
``goOnline()`` (both leave, the row returns). It proves what the scripts and the styles do:

- focus that was on the removed Cancel button moves to the stage heading, and focus anywhere else
  is never moved (``plexmix.js`` section 8);
- the particle field keeps going at the quiet step, holds still at the alert step and resumes
  (``plexmix.js`` section 3);
- the quiet line takes exactly the row's box, and the alert fits narrow widths and 200% zoom.

It does NOT show what the app does on a real reconnect, which rebuilds the whole page through the
boot screen (design D4); live check 6.4 of the change looks at that. The ``goOnline()`` focus
assertions only prove that section 8 moves nothing when the row returns.

Pages are served through Playwright request interception, so no port is bound and nothing reaches
the network. The module is skipped when Playwright or its Chromium build is missing (CI installs
neither).
"""

from __future__ import annotations

import ast
import html
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

_REPO = Path(__file__).resolve().parents[1]
_ASSETS = _REPO / "assets"
_GENERATOR_PAGE = _REPO / "src" / "plexmix" / "ui" / "pages" / "generator.py"
_SCRIPT = _ASSETS / "plexmix.js"
# Same order as plexmix_ui/plexmix_ui.py (rx.App stylesheets).
_STYLESHEETS = ("styles.css", "colors_and_type.css", "redesign.css")
_ORIGIN = "http://pmoffline.test"
_SETTLE_MS = 400  # pmFocus.when polls for up to ~60 frames


def _page_copy(name: str) -> str:
    """A string constant of pages/generator.py, read without importing Reflex."""
    tree = ast.parse(_GENERATOR_PAGE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return str(ast.literal_eval(node.value))
    raise AssertionError(f"{name} is not a string constant of pages/generator.py")


_RECONNECTING = html.escape(_page_copy("RECONNECTING_TEXT"))
_LOST_TITLE = html.escape(_page_copy("LOST_CONNECTION_TITLE"))
_LOST_BODY = html.escape(_page_copy("LOST_CONNECTION_BODY"))

# The thinking stage inside the shell (components/navbar.py: .shell > .rail + .main > .scroll),
# in the order of _thinking_stage: heading, canvas, phase track, the two notice regions, the
# progress row (the slot), the phase status and the log.
_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    + "".join(f"<link rel='stylesheet' href='/{name}'>" for name in _STYLESHEETS)
    + """<script>window.__draws = 0;
(function () {
  var clear = CanvasRenderingContext2D.prototype.clearRect;
  CanvasRenderingContext2D.prototype.clearRect = function () {
    window.__draws++;
    return clear.apply(this, arguments);
  };
})();
</script>
<script src="/plexmix.js"></script></head><body>
<div class="shell">
 <nav class="rail"><button id="rail-a" class="rail-item" type="button">A</button>
   <button id="rail-b" class="rail-item" type="button">B</button></nav>
 <div class="main"><main class="scroll" id="main-content" tabindex="-1">
  <div class="think" id="stage"><div class="think-inner">
   <div class="think-head"><h1 id="main-heading" tabindex="-1" class="think-query">Curating
     <b>"rainy day melancholy"</b></h1></div>
   <div class="viz-wrap" data-phase="1"><canvas id="thinkviz"></canvas>
     <div class="viz-phase-label">Searching for candidate tracks...</div></div>
   <div class="phase-track">
     <div class="phase-node done"><div class="phase-line"><div class="fill"></div></div>
       <div class="phase-dot"></div><div class="phase-label">Embed vibe</div></div>
     <div class="phase-node active"><div class="phase-line"><div class="fill"></div></div>
       <div class="phase-dot"></div><div class="phase-label">Vector search</div></div>
     <div class="phase-node"><div class="phase-line"><div class="fill"></div></div>
       <div class="phase-dot"></div><div class="phase-label">Diversity</div></div>
     <div class="phase-node"><div class="phase-line"><div class="fill"></div></div>
       <div class="phase-dot"></div><div class="phase-label">Order set</div></div>
   </div>
   <div id="gen-reconnect-region" role="status"></div>
   <div id="gen-offline-region" role="alert"></div>
   <div id="row-slot"></div>
   <div id="gen-phase-status" role="status" class="sr-only">Vector search</div>
   <div class="genlog"><div class="ln"><span>Starting playlist generation...</span></div>
     <div class="ln"><span>Searching for candidate tracks...</span></div></div>
  </div></div>
 </main></div>
</div>
<script>
// What React does to the stage (the markup mirrors pages/generator.py).
var ROW = '<div class="gen-progress"><div class="pbar" role="progressbar">' +
  '<div class="pfill" style="width:40%"></div></div><span class="pct">40%</span>' +
  '<button id="cancel" class="btn btn-sm btn-ghost" type="button">Cancel</button></div>';
function el(id) { return document.getElementById(id); }
window.goOnline = function () {
  el('stage').className = 'think';
  el('gen-reconnect-region').innerHTML = '';
  el('gen-offline-region').innerHTML = '';
  el('row-slot').innerHTML = ROW;
};
window.goRetrying = function () {
  el('row-slot').innerHTML = '';
  el('gen-reconnect-region').innerHTML =
    '<div id="gen-reconnect" class="gen-reconnect">"""
    + _RECONNECTING
    + """</div>';
};
window.goLost = function () {
  el('stage').className = 'think is-offline';
  el('row-slot').innerHTML = '';
  el('gen-reconnect-region').innerHTML = '';
  el('gen-offline-region').innerHTML =
    '<div id="gen-offline" class="callout callout-error"><div class="c-ico">!</div>' +
    '<div class="c-main"><div class="c-title">"""
    + _LOST_TITLE
    + """</div>' +
    '<div class="c-body">"""
    + _LOST_BODY
    + """</div></div></div>';
};
// The whole stage leaves for another stage (the results), which has its own heading.
window.goResults = function () {
  el('stage').outerHTML = '<div id="results"><h1 id="main-heading" tabindex="-1">Results</h1></div>';
};
window.goOnline();
window.rect = function (selector) {
  var r = document.querySelector(selector).getBoundingClientRect();
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


def _open(browser: Any, width: int, height: int) -> Any:
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
        elif name == "plexmix.js":
            route.fulfill(
                body=_SCRIPT.read_text(encoding="utf-8"), content_type="application/javascript"
            )
        elif name.endswith((".woff2", ".woff", ".ttf")):
            route.abort()  # fonts do not affect what these tests measure
        else:
            route.fulfill(body=_PAGE, content_type="text/html")

    pg.route("**/*", _serve)
    pg.goto(f"{_ORIGIN}/generator")
    pg.wait_for_timeout(300)  # the canvas starts once the DOM is parsed
    return pg


@pytest.fixture()
def page(browser: Any) -> Iterator[Any]:
    pg = _open(browser, 1280, 800)
    yield pg
    pg.close()


def _active(pg: Any) -> str:
    return str(pg.evaluate("document.activeElement.id || document.activeElement.tagName"))


def _step(pg: Any, name: str) -> None:
    pg.evaluate(f"{name}()")
    pg.wait_for_timeout(_SETTLE_MS)


def _draws(pg: Any, ms: int = 500) -> int:
    pg.evaluate("window.__draws = 0")
    pg.wait_for_timeout(ms)
    return int(pg.evaluate("window.__draws"))


def _top(pg: Any, selector: str) -> float:
    return float(pg.evaluate("(s) => window.rect(s).top", selector))


class TestLostConnectionFocus:
    def test_focus_on_cancel_moves_to_the_heading_when_the_row_leaves(self, page: Any) -> None:
        page.focus("#cancel")
        _step(page, "goRetrying")
        assert _active(page) == "main-heading"

    def test_the_alert_and_the_return_leave_it_there(self, page: Any) -> None:
        page.focus("#cancel")
        _step(page, "goRetrying")
        _step(page, "goLost")
        assert _active(page) == "main-heading"
        _step(page, "goOnline")
        assert _active(page) == "main-heading"

    def test_focus_the_user_still_has_is_never_moved(self, page: Any) -> None:
        for target in ("#main-heading", "#rail-a", "#rail-b"):
            _step(page, "goOnline")
            page.focus(target)
            for step in ("goRetrying", "goLost", "goOnline"):
                _step(page, step)
                assert _active(page) == target.lstrip("#"), (target, step)

    def test_a_drop_that_goes_straight_to_the_alert_also_moves_lost_focus_once(
        self, page: Any
    ) -> None:
        page.focus("#cancel")
        _step(page, "goLost")  # the row leaves in the same render
        assert _active(page) == "main-heading"

    def test_nothing_happens_when_the_whole_stage_leaves(self, page: Any) -> None:
        # The stage is replaced by another one (a finished generation) that has its own
        # heading: the stage's mount script places focus there, not this watcher.
        page.focus("#cancel")
        _step(page, "goResults")
        assert _active(page) == "BODY"


class TestLostConnectionCanvas:
    def test_canvas_keeps_going_at_the_quiet_step(self, page: Any) -> None:
        assert _draws(page) > 5
        _step(page, "goRetrying")
        assert _draws(page) > 5

    def test_canvas_holds_still_at_the_alert_step_and_resumes(self, page: Any) -> None:
        _step(page, "goRetrying")
        _step(page, "goLost")
        assert _draws(page) == 0
        _step(page, "goOnline")
        assert _draws(page) > 5

    def test_stale_progress_message_is_hidden_at_the_alert_step_only(self, page: Any) -> None:
        assert page.is_visible(".viz-phase-label")
        _step(page, "goRetrying")
        assert page.is_visible(".viz-phase-label")
        _step(page, "goLost")
        assert not page.is_visible(".viz-phase-label")
        _step(page, "goOnline")
        assert page.is_visible(".viz-phase-label")

    def test_active_phase_is_lit_until_the_alert_step(self, page: Any) -> None:
        def glow() -> str:
            return str(
                page.evaluate(
                    "getComputedStyle(document.querySelector('.phase-node.active .phase-dot'))"
                    ".boxShadow"
                )
            )

        assert glow() != "none"
        _step(page, "goRetrying")
        assert glow() != "none"
        _step(page, "goLost")
        assert glow() == "none"
        _step(page, "goOnline")
        assert glow() != "none"


class TestLostConnectionLayout:
    def test_quiet_line_takes_exactly_the_rows_box(self, page: Any) -> None:
        row_log_top = _top(page, ".genlog")
        row = page.evaluate("window.rect('.gen-progress')")
        _step(page, "goRetrying")
        line = page.evaluate("window.rect('#gen-reconnect')")
        assert abs(_top(page, ".genlog") - row_log_top) <= 1
        # The line sits where the row sat, and is as tall as the row's 32px button.
        assert abs(line["top"] - row["top"]) <= 1
        assert abs(line["height"] - row["height"]) <= 1
        _step(page, "goOnline")
        assert abs(_top(page, ".genlog") - row_log_top) <= 1

    def test_quiet_line_is_not_styled_as_an_error(self, page: Any) -> None:
        _step(page, "goRetrying")
        assert page.evaluate("document.querySelector('#gen-reconnect').closest('.callout')") is None
        look = page.evaluate(
            """() => {
                const colour = (token) => {
                    const probe = document.createElement('span');
                    probe.style.color = 'var(' + token + ')';
                    document.body.appendChild(probe);
                    const value = getComputedStyle(probe).color;
                    probe.remove();
                    return value;
                };
                const line = getComputedStyle(document.querySelector('#gen-reconnect'));
                return {
                    color: line.color,
                    secondary: colour('--fg-2'),
                    error: colour('--error-fg'),
                    background: line.backgroundColor,
                    animation: line.animationName,
                };
            }"""
        )
        assert look["color"] == look["secondary"], look  # plain secondary text
        assert look["color"] != look["error"], look
        assert look["background"] == "rgba(0, 0, 0, 0)", look  # no filled background
        assert look["animation"] == "none", look

    @pytest.mark.parametrize(
        ("width", "height"),
        [(320, 640), (640, 400), (720, 450)],
        ids=["320x640", "640x400-zoom200", "720x450-zoom200"],
    )
    def test_alert_fits_narrow_widths_and_zoom(self, browser: Any, width: int, height: int) -> None:
        pg = _open(browser, width, height)
        try:
            pg.evaluate("goLost()")
            pg.wait_for_timeout(200)
            metrics = pg.evaluate(
                """() => {
                    const scroll = document.querySelector('.scroll');
                    const callout = document.querySelector('#gen-offline');
                    const body = callout.querySelector('.c-body');
                    return {
                        innerWidth: window.innerWidth,
                        docScroll: document.documentElement.scrollWidth,
                        scrollOverflow: scroll.scrollWidth - scroll.clientWidth,
                        calloutOverflow: callout.scrollWidth - callout.clientWidth,
                        bodyOverflow: body.scrollWidth - body.clientWidth,
                        calloutLeft: callout.getBoundingClientRect().left,
                        calloutRight: callout.getBoundingClientRect().right,
                        scrollRight: scroll.getBoundingClientRect().right,
                        bodyRight: body.getBoundingClientRect().right,
                    };
                }"""
            )
            assert metrics["docScroll"] <= metrics["innerWidth"], metrics
            assert metrics["scrollOverflow"] <= 0, metrics
            assert metrics["calloutOverflow"] <= 0, metrics
            assert metrics["bodyOverflow"] <= 0, metrics
            assert 0 <= metrics["calloutLeft"], metrics
            assert metrics["calloutRight"] <= metrics["innerWidth"], metrics
            assert metrics["calloutRight"] <= metrics["scrollRight"] + 0.5, metrics
            assert metrics["bodyRight"] <= metrics["calloutRight"] + 0.5, metrics
        finally:
            pg.close()
