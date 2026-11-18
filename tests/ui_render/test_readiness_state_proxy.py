"""Readiness computed vars under real Reflex (state dicts arrive as MutableProxy).

Reflex hands ``AppState.readiness`` (and ``DashboardState.setup_jobs``) to
computed vars wrapped in ``MutableProxy``. The readiness helpers must read those
exactly like plain dicts: a ``typing.Mapping`` isinstance check is False for the
proxy, which once made every entry read as level "" / blocking "false" (Generate
enabled while blocked, no Doctor badge, a first-run checklist that never went
away). Each test builds a real state tree and compares the computed vars with the
pure functions over plain dicts.
"""

from __future__ import annotations

from typing import Any

import pytest
from reflex.state import State

from plexmix.services import readiness as rd
from plexmix.ui.states.app_state import AppState
from plexmix.ui.states.dashboard_state import DashboardState
from plexmix.ui.states.generator_state import GeneratorState


def _entry(component: str, level: str, cause: str = "", blocking: str = "false") -> dict:
    keys = (
        "state tone label provider model detail title body message action_label action_href "
        "fix_label secondary_label secondary_href sends_to checked_at checked_ts checked_ago "
        "checked_title"
    ).split()
    out = {key: "" for key in keys}
    out.update(component=component, level=level, cause=cause, blocking=blocking)
    return out


def _snapshot(**levels: tuple) -> dict:
    snap = {
        "plex": _entry("plex", "ready"),
        "ai": _entry("ai", "unchecked"),
        "embeddings": _entry("embeddings", "unchecked"),
        "library": _entry("library", "ready"),
        "index": _entry("index", "ready"),
        "audio": _entry("audio", "off"),
        "musicbrainz": _entry("musicbrainz", "off"),
    }
    for component, spec in levels.items():
        snap[component] = _entry(component, *spec)
    return snap


COMPLETE = _snapshot()
BLOCKED = _snapshot(
    plex=("failing", "unreachable"),
    embeddings=("failing", "missing_dependency", "true"),
    index=("failing", "index_empty", "true"),
)
FRESH = _snapshot(
    plex=("not_set_up", "missing_config", "true"),
    library=("not_set_up", "not_synced", "true"),
    index=("not_set_up", "not_synced", "true"),
)


def _tree(snapshot: dict, jobs: Any = None) -> tuple[Any, Any, Any]:
    root = State(_reflex_internal_init=True)
    app = root.get_substate(AppState.get_full_name().split(".")[1:])
    dash = root.get_substate(DashboardState.get_full_name().split(".")[1:])
    gen = root.get_substate(GeneratorState.get_full_name().split(".")[1:])
    app.library_stats_loaded = True
    app.stat_total = 5943
    app.readiness = snapshot
    if jobs is not None:
        dash.setup_jobs = jobs
    root.get_delta()
    return app, dash, gen


@pytest.mark.parametrize("name,snapshot", [("complete", COMPLETE), ("blocked", BLOCKED)])
def test_computed_vars_match_the_pure_functions(name: str, snapshot: dict) -> None:
    app, dash, gen = _tree(snapshot)
    assert type(app.readiness).__name__ == "MutableProxy"  # the case this guards
    assert app.show_doctor_badge is rd.needs_attention(snapshot), name
    assert gen.can_generate is rd.can_generate(snapshot), name
    incomplete = rd.setup_incomplete(snapshot)
    assert dash.setup_incomplete is incomplete, name
    assert dash.generate_primary is (not incomplete), name
    expected = rd.setup_steps(snapshot, {"total_tracks": 5943, "missing_embeddings": 0}, {})
    assert [s["state"] for s in dash.setup_steps] == [s["state"] for s in expected], name


def test_complete_setup_hides_the_checklist() -> None:
    app, dash, gen = _tree(COMPLETE)
    assert dash.setup_incomplete is False
    assert dash.generate_primary is True
    assert app.show_doctor_badge is False
    assert gen.can_generate is True


def test_blocked_generation_is_disabled_and_flagged() -> None:
    app, dash, gen = _tree(BLOCKED)
    assert gen.can_generate is False
    assert gen.gen_status["blocked"] == "true"
    assert app.show_doctor_badge is True
    assert dash.setup_incomplete is True


def test_fresh_install_starts_at_connect_plex() -> None:
    app, dash, gen = _tree(FRESH)
    steps = dash.setup_steps
    assert [s["state"] for s in steps][:1] == ["current"]
    assert [s["id"] for s in steps if s["primary"] == "true"] == ["plex"]
    assert app.show_doctor_badge is True
    assert gen.can_generate is False


def test_running_sync_shows_in_its_step_through_the_proxy() -> None:
    plex_ready = _snapshot(
        library=("not_set_up", "not_synced", "true"),
        index=("not_set_up", "not_synced", "true"),
    )
    jobs = {"sync": {"status": "running", "progress": "42", "message": "Syncing…"}}
    _app, dash, _gen = _tree(plex_ready, jobs)
    steps = {s["id"]: s for s in dash.setup_steps}
    assert steps["plex"]["state"] == "done"
    assert steps["sync"]["state"] == "running"
    assert steps["sync"]["progress"] == "42"
