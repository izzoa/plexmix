"""Browser tests for the Generator prompt form's submit focus script (headless Chromium).

``_SUBMIT_FOCUS_SCRIPT`` runs first in the prompt form's ``on_submit`` chain. A
non-blank submit chases ``#gen-error`` exactly like the old Generate ``on_click``
(``_FAILURE_FOCUS_SCRIPT``); a blank submit starts no generation, so it must not
chase, or the poll would land on a stale callout from an earlier failure.

The real ``assets/plexmix.js`` (pmFocus) is loaded into a static page served through
Playwright request interception, so no port is bound. Skipped when Playwright or its
Chromium build is missing. Lives in ``tests/ui_render`` because it reads the script
from the real page module (real Reflex): run ``pytest tests/ui_render -q``.

The page mirrors the prompt form's attributes (``test_generator_render.TestPromptForm``
pins them on the compiled markup), so ``TestPromptFormKeys`` checks the generator's
contract with the plexmix.js form helpers: ⌘/Ctrl+Enter submits, plain Enter adds a
line, Enter in the advanced controls never submits, and the counter follows typing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from plexmix.ui.pages import generator as gen_page

sync_api = pytest.importorskip("playwright.sync_api")

_SCRIPT = Path(__file__).resolve().parents[2] / "assets" / "plexmix.js"
_ORIGIN = "http://pmgenerator.test"

# The idle hero's focus-relevant structure (pages/generator.py), without React.
_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script src="/plexmix.js"></script></head><body>
<main id="main-content" tabindex="-1">
<h1 id="main-heading" tabindex="-1">What should your library sound like?</h1>
<form class="prompt-box"><textarea id="gen-prompt" name="prompt" maxlength="280"
 data-submit-shortcut="" data-counter="gen-prompt-count"></textarea>
<div class="prompt-foot"><button id="gen-opt" type="button">Options</button>
<span class="charcount" id="gen-prompt-count">0/280</span>
<button id="gen-go" type="submit">Generate</button></div>
<div class="adv-panel" id="gen-adv-panel">
<input id="gen-max-tracks" type="range" min="10" max="100" step="5" value="50" data-enter="none">
<input id="gen-genre" name="genre" class="input" data-enter="none">
<fieldset role="radiogroup" data-enter="none"><legend>Track ordering</legend>
<label><input id="gen-ord-sim" type="radio" name="gen-ordering" value="similarity" checked>
<span>Similarity</span></label></fieldset></div></form>
<div id="gen-prompt-hint" role="alert"></div>
<div id="gen-error-region" role="alert"></div>
</main></body></html>"""

# Longer than pmFocus.when's ~60-frame poll (about 1 s at 60 fps).
_PAST_POLL_MS = 1600


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
    pg.goto(f"{_ORIGIN}/generator")
    # Count submits the way Reflex's handleSubmit sees them (it prevents the default).
    pg.evaluate(
        """() => { window.__submits = [];
            document.querySelector('form.prompt-box').addEventListener('submit', (e) => {
                e.preventDefault();
                window.__submits.push(new FormData(e.target).get('prompt'));
            }); }"""
    )
    yield pg
    pg.close()


def _active(pg: Any) -> str:
    return str(pg.evaluate("document.activeElement.id || document.activeElement.tagName"))


def _stale_callout(pg: Any) -> None:
    """The error callout an earlier failed generation left on screen."""
    pg.evaluate(
        """() => { const c = document.createElement('div'); c.id = 'gen-error';
                   c.tabIndex = -1; c.textContent = 'Database not found.';
                   document.getElementById('gen-error-region').appendChild(c); }"""
    )


def _submit(pg: Any, prompt: str) -> None:
    """Type the prompt, focus Generate and run the form's first on_submit event."""
    pg.fill("#gen-prompt", prompt)
    pg.focus("#gen-go")
    pg.evaluate(gen_page._SUBMIT_FOCUS_SCRIPT)


