"""Render smoke: Generator names, states, progress, stage focus, the lost-connection notice,
results and the prompt form.

Run in its own session (real Reflex): ``pytest tests/ui_render -q``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from plexmix.ui.pages import generator as gen_page
from tests.ui_render.conftest import rendered

_REPO = Path(__file__).resolve().parents[2]
_FOCUS_SCRIPT = 'window.pmFocus&&pmFocus.when([\\"#gen-error\\", \\"#main-heading\\"])'


def _js(builder_name: str) -> str:
    return rendered(getattr(gen_page, builder_name)())


def _stage_hooks(builder_name: str) -> list[str]:
    return list(getattr(gen_page, builder_name)()._get_all_hooks())


def _mount_focus_hooks(builder_name: str) -> list[str]:
    """pmFocus hooks other than the prompt form's submit handler (which also runs one)."""
    return [h for h in _stage_hooks(builder_name) if "pmFocus" in h and "handleSubmit_" not in h]


def _submit_hook(builder_name: str) -> str:
    """The prompt form's ``handleSubmit_<hash>`` hook (exactly one per stage)."""
    hooks = [h for h in _stage_hooks(builder_name) if "const handleSubmit_" in h]
    assert len(hooks) == 1, hooks
    return hooks[0]


def _element(js: str, start: int) -> str:
    """Return the ``jsx(...)`` call that starts at ``start`` (balanced parentheses)."""
    depth = 0
    in_str = False
    escaped = False
    for pos in range(start, len(js)):
        ch = js[pos]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return js[start : pos + 1]
    raise AssertionError("unbalanced jsx call")


def _element_with(js: str, marker: str) -> str:
    """Return the innermost-starting ``jsx(...)`` element whose props contain ``marker``."""
    idx = js.index(marker)
    start = js.rindex("jsx(", 0, idx)
    return _element(js, start)


# ── 6.1 one h1 per stage, stage focus ────────────────────────────────


class TestStageHeadings:
    STAGES = ("_idle_hero", "_thinking_stage", "_results_stage")

    def test_exactly_one_h1_per_stage(self) -> None:
        for stage in self.STAGES:
            js = _js(stage)
            assert js.count('jsx("h1",') == 1, stage
            assert js.count('id:"main-heading"') == 1, stage

    def test_stage_h1_is_the_focusable_main_heading(self) -> None:
        for stage in self.STAGES:
            h1 = _element_with(_js(stage), 'id:"main-heading"')
            assert h1.startswith('jsx("h1",'), stage
            assert "tabIndex:-1" in h1, stage

    def test_hero_title_and_results_title_are_the_h1s(self) -> None:
        hero = _element_with(_js("_idle_hero"), 'id:"main-heading"')
        assert 'className:"gen-title"' in hero
        assert '"What should your "' in hero
        results = _element_with(_js("_results_stage"), 'id:"main-heading"')
        assert "mood_query_rx_state_" in results

    def test_thinking_query_pill_becomes_the_h1(self) -> None:
        h1 = _element_with(_js("_thinking_stage"), 'id:"main-heading"')
        assert 'className:"think-query"' in h1
        assert '"Curating "' in h1
        assert "mood_query_rx_state_" in h1

    def test_each_stage_root_runs_the_focus_script_on_mount(self) -> None:
        for stage in self.STAGES:
            hooks = _mount_focus_hooks(stage)
            assert len(hooks) == 1, stage
            assert "useEffect" in hooks[0], stage
            assert '"_call_script"' in hooks[0], stage
            assert _FOCUS_SCRIPT in hooks[0], stage

    def test_page_uses_stage_headings_not_the_shell_heading(self) -> None:
        js = rendered(gen_page.generator())
        # One h1 per stage branch of the rx.cond, and no sr-only shell heading.
        assert js.count('jsx("h1",') == 3
        assert js.count('id:"main-heading"') == 3
        assert 'jsx("h1",{className:"sr-only"' not in js
        assert js.count('id:"main-content"') == 1


# ── 6.2 prompt name, Options disclosure, always-rendered panel ───────


class TestPromptAndOptions:
    def test_prompt_textarea_is_named(self) -> None:
        textarea = _element_with(_js("_prompt_box"), 'id:"gen-prompt"')
        assert textarea.startswith('jsx("textarea",')
        assert '"aria-label":"Describe the playlist you want"' in textarea

    def test_options_button_exposes_expanded_and_controls(self) -> None:
        button = _element_with(_js("_prompt_box"), '"aria-controls":"gen-adv-panel"')
        assert button.startswith('jsx("button",')
        assert re.search(
            r'"aria-expanded":\(\S+show_advanced_rx_state_ \? "true" : "false"\)', button
        )
        assert '"Options"' in button

    def test_panel_is_always_rendered_and_hidden_when_collapsed(self) -> None:
        js = _js("_prompt_box")
        panel = _element_with(js, 'id:"gen-adv-panel"')
        assert panel.startswith('jsx("div",{className:"adv-panel",hidden:!(')
        assert "show_advanced_rx_state_)" in panel.split(",id:", 1)[0]
        # Not behind a conditional: the panel is a direct child of the prompt box.
        before = js[: js.index('jsx("div",{className:"adv-panel"')]
        assert not before.rstrip().endswith("?")
        assert not before.endswith("(jsx(Fragment,{},")
        assert js.count('id:"gen-adv-panel"') == 1

    def test_css_guards_hidden_panel(self) -> None:
        css = (_REPO / "assets" / "redesign.css").read_text(encoding="utf-8")
        assert re.search(r"\.adv-panel\[hidden\]\s*\{\s*display:\s*none;?\s*\}", css)


