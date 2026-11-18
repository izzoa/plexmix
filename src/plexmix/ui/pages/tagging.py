"""Tagging — batch AI tagging with preview, progress, and inline tag editing."""

from typing import Any, cast

import reflex as rx
from reflex.vars.object import ObjectVar

from plexmix.ui.components.inputs import compose_input, filter_input
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.scope_confirm import scope_confirm, scope_provider_line
from plexmix.ui.states.tagging_state import (
    ARTIST_FILTER_ID,
    EDIT_BUTTON_ID_PREFIX,
    EDIT_ENVIRONMENTS_ID,
    EDIT_INSTRUMENTS_ID,
    EDIT_MOODS_ID,
    GENRE_FILTER_ID,
    STALE_DAYS_ID,
    TAG_CONFIRM_ID,
    TAG_NOTICE_REGION_ID,
    TAG_START_ID,
    TAG_START_REASON_ID,
    TaggingState,
)

# Shared help text for the three inline-editor fields (one editor is open at a time).
_EDIT_HELP_ID = "tag-edit-help"


def _filter_bar() -> rx.Component:
    """The filter form: Enter in any field (or Preview) applies the filters and previews.

    The text fields are client-owned (``filter_input``): each commits to its own
    setter 300 ms after typing pauses and on blur, and the form's submit reads the
    values on screen (``apply_filter_form``). The checkbox stays controlled.
    """
    return rx.el.form(
        rx.box(
            rx.icon("search", size=15, color="var(--fg-3)"),
            filter_input(
                id=ARTIST_FILTER_ID,
                name="artist",
                value=TaggingState.artist_filter,
                on_commit=TaggingState.set_artist_filter,
                placeholder="Filter by artist…",
                aria_label="Filter by artist",
            ),
            class_name="search",
        ),
        filter_input(
            id=GENRE_FILTER_ID,
            name="genre",
            value=TaggingState.genre_filter,
            on_commit=TaggingState.set_genre_filter,
            placeholder="Genre",
            class_name="minisel",
            style={"maxWidth": "160px"},
            aria_label="Filter by genre",
        ),
        filter_input(
            id=STALE_DAYS_ID,
            name="stale_days",
            value=TaggingState.stale_days,
            on_commit=TaggingState.set_stale_days,
            type="number",
            min="0",
            step="1",
            placeholder="Stale days",
            class_name="minisel",
            style={"maxWidth": "120px"},
            aria_label="Stale after days",
            # assets/plexmix.js: only digits can be typed or pasted here.
            custom_attrs={"data-digits": ""},
        ),
        rx.el.label(
            rx.el.input(
                type="checkbox",
                checked=TaggingState.has_no_tags,
                on_change=lambda _v: TaggingState.toggle_has_no_tags(),
            ),
            "Untagged only",
            style={"display": "flex", "alignItems": "center", "gap": "7px", "fontSize": "13px"},
        ),
        rx.el.button(
            rx.icon("scan-search", size=15),
            "Preview",
            class_name="btn btn-3 btn-soft",
            type="submit",
        ),
        on_submit=TaggingState.apply_filter_form,
        custom_attrs={"data-enter-submit": ""},
        # No native constraint validation: a click on Preview with "1.5" or "-3" in
        # Stale days would otherwise stop at a browser bubble. set_stale_days keeps
        # digits only, so an invalid entry previews with no stale-days filter.
        no_validate=True,
        class_name="filterbar",
    )


def _tag_edit_field(
    label: str,
    field_id: str,
    name: str,
    value: Any,
    on_commit: Any,
    auto_focus: bool = False,
) -> rx.Component:
    """One inline-editor field: a visible ``<label for>`` above a named text input.

    The input is client-owned (``compose_input``): it commits to ``on_commit`` on
    blur only, and ``name`` is the key ``submit_tag_edit`` reads from the submitted
    form data, so Save uses the text on screen.
    """
    return rx.box(
        rx.el.label(label, html_for=field_id, class_name="field-label"),
        compose_input(
            id=field_id,
            name=name,
            value=value,
            on_commit=on_commit,
            auto_focus=auto_focus,
            aria_describedby=_EDIT_HELP_ID,
            class_name="input",
        ),
        class_name="tag-edit-field",
    )


