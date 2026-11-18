"""Regression guard: no raw server-controlled text fields in the web UI.

webui-input-responsiveness (design D1): a text field that binds ``value=`` to
server state and sends ``on_change`` on every key loses typed characters when
the websocket round trip is slower than the typing (the server echoes an older
value over newer text). Text fields are built with the client-owned helpers in
``plexmix.ui.components.inputs`` instead (``default_value=``, debounced or
blur commits, ``rx.set_value`` for server writes).

This module AST-scans ``src/plexmix/ui/**/*.py`` for ``rx.el.input(...)`` and
``rx.el.textarea(...)`` calls that pass both ``value=`` and ``on_change=``.
Inputs whose ``type`` resolves to ``range``, ``checkbox`` or ``radio`` (a string
literal, a module or function constant, ``rx.Var.create("range")``, or a
``rx.cond``/``x if c else y`` whose branches all resolve to those) are exempt:
one discrete change makes one event, so they stay controlled. Settings' Radix
``rx.input`` fields are not ``rx.el`` fields and stay on ``DebounceInput`` (D5).

It also scans ``src/plexmix/ui/states/**/*.py`` for a handler that returns (or
yields) ``rx.toggle_color_mode``: that is a client-only Var, and returning it
from a backend handler fails on the server.

``PENDING`` listed the offending call sites that existed when the guard landed;
each entry was removed by the task that converted it, and the list is now empty
(task 9.1). The guard fails both for an offender that is not listed and for a
listed entry that no longer offends, and ``test_pending_allowlist_is_empty``
keeps the list empty.

An offender's key is ``"<path under src/plexmix/ui>::<enclosing def>::<tag>[<discriminator>]"``.
The discriminator is the first of ``id``, ``aria_label``, ``name``,
``class_name`` or ``placeholder`` the call passes, written ``kw=<text>`` for a
string literal and ``kw={<expression>}`` otherwise.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pytest

UI_ROOT = Path(__file__).resolve().parents[2] / "src" / "plexmix" / "ui"

# Input types that stay controlled (one discrete change = one event).
EXEMPT_INPUT_TYPES = frozenset({"range", "checkbox", "radio"})

# Keyword arguments used, in this order, to tell offenders in one function apart.
DISCRIMINATOR_KWARGS = ("id", "aria_label", "name", "class_name", "placeholder")

# Callee suffixes that build a raw HTML text field.
_FIELD_CALLEES = {
    ("el", "input"): "input",
    ("el", "textarea"): "textarea",
    ("forms", "input"): "input",
    ("forms", "textarea"): "textarea",
    ("Input", "create"): "input",
    ("Textarea", "create"): "textarea",
}

TOGGLE_LABEL = "returns rx.toggle_color_mode"

# Offending call sites still awaiting conversion, with the task that converts each.
# The guard landed with 12 entries (navbar palette, generator prompt and genre,
# library search, genre and bulk tags, tagging artist, genre, stale days and the
# inline editor, history search, and the palette's returned toggle_color_mode);
# tasks 2.1, 3.1, 5.2, 6.2, 7.2 and 7.3 converted them all, so the list is empty
# (task 9.1). Never add an entry here: build the field with the inputs helpers.
PENDING: dict[str, str] = {}


@dataclass(frozen=True)
class Offender:
    """One offending call site."""

    key: str
    path: Path
    line: int

    def where(self) -> str:
        return f"{self.path}:{self.line}"


# ── Static resolution helpers ────────────────────────────────────────────────


def _dotted(node: ast.AST) -> Optional[str]:
    """``a.b.c`` for a Name/Attribute chain, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Map local names to the dotted path they were imported as (any scope)."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _qualified(name: str, aliases: dict[str, str]) -> str:
    head, _, rest = name.partition(".")
    base = aliases.get(head, head)
    return f"{base}.{rest}" if rest else base


