"""One confirmation shell for long, cloud-bound or overwriting jobs (design D7).

``scope_confirm`` is a Radix AlertDialog (``role=alertdialog``, focus trapped,
Escape closes through ``on_open_change``, outside clicks are ignored) that states
a job's scope before it starts: the count, the provider and model, whether track
metadata leaves the machine, and how long it can take. The copy comes from
``plexmix.services.readiness.describe_job_scope``; each state keeps its own open
flag and scope dict, so dialogs on different pages never share state.

It follows webui-keyboard-and-semantics' confirm pattern (its D3):

- The safe button is the AlertDialog Cancel part, so it takes focus on open.
- The confirm and secondary buttons are plain buttons, deliberately **not**
  ``alert_dialog.action``: an Action would also queue ``on_open_change(False)``,
  which could run first and clear the pending scope the confirm handler reads.
  Each confirm handler closes its dialog through state, and the
  ``on_open_change`` handler must be idempotent (closing twice changes nothing).
- On close, focus returns to the opener through ``pmFocus.restore()``
  (``assets/plexmix.js`` block 5).

Buttons use the kit classes only (``btn btn-3 btn-soft`` for Cancel and the
secondary action, ``btn-primary`` for the confirm, ``btn-danger`` when the
confirmed action replaces data). The component adds no colours.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Sequence, Union

import reflex as rx

from plexmix.services import readiness as rd

# Return focus to the element that opened the dialog (or #main-heading if it is gone).
RESTORE_FOCUS_JS = "window.pmFocus&&pmFocus.restore()"

# A line of the scope description: plain text, a state Var, or a component.
if TYPE_CHECKING:
    ScopeLine = Union[str, rx.Var, rx.Component]
else:  # kept loose at runtime so the mocked Reflex of tests/ui can import this module
    ScopeLine = Any

_ACTIONS_STYLE: dict[str, str] = {
    "display": "flex",
    "flexWrap": "wrap",
    "justifyContent": "flex-end",
    "gap": "10px",
    "marginTop": "20px",
}
_LINE_STYLE: dict[str, str] = {"display": "block"}
_NOTE_STYLE: dict[str, str] = {"display": "block", "marginTop": "8px"}


def _is_var(value: Any) -> bool:
    """True for a Reflex Var (False under the mocked Reflex of tests/ui)."""
    var_cls = getattr(rx, "Var", None)
    return isinstance(var_cls, type) and isinstance(value, var_cls)


def _line(content: Any, style: dict[str, str], class_name: str) -> Any:
    """One block line inside the description; an empty text line renders nothing."""
    if content is None:
        return rx.fragment()
    if isinstance(content, str):
        if not content.strip():
            return rx.fragment()
        return rx.el.span(content, class_name=class_name, style=style)
    span = rx.el.span(content, class_name=class_name, style=style)
    if _is_var(content):
        return rx.cond(content != "", span, rx.fragment())
    return span


def scope_provider_line(label: Union[str, rx.Var], model: Union[str, rx.Var]) -> rx.Component:
    """ "<Provider> · <model>" with the model id in the mono data style.

    Same rule as ``readiness.scope_provider_text``: without a model the line is the
    bare provider name, and nothing at all for a provider the scope copy already names
    (``readiness.SCOPE_COPY_NAMES_PROVIDER``, e.g. MusicBrainz), so the description
    never reads the name twice in a row.
    """
    if not _is_var(label) and not _is_var(model):
        text_label = str(label or "").strip()
        text_model = str(model or "").strip()
        if not rd.scope_provider_text(text_label, text_model):
            return rx.fragment()
        if not text_model:
            return rx.el.span(text_label)
        return rx.el.span(text_label, " · ", rx.el.span(text_model, class_name="mono"))
    model_part: Any = rx.fragment(" · ", rx.el.span(model, class_name="mono"))
    if _is_var(model):
        model_part = rx.cond(model != "", model_part, rx.fragment())
    elif not str(model or "").strip():
        model_part = rx.fragment()
    line = rx.el.span(label, model_part)
    if not _is_var(model) and str(model or "").strip():
        return line  # a model to show: always a line
    # Decided in the browser: a model, or a name the scope copy doesn't already say.
    named_by_copy = rx.Var.create(sorted(rd.SCOPE_COPY_NAMES_PROVIDER))
    shown = ~named_by_copy.contains(label)
    if _is_var(model):
        shown = (model != "") | shown
    return rx.cond(shown, line, rx.fragment())


def _confirm_class(danger: Union[bool, rx.Var]) -> Any:
    if _is_var(danger):
        return rx.cond(danger, "btn btn-3 btn-danger", "btn btn-3 btn-primary")
    return "btn btn-3 btn-danger" if danger else "btn btn-3 btn-primary"


def _secondary_button(
    secondary_label: Optional[Union[str, rx.Var]],
    on_secondary: Any,
    id_prefix: str,
) -> Any:
    if secondary_label is None or on_secondary is None:
        return rx.fragment()
    if isinstance(secondary_label, str) and not secondary_label.strip():
        return rx.fragment()
    extra: dict[str, Any] = {"id": f"{id_prefix}-secondary"} if id_prefix else {}
    button = rx.el.button(
        secondary_label,
        class_name="btn btn-3 btn-soft",
        on_click=on_secondary,
        type="button",
        **extra,
    )
    if _is_var(secondary_label):
        return rx.cond(secondary_label != "", button, rx.fragment())
    return button


def scope_confirm(
    open_var: Union[rx.Var, bool],
    on_open_change: Any,
    title: Union[str, rx.Var],
    lines: Sequence[ScopeLine],
    privacy_note: Optional[Union[str, rx.Var]],
    confirm_label: Union[str, rx.Var],
    on_confirm: Any,
    secondary_label: Optional[Union[str, rx.Var]] = None,
    on_secondary: Any = None,
    *,
    danger: Union[bool, rx.Var] = False,
    cancel_label: str = "Cancel",
    id_prefix: str = "",
    max_width: str = "480px",
) -> rx.Component:
    """A scope confirmation AlertDialog.

    Args:
        open_var: Controlled open state (a bool state var).
        on_open_change: Idempotent handler; Radix calls it with False on Escape
            and on Cancel. It must close the dialog and clear the pending flag.
        title: Dialog title (``describe_job_scope()["title"]``).
        lines: Scope lines (count, replace, provider, duration). Plain text,
            Vars (an empty string renders nothing) or components such as
            :func:`scope_provider_line`.
        privacy_note: Whether data leaves the machine (``privacy_line``).
        confirm_label: Label of the confirm button.
        on_confirm: Confirm handler. It closes the dialog through state.
        secondary_label: Optional second action (e.g. "Tag only the 19 untagged
            tracks"); a Var renders the button only while it is non-empty.
        on_secondary: Handler of the second action; closes the dialog itself.
        danger: True (or a bool Var) when the confirmed action replaces data:
            the confirm button uses ``btn-danger`` instead of ``btn-primary``.
        cancel_label: Label of the safe button (initial focus).
        id_prefix: When set, the content gets this id and the buttons
            ``<prefix>-cancel``, ``<prefix>-confirm`` and ``<prefix>-secondary``.
        max_width: Dialog width cap.
    """
    description = rx.alert_dialog.description(
        *[_line(line, _LINE_STYLE, "scope-line") for line in lines],
        _line(privacy_note, _NOTE_STYLE, "scope-note"),
    )
    cancel_extra: dict[str, Any] = {"id": f"{id_prefix}-cancel"} if id_prefix else {}
    confirm_extra: dict[str, Any] = {"id": f"{id_prefix}-confirm"} if id_prefix else {}
    content_extra: dict[str, Any] = {"id": id_prefix} if id_prefix else {}
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title(title),
            description,
            rx.box(
                rx.alert_dialog.cancel(
                    rx.el.button(
                        cancel_label,
                        class_name="btn btn-3 btn-soft",
                        type="button",
                        **cancel_extra,
                    ),
                ),
                _secondary_button(secondary_label, on_secondary, id_prefix),
                rx.el.button(
                    confirm_label,
                    class_name=_confirm_class(danger),
                    on_click=on_confirm,
                    type="button",
                    **confirm_extra,
                ),
                style=_ACTIONS_STYLE,
            ),
            max_width=max_width,
            aria_modal="true",
            on_close_auto_focus=rx.call_script(RESTORE_FOCUS_JS),
            **content_extra,
        ),
        open=open_var,
        on_open_change=on_open_change,
    )


def embed_all_confirm_dialog(before_confirm: Any = None) -> rx.Component:
    """The whole-library "Build search index" confirmation, bound to LibraryState.

    Render it once on any page that dispatches ``LibraryState.request_embed_all``
    (the Dashboard setup checklist and the Library page). It shows how many
    tracks will be embedded, the embedding provider and model, whether track
    metadata leaves the machine, and the batch/cancel note.

    Args:
        before_confirm: Optional event dispatched just before
            ``LibraryState.confirm_embed_all`` (the Dashboard passes
            ``DashboardState.watch_setup_job("embedding")`` so its setup poller
            follows the job; it must run first so the entry the job replaces is
            never mistaken for it).
    """
    from plexmix.ui.states.library_state import LibraryState

    on_confirm: Any = LibraryState.confirm_embed_all
    if before_confirm is not None:
        on_confirm = [before_confirm, LibraryState.confirm_embed_all]
    scope = LibraryState.embed_all_scope
    return scope_confirm(
        open_var=LibraryState.embed_all_open,
        on_open_change=LibraryState.set_embed_all_open,
        title=scope["title"],
        lines=[
            scope["count_line"],
            scope_provider_line(scope["provider_label"], scope["model"]),
            scope["duration_line"],
        ],
        privacy_note=scope["privacy_line"],
        confirm_label=scope["confirm_label"],
        on_confirm=on_confirm,
        danger=scope["danger"] == "true",
        id_prefix="embed-all-confirm",
    )