def _tag_editor(track: ObjectVar[dict[str, str]]) -> rx.Component:
    """Inline tag editor: a form so Enter saves, and Escape (pmFocus) clicks Cancel."""
    return rx.el.form(
        rx.el.fieldset(
            rx.el.legend(
                "Edit tags for ",
                rx.el.span(track["title"], class_name="tag-edit-title"),
                " — ",
                track["artist"],
            ),
            rx.box(
                _tag_edit_field(
                    "Moods",
                    EDIT_MOODS_ID,
                    "tags",
                    TaggingState.edit_tags,
                    TaggingState.set_edit_tags,
                    auto_focus=True,
                ),
                _tag_edit_field(
                    "Environments",
                    EDIT_ENVIRONMENTS_ID,
                    "environments",
                    TaggingState.edit_environments,
                    TaggingState.set_edit_environments,
                ),
                _tag_edit_field(
                    "Instruments",
                    EDIT_INSTRUMENTS_ID,
                    "instruments",
                    TaggingState.edit_instruments,
                    TaggingState.set_edit_instruments,
                ),
                class_name="tag-edit-fields",
            ),
            rx.box(
                rx.el.span("Comma-separated", id=_EDIT_HELP_ID, class_name="field-help"),
                rx.el.button(
                    "Save",
                    class_name="btn btn-sm btn-primary",
                    type="submit",
                ),
                rx.el.button(
                    "Cancel",
                    class_name="btn btn-sm btn-ghost",
                    on_click=TaggingState.cancel_edit,
                    type="button",
                    data_pm_cancel="",
                ),
                class_name="tag-edit-foot",
            ),
            class_name="tag-edit-fieldset",
        ),
        on_submit=TaggingState.submit_tag_edit,
        data_pm_escape_cancel="",
        class_name="tag-edit",
    )


def _tagged_row(track: ObjectVar[dict[str, str]]) -> rx.Component:
    editing = TaggingState.editing_track_id == track["id"]
    return rx.box(
        rx.cond(
            editing,
            _tag_editor(track),
            # Title column, tags and Edit wrap onto further lines when they don't fit
            # (.tag-row* in redesign.css). The tags show spaced ("a, b") so a long
            # list can break; Edit still prefills the editor with the stored value.
            rx.box(
                rx.box(
                    rx.box(track["title"], style={"fontWeight": "500", "fontSize": "14px"}),
                    rx.box(track["artist"], class_name="fg3", style={"fontSize": "13px"}),
                    class_name="tag-row-title",
                ),
                rx.el.span(track["tags_display"], class_name="tag-row-tags fg2"),
                rx.el.button(
                    rx.icon("pencil", size=14),
                    "Edit",
                    id=EDIT_BUTTON_ID_PREFIX + track["id"],
                    aria_label="Edit tags for " + track["title"],
                    class_name="btn btn-sm btn-soft",
                    on_click=_start_edit(track),
                    type="button",
                ),
                class_name="tag-row",
            ),
        ),
        style={"padding": "12px 14px", "borderBottom": "1px solid var(--border-subtle)"},
        # Keyed by track id, not the foreach index: when a save drops or moves a
        # row, each Edit button keeps its own node, so focus sent to a row's
        # button (tagging_state._focus_after_save) stays on that track.
        key=track["id"],
    )


def _start_edit(track: ObjectVar[dict[str, str]]) -> Any:
    """Event spec that opens the inline editor on ``track``, prefilled from the row.

    mypy models ``TaggingState.start_edit_tag`` as a plain function (``self`` first,
    ``str`` parameters); Reflex binds it to the state and accepts Vars as arguments.
    """
    handler: Any = TaggingState.start_edit_tag
    return handler(track["id"], track["tags"], track["environments"], track["instruments"])


# ── Count card, notices and the scope confirmation (honest-status D10) ─────────


def _count_text() -> rx.Component:
    """The match readout: a count only for a preview of the filters now set.

    Before any preview it invites one; after a filter change it asks for a new
    preview instead of showing the earlier count as current.
    """
    current = rx.fragment(
        rx.el.span(TaggingState.preview_count_label, class_name="mono tag-count-num"),
        " ",
        TaggingState.preview_match_text,
        rx.cond(
            TaggingState.preview_tagged_note != "",
            rx.el.span(" · ", TaggingState.preview_tagged_note, class_name="tag-count-note"),
            rx.fragment(),
        ),
    )
    # rx.match is typed Component | Var; with component branches it is a Component.
    return cast(
        rx.Component,
        rx.match(
            TaggingState.preview_state,
            ("current", current),
            ("running", rx.el.span("Counting matching tracks…")),
            ("empty", rx.el.span("No tracks match the current filters")),
            ("stale", rx.el.span("Filters changed — preview again")),
            rx.el.span("Preview to count matching tracks"),
        ),
    )


