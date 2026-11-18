"""Tests for the pure Web UI helpers (no Reflex needed).

Kept at the top level of ``tests/`` so the ``tests/ui`` Reflex mock never
applies: these modules must work without the ``ui`` extra.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from plexmix.ui.utils import a11y, playlist_identity
from plexmix.ui.utils.a11y import (
    SETTINGS_SECTIONS,
    aria_sort_value,
    enrichment_flags,
    focus_by_id_script,
    focus_first_script,
    resolve_settings_section,
    settings_section_url_script,
)
from plexmix.ui.utils.playlist_identity import (
    cover_seed,
    cover_variant,
    short_date,
    top_artists_label,
)

_SRC = Path(__file__).resolve().parents[1] / "src"


class TestSettingsSections:
    def test_section_ids_and_order(self) -> None:
        assert SETTINGS_SECTIONS == (
            "plex",
            "ai",
            "embedding",
            "audio",
            "musicbrainz",
            "advanced",
            "appearance",
        )

    @pytest.mark.parametrize("section", SETTINGS_SECTIONS)
    def test_valid_section_is_selected(self, section: str) -> None:
        assert resolve_settings_section(section, "plex") == section

    def test_unknown_section_keeps_current(self) -> None:
        assert resolve_settings_section("bogus", "plex") == "plex"
        assert resolve_settings_section("bogus", "audio") == "audio"

    def test_missing_or_empty_section_keeps_current(self) -> None:
        assert resolve_settings_section(None, "advanced") == "advanced"
        assert resolve_settings_section("", "advanced") == "advanced"
        assert resolve_settings_section("   ", "advanced") == "advanced"

    def test_case_and_whitespace_tolerated(self) -> None:
        assert resolve_settings_section(" AI ", "plex") == "ai"

    def test_url_script_rewrites_address_in_place(self) -> None:
        script = settings_section_url_script("embedding")
        assert script == "history.replaceState(history.state,'','/settings?section=embedding')"
        assert "section=embedding" in script
        assert "history.state" in script

    @pytest.mark.parametrize("section", SETTINGS_SECTIONS)
    def test_url_script_contains_every_section(self, section: str) -> None:
        assert f"/settings?section={section}'" in settings_section_url_script(section)

    @pytest.mark.parametrize("bad", ["bogus", "", "ai');alert(1)//", "AI"])
    def test_url_script_rejects_unknown_tabs(self, bad: str) -> None:
        with pytest.raises(ValueError):
            settings_section_url_script(bad)


class TestFocusScripts:
    def test_focus_by_id_forces_focus_via_pmfocus(self) -> None:
        assert (
            focus_by_id_script("tag-edit-btn-12")
            == "window.pmFocus&&pmFocus.when('#tag-edit-btn-12',{force:true})"
        )

    @pytest.mark.parametrize("bad", ["", "has space", "x'y", "1abc", "a#b", "a.b"])
    def test_focus_by_id_rejects_unsafe_ids(self, bad: str) -> None:
        with pytest.raises(ValueError):
            focus_by_id_script(bad)

    def test_focus_first_waits_for_lost_focus(self) -> None:
        script = focus_first_script(["#gen-error", "#main-heading"])
        assert script == 'window.pmFocus&&pmFocus.when(["#gen-error", "#main-heading"])'
        assert "force" not in script

    def test_focus_first_force(self) -> None:
        script = focus_first_script(["#main-heading"], force=True)
        assert script == 'window.pmFocus&&pmFocus.when(["#main-heading"],{force:true})'

    def test_focus_first_escapes_selectors(self) -> None:
        script = focus_first_script(['button[title="a"]'])
        assert script == 'window.pmFocus&&pmFocus.when(["button[title=\\"a\\"]"])'


class TestAriaSort:
    def test_sorted_column_ascending(self) -> None:
        assert aria_sort_value("artist", "artist", True) == "ascending"

    def test_sorted_column_descending(self) -> None:
        assert aria_sort_value("artist", "artist", False) == "descending"

    def test_other_column_none(self) -> None:
        assert aria_sort_value("title", "artist", True) == "none"
        assert aria_sort_value("album", "artist", False) == "none"


class TestEnrichmentFlags:
    def test_partially_enriched_track(self) -> None:
        flags = enrichment_flags(
            {
                "tags": "chill, late night",
                "has_embedding": "1",
                "has_audio_features": "0",
                "has_musicbrainz": "0",
            }
        )
        assert flags == {
            "enr_tags": "1",
            "enr_embedded": "1",
            "enr_audio": "0",
            "enr_mb": "0",
            "enr_label": "Tagged, embedded, audio not analyzed, MusicBrainz not matched",
        }

    def test_empty_tags_are_not_tagged(self) -> None:
        flags = enrichment_flags({"tags": "", "has_embedding": "0"})
        assert flags["enr_tags"] == "0"
        assert flags["enr_label"].startswith("Not tagged, ")

    @pytest.mark.parametrize("tags", ["   ", "\t\n"])
    def test_whitespace_tags_count_as_tagged_like_the_sql_counts(self, tags: str) -> None:
        # count_untagged_tracks() is ``tags IS NULL OR tags = ''``: whitespace is tagged.
        flags = enrichment_flags({"tags": tags, "has_embedding": "0"})
        assert flags["enr_tags"] == "1"
        assert flags["enr_label"].startswith("Tagged, ")

    def test_missing_keys_count_as_absent(self) -> None:
        flags = enrichment_flags({})
        assert flags["enr_tags"] == "0"
        assert flags["enr_embedded"] == "0"
        assert flags["enr_audio"] == "0"
        assert flags["enr_mb"] == "0"
        assert flags["enr_label"] == (
            "Not tagged, not embedded, audio not analyzed, MusicBrainz not matched"
        )

    @pytest.mark.parametrize("value,expected", [("0", "0"), ("1", "1"), ("", "0")])
    def test_has_embedding(self, value: str, expected: str) -> None:
        assert enrichment_flags({"has_embedding": value})["enr_embedded"] == expected

    def test_audio_and_musicbrainz_flags(self) -> None:
        flags = enrichment_flags(
            {"tags": "a", "has_embedding": "1", "has_audio_features": "1", "has_musicbrainz": "1"}
        )
        assert flags["enr_audio"] == "1"
        assert flags["enr_mb"] == "1"
        assert flags["enr_label"] == "Tagged, embedded, audio analyzed, MusicBrainz matched"

    def test_boolean_strings_are_tolerated(self) -> None:
        flags = enrichment_flags({"has_audio_features": "True", "has_musicbrainz": "false"})
        assert flags["enr_audio"] == "1"
        assert flags["enr_mb"] == "0"


class TestCoverVariant:
    def test_stable_across_calls_and_in_range(self) -> None:
        seed = "smoky late night jazz"
        first = cover_variant(seed)
        assert first == cover_variant(seed)
        assert 0 <= first < 6

    def test_stable_across_fresh_processes(self) -> None:
        seed = "smoky late night jazz"
        expected = cover_variant(seed)
        code = (
            "from plexmix.ui.utils.playlist_identity import cover_variant;"
            f"print(cover_variant({seed!r}))"
        )
        for hash_seed in ("1", "2"):
            env = {**os.environ, "PYTHONPATH": str(_SRC), "PYTHONHASHSEED": hash_seed}
            out = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                env=env,
                check=True,
                timeout=60,
            )
            assert int(out.stdout.strip().splitlines()[-1]) == expected

    def test_normalizes_case_and_whitespace(self) -> None:
        assert cover_variant("  Late Night Jazz ") == cover_variant("late night jazz")

    @pytest.mark.parametrize("n", [1, 2, 6, 11])
    def test_always_in_range(self, n: int) -> None:
        for i in range(200):
            assert 0 <= cover_variant(f"seed {i}", n) < n

    def test_different_seeds_spread_over_variants(self) -> None:
        variants = {cover_variant(f"prompt number {i}") for i in range(30)}
        assert len(variants) > 1

    def test_empty_seed_is_valid(self) -> None:
        assert 0 <= cover_variant("") < 6

    def test_n_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            cover_variant("x", 0)


class TestCoverSeed:
    def test_prompt_wins(self) -> None:
        assert cover_seed("smoky late night jazz", "Late Night Jazz") == "smoky late night jazz"

    @pytest.mark.parametrize("prompt", ["", "   "])
    def test_name_when_no_prompt(self, prompt: str) -> None:
        assert cover_seed(prompt, "Imported Mix") == "Imported Mix"


class TestShortDate:
    def test_sqlite_timestamp_with_microseconds(self) -> None:
        assert short_date("2025-12-27 20:35:19.327064") == "2025-12-27"

    def test_iso_with_z(self) -> None:
        assert short_date("2025-12-27T20:35:19Z") == "2025-12-27"

    @pytest.mark.parametrize(
        "value",
        ["2025-12-27T20:35:19.5+02:00", "2025-12-27T20:35", "2025-12-27", " 2025-12-27 01:02:03 "],
    )
    def test_other_iso_shapes(self, value: str) -> None:
        assert short_date(value) == "2025-12-27"

    @pytest.mark.parametrize(
        "value", ["", "garbage", "yesterday", "2025-13-45 00:00:00", "27/12/2025", "2025-12-27xyz"]
    )
    def test_garbage_is_empty(self, value: str) -> None:
        assert short_date(value) == ""


class TestTopArtistsLabel:
    def test_first_three(self) -> None:
        names = ["Bill Evans", "Chet Baker", "Miles Davis", "John Coltrane"]
        assert top_artists_label(names) == "Bill Evans, Chet Baker, Miles Davis"

    def test_fewer_than_limit(self) -> None:
        assert top_artists_label(["Bill Evans"]) == "Bill Evans"

    def test_empty(self) -> None:
        assert top_artists_label([]) == ""

    def test_skips_blank_and_duplicate_names(self) -> None:
        assert top_artists_label(["", "Bill Evans", "  ", "bill evans", "Chet Baker"]) == (
            "Bill Evans, Chet Baker"
        )

    def test_custom_limit(self) -> None:
        assert top_artists_label(["A", "B", "C"], limit=2) == "A, B"
        assert top_artists_label(["A", "B"], limit=0) == ""


class TestNoReflexImport:
    @pytest.mark.parametrize("module", [a11y, playlist_identity])
    def test_module_does_not_import_reflex(self, module: object) -> None:
        source = Path(getattr(module, "__file__")).read_text(encoding="utf-8")
        assert "import reflex" not in source
        assert "from reflex" not in source
