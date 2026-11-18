"""GeneratorState.cover_variant: the Generator results swatch matches History's (task 6.7).

Runs under the mocked Reflex from ``tests/ui/conftest.py`` (``@rx.var(cache=True)``
becomes a plain property there).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from plexmix.database.models import Playlist
from plexmix.ui.utils.helpers import str_dict
from plexmix.ui.utils.playlist_identity import cover_seed, cover_variant

PROMPTS = [
    "Chill rainy day vibes with acoustic guitar",
    "Energetic workout music to pump me up",
    "late night jazz",
    "  Late Night Jazz  ",
    # Longer than the 60 characters a saved playlist's default name keeps.
    "Melancholic indie tracks for late night reflection after a long week at work",
]


def _generator(prompt: str):  # type: ignore[no-untyped-def]
    from plexmix.ui.states.generator_state import GeneratorState

    state = GeneratorState()
    state.mood_query = prompt
    return state


def _saved_history_card(prompt: str) -> dict[str, str]:
    """The History dict for a playlist saved from the Generator with ``prompt``.

    Mirrors ``GeneratorState.save_locally`` (name defaults to the first 60
    characters of the prompt; ``mood_query`` is the prompt) and
    ``HistoryState.load_playlists`` (``str_dict(model_dump())`` then decoration).
    """
    from plexmix.ui.states.history_state import HistoryState

    saved = Playlist(
        id=7,
        name=prompt.strip()[:60],
        created_by_ai=True,
        mood_query=prompt,
        created_at=datetime(2025, 12, 27, 20, 35, 19, 327064),
    )
    row = str_dict(saved.model_dump())
    return HistoryState._decorate_playlists([row], {7: ["Artist A"]})[0]


class TestGeneratorCoverVariant:
    @pytest.mark.parametrize("prompt", PROMPTS)
    def test_matches_the_shared_seed_rule(self, prompt: str) -> None:
        state = _generator(prompt)
        assert state.cover_variant == cover_variant(cover_seed(prompt, ""))
        assert 0 <= state.cover_variant < 6

    @pytest.mark.parametrize("prompt", PROMPTS)
    def test_equals_the_history_variant_for_the_same_prompt(self, prompt: str) -> None:
        card = _saved_history_card(prompt)
        assert str(card["cover_variant"]) == str(_generator(prompt).cover_variant)

    def test_follows_the_prompt(self) -> None:
        state = _generator("late night jazz")
        before = state.cover_variant
        assert _generator("  LATE NIGHT JAZZ ").cover_variant == before
        # Some other prompt lands on a different swatch (six variants).
        others = {_generator(f"prompt number {i}").cover_variant for i in range(12)}
        assert len(others) > 1

    def test_empty_prompt_is_in_range(self) -> None:
        assert 0 <= _generator("").cover_variant < 6
