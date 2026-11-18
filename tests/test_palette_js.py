"""Browser tests for the keyboard section of ``assets/plexmix.js`` (headless Chromium).

Covers webui-input-responsiveness group 4: the platform tag set before first paint,
the global shortcuts (⌘K / Ctrl+K, "/", g+key) and their guards, the ⌘K palette
controller ``window.__pmPalette`` (filtering, highlight, Enter, focus restore, late
rows) and the form helpers (Enter / ⌘+Enter submit, ``data-enter="none"``, the live
character counter).

The real script is loaded into a static page that mirrors the DOM contract of
design.md D6-D9/D12-D14 and simulates what Radix and React do: the trigger toggles a
``role=dialog`` ``.cmdk`` content that auto-focuses ``#cmdk-input``; rows close it
when clicked; Escape closes it; a tick after it closes, Radix's ``onCloseAutoFocus``
runs ``__pmPalette.closeAutoFocus(event)`` synchronously (navbar.py binds it as a
client-side callback) and the trigger is focused unless that handler called
``preventDefault()``. Pages are served through Playwright request interception
on an https origin (a secure context, so ``navigator.userAgentData`` exists), so no
port is bound. The module is skipped when Playwright or its Chromium build is missing.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

_ASSETS = Path(__file__).resolve().parents[1] / "assets"
_SCRIPT = _ASSETS / "plexmix.js"


def test_script_parses_with_node() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    proc = subprocess.run(
        [node, "--check", str(_SCRIPT)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr


sync_api = pytest.importorskip("playwright.sync_api")

_ORIGIN = "https://palette.test"

MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
LINUX_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
WIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_FIXTURE_CSS = """<style>
.kbd-mac { display: none; }
html[data-platform="mac"] .kbd-mac { display: inline; }
html[data-platform="mac"] .kbd-other { display: none; }
.cmdk-item[hidden], .cmdk-group[hidden] { display: none; }
</style>"""
_REAL_CSS = (
    '<link rel="stylesheet" href="/colors_and_type.css">'
    '<link rel="stylesheet" href="/redesign.css">'
)

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script src="/plexmix.js"></script>
__CSS__
<style>
.cmdk-item { display: flex; height: 40px; }
#cmdk-list { max-height: 120px; overflow-y: auto; }
</style></head><body>
<script>window.__platformAtBody = document.documentElement.dataset.platform || null;</script>
<nav class="rail">
<button type="button" class="rail-item" data-route="/dashboard">Dashboard</button>
<button type="button" class="rail-item" data-route="/generator">Generator</button>
<button type="button" class="rail-item" data-route="/library">Library</button>
<button type="button" class="rail-item" data-route="/tagging">Tagging</button>
<button type="button" class="rail-item" data-route="/history">History</button>
<button type="button" class="rail-item" data-route="/doctor">Doctor</button>
<button type="button" class="rail-item" data-route="/settings">Settings</button>
</nav>
<header class="topbar">
<button type="button" class="cmd-trigger" aria-haspopup="dialog" aria-expanded="false"
  aria-keyshortcuts="Meta+K Control+K /"><span>Search or jump to…</span><span class="kbd"
  ><span class="kbd-mac">⌘K</span><span class="kbd-other">Ctrl K</span></span></button>
</header>
<main id="main-content">
<h1 id="main-heading" tabindex="-1">Library</h1>
<form id="filters" class="filterbar" data-enter-submit="">
  <input id="library-search" name="q" aria-label="Search tracks">
  <input id="f-year" name="year_min" type="number" min="1000" max="9999" aria-label="From year">
  <input id="f-audio" name="has_audio" type="checkbox">
  <button type="button" id="f-clear">Clear</button>
</form>
<form id="tag-filters" data-enter-submit="">
  <input id="t-genre" name="genre" aria-label="Filter by genre">
  <button type="submit" id="t-preview">Preview</button>
</form>
<form id="plain-form"><input id="p1" name="a"><input id="p2" name="b"></form>
<form id="prompt-form" class="prompt-box">
  <textarea id="gen-prompt" name="prompt" maxlength="280" data-submit-shortcut=""
    data-counter="gen-prompt-count"></textarea>
  <span id="gen-prompt-count">0/280</span>
  <input id="gen-genre" name="genre" data-enter="none" aria-label="Genre focus">
  <button type="submit" id="gen-submit">Generate</button>
</form>
<textarea id="notes" aria-label="Notes"></textarea>
<select id="sel" aria-label="Pick"><option>a</option></select>
<div id="ce" contenteditable="true"></div>
<div id="combo" role="combobox" tabindex="0" aria-expanded="false">combo</div>
<button id="plain-btn" type="button">Plain</button>
<input id="f-range" type="range" aria-label="Level">
<input id="f-radio" type="radio" name="r" aria-label="Pick one">
</main>
<script>
window.__navs = []; window.__ran = []; window.__submits = []; window.__opens = 0;
document.querySelectorAll('.rail [data-route]').forEach(function (b) {
  b.addEventListener('click', function () {     // rx.redirect: React Router navigate
    var to = b.getAttribute('data-route');
    history.pushState({}, '', to); window.__navs.push(to);
  });
});
document.addEventListener('submit', function (e) {
  e.preventDefault();                            // Reflex handleSubmit
  window.__submits.push({form: e.target.id, data: Object.fromEntries(new FormData(e.target))});
});
var trigger = document.querySelector('.cmd-trigger');
function opt(id, title, sub, search, route) {
  return '<div role="option" id="cmdk-opt-' + id + '" class="cmdk-item" data-search="' +
    search + '"' + (route ? ' data-route="' + route + '"' : '') +
    '><span class="ci-title">' + title + '</span><span class="ci-sub">' + sub + '</span></div>';
}
var ACTIONS = [
  ['generate', 'Generate a playlist', 'Start a new mix', 'new mix create'],
  ['sync', 'Sync library', 'Pull latest from Plex', 'refresh'],
  ['tag', 'Tag untagged tracks', 'Open AI tagging', 'tags'],
  ['diagnose', 'Run diagnostics', 'Open Doctor', 'health check'],
  ['theme', 'Switch to dark mode', 'Appearance', 'theme color'],
];
var JUMPS = [['dashboard', '/dashboard'], ['generator', '/generator'], ['library', '/library'],
  ['tagging', '/tagging'], ['history', '/history'], ['doctor', '/doctor'],
  ['settings', '/settings']];
function group(id, label, rows, hidden) {
  return '<div class="cmdk-group" role="group" id="cmdk-' + id + '" aria-labelledby="cmdk-g-' +
    id + '"' + (hidden ? ' hidden' : '') + '><div class="cmdk-group-label" id="cmdk-g-' + id +
    '">' + label + '</div>' + rows + '</div>';
}
window.openPalette = function () {
  var acts = ACTIONS.map(function (a) {
    return opt(a[0], a[1], a[2], (a[1] + ' ' + a[2] + ' ' + a[3]).toLowerCase());
  }).join('');
  var jumps = JUMPS.map(function (j) {
    var t = j[0].charAt(0).toUpperCase() + j[0].slice(1);
    return opt(j[0], t, 'Go to page', (t + ' go to page ' + j[0]).toLowerCase(), j[1]);
  }).join('');
  var portal = document.createElement('div');
  portal.id = 'portal'; portal.className = 'rt-DialogOverlay';
  portal.innerHTML = '<div class="rt-DialogScroll"><div class="rt-DialogScrollPadding">' +
    '<div role="dialog" aria-modal="true" data-state="open" class="rt-DialogContent cmdk" ' +
    'aria-labelledby="cmdk-title" aria-describedby="cmdk-foot">' +
    '<h2 id="cmdk-title" class="sr-only">Command palette</h2>' +
    '<div class="cmdk-input-row"><input id="cmdk-input" class="cmdk-input" role="combobox" ' +
    'aria-label="Search commands" aria-expanded="true" aria-controls="cmdk-list" ' +
    'aria-autocomplete="list" autocomplete="off"></div>' +
    '<div id="cmdk-list" class="cmdk-list" role="listbox" aria-label="Commands">' +
    group('actions', 'Actions', acts) + group('jump', 'Jump to', jumps) +
    // Like navbar.py: the Generate row and group are rendered hidden and have no
    // data-search; Recent prompts only mounts once there are recents (rx.cond).
    group('gen', 'Generate', '<div role="option" id="cmdk-opt-generate-query" ' +
      'class="cmdk-item" hidden><span class="ci-title">Generate “' +
      '<span class="cmdk-echo"></span>”</span><span class="ci-sub">Create a playlist ' +
      'from this prompt</span></div>', true) +
    '</div><div id="cmdk-status" role="status" aria-live="polite" class="sr-only"></div>' +
    '<p id="cmdk-foot" class="cmdk-foot">move, run, close</p></div></div></div>';
  document.body.appendChild(portal);
  trigger.setAttribute('aria-expanded', 'true');
  window.__opens++;
  document.getElementById('cmdk-input').focus();   // Radix onOpenAutoFocus
};
window.__closeEvents = [];
window.closePalette = function () {
  var p = document.getElementById('portal'); if (!p) return;
  p.remove(); trigger.setAttribute('aria-expanded', 'false');
  // Radix FocusScope: a tick after unmount it dispatches onCloseAutoFocus, which
  // navbar.py binds straight to __pmPalette.closeAutoFocus (a client-side callback,
  // not a queued server event); DialogContentModal then focuses the trigger unless
  // that handler called preventDefault().
  setTimeout(function () {
    var ev = new CustomEvent('focusScope.autoFocusOnUnmount', { cancelable: true });
    var pm = window.__pmPalette;
    pm && pm.closeAutoFocus && pm.closeAutoFocus(ev);
    window.__closeEvents.push(ev.defaultPrevented);
    if (!ev.defaultPrevented) trigger.focus();       // Radix: triggerRef.current.focus()
  }, 0);
};
trigger.addEventListener('click', function () {      // Dialog.Trigger toggles
  if (document.getElementById('portal')) window.closePalette(); else window.openPalette();
});
document.addEventListener('keydown', function (e) {  // Radix DismissableLayer
  if (e.key === 'Escape' && document.getElementById('portal')) {
    e.preventDefault(); window.closePalette();
  }
}, true);
document.addEventListener('click', function (e) {    // row handler, then Dialog.Close
  var row = e.target.closest && e.target.closest('#portal [role="option"]');
  if (!row) return;
  var id = row.id.replace('cmdk-opt-', '');
  if (id === 'generate-query') {
    id = 'gen:' + (document.getElementById('cmdk-input').value || '');
  }
  window.__ran.push(id);
  var to = row.getAttribute('data-route');
  if (to) history.pushState({}, '', to);
  window.closePalette();
});
window.addGroup = function (id, label) {           // a group mounting late, empty
  document.getElementById('cmdk-gen').insertAdjacentHTML('beforebegin', group(id, label, ''));
};
window.addRecent = function (id, prompt) {          // rx.cond mounts the group with its rows
  if (!document.getElementById('cmdk-recent')) window.addGroup('recent', 'Recent prompts');
  document.getElementById('cmdk-recent').insertAdjacentHTML('beforeend',
    '<div role="option" id="cmdk-opt-recent-' + id + '" class="cmdk-item" data-search="' +
    prompt + ' use this prompt"><span class="ci-title">' + prompt + '</span></div>');
};
window.openOther = function (role, state) {
  var d = document.createElement('div'); d.id = 'other';
  d.setAttribute('role', role); d.setAttribute('data-state', state || 'open');
  d.innerHTML = '<button id="other-btn" type="button">Close</button>';
  document.body.appendChild(d);
  document.getElementById('other-btn').focus();
};
</script></body></html>"""


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser build not installed
            pytest.skip(f"Chromium for Playwright is unavailable: {type(exc).__name__}")
        yield b
        b.close()