# ── 6.3 labels for sliders and genre ─────────────────────────────────


class TestAdvancedLabels:
    def test_each_label_for_matches_an_input_id(self) -> None:
        js = _js("_adv_panel")
        targets = re.findall(r'jsx\("label",\{className:"adv-label",htmlFor:"([^"]+)"\}', js)
        assert sorted(targets) == ["gen-genre", "gen-max-tracks", "gen-pool"]
        for target in targets:
            field = _element_with(js, f'id:"{target}"')
            assert field.startswith('jsx("input",'), target
            assert js.count(f'id:"{target}"') == 1, target

    def test_ranges_and_genre_carry_the_ids(self) -> None:
        js = _js("_adv_panel")
        assert 'type:"range"' in _element_with(js, 'id:"gen-max-tracks"')
        assert 'type:"range"' in _element_with(js, 'id:"gen-pool"')
        genre = _element_with(js, 'id:"gen-genre"')
        assert 'placeholder:"Any genre"' in genre
        assert "genre_filter_rx_state_" in genre

    def test_label_text_matches_visible_label(self) -> None:
        js = _js("_adv_panel")
        assert '"Tracks"' in _element_with(js, 'htmlFor:"gen-max-tracks"')
        assert '"Candidate pool \\u00d7"' in _element_with(js, 'htmlFor:"gen-pool"')
        assert '"Genre focus"' in _element_with(js, 'htmlFor:"gen-genre"')

    def test_no_div_adv_labels_remain(self) -> None:
        js = _js("_adv_panel")
        assert 'jsx(RadixThemesBox,{className:"adv-label"' not in js
        assert 'jsx("div",{className:"adv-label"' not in js


# ── 6.4 Track ordering as a radio group ──────────────────────────────


class TestTrackOrdering:
    def test_four_native_radios_with_one_name(self) -> None:
        js = _js("_adv_panel")
        group = _element_with(js, 'role:"radiogroup"')
        assert group.startswith('jsx("fieldset",')
        assert group.count('type:"radio"') == 4
        assert re.findall(r'\bname:"([^"]*)"', group) == ["gen-ordering"] * 4

    def test_group_is_labelled_track_ordering(self) -> None:
        group = _element_with(_js("_adv_panel"), 'role:"radiogroup"')
        assert '"aria-labelledby":"gen-ordering-legend"' in group
        legend = _element_with(group, 'id:"gen-ordering-legend"')
        assert legend.startswith('jsx("legend",')
        assert '"Track ordering"' in legend
        assert "adv-full" in group.split("},", 1)[0]

    def test_radios_select_shuffle_mode(self) -> None:
        group = _element_with(_js("_adv_panel"), 'role:"radiogroup"')
        for value, label in gen_page._ORDERINGS:
            assert f'shuffle_mode_rx_state_?.valueOf?.() === "{value}"' in group
            assert f'.set_shuffle_mode", ({{ ["value"] : "{value}" }})' in group
            assert f'"{label}"' in group

    def test_segment_buttons_are_gone(self) -> None:
        js = _js("_adv_panel")
        assert 'jsx("button",' not in js
        assert '"on" : ""' not in js


# ── 6.5 persistent alert region around the error ─────────────────────


class TestErrorRegion:
    def test_alert_region_wraps_the_conditional_callout(self) -> None:
        js = _js("_idle_hero")
        region = _element_with(js, 'id:"gen-error-region"')
        assert region.startswith('jsx(RadixThemesBox,{id:"gen-error-region",')
        assert 'role:"alert"' in region.split("},", 1)[0]
        # The cond (an error title is set) is the region's child, so the region persists.
        children = region.split("},", 1)[1]
        assert children.startswith("jsx(Fragment,{},(")
        assert "generation_error_rx_state_" in children
        assert 'id:"gen-error"' in children

    def test_region_is_not_conditional(self) -> None:
        js = _js("_idle_hero")
        start = js.index('jsx(RadixThemesBox,{id:"gen-error-region"')
        # The error is only read inside the region.
        assert start < js.index("generation_error_rx_state_")
        region = _element_with(js, 'id:"gen-error-region"')
        assert js.count("generation_error_rx_state_") == region.count("generation_error_rx_state_")
        # generation_message is progress only: the idle hero never shows it.
        assert "generation_message_rx_state_" not in js

    def test_callout_is_a_focus_target(self) -> None:
        callout = _element_with(_js("_idle_hero"), 'id:"gen-error"')
        assert '"callout callout-error"' in callout
        assert "tabIndex:-1" in callout
        assert "generation_error_rx_state_" in callout


# ── 6.6 progress bar and phase status ────────────────────────────────