def _count_card() -> rx.Component:
    """The preview count and Tag tracks, which is disabled with a visible reason."""
    return rx.box(
        rx.icon("sparkles", size=18, color="var(--accent-fg)"),
        # Persistent polite live region: the readout is announced when it changes.
        rx.box(
            _count_text(),
            role="status",
            aria_live="polite",
            aria_atomic="true",
            id="tag-preview-status",
            class_name="tag-count-text",
        ),
        rx.box(
            rx.el.button(
                rx.icon("tags", size=16),
                "Tag tracks",
                id=TAG_START_ID,
                class_name="btn btn-3 btn-primary glow",
                # Opens the scope confirmation; nothing starts without it.
                on_click=TaggingState.request_tagging,
                disabled=~TaggingState.can_start_tagging,
                aria_busy=TaggingState.tag_confirm_checking,
                aria_describedby=TAG_START_REASON_ID,
                type="button",
            ),
            rx.el.span(
                TaggingState.tag_block_reason,
                id=TAG_START_REASON_ID,
                class_name="tag-reason",
            ),
            class_name="tag-start",
        ),
        class_name="card tag-count",
    )


def _notice_region() -> rx.Component:
    """Persistent assertive region for preview, validation, start and job problems.

    It stays mounted (outside the job card) while the callout inside it appears
    and changes, so a problem is announced and stays visible until dismissed or
    replaced by the next preview or start.
    """
    notice = TaggingState.tagging_notice
    callout = rx.box(
        rx.box(
            rx.cond(
                notice["level"] == "error",
                rx.icon("circle-alert", size=16),
                rx.icon("triangle-alert", size=16),
            ),
            class_name="c-ico",
        ),
        rx.box(
            rx.cond(
                notice["title"] != "",
                rx.box(notice["title"], class_name="c-title"),
                rx.fragment(),
            ),
            rx.box(notice["text"], class_name="c-body"),
            rx.cond(
                notice["action_href"] != "",
                rx.el.a(
                    notice["action_label"],
                    href=notice["action_href"],
                    class_name="tag-notice-action",
                ),
                rx.fragment(),
            ),
            class_name="tag-notice-main",
        ),
        rx.el.button(
            rx.icon("x", size=14),
            class_name="btn btn-sm btn-ghost tag-notice-dismiss",
            on_click=TaggingState.dismiss_notice,
            aria_label="Dismiss message",
            type="button",
        ),
        class_name=rx.cond(
            notice["level"] == "error",
            "callout callout-error tag-notice",
            "callout callout-warning tag-notice",
        ),
    )
    return rx.box(
        rx.cond(notice["text"] != "", callout, rx.fragment()),
        role="alert",
        id=TAG_NOTICE_REGION_ID,
    )


def _tag_confirm_dialog() -> rx.Component:
    """The tagging scope confirmation (count, replaced tag sets, provider + model,
    data disclosure; "Tag only the N untagged tracks" when some already have tags)."""
    scope = TaggingState.tag_scope
    return scope_confirm(
        open_var=TaggingState.tag_confirm_open,
        on_open_change=TaggingState.set_tag_confirm_open,
        title=scope["title"],
        lines=[
            scope["count_line"],
            scope["replace_line"],
            scope_provider_line(scope["provider_label"], scope["model"]),
            scope["duration_line"],
        ],
        privacy_note=scope["privacy_line"],
        confirm_label=scope["confirm_label"],
        on_confirm=TaggingState.confirm_tagging,
        secondary_label=scope["secondary_label"],
        on_secondary=TaggingState.tag_all_untagged,
        danger=scope["danger"] == "true",
        id_prefix=TAG_CONFIRM_ID,
    )


def _recent_skeleton() -> rx.Component:
    """Loading placeholder for "Recently tagged" until its first load completes."""
    rows = [
        rx.box(
            rx.box(class_name="skeleton tag-skel-title", aria_hidden="true"),
            rx.box(class_name="skeleton tag-skel-meta", aria_hidden="true"),
            class_name="tag-skel-row",
        )
        for _ in range(3)
    ]
    return rx.box(
        rx.el.span("Loading recently tagged tracks…", class_name="sr-only"),
        *rows,
        class_name="card",
        aria_busy="true",
        style={"width": "100%", "overflow": "hidden"},
    )