PageFactory = Callable[..., Any]


@pytest.fixture()
def make_page(browser: Any) -> Iterator[PageFactory]:
    contexts: list[Any] = []

    def _make(
        *,
        user_agent: str | None = None,
        origin: str = _ORIGIN,
        init_script: str | None = None,
        css: str = _FIXTURE_CSS,
        path: str = "/library",
    ) -> Any:
        ctx = browser.new_context(user_agent=user_agent) if user_agent else browser.new_context()
        contexts.append(ctx)
        if init_script:
            ctx.add_init_script(init_script)
        pg = ctx.new_page()
        html = _PAGE.replace("__CSS__", css)

        def _serve(route: Any) -> None:
            url = route.request.url
            if url.endswith("/plexmix.js"):
                route.fulfill(
                    body=_SCRIPT.read_text(encoding="utf-8"),
                    content_type="application/javascript",
                )
            elif url.endswith(".css"):
                name = url.rsplit("/", 1)[-1]
                route.fulfill(
                    body=(_ASSETS / name).read_text(encoding="utf-8"), content_type="text/css"
                )
            else:
                route.fulfill(body=html, content_type="text/html")

        pg.route(f"{origin}/**", _serve)
        pg.goto(f"{origin}{path}")
        return pg

    yield _make
    for ctx in contexts:
        ctx.close()


