"""Library — synced tracks: filters, table, sync/embed/enrich jobs, bulk actions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import reflex as rx

from plexmix.ui.components.inputs import COMMIT_DEBOUNCE_MS, compose_input, filter_input
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.scope_confirm import embed_all_confirm_dialog, scope_provider_line
from plexmix.ui.components.seg_radio import seg_radio_group
from plexmix.ui.states.app_state import AppState
from plexmix.ui.states.library_state import (
    BULK_TAG_ADD,
    BULK_TAG_REPLACE,
    LIBRARY_BULK_TAGS_ID,
    LIBRARY_GENRE_ID,
    LIBRARY_SEARCH_ID,
    LIBRARY_TAG_ID,
    LIBRARY_YEAR_MAX_ID,
    LIBRARY_YEAR_MIN_ID,
    NEXT_PAGE_BUTTON_ID,
    PREV_PAGE_BUTTON_ID,
    REGENERATE_COST_LINE,
    REGENERATE_IN_PLACE_LINE,
    LibraryState,
)

if TYPE_CHECKING:  # annotation only; keeps the page importable under the tests/ui Reflex mock
    from reflex.vars.object import ObjectVar

# Run by every dialog's on_close_auto_focus: return focus to the control that
# opened the dialog (bulk bar Tag/Delete, Sync), or to #main-heading when it no
# longer exists (pmFocus in assets/plexmix.js).
_RESTORE_FOCUS_JS = "window.pmFocus&&pmFocus.restore()"

# The filter bar form. Buttons outside it (the "No tracks match" Clear filters)
# join it with ``form=`` so plexmix.js ``data-clear-form`` empties its text fields.
FILTER_FORM_ID = "lib-filters"
SYNC_NOTE_ID = "lib-sync-note"
SYNC_ERROR_REGION_ID = "lib-sync-error-region"
REMOVAL_NOTICE_REGION_ID = "lib-removal-notice-region"
EMBED_NOTICE_REGION_ID = "lib-embed-notice-region"
BULK_TAG_ERROR_ID = "lib-bulk-tags-error"
BULK_MODE_LEGEND_ID = "lib-bulk-mode-legend"
REGENERATE_SCOPE_ID = "lib-regenerate-scope"

# The Sync buttons. The "regenerate" mode opens its confirmation first
# (start_sync_or_confirm), and the second handler then reads the scope that the
# confirmation states (it does nothing in the other modes).
_SYNC_CLICK: Any = [LibraryState.start_sync_or_confirm, LibraryState.load_regenerate_scope]

# Enrichment column chips: (row flag key, Lucide icon, title when present, title when absent).
# The flags come from enrichment_flags() in LibraryState.load_tracks.
_ENRICHMENT_CHIPS: tuple[tuple[str, str, str, str], ...] = (
    ("enr_tags", "tags", "Tagged", "Not tagged"),
    ("enr_embedded", "cpu", "Embedded", "Not embedded"),
    ("enr_audio", "audio-waveform", "Audio analyzed", "Audio not analyzed"),
    ("enr_mb", "disc", "MusicBrainz matched", "MusicBrainz not matched"),
)


def _job_bar(
    active: rx.Var[bool] | bool,
    message: rx.Var[str] | str,
    progress: rx.Var[int] | int,
    job_type: str,
    job_name: str,
) -> rx.Component:
    """Running-job card: message, percentage, Cancel and a named progress bar.

    The bar is a ``role=progressbar`` labelled by the message span, which starts
    with an sr-only job name so the name always identifies the job. No live
    region: progress values are not announced on every update. ``job_name`` is
    written mid-sentence ("library sync"); it is capitalised for the prefix.
    """
    msg_id = f"job-{job_type}-msg"
    name_prefix = job_name[:1].upper() + job_name[1:] + ": "
    pct = rx.Var.create(progress)
    return rx.cond(
        active,
        rx.box(
            rx.box(
                rx.el.span(
                    rx.el.span(name_prefix, class_name="sr-only"),
                    message,
                    id=msg_id,
                    style={"fontSize": "13px", "fontWeight": "500"},
                ),
                rx.spacer(),
                rx.el.span(
                    pct.to_string() + "%",
                    class_name="mono fg3",
                    style={"fontSize": "13px"},
                ),
                rx.el.button(
                    rx.icon("x", size=14, aria_hidden="true"),
                    "Cancel",
                    class_name="btn btn-sm btn-ghost",
                    on_click=AppState.request_cancel(job_type),
                    aria_label=f"Cancel {job_name}",
                    type="button",
                ),
                style={
                    "display": "flex",
                    "alignItems": "center",
                    "gap": "12px",
                    "marginBottom": "8px",
                },
            ),
            rx.box(
                rx.box(class_name="pfill", style={"width": pct.to_string() + "%"}),
                class_name="pbar",
                role="progressbar",
                aria_valuemin="0",
                aria_valuemax="100",
                aria_valuenow=pct,
                aria_valuetext=pct.to_string() + "%",
                aria_labelledby=msg_id,
            ),
            class_name="card",
            style={"padding": "14px 16px", "width": "100%"},
        ),
        rx.fragment(),
    )


def _action_bar() -> rx.Component:
    return rx.box(
        rx.el.select(
            rx.el.option("Incremental sync", value="incremental"),
            rx.el.option("Full sync", value="full"),
            rx.el.option("Regenerate (replace tags + embeddings)", value="regenerate"),
            value=LibraryState.sync_mode,
            on_change=LibraryState.set_sync_mode,
            class_name="minisel",
            aria_label="Sync mode",
        ),
        rx.el.button(
            rx.icon("refresh-cw", size=16),
            "Sync",
            class_name="btn btn-3 btn-primary",
            # Route through a dispatcher so the 'regenerate' mode, which replaces
            # every tag and embedding, is confirmed first.
            on_click=_SYNC_CLICK,
            disabled=LibraryState.is_syncing,
            type="button",
            # The AI-tagging note below (absent when no AI provider is set up).
            aria_describedby=SYNC_NOTE_ID,
        ),
        rx.el.button(
            rx.icon("cpu", size=16),
            "Generate embeddings",
            class_name="btn btn-3 btn-soft",
            # With nothing selected it offers to embed every track without an
            # embedding (LibraryState.request_embed_all's count + confirmation).
            on_click=LibraryState.generate_embeddings,
            disabled=LibraryState.is_embedding,
            # Stays focusable while it counts; the handler ignores a second press.
            aria_busy=LibraryState.embed_all_checking,
            type="button",
        ),
        rx.el.button(
            rx.icon("disc", size=16),
            "Enrich MusicBrainz",
            class_name="btn btn-3 btn-soft",
            on_click=LibraryState.enrich_musicbrainz,
            disabled=LibraryState.is_enriching_musicbrainz,
            type="button",
        ),
        # Sync also AI-tags new untagged tracks when an AI provider is set up.
        rx.cond(
            LibraryState.sync_tagging_note != "",
            rx.el.p(LibraryState.sync_tagging_note, id=SYNC_NOTE_ID, class_name="lib-sync-note"),
            rx.fragment(),
        ),
        class_name="card",
        style={
            "display": "flex",
            "alignItems": "center",
            "gap": "10px",
            "padding": "14px 16px",
            "width": "100%",
            "flexWrap": "wrap",
        },
    )


def _sync_error_region() -> rx.Component:
    """Inline sync failure next to the action bar (the toast is shown as well).

    A persistent ``role=alert`` region: it is mounted before any failure is
    written into it, so the failure is announced. The copy comes from
    ``readiness.describe`` for Plex connection failures and links to the setting
    that fixes it; it never names a terminal command.
    """
    return rx.box(
        rx.cond(
            LibraryState.sync_error != "",
            rx.box(
                rx.box(rx.icon("circle-alert", size=16, aria_hidden="true"), class_name="c-ico"),
                rx.box(
                    rx.box(LibraryState.sync_error, class_name="c-title"),
                    rx.cond(
                        LibraryState.sync_error_body != "",
                        rx.box(LibraryState.sync_error_body, class_name="c-body"),
                        rx.fragment(),
                    ),
                    rx.cond(
                        LibraryState.sync_error_action_href != "",
                        # A router link (Settings ?section=…), styled as a kit button.
                        rx.el.a(
                            LibraryState.sync_error_action_label,
                            href=LibraryState.sync_error_action_href,
                            class_name="btn btn-sm btn-soft lib-callout-action",
                        ),
                        rx.fragment(),
                    ),
                    class_name="lib-callout-text",
                ),
                rx.el.button(
                    "Dismiss",
                    class_name="btn btn-sm btn-ghost",
                    on_click=LibraryState.dismiss_sync_error,
                    aria_label="Dismiss sync error",
                    type="button",
                ),
                id="lib-sync-error",
                class_name="callout callout-error",
            ),
            rx.fragment(),
        ),
        id=SYNC_ERROR_REGION_ID,
        role="alert",
    )


def _removal_notice_region() -> rx.Component:
    """Tracks the last sync kept because Plex no longer lists them (persistent ``role=status``).

    Shown only while a removal is held (``removal_notice_count`` above 0): the
    sync found more tracks missing from Plex than it removes without confirmation.
    "Review removal" opens the confirmation. There is no Dismiss button: the
    notice follows the sync task and ends with the next sync or a confirmed removal.
    """
    return rx.box(
        rx.cond(
            LibraryState.removal_notice_count > 0,
            rx.box(
                rx.box(rx.icon("triangle-alert", size=16, aria_hidden="true"), class_name="c-ico"),
                rx.box(
                    rx.box(LibraryState.removal_notice_line, class_name="c-title"),
                    rx.box(
                        "They were kept. Removing this many tracks needs your confirmation.",
                        class_name="c-body",
                    ),
                    rx.el.button(
                        "Review removal",
                        id="lib-removal-review",
                        class_name="btn btn-sm btn-soft lib-callout-action",
                        on_click=LibraryState.open_removal_confirm,
                        type="button",
                    ),
                    class_name="lib-callout-text",
                ),
                id="lib-removal-notice",
                class_name="callout callout-warning",
            ),
            rx.fragment(),
        ),
        id=REMOVAL_NOTICE_REGION_ID,
        role="status",
    )


def _embed_all_notice_region() -> rx.Component:
    """Why "Generate embeddings" opened no confirmation (persistent ``role=status``).

    For example "All tracks already have embeddings.", or the embedding
    provider's readiness cause and remedy when it can't start.
    """
    tone = LibraryState.embed_all_notice_tone
    return rx.box(
        rx.cond(
            LibraryState.embed_all_notice != "",
            rx.box(
                rx.box(
                    rx.match(
                        tone,
                        ("error", rx.icon("circle-alert", size=16, aria_hidden="true")),
                        ("warning", rx.icon("triangle-alert", size=16, aria_hidden="true")),
                        rx.icon("info", size=16, aria_hidden="true"),
                    ),
                    class_name="c-ico",
                ),
                rx.box(
                    rx.box(LibraryState.embed_all_notice, class_name="c-body"),
                    # A mismatch links to the rebuild (Doctor) and to switching
                    # back (Settings); a blocked provider links to its fix.
                    rx.cond(
                        LibraryState.embed_all_notice_action_href != "",
                        rx.box(
                            rx.el.a(
                                LibraryState.embed_all_notice_action_label,
                                href=LibraryState.embed_all_notice_action_href,
                                class_name="btn btn-sm btn-soft",
                            ),
                            rx.cond(
                                LibraryState.embed_all_notice_secondary_href != "",
                                rx.el.a(
                                    LibraryState.embed_all_notice_secondary_label,
                                    href=LibraryState.embed_all_notice_secondary_href,
                                    class_name="btn btn-sm btn-ghost",
                                ),
                                rx.fragment(),
                            ),
                            class_name="notice-links",
                        ),
                        rx.fragment(),
                    ),
                    class_name="lib-callout-text",
                ),
                rx.el.button(
                    "Dismiss",
                    class_name="btn btn-sm btn-ghost",
                    on_click=LibraryState.dismiss_embed_all_notice,
                    aria_label="Dismiss embeddings notice",
                    type="button",
                ),
                class_name=rx.match(
                    tone,
                    ("error", "callout callout-error"),
                    ("warning", "callout callout-warning"),
                    "callout callout-info",
                ),
            ),
            rx.fragment(),
        ),
        id=EMBED_NOTICE_REGION_ID,
        role="status",
    )


def _actions() -> rx.Component:
    """Action bar plus its result regions; empty regions take no space (no gap)."""
    return rx.box(
        _action_bar(),
        _sync_error_region(),
        _removal_notice_region(),
        _embed_all_notice_region(),
        class_name="lib-actions",
    )


def _year_input(field_id: str, name: str, value: Any, on_commit: Any, label: str) -> rx.Component:
    """A four-digit year bound (``.minisel.lib-year``, about 96 px wide).

    ``set_year_min``/``set_year_max`` apply only a complete four-digit year, so a
    partly typed year leaves the list unchanged. ``data-digits``: assets/plexmix.js
    lets only digits be typed or pasted (``type="number"`` alone admits "-", "e"...).
    """
    return filter_input(
        id=field_id,
        name=name,
        value=value,
        on_commit=on_commit,
        type="number",
        input_mode="numeric",
        min="1000",
        max="9999",
        placeholder=label,
        class_name="minisel lib-year",
        aria_label=label,
        custom_attrs={"data-digits": ""},
    )


def _filter_bar() -> rx.Component:
    """Library filters: text search, genre, year range, tag and "has audio analysis".

    The text fields are client-owned (plexmix.ui.components.inputs): each applies
    300 ms after typing pauses and on blur. Enter in any field submits the form
    (``data-enter-submit``; assets/plexmix.js calls ``requestSubmit``), which
    applies every field at once with one reload.
    """
    return rx.el.form(
        rx.box(
            rx.icon("search", size=15, color="var(--fg-3)", aria_hidden="true"),
            filter_input(
                id=LIBRARY_SEARCH_ID,
                name="search",
                value=LibraryState.search_query,
                on_commit=LibraryState.set_search_query,
                placeholder="Search tracks…",
                aria_label="Search tracks",
            ),
            class_name="search",
        ),
        filter_input(
            id=LIBRARY_GENRE_ID,
            name="genre",
            value=LibraryState.genre_filter,
            on_commit=LibraryState.set_genre_filter,
            placeholder="Genre",
            class_name="minisel",
            style={"maxWidth": "160px"},
            aria_label="Filter by genre",
        ),
        _year_input(
            LIBRARY_YEAR_MIN_ID,
            "year_min",
            LibraryState.year_min,
            LibraryState.set_year_min,
            "From year",
        ),
        _year_input(
            LIBRARY_YEAR_MAX_ID,
            "year_max",
            LibraryState.year_max,
            LibraryState.set_year_max,
            "To year",
        ),
        filter_input(
            id=LIBRARY_TAG_ID,
            name="tag",
            value=LibraryState.tag_filter,
            on_commit=LibraryState.set_tag_filter,
            placeholder="Tag",
            class_name="minisel",
            style={"maxWidth": "160px"},
            aria_label="Filter by tag",
        ),
        # Offered only when at least one track has audio analysis, so the filter
        # can never produce a guaranteed-empty list.
        rx.cond(
            AppState.audio_analyzed_tracks != "0",
            rx.el.label(
                rx.el.input(
                    type="checkbox",
                    checked=LibraryState.has_audio,
                    on_change=LibraryState.toggle_has_audio,
                ),
                "Has audio analysis",
                style={"display": "flex", "alignItems": "center", "gap": "7px", "fontSize": "13px"},
            ),
        ),
        rx.el.button(
            "Clear",
            class_name="btn btn-3 btn-ghost",
            # Debounced like the field commits: a field's pending commit (typed
            # less than 300 ms before Clear) is delivered first, so it cannot
            # re-apply a filter after Clear has emptied the fields.
            on_click=cast(Any, LibraryState.clear_filters).debounce(COMMIT_DEBOUNCE_MS),
            aria_label="Clear filters",
            type="button",
            # assets/plexmix.js empties the form's text fields in the click itself, so
            # text typed while the reset travels to the server is never wiped.
            custom_attrs={"data-clear-form": ""},
        ),
        class_name="filterbar",
        id=FILTER_FORM_ID,
        on_submit=LibraryState.apply_filter_form,
        custom_attrs={"data-enter-submit": ""},
        # No native constraint validation: the year setters ignore a partial or
        # out-of-range year instead of a browser bubble blocking the submit.
        no_validate=True,
    )


def _bulk_bar() -> rx.Component:
    """Selection count and bulk actions: "N selected · M on other pages".

    Rendered after the track table as ``.bulkbar`` (``position: sticky; bottom``
    in assets/redesign.css): it sticks to the bottom of the ``.scroll`` container
    while the table scrolls, so it is reachable from any row, and appearing
    below the rows never moves the rows above it.
    """
    return rx.cond(
        LibraryState.selected_tracks.length() > 0,
        rx.box(
            rx.el.span(LibraryState.selection_label, class_name="bulkbar-count"),
            rx.el.button(
                rx.icon("tags", size=15),
                "Tag",
                class_name="btn btn-sm btn-soft",
                on_click=LibraryState.open_bulk_tag_dialog,
                type="button",
            ),
            rx.el.button(
                rx.icon("trash-2", size=15),
                "Delete",
                class_name="btn btn-sm btn-soft",
                on_click=LibraryState.open_delete_confirm,
                type="button",
            ),
            rx.el.button(
                "Clear",
                class_name="btn btn-sm btn-ghost",
                on_click=LibraryState.clear_selection,
                aria_label="Clear selection",
                type="button",
            ),
            class_name="card bulkbar",
        ),
        rx.fragment(),
    )


def _bulk_tag_dialog() -> rx.Component:
    """Form dialog for tagging the selected tracks: Add (default) or Replace.

    A plain modal Dialog (not an AlertDialog): it is a form. It states how many
    tracks it changes (and how many are on other pages), and how many already
    have tags (``selected_with_tags_count``, counted when it opens). Add keeps
    those tags; Replace overwrites them, and while Replace is chosen the confirm
    button is ``btn-danger``. The tags field is a client-owned compose field; the
    confirm button (or Enter in the field) submits the form, and
    ``submit_bulk_tags`` reads the text and the ``mode`` radios from the form data.
    """
    replace = LibraryState.bulk_tag_mode == BULK_TAG_REPLACE
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Tag selected tracks"),
            rx.dialog.description(LibraryState.bulk_tag_scope_line, size="2"),
            rx.el.form(
                compose_input(
                    id=LIBRARY_BULK_TAGS_ID,
                    name="tags",
                    value=LibraryState.bulk_tag_input,
                    on_commit=LibraryState.set_bulk_tag_input,
                    placeholder="e.g. chill, focus, instrumental",
                    class_name="input",
                    aria_label="Tags to apply",
                    aria_describedby=BULK_TAG_ERROR_ID,
                    style={"width": "100%", "marginTop": "12px"},
                ),
                # "Enter at least one tag." (persistent alert region).
                rx.box(
                    rx.cond(
                        LibraryState.bulk_tag_error != "",
                        rx.el.p(
                            rx.icon("circle-alert", size=14, aria_hidden="true"),
                            LibraryState.bulk_tag_error,
                            class_name="field-hint",
                        ),
                        rx.fragment(),
                    ),
                    id=BULK_TAG_ERROR_ID,
                    role="alert",
                ),
                seg_radio_group(
                    name="mode",
                    legend="Existing tags",
                    options=[
                        (BULK_TAG_ADD, "Add to them"),
                        (BULK_TAG_REPLACE, "Replace them"),
                    ],
                    current=LibraryState.bulk_tag_mode,
                    on_select=LibraryState.set_bulk_tag_mode,
                    legend_id=BULK_MODE_LEGEND_ID,
                    class_name="lib-bulk-mode",
                ),
                # The replace count; a warning callout while Replace is chosen.
                rx.box(
                    LibraryState.bulk_tag_existing_line,
                    id="lib-bulk-existing",
                    role="status",
                    class_name=rx.cond(
                        replace,
                        "callout callout-warning lib-bulk-existing",
                        "lib-bulk-existing",
                    ),
                ),
                rx.hstack(
                    rx.dialog.close(
                        rx.el.button("Cancel", class_name="btn btn-3 btn-soft", type="button"),
                    ),
                    rx.el.button(
                        rx.cond(replace, "Replace tags", "Add tags"),
                        class_name=rx.cond(
                            replace, "btn btn-3 btn-danger", "btn btn-3 btn-primary"
                        ),
                        id="lib-bulk-tags-confirm",
                        # Replace waits for the count it states (a moment).
                        disabled=LibraryState.bulk_tag_confirm_blocked,
                        type="submit",
                    ),
                    spacing="2",
                    margin_top="16px",
                    justify="end",
                ),
                on_submit=LibraryState.submit_bulk_tags,
            ),
            max_width="440px",
            aria_modal="true",
            on_close_auto_focus=rx.call_script(_RESTORE_FOCUS_JS),
        ),
        open=LibraryState.show_bulk_tag_dialog,
        on_open_change=LibraryState.set_bulk_tag_dialog_open,
    )


def _regenerate_scope_line(line: Any) -> rx.Component:
    """One line of the regenerate scope, rendered only once it has text."""
    return cast(
        rx.Component,
        rx.cond(line != "", rx.el.span(line, class_name="lib-dialog-line"), rx.fragment()),
    )


def _regenerate_step(line: Any, role: str, label: Any, model: Any) -> rx.Component:
    """One step of the regenerate scope: its count sentence, then its provider.

    "5,943 tracks will be tagged. AI provider: OpenAI · gpt-5-mini", with the model
    in the mono data style. Nothing is rendered while the scope is still being read.
    """
    return cast(
        rx.Component,
        rx.cond(
            line != "",
            rx.el.span(
                line, " ", role, scope_provider_line(label, model), class_name="lib-dialog-line"
            ),
            rx.fragment(),
        ),
    )


def _regenerate_confirm_dialog() -> rx.Component:
    """Confirm the "Regenerate" sync, which replaces every tag and embedding in place.

    An AlertDialog: Cancel (``alert_dialog.cancel``) takes focus on open, Escape
    dismisses through ``on_open_change`` and outside clicks are ignored. The body
    states how many tracks, the AI provider and the embedding provider with their
    models, and where track details are sent, from ``LibraryState.regenerate_scope``
    (``readiness.describe_job_scope`` copy). Then it says that nothing is cleared first.

    The scope is read when the dialog opens and arrives a moment later, so it sits
    in a polite live region, and "Regenerate" is disabled until it is stated. The
    confirm is a plain button, not ``alert_dialog.action``; ``start_sync`` closes
    the dialog by state, and ``set_regenerate_confirm_open(False)`` is idempotent.
    """
    scope = LibraryState.regenerate_scope
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Regenerate all tags & embeddings?"),
            rx.alert_dialog.description(
                rx.el.span(
                    _regenerate_scope_line(scope["checking_line"]),
                    _regenerate_step(
                        scope["tags_line"], "AI provider: ", scope["ai_label"], scope["ai_model"]
                    ),
                    _regenerate_step(
                        scope["embeddings_line"],
                        "Embedding provider: ",
                        scope["embedding_label"],
                        scope["embedding_model"],
                    ),
                    _regenerate_scope_line(scope["privacy_line"]),
                    id=REGENERATE_SCOPE_ID,
                    role="status",
                    class_name="lib-dialog-line",
                ),
                rx.el.span(REGENERATE_IN_PLACE_LINE, class_name="lib-dialog-line"),
                rx.el.span(REGENERATE_COST_LINE, class_name="lib-dialog-line"),
                size="2",
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.el.button("Cancel", class_name="btn btn-3 btn-soft", type="button"),
                ),
                rx.el.button(
                    "Regenerate",
                    class_name="btn btn-3 btn-danger",
                    on_click=LibraryState.start_sync,
                    # Not before the dialog has stated what it confirms (a moment).
                    disabled=LibraryState.regenerate_confirm_blocked,
                    type="button",
                ),
                spacing="2",
                margin_top="16px",
                justify="end",
            ),
            max_width="480px",
            aria_modal="true",
            on_close_auto_focus=rx.call_script(_RESTORE_FOCUS_JS),
        ),
        open=LibraryState.show_regenerate_confirm,
        on_open_change=LibraryState.set_regenerate_confirm_open,
    )


def _delete_confirm_dialog() -> rx.Component:
    """Confirm deleting the selected tracks (AlertDialog; see the regenerate confirm).

    States the total, how many of them are on other pages, and what deleting
    means: tracks still in Plex come back on the next sync, without their tags
    or embeddings.
    """
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Delete selected tracks?"),
            rx.alert_dialog.description(
                rx.el.span(LibraryState.delete_scope_line, class_name="lib-dialog-line"),
                rx.el.span(
                    "Tracks still in Plex come back on the next sync, without their "
                    "tags or embeddings. Your Plex library is not affected.",
                    class_name="lib-dialog-line",
                ),
                size="2",
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.el.button("Cancel", class_name="btn btn-3 btn-soft", type="button"),
                ),
                rx.el.button(
                    "Delete",
                    class_name="btn btn-3 btn-danger",
                    on_click=LibraryState.delete_selected_tracks,
                    type="button",
                ),
                spacing="2",
                margin_top="16px",
                justify="end",
            ),
            max_width="440px",
            aria_modal="true",
            on_close_auto_focus=rx.call_script(_RESTORE_FOCUS_JS),
        ),
        open=LibraryState.show_delete_confirm,
        on_open_change=LibraryState.set_delete_confirm_open,
    )


def _removal_confirm_dialog() -> rx.Component:
    """Confirm removing the tracks the last sync kept (AlertDialog; see the regenerate confirm).

    States how many tracks are removed out of how many, that their tags,
    embeddings and playlist entries go with them, that a backup is made first and
    that Plex is checked again. Cancel is the ``alert_dialog.cancel`` part, so it
    takes focus on open; Escape dismisses and the notice stays. The confirm is a
    plain button: ``confirm_removal`` closes the dialog by state.
    """
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Remove tracks Plex no longer lists?"),
            rx.alert_dialog.description(
                rx.el.span(LibraryState.removal_scope_line, class_name="lib-dialog-line"),
                rx.el.span(
                    "Their tags, embeddings and playlist entries are removed with them. "
                    "Your Plex library is not affected.",
                    class_name="lib-dialog-line",
                ),
                rx.el.span(
                    "A backup of your PlexMix library is made first. Plex is checked again, "
                    "and only tracks it still does not list are removed.",
                    class_name="lib-dialog-line",
                ),
                size="2",
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.el.button("Cancel", class_name="btn btn-3 btn-soft", type="button"),
                ),
                rx.el.button(
                    "Remove tracks",
                    class_name="btn btn-3 btn-danger",
                    on_click=LibraryState.confirm_removal,
                    type="button",
                ),
                spacing="2",
                margin_top="16px",
                justify="end",
            ),
            max_width="480px",
            aria_modal="true",
            on_close_auto_focus=rx.call_script(_RESTORE_FOCUS_JS),
        ),
        open=LibraryState.show_removal_confirm,
        on_open_change=LibraryState.set_removal_confirm_open,
    )


def _enrichment_chip(flag: rx.Var, icon: str, on_title: str, off_title: str) -> rx.Component:
    """One decorative status chip; ``.enr-on`` is filled, ``.enr-off`` is a dashed outline."""
    present = flag == "1"
    return rx.el.span(
        rx.icon(icon, size=12),
        class_name=rx.cond(present, "enr enr-on", "enr enr-off"),
        title=rx.cond(present, on_title, off_title),
        aria_hidden="true",
    )


def _enrichment_cell(track: ObjectVar[dict[str, str]]) -> rx.Component:
    """Tags / embedding / audio / MusicBrainz chips plus an sr-only text alternative."""
    return rx.el.td(
        rx.el.span(
            *[
                _enrichment_chip(track[key], icon, on_title, off_title)
                for key, icon, on_title, off_title in _ENRICHMENT_CHIPS
            ],
            class_name="enr-set",
        ),
        rx.el.span(track["enr_label"], class_name="sr-only"),
    )


def _track_row(track: ObjectVar[dict[str, str]]) -> rx.Component:
    tid = track["id"]
    return rx.el.tr(
        rx.el.td(
            rx.checkbox(
                checked=LibraryState.selected_tracks.contains(tid),
                # mypy sees the plain method, not Reflex's bound EventHandler.
                on_change=lambda _v: LibraryState.toggle_track_selection(  # type: ignore[call-arg]
                    tid  # type: ignore[arg-type]
                ),
                aria_label="Select " + track["title"] + " by " + track["artist_name"],
            ),
            style={"width": "40px"},
        ),
        rx.el.td(track["title"], style={"fontWeight": "500"}),
        rx.el.td(track["artist_name"], class_name="fg2"),
        rx.el.td(track["album_title"], class_name="fg3"),
        _enrichment_cell(track),
    )


def _sort_header(label: str, column: str) -> rx.Component:
    """A sortable column header: ``th[aria-sort] > button.th-sort`` wired to set_sort.

    ``aria-sort`` follows ``aria_sort_value`` (plexmix.ui.utils.a11y): ascending or
    descending on the sorted column, ``none`` on the others.
    """
    is_sorted = LibraryState.sort_column == column
    return rx.el.th(
        rx.el.button(
            label,
            rx.cond(
                is_sorted,
                rx.cond(
                    LibraryState.sort_ascending,
                    rx.icon("arrow-up", size=13, aria_hidden="true"),
                    rx.icon("arrow-down", size=13, aria_hidden="true"),
                ),
                rx.icon("chevrons-up-down", size=13, aria_hidden="true"),
            ),
            type="button",
            class_name="th-sort",
            # mypy sees the plain method, not Reflex's bound EventHandler.
            on_click=LibraryState.set_sort(column),  # type: ignore[call-arg, arg-type]
        ),
        aria_sort=rx.cond(
            is_sorted,
            rx.cond(LibraryState.sort_ascending, "ascending", "descending"),
            "none",
        ),
    )


def _track_table() -> rx.Component:
    return rx.box(
        rx.el.table(
            rx.el.thead(
                rx.el.tr(
                    rx.el.th(
                        rx.checkbox(
                            checked=LibraryState.all_page_selected,
                            on_change=LibraryState.toggle_select_all,
                            aria_label="Select all tracks on this page",
                        ),
                    ),
                    _sort_header("Title", "title"),
                    _sort_header("Artist", "artist"),
                    _sort_header("Album", "album"),
                    rx.el.th("Enrichment"),
                )
            ),
            rx.el.tbody(rx.foreach(LibraryState.tracks, _track_row)),
            class_name="tbl",
        ),
        # tbl-scroll: the table scrolls sideways inside this box when it can't fit,
        # instead of the shared .tbl-wrap clipping whole columns.
        class_name="tbl-wrap tbl-scroll",
        style={"width": "100%"},
    )


def _table_loading() -> rx.Component:
    """Placeholder rows until the first track query completes (never "No tracks")."""
    return rx.box(
        rx.el.span("Loading tracks…", class_name="sr-only"),
        *[
            rx.box(class_name="skeleton", aria_hidden="true", style={"width": width})
            for width in ("100%", "94%", "97%", "90%", "96%", "92%")
        ],
        id="lib-tracks-loading",
        class_name="tbl-wrap lib-skeleton",
        aria_busy="true",
    )


def _table_error() -> rx.Component:
    """The track query failed: say so (not "No tracks") and offer Retry."""
    return rx.box(
        rx.box(rx.icon("circle-alert", size=16, aria_hidden="true"), class_name="c-ico"),
        rx.box(LibraryState.tracks_error, class_name="c-body lib-callout-text"),
        rx.el.button(
            "Retry",
            class_name="btn btn-sm btn-soft",
            on_click=LibraryState.retry_load_tracks,
            aria_label="Retry loading tracks",
            type="button",
        ),
        id="lib-tracks-error",
        role="alert",
        class_name="callout callout-error",
        style={"width": "100%"},
    )


def _empty_state(icon: str, title: str, desc: str, action: rx.Component) -> rx.Component:
    return rx.box(
        rx.box(rx.icon(icon, size=22, color="var(--fg-3)", aria_hidden="true"), class_name="e-ico"),
        rx.box(title, class_name="e-title"),
        rx.box(desc, class_name="e-desc"),
        action,
        class_name="empty",
        style={"width": "100%"},
    )


def _empty_not_synced() -> rx.Component:
    """The library is empty: point to Sync (a UI action, never a terminal command)."""
    return _empty_state(
        "music",
        "No tracks synced yet",
        "Sync copies your Plex music library into PlexMix so you can search it, "
        "tag it and build playlists from it.",
        rx.el.button(
            rx.icon("refresh-cw", size=16, aria_hidden="true"),
            "Sync library",
            class_name="btn btn-3 btn-soft",
            on_click=_SYNC_CLICK,
            disabled=LibraryState.is_syncing,
            type="button",
        ),
    )


def _empty_no_match() -> rx.Component:
    """Filters exclude every track: say so and offer Clear filters.

    The button belongs to the filter form (``form=``), so plexmix.js
    ``data-clear-form`` empties the client-owned filter fields in the click,
    exactly like the filter bar's own Clear; ``clear_filters`` then resets state.
    """
    return _empty_state(
        "search-x",
        "No tracks match",
        "Nothing in your library matches the current filters.",
        rx.el.button(
            "Clear filters",
            class_name="btn btn-3 btn-soft",
            on_click=cast(Any, LibraryState.clear_filters).debounce(COMMIT_DEBOUNCE_MS),
            form=FILTER_FORM_ID,
            type="button",
            custom_attrs={"data-clear-form": ""},
        ),
    )


def _table_area() -> rx.Component:
    """Loading placeholder, load error, the table, "No tracks match" or "No tracks synced yet"."""
    # rx.match over components builds a component (its stub also allows a Var).
    return cast(
        rx.Component,
        rx.match(
            LibraryState.table_view,
            ("rows", _track_table()),
            ("loading", _table_loading()),
            ("error", _table_error()),
            ("no_match", _empty_no_match()),
            _empty_not_synced(),
        ),
    )


def _pagination() -> rx.Component:
    return rx.box(
        # No count until the first query has run (never a made-up "0 tracks").
        rx.cond(
            LibraryState.tracks_loaded,
            rx.el.span(
                LibraryState.total_filtered_tracks,
                " tracks",
                class_name="fg3",
                style={"fontSize": "13px"},
            ),
            rx.fragment(),
        ),
        rx.spacer(),
        rx.el.button(
            rx.icon("chevron-left", size=16, aria_hidden="true"),
            class_name="btn btn-sm btn-soft",
            on_click=LibraryState.previous_page,
            disabled=LibraryState.current_page <= 1,
            aria_label="Previous page",
            id=PREV_PAGE_BUTTON_ID,
            type="button",
        ),
        rx.el.span(
            "Page ",
            LibraryState.current_page,
            class_name="mono",
            style={"fontSize": "13px", "padding": "0 8px"},
        ),
        rx.el.button(
            rx.icon("chevron-right", size=16, aria_hidden="true"),
            class_name="btn btn-sm btn-soft",
            on_click=LibraryState.next_page,
            disabled=~LibraryState.has_next_page,
            aria_label="Next page",
            id=NEXT_PAGE_BUTTON_ID,
            type="button",
        ),
        # Wraps at narrow widths (320 CSS px / 200% zoom) instead of overflowing.
        style={
            "display": "flex",
            "alignItems": "center",
            "gap": "6px",
            "width": "100%",
            "flexWrap": "wrap",
        },
    )


def library() -> rx.Component:
    content = rx.vstack(
        # Hidden poller: drives poll_task_progress every 2s so long-running job
        # progress updates live (on_load alone only polled once, so bars froze).
        rx.box(
            rx.moment(interval=2000, on_change=LibraryState.poll_task_progress),
            display="none",
        ),
        _actions(),
        _job_bar(
            LibraryState.is_syncing,
            LibraryState.sync_message,
            LibraryState.sync_progress,
            "sync",
            "library sync",
        ),
        _job_bar(
            LibraryState.is_embedding,
            LibraryState.embedding_message,
            LibraryState.embedding_progress,
            "embedding",
            "embedding generation",
        ),
        _job_bar(
            LibraryState.is_enriching_musicbrainz,
            LibraryState.musicbrainz_message,
            LibraryState.musicbrainz_progress,
            "musicbrainz",
            "MusicBrainz enrichment",
        ),
        _job_bar(
            LibraryState.is_analyzing_audio,
            LibraryState.audio_analysis_message,
            LibraryState.audio_analysis_progress,
            "audio",
            "audio analysis",
        ),
        _filter_bar(),
        _table_area(),
        # After the table and sticky to the viewport bottom (see _bulk_bar).
        _bulk_bar(),
        _pagination(),
        _bulk_tag_dialog(),
        _delete_confirm_dialog(),
        _regenerate_confirm_dialog(),
        _removal_confirm_dialog(),
        embed_all_confirm_dialog(),
        spacing="4",
        width="100%",
        on_mount=LibraryState.on_load,
    )
    return layout(content)
