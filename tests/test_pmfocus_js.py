"""Browser tests for the pmFocus helper in ``assets/plexmix.js`` (headless Chromium).

They load the real script into a static page and simulate what React does to the
DOM. Pages are served through Playwright request interception, so no port is
bound. The module is skipped when Playwright or its Chromium build is missing
(CI installs neither).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

_SCRIPT = Path(__file__).resolve().parents[1] / "assets" / "plexmix.js"
_ORIGIN = "http://pmfocus.test"

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script src="/plexmix.js"></script></head><body>
<nav><button id="rail-a" type="button">A</button></nav>
<main id="main-content" tabindex="-1">
<h1 id="main-heading" tabindex="-1">History</h1>
<ul id="grid">
<li><button id="pl-card-3" type="button">Playlist C</button></li>
<li><button id="pl-card-2" type="button">Playlist B</button></li>
<li><button id="pl-card-1" type="button">Playlist A</button></li>
</ul>
<div id="slot"></div>
</main></body></html>"""


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
        if route.request.url.endswith("/plexmix.js"):
            route.fulfill(
                body=_SCRIPT.read_text(encoding="utf-8"), content_type="application/javascript"
            )
        else:
            route.fulfill(body=_PAGE, content_type="text/html")

    pg.route(f"{_ORIGIN}/**", _serve)
    pg.goto(f"{_ORIGIN}/history")
    yield pg
    pg.close()


def _active(pg: Any) -> str:
    return str(pg.evaluate("document.activeElement.id || document.activeElement.tagName"))


def _open_detail_then_confirm(pg: Any) -> None:
    """Card -> detail dialog -> Delete -> nested alert dialog (focus inside each)."""
    pg.evaluate(
        """() => {
        const d = document.createElement('div'); d.setAttribute('role', 'dialog'); d.id = 'det';
        d.innerHTML = '<button id="del" type="button">Delete</button>';
        document.getElementById('slot').appendChild(d); document.getElementById('del').focus();
        const c = document.createElement('div'); c.setAttribute('role', 'alertdialog');
        c.id = 'conf'; c.innerHTML = '<button id="ok" type="button">Delete</button>';
        document.body.appendChild(c); document.getElementById('ok').focus();
    }"""
    )


def _close_both(pg: Any) -> None:
    pg.evaluate(
        """() => { document.getElementById('conf').remove(); pmFocus.restore();
                   document.getElementById('det').remove(); pmFocus.restore(); }"""
    )


class TestRestoreAfterDelete:
    def test_index_keyed_reuse_of_the_opener_goes_to_main_heading(self, page: Any) -> None:
        page.focus("#pl-card-3")
        _open_detail_then_confirm(page)
        # React keyed by index: li 0 is re-pointed at playlist B, the last li is removed.
        page.evaluate(
            """() => { const b = document.getElementById('pl-card-3');
                       b.id = 'pl-card-2'; b.textContent = 'Playlist B';
                       const lis = document.querySelectorAll('#grid li');
                       lis[1].querySelector('button').id = 'pl-card-1';
                       lis[2].remove(); }"""
        )
        _close_both(page)
        assert _active(page) == "main-heading"

    def test_opener_removed_goes_to_main_heading(self, page: Any) -> None:
        page.focus("#pl-card-1")
        _open_detail_then_confirm(page)
        page.evaluate("document.getElementById('pl-card-1').parentElement.remove()")
        _close_both(page)
        assert _active(page) == "main-heading"

    def test_unchanged_opener_gets_focus_back(self, page: Any) -> None:
        page.focus("#pl-card-3")
        page.evaluate(
            """() => { const d = document.createElement('div'); d.setAttribute('role', 'dialog');
                d.id = 'det'; d.innerHTML = '<button id="x" type="button">Close</button>';
                document.getElementById('slot').appendChild(d); document.getElementById('x').focus();
                d.remove(); pmFocus.restore(); }"""
        )
        assert _active(page) == "pl-card-3"