@pytest.fixture()
def page(make_page: PageFactory) -> Any:
    return make_page()


# ── helpers ──────────────────────────────────────────────────────────────


def _active(pg: Any) -> str:
    return str(pg.evaluate("document.activeElement.id || document.activeElement.tagName"))


def _is_open(pg: Any) -> bool:
    return bool(pg.evaluate("!!document.getElementById('portal')"))


def _visible_ids(pg: Any) -> list[str]:
    return list(
        pg.evaluate(
            """() => Array.from(document.querySelectorAll('#cmdk-list [role="option"]'))
                .filter(r => r.getClientRects().length > 0).map(r => r.id)"""
        )
    )


def _selected(pg: Any) -> list[str]:
    return list(
        pg.evaluate(
            """() => Array.from(document.querySelectorAll('#cmdk-list [role="option"]'))
                .filter(r => r.getAttribute('aria-selected') === 'true').map(r => r.id)"""
        )
    )


def _activedescendant(pg: Any) -> str | None:
    value = pg.evaluate(
        "document.getElementById('cmdk-input').getAttribute('aria-activedescendant')"
    )
    return None if value is None else str(value)


def _status(pg: Any) -> str:
    return str(pg.evaluate("document.getElementById('cmdk-status').textContent"))


def _open(pg: Any, focus: str | None = None) -> None:
    if focus:
        pg.focus(focus)
    pg.keyboard.press("Meta+k")
    assert _is_open(pg)
    assert _active(pg) == "cmdk-input"


def _wait_active(pg: Any, element_id: str) -> None:
    pg.wait_for_function(f"document.activeElement.id === {element_id!r}", timeout=2000)


def _composing_enter(pg: Any, selector: str, **extra: Any) -> bool:
    """Dispatch an IME-confirm Enter keydown; returns whether it was default-prevented."""
    init = {"key": "Enter", "code": "Enter", "bubbles": True, "cancelable": True, **extra}
    return bool(
        pg.evaluate(
            """([sel, init]) => { const ev = new KeyboardEvent('keydown', init);
                document.querySelector(sel).dispatchEvent(ev); return ev.defaultPrevented; }""",
            [selector, init],
        )
    )


ALL_ROWS = [
    "cmdk-opt-generate",
    "cmdk-opt-sync",
    "cmdk-opt-tag",
    "cmdk-opt-diagnose",
    "cmdk-opt-theme",
    "cmdk-opt-dashboard",
    "cmdk-opt-generator",
    "cmdk-opt-library",
    "cmdk-opt-tagging",
    "cmdk-opt-history",
    "cmdk-opt-doctor",
    "cmdk-opt-settings",
]


# ── 4.1 platform tag ─────────────────────────────────────────────────────


class TestPlatform:
    @pytest.mark.parametrize(
        ("user_agent", "platform", "label"),
        [(MAC_UA, "mac", "⌘K"), (LINUX_UA, "other", "Ctrl K"), (WIN_UA, "other", "Ctrl K")],
    )
    def test_user_agent_platform_picks_the_label(
        self, make_page: PageFactory, user_agent: str, platform: str, label: str
    ) -> None:
        pg = make_page(user_agent=user_agent)
        assert pg.evaluate("document.documentElement.dataset.platform") == platform
        # Set by the <head> script, before the body is parsed (no label flash).
        assert pg.evaluate("window.__platformAtBody") == platform
        assert pg.inner_text(".cmd-trigger .kbd").strip() == label

    @pytest.mark.parametrize(
        ("nav_platform", "platform"),
        [("MacIntel", "mac"), ("iPhone", "mac"), ("Win32", "other"), ("Linux x86_64", "other")],
    )
    def test_falls_back_to_navigator_platform_without_user_agent_data(
        self, make_page: PageFactory, nav_platform: str, platform: str
    ) -> None:
        # Plain http (e.g. a NAS on the LAN) is not a secure context: no userAgentData.
        pg = make_page(
            origin="http://palette.test",
            init_script=(
                "Object.defineProperty(Navigator.prototype, 'platform', "
                f"{{get: () => {nav_platform!r}}});"
            ),
        )
        assert pg.evaluate("navigator.userAgentData === undefined")
        assert pg.evaluate("document.documentElement.dataset.platform") == platform

    @pytest.mark.parametrize(("user_agent", "label"), [(MAC_UA, "⌘K"), (LINUX_UA, "Ctrl K")])
    def test_label_with_the_real_stylesheet(
        self, make_page: PageFactory, user_agent: str, label: str
    ) -> None:
        css = (_ASSETS / "redesign.css").read_text(encoding="utf-8")
        if "kbd-mac" not in css:
            pytest.skip("the .kbd-mac/.kbd-other rules (task 3.3) are not in redesign.css yet")
        pg = make_page(user_agent=user_agent, css=_REAL_CSS)
        assert pg.inner_text(".cmd-trigger .kbd").strip() == label


# ── 4.2 shortcuts and guards ─────────────────────────────────────────────