class TestSubmitFocusScript:
    @pytest.mark.parametrize("prompt", ["", "   ", "\n\t "])
    def test_blank_submit_never_lands_on_a_stale_callout(self, page: Any, prompt: str) -> None:
        _stale_callout(page)
        _submit(page, prompt)
        page.wait_for_timeout(_PAST_POLL_MS)
        assert _active(page) == "gen-go"

    def test_prompt_submit_chases_a_new_callout(self, page: Any) -> None:
        _submit(page, "late night drive")
        page.wait_for_timeout(100)
        # A fast failure: the callout appears without the stage ever changing.
        _stale_callout(page)
        page.wait_for_function("document.activeElement.id === 'gen-error'", timeout=3000)

    def test_prompt_submit_accepts_a_callout_reused_in_place(self, page: Any) -> None:
        # Unchanged from _FAILURE_FOCUS_SCRIPT: the excluded earlier callout is accepted
        # when it is still there at poll expiry (React re-used it for the new error).
        _stale_callout(page)
        _submit(page, "late night drive")
        page.wait_for_function("document.activeElement.id === 'gen-error'", timeout=3000)

    def test_vibe_pill_script_is_the_unguarded_chase(self) -> None:
        # The pills always carry text, so they keep the plain failure chase.
        assert gen_page._FAILURE_FOCUS_SCRIPT in gen_page._SUBMIT_FOCUS_SCRIPT
        assert "gen-prompt" not in gen_page._FAILURE_FOCUS_SCRIPT


class TestPromptLimitInTheBrowser:
    """The textarea's ``maxlength`` (rendered from ``PROMPT_MAX_CHARS``) caps input."""

    def test_typing_past_the_limit_is_ignored(self, page: Any) -> None:
        page.fill("#gen-prompt", "a" * 280)
        page.focus("#gen-prompt")
        page.keyboard.press("End")
        page.keyboard.type("b")
        assert page.eval_on_selector("#gen-prompt", "el => el.value") == "a" * 280

    def test_pasting_past_the_limit_keeps_the_first_280(self, page: Any) -> None:
        text = "".join(chr(ord("a") + i % 26) for i in range(300))
        page.focus("#gen-prompt")
        page.keyboard.insert_text(text)
        assert page.eval_on_selector("#gen-prompt", "el => el.value") == text[:280]

    def test_page_renders_the_same_limit(self) -> None:
        assert gen_page.PROMPT_MAX_CHARS == 280


def _submits(pg: Any) -> list[str]:
    return list(pg.evaluate("window.__submits"))


class TestPromptFormKeys:
    @pytest.mark.parametrize("chord", ["Meta+Enter", "Control+Enter"])
    def test_modifier_enter_submits_the_typed_prompt(self, page: Any, chord: str) -> None:
        page.focus("#gen-prompt")
        page.keyboard.type("sunday morning coffee")
        page.keyboard.press(chord)
        assert _submits(page) == ["sunday morning coffee"]
        assert page.eval_on_selector("#gen-prompt", "el => el.value") == "sunday morning coffee"

    @pytest.mark.parametrize("key", ["Enter", "Shift+Enter"])
    def test_plain_enter_adds_a_line(self, page: Any, key: str) -> None:
        page.focus("#gen-prompt")
        page.keyboard.type("jazz")
        page.keyboard.press(key)
        page.keyboard.type("for cooking")
        assert _submits(page) == []
        assert page.eval_on_selector("#gen-prompt", "el => el.value") == "jazz\nfor cooking"

    @pytest.mark.parametrize("target", ["#gen-genre", "#gen-max-tracks", "#gen-ord-sim"])
    def test_enter_in_advanced_controls_never_submits(self, page: Any, target: str) -> None:
        page.fill("#gen-prompt", "jazz")
        page.focus(target)
        page.keyboard.press("Enter")
        page.wait_for_timeout(50)
        assert _submits(page) == []

    def test_generate_click_submits(self, page: Any) -> None:
        page.fill("#gen-prompt", "late night drive")
        page.click("#gen-go")
        assert _submits(page) == ["late night drive"]

    def test_counter_follows_typing(self, page: Any) -> None:
        page.focus("#gen-prompt")
        page.keyboard.type("twelve chars")
        assert page.text_content("#gen-prompt-count") == "12/280"
        page.keyboard.press("Backspace")
        assert page.text_content("#gen-prompt-count") == "11/280"


