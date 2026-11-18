"""Render smoke: Doctor headings, check lists, live result region, fixes and progress.

webui-keyboard-and-semantics gave Doctor its structure (one h2 verdict, check
rows as list items headed by h3, fix buttons named "<action> – <row>", a
persistent polite status region, a named progressbar). webui-honest-status-and-
first-run (group 6) keeps it and changes the content: the verdict and every row
state come from ``DoctorState.verdict``/``row_map`` (never a literal "Healthy"),
a Connections list joins the library checks, fixes go through ``start_fix`` and a
scope confirmation, and an unknown total shows an indeterminate ``.loadbar``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from reflex.components.component import Component
from reflex.components.core.cond import Cond

from plexmix.ui.pages.doctor import _fix_progress_bar, doctor
from tests.ui_render.conftest import rendered

# (row id, h3 name) for the library checks, in page order.
_LIBRARY_CHECKS = (
    ("library", "Library"),
    ("embeddings", "Embeddings"),
    ("orphans", "Orphaned embeddings"),
    ("tags", "Tags"),
    ("audio", "Audio features"),
    ("musicbrainz", "MusicBrainz genres"),
)
_CONNECTIONS = (
    ("conn_plex", "Plex"),
    ("conn_ai", "AI provider"),
    ("conn_embeddings", "Embedding provider"),
)
_FIX_HANDLERS = (
    "generate_missing_embeddings",
    "regenerate_all_embeddings",
    "delete_orphaned_embeddings",
    "regenerate_missing_tags",
    "regenerate_all_tags",
    "analyze_missing_audio",
    "enrich_missing_musicbrainz",
)


def _walk(comp: Any, ancestors: tuple[Any, ...] = ()) -> Iterator[tuple[Any, tuple[Any, ...]]]:
    yield comp, ancestors
    for child in getattr(comp, "children", None) or []:
        if isinstance(child, Component):
            yield from _walk(child, (*ancestors, comp))


def _static_id(comp: Any) -> str | None:
    value = getattr(comp, "id", None)
    return value if isinstance(value, str) else None


def _row(row_id: str) -> str:
    """The rendered expression of a row_map entry (``…row_map_rx_state_?.["tags"]``)."""
    return f'row_map_rx_state_?.["{row_id}"]'


class TestDoctorStructure:
    def test_one_h1_one_h2_and_an_h3_per_row(self) -> None:
        js = rendered(doctor())
        assert js.count('jsx("h1",') == 1  # sr-only page heading from layout()
        assert js.count('jsx("h2",') == 1
        # 3 connection rows + the "No library yet" block + 6 library checks.
        assert js.count('jsx("h3",') == 10
        static = re.findall(r'jsx\("h3",\{className:"cname"\},"([^"]+)"\)', js)
        assert static == [name for _, name in _LIBRARY_CHECKS]
        # Connection names carry the provider ("AI provider: OpenAI") from the row.
        for row_id, _ in _CONNECTIONS:
            pattern = r'jsx\("h3",\{className:"cname"\},\S*?' + re.escape(
                f'{_row(row_id)}?.["title"])'
            )
            assert re.search(pattern, js), row_id

    def test_verdict_h2_reads_the_verdict_and_keeps_its_style(self) -> None:
        js = rendered(doctor())
        h2 = js[js.index('jsx("h2",') :]
        h2 = h2[: h2.index('role:"status"')]
        assert 'verdict_rx_state_?.["title"]' in h2
        assert '["fontSize"] : "18px"' in h2
        assert '["fontFamily"] : "var(--font-display)"' in h2

    def test_no_health_is_hardcoded(self) -> None:
        js = rendered(doctor())
        # Every state word comes from the rows; nothing renders "Healthy" or
        # "All systems healthy" unless a scan measured it.
        for literal in ('"Healthy"', '"All systems healthy"', '"Attention needed"', "Run check"):
            assert literal not in js, literal
        assert '?.["state_word"]' in js

    def test_checks_are_two_labelled_lists(self) -> None:
        js = rendered(doctor())
        assert js.count('jsx("ul",') == 2
        for label_id, text in (
            ("doctor-conn-label", "Connections"),
            ("doctor-lib-label", "Library checks"),
        ):
            assert f'id:"{label_id}"' in js
            assert f'"{text}")' in js
            assert f'"aria-labelledby":"{label_id}"' in js
        for start in [m.start() for m in re.finditer(r'jsx\("ul",', js)]:
            props = js[start : js.index('role:"list"}', start) + 12]
            # Explicit list role: WebKit drops list semantics for list-style: none.
            assert 'className:"card doctor-checks"' in props
            assert len(props) < 300  # the role is one of the list's own props
        assert js.count('jsx("li",{className:"check"}') == 9
        assert js.count('jsx("li",{className:"check doctor-nolib"}') == 1
        assert js.count('jsx("li",') == 10

    def test_library_rows_collapse_into_one_block(self) -> None:
        page = doctor()
        js = rendered(page)
        lib_ul = js[js.rindex('jsx("ul",') :]
        assert lib_ul.index("library_collapsed_rx_state_") < lib_ul.index("doctor-nolib")
        # The block's two actions: Connect Plex and Sync library, from the row.
        assert f'{_row("library")}?.["action_label"]' in lib_ul
        assert f'{_row("library")}?.["secondary_label"]' in lib_ul
        assert '"btn btn-sm btn-primary"' in lib_ul  # the next step is the primary one

    def test_fix_buttons_name_their_check(self) -> None:
        js = rendered(doctor())
        for row_id, name in _LIBRARY_CHECKS:
            # JSON-escaped en dash: "<visible label> – <check name>"
            assert f'{_row(row_id)}?.["fix_label"]+" \\u2013 {name}")' in js, row_id

    def test_connection_actions_name_their_row(self) -> None:
        js = rendered(doctor())
        for row_id, name in _CONNECTIONS:
            assert f'{_row(row_id)}?.["check_label"]+" \\u2013 {name}")' in js, row_id
            assert f'{_row(row_id)}?.["action_label"]+" \\u2013 {name}")' in js, row_id
        # Plex's Check runs the free reachability check in place (forced).
        assert re.search(r'refresh_plex_readiness", \(\{ \["force"\] : true \}\)', js)

    def test_every_fix_goes_through_the_confirmation(self) -> None:
        js = rendered(doctor())
        starts = re.findall(r'doctor_state\.start_fix", \(\{ \["target"\] : (\S+?)\["fix"\]', js)
        assert len(starts) == len(_LIBRARY_CHECKS)
        for handler in _FIX_HANDLERS:
            assert f'doctor_state.{handler}"' not in js, handler

    def test_other_fixes_are_disabled_with_a_visible_reason(self) -> None:
        js = rendered(doctor())
        assert js.count('"Another fix is running")') == len(_LIBRARY_CHECKS)
        assert re.search(r"disabled:\(\S+is_fixing_rx_state_ && !\(\(\S+current_fix_target", js)
        assert re.search(r'"aria-describedby":\(\(\S+is_fixing_rx_state_', js)
        assert js.count('"doctor-busy-tags"') >= 2  # the reason id and its reference
        # The running fix's own button stays focusable: aria-disabled, not disabled.
        assert re.search(r'"aria-disabled":\(\(\S+is_fixing_rx_state_', js)

    def test_rows_explain_why_they_matter(self) -> None:
        js = rendered(doctor())
        for row_id, _ in (*_CONNECTIONS, *_LIBRARY_CHECKS[1:]):
            pattern = r'className:"cwhy"\},\S*?' + re.escape(f'{_row(row_id)}?.["why"])')
            assert re.search(pattern, js), row_id

    def test_counts_render_in_mono(self) -> None:
        js = rendered(doctor())
        pattern = r'jsx\("span",\{className:"mono"\},\S*?' + re.escape(
            f'{_row("embeddings")}?.["num"])'
        )
        assert re.search(pattern, js)

    def test_rows_show_their_fix_failure(self) -> None:
        js = rendered(doctor())
        assert js.count('"callout callout-error cerr"') == len(_LIBRARY_CHECKS)
        assert f'{_row("tags")}?.["error_action_href"]' in js

    def test_check_result_is_a_persistent_polite_region(self) -> None:
        page = doctor()
        js = rendered(page)
        found = [(c, a) for c, a in _walk(page) if _static_id(c) == "doctor-check-status"]
        assert len(found) == 1
        # Mounted before any result: no rx.cond between <main> (layout's auth
        # branch wraps the whole page) and the region.
        ids = [_static_id(a) for a in found[0][1]]
        assert "main-content" in ids
        inside_main = found[0][1][ids.index("main-content") :]
        assert not any(isinstance(a, Cond) for a in inside_main)
        region = js[js.index('"aria-atomic":"true"') : js.index('id:"doctor-check-status"')]
        assert '"aria-live":"polite"' in region
        after = js[js.index('id:"doctor-check-status"') :]
        assert 'role:"status"' in after[:200]
        assert "scan_summary_rx_state_" in after[:1200]
        # The only status region besides the shell's ⌘K palette count (#cmdk-status).
        assert js.count('role:"status"') - js.count('id:"cmdk-status"') == 1

    def test_rescan_is_quiet_and_never_starts_twice(self) -> None:
        js = rendered(doctor())
        hero = js[js.index('"card doctor-hero"') :]
        hero = hero[: hero.index('"Re-scan")') + 20]
        assert '"btn btn-sm btn-ghost"' in hero
        assert "btn-primary" not in hero
        assert "doctor_state.run_health_check" in hero
        # Busy, not disabled: focus stays on the button while the scan runs.
        assert re.search(r'"aria-busy":\(\S+is_checking_rx_state_ \? "true" : null\)', hero)


class TestFixConfirmation:
    def test_scope_confirmation_is_rendered_once(self) -> None:
        js = rendered(doctor())
        assert js.count('id:"doctor-fix-confirm"') == 1
        assert js.count('id:"doctor-fix-confirm-cancel"') == 1
        assert js.count('id:"doctor-fix-confirm-confirm"') == 1
        dialog = js[js.index("jsx(RadixThemesAlertDialog.Root,") :]
        root_props = dialog[: dialog.index("jsx(RadixThemesAlertDialog.Content,")]
        assert re.search(r"open:\S+fix_confirm_open_rx_state_", root_props)
        assert "doctor_state.set_fix_confirm_open" in dialog
        assert "doctor_state.confirm_fix" in dialog
        assert "AlertDialog.Action" not in dialog
        for key in ("title", "count_line", "provider_label", "duration_line", "privacy_line"):
            assert f'fix_scope_rx_state_?.["{key}"]' in dialog, key


class TestFixProgress:
    def test_progressbar_in_tracks(self) -> None:
        js = rendered(_fix_progress_bar())
        assert 'role:"progressbar"' in js
        assert '"aria-label":"Fix progress"' in js
        assert '"aria-valuemin":"0"' in js
        assert re.search(r'"aria-valuemax":\(\(\S+fix_total_rx_state_ > 0\) \? \S+fix_total', js)
        assert re.search(r'"aria-valuenow":\(\(\S+ > 0\) \? \S+fix_progress_rx_state_ : null\)', js)
        assert re.search(
            r'"aria-valuetext":\(\(\S+ > 0\) \? \(\S+fix_progress_label\S*\+" tracks"', js
        )
        assert '"pbar doctor-fix-pbar"' in js and "fix_percent" in js

    def test_unknown_total_is_an_indeterminate_loadbar(self) -> None:
        js = rendered(_fix_progress_bar())
        # Same element, same name and role; the kit's loadbar instead of "0 / 0".
        assert re.search(
            r'className:\(\(\S+fix_total_rx_state_ > 0\) \? "pbar doctor-fix-pbar" : '
            r'"loadbar doctor-fix-loadbar"\)',
            js,
        )
        assert 'jsx(RadixThemesBox,{className:"ind"},)' in js
        assert re.search(r'"aria-valuemax":\(\(\S+ > 0\) \? \S+ : null\)', js)
        assert "0 / 0" not in js

    def test_progressbar_sits_in_the_fix_callout(self) -> None:
        js = rendered(doctor())
        assert js.count('role:"progressbar"') == 1
        # Props precede children: the bar follows the callout's className and
        # comes before the callout's Cancel button.
        start = js.index('"callout callout-info"')
        callout = js[start : js.index('"Cancel")', start)]
        assert 'role:"progressbar"' in callout
        assert "fix_title_rx_state_" in callout