class TestProgress:
    def test_pbar_is_a_named_progressbar(self) -> None:
        bar = _element_with(_js("_thinking_stage"), 'className:"pbar"')
        props = bar.split("},", 1)[0]
        assert 'role:"progressbar"' in props
        assert '"aria-label":"Generation progress"' in props
        assert '"aria-valuemin":"0"' in props
        assert '"aria-valuemax":"100"' in props
        assert re.search(r'"aria-valuenow":\S+generation_progress_rx_state_[,}]', props)
        assert re.search(
            r'"aria-valuetext":\(\(JSON\.stringify\(\S+generation_progress_rx_state_\)\)\+"%"\)',
            props,
        )

    def test_phase_status_is_sr_only_and_polite(self) -> None:
        js = _js("_thinking_stage")
        status = _element_with(js, 'id:"gen-phase-status"')
        props = status.split("},", 1)[0]
        assert 'className:"sr-only"' in props
        assert 'role:"status"' in props
        assert "active_phase_rx_state_" in status
        for label in ("Embed vibe", "Vector search", "Diversity", "Order set"):
            assert f'return ("{label}")' in status, label
        # The phase region and the lost-connection region (TestLostConnection).
        assert js.count('role:"status"') == 2

    def test_message_and_log_stay_out_of_live_regions(self) -> None:
        js = _js("_thinking_stage")
        status = _element_with(js, 'id:"gen-phase-status"')
        assert "generation_message_rx_state_" not in status
        assert "generation_log_rx_state_" not in status
        assert "aria-live" not in js


# ── lost connection: the thinking stage when the server cannot be reached ──

_RETRYING = "(connectErrors.length >= 2)"
_LOST = "(connectErrors.length >= 5)"
_RETRYING_ONLY = "((connectErrors.length >= 2) && (connectErrors.length < 5))"


class TestLostConnection:
    """Two steps, client-side only: a quiet line, then an alert (design D2-D5).

    Both read Reflex's ``connectErrors`` list, so nothing here needs the server.
    """

    def test_reflex_still_exposes_the_connection_state(self) -> None:
        # Reflex-internal: recheck on every Reflex upgrade (CLAUDE.md lists the others).
        from reflex.components.core.banner import has_too_many_connection_errors

        # Reflex fixes its own var at 2 failures; the constant must follow it.
        assert (
            str(has_too_many_connection_errors)
            == f"(connectErrors.length >= {gen_page._RECONNECT_AFTER})"
        )
        assert str(gen_page._CONN_RETRYING) == _RETRYING
        assert str(gen_page._CONN_LOST) == _LOST
        assert str(gen_page._CONN_RETRYING_ONLY) == _RETRYING_ONLY
        assert gen_page._ALERT_AFTER == 5
        hooks = _stage_hooks("_thinking_stage")
        assert any("useContext(EventLoopContext)" in hook for hook in hooks)

    def test_reconnect_region_is_a_persistent_polite_region(self) -> None:
        js = _js("_thinking_stage")
        region = _element_with(js, 'id:"gen-reconnect-region"')
        assert region.startswith('jsx(RadixThemesBox,{id:"gen-reconnect-region",')
        assert 'role:"status"' in region.split("},", 1)[0]
        assert js[js.index(region) - 1] == ","  # a plain child of the stage, not a cond branch
        # Only its child is conditional (the "2 to 4" step), so the region persists.
        assert region.split("},", 1)[1].startswith(f"jsx(Fragment,{{}},({_RETRYING_ONLY}?")

    def test_quiet_line_is_quiet(self) -> None:
        line = _element_with(_js("_thinking_stage"), 'id:"gen-reconnect"')
        assert 'className:"gen-reconnect"' in line
        assert json.dumps(gen_page.RECONNECTING_TEXT) in line
        assert gen_page.RECONNECTING_TEXT == "Reconnecting to PlexMix\u2026"
        assert "callout" not in line

    def test_alert_region_is_a_persistent_alert(self) -> None:
        js = _js("_thinking_stage")
        region = _element_with(js, 'id:"gen-offline-region"')
        assert region.startswith('jsx(RadixThemesBox,{id:"gen-offline-region",')
        assert 'role:"alert"' in region.split("},", 1)[0]
        assert js[js.index(region) - 1] == ","  # a plain child of the stage, not a cond branch
        # Only its child is conditional (the "5 or more" step), so the region persists.
        assert region.split("},", 1)[1].startswith(f"jsx(Fragment,{{}},({_LOST}?")

    def test_alert_wording_is_conditional(self) -> None:
        notice = _element_with(_js("_thinking_stage"), 'id:"gen-offline"')
        assert '"callout callout-error"' in notice
        assert json.dumps(gen_page.LOST_CONNECTION_TITLE) in notice
        assert json.dumps(gen_page.LOST_CONNECTION_BODY) in notice
        assert gen_page.LOST_CONNECTION_TITLE == "Lost connection to PlexMix"
        body = gen_page.LOST_CONNECTION_BODY
        for phrase in (
            "can't reach",
            "reconnects on its own",
            "may still be generating",
            "If it does not reconnect",
            "may have stopped",
        ):
            assert phrase in body, phrase
        # No claim that the playlist is lost, no jargon and no command (the page carries
        # no CLI hints; README.md names how to start PlexMix again).
        for banned in ("not kept", "websocket", "Run:", "plexmix "):
            assert banned not in body, banned
        assert "tabIndex" not in notice  # the alert is not a focus target

    def test_notices_need_no_server(self) -> None:
        js = _js("_thinking_stage")
        for marker in ('id:"gen-reconnect-region"', 'id:"gen-offline-region"'):
            notice = _element_with(js, marker)
            for needs_server in ("addEvents", "ReflexEvent", "_rx_state_"):
                assert needs_server not in notice, (marker, needs_server)
            # No button, link or other control inside either notice.
            for control in ('jsx("button",', "RadixThemesLink", "ReactRouterLink", "onClick"):
                assert control not in notice, (marker, control)

    def test_progress_and_cancel_are_offered_only_while_connected(self) -> None:
        js = _js("_thinking_stage")
        assert (
            f"({_RETRYING}?(jsx(Fragment,{{}},)):(jsx(Fragment,{{}},"
            'jsx(RadixThemesBox,{className:"gen-progress"}' in js
        )
        assert js.count("cancel_generation") == 1
        assert js.index("cancel_generation") > js.index('className:"gen-progress"')
        # Order on screen: phase track, the two regions, then (when connected) the row.
        assert (
            js.index('className:"phase-track"')
            < js.index('id:"gen-reconnect-region"')
            < js.index('id:"gen-offline-region"')
            < js.index('className:"gen-progress"')
        )

    def test_stage_is_marked_offline_only_at_the_alert_step(self) -> None:
        js = _js("_thinking_stage")
        assert f'className:({_LOST} ? "think is-offline" : "think")' in js
        assert "is-offline" not in js.replace('"think is-offline"', "")
        css = (_REPO / "assets" / "redesign.css").read_text(encoding="utf-8")
        assert ".think.is-offline .phase-node.active .phase-dot" in css
        assert ".think.is-offline .viz-phase-label" in css
        script = (_REPO / "assets" / "plexmix.js").read_text(encoding="utf-8")
        assert "closest('.is-offline')" in script

    def test_other_stages_do_not_read_the_connection(self) -> None:
        for stage in ("_idle_hero", "_results_stage"):
            assert "connectErrors" not in _js(stage), stage


