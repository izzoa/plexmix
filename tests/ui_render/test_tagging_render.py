"""Render smoke: Tagging inline editor, Edit button, filter names, status and progress."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import reflex as rx
from reflex.components.component import Component
from reflex.components.core.cond import Cond
from reflex.vars.object import ObjectVar

from plexmix.ui.pages.tagging import _filter_bar, _tag_editor, _tagged_row, tagging
from tests.ui_render.conftest import rendered


def _track() -> ObjectVar[dict[str, str]]:
    """A loop-item Var shaped like a ``recently_tagged_tracks`` row."""
    return rx.Var(_js_expr="track", _var_type=dict[str, str]).to(ObjectVar, dict[str, str])


def _walk(comp: Any, ancestors: tuple[Any, ...] = ()) -> Iterator[tuple[Any, tuple[Any, ...]]]:
    yield comp, ancestors
    for child in getattr(comp, "children", None) or []:
        if isinstance(child, Component):
            yield from _walk(child, (*ancestors, comp))


def _find_by_id(root: Component, element_id: str) -> list[tuple[Any, tuple[Any, ...]]]:
    """Components whose static id is ``element_id``, with their ancestors."""
    return [(c, a) for c, a in _walk(root) if _static_id(c) == element_id]


def _static_id(comp: Any) -> str | None:
    value = getattr(comp, "id", None)
    return value if isinstance(value, str) else None


def _inside_main(ancestors: tuple[Any, ...]) -> tuple[Any, ...]:
    """Ancestors below ``<main id="main-content">`` (layout's auth ``rx.cond`` wraps the page)."""
    ids = [_static_id(a) for a in ancestors]
    assert "main-content" in ids
    return ancestors[ids.index("main-content") :]


class TestInlineEditor:
    def test_form_submits_and_escape_cancels(self) -> None:
        comp = _tag_editor(_track())
        js = rendered(comp)
        assert js.startswith('jsx("form",')
        assert '"data-pm-escape-cancel":""' in js
        assert "onSubmit:handleSubmit_" in js
        hooks = "".join(comp._get_all_hooks())
        assert "submit_tag_edit" in hooks and "form_data" in hooks

    def test_fieldset_legend_names_the_track(self) -> None:
        js = rendered(_tag_editor(_track()))
        assert 'jsx("fieldset",{className:"tag-edit-fieldset"}' in js
        legend = js[js.index('jsx("legend",') :]
        legend = legend[: legend.index('jsx(RadixThemesBox,{className:"tag-edit-fields"}')]
        assert '"Edit tags for "' in legend
        assert 'track?.["title"]' in legend
        assert '" \\u2014 "' in legend  # " — "
        assert 'track?.["artist"]' in legend

    def test_three_visible_labels_match_named_inputs(self) -> None:
        js = rendered(_tag_editor(_track()))
        labels = re.findall(
            r'jsx\("label",\{className:"field-label",htmlFor:"([^"]+)"\},"([^"]+)"\)', js
        )
        assert labels == [
            ("tag-edit-moods", "Moods"),
            ("tag-edit-envs", "Environments"),
            ("tag-edit-instr", "Instruments"),
        ]
        for field_id, name in (
            ("tag-edit-moods", "tags"),
            ("tag-edit-envs", "environments"),
            ("tag-edit-instr", "instruments"),
        ):
            assert re.search(rf'id:"{field_id}",name:"{name}"', js), field_id

    def test_moods_autofocuses_and_help_describes_every_field(self) -> None:
        js = rendered(_tag_editor(_track()))
        moods = js[js.index('htmlFor:"tag-edit-moods"') : js.index('htmlFor:"tag-edit-envs"')]
        assert "autoFocus:true" in moods
        assert js.count("autoFocus:true") == 1
        assert js.count('"aria-describedby":"tag-edit-help"') == 3
        assert 'jsx("span",{className:"field-help",id:"tag-edit-help"' in js
        assert '"Comma-separated"' in js

    def test_save_submits_and_cancel_is_marked(self) -> None:
        js = rendered(_tag_editor(_track()))
        assert re.search(
            r'jsx\("button",\{className:"btn btn-sm btn-primary",type:"submit"\},"Save"\)', js
        )
        cancel = js[js.index('"Cancel")') - 900 : js.index('"Cancel")')]
        assert '"data-pm-cancel":""' in cancel
        assert 'type:"button"' in cancel
        assert "cancel_edit" in cancel

    def test_no_placeholder_only_names(self) -> None:
        # Visible labels name the fields; placeholders are no longer the only cue.
        assert "placeholder" not in rendered(_tag_editor(_track()))


class TestReadOnlyRow:
    def test_edit_button_id_and_name(self) -> None:
        js = rendered(_tagged_row(_track()))
        assert 'id:("tag-edit-btn-"+track?.["id"])' in js
        assert '"aria-label":("Edit tags for "+track?.["title"])' in js
        # Both branches present: editor while editing, read-only row otherwise.
        assert 'jsx("form",' in js

    def test_row_is_keyed_by_track_id(self) -> None:
        # Not the foreach index: focus sent to a row's Edit button after a save
        # must stay on that track when rows are dropped or reordered.
        js = rendered(_tagged_row(_track()))
        assert js.startswith("jsx(RadixThemesBox,{")
        # Root props are sorted: css (the row style) and then the key.
        assert '}),key:track?.["id"]},' in js[:200]

    def test_edit_button_opens_the_editor_with_row_values(self) -> None:
        js = rendered(_tagged_row(_track()))
        start = js.index('id:("tag-edit-btn-"')  # sorted props: id, then onClick
        button = js[start : js.index('"Edit")', start)]
        assert "start_edit_tag" in button
        for key, value in (
            ("track_id", "id"),
            ("tags", "tags"),
            ("environments", "environments"),
            ("instruments", "instruments"),
        ):
            assert f'["{key}"] : track?.["{value}"]' in button, key

    def test_row_wraps_with_classes_not_inline_layout(self) -> None:
        # webui-zoom-and-narrow-fit 7.3: .tag-row > .tag-row-title, tags, Edit.
        js = rendered(_tagged_row(_track()))
        row = js.index('jsx(RadixThemesBox,{className:"tag-row"},')
        title = js.index('jsx(RadixThemesBox,{className:"tag-row-title"},', row)
        tags = js.index('jsx("span",{className:"tag-row-tags fg2"},track?.["tags_display"])', title)
        assert js.index('id:("tag-edit-btn-"', tags) > tags
        # The old inline flex layout is gone from the row and its title column.
        assert '["display"] : "flex"' not in js[row:tags]
        assert '["flex"] : "1"' not in js[row:tags]

    def test_row_shows_the_spaced_tags_and_edit_keeps_the_stored_value(self) -> None:
        js = rendered(_tagged_row(_track()))
        read_only = js[js.index('className:"tag-row"') :]
        # The visible list is the display string, never the raw comma-joined value...
        assert 'track?.["tags_display"]' in read_only
        assert ',track?.["tags"])' not in read_only
        # ...while Edit still prefills the editor with the raw stored tags.
        assert '["tags"] : track?.["tags"]' in read_only


class TestFiltersStatusProgress:
    def test_filter_inputs_are_named(self) -> None:
        js = rendered(tagging())
        for name in ("Filter by artist", "Filter by genre", "Stale after days"):
            assert f'"aria-label":"{name}"' in js, name

    def test_preview_count_is_a_persistent_polite_region(self) -> None:
        page = tagging()
        js = rendered(page)
        found = _find_by_id(page, "tag-preview-status")
        assert len(found) == 1
        _, ancestors = found[0]
        # Mounted before any result: no rx.cond between <main> and the region.
        assert not any(isinstance(a, Cond) for a in _inside_main(ancestors))
        # Props are sorted: aria-atomic, aria-live, css, id, ref, role.
        end = js.index('id:"tag-preview-status"')
        region = js[js.rindex('"aria-atomic":"true"', 0, end) : end + 200]
        assert '"aria-live":"polite"' in region
        assert 'id:"tag-preview-status",ref:ref_tag_preview_status,role:"status"' in region
        # The page's only status region. The shell's command palette has its own
        # (#cmdk-status), which is not part of this page.
        status_ids = re.findall(r'id:"([^"]+)",ref:ref_\w+,role:"status"', js)
        assert js.count('role:"status"') == len(status_ids)
        assert [i for i in status_ids if i != "cmdk-status"] == ["tag-preview-status"]

    def test_tagging_progressbar(self) -> None:
        js = rendered(tagging())
        assert js.count('role:"progressbar"') == 1
        bar = js[js.index('"aria-label":"Tagging progress"') : js.index('role:"progressbar"')]
        assert '"aria-valuemin":"0"' in bar
        assert '"aria-valuemax":"100"' in bar
        assert re.search(r'"aria-valuenow":[^,]*tagging_progress_rx_state_', bar)
        assert re.search(
            r'"aria-valuetext":\("Batch "\+[^+]*current_batch_rx_state_\+" of "\+'
            r"[^)]*total_batches_rx_state_\)",
            bar,
        )
        assert 'className:"pbar"' in bar


# ── Client-owned text fields (webui-input-responsiveness 7.2) ──────────────────

_DEBOUNCE_300 = '({ ["debounce"] : 300 })'


def _input_props(js: str) -> dict[str, str]:
    """Props text of each ``jsx("input",{...},)`` keyed by its static id (inputs have no children)."""
    found: dict[str, str] = {}
    for chunk in js.split('jsx("input",{')[1:]:
        props = chunk[: chunk.index("},)")]
        match = re.search(r'(?<![\w"])id:"([^"]+)"', props)
        if match:
            found[match.group(1)] = props
    return found


def _has_value_prop(props: str) -> bool:
    """True when the props set a controlled ``value:`` (not ``defaultValue`` or an event arg)."""
    return re.search(r'(?<![\w"])value:', props) is not None


class TestFilterBarForm:
    def test_filter_bar_is_a_form_that_submits_on_enter(self) -> None:
        comp = _filter_bar()
        js = rendered(comp)
        assert js.startswith(
            'jsx("form",{className:"filterbar","data-enter-submit":"",noValidate:true,'
            "onSubmit:handleSubmit_"
        )
        hooks = "".join(comp._get_all_hooks())
        assert "new FormData($form)" in hooks
        assert ".apply_filter_form" in hooks and '["form_data"] : form_data' in hooks

    def test_preview_is_the_submit_button(self) -> None:
        js = rendered(_filter_bar())
        assert (
            'jsx("button",{className:"btn btn-3 btn-soft",type:"submit"},'
            'jsx(LucideScanSearch,{size:15},),"Preview")'
        ) in js
        # Preview runs through the form (apply_filter_form -> preview_selection).
        assert "preview_selection" not in js
        assert js.count('type:"submit"') == 1

    def test_text_filters_are_client_owned_and_debounced(self) -> None:
        inputs = _input_props(rendered(_filter_bar()))
        expected = {
            "tag-filter-artist": ("artist", "artist_filter", "set_artist_filter"),
            "tag-filter-genre": ("genre", "genre_filter", "set_genre_filter"),
            "tag-filter-stale-days": ("stale_days", "stale_days", "set_stale_days"),
        }
        assert set(expected) <= set(inputs)
        for field_id, (name, var, setter) in expected.items():
            props = inputs[field_id]
            assert not _has_value_prop(props), field_id
            assert re.search(rf"defaultValue:[\w.]*\.{var}_rx_state_", props), field_id
            assert f'name:"{name}"' in props, field_id
            on_change = props[props.index("onChange:") :]
            on_change = on_change[: on_change.index("placeholder:")]
            assert f'.{setter}"' in on_change and _DEBOUNCE_300 in on_change, field_id
            on_blur = props[props.index("onBlur:") : props.index("onChange:")]
            assert f'.{setter}"' in on_blur and "debounce" not in on_blur, field_id
            assert 'autoComplete:"off"' in props and "spellCheck:false" in props, field_id
            ref = "ref_" + field_id.replace("-", "_")
            assert f"ref:{ref}" in props, field_id

    def test_filter_labels_and_placeholders_are_kept(self) -> None:
        inputs = _input_props(rendered(_filter_bar()))
        for field_id, label, placeholder in (
            ("tag-filter-artist", "Filter by artist", "Filter by artist\\u2026"),
            ("tag-filter-genre", "Filter by genre", "Genre"),
            ("tag-filter-stale-days", "Stale after days", "Stale days"),
        ):
            assert f'"aria-label":"{label}"' in inputs[field_id], field_id
            assert f'placeholder:"{placeholder}"' in inputs[field_id], field_id
        for field_id in ("tag-filter-genre", "tag-filter-stale-days"):
            assert 'className:"minisel"' in inputs[field_id], field_id

    def test_stale_days_is_a_whole_number_field_sending_the_raw_text(self) -> None:
        props = _input_props(rendered(_filter_bar()))["tag-filter-stale-days"]
        assert 'type:"number"' in props
        assert 'min:"0"' in props
        assert 'step:"1"' in props
        # Not Reflex's ValueNumberInput: "" must reach the setter as "", not 0.
        assert "Number(" not in props
        assert '({ ["value"] : _e?.["target"]?.["value"] })' in props
        # assets/plexmix.js lets only digits be typed or pasted (data-digits).
        assert '"data-digits":""' in props

    def test_untagged_checkbox_stays_controlled(self) -> None:
        js = rendered(_filter_bar())
        checkbox = js[js.index('jsx("input",{checked:') :]
        checkbox = checkbox[: checkbox.index("},)")]
        assert "has_no_tags_rx_state_" in checkbox
        assert "toggle_has_no_tags" in checkbox
        assert 'type:"checkbox"' in checkbox

    def test_no_text_field_on_the_filter_bar_is_controlled(self) -> None:
        js = rendered(_filter_bar())
        assert not _has_value_prop(js)
        assert js.count("defaultValue:") == 3


class TestEditorFieldsClientOwned:
    def test_editor_fields_commit_on_blur_only(self) -> None:
        inputs = _input_props(rendered(_tag_editor(_track())))
        for field_id, name, var, setter in (
            ("tag-edit-moods", "tags", "edit_tags", "set_edit_tags"),
            ("tag-edit-envs", "environments", "edit_environments", "set_edit_environments"),
            ("tag-edit-instr", "instruments", "edit_instruments", "set_edit_instruments"),
        ):
            props = inputs[field_id]
            assert not _has_value_prop(props), field_id
            assert "onChange" not in props, field_id
            assert re.search(rf"defaultValue:[\w.]*\.{var}_rx_state_", props), field_id
            assert f'.{setter}"' in props[props.index("onBlur:") :], field_id
            assert f'name:"{name}"' in props, field_id
            assert '"aria-describedby":"tag-edit-help"' in props, field_id
            assert 'className:"input"' in props, field_id
            assert 'autoComplete:"off"' in props, field_id

    def test_editor_form_reads_the_named_fields(self) -> None:
        comp = _tag_editor(_track())
        hooks = "".join(comp._get_all_hooks())
        assert "new FormData($form)" in hooks
        assert ".submit_tag_edit" in hooks


# ── Tagging scope (webui-honest-status-and-first-run 8.3) ──────────────────────


def _region(page: Component, element_id: str) -> tuple[Any, tuple[Any, ...]]:
    found = _find_by_id(page, element_id)
    assert len(found) == 1, element_id
    return found[0]


class TestCountCard:
    def test_shows_a_count_only_for_a_current_preview(self) -> None:
        from plexmix.ui.pages.tagging import _count_card

        js = rendered(_count_card())
        # The raw count var is never printed; only the current branch shows the label.
        assert "preview_count_rx_state_" not in js
        switch = js[js.index("switch (JSON.stringify(") :]
        assert "preview_state_rx_state_" in switch[:300]
        current = switch[switch.index('case JSON.stringify("current"):') :]
        current = current[: current.index("break;")]
        assert "preview_count_label_rx_state_" in current
        assert "preview_match_text_rx_state_" in current
        assert 'className:"mono tag-count-num"' in current
        assert "preview_tagged_note_rx_state_" in current
        for state, text in (
            ("running", "Counting matching tracks\\u2026"),
            ("empty", "No tracks match the current filters"),
            ("stale", "Filters changed \\u2014 preview again"),
        ):
            branch = switch[switch.index(f'case JSON.stringify("{state}"):') :]
            assert f'"{text}"' in branch[: branch.index("break;")], state
        default = switch[switch.index("default:") :]
        assert '"Preview to count matching tracks"' in default[: default.index("break;")]
        # No static "0 tracks match" or "N tracks match" text outside the current branch.
        assert '" tracks match the current filter"' not in js

    def test_count_lives_in_the_persistent_status_region(self) -> None:
        page = tagging()
        comp, ancestors = _region(page, "tag-preview-status")
        assert not any(isinstance(a, Cond) for a in _inside_main(ancestors))
        js = rendered(comp)
        assert 'role:"status"' in js and '"aria-live":"polite"' in js
        assert "preview_state_rx_state_" in js

    def test_tag_tracks_opens_the_confirmation_and_is_disabled_with_a_reason(self) -> None:
        from plexmix.ui.pages.tagging import _count_card

        js = rendered(_count_card())
        start = js.index('jsx("button",{"aria-busy":')
        button = js[start : js.index('"Tag tracks")', start)]
        assert ".request_tagging" in button
        assert ".start_tagging" not in button  # nothing starts without the confirmation
        assert re.search(r"disabled:!\([\w.]*can_start_tagging_rx_state_\)", button)
        assert re.search(r'"aria-busy":[\w.]*tag_confirm_checking_rx_state_', button)
        assert '"aria-describedby":"tag-start-reason"' in button
        assert 'id:"tag-start"' in button
        assert 'className:"btn btn-3 btn-primary glow"' in button
        reason = js[js.index('jsx("span",{className:"tag-reason",id:"tag-start-reason"') :]
        assert "tag_block_reason_rx_state_" in reason[:400]


class TestNoticeRegion:
    def test_is_a_persistent_alert_outside_the_job_card(self) -> None:
        page = tagging()
        comp, ancestors = _region(page, "tag-notice-region")
        # Mounted before any message, and never inside rx.cond(is_tagging).
        inside = _inside_main(ancestors)
        assert not any(isinstance(a, Cond) for a in inside)
        js = rendered(comp)
        assert js.startswith(
            'jsx(RadixThemesBox,{id:"tag-notice-region",ref:ref_tag_notice_region,role:"alert"}'
        )
        assert "is_tagging_rx_state_" not in js

    def test_renders_title_text_action_and_dismiss(self) -> None:
        from plexmix.ui.pages.tagging import _notice_region

        js = rendered(_notice_region())
        assert 'tagging_notice_rx_state_?.["text"]' in js
        assert 'tagging_notice_rx_state_?.["title"]' in js
        assert '"callout callout-error tag-notice"' in js
        assert '"callout callout-warning tag-notice"' in js
        link = js[js.index('jsx(ReactRouterLink,{className:"tag-notice-action"') :]
        assert "to:reflex___state" in link[:300] and '["action_href"]' in link[:300]
        assert '["action_label"]' in link[:500]
        dismiss = js[js.index('"aria-label":"Dismiss message"') :]
        assert ".dismiss_notice" in dismiss[:400]
        assert 'type:"button"' in dismiss[:600]

    def test_page_has_one_alert_region_of_its_own(self) -> None:
        js = rendered(tagging())
        alerts = re.findall(r'id:"([^"]+)",ref:ref_\w+,role:"alert"', js)
        assert [i for i in alerts if i != "login-error"] == ["tag-notice-region"]


class TestTagConfirmDialog:
    def test_alert_dialog_with_safe_cancel_and_plain_confirm_buttons(self) -> None:
        from plexmix.ui.pages.tagging import _tag_confirm_dialog

        js = rendered(_tag_confirm_dialog())
        assert js.startswith("jsx(RadixThemesAlertDialog.Root,")
        assert ".set_tag_confirm_open" in js[:400]
        assert "tag_confirm_open_rx_state_" in js[:800]
        assert 'id:"tag-confirm"' in js and '"aria-modal":"true"' in js
        assert "window.pmFocus&&pmFocus.restore()" in js
        assert js.count("RadixThemesAlertDialog.Cancel") == 1
        assert "RadixThemesAlertDialog.Action" not in js
        cancel = js[js.index("RadixThemesAlertDialog.Cancel") :]
        assert 'id:"tag-confirm-cancel"' in cancel[:400]
        confirm = js[js.index('id:"tag-confirm-confirm"') - 400 :]
        assert ".confirm_tagging" in confirm[: confirm.index('id:"tag-confirm-confirm"') + 600]
        secondary = js[js.index('id:"tag-confirm-secondary"') - 400 :]
        assert ".tag_all_untagged" in secondary[:800]

    def test_scope_lines_and_danger_class(self) -> None:
        from plexmix.ui.pages.tagging import _tag_confirm_dialog

        js = rendered(_tag_confirm_dialog())
        for key in (
            "title",
            "count_line",
            "replace_line",
            "provider_label",
            "model",
            "duration_line",
            "privacy_line",
            "confirm_label",
            "secondary_label",
        ):
            assert f'tag_scope_rx_state_?.["{key}"]' in js, key
        assert '"btn btn-3 btn-danger"' in js and '"btn btn-3 btn-primary"' in js
        assert re.search(r'tag_scope_rx_state_\?\.\["danger"\]\?\.valueOf\?\.\(\) === "true"', js)

    def test_rendered_once_on_the_page(self) -> None:
        js = rendered(tagging())
        assert js.count('id:"tag-confirm"') == 1


class TestRecentlyTaggedLoading:
    def test_skeleton_until_loaded_then_rows_empty_state_or_error(self) -> None:
        from plexmix.ui.pages.tagging import _recently_tagged

        js = rendered(_recently_tagged())
        assert '"Recently tagged"' in js  # the heading shows in every state
        loaded = js.index("recent_loaded_rx_state_?")
        assert loaded < js.index('className:"skeleton tag-skel-title"')
        skeleton = js[js.rindex('"aria-busy":"true"') :]
        assert '"Loading recently tagged tracks\\u2026"' in skeleton[:300]
        assert js.count('"aria-hidden":"true",className:"skeleton') == 6
        assert '"No tagged tracks yet"' in js and 'className:"empty"' in js
        assert "recent_error_rx_state_" in js and ".retry_recently_tagged" in js
        assert "recently_tagged_tracks_rx_state_.length > 0" in js

    def test_rows_keep_their_keys_and_edit_buttons(self) -> None:
        js = rendered(tagging())
        assert 'key:track_rx_state_?.["id"]' in js
        assert '("tag-edit-btn-"+track_rx_state_?.["id"])' in js
