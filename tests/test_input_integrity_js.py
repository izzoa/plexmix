"""Browser tests for the text-input integrity helpers in ``assets/plexmix.js`` (block 2c).

Covers the fix-step additions to webui-input-responsiveness:

- popstate (browser Back/Forward): the focused text field of a form is blurred
  before React Router unmounts the page, so a compose field's blur commit (the
  Generator prompt draft) is still sent. React ignores events fired while it
  commits a render, so without this the draft is lost.
- ``data-clear-form``: Library's Clear empties its form's text fields in the click
  itself; the server returns no ``rx.set_value`` that could wipe later typing.
- ``data-digits``: the year and stale-days filters accept ASCII digits only.

It also covers ``check_input_integrity.py``'s Generator row on a profile where Generate is
gated by readiness (``generate_gate_reason``, ``generator_row_status`` and the
``GENERATE_GATE_JS`` probe on a static page).

The real script is loaded into a static page served through Playwright request
interception (no port is bound). The page simulates the two React behaviours that
matter: ``onBlur`` commits are dispatched from a root ``focusout`` listener, which
drops events while a render is being committed, and React Router re-renders the
route from its own ``popstate`` listener, added after plexmix.js (as the app bundle
loads after the ``<head>`` script). The module is skipped when Playwright or its
Chromium build is missing.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

_SCRIPT = Path(__file__).resolve().parents[1] / "assets" / "plexmix.js"
_ORIGIN = "https://inputs.test"

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
__SCRIPT__
</head><body>
<main id="main-content"><div id="route"></div>
<input id="outside" name="outside" aria-label="Not in a form"></main>
<script>
window.__commits = []; window.__changes = []; window.__committing = false;
// React's root listeners: onBlur commits are sent from focusout; while a render is
// being committed ReactDOM's event system is disabled and the event is dropped.
document.addEventListener('focusout', function (e) {
  if (window.__committing) return;
  var t = e.target;
  if (t && t.name) window.__commits.push([t.name, t.value]);
}, true);
document.addEventListener('input', function (e) {
  if (e.target && e.target.name) window.__changes.push([e.target.name, e.target.value]);
}, true);
var PAGES = {
  '/generator': '<form id="prompt-form"><textarea id="gen-prompt" name="prompt" ' +
    'maxlength="280"></textarea><input id="gen-genre" name="genre">' +
    '<input id="gen-range" name="max_tracks" type="range">' +
    '<button type="submit">Generate</button></form>',
  '/library': '<form id="filters" data-enter-submit="">' +
    '<input id="lib-search" name="search"><input id="lib-genre" name="genre">' +
    '<input id="lib-year-min" name="year_min" type="number" min="1000" max="9999" ' +
    'inputmode="numeric" data-digits=""><input id="lib-audio" name="has_audio" ' +
    'type="checkbox"><button type="button" id="lib-clear" data-clear-form="">' +
    '<span>Clear</span></button></form>',
  '/tagging': '<form id="tag-filters"><input id="stale" name="stale_days" type="number" ' +
    'min="0" step="1" data-digits=""><input id="tag-genre" name="genre"></form>'
};
function render() {                        // a React commit: old route out, new route in
  if (window.__frozen) return;             // tests: a popstate that keeps the route
  window.__committing = true;
  document.getElementById('route').innerHTML = PAGES[location.pathname] || '';
  window.__committing = false;
}
window.addEventListener('popstate', render);   // React Router, after plexmix.js
window.navigate = function (to) { history.pushState({}, '', to); render(); };
render();
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

    def _make(*, path: str = "/library", with_script: bool = True) -> Any:
        ctx = browser.new_context()
        contexts.append(ctx)
        pg = ctx.new_page()
        script = '<script src="/plexmix.js"></script>' if with_script else ""
        html = _PAGE.replace("__SCRIPT__", script)

        def _serve(route: Any) -> None:
            if route.request.url.endswith("/plexmix.js"):
                route.fulfill(
                    body=_SCRIPT.read_text(encoding="utf-8"),
                    content_type="application/javascript",
                )
            else:
                route.fulfill(body=html, content_type="text/html")

        pg.route(f"{_ORIGIN}/**", _serve)
        pg.goto(f"{_ORIGIN}{path}")
        return pg

    yield _make
    for ctx in contexts:
        ctx.close()


@pytest.fixture()
def page(make_page: PageFactory) -> Any:
    return make_page()


def _active(pg: Any) -> str:
    return str(pg.evaluate("document.activeElement.id || document.activeElement.tagName"))


def _back(pg: Any, path: str) -> None:
    pg.evaluate("history.back()")
    pg.wait_for_function(f"location.pathname === {path!r}", timeout=2000)


class TestBackKeepsDrafts:
    def test_harness_reproduces_the_lost_draft_without_the_script(
        self, make_page: PageFactory
    ) -> None:
        pg = make_page(with_script=False)
        pg.evaluate("navigate('/generator')")
        pg.focus("#gen-prompt")
        pg.keyboard.type("late night drive")
        _back(pg, "/library")
        assert pg.evaluate("window.__commits") == []

    def test_back_commits_the_prompt_draft_first(self, page: Any) -> None:
        page.evaluate("navigate('/generator')")
        page.focus("#gen-prompt")
        page.keyboard.type("late night drive")
        _back(page, "/library")
        assert page.evaluate("window.__commits") == [["prompt", "late night drive"]]

    def test_forward_commits_a_filter_draft_too(self, page: Any) -> None:
        page.evaluate("navigate('/generator'); history.back()")
        page.wait_for_function("location.pathname === '/library'", timeout=2000)
        page.focus("#lib-search")
        page.keyboard.type("coltrane")
        page.evaluate("history.forward()")
        page.wait_for_function("location.pathname === '/generator'", timeout=2000)
        assert ["search", "coltrane"] in page.evaluate("window.__commits")

    def test_fields_outside_a_form_keep_focus(self, page: Any) -> None:
        page.evaluate("navigate('/generator')")
        page.focus("#outside")
        _back(page, "/library")
        assert _active(page) == "outside"

    @pytest.mark.parametrize("selector", ["#gen-range", "#outside"])
    def test_controls_without_text_keep_focus(self, page: Any, selector: str) -> None:
        page.evaluate("navigate('/generator'); window.__frozen = true")
        page.focus(selector)
        page.evaluate("window.dispatchEvent(new PopStateEvent('popstate'))")
        assert _active(page) == selector[1:]

    def test_pagehide_commits_the_focused_field(self, page: Any) -> None:
        page.evaluate("navigate('/generator')")
        page.focus("#gen-prompt")
        page.keyboard.type("rainy day")
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
        assert page.evaluate("window.__commits") == [["prompt", "rainy day"]]


class TestClearForm:
    def test_clear_empties_the_text_fields_in_the_click(self, page: Any) -> None:
        page.fill("#lib-search", "coltrane")
        page.fill("#lib-genre", "jazz")
        page.fill("#lib-year-min", "1995")
        page.check("#lib-audio")
        result = page.evaluate(
            """() => { document.querySelector('#lib-clear span').click();
                return ['lib-search', 'lib-genre', 'lib-year-min']
                  .map(id => document.getElementById(id).value); }"""
        )
        assert result == ["", "", ""]
        # The controlled checkbox follows the server's has_audio, not the click.
        assert page.is_checked("#lib-audio")

    def test_typing_after_clear_is_kept(self, page: Any) -> None:
        page.fill("#lib-search", "coltrane")
        page.click("#lib-clear")
        page.focus("#lib-search")
        page.keyboard.type("miles")
        page.wait_for_timeout(400)  # longer than Clear's debounce: nothing writes the field
        assert page.input_value("#lib-search") == "miles"

    def test_keyboard_activation_clears_too(self, page: Any) -> None:
        page.fill("#lib-genre", "rock")
        page.focus("#lib-clear")
        page.keyboard.press("Enter")
        assert page.input_value("#lib-genre") == ""


class TestDigitsOnly:
    @pytest.mark.parametrize(
        ("selector", "path"), [("#stale", "/tagging"), ("#lib-year-min", "/library")]
    )
    def test_only_digits_can_be_typed(
        self, make_page: PageFactory, selector: str, path: str
    ) -> None:
        pg = make_page(path=path)
        pg.focus(selector)
        pg.keyboard.type("-1e9.9+5a")
        assert pg.input_value(selector) == "1995"

    def test_paste_keeps_only_the_digits(self, page: Any) -> None:
        page.focus("#lib-year-min")
        prevented = page.evaluate(
            """() => { const dt = new DataTransfer(); dt.setData('text/plain', ' 1,995 ');
                const ev = new ClipboardEvent('paste',
                  {clipboardData: dt, bubbles: true, cancelable: true});
                document.getElementById('lib-year-min').dispatchEvent(ev);
                return ev.defaultPrevented; }"""
        )
        assert prevented is True
        assert page.input_value("#lib-year-min") == "1995"
        # Inserted as typed text: the change handler saw it.
        assert page.evaluate("window.__changes").pop() == ["year_min", "1995"]

    def test_digit_only_paste_is_left_to_the_browser(self, page: Any) -> None:
        page.focus("#lib-year-min")
        prevented = page.evaluate(
            """() => { const dt = new DataTransfer(); dt.setData('text/plain', '2001');
                const ev = new ClipboardEvent('paste',
                  {clipboardData: dt, bubbles: true, cancelable: true});
                document.getElementById('lib-year-min').dispatchEvent(ev);
                return ev.defaultPrevented; }"""
        )
        assert prevented is False

    def test_other_fields_are_untouched(self, page: Any) -> None:
        page.focus("#lib-search")
        page.keyboard.type("-1e9 abc")
        assert page.input_value("#lib-search") == "-1e9 abc"

    def test_deleting_still_works(self, make_page: PageFactory) -> None:
        pg = make_page(path="/tagging")
        pg.focus("#stale")
        pg.keyboard.type("305")
        pg.keyboard.press("Backspace")
        assert pg.input_value("#stale") == "30"


# ── check_input_integrity.py: the Generator row where Generate is gated ──

_CHECKER = Path(__file__).resolve().parents[1] / "check_input_integrity.py"

_GATED_LINE = (
    '<p class="rl-facts"><span class="mono">0 tracks</span> · <span>Local</span> · '
    "<span>not ready</span> · <span>prompt stays on this machine</span></p>"
    '<p class="rl-issue">No tracks synced yet</p>'
)
_GEN_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form class="prompt-box"><textarea id="gen-prompt" name="prompt"></textarea>
<button type="submit" __DISABLED__ aria-describedby="__DESCRIBEDBY__">Generate</button>
<div id="gen-ready-line" role="status" class="ready-line">__LINE__</div></form>
</body></html>"""