# ── honest-status 7.4: the readiness gate and focus after chip/callout removal ──


class TestReadinessGateKeys:
    """Generate is ``disabled`` while a blocking readiness cause stops generation."""

    @pytest.mark.parametrize("chord", ["Meta+Enter", "Control+Enter"])
    def test_disabled_generate_blocks_the_shortcut(self, page: Any, chord: str) -> None:
        page.evaluate("document.getElementById('gen-go').disabled = true")
        page.focus("#gen-prompt")
        page.keyboard.type("late night drive")
        page.keyboard.press(chord)
        page.wait_for_timeout(50)
        assert _submits(page) == []
        # The typed prompt is kept.
        assert page.eval_on_selector("#gen-prompt", "el => el.value") == "late night drive"

    def test_enabled_again_submits(self, page: Any) -> None:
        page.evaluate("document.getElementById('gen-go').disabled = true")
        page.evaluate("document.getElementById('gen-go').disabled = false")
        page.focus("#gen-prompt")
        page.keyboard.type("late night drive")
        page.keyboard.press("Control+Enter")
        assert _submits(page) == ["late night drive"]


_CHIPS = """() => {
  const row = document.createElement('div'); row.className = 'chip-x-row';
  const ul = document.createElement('ul'); ul.className = 'chip-x-list';
  for (const k of ['tempo', 'energy']) {
    const li = document.createElement('li'); li.className = 'chip-x'; li.id = 'chip-' + k;
    const b = document.createElement('button'); b.type = 'button';
    b.className = 'chip-x-btn'; b.id = 'chip-btn-' + k; b.textContent = 'x';
    li.appendChild(b); ul.appendChild(li);
  }
  row.appendChild(ul);
  document.querySelector('.prompt-foot').after(row);
}"""


class TestChipAndDismissFocus:
    def _remove(self, pg: Any, selector: str) -> None:
        # What React does once remove_constraint's delta lands: the chip leaves the DOM.
        pg.evaluate(f"document.querySelector('{selector}').remove()")

    def test_removing_a_chip_focuses_the_next_chip(self, page: Any) -> None:
        page.evaluate(_CHIPS)
        page.focus("#chip-btn-tempo")
        page.evaluate(gen_page._CHIP_FOCUS_SCRIPT)
        self._remove(page, "#chip-tempo")
        page.wait_for_function("document.activeElement.id === 'chip-btn-energy'", timeout=3000)

    def test_removing_the_last_chip_focuses_the_prompt(self, page: Any) -> None:
        page.evaluate(_CHIPS)
        self._remove(page, "#chip-tempo")
        page.focus("#chip-btn-energy")
        page.evaluate(gen_page._CHIP_FOCUS_SCRIPT)
        self._remove(page, ".chip-x-row")
        page.wait_for_function("document.activeElement.id === 'gen-prompt'", timeout=3000)

    def test_dismiss_lands_on_the_prompt(self, page: Any) -> None:
        _stale_callout(page)
        page.evaluate(
            """() => { const b = document.createElement('button'); b.id = 'gen-dismiss';
                       document.getElementById('gen-error').appendChild(b); }"""
        )
        page.focus("#gen-dismiss")
        page.evaluate(gen_page._PROMPT_FOCUS_SCRIPT)
        self._remove(page, "#gen-error")
        page.wait_for_function("document.activeElement.id === 'gen-prompt'", timeout=3000)

    def test_focus_stays_put_when_it_was_not_lost(self, page: Any) -> None:
        # A chip removed by someone else's action never steals focus from the prompt.
        page.evaluate(_CHIPS)
        page.focus("#gen-prompt")
        page.evaluate(gen_page._CHIP_FOCUS_SCRIPT)
        self._remove(page, "#chip-tempo")
        page.wait_for_timeout(_PAST_POLL_MS)
        assert _active(page) == "gen-prompt"
