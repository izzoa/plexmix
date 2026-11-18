"""Segmented control built on native radio buttons (exclusive choices).

Replaces ``div.seg > button.on`` segments. The markup is

    fieldset.seg-field[role=radiogroup] > legend.field-label#<legend_id>
        + div.seg > label > input[type=radio].sr-only + span

so assistive technology announces a labelled radio group with the selected
option checked, and the browser provides arrow-key selection with no JS. The
pill look lives in ``assets/redesign.css`` (``.seg input:checked + span`` for the
selected segment, ``.seg input:focus-visible + span`` for the focus ring).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Union, cast

import reflex as rx

if TYPE_CHECKING:
    from reflex.event import EventNamespace

# What mypy sees for a state event handler accessed on the class: a plain method
# (``Callable``) or, for ``@rx.event`` handlers, Reflex's ``EventNamespace``.
SelectHandler = Union[Callable[..., Any], "EventNamespace"]


def seg_radio_group(
    name: str,
    legend: str,
    options: list[tuple[str, str]],
    current: rx.Var | str,
    on_select: SelectHandler,
    legend_id: str,
    **fieldset_props: Any,
) -> rx.Component:
    """Render an exclusive choice as a native radio group styled as segments.

    Args:
        name: Shared ``name`` for every radio in the group (must be unique per page).
        legend: Visible group label (the group's accessible name).
        options: ``(value, label)`` pairs, in display order.
        current: State var holding the selected value (mypy sees a state field
            as its Python type, hence ``| str``; a plain str gives a static selection).
        on_select: Event handler called with the chosen value, e.g.
            ``GeneratorState.set_shuffle_mode``.
        legend_id: Id for the ``<legend>``; the fieldset is labelled by it.
        **fieldset_props: Extra props for the ``<fieldset>`` (e.g. ``style``);
            a ``class_name`` is appended to ``seg-field``.

    Returns:
        The ``fieldset.seg-field`` component.
    """
    extra_class = fieldset_props.pop("class_name", "")
    class_name = f"seg-field {extra_class}".strip() if isinstance(extra_class, str) else "seg-field"
    # rx.el.fieldset is an Element, not BaseHTML: it has no ``role`` prop, and an
    # unknown kwarg would be emitted as CSS. Set the role as a raw attribute.
    custom_attrs = {**fieldset_props.pop("custom_attrs", {}), "role": "radiogroup"}
    select = cast(Callable[[str], Any], on_select)
    segments = [
        rx.el.label(
            rx.el.input(
                type="radio",
                name=name,
                value=value,
                checked=current == value,
                on_change=select(value),
                class_name="sr-only",
            ),
            rx.el.span(label),
        )
        for value, label in options
    ]
    return rx.el.fieldset(
        rx.el.legend(legend, id=legend_id, class_name="field-label"),
        rx.el.div(*segments, class_name="seg"),
        custom_attrs=custom_attrs,
        aria_labelledby=legend_id,
        class_name=class_name,
        **fieldset_props,
    )
