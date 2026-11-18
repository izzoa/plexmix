"""Unit tests for the client-owned text field helpers (``plexmix.ui.components.inputs``).

Reflex is mocked here (``tests/ui/conftest.py``), so each test swaps
``rx.el.input``/``rx.el.textarea`` for a recording ``MagicMock`` and inspects
the props the helper passes. ``tests/ui_render/test_inputs_render.py`` renders
the same helpers with real Reflex.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def el(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Fresh recording mocks for ``rx.el.input``, ``rx.el.textarea`` and ``rx.Var.create``."""
    import reflex as rx

    monkeypatch.setattr(rx.el, "input", MagicMock(name="rx.el.input"))
    monkeypatch.setattr(rx.el, "textarea", MagicMock(name="rx.el.textarea"))
    monkeypatch.setattr(rx.Var, "create", MagicMock(name="rx.Var.create"))
    return rx


def _handler() -> MagicMock:
    """A stand-in Reflex event handler whose ``.debounce(ms)`` returns a marker."""
    handler = MagicMock(name="State.set_field")
    handler.debounce.return_value = MagicMock(name="State.set_field.debounce(300)")
    return handler


def _kwargs(mock: MagicMock) -> dict[str, Any]:
    mock.assert_called_once()
    args, kwargs = mock.call_args
    assert args == ()
    return dict(kwargs)


def test_commit_debounce_is_300ms() -> None:
    from plexmix.ui.components.inputs import COMMIT_DEBOUNCE_MS

    assert COMMIT_DEBOUNCE_MS == 300


def test_module_docstring_states_the_set_value_rule() -> None:
    import plexmix.ui.components.inputs as inputs

    doc = inputs.__doc__ or ""
    assert "rx.set_value(<field id>, <new value>)" in doc
    assert "MUST also return" in doc


def test_filter_input_is_uncontrolled_and_debounced(el: Any) -> None:
    from plexmix.ui.components.inputs import filter_input

    handler = _handler()
    value = MagicMock(name="State.field")
    result = filter_input(
        id="library-search",
        name="q",
        value=value,
        on_commit=handler,
        placeholder="Search tracks…",
        aria_label="Search tracks",
    )

    assert result is el.el.input.return_value
    kwargs = _kwargs(el.el.input)
    assert "value" not in kwargs
    assert kwargs["default_value"] is value
    handler.debounce.assert_called_once_with(300)
    assert kwargs["on_change"] is handler.debounce.return_value
    assert kwargs["on_blur"] is handler
    assert kwargs["id"] == "library-search"
    assert kwargs["name"] == "q"
    assert kwargs["auto_complete"] == "off"
    assert kwargs["spell_check"] is False
    assert kwargs["placeholder"] == "Search tracks…"
    assert kwargs["aria_label"] == "Search tracks"
    el.el.textarea.assert_not_called()


def test_compose_input_commits_on_blur_only(el: Any) -> None:
    from plexmix.ui.components.inputs import compose_input

    handler = _handler()
    value = MagicMock(name="State.field")
    compose_input(id="tag-edit-moods", name="tags", value=value, on_commit=handler)

    kwargs = _kwargs(el.el.input)
    assert "value" not in kwargs
    assert "on_change" not in kwargs
    assert kwargs["default_value"] is value
    assert kwargs["on_blur"] is handler
    handler.debounce.assert_not_called()
    assert kwargs["id"] == "tag-edit-moods"
    assert kwargs["name"] == "tags"
    assert kwargs["auto_complete"] == "off"
    assert kwargs["spell_check"] is False


def test_compose_textarea_commits_on_blur_only(el: Any) -> None:
    from plexmix.ui.components.inputs import compose_textarea

    handler = _handler()
    value = MagicMock(name="State.mood_query")
    result = compose_textarea(
        id="gen-prompt",
        name="prompt",
        value=value,
        on_commit=handler,
        max_length=280,
        custom_attrs={"data-submit-shortcut": "", "data-counter": "gen-prompt-count"},
    )

    assert result is el.el.textarea.return_value
    el.el.input.assert_not_called()
    kwargs = _kwargs(el.el.textarea)
    assert "value" not in kwargs
    assert "on_change" not in kwargs
    assert kwargs["default_value"] is value
    assert kwargs["on_blur"] is handler
    handler.debounce.assert_not_called()
    assert kwargs["name"] == "prompt"
    assert kwargs["max_length"] == 280
    assert kwargs["custom_attrs"] == {
        "data-submit-shortcut": "",
        "data-counter": "gen-prompt-count",
    }


@pytest.mark.parametrize("helper", ["filter_input", "compose_input", "compose_textarea"])
@pytest.mark.parametrize("prop", ["value", "on_change"])
def test_helpers_refuse_controlled_props(el: Any, helper: str, prop: str) -> None:
    import plexmix.ui.components.inputs as inputs

    build = getattr(inputs, helper)
    with pytest.raises(TypeError, match=prop):
        build(id="x", name="x", value="", on_commit=_handler(), **{prop: MagicMock()})
    el.el.input.assert_not_called()
    el.el.textarea.assert_not_called()


@pytest.mark.parametrize("helper", ["filter_input", "compose_input"])
def test_number_type_keeps_raw_string_events(el: Any, helper: str) -> None:
    """``type="number"`` is passed as a Var so the setter gets the raw text, not Number()."""
    import plexmix.ui.components.inputs as inputs

    getattr(inputs, helper)(
        id="lib-year-min", name="year_min", value="", on_commit=_handler(), type="number"
    )
    el.Var.create.assert_called_once_with("number")
    assert _kwargs(el.el.input)["type"] is el.Var.create.return_value


def test_text_types_pass_through_unchanged(el: Any) -> None:
    from plexmix.ui.components.inputs import filter_input

    filter_input(id="s", name="s", value="", on_commit=_handler(), type="search")
    el.Var.create.assert_not_called()
    assert _kwargs(el.el.input)["type"] == "search"


def test_defaults_can_be_overridden(el: Any) -> None:
    from plexmix.ui.components.inputs import filter_input

    filter_input(
        id="s", name="s", value="", on_commit=_handler(), auto_complete="on", spell_check=True
    )
    kwargs = _kwargs(el.el.input)
    assert kwargs["auto_complete"] == "on"
    assert kwargs["spell_check"] is True


def test_filter_input_needs_a_debounceable_handler(el: Any) -> None:
    from plexmix.ui.components.inputs import filter_input

    def plain_function(value: str) -> None:
        return None

    with pytest.raises(TypeError, match="event handler"):
        filter_input(id="s", name="s", value="", on_commit=plain_function)
    el.el.input.assert_not_called()