def _field_tag(call: ast.Call, aliases: dict[str, str]) -> Optional[str]:
    """``"input"``/``"textarea"`` when ``call`` builds a raw ``rx.el`` text field."""
    name = _dotted(call.func)
    if name is None:
        return None
    parts = _qualified(name, aliases).split(".")
    if len(parts) < 2:
        return None
    return _FIELD_CALLEES.get((parts[-2], parts[-1]))


def _constant_bindings(body: Iterable[ast.stmt]) -> dict[str, ast.expr]:
    """Simple ``NAME = <expr>`` bindings in a block (not descending into nested defs)."""
    bindings: dict[str, ast.expr] = {}
    stack = list(body)
    while stack:
        stmt = stack.pop(0)
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            if isinstance(stmt.target, ast.Name):
                bindings[stmt.target.id] = stmt.value
        for field in ("body", "orelse", "finalbody", "handlers"):
            stack.extend(getattr(stmt, field, []) or [])
    return bindings


class _Scopes:
    """Constant lookup through enclosing function scopes, then the module."""

    def __init__(self, module: dict[str, ast.expr], frames: list[dict[str, ast.expr]]):
        self.module = module
        self.frames = frames

    def lookup(self, name: str) -> Optional[ast.expr]:
        for frame in reversed(self.frames):
            if name in frame:
                return frame[name]
        return self.module.get(name)


def _possible_strings(node: ast.AST, scopes: _Scopes, depth: int = 0) -> Optional[set[str]]:
    """Every string ``node`` can evaluate to, or None when it can't be resolved."""
    if depth > 8:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        bound = scopes.lookup(node.id)
        return None if bound is None else _possible_strings(bound, scopes, depth + 1)
    if isinstance(node, ast.IfExp):
        a = _possible_strings(node.body, scopes, depth + 1)
        b = _possible_strings(node.orelse, scopes, depth + 1)
        return None if a is None or b is None else a | b
    if isinstance(node, ast.Call):
        callee = _dotted(node.func) or ""
        last = callee.split(".")[-1]
        # rx.Var.create("range") / LiteralVar.create("range") / Var.create(...)
        if callee.endswith("Var.create") and node.args:
            return _possible_strings(node.args[0], scopes, depth + 1)
        # rx.cond(c, "range", "checkbox")
        if last == "cond" and len(node.args) == 3:
            a = _possible_strings(node.args[1], scopes, depth + 1)
            b = _possible_strings(node.args[2], scopes, depth + 1)
            return None if a is None or b is None else a | b
    return None