# ── the prompt of a generation in flight is held by the browser ──────


class TestInFlightStorage:
    """``GeneratorState.in_flight_prompt`` rides on Reflex's session storage (design D7).

    The restore after a restart relies on how Reflex names the entry, on the order of the
    events a reconnecting tab sends, and on the browser's value reaching the var. All of it
    is Reflex-internal: recheck on every Reflex upgrade (CLAUDE.md lists it).
    """

    def test_one_session_storage_entry_named_for_the_prompt(self) -> None:
        import reflex as rx
        from reflex.compiler.utils import compile_client_storage

        entries = compile_client_storage(rx.State)["session_storage"]
        assert len(entries) == 1, entries  # nothing but the prompt is kept in the tab
        key, options = next(iter(entries.items()))
        assert options["name"] == "pm_gen_in_flight"
        assert "generator_state" in key and "in_flight_prompt" in key

    def test_browser_values_are_queued_before_the_pages_on_load(self) -> None:
        import inspect

        from reflex.compiler import templates

        source = inspect.getsource(templates.context_template)
        assert source.index("CompileVars.UPDATE_VARS_INTERNAL") < source.index(
            "CompileVars.ON_LOAD_INTERNAL"
        )
        # What the compiler writes: hydrate, then the storage values, then the page's on_load.
        context = templates.context_template(
            is_dev_mode=False,
            default_color_mode='"light"',
            initial_state={},
            state_name="state",
            client_storage={},
        )
        assert context.index("update_vars_internal") < context.index("on_load_internal")
        initial = context[context.index("export const initialEvents") :]
        assert initial.index("state.hydrate") < initial.index("...onLoadInternalEvent()")

    def test_the_browsers_value_reaches_the_var_and_the_restore_empties_it(self) -> None:
        import asyncio
        from typing import Any

        import reflex as rx
        from reflex.compiler.utils import compile_client_storage
        from reflex.istate.manager.memory import StateManagerMemory
        from reflex.state import State, UpdateVarsInternalState

        from plexmix.ui.states.generator_state import INTERRUPTED_TITLE, GeneratorState

        # The key the browser sends with the hydrate: the compiled storage map's own key.
        key = next(iter(compile_client_storage(rx.State)["session_storage"]))

        async def scenario() -> tuple[str, list[Any], GeneratorState, dict[str, Any]]:
            manager = StateManagerMemory(state=State)
            async with manager.modify_state("token-restore") as root:
                root._reset_client_storage()  # what every hydrate does first
                updater = await root.get_state(UpdateVarsInternalState)
                await updater.update_vars_internal({key: "rainy day melancholy"})
                generator = await root.get_state(GeneratorState)
                held = generator.in_flight_prompt
                events = list(generator._resume_interrupted_prompt())
                sent = {
                    name: value
                    for delta in root.get_delta().values()
                    for name, value in delta.items()
                    if name.startswith("in_flight_prompt")
                }
                return held, events, generator, sent

        held, events, generator, sent = asyncio.run(scenario())

        assert held == "rainy day melancholy"
        assert len(events) == 1  # rx.set_value for the prompt field
        assert generator.mood_query == "rainy day melancholy"
        assert generator.generation_error["title"] == INTERRUPTED_TITLE
        assert generator.in_flight_prompt == ""
        # The empty value goes back to the browser, so the entry is emptied there too.
        assert list(sent.values()) == [""]


# ── 6.7 results: cover, ordered list, named remove buttons ───────────


class TestResults:
    def test_tracks_are_an_ordered_list(self) -> None:
        js = _js("_results_stage")
        ol = _element_with(js, 'className:"tbl-wrap trk-list"')
        assert ol.startswith('jsx("ol",')
        assert 'jsx("li",{className:"trk"' in ol
        assert "generated_playlist_rx_state_" in ol

    def test_remove_button_names_its_track(self) -> None:
        button = _element_with(_js("_results_stage"), 'className:"icon-btn trm"')
        assert button.startswith('jsx("button",')
        assert '"aria-label":(("Remove "+track_rx_state_?.["title"])+" from playlist")' in button
        assert 'title:"Remove"' in button

    def test_cover_is_the_shared_decorative_swatch(self) -> None:
        js = _js("_results_stage")
        cover = _element_with(js, "result-cover")
        props = cover.split("},", 1)[0]
        assert '"aria-hidden":"true"' in props
        # Container class "result-cover cover-v<GeneratorState.cover_variant>".
        assert re.search(r'className:\("result-cover cover-v"\+\S+cover_variant_rx_state_\)', props)
        assert "linear-gradient" not in cover
        assert cover.count('jsx("span",{},)') == 4  # four quadrants
        assert js.count("result-cover") == 1

    def test_old_gradients_are_gone(self) -> None:
        source = (_REPO / "src" / "plexmix" / "ui" / "pages" / "generator.py").read_text(
            encoding="utf-8"
        )
        assert "_COVER_GRADIENTS" not in source
        assert "#e94560" not in source
        assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", source)