class TestRouteChange:
    def test_outgoing_heading_is_not_focused(self, page: Any) -> None:
        # Focus is already lost (the focused control unmounted) when the router pushes.
        page.focus("#rail-a")
        page.evaluate("document.getElementById('rail-a').remove()")
        page.evaluate("history.pushState(history.state, '', '/library')")
        assert _active(page) == "BODY"  # the old page's heading was skipped
        page.evaluate(
            """() => { document.getElementById('main-heading').remove();
                const h = document.createElement('h1'); h.id = 'main-heading'; h.tabIndex = -1;
                h.textContent = 'Library'; document.getElementById('main-content').prepend(h); }"""
        )
        page.wait_for_function("document.activeElement.textContent === 'Library'", timeout=2000)

    def test_heading_reused_in_place_is_focused_when_the_poll_expires(self, page: Any) -> None:
        page.focus("#rail-a")
        page.evaluate("document.getElementById('rail-a').remove()")
        page.evaluate("history.pushState(history.state, '', '/library')")
        page.wait_for_function("document.activeElement.id === 'main-heading'", timeout=6000)

    def test_route_change_does_not_move_focus_that_was_not_lost(self, page: Any) -> None:
        page.focus("#pl-card-2")
        page.evaluate("history.pushState(history.state, '', '/library')")
        page.wait_for_timeout(150)
        assert _active(page) == "pl-card-2"


class TestWhenExclude:
    def test_excluded_match_is_skipped_for_a_later_one(self, page: Any) -> None:
        page.evaluate(
            """() => { pmFocus.when('#main-heading',
                         {force: true, exclude: document.getElementById('main-heading')});
                setTimeout(() => { const h = document.createElement('h2'); h.id = 'main-heading';
                    h.textContent = 'New'; document.getElementById('slot').appendChild(h); }, 50); }"""
        )
        page.wait_for_function("document.activeElement.textContent === 'New'", timeout=2000)


# A stand-in for one Radix layer: it handles Escape on document in the capture phase,
# marks it handled (preventDefault) and closes, as DismissableLayer does. ``kind`` is the
# layer's content: a tooltip (role=tooltip inside the popper wrapper), a menu, or a dialog.
_LAYER = """(kind) => {
  const wrap = document.createElement('div');
  if (kind === 'dialog') {
    wrap.setAttribute('role', 'dialog'); wrap.setAttribute('data-state', 'open');
  } else {
    wrap.setAttribute('data-radix-popper-content-wrapper', '');
    wrap.innerHTML = kind === 'tooltip'
      ? '<div class="rt-TooltipContent">Library · g l<span role="tooltip">Library</span></div>'
      : '<div role="menu"><div role="menuitem">Item</div></div>';
  }
  document.body.appendChild(wrap);
  document.addEventListener('keydown', function onKey(e) {
    if (e.key !== 'Escape' || !wrap.isConnected) return;
    e.preventDefault(); wrap.remove(); document.removeEventListener('keydown', onKey, true);
  }, true);
}"""

_EDITOR = """() => {
  document.getElementById('slot').innerHTML = '<div data-pm-escape-cancel>' +
    '<input id="ed" value="indie pop"><button id="cancel" data-pm-cancel type="button">' +
    'Cancel</button></div>';
  window.cancelled = 0;
  document.getElementById('cancel').addEventListener('click', () => { window.cancelled++; });
  document.getElementById('ed').focus();
}"""


class TestEscapeCancel:
    def test_escape_in_an_inline_editor_cancels_it(self, page: Any) -> None:
        page.evaluate(_EDITOR)
        page.keyboard.press("Escape")
        assert page.evaluate("window.cancelled") == 1

    def test_escape_still_cancels_when_only_a_hover_label_took_it(self, page: Any) -> None:
        """A rail label (Radix tooltip) open under the resting pointer closes on that Escape
        and marks it handled; the editor still cancels."""
        page.evaluate(_EDITOR)
        page.evaluate(_LAYER, "tooltip")
        page.keyboard.press("Escape")
        assert page.evaluate("window.cancelled") == 1
        assert (
            page.evaluate("!!document.querySelector('[data-radix-popper-content-wrapper]')")
            is False
        )

    @pytest.mark.parametrize("kind", ["menu", "dialog"])
    def test_escape_taken_by_a_menu_or_dialog_does_not_cancel(self, page: Any, kind: str) -> None:
        page.evaluate(_EDITOR)
        page.evaluate(_LAYER, kind)
        page.keyboard.press("Escape")
        assert page.evaluate("window.cancelled") == 0
