"""Harness for render-smoke tests that build real Reflex components.

These tests need the real ``reflex`` package (``poetry install -E ui``) and must
run in their own pytest session::

    pytest tests/ui_render -q

The whole directory is reported as skipped when Reflex can't be imported, or
when ``sys.modules["reflex"]`` is a mock. ``tests/ui/conftest.py`` replaces
Reflex with a ``MagicMock`` when ``tests/ui`` is collected, so in a full
``pytest`` run (``tests/ui`` is collected first) these tests skip rather than
render against the mock. Run them first in a mixed session
(``pytest tests/ui_render tests/ui``) and ``tests/ui`` breaks instead, because the
UI modules were already imported against real Reflex: keep them separate.

What ``str(component)`` prints (verified in Reflex 0.8.24.post1)
---------------------------------------------------------------
``str(component)`` returns the compiled JSX *expression* for the component
tree (no imports, hooks or page wrapper), on one line:

- HTML elements (``rx.el.*``): ``jsx("tag",{props},child,...)``, e.g.
  ``jsx("main",{className:"scroll",id:"main-content",ref:ref_main_content,tabIndex:-1},...)``.
- Library components render their JS name unquoted:
  ``jsx(RadixThemesBox,{className:"shell"},...)``, ``jsx(Fragment,{},...)``.
  ``rx.el.a`` is a React Router link: ``jsx(ReactRouterLink,{...,to:"#main-content"},...)``
  (``href=`` becomes ``to:``).
- Props are a JS object literal with keys sorted. React prop names are bare
  camelCase (``className``, ``tabIndex``, ``htmlFor``, ``onChange``,
  ``reloadDocument``); hyphenated attributes are quoted (``"aria-label":"x"``,
  ``"aria-labelledby":"x"``, ``"data-pm-cancel":""``). String values are
  double-quoted; numbers and booleans are bare (``tabIndex:-1``, ``checked:true``).
- An ``id="a-b"`` also adds ``ref:ref_a_b``.
- Text children are quoted strings: ``...},"Skip to main content")``.
- State vars render as JS expressions over ``reflex___state____state...``;
  ``rx.cond`` becomes ``(cond ? (a) : (b))`` and ``rx.match`` becomes
  ``(() => { switch (JSON.stringify(...)) {case JSON.stringify("/dashboard"):
  return ("Dashboard"); ...``.
- Event handlers render as ``onChange:((_e) => (addEvents([(ReflexEvent(
  "<state path>.<handler>", ({ ["arg"] : "value" }), ...``.
- A string ``value=`` on an input renders as ``value:(isNotNullOrUndefined("a") ? "a" : "")``.
- Gotcha: a kwarg that is not a prop of the component is emitted as CSS, not
  as an attribute. ``rx.el.fieldset`` derives from ``Element`` (not
  ``BaseHTML``), so ``role=``/``tab_index=``/``title=`` on it end up inside
  ``css:({ ["role"] : ... })``. Assert on ``role:"..."`` to catch that; use
  ``custom_attrs={"role": ...}`` for such elements.

Test modules import the helper with ``from tests.ui_render.conftest import rendered``.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import NonCallableMock

import pytest

# Render tests never need credentials: make sure nothing can reach the real keyring.
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _skip_reason() -> str | None:
    """Return why real-Reflex render tests can't run in this session, or None."""
    module = sys.modules.get("reflex")
    if module is not None:
        if isinstance(module, NonCallableMock) or not isinstance(module, types.ModuleType):
            return (
                "reflex is mocked in this session (tests/ui); "
                "run `pytest tests/ui_render` in its own session"
            )
        return None
    try:
        importlib.import_module("reflex")
    except Exception as exc:  # ImportError, or a broken install
        return f"real Reflex is not importable ({type(exc).__name__}); install the ui extra"
    return None


class _ReflexUnavailableItem(pytest.Item):
    """Placeholder test that reports a whole module as skipped."""

    skip_reason: str = ""

    def runtest(self) -> None:
        pytest.skip(self.skip_reason)

    def reportinfo(self) -> tuple[Path, int | None, str]:
        return self.path, None, f"{self.path.name} (needs real Reflex)"


class _SkippedModule(pytest.Module):
    """A test module that is never imported and yields one skipped placeholder.

    A skipped *test* (rather than a skipped collector) keeps the exit status 0
    when this directory runs alone without the ``ui`` extra.
    """

    skip_reason: str = ""

    def collect(self) -> list[pytest.Item]:
        item = _ReflexUnavailableItem.from_parent(self, name="needs_real_reflex")
        item.skip_reason = self.skip_reason
        return [item]


def pytest_pycollect_makemodule(module_path: Path, parent: pytest.Collector) -> Any:
    """Skip every test module in this directory when real Reflex is unavailable.

    Checked lazily, per module, so a mock installed by ``tests/ui/conftest.py``
    earlier in the session is seen. The module is not imported, so its
    top-level ``import reflex`` can't fail or bind the mock.
    """
    if module_path.name == "__init__.py":
        return None  # let pytest build the Package as usual
    reason = _skip_reason()
    if reason is None:
        return None
    module = _SkippedModule.from_parent(parent, path=module_path)
    module.skip_reason = reason
    return module


def rendered(component: Any) -> str:
    """Return the compiled JSX expression for ``component`` (``str(component)``).

    Also collects imports, hooks and custom code the way the compiler does, so a
    component that renders but would fail to compile is caught too.
    """
    js = str(component)
    component._get_all_imports()
    component._get_all_hooks()
    component._get_all_custom_code()
    return js
