"""Generator — the showpiece: idle hero → AI-thinking stage → results.

Bound entirely to the existing ``GeneratorState`` (no backend logic changes
beyond the additive cancel/view-state). The thinking visuals are driven by the
real ``generation_progress`` reported by ``generate_playlist``.

The prompt box is a form whose fields are client-owned (``components/inputs.py``):
Generate and ⌘/Ctrl+Enter submit the text on screen through
``GeneratorState.submit_prompt``, and server-side writes to the prompt (vibe pills,
History reruns, palette prefills) reach the textarea through ``rx.set_value``.

Honest status (webui-honest-status-and-first-run, design D9): a readiness line under
the prompt foot names the track count, the embedding provider and model that will
embed the prompt, the index state and where the prompt goes. Generate is disabled
only while a blocking readiness cause stops generation (``can_generate``), applied
filters without an Options control show as removable chips, and a failed generation
leaves a callout with its cause and one action until it is dismissed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import reflex as rx
from reflex.components.core.banner import (
    connect_error_var_data,
    has_too_many_connection_errors,
)

from plexmix.ui.components.inputs import compose_textarea, filter_input
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.playlist_cover import playlist_cover
from plexmix.ui.components.seg_radio import seg_radio_group
from plexmix.ui.states.generator_state import (
    FAILURE_FOCUS_SCRIPT,
    GENRE_INPUT_ID,
    PROMPT_INPUT_ID,
    PROMPT_MAX_CHARS,
    GeneratorState,
)
from plexmix.ui.utils.a11y import focus_first_script

if TYPE_CHECKING:
    from reflex.vars.object import ObjectVar


# Four phases, mapped to the real progress bands (0–10 / 10–40 / 40–70 / 70–100).
_PHASES = [
    ("Embed vibe", "brain"),
    ("Vector search", "search"),
    ("Diversity", "layers"),
    ("Order set", "audio-waveform"),
]

_ORDERINGS = [
    ("similarity", "Similarity"),
    ("random", "Random"),
    ("alternating_artists", "Alt. artists"),
    ("energy_curve", "Energy curve"),
]

# The Generator canvas and log-autoscroll scripts live in assets/plexmix.js,
# loaded from <head> (Helmet-injected page scripts are dropped under React StrictMode).

# Run when a stage mounts (idle hero, thinking, results). Each stage owns the page's
# one h1 (#main-heading); a failure lands on the error callout first. pmFocus only
# moves focus when it was lost (the focused control left with the previous stage),
# so a fresh page load is never hijacked.
_STAGE_FOCUS_SCRIPT = focus_first_script(["#gen-error", "#main-heading"])

# Lost connection (the backend process died, or the network dropped). The server cannot
# report that, so the thinking stage reads Reflex's client-side connection state: the
# browser's ``connectErrors`` list, one entry per failed reconnect attempt (capped at 10,
# emptied the moment the websocket connects). The client starts the next attempt
# 200 ms x (entries so far) after a failure, so against a backend on the same machine
# that stopped the 2nd failure lands about 0.2 s after the drop and the 5th about 2 s
# after it (measured in a test harness; in the running app, headless Chromium against a
# killed backend or a closed port, 0.2-0.5 s and 2.0-2.3 s). Two steps, so a short drop
# does not raise a false alarm: a quiet line at ``_RECONNECT_AFTER`` failures in a row,
# an alert at ``_ALERT_AFTER``. The expressions come from a Reflex-internal module
# (``reflex.components.core.banner``, not exported at ``rx.*``) and the thresholds depend
# on the client's retry policy: recheck both on every Reflex upgrade (the render smoke
# pins the compiled expressions; CI does not run it).
_RECONNECT_AFTER = 2
_ALERT_AFTER = 5

# 2 or more failures: Reflex's own var, fixed at 2 (the render smoke ties it to
# ``_RECONNECT_AFTER``).
_CONN_RETRYING = has_too_many_connection_errors
# 5 or more failures.
_CONN_LOST = rx.Var(
    _js_expr=f"(connectErrors.length >= {_ALERT_AFTER})", _var_data=connect_error_var_data
).to(bool)
# 2 to 4 failures: the quiet step only.
_CONN_RETRYING_ONLY = rx.Var(
    _js_expr=(
        f"((connectErrors.length >= {_RECONNECT_AFTER}) && "
        f"(connectErrors.length < {_ALERT_AFTER}))"
    ),
    _var_data=connect_error_var_data,
).to(bool)

RECONNECTING_TEXT = "Reconnecting to PlexMix…"
LOST_CONNECTION_TITLE = "Lost connection to PlexMix"
LOST_CONNECTION_BODY = (
    "This page can't reach PlexMix. It keeps trying and reconnects on its own. Your "
    "playlist may still be generating: if the connection returns, you'll see where it got "
    "to. If it does not reconnect, PlexMix may have stopped. Start it again, then generate "
    "again."
)

# Run by the idle-stage triggers just before generation starts (a vibe pill's
# use_vibe returns it ahead of generate_playlist, only when generation can run; the
# prompt form runs it through _SUBMIT_FOCUS_SCRIPT before submit_prompt). A generation
# that fails fast arrives in the same React render as its start, so the thinking stage
# never mounts, the idle hero never remounts, its on_mount never runs and focus stayed
# on Generate. This chases the error callout instead (see
# generator_state.FAILURE_FOCUS_SCRIPT).
_FAILURE_FOCUS_SCRIPT = FAILURE_FOCUS_SCRIPT

# The prompt form's version, run first in its on_submit chain (Generate, ⌘/Ctrl+Enter).
# A blank prompt starts no generation (submit_prompt shows the field hint instead), so
# the chase is skipped: polling would otherwise land on a stale callout from an earlier
# failure when the poll expires. The textarea is still mounted when this runs.
_SUBMIT_FOCUS_SCRIPT = (
    f"(function(){{var p=document.getElementById('{PROMPT_INPUT_ID}');"
    f"if(!p||p.value.trim()){_FAILURE_FOCUS_SCRIPT}}})()"
)

# The live "12/280" counter (assets/plexmix.js rewrites its text node on every input
# in the field that names it in data-counter) and the blank-prompt hint region.
_PROMPT_COUNT_ID = "gen-prompt-count"
_PROMPT_HINT_ID = "gen-prompt-hint"

# The readiness line under the prompt foot (also Generate's description).
_READY_LINE_ID = "gen-ready-line"

# A removed chip takes its focused button with it: focus the next chip's remove
# button, or the prompt once no chip is left (only when focus was lost). "Clear all"
# and the callout's Dismiss land on the prompt the same way.
_CHIP_FOCUS_SCRIPT = focus_first_script([".chip-x-btn", f"#{PROMPT_INPUT_ID}"])
_PROMPT_FOCUS_SCRIPT = focus_first_script([f"#{PROMPT_INPUT_ID}"])


# Only Generate and ⌘/Ctrl+Enter submit the prompt form. Browsers also submit a form on
# Enter in a text field, a range slider or a radio, so the advanced controls carry
# data-enter="none" (assets/plexmix.js suppresses Enter there; design D4).
def _no_enter() -> dict[str, Any]:
    """A fresh ``data-enter="none"`` attribute dict (Reflex mutates ``custom_attrs``)."""
    return {"data-enter": "none"}


# ── Idle hero ─────────────────────────────────────────────────────────


def _adv_panel() -> rx.Component:
    # Always rendered (the Options button's aria-controls target must exist) and
    # hidden with the native ``hidden`` attribute while collapsed; redesign.css
    # keeps ``.adv-panel[hidden]`` from being re-shown by its ``display: grid``.
    return rx.el.div(
        rx.box(
            rx.el.label(
                "Tracks",
                rx.el.span(GeneratorState.max_tracks, class_name="val"),
                html_for="gen-max-tracks",
                class_name="adv-label",
            ),
            rx.el.input(
                id="gen-max-tracks",
                type="range",
                min="10",
                max="100",
                step="5",
                value=GeneratorState.max_tracks,
                on_change=GeneratorState.set_max_tracks,
                custom_attrs=_no_enter(),
            ),
        ),
        rx.box(
            rx.el.label(
                "Candidate pool ×",
                rx.el.span(GeneratorState.candidate_pool_multiplier, class_name="val"),
                html_for="gen-pool",
                class_name="adv-label",
            ),
            rx.el.input(
                id="gen-pool",
                type="range",
                min="1",
                max="100",
                step="1",
                value=GeneratorState.candidate_pool_multiplier,
                on_change=GeneratorState.set_candidate_pool_multiplier,
                custom_attrs=_no_enter(),
            ),
        ),
        rx.box(
            rx.el.label("Genre focus", html_for=GENRE_INPUT_ID, class_name="adv-label"),
            # Client-owned: applies 300 ms after typing pauses and on blur. Submitting
            # the prompt form sends it too (name="genre"); Enter here does not generate.
            filter_input(
                id=GENRE_INPUT_ID,
                name="genre",
                value=GeneratorState.genre_filter,
                on_commit=GeneratorState.set_genre_filter,
                class_name="input",
                placeholder="Any genre",
                custom_attrs=_no_enter(),
            ),
        ),
        seg_radio_group(
            name="gen-ordering",
            legend="Track ordering",
            options=_ORDERINGS,
            current=GeneratorState.shuffle_mode,
            on_select=GeneratorState.set_shuffle_mode,
            legend_id="gen-ordering-legend",
            class_name="adv-full",
            # The radios are inside the prompt form: Enter on one must not generate.
            custom_attrs=_no_enter(),
        ),
        id="gen-adv-panel",
        hidden=~GeneratorState.show_advanced,
        class_name="adv-panel",
    )


def _constraint_chip(chip: ObjectVar[dict[str, str]]) -> rx.Component:
    # One applied filter: its name, the mono value and a named remove button.
    return rx.el.li(
        rx.el.span(chip["name"], class_name="chip-x-name"),
        rx.el.span(chip["value"], class_name="chip-x-value mono"),
        rx.el.button(
            rx.icon("x", size=12),
            type="button",
            class_name="chip-x-btn",
            aria_label="Remove filter: " + chip["label"],
            title="Remove filter",
            on_click=[
                cast(Any, GeneratorState.remove_constraint)(chip["key"]),
                rx.call_script(_CHIP_FOCUS_SCRIPT),
            ],
        ),
        class_name="chip-x",
        key=chip["key"],
    )


def _constraint_chips() -> rx.Component:
    # Filters a rerun or template carried that have no Options control (year, tempo,
    # energy, key, danceability, avoid recent). Outside the Options panel, so they
    # show whether it is open or not; nothing is applied while hidden.
    constraints = cast(Any, GeneratorState.active_constraints)
    return rx.cond(
        constraints.length() > 0,
        rx.box(
            rx.el.span("Filters", id="gen-filters-label", class_name="chip-x-label"),
            rx.el.ul(
                rx.foreach(constraints, _constraint_chip),
                role="list",
                aria_labelledby="gen-filters-label",
                class_name="chip-x-list",
            ),
            rx.el.button(
                "Clear all",
                type="button",
                class_name="btn btn-sm btn-ghost chip-x-clear",
                aria_label="Clear all filters",
                on_click=[GeneratorState.clear_constraints, rx.call_script(_PROMPT_FOCUS_SCRIPT)],
            ),
            class_name="chip-x-row",
        ),
        rx.fragment(),
    )


def _rl_link(label: rx.Var, href: rx.Var) -> rx.Component:
    return rx.cond(
        href,
        rx.link(label, href=href, class_name="rl-action", underline="always"),
        rx.fragment(),
    )


def _ready_line() -> rx.Component:
    # "5,943 tracks · Local · all-MiniLM-L6-v2 (384d) · ready · prompt stays on this
    # machine", then the cause and its action when generation is blocked or an earlier
    # run failed. A persistent polite region: it exists before its text is written.
    s = cast(Any, GeneratorState.gen_status)
    return rx.box(
        rx.cond(
            s["loaded"] == "true",
            rx.fragment(
                rx.el.p(
                    rx.el.span(class_name="dot dot-" + s["tone"]),
                    rx.el.span(s["tracks"], class_name="mono"),
                    " · ",
                    rx.el.span(s["provider"], class_name="rl-provider"),
                    rx.cond(
                        s["model_dim"] != "",
                        rx.fragment(" · ", rx.el.span(s["model_dim"], class_name="mono")),
                        rx.fragment(),
                    ),
                    " · ",
                    rx.el.span(s["state"], class_name="rl-state"),
                    rx.cond(
                        s["privacy"] != "",
                        rx.fragment(" · ", rx.el.span(s["privacy"], class_name="rl-privacy")),
                        rx.fragment(),
                    ),
                    class_name="rl-facts",
                ),
                rx.cond(
                    s["issue"] == "true",
                    rx.el.p(
                        rx.el.span(s["title"], class_name="rl-title"),
                        rx.el.span(s["body"], class_name="rl-body"),
                        _rl_link(s["action_label"], s["action_href"]),
                        _rl_link(s["secondary_label"], s["secondary_href"]),
                        class_name="rl-issue",
                    ),
                    rx.fragment(),
                ),
            ),
            rx.fragment(
                rx.el.span(class_name="skeleton rl-skeleton", aria_hidden="true"),
                rx.el.span("Checking your library…", class_name="sr-only"),
            ),
        ),
        id=_READY_LINE_ID,
        role="status",
        class_name="ready-line",
    )


def _prompt_box() -> rx.Component:
    # A form, so Generate (its one submit button) and ⌘/Ctrl+Enter generate from the text
    # the fields hold right now (form data: "prompt" and "genre"), never from a server
    # copy that may still be catching up (design D4). The prompt is client-owned: it
    # commits on blur only, and server writes reach it through rx.set_value.
    return rx.el.form(
        compose_textarea(
            id=PROMPT_INPUT_ID,
            name="prompt",
            value=GeneratorState.mood_query,
            on_commit=GeneratorState.set_mood_query,
            max_length=PROMPT_MAX_CHARS,
            aria_label="Describe the playlist you want",
            aria_describedby=_PROMPT_HINT_ID,
            aria_invalid=rx.cond(GeneratorState.prompt_error != "", "true", "false"),
            placeholder="rainy day melancholy with a little hope…",
            class_name="prompt-input",
            rows="2",
            # plexmix.js: ⌘/Ctrl+Enter submits the form; each input updates the counter.
            custom_attrs={"data-submit-shortcut": "", "data-counter": _PROMPT_COUNT_ID},
        ),
        rx.box(
            rx.el.button(
                rx.icon("sliders-horizontal", size=15),
                "Options",
                rx.cond(
                    GeneratorState.show_advanced,
                    rx.icon("chevron-up", size=14),
                    rx.icon("chevron-down", size=14),
                ),
                class_name=rx.cond(GeneratorState.show_advanced, "opt-btn on", "opt-btn"),
                on_click=GeneratorState.toggle_advanced,
                type="button",
                aria_expanded=rx.cond(GeneratorState.show_advanced, "true", "false"),
                aria_controls="gen-adv-panel",
            ),
            # One text node ("12/280"): React writes it when mood_query changes on the
            # server, plexmix.js rewrites the same node on every keystroke.
            rx.el.span(
                GeneratorState.prompt_count_label, id=_PROMPT_COUNT_ID, class_name="charcount"
            ),
            rx.box(class_name="spacer"),
            # Disabled only while a blocking readiness cause stops generation (the
            # readiness line below says why). Never for an empty prompt: the server may
            # not have seen the typing yet; submit_prompt checks the submitted text.
            rx.el.button(
                rx.icon("sparkles", size=16),
                "Generate",
                class_name="btn btn-3 btn-primary glow",
                type="submit",
                disabled=~GeneratorState.can_generate,
                aria_describedby=_READY_LINE_ID,
            ),
            class_name="prompt-foot",
        ),
        _constraint_chips(),
        _ready_line(),
        _adv_panel(),
        class_name="prompt-box",
        # The failure-focus chase runs before the submit (see _SUBMIT_FOCUS_SCRIPT).
        on_submit=[rx.call_script(_SUBMIT_FOCUS_SCRIPT), GeneratorState.submit_prompt],
    )


def _prompt_hint() -> rx.Component:
    # "Describe a vibe first." under the prompt box after a blank submit. The region
    # stays mounted (and is the prompt's aria-describedby target) so the message is
    # announced when it appears.
    return rx.box(
        rx.cond(
            GeneratorState.prompt_error != "",
            rx.el.p(
                rx.icon("circle-alert", size=14),
                GeneratorState.prompt_error,
                class_name="field-hint",
            ),
            rx.fragment(),
        ),
        id=_PROMPT_HINT_ID,
        role="alert",
        class_name="prompt-hint",
    )


def _callout_link(label: rx.Var, href: rx.Var) -> rx.Component:
    return rx.cond(
        href,
        rx.link(label, href=href, class_name="c-action", underline="always"),
        rx.fragment(),
    )


def _error_callout() -> rx.Component:
    # Persistent assertive region: it stays mounted while the callout inside it appears
    # and changes, so a failure is announced. The callout (#gen-error) is the stage's
    # focus target after a failed generation. It shows the cause's title, body and one
    # action (plus "Switch back" for a dimension mismatch) until Dismiss, a new
    # generation, or on_load finding the cause resolved.
    err = cast(Any, GeneratorState.generation_error)
    return rx.box(
        rx.cond(
            err["title"],
            rx.box(
                rx.box(rx.icon("circle-alert", size=16), class_name="c-ico"),
                rx.box(
                    rx.box(err["title"], class_name="c-title"),
                    rx.box(err["body"], class_name="c-body"),
                    rx.cond(
                        err["action_href"] | err["secondary_href"],
                        rx.box(
                            _callout_link(err["action_label"], err["action_href"]),
                            _callout_link(err["secondary_label"], err["secondary_href"]),
                            class_name="c-actions",
                        ),
                        rx.fragment(),
                    ),
                    class_name="c-main",
                ),
                rx.el.button(
                    rx.icon("x", size=15),
                    type="button",
                    class_name="icon-btn c-dismiss",
                    aria_label="Dismiss error",
                    title="Dismiss",
                    on_click=[GeneratorState.dismiss_error, rx.call_script(_PROMPT_FOCUS_SCRIPT)],
                ),
                id="gen-error",
                tab_index=-1,
                class_name=rx.cond(
                    err["tone"] == "warning",
                    "callout callout-warning",
                    "callout callout-error",
                ),
                style={"marginTop": "16px", "textAlign": "left"},
            ),
            rx.fragment(),
        ),
        role="alert",
        id="gen-error-region",
    )


def _idle_hero() -> rx.Component:
    return rx.box(
        rx.box(class_name="gen-hero-glow"),
        rx.box(
            rx.box(
                rx.icon("sparkles", size=14),
                "AI Playlist Generator",
                class_name="gen-kicker",
            ),
            rx.el.h1(
                "What should your ",
                rx.el.span("library", class_name="accent"),
                " sound like?",
                id="main-heading",
                tab_index=-1,
                class_name="gen-title",
            ),
            rx.el.p(
                "Describe a mood, a moment, or a memory. PlexMix searches every track "
                "you own and curates a playlist that fits — just your music.",
                class_name="gen-sub",
            ),
            _prompt_box(),
            _prompt_hint(),
            _error_callout(),
            rx.box(
                rx.foreach(
                    GeneratorState.mood_examples,
                    lambda v: rx.el.button(
                        v,
                        class_name="vibe-pill",
                        # use_vibe fills the prompt (returning the rx.set_value that shows
                        # it in the client-owned textarea, input-responsiveness D3) and,
                        # only when generation can run, the failure-focus chase and
                        # generate_playlist. mypy reads the @rx.event handler as an
                        # EventNamespace.
                        on_click=cast(Any, GeneratorState.use_vibe)(v),
                        type="button",
                    ),
                ),
                class_name="vibe-row",
            ),
            class_name="gen-hero-inner",
        ),
        class_name="gen-hero",
        on_mount=rx.call_script(_STAGE_FOCUS_SCRIPT),
    )


# ── Thinking stage ────────────────────────────────────────────────────


def _phase_node(idx: int, label: str, icon: str) -> rx.Component:
    ap = GeneratorState.active_phase
    return rx.box(
        rx.box(
            rx.box(
                class_name="fill",
                style={"width": rx.cond(ap > idx, "100%", "0%")},
            ),
            class_name="phase-line",
        ),
        rx.box(
            rx.cond(ap > idx, rx.icon("check", size=16), rx.icon(icon, size=16)),
            class_name="phase-dot",
        ),
        rx.box(label, class_name="phase-label"),
        class_name=rx.cond(
            ap > idx,
            "phase-node done",
            rx.cond(ap == idx, "phase-node active", "phase-node"),
        ),
    )


def _progress_row(pct: rx.Var) -> rx.Component:
    # "42%": the bar width, the visible percentage and the bar's value text.
    return rx.box(
        rx.box(
            rx.box(class_name="pfill", style={"width": pct}),
            role="progressbar",
            aria_label="Generation progress",
            aria_valuemin="0",
            aria_valuemax="100",
            aria_valuenow=GeneratorState.generation_progress,
            aria_valuetext=pct,
            class_name="pbar",
        ),
        rx.el.span(pct, class_name="pct"),
        rx.el.button(
            rx.icon("x", size=14),
            "Cancel",
            class_name="btn btn-sm btn-ghost",
            on_click=GeneratorState.cancel_generation,
            type="button",
        ),
        class_name="gen-progress",
    )


def _reconnect_notice() -> rx.Component:
    # Step 1 of a lost connection (2 to 4 failed reconnects): a quiet line in the
    # progress row's place. Persistent polite region: it is mounted with the stage, so the
    # line written into it is announced once. It is no focus target and has no control.
    # Client-side only: nothing here can need the server.
    return rx.box(
        rx.cond(
            _CONN_RETRYING_ONLY,
            rx.box(RECONNECTING_TEXT, id="gen-reconnect", class_name="gen-reconnect"),
            rx.fragment(),
        ),
        role="status",
        id="gen-reconnect-region",
    )


def _lost_notice() -> rx.Component:
    # Step 2 (5 or more failed reconnects): persistent assertive region, mounted with the
    # stage like #gen-error-region, so the callout inside it is announced once. The
    # wording is conditional: the playlist may still be generating on the server. No
    # tabindex, button or link: it is not a focus target and nothing in it needs the
    # server. assets/plexmix.js moves focus only when it was on the Cancel button that
    # left with the progress row, and then to the stage heading.
    return rx.box(
        rx.cond(
            _CONN_LOST,
            rx.box(
                rx.box(rx.icon("unplug", size=16), class_name="c-ico"),
                rx.box(
                    rx.box(LOST_CONNECTION_TITLE, class_name="c-title"),
                    rx.box(LOST_CONNECTION_BODY, class_name="c-body"),
                    class_name="c-main",
                ),
                id="gen-offline",
                class_name="callout callout-error",
            ),
            rx.fragment(),
        ),
        role="alert",
        id="gen-offline-region",
    )


def _thinking_stage() -> rx.Component:
    # "42%": the bar width, the visible percentage and the bar's value text.
    pct = GeneratorState.generation_progress.to_string() + "%"  # type: ignore[attr-defined]
    return rx.box(
        rx.box(
            rx.box(
                rx.el.h1(
                    rx.icon("sparkles", size=15, color="var(--accent-fg)"),
                    "Curating ",
                    rx.el.b('"', GeneratorState.mood_query, '"'),
                    id="main-heading",
                    tab_index=-1,
                    # The prompt is clamped to two lines (.think-query b): the full
                    # text stays in the DOM and shows on hover.
                    title=GeneratorState.mood_query,
                    class_name="think-query",
                ),
                class_name="think-head",
            ),
            rx.box(
                rx.el.canvas(id="thinkviz"),
                rx.box(GeneratorState.generation_message, class_name="viz-phase-label"),
                class_name="viz-wrap",
                custom_attrs={"data-phase": GeneratorState.active_phase.to_string()},
            ),
            rx.box(
                *[_phase_node(i, label, icon) for i, (label, icon) in enumerate(_PHASES)],
                class_name="phase-track",
            ),
            _reconnect_notice(),
            _lost_notice(),
            # While the page cannot reach the server the bar, the percentage and Cancel are
            # stale (a Cancel click could not reach the server), so the quiet line and then
            # the alert stand in for them.
            rx.cond(_CONN_RETRYING, rx.fragment(), _progress_row(pct)),
            # Polite, once per phase (about four times a run). The progress message
            # and the log stay out of live regions: they change too often.
            rx.box(
                rx.match(
                    GeneratorState.active_phase,
                    *[(idx, label) for idx, (label, _icon) in enumerate(_PHASES)],
                    "",
                ),
                role="status",
                id="gen-phase-status",
                class_name="sr-only",
            ),
            rx.box(
                rx.foreach(
                    GeneratorState.generation_log,
                    lambda ln: rx.box(rx.el.span(ln), class_name="ln"),
                ),
                class_name="genlog",
            ),
            class_name="think-inner fade-up",
        ),
        # .is-offline (alert step only): redesign.css dims the stage and plexmix.js idles the
        # canvas.
        class_name=rx.cond(_CONN_LOST, "think is-offline", "think"),
        on_mount=rx.call_script(_STAGE_FOCUS_SCRIPT),
    )


# ── Results ───────────────────────────────────────────────────────────


def _trk_row(track: ObjectVar[dict[str, str]], index: rx.Var) -> rx.Component:
    return rx.el.li(
        rx.el.span(index + 1, class_name="tnum"),
        rx.box(
            style={
                "width": "38px",
                "height": "38px",
                "borderRadius": "var(--radius-sm)",
                "background": "linear-gradient(135deg, var(--brand-7), var(--brand-10))",
                "flexShrink": "0",
            },
        ),
        rx.box(
            rx.box(track["title"], class_name="ttitle"),
            rx.box(track["artist"], class_name="tartist"),
            class_name="tinfo",
        ),
        rx.el.span(track["album"], class_name="talbum"),
        rx.el.span(track["duration_formatted"], class_name="tdur"),
        rx.el.button(
            rx.icon("x", size=15),
            class_name="icon-btn trm",
            title="Remove",
            aria_label="Remove " + track["title"] + " from playlist",
            # mypy reads the event handler as a plain method; the arg is a Var.
            on_click=GeneratorState.remove_track(track["id"]),  # type: ignore[arg-type]
            type="button",
        ),
        class_name="trk",
    )


def _results_stage() -> rx.Component:
    return rx.box(
        rx.box(
            # Same swatch History shows once this prompt's playlist is saved.
            playlist_cover(GeneratorState.cover_variant, class_name="result-cover"),
            rx.box(
                rx.box("Generated playlist", class_name="rk"),
                # Clamped to two lines (.result-meta h1); the full prompt shows on hover.
                rx.el.h1(
                    GeneratorState.mood_query,
                    id="main-heading",
                    tab_index=-1,
                    title=GeneratorState.mood_query,
                ),
                rx.box(
                    rx.el.span(
                        GeneratorState.generated_playlist.length(),
                        " tracks",
                        class_name="mono",
                    ),
                    " · ",
                    rx.el.span(GeneratorState.total_duration_label, class_name="mono"),
                    " · ordered by ",
                    rx.el.span(GeneratorState.ordering_label, class_name="badge badge-orange"),
                    class_name="result-stats",
                ),
                rx.box(
                    rx.el.button(
                        rx.icon("server", size=16),
                        "Save to Plex",
                        class_name="btn btn-3 btn-blue",
                        on_click=GeneratorState.save_to_plex,
                        type="button",
                    ),
                    rx.el.button(
                        rx.icon("hard-drive", size=16),
                        "Save Locally",
                        class_name="btn btn-3 btn-green",
                        on_click=GeneratorState.save_locally,
                        type="button",
                    ),
                    rx.el.button(
                        rx.icon("download", size=16),
                        "Export M3U",
                        class_name="btn btn-3 btn-soft",
                        on_click=GeneratorState.export_m3u,
                        type="button",
                    ),
                    rx.el.button(
                        rx.icon("refresh-cw", size=16),
                        "Regenerate",
                        class_name="btn btn-3 btn-outline",
                        on_click=GeneratorState.regenerate,
                        type="button",
                    ),
                    class_name="result-actions",
                ),
                class_name="result-meta",
            ),
            class_name="result-head",
        ),
        # Ordered list in playlist order. role="list" keeps the list semantics in
        # WebKit, which drops them for lists styled ``list-style: none``.
        rx.el.ol(
            rx.foreach(GeneratorState.generated_playlist, _trk_row),
            role="list",
            class_name="tbl-wrap trk-list",
            style={"padding": "6px"},
        ),
        rx.box(
            rx.el.button(
                rx.icon("plus", size=16),
                "New playlist",
                class_name="btn btn-3 btn-ghost",
                on_click=GeneratorState.new_playlist,
                type="button",
            ),
            style={"marginTop": "18px", "textAlign": "center"},
        ),
        class_name="gen-results fade-up",
        on_mount=rx.call_script(_STAGE_FOCUS_SCRIPT),
    )


# ── Page ──────────────────────────────────────────────────────────────


def generator() -> rx.Component:
    stage = rx.box(
        rx.cond(
            GeneratorState.is_generating,
            _thinking_stage(),
            rx.cond(
                GeneratorState.generated_playlist.length() > 0,
                _results_stage(),
                _idle_hero(),
            ),
        ),
        class_name="gen-stage",
    )
    # heading=False: each stage renders its own visible h1#main-heading.
    return layout(stage, full_bleed=True, heading=False)