class TestOpenShortcuts:
    @pytest.mark.parametrize("combo", ["Meta+k", "Control+k", "Meta+K"])
    def test_cmd_k_opens_from_a_text_field_and_records_the_opener(
        self, page: Any, combo: str
    ) -> None:
        page.focus("#library-search")
        page.keyboard.press(combo)
        assert _is_open(page)
        assert _active(page) == "cmdk-input"
        assert page.evaluate("window.__pmPalette.returnTo.id") == "library-search"

    @pytest.mark.parametrize("combo", ["Meta+Alt+k", "Meta+Shift+k", "Control+Alt+k"])
    def test_cmd_k_with_extra_modifiers_does_nothing(self, page: Any, combo: str) -> None:
        page.focus("#plain-btn")
        page.keyboard.press(combo)
        assert not _is_open(page)

    def test_cmd_k_again_closes_and_restores_the_opener(self, page: Any) -> None:
        _open(page, "#library-search")
        page.keyboard.type("hi")
        page.keyboard.press("Meta+k")
        assert not _is_open(page)
        _wait_active(page, "library-search")

    def test_typing_right_after_cmd_k_lands_in_the_search_field(self, page: Any) -> None:
        page.focus("#plain-btn")
        page.keyboard.press("Meta+k")
        page.keyboard.type("hist")
        assert page.input_value("#cmdk-input") == "hist"

    @pytest.mark.parametrize("start", ["#plain-btn", "#main-heading", None])
    def test_slash_opens_outside_text_fields(self, page: Any, start: str | None) -> None:
        if start:
            page.focus(start)
        page.keyboard.press("/")
        assert _is_open(page)
        assert page.input_value("#cmdk-input") == ""  # the "/" itself is not typed

    @pytest.mark.parametrize("field", ["#library-search", "#notes", "#gen-prompt"])
    def test_slash_is_typed_in_text_fields(self, page: Any, field: str) -> None:
        page.focus(field)
        page.keyboard.type("a/b")
        assert not _is_open(page)
        assert page.input_value(field) == "a/b"

    def test_slash_is_typed_in_contenteditable(self, page: Any) -> None:
        page.focus("#ce")
        page.keyboard.type("a/b")
        assert not _is_open(page)
        assert page.inner_text("#ce") == "a/b"

    @pytest.mark.parametrize("field", ["#sel", "#combo", "#f-year"])
    def test_slash_is_ignored_on_select_combobox_and_number_fields(
        self, page: Any, field: str
    ) -> None:
        page.focus(field)
        page.keyboard.press("/")
        assert not _is_open(page)

    @pytest.mark.parametrize("field", ["#f-audio", "#f-range", "#f-radio"])
    def test_slash_opens_from_controls_that_take_no_text(self, page: Any, field: str) -> None:
        """A checkbox, slider or radio is not a text field: "/" opens the palette there."""
        page.focus(field)
        page.keyboard.press("/")
        assert _is_open(page)
        assert page.input_value("#cmdk-input") == ""
        assert page.evaluate("window.__pmPalette.returnTo.id") == field[1:]

    @pytest.mark.parametrize("combo", ["Control+/", "Alt+/", "Meta+/"])
    def test_slash_with_a_modifier_does_nothing(self, page: Any, combo: str) -> None:
        page.focus("#plain-btn")
        page.keyboard.press(combo)
        assert not _is_open(page)

    def test_slash_inside_the_open_palette_is_typed(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.type("a/")
        assert _is_open(page)
        assert page.input_value("#cmdk-input") == "a/"
        assert page.evaluate("window.__opens") == 1


class TestOtherDialogGuard:
    @pytest.mark.parametrize("role", ["dialog", "alertdialog"])
    def test_every_shortcut_is_ignored_while_another_dialog_is_open(
        self, page: Any, role: str
    ) -> None:
        page.evaluate(f"openOther({role!r})")
        for combo in ("Meta+k", "Control+k", "/"):
            page.keyboard.press(combo)
            assert not _is_open(page), combo
        page.keyboard.press("g")
        page.keyboard.press("l")
        assert page.evaluate("window.__navs") == []
        assert page.evaluate("location.pathname") == "/library"
        assert _active(page) == "other-btn"

    def test_a_closing_dialog_does_not_block(self, page: Any) -> None:
        # Radix keeps a closing dialog mounted with data-state="closed" while it animates out.
        page.evaluate("openOther('dialog', 'closed')")
        page.keyboard.press("Meta+k")
        assert _is_open(page)

    def test_the_open_palette_is_not_another_dialog(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.press("Control+k")  # toggles it closed instead of being blocked
        assert not _is_open(page)


class TestChords:
    def test_g_l_clicks_the_rail_item_without_reloading(self, page: Any) -> None:
        page.evaluate("window.__marker = 42")
        page.focus("#plain-btn")
        page.keyboard.press("g")
        page.keyboard.press("l")
        assert page.evaluate("location.pathname") == "/library"
        assert page.evaluate("window.__navs") == ["/library"]
        assert page.evaluate("performance.getEntriesByType('navigation').length") == 1
        assert page.evaluate("window.__marker") == 42

    @pytest.mark.parametrize(
        ("key", "path"),
        [
            ("d", "/dashboard"),
            ("g", "/generator"),
            ("l", "/library"),
            ("t", "/tagging"),
            ("h", "/history"),
            ("x", "/doctor"),
            ("s", "/settings"),
        ],
    )
    def test_every_chord(self, page: Any, key: str, path: str) -> None:
        page.keyboard.press("g")
        page.keyboard.press(key)
        assert page.evaluate("window.__navs") == [path]
        assert page.evaluate("location.pathname") == path

    def test_chord_times_out(self, page: Any) -> None:
        page.keyboard.press("g")
        page.wait_for_timeout(750)
        page.keyboard.press("l")
        assert page.evaluate("window.__navs") == []

    def test_unmapped_second_key_discards_the_chord(self, page: Any) -> None:
        page.keyboard.press("g")
        page.keyboard.press("q")
        page.keyboard.press("l")
        assert page.evaluate("window.__navs") == []

    @pytest.mark.parametrize(
        ("first", "second"),
        [("Control+g", "l"), ("Meta+g", "l"), ("Alt+g", "l"), ("g", "Alt+l"), ("g", "Control+l")],
    )
    def test_modifiers_never_start_or_finish_a_chord(
        self, page: Any, first: str, second: str
    ) -> None:
        page.focus("#plain-btn")
        page.keyboard.press(first)
        page.keyboard.press(second)
        assert page.evaluate("window.__navs") == []

    @pytest.mark.parametrize("field", ["#library-search", "#notes", "#ce", "#combo", "#sel"])
    def test_chords_are_ignored_in_editable_focus(self, page: Any, field: str) -> None:
        page.focus(field)
        page.keyboard.press("g")
        page.keyboard.press("l")
        assert page.evaluate("window.__navs") == []
        assert page.evaluate("location.pathname") == "/library"

    @pytest.mark.parametrize("field", ["#f-audio", "#f-range", "#f-radio"])
    def test_chords_work_from_controls_that_take_no_text(self, page: Any, field: str) -> None:
        page.focus(field)
        page.keyboard.press("g")
        page.keyboard.press("h")
        assert page.evaluate("window.__navs") == ["/history"]
        assert page.evaluate("location.pathname") == "/history"

    def test_second_key_typed_into_a_field_is_not_a_chord(self, page: Any) -> None:
        page.focus("#plain-btn")
        page.keyboard.press("g")
        page.focus("#library-search")
        page.keyboard.press("l")
        assert page.evaluate("window.__navs") == []
        assert page.input_value("#library-search") == "l"

    def test_chord_without_a_route_element_does_nothing(self, page: Any) -> None:
        page.evaluate("document.querySelector('nav.rail').remove()")  # e.g. the login screen
        page.keyboard.press("g")
        page.keyboard.press("h")
        assert page.evaluate("location.pathname") == "/library"

    def test_chords_are_ignored_while_the_palette_is_open(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.focus("#cmdk-list")  # even off the combobox
        page.keyboard.press("g")
        page.keyboard.press("h")
        assert page.evaluate("window.__navs") == []


# ── 4.3 palette controller ───────────────────────────────────────────────


class TestPaletteFilter:
    def test_first_row_is_highlighted_on_open(self, page: Any) -> None:
        _open(page, "#plain-btn")
        assert _visible_ids(page) == ALL_ROWS  # the Generate row needs a query
        assert _selected(page) == ["cmdk-opt-generate"]
        assert _activedescendant(page) == "cmdk-opt-generate"
        assert page.evaluate(
            "Array.from(document.querySelectorAll('#cmdk-list [role=option]'))"
            ".every(r => ['true', 'false'].includes(r.getAttribute('aria-selected')))"
        )
        assert page.evaluate("document.getElementById('cmdk-gen').hidden")
        assert _status(page) == "12 commands"

    def test_an_empty_group_is_hidden(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.evaluate("addGroup('recent', 'Recent prompts')")
        page.wait_for_function("document.getElementById('cmdk-recent').hidden", timeout=2000)
        assert _status(page) == "12 commands"

    def test_filtering_with_the_real_stylesheet(self, make_page: PageFactory) -> None:
        css = (_ASSETS / "redesign.css").read_text(encoding="utf-8")
        if ".cmdk-item[hidden]" not in css:
            pytest.skip("the palette [hidden] rules (task 3.3) are not in redesign.css yet")
        pg = make_page(css=_REAL_CSS)
        _open(pg, "#plain-btn")
        assert _visible_ids(pg) == ALL_ROWS
        pg.keyboard.type("hist")
        assert _visible_ids(pg) == ["cmdk-opt-history", "cmdk-opt-generate-query"]
        assert pg.evaluate("getComputedStyle(document.getElementById('cmdk-actions')).display") == (
            "none"
        )

    def test_hist_leaves_only_history_and_generate(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.type("hist")
        assert _visible_ids(page) == ["cmdk-opt-history", "cmdk-opt-generate-query"]
        assert page.evaluate("document.getElementById('cmdk-actions').hidden")
        assert not page.evaluate("document.getElementById('cmdk-jump').hidden")
        assert not page.evaluate("document.getElementById('cmdk-gen').hidden")
        assert page.evaluate("document.querySelector('.cmdk-echo').textContent") == "hist"
        assert _selected(page) == ["cmdk-opt-history"]
        assert _status(page) == "1 command"

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("PAGE hist", ["cmdk-opt-history"]),
            ("  page   doc  ", ["cmdk-opt-doctor"]),
            ("open doctor", ["cmdk-opt-diagnose"]),
            ("tag", ["cmdk-opt-tag", "cmdk-opt-tagging"]),
        ],
    )
    def test_every_term_must_match_case_insensitively(
        self, page: Any, query: str, expected: list[str]
    ) -> None:
        _open(page, "#plain-btn")
        page.keyboard.type(query)
        assert _visible_ids(page) == expected + ["cmdk-opt-generate-query"]
        assert _selected(page) == [expected[0]]

    def test_no_match_shows_only_generate_highlighted_and_runs_it(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.type("  jazz for cooking ")
        assert _visible_ids(page) == ["cmdk-opt-generate-query"]
        assert _selected(page) == ["cmdk-opt-generate-query"]
        assert _activedescendant(page) == "cmdk-opt-generate-query"
        assert page.evaluate("document.querySelector('.cmdk-echo').textContent") == (
            "jazz for cooking"
        )
        assert _status(page) == (
            "No commands match. Press Enter to generate a playlist for “jazz for cooking”."
        )
        page.keyboard.press("Enter")
        assert page.evaluate("window.__ran") == ["gen:  jazz for cooking "]
        assert not _is_open(page)

    def test_clearing_the_query_hides_generate_again(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.type("zz")
        page.keyboard.press("Backspace")
        page.keyboard.press("Backspace")
        assert _visible_ids(page) == ALL_ROWS
        assert not page.evaluate("document.getElementById('cmdk-actions').hidden")
        assert _status(page) == "12 commands"


class TestPaletteHighlight:
    def test_highlight_resets_on_every_query_change(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowDown")
        assert _selected(page) == ["cmdk-opt-tag"]
        page.keyboard.type("a")
        assert _selected(page) == [_visible_ids(page)[0]]
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Backspace")
        assert _selected(page) == ["cmdk-opt-generate"]
        assert _activedescendant(page) == "cmdk-opt-generate"

    def test_arrows_wrap_and_move_aria_state(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.press("ArrowUp")
        assert _selected(page) == ["cmdk-opt-settings"]
        assert _activedescendant(page) == "cmdk-opt-settings"
        page.keyboard.press("ArrowDown")
        assert _selected(page) == ["cmdk-opt-generate"]
        page.keyboard.press("ArrowDown")
        assert _selected(page) == ["cmdk-opt-sync"]
        assert _activedescendant(page) == "cmdk-opt-sync"
        assert page.input_value("#cmdk-input") == ""

    def test_arrows_wrap_within_the_visible_rows(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.type("tag")
        for expected in ("cmdk-opt-tagging", "cmdk-opt-generate-query", "cmdk-opt-tag"):
            page.keyboard.press("ArrowDown")
            assert _selected(page) == [expected]

    def test_highlighted_row_is_scrolled_into_view(self, page: Any) -> None:
        in_view = """id => { const l = document.getElementById('cmdk-list').getBoundingClientRect();
            const r = document.getElementById(id).getBoundingClientRect();
            return r.top >= l.top - 0.5 && r.bottom <= l.bottom + 0.5; }"""
        _open(page, "#plain-btn")
        assert not page.evaluate(in_view, "cmdk-opt-settings")
        page.keyboard.press("ArrowUp")  # wraps to the last row, far below the 120px list
        assert page.evaluate("document.getElementById('cmdk-list').scrollTop") > 0
        assert page.evaluate(in_view, "cmdk-opt-settings")
        page.keyboard.press("ArrowDown")
        assert page.evaluate(in_view, "cmdk-opt-generate")

    def test_pointermove_highlights_and_enter_runs_the_hovered_row(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.hover("#cmdk-opt-sync")
        assert _selected(page) == ["cmdk-opt-sync"]
        assert _activedescendant(page) == "cmdk-opt-sync"
        assert _active(page) == "cmdk-input"
        page.keyboard.press("Enter")
        assert page.evaluate("window.__ran") == ["sync"]

    def test_enter_runs_the_highlighted_row_only(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert page.evaluate("window.__ran") == ["sync"]
        assert not _is_open(page)

    def test_fast_typing_history_then_enter_goes_to_history(self, page: Any) -> None:
        page.focus("#plain-btn")
        page.keyboard.press("Meta+k")
        page.keyboard.type("history", delay=40)
        page.keyboard.press("Enter")
        assert page.evaluate("window.__ran") == ["history"]
        assert page.evaluate("location.pathname") == "/history"

    @pytest.mark.parametrize("extra", [{"isComposing": True}, {"keyCode": 229}])
    def test_enter_while_composing_runs_nothing(self, page: Any, extra: dict[str, Any]) -> None:
        _open(page, "#plain-btn")
        page.keyboard.type("hist")
        assert not _composing_enter(page, "#cmdk-input", **extra)
        assert page.evaluate("window.__ran") == []
        assert _is_open(page)

    def test_enter_does_nothing_from_other_controls(self, page: Any) -> None:
        page.focus("#plain-btn")
        page.keyboard.press("Enter")
        assert page.evaluate("window.__ran") == []


class TestPaletteLateRows:
    def test_late_rows_are_filtered_counted_and_keep_the_highlight(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.press("ArrowDown")
        page.evaluate("addRecent('1', 'rainy day melancholy')")
        page.wait_for_function(
            "document.getElementById('cmdk-status').textContent === '13 commands'", timeout=2000
        )
        assert not page.evaluate("document.getElementById('cmdk-recent').hidden")
        assert _selected(page) == ["cmdk-opt-sync"]
        assert page.evaluate(
            "document.getElementById('cmdk-opt-recent-1').getAttribute('aria-selected')"
        ) == ("false")
        page.keyboard.type("rainy")
        assert _visible_ids(page) == ["cmdk-opt-recent-1", "cmdk-opt-generate-query"]
        assert _selected(page) == ["cmdk-opt-recent-1"]
        page.evaluate("addRecent('2', 'late night drive')")  # does not match "rainy"
        page.wait_for_function(
            "document.getElementById('cmdk-opt-recent-2').hidden === true", timeout=2000
        )
        assert _status(page) == "1 command"
        assert _selected(page) == ["cmdk-opt-recent-1"]

    def test_row_inserted_above_does_not_move_the_highlight(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.press("ArrowDown")
        page.evaluate(
            """() => document.getElementById('cmdk-g-actions').insertAdjacentHTML('afterend',
                 '<div role="option" id="cmdk-opt-cancel" class="cmdk-item" ' +
                 'data-search="cancel running task">Cancel running task</div>')"""
        )
        page.wait_for_function(
            "document.getElementById('cmdk-opt-cancel').getAttribute('aria-selected') === 'false'",
            timeout=2000,
        )
        assert _selected(page) == ["cmdk-opt-sync"]
        assert _activedescendant(page) == "cmdk-opt-sync"

    def test_removed_highlight_falls_back_to_the_first_row(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.keyboard.press("ArrowDown")
        page.evaluate("document.getElementById('cmdk-opt-sync').remove()")
        page.wait_for_function(
            "document.getElementById('cmdk-input').getAttribute('aria-activedescendant')"
            " === 'cmdk-opt-generate'",
            timeout=2000,
        )
        assert _selected(page) == ["cmdk-opt-generate"]
        assert _status(page) == "11 commands"

    def test_echo_and_status_writes_settle(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.evaluate(
            """() => { window.__muts = 0;
                new MutationObserver(r => { window.__muts += r.length; })
                  .observe(document.getElementById('portal'),
                           {childList: true, subtree: true, attributes: true,
                            characterData: true}); }"""
        )
        page.keyboard.type("abc")
        page.wait_for_timeout(300)
        settled = page.evaluate("window.__muts")
        page.wait_for_timeout(300)
        assert page.evaluate("window.__muts") == settled
        assert settled < 200

    def test_rows_without_an_id_get_one_for_activedescendant(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.evaluate(
            """() => document.getElementById('cmdk-jump').insertAdjacentHTML('beforeend',
                 '<div role="option" class="cmdk-item" data-search="zebra">Zebra</div>')"""
        )
        page.keyboard.type("zebra")
        selected = _selected(page)
        assert len(selected) == 1 and selected[0].startswith("cmdk-opt-")
        assert _activedescendant(page) == selected[0]


class TestPaletteFocusRestore:
    def test_escape_returns_focus_to_library_search(self, page: Any) -> None:
        _open(page, "#library-search")
        page.keyboard.type("hi")
        page.keyboard.press("Escape")
        assert not _is_open(page)
        _wait_active(page, "library-search")

    def test_trigger_click_then_escape_keeps_focus_on_the_trigger(self, page: Any) -> None:
        # A keyboard open from Library search first, so a stale opener would show up.
        _open(page, "#library-search")
        page.keyboard.press("Escape")
        _wait_active(page, "library-search")
        page.click(".cmd-trigger")
        assert _is_open(page)
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        assert page.evaluate("document.activeElement.classList.contains('cmd-trigger')")

    def test_trigger_click_after_a_keyboard_open_forgets_the_old_opener(self, page: Any) -> None:
        _open(page, "#library-search")
        page.keyboard.press("Escape")
        page.click(".cmd-trigger")  # re-opened by pointer: the trigger is the opener now
        assert _is_open(page)
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        assert page.evaluate("document.activeElement.classList.contains('cmd-trigger')")

    def test_escape_restores_in_the_close_callback_itself(self, page: Any) -> None:
        """No server round trip: focus is placed inside Radix's onCloseAutoFocus."""
        _open(page, "#library-search")
        page.evaluate("document.getElementById('portal').remove()")  # content unmounted
        result = page.evaluate(
            """() => { const ev = new CustomEvent('x', {cancelable: true});
                const kept = window.__pmPalette.closeAutoFocus(ev);
                return [kept, ev.defaultPrevented, document.activeElement.id]; }"""
        )
        assert result == [True, True, "library-search"]

    def test_keys_typed_right_after_escape_reach_the_opener(self, page: Any) -> None:
        _open(page, "#library-search")
        page.keyboard.type("hi")
        page.keyboard.press("Escape")
        _wait_active(page, "library-search")
        page.keyboard.type(" rainy day")
        assert page.input_value("#library-search") == " rainy day"
        assert not _is_open(page)
        assert page.evaluate("window.__opens") == 1  # Space never re-opened the palette
        assert page.evaluate("window.__closeEvents") == [True]  # Radix kept off the trigger

    def test_enter_on_the_focused_trigger_counts_as_the_trigger(self, page: Any) -> None:
        _open(page, "#library-search")
        page.keyboard.press("Escape")
        _wait_active(page, "library-search")
        page.focus(".cmd-trigger")
        page.keyboard.press("Enter")
        assert _is_open(page)
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        assert page.evaluate("document.activeElement.classList.contains('cmd-trigger')")

    def test_removed_opener_falls_back_to_the_trigger(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.evaluate("document.getElementById('plain-btn').remove()")
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        assert page.evaluate("document.activeElement.classList.contains('cmd-trigger')")

    def test_opened_from_body_falls_back_to_the_trigger(self, page: Any) -> None:
        page.evaluate("document.activeElement && document.activeElement.blur()")
        page.keyboard.press("/")
        assert page.evaluate("window.__pmPalette.returnTo") is None
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
        assert page.evaluate("document.activeElement.classList.contains('cmd-trigger')")

    def test_focus_lost_to_body_goes_to_the_trigger(self, page: Any) -> None:
        _open(page, "#plain-btn")
        page.evaluate(
            """() => { document.getElementById('portal').remove();
                document.activeElement && document.activeElement.blur();
                window.__restored = window.__pmPalette.restoreFocus(); }"""
        )
        assert page.evaluate("window.__restored") is True
        assert _active(page) == "plain-btn"

    def test_a_command_that_ran_keeps_focus_where_it_went(self, page: Any) -> None:
        _open(page, "#library-search")
        page.keyboard.type("sync")
        page.keyboard.press("Enter")
        page.wait_for_timeout(100)
        assert page.evaluate("window.__ran") == ["sync"]
        # Radix's own focus of the trigger was not cancelled: the command's result
        # (the new page's heading) takes focus from there.
        assert page.evaluate("window.__closeEvents") == [False]
        assert page.evaluate("document.activeElement.classList.contains('cmd-trigger')")

    def test_a_clicked_row_counts_as_a_command(self, page: Any) -> None:
        _open(page, "#library-search")
        page.click("#cmdk-opt-theme")
        page.wait_for_timeout(100)
        assert page.evaluate("window.__ran") == ["theme"]
        assert page.evaluate("document.activeElement.classList.contains('cmd-trigger')")

    def test_focus_the_user_moved_is_not_taken_back(self, page: Any) -> None:
        _open(page, "#library-search")
        page.evaluate("closePalette(); document.getElementById('notes').focus()")
        page.wait_for_timeout(100)
        assert _active(page) == "notes"

    def test_a_late_restore_from_an_earlier_close_is_ignored(self, page: Any) -> None:
        _open(page, "#library-search")
        # Close, then re-open with ⌘K from another control before the earlier close's
        # focus callback has run.
        page.evaluate(
            """() => { closePalette();
                const b = document.getElementById('plain-btn'); b.focus();
                b.dispatchEvent(new KeyboardEvent('keydown',
                  {key: 'k', metaKey: true, bubbles: true, cancelable: true})); }"""
        )
        page.wait_for_timeout(100)  # the stale callback has run by now
        assert _is_open(page)
        assert _active(page) == "cmdk-input"
        # It neither moved focus nor let the trigger take it from the open palette.
        assert page.evaluate("window.__closeEvents") == [True]
        assert page.evaluate("window.__pmPalette.restoreFocus()") is False
        assert _active(page) == "cmdk-input"
        assert page.evaluate("window.__pmPalette.returnTo.id") == "plain-btn"
        page.keyboard.press("Escape")
        _wait_active(page, "plain-btn")

    def test_reopening_starts_fresh(self, page: Any) -> None:
        _open(page, "#library-search")
        page.keyboard.type("sync")
        page.keyboard.press("Enter")  # ran = true
        # The close hands focus to the trigger a tick later (a command ran). Let that
        # finish first: landing between the focus and the ⌘K below, it would make the
        # trigger the opener that Escape returns to.
        page.wait_for_function("window.__closeEvents.length === 1", timeout=5000)
        _open(page, "#plain-btn")
        assert page.evaluate("window.__pmPalette.ran") is False
        assert _selected(page) == ["cmdk-opt-generate"]
        assert _status(page) == "12 commands"
        page.keyboard.press("Escape")
        _wait_active(page, "plain-btn")


# ── 4.5 form helpers ─────────────────────────────────────────────────────


class TestFormHelpers:
    def test_enter_in_a_filter_form_submits_once(self, page: Any) -> None:
        page.focus("#library-search")
        page.keyboard.type("coltrane")
        page.keyboard.press("Enter")
        assert page.evaluate("window.__submits") == [
            {"form": "filters", "data": {"q": "coltrane", "year_min": ""}}
        ]

    def test_enter_in_any_filter_input_submits(self, page: Any) -> None:
        page.focus("#f-year")
        page.keyboard.type("1995")
        page.keyboard.press("Enter")
        page.focus("#f-audio")
        page.keyboard.press("Enter")
        assert [s["data"]["year_min"] for s in page.evaluate("window.__submits")] == [
            "1995",
            "1995",
        ]

    def test_partial_year_does_not_block_enter(self, page: Any) -> None:
        page.focus("#f-year")
        page.keyboard.type("19")  # rangeUnderflow: requestSubmit would show a bubble
        page.focus("#library-search")
        page.keyboard.type("jazz")
        page.keyboard.press("Enter")
        assert page.evaluate("window.__submits") == [
            {"form": "filters", "data": {"q": "jazz", "year_min": "19"}}
        ]
        assert page.evaluate("document.getElementById('filters').noValidate") is False
        assert _active(page) == "library-search"

    def test_submit_button_form_submits_exactly_once(self, page: Any) -> None:
        page.focus("#t-genre")
        page.keyboard.type("jazz")
        page.keyboard.press("Enter")
        assert page.evaluate("window.__submits") == [
            {"form": "tag-filters", "data": {"genre": "jazz"}}
        ]

    def test_disabled_default_button_blocks_enter(self, page: Any) -> None:
        page.evaluate("document.getElementById('t-preview').disabled = true")
        page.focus("#t-genre")
        page.keyboard.press("Enter")
        assert page.evaluate("window.__submits") == []

    def test_forms_without_the_marker_are_left_alone(self, page: Any) -> None:
        page.focus("#p1")
        page.keyboard.type("x")
        page.keyboard.press("Enter")
        assert page.evaluate("window.__submits") == []

    @pytest.mark.parametrize("extra", [{"isComposing": True}, {"keyCode": 229}])
    def test_enter_while_composing_does_not_submit(self, page: Any, extra: dict[str, Any]) -> None:
        page.focus("#library-search")
        assert not _composing_enter(page, "#library-search", **extra)
        assert page.evaluate("window.__submits") == []

    def test_enter_none_suppresses_enter(self, page: Any) -> None:
        page.focus("#gen-genre")
        page.keyboard.type("jazz")
        for combo in ("Enter", "Meta+Enter", "Control+Enter"):
            page.keyboard.press(combo)
        assert page.evaluate("window.__submits") == []
        assert page.input_value("#gen-genre") == "jazz"

    @pytest.mark.parametrize("combo", ["Meta+Enter", "Control+Enter"])
    def test_mod_enter_submits_the_prompt_as_shown(self, page: Any, combo: str) -> None:
        page.focus("#gen-prompt")
        page.keyboard.type("sunday morning coffee")
        page.keyboard.press(combo)
        assert page.evaluate("window.__submits") == [
            {"form": "prompt-form", "data": {"prompt": "sunday morning coffee", "genre": ""}}
        ]
        assert page.input_value("#gen-prompt") == "sunday morning coffee"

    def test_plain_and_shift_enter_insert_newlines(self, page: Any) -> None:
        page.focus("#gen-prompt")
        page.keyboard.type("a")
        page.keyboard.press("Enter")
        page.keyboard.type("b")
        page.keyboard.press("Shift+Enter")
        assert page.input_value("#gen-prompt") == "a\nb\n"
        assert page.evaluate("window.__submits") == []

    def test_mod_enter_while_composing_does_not_submit(self, page: Any) -> None:
        page.focus("#gen-prompt")
        page.keyboard.type("x")
        _composing_enter(page, "#gen-prompt", metaKey=True, isComposing=True)
        _composing_enter(page, "#gen-prompt", ctrlKey=True, keyCode=229)
        assert page.evaluate("window.__submits") == []

    def test_mod_enter_respects_a_disabled_generate(self, page: Any) -> None:
        page.evaluate("document.getElementById('gen-submit').disabled = true")
        page.focus("#gen-prompt")
        page.keyboard.type("x")
        page.keyboard.press("Meta+Enter")
        assert page.evaluate("window.__submits") == []
        assert page.input_value("#gen-prompt") == "x"

    def test_counter_rewrites_the_same_text_node(self, page: Any) -> None:
        page.evaluate("window.__node = document.getElementById('gen-prompt-count').firstChild")
        page.focus("#gen-prompt")
        page.keyboard.type("hello world!")
        assert page.inner_text("#gen-prompt-count") == "12/280"
        assert page.evaluate(
            "document.getElementById('gen-prompt-count').firstChild === window.__node"
        )
        page.keyboard.press("Backspace")
        assert page.inner_text("#gen-prompt-count") == "11/280"

    def test_counter_and_limit_on_paste_and_typing(self, page: Any) -> None:
        page.focus("#gen-prompt")
        page.keyboard.insert_text("x" * 300)
        assert len(page.input_value("#gen-prompt")) == 280
        assert page.inner_text("#gen-prompt-count") == "280/280"
        page.keyboard.type("y")
        assert len(page.input_value("#gen-prompt")) == 280
        assert page.inner_text("#gen-prompt-count") == "280/280"

    def test_counter_creates_a_text_node_when_empty(self, page: Any) -> None:
        page.evaluate("document.getElementById('gen-prompt-count').textContent = ''")
        page.focus("#gen-prompt")
        page.keyboard.type("ab")
        assert page.inner_text("#gen-prompt-count") == "2/280"

    def test_counter_skips_composition_and_counts_on_compositionend(self, page: Any) -> None:
        page.focus("#gen-prompt")
        page.evaluate(
            """() => { const t = document.getElementById('gen-prompt'); t.value = 'nihon';
                t.dispatchEvent(new InputEvent('input', {bubbles: true, isComposing: true})); }"""
        )
        assert page.inner_text("#gen-prompt-count") == "0/280"
        page.evaluate(
            """() => { const t = document.getElementById('gen-prompt'); t.value = '日本';
                t.dispatchEvent(new CompositionEvent('compositionend',
                  {bubbles: true, data: '日本'})); }"""
        )
        assert page.inner_text("#gen-prompt-count") == "2/280"

    def test_real_ime_composition_ends_with_the_committed_count(self, page: Any) -> None:
        page.focus("#gen-prompt")
        page.keyboard.type("ab")
        page.keyboard.insert_text("日本語")  # committed like an IME / paste
        assert page.inner_text("#gen-prompt-count") == "5/280"
