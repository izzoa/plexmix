"""Render smoke: client-owned text field helpers compile as uncontrolled fields.

Run in its own session (real Reflex): ``pytest tests/ui_render -q``.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import pytest
import reflex as rx

from plexmix.ui.components.inputs import compose_input, compose_textarea, filter_input
from plexmix.ui.states.generator_state import GeneratorState
from tests.ui_render.conftest import rendered

# ``{"value": _e.target.value}`` — the raw string the browser holds.
_RAW_VALUE = '({ ["value"] : _e?.["target"]?.["value"] })'


def _prop(js: str, name: str) -> str:
    """The text of the ``name:`` prop up to the next top-level prop."""
    match = re.search(rf"(?<![\w\"]){name}:", js)
    assert match, f"{name} missing from {js}"
    return js[match.end() :]


def test_filter_input_compiles_uncontrolled_with_debounced_change() -> None:
    js = rendered(
        filter_input(
            id="gen-genre",
            name="genre",
            value=GeneratorState.genre_filter,
            on_commit=GeneratorState.set_genre_filter,
            placeholder="Any genre",
            custom_attrs={"data-enter": "none"},
        )
    )
    assert js.startswith('jsx("input",')
    assert "defaultValue:" in js
    assert not re.search(r"(?<![\w\"])value:", js), "a client-owned field has no value prop"
    on_change = _prop(js, "onChange")
    assert on_change.startswith("((_e) => (addEvents([(ReflexEvent(")
    assert ".set_genre_filter" in on_change.split("onBlur")[0]
    assert '({ ["debounce"] : 300 })' in on_change.split("])")[0]
    on_blur = _prop(js, "onBlur").split("onChange")[0]
    assert ".set_genre_filter" in on_blur
    assert "debounce" not in on_blur
    assert 'autoComplete:"off"' in js
    assert "spellCheck:false" in js
    assert 'id:"gen-genre"' in js and "ref:ref_gen_genre" in js
    assert 'name:"genre"' in js
    assert '"data-enter":"none"' in js


def test_compose_textarea_compiles_with_blur_commit_only() -> None:
    js = rendered(
        compose_textarea(
            id="gen-prompt",
            name="prompt",
            value=GeneratorState.mood_query,
            on_commit=GeneratorState.set_mood_query,
            max_length=280,
            custom_attrs={"data-submit-shortcut": "", "data-counter": "gen-prompt-count"},
        )
    )
    assert js.startswith('jsx("textarea",')
    assert "defaultValue:" in js
    assert not re.search(r"(?<![\w\"])value:", js)
    assert "onChange" not in js
    assert ".set_mood_query" in _prop(js, "onBlur")
    assert "maxLength:280" in js
    assert "ref:ref_gen_prompt" in js
    assert '"data-submit-shortcut":""' in js
    assert '"data-counter":"gen-prompt-count"' in js


def test_compose_input_compiles_with_blur_commit_only() -> None:
    js = rendered(
        compose_input(
            id="tag-edit-moods",
            name="tags",
            value=GeneratorState.genre_filter,
            on_commit=GeneratorState.set_genre_filter,
        )
    )
    assert js.startswith('jsx("input",')
    assert "defaultValue:" in js
    assert "onChange" not in js
    assert ".set_genre_filter" in _prop(js, "onBlur")
    assert 'autoComplete:"off"' in js and "spellCheck:false" in js


@pytest.mark.parametrize("helper", [filter_input, compose_input])
def test_number_fields_send_the_raw_string(helper: Callable[..., Any]) -> None:
    """A plain ``rx.el.input(type="number")`` would send ``Number(value)`` ("" -> 0)."""
    js = rendered(
        helper(
            id="probe-year",
            name="year",
            value=GeneratorState.year_min,
            on_commit=GeneratorState.set_year_min,
            type="number",
            input_mode="numeric",
            min="1000",
            max="9999",
        )
    )
    assert 'type:"number"' in js
    assert "Number(" not in js
    assert _RAW_VALUE in js
    assert 'inputMode:"numeric"' in js


def test_plain_number_input_would_coerce() -> None:
    """Guards the gotcha the helpers work around (fails if Reflex changes it)."""
    js = rendered(rx.el.input(type="number", default_value="", on_blur=GeneratorState.set_year_min))
    assert "Number(" in js