@pytest.fixture(scope="module")
def checker() -> Iterator[Any]:
    """``check_input_integrity`` loaded from the repo root (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("check_input_integrity", _CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while building
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def _gate(**overrides: Any) -> dict[str, Any]:
    gate: dict[str, Any] = {
        "found": True,
        "disabled": True,
        "describedby": "gen-ready-line",
        "readyLine": "0 tracks · Local · not ready\nNo tracks synced yet",
    }
    gate.update(overrides)
    return gate


class TestGenerateGateReason:
    def test_disabled_and_described_by_the_readiness_line_is_a_gate(self, checker: Any) -> None:
        assert checker.generate_gate_reason(_gate()) == (
            "Generate is gated: 0 tracks · Local · not ready No tracks synced yet"
        )
        both = _gate(describedby="gen-prompt-hint gen-ready-line")
        assert checker.generate_gate_reason(both).startswith("Generate is gated: ")

    @pytest.mark.parametrize(
        "overrides",
        [
            {"disabled": False},
            {"found": False, "disabled": False, "describedby": ""},
            {"describedby": ""},
            {"describedby": "gen-prompt-hint"},
            {"readyLine": "   \n "},
        ],
        ids=["enabled", "no-button", "not-described", "other-description", "empty-line"],
    )
    def test_anything_else_is_not_a_gate(self, checker: Any, overrides: dict[str, Any]) -> None:
        assert checker.generate_gate_reason(_gate(**overrides)) == ""


class TestGeneratorRowStatus:
    def _results(self, **oks: Any) -> dict[str, dict[str, Any]]:
        keys = ("counter", "submit", "max_length", "blank_submit")
        return {key: {"ok": oks.get(key, True)} for key in keys}

    def test_ungated_and_all_pass(self, checker: Any) -> None:
        assert checker.generator_row_status(self._results()) == checker.PASS

    def test_gated_with_typing_checks_passing_is_a_skip(self, checker: Any) -> None:
        results = self._results(submit=None, blank_submit=None)
        reason = checker.generate_gate_reason(_gate())
        assert checker.generator_row_status(results, reason) == checker.SKIP

    def test_gated_but_a_typing_check_fails_is_a_fail(self, checker: Any) -> None:
        results = self._results(submit=None, blank_submit=None, max_length=False)
        reason = checker.generate_gate_reason(_gate())
        assert checker.generator_row_status(results, reason) == checker.FAIL

    def test_ungated_failures_stay_failures(self, checker: Any) -> None:
        assert checker.generator_row_status(self._results(submit=False)) == checker.FAIL
        # A skipped sub-check without a gate reason is not a pass either.
        assert checker.generator_row_status(self._results(submit=None)) == checker.FAIL
        assert checker.generator_row_status({}) == checker.FAIL


class TestGenerateGateProbe:
    def _probe(
        self, browser: Any, checker: Any, *, disabled: bool, describedby: str, line: str
    ) -> Any:
        ctx = browser.new_context()
        try:
            pg = ctx.new_page()
            html = (
                _GEN_PAGE.replace("__DISABLED__", "disabled" if disabled else "")
                .replace("__DESCRIBEDBY__", describedby)
                .replace("__LINE__", line)
            )
            pg.set_content(html)  # no URL, no port: the page is set in place
            loaded = pg.evaluate(checker.READY_LINE_LOADED_JS)
            return loaded, pg.evaluate(checker.GENERATE_GATE_JS)
        finally:
            ctx.close()

    def test_reads_a_gated_button_and_the_readiness_line(self, browser: Any, checker: Any) -> None:
        loaded, gate = self._probe(
            browser, checker, disabled=True, describedby="gen-ready-line", line=_GATED_LINE
        )
        assert loaded is True
        assert gate["found"] is True and gate["disabled"] is True
        assert gate["describedby"] == "gen-ready-line"
        reason = checker.generate_gate_reason(gate)
        assert reason.startswith("Generate is gated: 0 tracks · Local · not ready")
        assert "No tracks synced yet" in reason

    def test_an_enabled_button_is_not_gated(self, browser: Any, checker: Any) -> None:
        _loaded, gate = self._probe(
            browser, checker, disabled=False, describedby="gen-ready-line", line=_GATED_LINE
        )
        assert gate["disabled"] is False
        assert checker.generate_gate_reason(gate) == ""

    def test_line_not_loaded_yet(self, browser: Any, checker: Any) -> None:
        loaded, gate = self._probe(
            browser, checker, disabled=False, describedby="gen-ready-line", line="Checking…"
        )
        assert loaded is False
        assert checker.generate_gate_reason(gate) == ""

    def test_gated_shortcut_expectation_holds_with_plexmix_js(
        self, browser: Any, checker: Any
    ) -> None:
        """The gated row's own sub-check: with the real plexmix.js, ⌘/Ctrl+Enter on a gated
        form submit nothing and leave the text as typed; plain Enter types a newline."""
        ctx = browser.new_context()
        try:
            pg = ctx.new_page()
            html = (
                _GEN_PAGE.replace("__DISABLED__", "disabled")
                .replace("__DESCRIBEDBY__", "gen-ready-line")
                .replace("__LINE__", _GATED_LINE)
                .replace(
                    '<textarea id="gen-prompt" name="prompt">',
                    '<textarea id="gen-prompt" name="prompt" data-submit-shortcut="">',
                )
            )
            pg.set_content(html)
            pg.add_script_tag(content=_SCRIPT.read_text(encoding="utf-8"))
            pg.evaluate(checker.SUBMIT_GUARD_JS, False)
            prompt = pg.locator("#gen-prompt")
            prompt.press_sequentially("late night drive")
            for chord in ("Control+Enter", "Meta+Enter"):
                prompt.press(chord)
            assert prompt.input_value() == "late night drive"
            prompt.press("Enter")
            assert prompt.input_value() == "late night drive\n"
            assert pg.evaluate("window.__pmCheckSubmits") == []
            # Contrast: once Generate is enabled the same chord is a submit the guard sees.
            pg.evaluate("document.querySelector('button[type=submit]').disabled = false")
            prompt.press("Control+Enter")
            submits = pg.evaluate("window.__pmCheckSubmits")
            assert [s["prompt"] for s in submits] == ["late night drive\n"]
        finally:
            ctx.close()