# ── webui-zoom-and-narrow-fit 5.2: clamped prompts keep the full text on hover ──

# The title prop is the whole mood_query var (props are sorted: title comes last).
_MOOD_TITLE = re.compile(r"title:\S+generator_state\.mood_query_rx_state_(?:,|$)")


class TestPromptTitles:
    def test_thinking_pill_title_is_the_full_prompt(self) -> None:
        h1 = _element_with(_js("_thinking_stage"), 'id:"main-heading"')
        props = h1.split("},", 1)[0]
        assert 'className:"think-query"' in props
        assert _MOOD_TITLE.search(props), props
        # The visible text still holds the whole prompt (screen readers read it all).
        assert "mood_query_rx_state_" in h1.split("},", 1)[1]

    def test_results_heading_title_is_the_full_prompt(self) -> None:
        h1 = _element_with(_js("_results_stage"), 'id:"main-heading"')
        assert h1.startswith('jsx("h1",')
        props = h1.split("},", 1)[0]
        assert "tabIndex:-1" in props
        assert _MOOD_TITLE.search(props), props
        assert "mood_query_rx_state_" in h1.split("},", 1)[1]

    def test_idle_hero_title_has_no_prompt_title(self) -> None:
        # Only the two prompt headings are clamped; the hero headline is static copy.
        hero = _element_with(_js("_idle_hero"), 'id:"main-heading"')
        assert "title:" not in hero.split("},", 1)[0]


# ── 10.7 follow-up: a fast failure still lands on the error callout ──


class TestFailureFocusFromTriggers:
    """Generate and the vibe pills chase ``#gen-error`` before ``generate_playlist``.

    A failure that arrives in the same render as the start never mounts the thinking
    stage, so no stage on_mount runs; the trigger's own forced poll covers that case.
    """

    _SCRIPT = (
        "window.pmFocus&&pmFocus.when('#gen-error',"
        "{force:true,exclude:document.getElementById('gen-error')})"
    )

    def _on_click(self, element: str) -> str:
        return element[element.index("onClick:") :]

    def test_generate_runs_the_failure_focus_script_first(self) -> None:
        # Generate is the prompt form's submit button: the chase runs first in the
        # form's on_submit chain, before submit_prompt (which returns generate_playlist).
        button = _element_with(_js("_idle_hero"), 'className:"btn btn-3 btn-primary glow"')
        assert 'type:"submit"' in button
        assert "onClick" not in button
        hook = _submit_hook("_idle_hero")
        assert self._SCRIPT in hook
        assert hook.index(self._SCRIPT) < hook.index(".submit_prompt")

    def test_submit_skips_the_chase_for_a_blank_prompt(self) -> None:
        # A blank submit starts nothing, so it must not chase (and land on) a stale callout.
        hook = _submit_hook("_idle_hero")
        guard = "var p=document.getElementById('gen-prompt');if(!p||p.value.trim())"
        assert guard + self._SCRIPT in hook
        assert gen_page._SUBMIT_FOCUS_SCRIPT.startswith("(function(){")
        assert gen_page._SUBMIT_FOCUS_SCRIPT.endswith("})()")

    def test_vibe_pills_call_use_vibe_only(self) -> None:
        # use_vibe returns the chase and generate_playlist itself, and only when
        # generation can run (a blocked pill just fills the prompt, so nothing chases).
        pill = _element_with(_js("_idle_hero"), 'className:"vibe-pill"')
        on_click = self._on_click(pill)
        assert on_click.count("ReflexEvent(") == 1
        assert '.use_vibe", ({ ["text"] : v_rx_state_ })' in on_click
        assert self._SCRIPT not in on_click
        assert ".generate_playlist" not in on_click
        assert ".prefill_prompt" not in on_click
        assert ".set_mood_query" not in on_click
        assert ".submit_prompt" not in on_click
        assert pill.endswith(",v_rx_state_)")  # the label is the same text

    def test_the_page_script_is_the_state_script(self) -> None:
        from plexmix.ui.states import generator_state as gs

        assert gen_page._FAILURE_FOCUS_SCRIPT == gs.FAILURE_FOCUS_SCRIPT == self._SCRIPT

    def test_stage_mount_hooks_are_unchanged(self) -> None:
        # The trigger script is an event, not a mount hook: one pmFocus effect per stage.
        hooks = _mount_focus_hooks("_idle_hero")
        assert len(hooks) == 1 and _FOCUS_SCRIPT in hooks[0]
        assert "useEffect" in hooks[0]


# ── input-responsiveness 5.2: prompt form, client-owned fields ───────