def _keywords(call: ast.Call, scopes: _Scopes) -> dict[str, ast.expr]:
    """Keyword arguments, including keys of ``**{...}`` / ``**dict(...)`` it can resolve."""
    found: dict[str, ast.expr] = {}
    for kw in call.keywords:
        if kw.arg is not None:
            found[kw.arg] = kw.value
            continue
        spread: Optional[ast.AST] = kw.value
        if isinstance(spread, ast.Name):
            spread = scopes.lookup(spread.id)
        if isinstance(spread, ast.Dict):
            for key, value in zip(spread.keys, spread.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found[key.value] = value
        elif isinstance(spread, ast.Call) and _dotted(spread.func) == "dict":
            for inner in spread.keywords:
                if inner.arg is not None:
                    found[inner.arg] = inner.value
    return found


def _discriminator(keywords: dict[str, ast.expr]) -> str:
    for kw in DISCRIMINATOR_KWARGS:
        if kw in keywords:
            value = keywords[kw]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return f"{kw}={value.value}"
            return f"{kw}={{{ast.unparse(value)}}}"
    return "?"


def _is_exempt_type(keywords: dict[str, ast.expr], scopes: _Scopes) -> bool:
    if "type" not in keywords:
        return False
    values = _possible_strings(keywords["type"], scopes)
    return values is not None and len(values) > 0 and values <= EXEMPT_INPUT_TYPES


# ── Scanners ─────────────────────────────────────────────────────────────────


class _Visitor(ast.NodeVisitor):
    def __init__(self, rel: str, path: Path, tree: ast.Module, scan_toggle: bool):
        self.rel = rel
        self.path = path
        self.aliases = _import_aliases(tree)
        self.module = _constant_bindings(tree.body)
        self.frames: list[dict[str, ast.expr]] = []
        self.names: list[str] = []
        self.scan_toggle = scan_toggle
        self.found: list[Offender] = []

    def _scope(self) -> _Scopes:
        return _Scopes(self.module, self.frames)

    def _where(self) -> str:
        return ".".join(self.names) or "<module>"

    def _visit_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.names.append(node.name)
        self.frames.append(_constant_bindings(node.body))
        self.generic_visit(node)
        self.frames.pop()
        self.names.pop()

    visit_FunctionDef = _visit_def
    visit_AsyncFunctionDef = _visit_def

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Methods are keyed by their own name (``run_action``), not ``Class.run_action``.
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        tag = _field_tag(node, self.aliases)
        if tag is not None:
            scopes = self._scope()
            keywords = _keywords(node, scopes)
            if (
                "value" in keywords
                and "on_change" in keywords
                and not _is_exempt_type(keywords, scopes)
            ):
                key = f"{self.rel}::{self._where()}::{tag}[{_discriminator(keywords)}]"
                self.found.append(Offender(key, self.path, node.lineno))
        self.generic_visit(node)

    def _check_return(self, value: Optional[ast.AST], lineno: int) -> None:
        if not self.scan_toggle or value is None:
            return
        for sub in ast.walk(value):
            if (isinstance(sub, ast.Attribute) and sub.attr == "toggle_color_mode") or (
                isinstance(sub, ast.Name) and sub.id == "toggle_color_mode"
            ):
                key = f"{self.rel}::{self._where()}::{TOGGLE_LABEL}"
                self.found.append(Offender(key, self.path, lineno))
                return

    def visit_Return(self, node: ast.Return) -> None:
        self._check_return(node.value, node.lineno)
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        self._check_return(node.value, node.lineno)
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self._check_return(node.value, node.lineno)
        self.generic_visit(node)


def scan_source(source: str, rel: str, *, path: Optional[Path] = None) -> list[Offender]:
    """Offenders in one module's source. ``rel`` is its path under the UI root.

    Modules under ``states/`` are also checked for returned ``toggle_color_mode``.
    """
    tree = ast.parse(source)
    visitor = _Visitor(rel, path or Path(rel), tree, scan_toggle=rel.startswith("states/"))
    visitor.visit(tree)
    return visitor.found


def scan_tree(root: Path) -> list[Offender]:
    """Offenders in every ``*.py`` under ``root`` (keys relative to ``root``)."""
    found: list[Offender] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        found.extend(scan_source(path.read_text(encoding="utf-8"), rel, path=path))
    return found


def unlisted(found: Iterable[Offender], pending: Iterable[str]) -> list[Offender]:
    """Offenders not covered by ``pending`` (each entry covers one occurrence)."""
    budget = Counter(list(pending))  # keys only, even when given the PENDING dict
    extra: list[Offender] = []
    for offender in found:
        if budget[offender.key] > 0:
            budget[offender.key] -= 1
        else:
            extra.append(offender)
    return extra


def stale(found: Iterable[Offender], pending: Iterable[str]) -> list[str]:
    """``pending`` entries that no longer match an offender."""
    remaining = Counter(o.key for o in found)
    gone: list[str] = []
    for key in pending:
        if remaining[key] > 0:
            remaining[key] -= 1
        else:
            gone.append(key)
    return gone


# ── The guard ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def ui_offenders() -> list[Offender]:
    assert UI_ROOT.is_dir(), UI_ROOT
    return scan_tree(UI_ROOT)


def test_no_new_raw_controlled_text_fields(ui_offenders: list[Offender]) -> None:
    extra = unlisted(ui_offenders, PENDING)
    assert not extra, (
        "Raw server-controlled text fields lose typed characters under latency. Build "
        "them with plexmix.ui.components.inputs (filter_input / compose_input / "
        "compose_textarea) and return rx.set_value(<id>, <value>) from server writes; "
        "a backend handler must not return rx.toggle_color_mode (client-only):\n"
        + "\n".join(f"  {o.where()}  {o.key}" for o in extra)
    )


def test_pending_allowlist_has_no_stale_entries(ui_offenders: list[Offender]) -> None:
    gone = stale(ui_offenders, PENDING)
    assert not gone, (
        "These PENDING entries no longer offend; remove them from PENDING in "
        "tests/ui/test_input_bindings.py:\n" + "\n".join(f"  {key}" for key in gone)
    )


def test_pending_entries_are_unique() -> None:
    # A dict can't hold duplicates, but keep the invariant explicit for edits.
    assert len(PENDING) == len(set(PENDING))


def test_pending_allowlist_is_empty(ui_offenders: list[Offender]) -> None:
    """Task 9.1: every raw controlled text field has been converted."""
    assert PENDING == {}
    assert ui_offenders == []


# ── Self-tests of the scanner ────────────────────────────────────────────────


def _keys(source: str, rel: str = "pages/probe.py") -> list[str]:
    return [o.key for o in scan_source(source, rel)]


def test_scanner_flags_controlled_input_and_textarea() -> None:
    source = (
        "import reflex as rx\n"
        "def bar():\n"
        "    rx.el.input(value=S.q, on_change=S.set_q, aria_label='Search')\n"
        "    rx.el.textarea(id='p', value=S.p, on_change=S.set_p)\n"
    )
    assert _keys(source) == [
        "pages/probe.py::bar::input[aria_label=Search]",
        "pages/probe.py::bar::textarea[id=p]",
    ]


def test_scanner_ignores_client_owned_and_read_only_fields() -> None:
    source = (
        "import reflex as rx\n"
        "def bar():\n"
        "    rx.el.input(default_value=S.q, on_change=S.set_q.debounce(300))\n"
        "    rx.el.input(value=S.q, read_only=True)\n"
        "    rx.el.textarea(default_value=S.p, on_blur=S.set_p)\n"
        "    rx.input(value=S.q, on_change=S.set_q)\n"  # Radix: DebounceInput (D5)
    )
    assert _keys(source) == []


@pytest.mark.parametrize(
    "type_expr",
    [
        "'range'",
        "'checkbox'",
        "'radio'",
        "RANGE",
        "LOCAL",
        "rx.Var.create('range')",
        "rx.cond(S.flag, 'checkbox', 'radio')",
        "'radio' if S.flag else 'checkbox'",
    ],
)
def test_scanner_exempts_discrete_input_types(type_expr: str) -> None:
    source = (
        "import reflex as rx\n"
        "RANGE = 'range'\n"
        "def bar():\n"
        "    LOCAL = 'checkbox'\n"
        f"    rx.el.input(type={type_expr}, value=S.v, on_change=S.set_v)\n"
    )
    assert _keys(source) == []


@pytest.mark.parametrize(
    "type_expr",
    ["'text'", "'number'", "'search'", "S.kind", "rx.cond(S.flag, 'range', 'text')", "UNKNOWN"],
)
def test_scanner_flags_text_and_unresolvable_types(type_expr: str) -> None:
    source = (
        "import reflex as rx\n"
        "def bar():\n"
        f"    rx.el.input(type={type_expr}, id='f', value=S.v, on_change=S.set_v)\n"
    )
    assert _keys(source) == ["pages/probe.py::bar::input[id=f]"]


def test_scanner_follows_import_aliases_and_spread_kwargs() -> None:
    source = (
        "from reflex import el\n"
        "from reflex.el import textarea as ta\n"
        "PROPS = {'value': S.q, 'on_change': S.set_q}\n"
        "def bar():\n"
        "    el.input(name='q', **PROPS)\n"
        "    ta(**dict(value=S.p, on_change=S.set_p, class_name='prompt'))\n"
        "    input('builtin input is not a field')\n"
    )
    assert _keys(source) == [
        "pages/probe.py::bar::input[name=q]",
        "pages/probe.py::bar::textarea[class_name=prompt]",
    ]


def test_scanner_discriminator_order_and_expressions() -> None:
    source = (
        "import reflex as rx\n"
        "def field(field_id):\n"
        "    return rx.el.input(id=field_id, aria_label='x', value=S.v, on_change=S.set_v)\n"
        "def other():\n"
        "    rx.el.input(placeholder='Genre', class_name='minisel', value=S.v, on_change=S.s)\n"
        "    rx.el.input(value=S.v, on_change=S.s)\n"
    )
    assert _keys(source) == [
        "pages/probe.py::field::input[id={field_id}]",
        "pages/probe.py::other::input[class_name=minisel]",
        "pages/probe.py::other::input[?]",
    ]


def test_scanner_flags_returned_toggle_color_mode_in_states_only() -> None:
    source = (
        "import reflex as rx\n"
        "class S(rx.State):\n"
        "    def run(self, a):\n"
        "        if a == 'theme':\n"
        "            return rx.toggle_color_mode\n"
        "        return [rx.redirect('/'), rx.toggle_color_mode]\n"
        "    async def gen(self):\n"
        "        yield rx.toggle_color_mode\n"
    )
    assert _keys(source, "states/probe_state.py") == [
        f"states/probe_state.py::run::{TOGGLE_LABEL}",
        f"states/probe_state.py::run::{TOGGLE_LABEL}",
        f"states/probe_state.py::gen::{TOGGLE_LABEL}",
    ]
    # Components may use it as a client-side on_click.
    assert _keys(source, "components/probe.py") == []


def test_new_offender_outside_pending_fails_the_guard(tmp_path: Path) -> None:
    """A raw controlled field added to a tree is reported as unlisted."""
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "new_page.py").write_text(
        "import reflex as rx\n"
        "def new_page():\n"
        "    return rx.el.input(aria_label='Filter', value=S.f, on_change=S.set_f)\n",
        encoding="utf-8",
    )
    found = scan_tree(tmp_path)
    assert [o.key for o in unlisted(found, PENDING)] == [
        "pages/new_page.py::new_page::input[aria_label=Filter]"
    ]
    # Allowlisting it is the only way to silence it.
    assert unlisted(found, ["pages/new_page.py::new_page::input[aria_label=Filter]"]) == []


def test_converted_call_site_makes_its_entry_stale(tmp_path: Path) -> None:
    key = "pages/p.py::bar::input[id=q]"
    pages = tmp_path / "pages"
    pages.mkdir()
    target = pages / "p.py"
    target.write_text(
        "import reflex as rx\ndef bar():\n    rx.el.input(id='q', value=S.q, on_change=S.s)\n",
        encoding="utf-8",
    )
    assert stale(scan_tree(tmp_path), [key]) == []
    target.write_text(
        "import reflex as rx\ndef bar():\n    rx.el.input(id='q', default_value=S.q)\n",
        encoding="utf-8",
    )
    assert stale(scan_tree(tmp_path), [key]) == [key]


def test_each_pending_entry_covers_one_occurrence() -> None:
    source = (
        "import reflex as rx\n"
        "def bar():\n"
        "    rx.el.input(id='q', value=S.q, on_change=S.s)\n"
        "    rx.el.input(id='q', value=S.q, on_change=S.s)\n"
    )
    found = scan_source(source, "pages/p.py")
    assert len(unlisted(found, ["pages/p.py::bar::input[id=q]"])) == 1