def _recent_empty() -> rx.Component:
    return rx.box(
        rx.box(
            rx.icon("tags", size=22, color="var(--fg-3)", aria_hidden="true"), class_name="e-ico"
        ),
        rx.box("No tagged tracks yet", class_name="e-title"),
        rx.box("Tracks you tag show up here, newest first.", class_name="e-desc"),
        class_name="empty",
        style={"width": "100%"},
    )


def _recent_error() -> rx.Component:
    return rx.box(
        rx.box(rx.icon("circle-alert", size=16), class_name="c-ico"),
        rx.box(TaggingState.recent_error, class_name="c-body tag-notice-main"),
        rx.el.button(
            "Retry",
            class_name="btn btn-sm btn-soft",
            on_click=TaggingState.retry_recently_tagged,
            type="button",
        ),
        class_name="callout callout-error",
        style={"width": "100%"},
    )


def _recently_tagged() -> rx.Component:
    """Recently tagged: a placeholder while loading, then the rows, an empty state,
    or the load error with Retry."""
    return rx.box(
        rx.box(
            rx.el.h2("Recently tagged", style={"fontSize": "17px", "fontWeight": "700"}),
            class_name="section-head",
        ),
        rx.cond(
            TaggingState.recent_loaded,
            rx.cond(
                TaggingState.recent_error != "",
                _recent_error(),
                rx.cond(
                    TaggingState.recently_tagged_tracks.length() > 0,
                    rx.box(
                        rx.foreach(TaggingState.recently_tagged_tracks, _tagged_row),
                        class_name="card",
                        style={"width": "100%", "overflow": "hidden"},
                    ),
                    _recent_empty(),
                ),
            ),
            _recent_skeleton(),
        ),
        style={"width": "100%"},
    )


def tagging() -> rx.Component:
    content = rx.vstack(
        # Hidden poller so tagging progress updates live (not just on page load).
        rx.box(
            rx.moment(interval=2000, on_change=TaggingState.poll_task_progress),
            display="none",
        ),
        _filter_bar(),
        # Preview + start, with preview/start problems right below (outside the
        # job card, so they show whether or not a job runs).
        rx.box(
            _count_card(),
            _notice_region(),
            style={"width": "100%"},
        ),
        # Batch progress
        rx.cond(
            TaggingState.is_tagging,
            rx.box(
                rx.box(
                    rx.el.span(
                        TaggingState.tagging_message,
                        style={"fontSize": "14px", "fontWeight": "500"},
                    ),
                    rx.spacer(),
                    rx.el.span(
                        "Batch ",
                        TaggingState.current_batch,
                        "/",
                        TaggingState.total_batches,
                        class_name="mono fg3",
                        style={"fontSize": "13px"},
                    ),
                    style={"display": "flex", "alignItems": "center", "marginBottom": "10px"},
                ),
                rx.box(
                    rx.box(
                        class_name="pfill",
                        style={"width": TaggingState.tagging_progress.to_string() + "%"},
                    ),
                    class_name="pbar",
                    role="progressbar",
                    aria_label="Tagging progress",
                    aria_valuemin="0",
                    aria_valuemax="100",
                    aria_valuenow=TaggingState.tagging_progress,
                    aria_valuetext=(
                        f"Batch {TaggingState.current_batch} of {TaggingState.total_batches}"
                    ),
                ),
                rx.box(
                    rx.el.span(
                        TaggingState.tags_generated_count, " tags generated", class_name="fg3"
                    ),
                    rx.spacer(),
                    rx.el.button(
                        "Cancel",
                        class_name="btn btn-sm btn-ghost",
                        on_click=TaggingState.cancel_tagging,
                        type="button",
                    ),
                    style={
                        "display": "flex",
                        "alignItems": "center",
                        "marginTop": "10px",
                        "fontSize": "13px",
                    },
                ),
                class_name="card",
                style={"padding": "16px 18px", "width": "100%"},
            ),
            rx.fragment(),
        ),
        _recently_tagged(),
        _tag_confirm_dialog(),
        spacing="4",
        width="100%",
        on_mount=TaggingState.on_load,
    )
    return layout(content)
