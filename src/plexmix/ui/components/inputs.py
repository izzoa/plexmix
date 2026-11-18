"""Client-owned text fields: the browser owns the text, the server receives commits.

Every free-text field outside Settings is built with one of these helpers
(webui-input-responsiveness, design D1-D4). A field is *uncontrolled*: it takes
its initial text from ``default_value`` and never has a ``value=`` prop, so React
never writes the DOM value after mount and no server echo can overwrite what the
user is typing, at any latency. The server receives the text through the field's
own setter (``on_commit``):

- ``filter_input``: commits ``COMMIT_DEBOUNCE_MS`` after the last key (the latest
  value wins) and immediately on blur. Enter commits through the surrounding
  form's ``on_submit`` (``assets/plexmix.js`` submits ``form[data-enter-submit]``).
- ``compose_input`` / ``compose_textarea``: commit on blur only, with no per-key
  traffic. The action that uses them reads the value from the form on submit.

Setters must be idempotent (return early when the value is unchanged), because a
blur commit and a pending debounced commit can both arrive with the same text.
Give every field its own setter: the client debounce timer is keyed by handler
name, so two fields sharing one setter would cancel each other's commits.

Rule for server-initiated writes (design D3)
--------------------------------------------
Any event handler that changes a client-owned field's state value for a reason
other than that field's own commit (a History rerun, a palette prefill, a
template, a programmatic filter reset, ...) MUST also return
``rx.set_value(<field id>, <new value>)``. A reset the user starts with a button
on the form itself (Library's Clear) is better done in the browser, in the click:
mark the button ``data-clear-form`` (``assets/plexmix.js`` empties the form's text
fields at once) and return no ``set_value``, which would arrive a round trip later
and wipe anything typed in between. Field ids are module constants shared
by the page and its state (for example ``PROMPT_INPUT_ID = "gen-prompt"`` in
``generator_state.py``). Changing the state alone is not enough: React writes a
changed ``defaultValue`` to the DOM, but the browser only shows it while the
user has not edited the field, so an edited field would keep its old text. A
``set_value`` aimed at a field that is not mounted is a no-op, and a field
mounted later starts from the current state through ``default_value``. Never
return ``set_value`` from the field's own commit setter: that would be the echo
this pattern exists to prevent.

Reflex API verified against the installed Reflex 0.8.24.post1
-------------------------------------------------------------
- ``EventHandler`` and ``EventSpec`` both carry ``.debounce(ms)`` (and
  ``.throttle``/``.prevent_default``/``.stop_propagation``) from
  ``reflex.event.EventActionsMixin``. ``.debounce(300)`` returns a copy with
  ``event_actions={"debounce": 300}``, compiled as
  ``ReflexEvent("<state>.<handler>", {value: _e.target.value}, {debounce: 300})``.
  The client (``addEvents`` in ``.templates/web/utils/state.js`` and
  ``utils/helpers/debounce.js``) restarts one timer per key
  ``"<event names>__<ms>"`` on every call, so the latest arguments win and each
  handler name has its own timer. ``rx.event(debounce=ms)`` also exists, but it
  applies to every call site of the handler; the per-call ``.debounce()`` keeps
  the setter undebounced for blur and form commits.
- ``rx.el.input`` (``BaseInput``) and ``rx.el.textarea`` accept
  ``default_value`` (``defaultValue``), ``auto_complete`` (``autoComplete``),
  ``max_length`` (``maxLength``), ``name`` and, from ``BaseHTML``,
  ``spell_check`` (``spellCheck``) and ``input_mode`` (``inputMode``).
  ``custom_attrs={...}`` emits raw attributes (``data-*``); ``aria_*`` and
  ``data_*`` keyword arguments are moved into ``custom_attrs`` as kebab-case.
  An ``id="a-b"`` also gets ``ref:ref_a_b``, the ref ``rx.set_value("a-b", v)``
  writes (``ref.current.value = v`` on the client; no-op when unmounted).
- Gotcha: ``rx.el.input(type="number")`` (and ``"range"``) becomes
  ``ValueNumberInput``, whose change and blur events send
  ``Number(e.target.value)``: ``""`` arrives as ``0``. The helpers pass a
  ``"number"`` type as a ``Var`` so the plain ``Input`` is built and the setter
  receives the raw string, which numeric filters sanitize themselves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Union

import reflex as rx

if TYPE_CHECKING:
    from reflex.event import EventNamespace
    from reflex.vars.base import Var

__all__ = [
    "COMMIT_DEBOUNCE_MS",
    "CommitHandler",
    "compose_input",
    "compose_textarea",
    "filter_input",
]

# Filters commit this long after the last key (Reflex's own default debounce).
COMMIT_DEBOUNCE_MS = 300

# What mypy sees for a state event handler accessed on the class: a plain method
# (``Callable``) or, for ``@rx.event`` handlers, Reflex's ``EventNamespace``. At
# runtime it is a Reflex ``EventHandler``.
CommitHandler = Union[Callable[..., Any], "EventNamespace"]

# The value a field starts from: a state var (``State.field``), which mypy sees
# as its annotated Python type, or a literal.
FieldValue = Union["Var[Any]", str, int, float]
# A textarea's text (its ``default_value`` takes no numbers).
TextValue = Union["Var[Any]", str]


def _reject_controlled_props(helper: str, props: dict[str, Any], *names: str) -> None:
    """Raise ``TypeError`` when a caller passes props that would make the field controlled."""
    bad = sorted(name for name in names if name in props)
    if bad:
        raise TypeError(
            f"{helper}() builds a client-owned field and does not accept {', '.join(bad)}; "
            "pass the state var as value= (it becomes default_value) and its setter as "
            "on_commit="
        )


def _plain_text_type(props: dict[str, Any]) -> None:
    """Keep ``type="number"`` on the plain ``Input`` so setters receive the raw string."""
    if props.get("type") == "number":
        props["type"] = rx.Var.create("number")


def _debounced(on_commit: CommitHandler) -> Any:
    """Return ``on_commit.debounce(COMMIT_DEBOUNCE_MS)`` for a Reflex event handler."""
    debounce = getattr(on_commit, "debounce", None)
    if not callable(debounce):
        raise TypeError(
            "filter_input() needs a Reflex event handler (e.g. State.set_field) as on_commit, "
            f"got {on_commit!r}"
        )
    return debounce(COMMIT_DEBOUNCE_MS)


def filter_input(
    *,
    id: str,
    name: str,
    value: FieldValue,
    on_commit: CommitHandler,
    **props: Any,
) -> rx.Component:
    """A client-owned filter field that applies 300 ms after typing pauses.

    Args:
        id: Element id, shared with the state as a module constant so server
            resets can target it with ``rx.set_value(id, ...)``.
        name: Form field name (read by the surrounding form's ``on_submit``).
        value: The state var holding the committed value (rendered as
            ``default_value``; the field is never controlled).
        on_commit: The field's own idempotent setter. Called with the text
            ``COMMIT_DEBOUNCE_MS`` after the last change and immediately on blur.
        **props: Any other ``rx.el.input`` props (``placeholder``, ``class_name``,
            ``aria_label``, ``type``, ``custom_attrs``, ...). ``auto_complete``
            defaults to ``"off"`` and ``spell_check`` to ``False``.

    Returns:
        The ``<input>`` component.

    Raises:
        TypeError: if ``value``/``on_change`` are passed through ``props``, or
            ``on_commit`` has no ``.debounce``.
    """
    _reject_controlled_props("filter_input", props, "value", "on_change")
    _plain_text_type(props)
    props.setdefault("auto_complete", "off")
    props.setdefault("spell_check", False)
    return rx.el.input(
        id=id,
        name=name,
        default_value=value,
        on_change=_debounced(on_commit),
        on_blur=on_commit,
        **props,
    )


def compose_input(
    *,
    id: str,
    name: str,
    value: FieldValue,
    on_commit: CommitHandler,
    **props: Any,
) -> rx.Component:
    """A client-owned single-line compose field that commits on blur only.

    The action that uses it must read the value from the submitted form data
    (``rx.el.form(on_submit=...)``), never from state, since the blur commit may
    not have arrived yet.

    Args:
        id: Element id (also the ``rx.set_value`` target for server writes).
        name: Form field name, the key the form's ``on_submit`` handler reads.
        value: The state var holding the committed value (``default_value``).
        on_commit: The field's own idempotent setter, called on blur.
        **props: Any other ``rx.el.input`` props. ``auto_complete`` defaults to
            ``"off"`` and ``spell_check`` to ``False``.

    Returns:
        The ``<input>`` component.

    Raises:
        TypeError: if ``value``/``on_change`` are passed through ``props``.
    """
    _reject_controlled_props("compose_input", props, "value", "on_change")
    _plain_text_type(props)
    props.setdefault("auto_complete", "off")
    props.setdefault("spell_check", False)
    return rx.el.input(
        id=id,
        name=name,
        default_value=value,
        on_blur=on_commit,
        **props,
    )


def compose_textarea(
    *,
    id: str,
    name: str,
    value: TextValue,
    on_commit: CommitHandler,
    **props: Any,
) -> rx.Component:
    """A client-owned multi-line compose field that commits on blur only.

    The action that uses it must read the value from the submitted form data.
    The browser's spell checking is left on (prose); pass ``spell_check=False``
    to turn it off.

    Args:
        id: Element id (also the ``rx.set_value`` target for server writes).
        name: Form field name, the key the form's ``on_submit`` handler reads.
        value: The state var holding the committed value (``default_value``).
        on_commit: The field's own idempotent setter, called on blur.
        **props: Any other ``rx.el.textarea`` props (``max_length``, ``rows``,
            ``placeholder``, ``custom_attrs``, ...). ``auto_complete`` defaults
            to ``"off"``.

    Returns:
        The ``<textarea>`` component.

    Raises:
        TypeError: if ``value``/``on_change`` are passed through ``props``.
    """
    _reject_controlled_props("compose_textarea", props, "value", "on_change")
    props.setdefault("auto_complete", "off")
    return rx.el.textarea(
        id=id,
        name=name,
        default_value=value,
        on_blur=on_commit,
        **props,
    )