def _props(element: str) -> str:
    """The props object of a ``jsx(tag,{...},...)`` element (balanced, up to its children)."""
    start = element.index("{")
    depth = 0
    in_str = False
    escaped = False
    for pos in range(start, len(element)):
        ch = element[pos]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
            if depth == 0:
                return element[start : pos + 1]
    raise AssertionError("unbalanced props")


_BARE_VALUE = re.compile(r'(?<![\w"])value:')


class TestPromptForm:
    """Generate and ⌘/Ctrl+Enter submit the on-screen text through ``submit_prompt``."""

    def test_prompt_box_is_a_form_submitting_to_submit_prompt(self) -> None:
        js = _js("_prompt_box")
        assert js.startswith('jsx("form",{className:"prompt-box",onSubmit:handleSubmit_')
        hook = _submit_hook("_prompt_box")
        assert "new FormData($form)" in hook
        assert '.submit_prompt", ({ ["form_data"] : form_data })' in hook
        assert ".generate_playlist" not in hook

    def test_generate_is_the_only_submit_button_and_gated_only_by_readiness(self) -> None:
        js = _js("_prompt_box")
        assert js.count('type:"submit"') == 1
        generate = _element_with(js, 'className:"btn btn-3 btn-primary glow"')
        props = _props(generate)
        assert 'type:"submit"' in props
        # Disabled only while a blocking readiness cause stops generation.
        assert re.search(r"disabled:!\(\S+\.can_generate_rx_state_\)[,}]", props), props
        assert "mood_query" not in props and "prompt" not in props.split("disabled:", 1)[1]
        assert '"aria-describedby":"gen-ready-line"' in props
        assert '"Generate"' in generate
        # Every other button in the form is a plain button (Options, chip removal,
        # Clear all); none of them is ever disabled.
        options = _element_with(js, '"aria-controls":"gen-adv-panel"')
        assert 'type:"button"' in _props(options)
        assert js.count("disabled:") == 1
        # The prompt's emptiness never disables anything in the form.
        assert "prompt_error" not in props

    def test_prompt_is_a_client_owned_compose_textarea(self) -> None:
        textarea = _element_with(_js("_prompt_box"), 'id:"gen-prompt"')
        props = _props(textarea)
        assert textarea.startswith('jsx("textarea",')
        assert "defaultValue:" in props and "mood_query_rx_state_" in props
        assert not _BARE_VALUE.search(props), "the prompt must not be controlled"
        assert "onChange" not in props, "no per-key traffic while composing"
        assert ".set_mood_query" in props.split("onBlur:", 1)[1]
        assert "debounce" not in props
        assert 'name:"prompt"' in props
        assert "maxLength:280" in props
        assert '"data-submit-shortcut":""' in props
        assert '"data-counter":"gen-prompt-count"' in props
        # keyboard-and-semantics' name is kept; the hint describes the field.
        assert '"aria-label":"Describe the playlist you want"' in props
        assert '"aria-describedby":"gen-prompt-hint"' in props
        assert re.search(r'"aria-invalid":\(!\(\(\S+prompt_error_rx_state_', props)
        assert 'className:"prompt-input"' in props
        assert 'autoComplete:"off"' in props

    def test_counter_is_one_text_node_from_prompt_count_label(self) -> None:
        js = _js("_prompt_box")
        counter = _element_with(js, 'id:"gen-prompt-count"')
        assert re.fullmatch(
            r'jsx\("span",\{className:"charcount",id:"gen-prompt-count",'
            r"ref:ref_gen_prompt_count\},\S+\.prompt_count_label_rx_state_\)",
            counter,
        ), counter
        assert '"/280"' not in js
        assert js.count('id:"gen-prompt-count"') == 1

    def test_genre_focus_is_a_filter_input_in_the_form(self) -> None:
        js = _js("_prompt_box")
        genre = _element_with(js, 'id:"gen-genre"')
        props = _props(genre)
        assert genre.startswith('jsx("input",')
        assert "defaultValue:" in props and "genre_filter_rx_state_" in props
        assert not _BARE_VALUE.search(props)
        assert 'name:"genre"' in props
        on_change = props.split("onChange:", 1)[1]
        assert ".set_genre_filter" in on_change
        assert '({ ["debounce"] : 300 })' in on_change
        on_blur = props.split("onBlur:", 1)[1].split("onChange:", 1)[0]
        assert ".set_genre_filter" in on_blur and "debounce" not in on_blur
        assert '"data-enter":"none"' in props
        assert 'placeholder:"Any genre"' in props

    def test_enter_in_advanced_controls_does_not_submit(self) -> None:
        # Browsers submit a form on Enter in a range or radio too: only Generate and
        # ⌘/Ctrl+Enter may start a generation.
        js = _js("_prompt_box")
        for target in ("gen-max-tracks", "gen-pool", "gen-genre"):
            assert '"data-enter":"none"' in _props(_element_with(js, f'id:"{target}"')), target
        group = _element_with(js, 'role:"radiogroup"')
        assert '"data-enter":"none"' in _props(group)
        # The textarea keeps plain Enter (a new line).
        assert "data-enter" not in _props(_element_with(js, 'id:"gen-prompt"'))

    def test_hint_region_is_persistent_and_shows_prompt_error(self) -> None:
        js = _js("_idle_hero")
        region = _element_with(js, 'id:"gen-prompt-hint"')
        props = _props(region)
        assert region.startswith("jsx(RadixThemesBox,")
        assert 'role:"alert"' in props
        assert 'className:"prompt-hint"' in props
        children = region[len("jsx(RadixThemesBox,") + len(props) + 1 :]
        assert children.startswith("jsx(Fragment,{},(")
        assert "prompt_error_rx_state_" in children
        hint = _element_with(region, 'className:"field-hint"')
        assert hint.startswith('jsx("p",{className:"field-hint"}')
        assert "prompt_error_rx_state_" in hint
        # The region is not itself conditional: prompt_error only appears inside it
        # (and in the textarea's aria-invalid).
        start = js.index('jsx(RadixThemesBox,{className:"prompt-hint"')
        before = js[:start]
        assert not before.rstrip().endswith("?")
        assert js.count('id:"gen-prompt-hint"') == 1

    def test_hint_sits_between_the_prompt_box_and_the_error_region(self) -> None:
        js = _js("_idle_hero")
        form_at = js.index('jsx("form",{className:"prompt-box"')
        form_end = form_at + len(_element(js, form_at))
        hint_at = js.index('jsx(RadixThemesBox,{className:"prompt-hint"')
        assert form_end <= hint_at < js.index('id:"gen-error-region"')

    def test_page_keeps_one_form_and_no_controlled_text_fields(self) -> None:
        js = _js("_idle_hero")
        assert js.count('jsx("form",') == 1
        for tag in ('jsx("textarea",', 'jsx("input",'):
            pos = 0
            while (pos := js.find(tag, pos)) != -1:
                element = _element(js, pos)
                props = _props(element)
                if 'type:"range"' not in props and 'type:"radio"' not in props:
                    assert not _BARE_VALUE.search(props), element[:120]
                pos += len(tag)

    def test_field_hint_css_uses_error_fg_at_13px(self) -> None:
        css = (_REPO / "assets" / "redesign.css").read_text(encoding="utf-8")
        start = css.index("/* ── change 3: generator (webui-input-responsiveness) ── */")
        end = css.index("/* ── end change 3: generator ── */", start)
        section = css[start:end]
        rule = re.search(r"\.field-hint\s*\{([^}]*)\}", section)
        assert rule, "no .field-hint rule in the change-3 generator section"
        assert "color: var(--error-fg)" in rule.group(1)
        assert "font-size: 13px" in rule.group(1)
        assert not re.search(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(", section), "tokens only"


# ── honest-status 7.4: readiness line, constraint chips, failure callout ──

_GEN_CSS_START = "/* ── change 4: generator (webui-honest-status-and-first-run) ── */"
_GEN_CSS_END = "/* ── end change 4: generator ── */"


def _gen_css() -> str:
    css = (_REPO / "assets" / "redesign.css").read_text(encoding="utf-8")
    start = css.index(_GEN_CSS_START)
    return css[start : css.index(_GEN_CSS_END, start)]


class TestReadyLine:
    def test_line_sits_under_the_prompt_foot_inside_the_box(self) -> None:
        js = _js("_prompt_box")
        foot_at = js.index('className:"prompt-foot"')
        foot = _element_with(js, 'className:"prompt-foot"')
        line_at = js.index('jsx(RadixThemesBox,{className:"ready-line"')
        panel_at = js.index('jsx("div",{className:"adv-panel"')
        assert foot_at < line_at < panel_at
        assert 'className:"ready-line"' not in foot
        assert js.count('id:"gen-ready-line"') == 1

    def test_line_is_a_persistent_status_region(self) -> None:
        js = _js("_prompt_box")
        line = _element_with(js, 'id:"gen-ready-line"')
        props = _props(line)
        assert 'role:"status"' in props
        assert 'className:"ready-line"' in props
        # The region itself is unconditional; its content is a cond on "loaded".
        before = js[: js.index('jsx(RadixThemesBox,{className:"ready-line"')]
        assert not before.rstrip().endswith("?")
        children = line[len("jsx(RadixThemesBox,") + len(props) + 1 :]
        assert children.startswith("jsx(Fragment,{},((")
        assert '["loaded"]?.valueOf?.() === "true"' in children

    def test_facts_row_shows_count_provider_mono_model_state_and_privacy(self) -> None:
        line = _element_with(_js("_prompt_box"), 'id:"gen-ready-line"')
        facts = _element_with(line, 'className:"rl-facts"')
        assert facts.startswith('jsx("p",')
        for key in ("tracks", "provider", "model_dim", "state", "privacy"):
            assert f'gen_status_rx_state_?.["{key}"]' in facts, key
        assert re.search(r'jsx\("span",\{className:"mono"\},\S+\["tracks"\]\)', facts)
        assert re.search(r'jsx\("span",\{className:"mono"\},\S+\["model_dim"\]\)', facts)
        assert '("dot dot-"+' in facts and '?.["tone"])' in facts
        assert '" \\u00b7 "' in facts

    def test_issue_row_shows_title_body_and_actions(self) -> None:
        line = _element_with(_js("_prompt_box"), 'id:"gen-ready-line"')
        assert '?.["issue"]?.valueOf?.() === "true"' in line
        issue = _element_with(line, 'className:"rl-issue"')
        for key in ("title", "body", "action_label", "action_href"):
            assert f'gen_status_rx_state_?.["{key}"]' in issue, key
        assert 'gen_status_rx_state_?.["secondary_href"]' in issue
        # Internal navigation through the router link, styled as the line's action.
        assert issue.count("RadixThemesLink") == 2
        assert issue.count('className:"rl-action"') == 2
        assert "ReactRouterLink" in issue

    def test_loading_shows_a_skeleton_not_a_fabricated_zero(self) -> None:
        line = _element_with(_js("_prompt_box"), 'id:"gen-ready-line"')
        assert 'className:"skeleton rl-skeleton"' in line
        assert '"Checking your library\\u2026"' in line
        assert '"0 tracks"' not in line

    def test_no_cli_hints_or_api_key_advice_in_the_page(self) -> None:
        js = rendered(gen_page.generator())
        for banned in ("Check your API keys", "plexmix config", "Run:", "pip install"):
            assert banned not in js, banned


class TestConstraintChipsRender:
    def test_chip_row_is_outside_the_options_panel(self) -> None:
        js = _js("_prompt_box")
        panel = _element_with(js, 'id:"gen-adv-panel"')
        assert "chip-x" not in panel
        row_at = js.index('className:"chip-x-row"')
        assert js.index('className:"prompt-foot"') < row_at < js.index('id:"gen-ready-line"')

    def test_row_renders_only_with_constraints(self) -> None:
        js = _js("_prompt_box")
        cond_at = js.index("active_constraints_rx_state_.length > 0)?")
        assert cond_at < js.index('className:"chip-x-row"')

    def test_chips_are_a_keyed_list_with_named_remove_buttons(self) -> None:
        js = _js("_prompt_box")
        lst = _element_with(js, 'className:"chip-x-list"')
        assert lst.startswith('jsx("ul",')
        assert 'role:"list"' in _props(lst)
        assert '"aria-labelledby":"gen-filters-label"' in _props(lst)
        chip = _element_with(lst, 'className:"chip-x"')
        assert chip.startswith('jsx("li",')
        assert 'key:chip_rx_state_?.["key"]' in _props(chip)
        assert re.search(
            r'jsx\("span",\{className:"chip-x-value mono"\},chip_rx_state_\?\.\["value"\]\)', chip
        )
        button = _element_with(chip, 'className:"chip-x-btn"')
        props = _props(button)
        assert button.startswith('jsx("button",')
        assert 'type:"button"' in props
        assert '"aria-label":("Remove filter: "+chip_rx_state_?.["label"])' in props
        assert '.remove_constraint", ({ ["key"] : chip_rx_state_?.["key"] })' in props
        # Focus moves to the next chip, or the prompt once none is left.
        assert 'pmFocus.when([\\".chip-x-btn\\", \\"#gen-prompt\\"])' in props
        assert props.index(".remove_constraint") < props.index("pmFocus.when")

    def test_clear_all_resets_every_chip(self) -> None:
        js = _js("_prompt_box")
        clear = _element_with(js, 'className:"btn btn-sm btn-ghost chip-x-clear"')
        props = _props(clear)
        assert clear.startswith('jsx("button",')
        assert 'type:"button"' in props
        assert '"aria-label":"Clear all filters"' in props
        assert ".clear_constraints" in props
        assert '"Clear all"' in clear

    def test_chip_controls_never_echo_into_client_owned_fields(self) -> None:
        # None of the chip filters has a text field: no set_value on removal.
        row = _element_with(_js("_prompt_box"), 'className:"chip-x-row"')
        assert "_set_value" not in row


class TestFailureCallout:
    def test_callout_shows_title_body_actions_and_dismiss(self) -> None:
        callout = _element_with(_js("_idle_hero"), 'id:"gen-error"')
        for key in ("title", "body", "action_label", "action_href"):
            assert f'generation_error_rx_state_?.["{key}"]' in callout, key
        assert 'generation_error_rx_state_?.["secondary_href"]' in callout
        assert re.search(r'className:"c-title"\},\S+\["title"\]\)', callout)
        assert re.search(r'className:"c-body"\},\S+\["body"\]\)', callout)
        assert callout.count('className:"c-action"') == 2
        dismiss = _element_with(callout, 'className:"icon-btn c-dismiss"')
        props = _props(dismiss)
        assert dismiss.startswith('jsx("button",')
        assert '"aria-label":"Dismiss error"' in props and 'type:"button"' in props
        assert ".dismiss_error" in props
        assert 'pmFocus.when([\\"#gen-prompt\\"])' in props

    def test_warning_tone_for_results_that_are_not_failures(self) -> None:
        callout = _element_with(_js("_idle_hero"), 'id:"gen-error"')
        assert '["tone"]?.valueOf?.() === "warning"' in callout
        assert '"callout callout-warning"' in callout


class TestGeneratorCss:
    def test_section_exists_after_earlier_changes(self) -> None:
        css = (_REPO / "assets" / "redesign.css").read_text(encoding="utf-8")
        assert css.count(_GEN_CSS_START) == 1
        assert css.count(_GEN_CSS_END) == 1

    def test_tokens_only(self) -> None:
        section = _gen_css().replace("#gen-error-region", "")
        assert not re.search(r"#[0-9A-Fa-f]{3,8}\b", section), "tokens only"
        assert "rgb" not in section and "hsl(" not in section

    def test_intrinsic_layout(self) -> None:
        section = _gen_css()
        assert re.search(r"\.chip-x-row\s*\{[^}]*flex-wrap:\s*wrap", section)
        assert re.search(r"\.chip-x-list\s*\{[^}]*flex-wrap:\s*wrap", section)
        assert "grid-template-columns" not in section
        # The only fixed widths are the icon buttons' hit areas.
        widths = re.findall(r"(?<![-\w])width:\s*(\d+)px", section)
        assert sorted(set(widths)) == ["24", "28"], widths

    def test_new_controls_have_a_visible_focus_ring(self) -> None:
        section = _gen_css()
        assert re.search(
            r"\.chip-x-btn:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--focus-ring\)",
            section,
        )
