"""CSS and markup invariants for webui-zoom-and-narrow-fit (200% zoom and 320 CSS px reflow).

The live layout gate is ``check_reflow.py`` at the repo root (it needs a running UI). These
tests pin the stylesheet rules it depends on, so a regression shows up in ``pytest``.

``declarations(name, selector, media=None)`` returns the ``(property, value)`` pairs, in
source order, that ``assets/<name>`` declares for ``selector``: at the top level when
``media`` is None, else inside every top-level ``@media`` block whose query matches
``media`` (for example ``"(max-width: 640px)"``). A fallback pair such as
``overflow: hidden; overflow: clip`` comes back as two entries. ``declared(name, selector,
prop, media=None)`` narrows that to one property's values; ``declarations_in`` and
``media_block_in`` do the same on a CSS string.

Sections below: one per owner of the change (shell, pages, settings, carryover, sweep).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "assets"
REDESIGN = "redesign.css"
STYLES = "styles.css"

ZOOM_FIT_START = "/* === Zoom & narrow fit === */"
ZOOM_FIT_END = "/* === end zoom & narrow fit === */"
ZOOM_FIT_OWNERS = ("shell", "pages", "settings", "carryover", "sweep")

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
# A rule with no nested braces: "<selectors> { <declarations> }".
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECLARATION = re.compile(r"([-\w]+)\s*:\s*([^;]+?)\s*(?:;|$)")
_COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch)\(", re.I)


# ── Helpers ───────────────────────────────────────────────────────────


@lru_cache(maxsize=None)
def css_text(name: str) -> str:
    """The raw text of ``assets/<name>``."""
    return (ASSETS / name).read_text(encoding="utf-8")


def strip_comments(css: str) -> str:
    """``css`` without ``/* … */`` comments (keeps braces inside comments out of parsing)."""
    return _COMMENT.sub("", css)


def top_level_blocks(css: str) -> list[tuple[str, str]]:
    """``(prelude, body)`` for every top-level ``{…}`` block of comment-free ``css``.

    The prelude is a rule's selector list or an at-rule such as ``@media (…)``; an at-rule's
    body keeps its nested rules.
    """
    blocks: list[tuple[str, str]] = []
    depth = 0
    prelude_start = 0
    body_start = 0
    prelude = ""
    for index, char in enumerate(css):
        if char == "{":
            if depth == 0:
                prelude = css[prelude_start:index].strip()
                body_start = index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                blocks.append((prelude, css[body_start:index]))
                prelude_start = index + 1
        elif char == ";" and depth == 0:
            prelude_start = index + 1
    return blocks


def _normalise_media(query: str) -> str:
    query = query.strip().lower()
    if not query.startswith("@media"):
        query = "@media " + query
    return re.sub(r"\s+", "", query)


def _split_selectors(selector_text: str) -> list[str]:
    """Split a selector list on top-level commas (not inside ``:is(…)``/``:has(…)``)."""
    parts: list[str] = []
    depth = 0
    current = ""
    for char in selector_text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [" ".join(p.split()) for p in parts if p.strip()]


def media_block_in(css: str, media: str) -> str:
    """The bodies of every top-level ``@media`` block of ``css`` whose query is ``media``."""
    wanted = _normalise_media(media)
    return "\n".join(
        body
        for prelude, body in top_level_blocks(strip_comments(css))
        if _normalise_media(prelude) == wanted
    )


def media_block(name: str, media: str) -> str:
    """The bodies of every top-level ``@media`` block of ``assets/<name>`` matching ``media``."""
    return media_block_in(css_text(name), media)


def rules(css: str) -> list[tuple[list[str], str]]:
    """``(selectors, body)`` for every brace-free rule in comment-free ``css``."""
    return [(_split_selectors(sel), body) for sel, body in _RULE.findall(css)]


def declarations_in(css: str, selector: str, media: Optional[str] = None) -> list[tuple[str, str]]:
    """The ``(property, value)`` pairs that the stylesheet text ``css`` declares for ``selector``.

    Top level when ``media`` is None (rules inside at-rules are ignored), else inside the
    matching top-level ``@media`` blocks. ``selector`` must equal one entry of a rule's
    selector list (whitespace-normalised).
    """
    target = " ".join(selector.split())
    if media is None:
        scope = "\n".join(
            f"{prelude}{{{body}}}"
            for prelude, body in top_level_blocks(strip_comments(css))
            if not prelude.startswith("@")
        )
    else:
        scope = media_block_in(css, media)
    found: list[tuple[str, str]] = []
    for selectors, body in rules(scope):
        if target in selectors:
            found.extend(
                (prop.lower(), value.strip()) for prop, value in _DECLARATION.findall(body)
            )
    return found


def declarations(name: str, selector: str, media: Optional[str] = None) -> list[tuple[str, str]]:
    """The ``(property, value)`` pairs ``assets/<name>`` declares for ``selector``."""
    return declarations_in(css_text(name), selector, media)


def declared(name: str, selector: str, prop: str, media: Optional[str] = None) -> list[str]:
    """Every value ``assets/<name>`` gives ``prop`` on ``selector``, in source order."""
    return [value for p, value in declarations(name, selector, media) if p == prop.lower()]


def zoom_fit_section() -> str:
    """The raw ``/* === Zoom & narrow fit === */`` … end-marker section of redesign.css."""
    css = css_text(REDESIGN)
    start = css.index(ZOOM_FIT_START)
    end = css.index(ZOOM_FIT_END, start)
    return css[start : end + len(ZOOM_FIT_END)]


def color_literals(css: str) -> list[str]:
    """Colour literals used in declaration values (selectors such as ``#add`` are skipped)."""
    found: list[str] = []
    for _selectors, body in rules(strip_comments(css)):
        found.extend(_COLOR_LITERAL.findall(body))
    return found


# ── Scaffold ──────────────────────────────────────────────────────────


_SAMPLE_CSS = """
/* .a { color: red; } is a comment, not a rule */
:root { --rail-w: 76px; }
.a, .b > .c { overflow: hidden; overflow: clip; height: 100vh }
.d:has(> .e), .a:hover { width: 1px; }
@media (max-width: 640px) {
  .a { overflow: visible; }
  :root, :root[data-density] { --page-pad-x: 16px; }
}
@media (max-width: 768px) { .a { display: none !important; } }
@media (hover: none) { .b > .c { opacity: 1; } }
"""


def test_declaration_helper_on_sample_css() -> None:
    assert declarations_in(_SAMPLE_CSS, ".a") == [
        ("overflow", "hidden"),
        ("overflow", "clip"),
        ("height", "100vh"),
    ]
    assert declarations_in(_SAMPLE_CSS, ".b  >  .c") == declarations_in(_SAMPLE_CSS, ".a")
    assert declarations_in(_SAMPLE_CSS, ".d:has(> .e)") == [("width", "1px")]
    assert declarations_in(_SAMPLE_CSS, ".a", "(max-width: 640px)") == [("overflow", "visible")]
    assert declarations_in(_SAMPLE_CSS, ":root[data-density]", "@media (max-width:640px)") == [
        ("--page-pad-x", "16px")
    ]
    assert declarations_in(_SAMPLE_CSS, ".a", "(max-width: 768px)") == [
        ("display", "none !important")
    ]
    assert declarations_in(_SAMPLE_CSS, ".b > .c", "(hover: none)") == [("opacity", "1")]
    assert declarations_in(_SAMPLE_CSS, ".a", "(max-width: 480px)") == []
    assert declarations_in(_SAMPLE_CSS, ".e") == []


def test_declaration_helper_reads_both_stylesheets() -> None:
    assert ("display", "flex") in declarations(REDESIGN, ".shell")
    assert declared(STYLES, ".hide-mobile", "display", "(max-width: 768px)") == ["none !important"]
    assert declared(STYLES, ".hide-mobile", "display") == []


def test_color_literal_scan_ignores_selectors_and_comments() -> None:
    css = "/* rgb(1,2,3) #fff */ #add, .x { color: var(--fg-1); } .y { color: #abc; }"
    assert color_literals(css) == ["#abc"]


def test_zoom_fit_section_is_last_and_has_every_owner_block() -> None:
    css = css_text(REDESIGN)
    assert css.count(ZOOM_FIT_START) == 1
    assert css.count(ZOOM_FIT_END) == 1
    assert css.rstrip().endswith(ZOOM_FIT_END)
    section = zoom_fit_section()
    markers = [section.index(f"/* --- zoom-fit: {owner} --- */") for owner in ZOOM_FIT_OWNERS]
    assert markers == sorted(markers)


def test_zoom_fit_section_has_no_color_literals() -> None:
    assert color_literals(zoom_fit_section()) == []


# --- shell ---
# Rail, top bar and palette (webui-app-shell, webui-command-palette; design D1-D4, D9).

_NAVBAR = (
    Path(__file__).resolve().parents[2] / "src" / "plexmix" / "ui" / "components" / "navbar.py"
)
_COLORS = "colors_and_type.css"
_HEIGHT = "(max-height: 600px)"
_W768 = "(max-width: 768px)"
_W640 = "(max-width: 640px)"
_W480 = "(max-width: 480px)"
# The visually-hidden pattern (.sr-only): out of flow and clipped, still in the a11y tree.
_VISUALLY_HIDDEN = {
    "position": "absolute",
    "width": "1px",
    "height": "1px",
    "overflow": "hidden",
    "clip-path": "inset(50%)",
    "white-space": "nowrap",
}


def _shell_props(name: str, selector: str, media: Optional[str] = None) -> dict[str, str]:
    """The last value of every property ``selector`` declares (what the cascade keeps)."""
    return dict(declarations(name, selector, media))


def _navbar_module() -> ast.Module:
    return ast.parse(_NAVBAR.read_text(encoding="utf-8"), filename=str(_NAVBAR))


def _navbar_function(name: str) -> ast.FunctionDef:
    for node in _navbar_module().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"navbar.py has no function {name}")


def _navbar_calls(node: ast.AST, dotted: str) -> list[ast.Call]:
    """Every call to ``dotted`` (such as ``rx.tooltip``) inside ``node``."""
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and ast.unparse(call.func) == dotted
    ]


def _keyword(call: ast.Call, name: str) -> Optional[ast.expr]:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _px(value: str) -> float:
    assert value.endswith("px"), value
    return float(value[:-2])


def test_shell_clips_instead_of_scrolling() -> None:
    """Spec "Keyboard focus never displaces the shell": ``clip`` forbids programmatic
    scrolling, so focusing a clipped control can never scroll the shell (D2)."""
    assert declared(REDESIGN, ".shell", "overflow") == ["hidden", "clip"]
    for selector in ("#root", ".shell", ".main"):
        assert declared(REDESIGN, selector, "height") == ["100vh", "100dvh"], selector


def test_rail_scrolls_itself() -> None:
    """Spec "Rail controls stay reachable": the rail scrolls within itself (D2)."""
    rail = _shell_props(REDESIGN, ".rail")
    assert rail["overflow-y"] == "auto"
    assert rail["overflow-x"] == "hidden"
    assert rail["height"] == "100%"
    # The bar is the 6px ::-webkit-scrollbar (the shared gray-5 thumb). A non-auto
    # scrollbar-width or scrollbar-color switches it off in Chrome 121+ for the native thin
    # bar (11px with classic scrollbars), so those apply only without ::-webkit-scrollbar.
    assert declared(REDESIGN, ".rail::-webkit-scrollbar", "width") == ["6px"]
    assert "scrollbar-width" not in rail and "scrollbar-color" not in rail
    supports = [
        body
        for prelude, body in top_level_blocks(strip_comments(css_text(REDESIGN)))
        if " ".join(prelude.split()) == "@supports not selector(::-webkit-scrollbar)"
    ]
    assert any(
        declarations_in(f"x{{}}{body}", ".rail")
        == [("scrollbar-width", "thin"), ("scrollbar-color", "var(--pm-gray-5) transparent")]
        for body in supports
    )
    # Scroll containment where the rail scrolls; on the base rule Chromium would composite
    # the non-scrolling desktop rail and re-rasterize its shadows (not pixel-identical).
    assert "overscroll-behavior" not in rail
    assert declared(REDESIGN, ".rail", "overscroll-behavior", _HEIGHT) == ["contain"]


def test_rail_items_are_start_aligned_at_the_old_centred_offset() -> None:
    """D3: start-aligned items at a fixed inset; the 1px border keeps desktop geometry."""
    root = _shell_props(REDESIGN, ":root")
    rail_w, item_w = _px(root["--rail-w"]), _px(root["--rail-item-w"])
    assert (rail_w, item_w) == (76.0, 46.0)
    rail = _shell_props(REDESIGN, ".rail")
    assert rail["align-items"] == "flex-start"
    assert rail["border-right"].startswith("1px ")
    inset = rail["padding-inline-start"]
    assert inset == "calc((var(--rail-w) - var(--rail-item-w) - 1px) / 2)"
    # The old rail centred 46px items in its 75px content box: (75 - 46) / 2.
    assert (rail_w - item_w - 1) / 2 == (rail_w - 1 - 46) / 2 == 14.5
    for selector in (".rail-item", ".rail-group", ".rail-logo-link"):
        assert declared(REDESIGN, selector, "width") == ["var(--rail-item-w)"], selector
    assert declared(REDESIGN, ".rail-sep", "margin") == [
        "8px calc((var(--rail-item-w) - 28px) / 2)"
    ]
    # The active bar keeps its desktop x (-15px from the item) and never sits under a
    # classic scrollbar, which takes width from the inline end.
    assert declared(REDESIGN, ".rail-item.active::before", "left") == [
        "calc((var(--rail-item-w) - var(--rail-w)) / 2)"
    ]
    assert (item_w - rail_w) / 2 == -15


def test_compact_rail_on_short_viewports() -> None:
    """D1 height tier: the compact rail (about 439px) fits 720x450 without scrolling."""
    rail = _shell_props(REDESIGN, ".rail", _HEIGHT)
    assert (rail["gap"], rail["padding-top"], rail["padding-bottom"]) == ("3px", "10px", "10px")
    assert _shell_props(REDESIGN, ".rail-group", _HEIGHT) == {"gap": "2px"}
    assert _shell_props(REDESIGN, ".rail-item", _HEIGHT) == {"height": "36px"}
    assert _shell_props(REDESIGN, ".rail-sep", _HEIGHT) == {
        "margin-top": "4px",
        "margin-bottom": "4px",
    }
    assert _shell_props(REDESIGN, ".rail-logo", _HEIGHT) == {"width": "34px", "height": "34px"}
    # 36px items still clear the 24px target-size minimum (WCAG 2.5.8).
    assert _px(_shell_props(REDESIGN, ".rail-item", _HEIGHT)["height"]) >= 24
    # Natural height: padding, logo, three separators, seven items in three groups plus
    # Changelog and the theme toggle, and eight rail gaps.
    natural = 10 + 10 + 34 + 3 * (1 + 4 + 4) + (3 * 36 + 2 * 2) + 2 * (2 * 36 + 2)
    natural += 2 * 36 + 2 + 8 * 3
    assert natural <= 450


def test_narrow_rail_at_480px() -> None:
    """Spec "Narrow viewport": the rail takes no more than 56 CSS px at 320 wide."""
    root = _shell_props(REDESIGN, ":root", _W480)
    assert root == {"--rail-w": "56px", "--rail-item-w": "35px"}
    assert _px(root["--rail-w"]) <= 56
    assert _shell_props(REDESIGN, ".rail-logo", _W480) == {"width": "34px", "height": "34px"}
    for selector in (".topbar .cmd-trigger", ".topbar .icon-btn", ".topbar .avatar"):
        assert _shell_props(REDESIGN, selector, _W480) == {"width": "32px", "height": "32px"}


def test_no_rail_tip_remains() -> None:
    """The CSS-only rail labels are gone: they were clipped by the scrolling rail (D2)."""
    for name in (REDESIGN, STYLES, _COLORS):
        assert "rail-tip" not in css_text(name), name
    assert "rail-tip" not in _NAVBAR.read_text(encoding="utf-8")


def test_rail_controls_are_wrapped_in_radix_tooltips() -> None:
    """D2: every rail control's label is a portalled Radix tooltip beside the rail."""
    props = ast.literal_eval(_module_level(_navbar_module(), "_RAIL_TIP_PROPS"))
    assert props == {"side": "right", "side_offset": 10, "delay_duration": 150}

    item = _navbar_function("_rail_item")
    (tip,) = _navbar_calls(item, "rx.tooltip")
    content = _keyword(tip, "content")
    assert isinstance(content, ast.JoinedStr)
    assert ast.unparse(content) == "f'{label} · g {key}'"
    assert any(kw.arg is None and ast.unparse(kw.value) == "_RAIL_TIP_PROPS" for kw in tip.keywords)
    # The tooltip wraps the rail's own button (Radix Trigger asChild keeps it the trigger).
    (button,) = _navbar_calls(item, "rx.el.button")
    assert isinstance(tip.args[0], ast.Name)
    assigned = [
        node for node in ast.walk(item) if isinstance(node, ast.Assign) and node.value is button
    ]
    assert [ast.unparse(target) for node in assigned for target in node.targets] == [tip.args[0].id]

    rail = _navbar_function("_icon_rail")
    tips = sorted(_navbar_calls(rail, "rx.tooltip"), key=lambda t: (t.lineno, t.col_offset))
    contents = [ast.unparse(_keyword(t, "content") or ast.Constant(None)) for t in tips]
    assert contents == [
        "'Changelog'",
        "'Log out'",
        "rx.color_mode_cond(light='Dark mode', dark='Light mode')",
    ]
    for t in tips:
        (wrapped,) = t.args
        assert isinstance(wrapped, ast.Call) and ast.unparse(wrapped.func) == "rx.el.button"


def _module_level(module: ast.Module, name: str) -> ast.expr:
    for node in module.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name and node.value is not None:
                return node.value
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
    raise AssertionError(f"navbar.py has no module-level {name}")


def test_radix_tooltip_uses_plexmix_tokens() -> None:
    """Rail labels keep the old tip's look: Soot panel, Paper text, 12px/500 body face."""
    content = _shell_props(STYLES, ".rt-TooltipContent")
    assert content["background-color"] == "var(--pm-gray-12)"
    assert content["box-shadow"] == "var(--shadow-md)"
    assert content["border-radius"] == "var(--radius-sm)"
    assert _shell_props(STYLES, ".rt-TooltipText") == {
        "color": "var(--pm-gray-1)",
        "font-family": "var(--font-body)",
        "font-size": "12px",
        "font-weight": "500",
    }
    assert _shell_props(STYLES, ".rt-TooltipArrow") == {"fill": "var(--pm-gray-12)"}


def test_prefixed_backdrop_filter_comes_first() -> None:
    """Production builds (lightningcss) drop an unprefixed backdrop-filter that is
    followed by its -webkit- twin, so the glass lost its blur in `plexmix ui`."""
    for name, selector in (
        (REDESIGN, ".rail"),
        (REDESIGN, ".topbar"),
        (REDESIGN, ".rt-DialogOverlay:has(.cmdk)::before"),
        (STYLES, ".glass"),
    ):
        props = [prop for prop, _ in declarations(name, selector)]
        assert props.index("-webkit-backdrop-filter") < props.index("backdrop-filter"), (
            name,
            selector,
        )
    # And nowhere else in the stylesheets is the pair written the other way round.
    for name in (REDESIGN, STYLES, _COLORS):
        for selectors, body in rules(strip_comments(css_text(name))):
            props = [prop.lower() for prop, _ in _DECLARATION.findall(body)]
            if "backdrop-filter" in props and "-webkit-backdrop-filter" in props:
                assert props.index("-webkit-backdrop-filter") < props.index("backdrop-filter"), (
                    name,
                    selectors,
                )


def test_top_bar_title_truncates_instead_of_hiding_behind_controls() -> None:
    """D4 at every width: the breadcrumb shrinks first; the page name stays one line."""
    title = _shell_props(REDESIGN, ".topbar-title")
    assert (title["flex"], title["min-width"], title["overflow"]) == ("0 1 auto", "0", "hidden")
    page = _shell_props(REDESIGN, ".topbar-title .t")
    assert page["white-space"] == "nowrap"
    assert page["overflow"] == "hidden"
    assert page["text-overflow"] == "ellipsis"
    assert page["min-width"] == "0"
    for selector in (".topbar-title .brand", ".topbar-title .crumb-sep", ".cmd-trigger"):
        assert declared(REDESIGN, selector, "flex-shrink") == ["0"], selector
    for selector in (".icon-btn", ".avatar"):
        assert declared(REDESIGN, selector, "flex-shrink") == ["0"], selector


def test_compact_top_bar_at_768px() -> None:
    """Spec "Compact top bar": icon-only trigger that keeps its accessible name."""
    assert declared(REDESIGN, ".cmd-trigger", "min-width") == ["210px"]
    trigger = _shell_props(REDESIGN, ".cmd-trigger", _W768)
    assert trigger["min-width"] == "0"  # overrides the 210px desktop minimum
    assert (trigger["width"], trigger["padding"], trigger["border-radius"]) == ("36px", "0", "50%")
    assert trigger["justify-content"] == "center"
    assert _shell_props(REDESIGN, ".cmd-trigger .kbd", _W768) == {"display": "none"}
    label = _shell_props(REDESIGN, ".cmd-trigger .cmd-label", _W768)
    assert {k: label.get(k) for k in _VISUALLY_HIDDEN} == _VISUALLY_HIDDEN
    assert "display" not in label  # display: none would remove the trigger's name
    assert _shell_props(REDESIGN, ".topbar-title .s", _W768) == {"display": "none"}
    assert _shell_props(REDESIGN, ".topbar", _W768) == {"gap": "10px"}
    # Once the subtitle is gone, the page name may shrink and ellipsize.
    assert declared(REDESIGN, ".topbar-title .t", "flex-shrink") == ["0"]
    assert declared(REDESIGN, ".topbar-title .t", "flex-shrink", _W768) == ["1"]


def test_narrow_gutters_and_hidden_prefix_at_640px() -> None:
    """D1/D4 at <=640px: 16px gutters (also under compact density) and a visually hidden
    "PlexMix /" prefix that assistive technology still reads."""
    for selector in (":root", ":root[data-density]"):
        assert _shell_props(REDESIGN, selector, _W640) == {
            "--page-pad-x": "16px",
            "--page-pad-y": "20px",
        }, selector
    for selector in (".topbar-title .brand", ".topbar-title .crumb-sep"):
        hidden = _shell_props(REDESIGN, selector, _W640)
        assert {k: hidden.get(k) for k in _VISUALLY_HIDDEN} == _VISUALLY_HIDDEN, selector
        assert "display" not in hidden, selector
    assert declared(REDESIGN, ".topbar-title .t", "font-size", _W640) == ["17px"]


def test_top_bar_markup_hooks() -> None:
    """navbar.py: the page name carries its full name as a title (hover), and the
    trigger's label span has the ``cmd-label`` hook the compact rules hide."""
    bar = _navbar_function("_top_bar")
    page_names = [
        span
        for span in _navbar_calls(bar, "rx.el.span")
        if ast.unparse(_keyword(span, "class_name") or ast.Constant(None)) == "'t'"
    ]
    assert len(page_names) == 1
    title = _keyword(page_names[0], "title")
    assert title is not None
    assert ast.unparse(title) == "rx.match(path, *_PAGE_TITLES, 'PlexMix')"
    assert ast.unparse(page_names[0].args[0]) == ast.unparse(title)  # same text as shown

    palette = _navbar_function("_command_palette")
    labels = [
        span
        for span in _navbar_calls(palette, "rx.el.span")
        if span.args and ast.unparse(span.args[0]) == "'Search or jump to…'"
    ]
    assert len(labels) == 1
    assert ast.unparse(_keyword(labels[0], "class_name") or ast.Constant(None)) == "'cmd-label'"


def test_palette_fits_the_viewport() -> None:
    """Spec "Palette fits the viewport" (D9 port onto the Radix dialog hooks): the top
    offset is capped, the panel is bounded by the space left, and only the list scrolls."""
    padding = _shell_props(REDESIGN, ".rt-DialogScrollPadding:has(> .cmdk)")
    assert padding["padding-top"] == "min(12vh, 80px)"
    assert padding["padding-bottom"] == "16px"
    assert padding["margin-top"] == "0"
    panel = declarations(REDESIGN, ".rt-DialogContent.cmdk")
    heights = [value for prop, value in panel if prop == "max-height"]
    # vh fallback first, then dvh; offset + panel + bottom padding = the viewport, so the
    # dialog's own scroller never scrolls.
    assert heights == [
        "calc(100vh - min(12vh, 80px) - 16px)",
        "calc(100dvh - min(12vh, 80px) - 16px)",
    ]
    panel_props = dict(panel)
    assert (panel_props["display"], panel_props["flex-direction"]) == ("flex", "column")
    assert panel_props["max-width"] == "calc(100vw - 32px)"  # a margin on both sides
    assert panel_props["width"] == "560px"  # desktop width unchanged
    assert declared(REDESIGN, ".cmdk-input-row", "flex") == ["none"]
    assert declared(REDESIGN, ".cmdk .cmdk-foot", "flex") == ["none"]
    listbox = _shell_props(REDESIGN, ".cmdk-list")
    assert listbox["max-height"] == "380px"  # desktop list height unchanged
    assert (listbox["min-height"], listbox["flex"], listbox["overflow-y"]) == (
        "0",
        "0 1 auto",
        "auto",
    )


def test_palette_sizing_never_touches_unscoped_dialog_classes() -> None:
    """Only the palette's dialog is resized: the palette hooks name ``.cmdk``, and no rule
    restyles the shared Radix dialog wrappers without a class of our own."""
    hooks = [
        selector
        for selectors, _body in rules(strip_comments(css_text(REDESIGN)))
        for selector in selectors
        if "cmdk" in selector and re.search(r"\.rt-Dialog(?:ScrollPadding|Content)\b", selector)
    ]
    assert sorted(set(hooks)) == [".rt-DialogContent.cmdk", ".rt-DialogScrollPadding:has(> .cmdk)"]
    for name in (REDESIGN, STYLES):
        for selectors, _body in rules(strip_comments(css_text(name))):
            for selector in selectors:
                if re.search(r"\.rt-(?:Base)?Dialog(?:Scroll|ScrollPadding|Content)\b", selector):
                    own = re.findall(r"\.(?!rt-)[A-Za-z][\w-]*", selector)
                    assert own, (name, selector)


# --- pages ---
# Grids, Generator fit, Library table, Tagging rows and hover parity (webui-reflow,
# webui-generator-showpiece; design D5-D8, D10).

_PAGES_SRC = Path(__file__).resolve().parents[2] / "src" / "plexmix" / "ui"
_P640 = "(max-width: 640px)"
_P480 = "(max-width: 480px)"


def _all_rules(name: str) -> list[tuple[list[str], str]]:
    """Every innermost rule of ``assets/<name>``, top level and inside at-rules."""
    return rules(strip_comments(css_text(name)))


def _rule_props(name: str, selector: str, media: Optional[str] = None) -> dict[str, str]:
    """The last value of each property ``selector`` declares (what the cascade keeps)."""
    return dict(declarations(name, selector, media))


def _props(body: str) -> dict[str, str]:
    return {prop.lower(): value.strip() for prop, value in _DECLARATION.findall(body)}


def _page_module(*parts: str) -> ast.Module:
    path = _PAGES_SRC.joinpath(*parts)
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _string_kw(call: ast.Call, name: str) -> Optional[str]:
    value = next((kw.value for kw in call.keywords if kw.arg == name), None)
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def _calls_in(module: ast.Module, function: str) -> list[ast.Call]:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return [c for c in ast.walk(node) if isinstance(c, ast.Call)]
    raise AssertionError(f"no function {function}")


# 4.2 Intrinsic grids (D5)

_GRID_TEMPLATES = {
    ".stat-grid": "repeat(auto-fit, minmax(min(190px, 100%), 1fr))",
    ".qa-grid": "repeat(auto-fit, minmax(min(300px, 100%), 1fr))",
    ".pl-grid": "repeat(auto-fill, minmax(min(248px, 100%), 1fr))",
}


def test_card_and_tile_grids_are_intrinsic() -> None:
    """Spec "Card and tile grids reflow": as many columns as fit, down to one."""
    for selector, template in _GRID_TEMPLATES.items():
        values = declared(REDESIGN, selector, "grid-template-columns")
        assert values == [template], selector
        assert re.search(r"\bauto-(?:fit|fill)\b", values[0]), selector
        assert "minmax(min(" in values[0], selector


def test_no_grid_template_hard_codes_a_column_count() -> None:
    """``repeat(<n>, 1fr)`` (the old 4-up/2-up) never comes back, at any width."""
    for name in (REDESIGN, STYLES):
        for selectors, body in _all_rules(name):
            if not any(s in selectors for s in _GRID_TEMPLATES):
                continue
            value = _props(body).get("grid-template-columns", "")
            assert not re.search(r"repeat\(\s*\d+\s*,\s*1fr\s*\)", value), (name, selectors, value)


# 5.4 Generator fit (webui-generator-showpiece, D6)


def test_hero_glow_is_capped_to_the_hero() -> None:
    assert declared(REDESIGN, ".gen-hero-glow", "width") == ["min(760px, 100%)"]
    # Capped, not clipped: the hero holds the Generate button's glow (DESIGN.md).
    assert "overflow" not in dict(declarations(REDESIGN, ".gen-hero"))
    assert "overflow-x" not in dict(declarations(REDESIGN, ".gen-hero"))


def test_thinking_pill_wraps() -> None:
    props = _rule_props(REDESIGN, ".think-query")
    assert props["white-space"] == "normal"
    assert props["max-width"] == "100%"
    assert props["text-align"] == "start"
    for name in (REDESIGN, STYLES):
        for selectors, body in _all_rules(name):
            if any("think-query" in s for s in selectors):
                assert _props(body).get("white-space") != "nowrap", (name, selectors)


def test_long_prompt_is_clamped_to_two_lines() -> None:
    for selector in (".think-query b", ".result-meta h1"):
        props = declarations(REDESIGN, selector)
        assert ("-webkit-line-clamp", "2") in props, selector
        assert ("display", "-webkit-box") in props, selector
        assert ("-webkit-box-orient", "vertical") in props, selector
        assert ("overflow", "hidden") in props, selector
        assert ("min-width", "0") in props, selector
        # break-word first as the fallback, anywhere last (it wins where supported).
        wraps = declared(REDESIGN, selector, "overflow-wrap")
        assert wraps == ["break-word", "anywhere"], selector


def test_generator_rows_wrap_at_640px() -> None:
    for selector in (".prompt-foot", ".seg", ".result-head", ".trk"):
        assert declared(REDESIGN, selector, "flex-wrap", _P640) == ["wrap"], selector
    assert declared(REDESIGN, ".gen-title", "font-size", _P640) == ["34px"]
    assert declared(REDESIGN, ".adv-panel", "grid-template-columns", _P640) == ["1fr"]
    # Segments size to their label (basis auto) or a row of them could never wrap.
    for selector in (".seg button", ".seg label"):
        assert declared(REDESIGN, selector, "flex", _P640) == ["1 1 auto"], selector
    assert _rule_props(REDESIGN, ".result-cover", _P640) == {"width": "88px", "height": "88px"}
    assert declared(REDESIGN, ".result-meta", "flex-basis", _P640) == ["100%"]
    album = _rule_props(REDESIGN, ".trk .talbum", _P640)
    assert album["order"] == "1"
    assert album["flex"] == "1 0 100%"
    assert album["padding-inline-start"] == "calc(26px + 38px + 28px)"


def test_track_rows_keep_the_title_readable_at_480px() -> None:
    """Below 480px line one holds number, title and remove; album and duration share
    line two, and the decorative swatch (the art box after the number) steps aside."""
    assert declared(REDESIGN, ".trk > .tnum + *", "display", _P480) == ["none"]
    assert declared(REDESIGN, ".trk .tdur", "order", _P480) == ["2"]
    album = _rule_props(REDESIGN, ".trk .talbum", _P480)
    assert album["flex"] == "1 1 calc(100% - 5rem)"
    assert album["padding-inline-start"] == "calc(26px + 14px)"


def test_generator_prompt_headings_carry_the_full_prompt_as_title() -> None:
    """The clamped pill and results heading keep the whole prompt on hover (5.2)."""
    module = _page_module("pages", "generator.py")
    for function, marker in (("_thinking_stage", "think-query"), ("_results_stage", None)):
        h1s = [
            c
            for c in _calls_in(module, function)
            if ast.unparse(c.func) == "rx.el.h1" and _string_kw(c, "id") == "main-heading"
        ]
        assert len(h1s) == 1, function
        h1 = h1s[0]
        if marker:
            assert _string_kw(h1, "class_name") == marker
        title = next((kw.value for kw in h1.keywords if kw.arg == "title"), None)
        assert title is not None and ast.unparse(title) == "GeneratorState.mood_query", function


# 6.2 Library table scrolls inside its region (D7)


def test_library_table_scrolls_inside_its_own_box() -> None:
    props = _rule_props(REDESIGN, ".tbl-scroll")
    assert props["overflow-x"] == "auto"
    # Contains the cells' absolutely positioned .sr-only labels and checkbox inputs.
    assert props["position"] == "relative"
    assert declared(REDESIGN, ".tbl-scroll > .tbl", "min-width") == ["36rem"]
    # The shared wrapper still clips: the Generator results' 2px hover nudge must not
    # flash a scrollbar.
    assert declared(REDESIGN, ".tbl-wrap", "overflow") == ["hidden"]
    assert "overflow-x" not in dict(declarations(REDESIGN, ".tbl-wrap"))


def test_library_table_scrollbar_stays_visible() -> None:
    """Spec "a visible scroll indication": ``scrollbar-width`` would swap the classic
    ::-webkit-scrollbar bar for the native overlay one (hidden at rest on macOS), so it
    only applies where ::-webkit-scrollbar is unsupported."""
    assert declared(REDESIGN, ".tbl-scroll", "scrollbar-width") == []
    assert declared(REDESIGN, ".tbl-scroll::-webkit-scrollbar", "height") == ["8px"]
    supports = [
        body
        for prelude, body in top_level_blocks(strip_comments(css_text(REDESIGN)))
        if " ".join(prelude.split()) == "@supports not selector(::-webkit-scrollbar)"
    ]
    assert any(
        ("scrollbar-width", "thin") in declarations_in(f"x{{}}{body}", ".tbl-scroll")
        for body in supports
    )


def test_long_cell_values_wrap_inside_their_cell() -> None:
    assert declared(REDESIGN, ".tbl td", "overflow-wrap") == ["break-word", "anywhere"]


def test_only_the_library_table_gets_the_scroll_modifier() -> None:
    library = _page_module("pages", "library.py")
    wrappers = [
        _string_kw(c, "class_name") for c in _calls_in(library, "_track_table") if c.keywords
    ]
    assert "tbl-wrap tbl-scroll" in wrappers
    generator = _page_module("pages", "generator.py")
    results = [_string_kw(c, "class_name") for c in _calls_in(generator, "_results_stage")]
    assert "tbl-wrap trk-list" in results
    assert not any(r and "tbl-scroll" in r for r in results)


# 7.1-7.3 Recently tagged rows (D8)


def test_format_tags_for_display() -> None:
    from plexmix.ui.utils.helpers import format_tags_for_display

    assert format_tags_for_display("indie pop,dreamy,atmospheric") == (
        "indie pop, dreamy, atmospheric"
    )
    assert format_tags_for_display(" a , ,b,") == "a, b"
    assert format_tags_for_display("") == ""
    assert format_tags_for_display("lo-fi") == "lo-fi"


def test_format_tags_for_display_is_tolerant() -> None:
    """Spec "Comma-joined tags without spaces": each non-empty tag once, stored order."""
    from plexmix.ui.utils.helpers import format_tags_for_display

    assert format_tags_for_display("chill, focus,  study ") == "chill, focus, study"
    assert format_tags_for_display(",,, ,") == ""
    assert format_tags_for_display("b,a,b,c,a") == "b, a, c"
    # Case and inner spaces are kept as stored.
    assert format_tags_for_display("Indie Pop,indie pop") == "Indie Pop, indie pop"


def test_tag_row_classes_wrap_without_crushing_the_title() -> None:
    assert _rule_props(REDESIGN, ".tag-row") == {
        "display": "flex",
        "flex-wrap": "wrap",
        "align-items": "center",
        "gap": "8px 14px",
    }
    assert _rule_props(REDESIGN, ".tag-row-title") == {"flex": "1 1 12rem", "min-width": "0"}
    tags = declarations(REDESIGN, ".tag-row-tags")
    assert ("min-width", "0") in tags
    assert ("max-width", "40%") in tags
    assert [v for p, v in tags if p == "overflow-wrap"] == ["break-word", "anywhere"]
    narrow = _rule_props(REDESIGN, ".tag-row-tags", _P640)
    assert narrow == {"flex-basis": "100%", "max-width": "none"}


def test_tagged_row_uses_the_classes_and_the_display_string() -> None:
    module = _page_module("pages", "tagging.py")
    calls = _calls_in(module, "_tagged_row")
    classes = {_string_kw(c, "class_name") for c in calls}
    assert {"tag-row", "tag-row-title", "tag-row-tags fg2"} <= classes
    span = next(c for c in calls if _string_kw(c, "class_name") == "tag-row-tags fg2")
    assert [ast.unparse(a) for a in span.args] == ["track['tags_display']"]
    # The row layout moved to CSS: no inline display/flex left on the row or title box.
    for call in calls:
        style = next((kw.value for kw in call.keywords if kw.arg == "style"), None)
        if isinstance(style, ast.Dict):
            keys = {k.value for k in style.keys if isinstance(k, ast.Constant)}
            assert not keys & {"display", "flex", "gap", "alignItems"}, ast.unparse(style)
    # Edit still prefills the editor with the stored tags.
    start_edit = _calls_in(module, "_start_edit")
    assert any("track['tags']" in ast.unparse(c) for c in start_edit)


# 9.2 Hover parity (D10)


def test_remove_button_shows_without_hover_capability() -> None:
    assert declared(REDESIGN, ".trk .trm", "opacity", "(hover: none)") == ["1"]
    # Keyboard focus in the row still reveals it (pre-existing rule, kept).
    focus = declarations(REDESIGN, ".trk:focus-within .trm")
    assert ("opacity", "1") in focus


def test_playlist_card_overlay_shows_on_keyboard_focus() -> None:
    assert declared(REDESIGN, ".pl-card:focus-visible .pl-overlay", "opacity") == ["1"]
    assert declared(REDESIGN, ".pl-card:hover .pl-overlay", "opacity") == ["1"]
    # Not :focus-within: the card is the button itself, so it would also match mouse and
    # programmatic focus and leave the overlay up after a click (review fix).
    for name in (REDESIGN, STYLES):
        for selectors, _body in _all_rules(name):
            assert not any(".pl-card:focus-within" in s for s in selectors), (name, selectors)


def test_no_dead_play_or_grip_rules_remain() -> None:
    for name in (REDESIGN, STYLES):
        for selectors, _body in _all_rules(name):
            for selector in selectors:
                assert not re.search(r"\.(?:grip|play)\b", selector), (name, selector)


def test_hover_never_hides_the_track_number() -> None:
    hiding = {("display", "none"), ("opacity", "0"), ("visibility", "hidden")}
    for name in (REDESIGN, STYLES):
        for selectors, body in _all_rules(name):
            for selector in selectors:
                if ":hover" in selector and ".tnum" in selector:
                    assert not set(_props(body).items()) & hiding, (name, selector)


# Computed layout: the app stylesheets (app order) in headless Chromium, served through
# request interception (no port is bound, nothing reaches the network), with markup that
# mirrors the page modules. Each fixture fixes its own container width, so the results do
# not depend on the shell's rail and gutters. Skipped when Playwright or its Chromium build
# is missing (CI installs neither).

_LAYOUT_ORIGIN = "http://reflow-pages.test"
_LAYOUT_SHEETS = ("styles.css", "colors_and_type.css", "redesign.css")
_TOKEN_70 = "the_quick_brown_fox_jumps_over_the_lazy_dog_extended_clubmix_final.mp3"


def _layout_page(body: str, width: int, height: int, **context: Any) -> Iterator[Any]:
    """Yield a page showing ``body`` inside the shell's scroll container."""
    sync_api = pytest.importorskip("playwright.sync_api")
    doc = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        + "".join(f"<link rel='stylesheet' href='/{n}'>" for n in _LAYOUT_SHEETS)
        + "</head><body><div class='shell'><div class='main'>"
        + f"<main class='scroll' id='main-content'>{body}</main></div></div></body></html>"
    )

    def serve(route: Any) -> None:
        url = route.request.url
        name = url.rsplit("/", 1)[-1]
        if not url.startswith(_LAYOUT_ORIGIN) or name.endswith(".woff2"):
            route.abort()
        elif name in _LAYOUT_SHEETS:
            route.fulfill(body=css_text(name), content_type="text/css")
        else:
            route.fulfill(body=doc, content_type="text/html")

    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser build not installed
            pytest.skip(f"Chromium for Playwright is unavailable: {type(exc).__name__}")
        try:
            ctx = browser.new_context(viewport={"width": width, "height": height}, **context)
            page = ctx.new_page()
            page.route("**/*", serve)
            page.goto(f"{_LAYOUT_ORIGIN}/fixture")
            yield page
        finally:
            browser.close()


def _scroll_overflow(page: Any) -> float:
    return float(
        page.evaluate(
            "() => { const s = document.querySelector('.scroll');"
            " return s.scrollWidth - s.clientWidth; }"
        )
    )


def test_layout_library_table_scrolls_inside_its_region() -> None:
    """Spec "Narrow width keeps every column reachable" and "Long unbroken value"."""
    chips = "<span class='enr-set'><span class='enr enr-on'></span></span>"
    cells = (
        "<td><input type='checkbox' aria-label='Select'></td><td>{t}</td>"
        "<td class='fg2'>{a}</td><td class='fg3'>{al}</td>"
        f"<td>{chips}<span class='sr-only'>Tagged, embedded</span></td>"
    )
    rows = cells.format(t=_TOKEN_70, a="Artist", al="Album") + "".join(
        cells.format(t=f"Song {i}", a="Artist", al="Album") for i in range(3)
    )
    body = (
        "<div class='page'><div class='tbl-wrap tbl-scroll' style='width:100%'>"
        "<table class='tbl'><thead><tr><th></th><th>Title</th><th>Artist</th><th>Album</th>"
        f"<th>Enrichment</th></tr></thead><tbody><tr>{rows}</tr></tbody></table></div></div>"
    ).replace("</td><td><input", "</td></tr><tr><td><input")
    for page in _layout_page(body, 390, 844):
        # The page never scrolls sideways, even for the cells' absolutely positioned
        # .sr-only labels (their containing block is the scroller, not .scroll).
        assert _scroll_overflow(page) <= 0.5
        info = page.evaluate(
            """() => {
              const wrap = document.querySelector('.tbl-scroll');
              const td = wrap.querySelector('tbody td:nth-child(2)');
              const line = parseFloat(getComputedStyle(td).lineHeight) || 20;
              const out = {overflow: wrap.scrollWidth - wrap.clientWidth,
                           tokenLines: td.getBoundingClientRect().height / line};
              wrap.scrollLeft = wrap.scrollWidth;
              const wr = wrap.getBoundingClientRect();
              out.album = [...wrap.querySelectorAll('th')].filter((th) =>
                th.textContent === 'Album').map((th) => {
                  const r = th.getBoundingClientRect();
                  return r.left >= wr.left - 0.5 && r.right <= wr.right + 0.5; })[0];
              return out; }"""
        )
        assert info["overflow"] > 0  # the table is wider than its box: it scrolls there
        assert info["album"] is True  # ...and the Album column is reachable
        assert info["tokenLines"] > 1.5  # the 70-character token wrapped inside its cell


def _tag_row(title: str, tags: str) -> str:
    return (
        "<div style='padding:12px 14px'><div class='tag-row'><div class='tag-row-title'>"
        f"<div>{title}</div><div class='fg3'>Artist</div></div>"
        f"<span class='tag-row-tags fg2'>{tags}</span>"
        "<button class='btn btn-sm btn-soft' type='button'>Edit</button></div></div>"
    )


def test_layout_tagging_rows_keep_the_title_readable() -> None:
    """Spec "Long tag list in Recently tagged" and "Very narrow tagging row"."""
    from plexmix.ui.utils.helpers import format_tags_for_display

    tags = format_tags_for_display(
        "dreamy,atmospheric,melancholic,late-night,cinematic,slow-building,synth-heavy,"
        "nostalgic,introspective,rainy-day,hopeful,downtempo,ambient,lo-fi,night-drive"
    )
    rows = _tag_row("Symphony No. 9 in D Minor, Op. 125", tags) + _tag_row(_TOKEN_70, _TOKEN_70)
    measure = """(sel) => [...document.querySelectorAll('.tag-row')].map((row) => {
      const box = document.querySelector(sel).getBoundingClientRect();
      const parts = [...row.children].map((c) => c.getBoundingClientRect());
      return {title: parts[0].width, inside: parts.every((r) =>
        r.width > 0 && r.left >= box.left - 0.5 && r.right <= box.right + 0.5)}; })"""
    # About 232 CSS px of content: a 320 px viewport after the narrow rail and gutters.
    for page in _layout_page(f"<div class='card' style='width:232px'>{rows}</div>", 320, 640):
        for row in page.evaluate(measure, ".card"):
            assert row["inside"], row  # title, tags and Edit all on screen
            assert row["title"] >= 150, row  # not crushed to a few characters per line
    # Desktop-ish width: tags take at most 40% and the title column stays wide.
    for page in _layout_page(f"<div class='card' style='width:860px'>{rows}</div>", 1024, 768):
        for row in page.evaluate(measure, ".card"):
            assert row["inside"], row
            assert row["title"] >= 180, row


def _track_rows() -> str:
    row = (
        "<li class='trk'><span class='tnum'>{n}</span>"
        "<div style='width:38px;height:38px;flex-shrink:0'></div>"
        "<div class='tinfo'><div class='ttitle'>{t}</div><div class='tartist'>Artist</div></div>"
        "<span class='talbum'>{al}</span><span class='tdur'>3:42</span>"
        "<button class='icon-btn trm' type='button' aria-label='Remove'>x</button></li>"
    )
    items = row.format(n=1, t="Symphony No. 9 in D Minor", al=_TOKEN_70) + row.format(
        n=2, t=_TOKEN_70, al="Album"
    )
    return f"<ol class='tbl-wrap trk-list' style='padding:6px;width:232px'>{items}</ol>"


def test_layout_track_rows_at_320_show_every_part() -> None:
    """Spec "Results at 320 CSS px": title, artist, album, duration and remove."""
    for page in _layout_page(_track_rows(), 320, 640):
        rows = page.evaluate(
            """() => [...document.querySelectorAll('.trk')].map((row) => {
              const box = row.getBoundingClientRect(), out = {};
              for (const s of ['.ttitle', '.tartist', '.talbum', '.tdur', '.trm']) {
                const r = row.querySelector(s).getBoundingClientRect();
                out[s] = {w: r.width, top: r.top, inside: r.width > 0 &&
                  r.left >= box.left - 0.5 && r.right <= box.right + 0.5};
              }
              return out; })"""
        )
        for row in rows:
            assert all(part["inside"] for part in row.values()), row
            assert row[".ttitle"]["w"] >= 90, row  # readable, not a sliver
            assert row[".talbum"]["top"] > row[".tartist"]["top"], row  # album on its own line
            assert row[".tdur"]["top"] == row[".talbum"]["top"], row


def test_layout_remove_button_without_hover_capability() -> None:
    """Spec "Remove-track button on a touch device"."""
    opacity = "() => [...document.querySelectorAll('.trm')].map((b) => getComputedStyle(b).opacity)"
    for page in _layout_page(_track_rows(), 390, 844, has_touch=True, is_mobile=True):
        assert page.evaluate("matchMedia('(hover: none)').matches") is True
        assert page.evaluate(opacity) == ["1", "1"]
    for page in _layout_page(_track_rows(), 1024, 768):
        assert page.evaluate(opacity) == ["0", "0"]  # pointer devices still reveal on hover
        page.hover(".trk")
        tnum = "() => getComputedStyle(document.querySelector('.trk .tnum')).display"
        assert page.evaluate(tnum) != "none"


# --- settings ---

_REPO = ASSETS.parent
_SETTINGS_PAGE = _REPO / "src" / "plexmix" / "ui" / "pages" / "settings.py"
_SETTINGS_STATE = _REPO / "src" / "plexmix" / "ui" / "states" / "settings_state.py"
_FULL_SECTION_NAMES = [
    "Plex Server",
    "AI Provider",
    "Embeddings",
    "Audio Analysis",
    "MusicBrainz",
    "Advanced",
    "Appearance",
]
# What the old narrow-only list (_mobile_tabs) showed instead of the section names.
_ABBREVIATED_TAB_LABELS = {"MB", "Adv", "Look", "Embed"}
_NARROW = "(max-width: 768px)"
# Design D11: Radix hides the tab list's scrollbar; at <=768px a thin one shows the overflow.
# scrollbar-width is reset to auto (not thin): in Chrome 121+ a non-auto value switches off
# the 6px ::-webkit-scrollbar bar for the native overlay one, invisible at rest on macOS.
_TAB_STRIP_RULES = {
    ".settings-tabs .rt-BaseTabList": [
        ("scrollbar-width", "auto"),
        ("scroll-snap-type", "x proximity"),
        ("scroll-padding-inline", "8px"),
    ],
    ".settings-tabs .rt-BaseTabList::-webkit-scrollbar": [("display", "block"), ("height", "6px")],
    ".settings-tabs .rt-TabsTrigger": [("scroll-snap-align", "start")],
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _calls_to(node: ast.AST, dotted: str) -> list[ast.Call]:
    """Every call to ``dotted`` (for example ``rx.tabs.trigger``) inside ``node``."""
    return [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and ast.unparse(call.func) == dotted
    ]


def _module_values(module: ast.Module) -> dict[str, ast.expr]:
    """The value of every module-level ``name = …`` / ``name: T = …`` assignment."""
    values: dict[str, ast.expr] = {}
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                values[node.target.id] = node.value
    return values


def _section_labels() -> dict[str, str]:
    """``SECTION_LABELS`` from settings_state.py, read without importing the state module."""
    labels = ast.literal_eval(_module_values(_parse(_SETTINGS_STATE))["SECTION_LABELS"])
    assert isinstance(labels, dict)
    return {str(key): str(value) for key, value in labels.items()}


def _tab_list_builder(module: ast.Module) -> ast.FunctionDef:
    """The one top-level function of settings.py that renders a tab list (no second,
    narrow-only list such as the old ``_mobile_tabs``)."""
    builders = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and _calls_to(node, "rx.tabs.list")
    ]
    assert len(builders) == 1, [builder.name for builder in builders]
    return builders[0]


def _label_text(node: ast.expr, labels: dict[str, str]) -> str:
    """A trigger label written as a string literal or as ``SECTION_LABELS["<id>"]``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.Subscript)
        and ast.unparse(node.value) == "SECTION_LABELS"
        and isinstance(node.slice, ast.Constant)
    ):
        return labels[str(node.slice.value)]
    raise AssertionError(f"cannot resolve the tab label {ast.unparse(node)}")


def _trigger_labels(module: ast.Module) -> list[str]:
    """The visible label of every Settings tab trigger, in tab order.

    The triggers come from one comprehension over a module-level list of tuples
    (``for value, icon, label, _ in _SECTIONS``) and show the label as ``rx.el.span(label)``.
    """
    builder = _tab_list_builder(module)
    comprehensions = [
        node
        for node in ast.walk(builder)
        if isinstance(node, ast.ListComp) and _calls_to(node.elt, "rx.tabs.trigger")
    ]
    assert len(comprehensions) == 1, "tab triggers are no longer one comprehension: update"
    (generator,) = comprehensions[0].generators
    assert isinstance(generator.target, ast.Tuple) and isinstance(generator.iter, ast.Name)
    names = [ast.unparse(element) for element in generator.target.elts]
    spans = _calls_to(comprehensions[0].elt, "rx.el.span")
    label_names = [arg.id for span in spans for arg in span.args if isinstance(arg, ast.Name)]
    assert len(label_names) == 1, label_names
    index = names.index(label_names[0])
    rows = _module_values(module)[generator.iter.id]
    assert isinstance(rows, ast.List)
    labels = _section_labels()
    found: list[str] = []
    for row in rows.elts:
        assert isinstance(row, ast.Tuple)
        found.append(_label_text(row.elts[index], labels))
    return found


def test_settings_tab_triggers_use_full_section_names() -> None:
    """Spec "Narrow Settings section tabs are readable": one tab list at every width,
    labelled with the same full names as the wide layout."""
    assert _trigger_labels(_parse(_SETTINGS_PAGE)) == _FULL_SECTION_NAMES


def test_settings_tab_list_has_no_abbreviated_labels() -> None:
    module = _parse(_SETTINGS_PAGE)
    builder = _tab_list_builder(module)
    values = _module_values(module)
    referenced = {node.id for node in ast.walk(builder) if isinstance(node, ast.Name)}
    sources: list[ast.AST] = [builder, *(values[name] for name in referenced if name in values)]
    literals = {
        node.value.strip()
        for source in sources
        for node in ast.walk(source)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    literals |= set(_section_labels().values())
    assert literals.isdisjoint(_ABBREVIATED_TAB_LABELS), literals & _ABBREVIATED_TAB_LABELS


def test_narrow_settings_tab_strip_shows_its_overflow() -> None:
    """<=768px: a thin, visible scrollbar replaces Radix's hidden one, and triggers snap so
    a focused tab scrolls fully into view."""
    for selector, expected in _TAB_STRIP_RULES.items():
        assert declarations(STYLES, selector, _NARROW) == expected, selector


def test_settings_tab_strip_rules_extend_the_narrow_stacking_block_only() -> None:
    """The overrides live in keyboard-and-semantics' <=768px ``.settings-tabs`` block, so the
    desktop section column is unchanged."""
    narrow_blocks = [
        body
        for prelude, body in top_level_blocks(strip_comments(css_text(STYLES)))
        if _normalise_media(prelude) == _normalise_media(_NARROW)
        and declarations_in(body, '.rt-TabsRoot.settings-tabs[data-orientation="vertical"]')
    ]
    assert len(narrow_blocks) == 1
    for selector, expected in _TAB_STRIP_RULES.items():
        assert declarations_in(narrow_blocks[0], selector) == expected, selector
        assert declarations(STYLES, selector) == [], selector
        assert declarations(REDESIGN, selector) == [], selector


def test_settings_tab_strip_thin_bar_only_without_webkit_scrollbars() -> None:
    """Browsers without ``::-webkit-scrollbar`` (Firefox) get the thin standard bar with the
    shared gray-5 thumb; Chrome and Safari keep the always-visible 6px webkit bar, which a
    non-auto ``scrollbar-width`` or ``scrollbar-color`` would switch off."""
    fallback = [
        body
        for prelude, body in top_level_blocks(strip_comments(css_text(STYLES)))
        if " ".join(prelude.split()) == "@supports not selector(::-webkit-scrollbar)"
        and ".settings-tabs" in body
    ]
    assert len(fallback) == 1
    assert declarations_in(fallback[0], ".settings-tabs .rt-BaseTabList", _NARROW) == [
        ("scrollbar-width", "thin"),
        ("scrollbar-color", "var(--pm-gray-5) transparent"),
    ]
    narrow = media_block(STYLES, _NARROW)
    assert "scrollbar-color" not in narrow
    assert ("scrollbar-width", "thin") not in declarations(
        STYLES, ".settings-tabs .rt-BaseTabList", _NARROW
    )


# Stale CSS (task 11): an offset for a 220px sidebar that no longer exists, and two layout
# tokens that nothing reads.
_STALE_CSS_NAMES = (".floating-action-bar", "--navbar-width", "--content-max-width")


def test_stale_floating_bar_and_layout_tokens_are_removed() -> None:
    for sheet in sorted(ASSETS.glob("*.css")):
        text = sheet.read_text(encoding="utf-8")
        for name in _STALE_CSS_NAMES:
            assert name not in text, f"{name} is still in assets/{sheet.name}"
    assert "Floating Action Bar" not in css_text(STYLES)
    assert "/* --- Layout --- */" not in css_text("colors_and_type.css")


def test_no_code_references_the_removed_css() -> None:
    paths = [*(_REPO / "src").rglob("*.py"), *(_REPO / "plexmix_ui").rglob("*.py")]
    paths += sorted(ASSETS.glob("*.js"))
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for name in _STALE_CSS_NAMES:
            assert name.lstrip(".") not in text, f"{name} is referenced in {path}"


# --- carryover ---

_CARRYOVER_NATIVE_FOCUS = (
    "input[type=checkbox]:focus-visible",
    "input[type=radio]:focus-visible",
)


def _carryover_block() -> str:
    """The raw ``/* --- zoom-fit: carryover --- */`` sub-block of the zoom-fit section."""
    section = zoom_fit_section()
    start = section.index("/* --- zoom-fit: carryover --- */")
    return section[start : section.index("/* --- zoom-fit: sweep --- */", start)]


def test_native_checkbox_and_radio_focus_ring_matches_the_shared_rule() -> None:
    # The shared rule every custom control uses (redesign.css, "One focus treatment").
    outline = declared(REDESIGN, ".btn:focus-visible", "outline")
    offset = declared(REDESIGN, ".btn:focus-visible", "outline-offset")
    assert outline == ["2px solid var(--focus-ring)"] and offset == ["2px"]
    for selector in _CARRYOVER_NATIVE_FOCUS:
        expected = [("outline", outline[0]), ("outline-offset", offset[0])]
        assert declarations(REDESIGN, selector) == expected, selector
        assert declarations_in(_carryover_block(), selector) == expected, selector
        # Nothing in styles.css overrides it (it loads first, so it would lose anyway).
        assert declarations(STYLES, selector) == [], selector


def test_segmented_radios_keep_their_ring_on_the_visible_span() -> None:
    # The .seg radios are .sr-only (clipped, so the new input ring can't show on the
    # 1px input); their focus ring stays on the label's span.
    assert declared(REDESIGN, ".seg input:focus-visible + span", "outline") == [
        "2px solid var(--focus-ring)"
    ]
    assert declared(REDESIGN, ".sr-only", "clip-path") == ["inset(50%)"]


# --- sweep ---
# Residual reflow sweep (tasks 12.2-12.6) plus three honest-status carry-overs: Reflex's
# "Built with Reflex" badge over the sticky bars (carry-e), a sticky bar hiding the focused
# row (carry-f, WCAG 2.2 SC 2.4.11) and the Dashboard status strip (carry-g).

_BADGE_HOST = '.radix-themes:has(> a[href="https://reflex.dev"])'
_PLEXMIX_JS = ASSETS / "plexmix.js"


def _literal_styles(module: ast.Module, function: str) -> list[dict[str, Any]]:
    """Every ``style={...}`` dict literal in ``function``, keeping its literal entries."""
    found: list[dict[str, Any]] = []
    for call in _calls_in(module, function):
        for kw in call.keywords:
            if kw.arg == "style" and isinstance(kw.value, ast.Dict):
                found.append(
                    {
                        k.value: v.value
                        for k, v in zip(kw.value.keys, kw.value.values)
                        if isinstance(k, ast.Constant)
                        and isinstance(k.value, str)
                        and isinstance(v, ast.Constant)
                    }
                )
    return found


def _inline(style: dict[str, Any]) -> str:
    """A Reflex style dict as an inline ``style`` attribute (camelCase to kebab-case)."""
    kebab = {re.sub(r"([A-Z])", r"-\1", key).lower(): value for key, value in style.items()}
    return ";".join(f"{key}:{value}" for key, value in kebab.items())


# 12.2 / 12.6 Library: floors that never outgrow the column, a pager that wraps


def test_minisel_and_search_floors_fit_the_column() -> None:
    """12.1 found the Sync-mode select 9px past a 320 CSS px viewport."""
    minisel = _rule_props(REDESIGN, ".minisel")
    assert minisel["max-width"] == "min(240px, 100%)"
    assert minisel["min-width"] == "0"
    # The search floor is 220px or the page column, whichever is smaller. A % floor would
    # count as 0 in the shrink-to-fit filter bar and wrap it on desktop.
    assert declared(REDESIGN, ".search", "min-width") == [
        "min(220px, 100vw - var(--rail-w) - 2 * var(--page-pad-x))"
    ]
    # No bare pixel floor (or, on .minisel, cap) anywhere else either.
    checks = {".search": ("min-width",), ".minisel": ("min-width", "max-width")}
    for name in (REDESIGN, STYLES):
        for selectors, body in _all_rules(name):
            for selector, props in checks.items():
                if selector in selectors:
                    for prop in props:
                        value = _props(body).get(prop)
                        assert value is None or not re.fullmatch(r"\d+px", value), (name, body)


def test_library_pager_wraps() -> None:
    module = _page_module("pages", "library.py")
    pager = [s for s in _literal_styles(module, "_pagination") if s.get("gap") == "6px"]
    assert pager == [
        {
            "display": "flex",
            "alignItems": "center",
            "gap": "6px",
            "width": "100%",
            "flexWrap": "wrap",
        }
    ]
    ids = {
        kw.value.id
        for call in _calls_in(module, "_pagination")
        for kw in call.keywords
        if kw.arg == "id" and isinstance(kw.value, ast.Name)
    }
    assert ids == {"PREV_PAGE_BUTTON_ID", "NEXT_PAGE_BUTTON_ID"}


# 12.3 Doctor: the hero and the check rows wrap


def _doctor_hero_styles() -> tuple[dict[str, Any], dict[str, Any]]:
    styles = _literal_styles(_page_module("pages", "doctor.py"), "_hero")
    hero = [s for s in styles if s.get("padding") == "18px 20px"]
    text = [s for s in styles if "minWidth" in s]
    assert len(hero) == 1 and len(text) == 1, styles
    return hero[0], text[0]


def test_doctor_hero_wraps_with_a_readable_text_basis() -> None:
    hero, text = _doctor_hero_styles()
    assert hero.get("display") == "flex" and hero.get("flexWrap") == "wrap", hero
    assert text == {"flex": "1 1 12rem", "minWidth": "0"}


def test_doctor_check_rows_wrap_below_the_text() -> None:
    assert declared(REDESIGN, ".check", "flex-wrap") == ["wrap"]
    assert _rule_props(REDESIGN, ".check .cinfo") == {"flex": "1 1 10rem", "min-width": "0"}
    # No more specific rule (e.g. `.doctor-checks .check .cinfo`) overrides the basis.
    for name in (REDESIGN, STYLES):
        for selectors, body in _all_rules(name):
            if any(s.endswith(".cinfo") for s in selectors) and "flex" in _props(body):
                assert selectors == [".check .cinfo"], (name, selectors)


# 12.4 / carry-g Dashboard status strip


def test_status_strip_shares_one_row_then_reflows() -> None:
    base = _rule_props(REDESIGN, ".statusbar")
    assert base["display"] == "grid"
    assert base["grid-template-columns"] == "repeat(auto-fit, minmax(min(17rem, 100%), 1fr))"
    assert declared(REDESIGN, ".statusbar .vsep", "display") == ["none"]
    assert declared(REDESIGN, ".statusbar .st-text", "flex") == ["1 1 9rem"]
    wide = "(min-width: 1120px)"
    row = _rule_props(REDESIGN, ".statusbar", wide)
    assert row["grid-auto-flow"] == "column"
    assert row["grid-auto-columns"] == "minmax(0, max-content)"
    assert row["justify-content"] == "start"
    assert declared(REDESIGN, ".statusbar .vsep", "display", wide) == ["block"]


# carry-e Reflex badge


def test_sticky_bars_keep_their_buttons_out_of_the_badge_corner() -> None:
    reserve = "max(16px, calc(var(--badge-reserve) - var(--page-pad-x)))"
    for bar in (".bulkbar", ".savebar"):
        assert declared(REDESIGN, f"{_BADGE_HOST} {bar}", "padding-inline-end") == [reserve]
    narrow = declared(REDESIGN, _BADGE_HOST, "--badge-reserve")
    wide = declared(REDESIGN, _BADGE_HOST, "--badge-reserve", "(min-width: 62em)")
    assert narrow == ["calc(1rem + 0.75rem + 18px + 8px)"]
    assert wide == ["calc(1rem + 0.75rem + 18px + 0.375rem + 8.25rem + 8px)"]


# carry-f Sticky bars never hide focus


def test_sticky_bars_reserve_scroll_padding_and_reveal_focus() -> None:
    padding = "calc(var(--pm-sticky-bar-h, 64px) + 16px + 8px)"
    for bar in (".bulkbar", ".savebar"):
        assert declared(REDESIGN, f".scroll:has({bar})", "scroll-padding-bottom") == [padding]
        assert declared(REDESIGN, bar, "bottom") == ["16px"]
    js = _PLEXMIX_JS.read_text(encoding="utf-8")
    block = js[js.index("/* 6. Sticky bars never hide focus") :]
    for needle in ("'.bulkbar, .savebar'", "--pm-sticky-bar-h", "block: 'nearest'", "focusin"):
        assert needle in block, needle


# Computed layout (Playwright, request interception, no port), with the shell's rail.

_SWEEP_ORIGIN = "http://reflow-sweep.test"
_BADGE = (
    "<a href='https://reflex.dev' class='badge-fixture'><span class='bi'></span>"
    "<p class='bt'>Built with Reflex</p></a>"
    # Mirrors Reflex 0.8's compiled badge (.web/utils/components.jsx MemoizedBadge).
    "<style>.badge-fixture{position:fixed;bottom:1rem;right:1rem;display:flex;gap:.375rem;"
    "align-items:center;border:1px solid currentColor;border-radius:.5rem;padding:.375rem;"
    "z-index:9998}.badge-fixture .bi{display:block;width:16px;height:16px}"
    ".badge-fixture .bt{display:none;margin:0;font-weight:600;font-size:.875rem;"
    "line-height:1rem}@media (min-width:62em){.badge-fixture .bt{display:block}}</style>"
)


def _sweep_page(
    body: str, width: int, height: int, *, badge: bool = False, script: bool = False
) -> Iterator[Any]:
    """Yield a page with ``body`` in the shell's ``.scroll`` (rail included)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    doc = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        + "".join(f"<link rel='stylesheet' href='/{n}'>" for n in _LAYOUT_SHEETS)
        + ("<script src='/plexmix.js'></script>" if script else "")
        + "</head><body><div class='radix-themes'><div class='shell'><nav class='rail'></nav>"
        + "<div class='main'><main class='scroll' id='main-content' tabindex='-1'>"
        + f"{body}</main></div></div>{_BADGE if badge else ''}</div></body></html>"
    )

    def serve(route: Any) -> None:
        url = route.request.url
        name = url.rsplit("/", 1)[-1]
        if not url.startswith(_SWEEP_ORIGIN) or name.endswith(".woff2"):
            route.abort()
        elif name in _LAYOUT_SHEETS:
            route.fulfill(body=css_text(name), content_type="text/css")
        elif name == "plexmix.js":
            body_js = _PLEXMIX_JS.read_text(encoding="utf-8")
            route.fulfill(body=body_js, content_type="application/javascript")
        else:
            route.fulfill(body=doc, content_type="text/html")

    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browser build not installed
            pytest.skip(f"Chromium for Playwright is unavailable: {type(exc).__name__}")
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.route("**/*", serve)
            page.goto(f"{_SWEEP_ORIGIN}/fixture")
            yield page
        finally:
            browser.close()


_FRAMES = "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"


def test_layout_doctor_hero_and_rows_at_320() -> None:
    """12.3: no overflow at 320x640, check text >= 120px, hero text not a sliver."""
    hero, text = _doctor_hero_styles()
    row = (
        "<li class='check'><div class='ico'></div><div class='cinfo'>"
        "<h3 class='cname'>Embedding provider: Local</h3><div class='cdetail'>"
        "mixedbread-ai/mxbai-embed-large-v1 isn't installed on this machine</div>"
        "<div class='cwhy'>Search needs it to turn your words into vectors.</div></div>"
        "<div class='cact'><span class='badge'>Failing</span>"
        "<button class='btn btn-sm btn-soft' type='button'>Switch provider</button></div></li>"
    )
    body = (
        f"<div class='page'><div class='card doctor-hero' style='{_inline(hero)}'>"
        "<div class='ico' style='width:52px;height:52px;flex-shrink:0'></div>"
        f"<div style='{_inline(text)}'><h2>No library yet</h2>"
        "<div class='fg3'>Connect Plex and sync your library to get started.</div></div>"
        "<button class='btn btn-sm btn-ghost' type='button'>Re-scan</button></div>"
        f"<ul class='card doctor-checks'>{row * 3}</ul></div>"
    )
    for page in _sweep_page(body, 320, 640):
        assert _scroll_overflow(page) <= 0.5
        info = page.evaluate(
            """() => {
              const vw = innerWidth;
              const heroText = document.querySelector('.doctor-hero > div:nth-child(2)');
              return {heroText: heroText.getBoundingClientRect().width,
                cinfo: [...document.querySelectorAll('.check .cinfo')].map(
                  (c) => c.getBoundingClientRect().width),
                right: Math.max(...[...document.querySelectorAll('.page *')].map(
                  (e) => e.getBoundingClientRect().right)), vw}; }"""
        )
        assert info["heroText"] >= 150, info
        assert min(info["cinfo"]) >= 120, info
        assert info["right"] <= info["vw"], info


def _status_item(key: str, name: str, model: str, state: str, cause: str, action: str) -> str:
    model_html = f" · <span class='mono st-model'>{model}</span>" if model else ""
    cause_html = f"<span class='st-cause'>{cause}</span>" if cause else ""
    return (
        f"<div class='item st-item' id='dash-status-{key}'><span class='dot'></span>"
        "<span style='width:14px;height:14px;display:inline-block'></span>"
        f"<div class='st-text'><span class='st-name'><span class='lab'>{name}: </span>"
        f"<span class='val'>Local</span>{model_html}</span><span class='st-state'>{state}</span>"
        f"{cause_html}<span class='st-when'>checked 4 min ago</span></div>"
        f"<a class='st-action' href='#'>{action}</a></div>"
    )


def _status_strip(long: bool) -> str:
    cause = "Can't reach your Plex server at http://plex-media-server.home.arpa:32400"
    items = [
        _status_item(
            "plex",
            "Plex",
            "",
            "Failing" if long else "Ready",
            cause if long else "",
            "Fix" if long else "Check",
        ),
        _status_item("ai", "AI", "gpt-5-mini", "Ready", "", "Check"),
        _status_item(
            "embeddings",
            "Embeddings",
            "mixedbread-ai/mxbai-embed-large-v1" if long else "text-embedding-3-small",
            "Ready",
            "",
            "Check",
        ),
    ]
    sep = "<div class='vsep' aria-hidden='true'></div>"
    # pages/dashboard.py: the strip sits in a Radix vstack (flex column, align start).
    return (
        "<div class='page'><div style='display:flex;flex-direction:column;align-items:flex-start'>"
        f"<div class='card statusbar'>{sep.join(items)}</div></div></div>"
    )


_STRIP_JS = """() => {
  const strip = document.querySelector('.statusbar').getBoundingClientRect();
  const items = [...document.querySelectorAll('.st-item')].map((i) => {
    const r = i.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, left: r.left,
      right: r.right, width: r.width}; });
  const seps = [...document.querySelectorAll('.vsep')].map((v) => getComputedStyle(v).display);
  return {strip: {left: strip.left, right: strip.right}, items, seps}; }"""


def test_layout_status_strip_keeps_three_readouts_on_one_desktop_row() -> None:
    """carry-g: a long Plex cause no longer drops Embeddings to a row of its own."""
    for long in (True, False):
        for page in _sweep_page(_status_strip(long), 1440, 900):
            info = page.evaluate(_STRIP_JS)
            items = info["items"]
            assert len(items) == 3
            # One row: the readouts (centred, of different heights) overlap vertically and
            # sit side by side left to right, with the separators shown.
            assert max(i["top"] for i in items) < min(i["bottom"] for i in items), items
            lefts = [i["left"] for i in items]
            assert lefts == sorted(lefts)
            assert all(a["right"] <= b["left"] for a, b in zip(items, items[1:])), items
            assert info["seps"] == ["block", "block"]
            assert min(i["width"] for i in info["items"]) >= 240, info
            assert info["items"][-1]["right"] <= info["strip"]["right"] + 0.5
            assert _scroll_overflow(page) <= 0.5


def test_layout_status_strip_reflows_at_narrow_widths() -> None:
    for width, height in ((1024, 768), (720, 450), (320, 640)):
        for page in _sweep_page(_status_strip(True), width, height):
            info = page.evaluate(_STRIP_JS)
            assert info["seps"] == ["none", "none"], width  # no stray separators
            assert _scroll_overflow(page) <= 0.5, width
            for item in info["items"]:
                assert item["right"] <= info["strip"]["right"] + 0.5, (width, item)
                assert item["width"] >= min(
                    240, info["strip"]["right"] - info["strip"]["left"] - 40
                ), (width, item)
            if width <= 720:  # one readout per row
                assert len({round(i["left"]) for i in info["items"]}) == 1, info


_BULKBAR = (
    "<div class='card bulkbar'><span class='bulkbar-count'>1 selected</span>"
    "<button class='btn btn-sm btn-soft' type='button'>Tag</button>"
    "<button class='btn btn-sm btn-soft' type='button'>Delete</button>"
    "<button class='btn btn-sm btn-ghost' type='button' aria-label='Clear selection'>"
    "Clear</button></div>"
)
_SAVEBAR = (
    "<div class='savebar' id='set-savebar'><span class='dot dot-warning'></span>"
    "<div class='savebar-text'><p class='savebar-title'>Unsaved changes</p>"
    "<p class='savebar-sections'>In Plex Server</p></div><div class='savebar-actions'>"
    "<button class='btn btn-3 btn-soft' type='button' id='set-discard'>Discard</button>"
    "<button class='btn btn-3 btn-primary' type='button' id='set-save'>Save</button></div></div>"
)
_BADGE_JS = """() => {
  const b = document.querySelector('a[href="https://reflex.dev"]').getBoundingClientRect();
  return [...document.querySelectorAll('.bulkbar button, .savebar button')].map((btn) => {
    const r = btn.getBoundingClientRect();
    const hit = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
    return {text: btn.textContent, clear: r.right <= b.left || r.left >= b.right ||
      r.bottom <= b.top || r.top >= b.bottom, hit: !!hit && btn.contains(hit),
      stuck: r.bottom > innerHeight - 80}; }); }"""


@pytest.mark.parametrize(
    "size",
    [(1440, 900), (1280, 800), (1024, 768), (720, 450), (640, 400), (390, 844), (320, 640)],
    ids=lambda s: f"{s[0]}x{s[1]}",
)
def test_layout_badge_never_covers_a_sticky_bar_button(size: tuple[int, int]) -> None:
    """carry-e: every bar button's rect and centre stay clear of Reflex's badge."""
    tall = "<div style='height:2400px'></div>"
    for bar in (_BULKBAR, _SAVEBAR):
        for page in _sweep_page(f"<div class='page'>{tall}{bar}</div>", *size, badge=True):
            buttons = page.evaluate(_BADGE_JS)
            assert len(buttons) >= 2
            for button in buttons:
                assert button["stuck"], button  # the bar is at the bottom, by the badge
                assert button["clear"] and button["hit"], (size, button)


def test_layout_without_the_badge_the_bars_keep_their_padding() -> None:
    tall = "<div style='height:2400px'></div>"
    for page in _sweep_page(f"<div class='page'>{tall}{_BULKBAR}</div>", 1440, 900):
        pad = page.evaluate(
            "() => getComputedStyle(document.querySelector('.bulkbar')).paddingRight"
        )
        assert pad == "16px"


_ROWS_HTML = "".join(
    f"<tr><td><button type='button' role='checkbox' aria-checked='false' id='cb{i}' "
    f"aria-label='Select Song {i}'></button></td><td>Song {i}</td><td>Artist</td></tr>"
    for i in range(40)
)
_SELECT_PAGE = (
    "<div class='page'><div id='stack' style='display:flex;flex-direction:column;"
    "align-items:flex-start;gap:16px;width:100%'><div class='card' style='height:120px;"
    "width:100%'></div><div class='tbl-wrap tbl-scroll' style='width:100%'>"
    f"<table class='tbl'><tbody>{_ROWS_HTML}</tbody></table></div>"
    "<div id='pager' style='height:32px'>Page 1</div></div></div>"
    "<script>window.showBar = function () {"
    "  var t = document.createElement('template'); t.innerHTML = " + repr(_BULKBAR) + ";"
    "  document.getElementById('stack').insertBefore(t.content.firstChild,"
    "    document.getElementById('pager')); };"
    "window.rowRect = function (i) { var r = document.getElementById('cb' + i)"
    "  .closest('tr').getBoundingClientRect(); return {top: r.top, bottom: r.bottom}; };"
    "window.barTop = function () {"
    "  return document.querySelector('.bulkbar').getBoundingClientRect().top; };"
    "</script>"
)


def test_layout_selecting_a_low_row_keeps_it_above_the_bulk_bar() -> None:
    """carry-f: the row whose checkbox made the bar appear is never left under it."""
    for page in _sweep_page(_SELECT_PAGE, 1440, 900, script=True):
        scroller = "document.getElementById('main-content')"
        # A row in the middle: nothing moves when the bar appears (change 4's rule).
        page.focus("#cb2")
        before = page.evaluate("window.rowRect(0)")
        page.evaluate("window.showBar()")
        page.evaluate(_FRAMES)
        page.wait_for_timeout(100)
        assert page.evaluate("window.rowRect(0)") == before
        assert page.evaluate(f"{scroller}.scrollTop") == 0
        page.evaluate("document.querySelector('.bulkbar').remove()")
        # A row at the bottom edge (keyboard): it comes up just above the bar.
        page.evaluate(
            "document.getElementById('cb14').closest('tr')" ".scrollIntoView({block: 'end'})"
        )
        offset = page.evaluate(f"window.rowRect(0).top + {scroller}.scrollTop")
        page.focus("#cb14")
        page.evaluate("window.showBar()")
        page.evaluate(_FRAMES)
        page.wait_for_timeout(100)
        assert page.evaluate("window.rowRect(14).bottom") <= page.evaluate("window.barTop()") - 7
        # It scrolled; nothing was re-laid out (no row moved inside the content).
        assert page.evaluate(f"window.rowRect(0).top + {scroller}.scrollTop") == offset
        # Tab onwards: each focused checkbox stays above the bar (scroll-padding).
        for _ in range(4):
            page.keyboard.press("Tab")
            page.keyboard.press("Tab")
            page.evaluate(_FRAMES)
            covered = page.evaluate(
                "() => document.activeElement.getBoundingClientRect().bottom > window.barTop()"
            )
            assert not covered
        page.evaluate("document.querySelector('.bulkbar').remove()")
        # Pointer, with focus elsewhere (Safari does not focus buttons on click).
        page.evaluate(f"{scroller}.scrollTop = 0")
        page.evaluate(
            "document.getElementById('cb16').closest('tr')" ".scrollIntoView({block: 'end'})"
        )
        page.evaluate(
            "() => { document.activeElement.blur(); document.getElementById('cb16')"
            ".dispatchEvent(new PointerEvent('pointerdown', {bubbles: true})); }"
        )
        page.evaluate("window.showBar()")
        page.evaluate(_FRAMES)
        page.wait_for_timeout(100)
        assert page.evaluate("window.rowRect(16).bottom") <= page.evaluate("window.barTop()") - 7


_FIELDS_PAGE = (
    "<div class='page'><div id='col' style='display:flex;flex-direction:column;gap:16px'>"
    + "".join(
        f"<label>Field {i}<input class='minisel' id='f{i}' aria-label='Field {i}'></label>"
        for i in range(30)
    )
    + "</div></div><script>window.showSave = function () {"
    "  var t = document.createElement('template'); t.innerHTML = "
    + repr(_SAVEBAR.replace("class='savebar'", "class='savebar animate-slide-up'"))
    + "; document.getElementById('col').appendChild(t.content.firstChild); };</script>"
)


def test_layout_editing_a_low_field_keeps_it_above_the_save_bar() -> None:
    """carry-f: the field whose edit made the (sliding-in) save bar appear stays visible."""
    for page in _sweep_page(_FIELDS_PAGE, 720, 450, script=True):
        page.evaluate("document.getElementById('f6').scrollIntoView({block: 'end'})")
        page.focus("#f6")
        page.evaluate("window.showSave()")
        page.wait_for_timeout(600)  # past the 300ms slide-in
        page.evaluate(_FRAMES)
        info = page.evaluate(
            """() => ({field: document.getElementById('f6').getBoundingClientRect().bottom,
              bar: document.getElementById('set-savebar').getBoundingClientRect().top,
              pad: getComputedStyle(document.getElementById('main-content'))
                .scrollPaddingBottom})"""
        )
        assert info["field"] <= info["bar"] - 7, info
        assert info["pad"] != "0px" and info["pad"] != "auto", info


# --- fix ---
# Review fixes: the rail with classic (always-present) scrollbars, rail labels after a
# focus scroll (assets/plexmix.js block 7), the Library table's edge shadows, and
# check_reflow.py measuring with classic scrollbars and gating what its fixtures probe.
# (Escape in an inline editor under an open hover label: tests/test_pmfocus_js.py.)

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Desktop Chrome/Edge on Windows and Linux: what check_reflow.py measures with.
_CLASSIC_SCROLLBARS: dict[str, Any] = {
    "args": ["--disable-features=OverlayScrollbar"],
    "ignore_default_args": ["--hide-scrollbars"],
}
_FIX_ORIGIN = "http://reflow-fix.test"


def _rail_markup(items: int = 7) -> str:
    def item(i: int) -> str:
        active = " active" if i == 1 else ""
        return (
            f"<button class='rail-item{active}' type='button' aria-label='Item {i}'>"
            "<svg width='20' height='20' aria-hidden='true'></svg></button>"
        )

    groups = (range(0, 3), range(3, 5), range(5, items))
    body = "".join(
        f"<div class='rail-sep'></div><div class='rail-group'>{''.join(item(i) for i in g)}</div>"
        for g in groups
    )
    bottom = "".join(item(i) for i in range(items, items + 2))
    return (
        "<nav class='rail' aria-label='Main'><a class='rail-logo-link' href='#'>"
        "<span class='rail-logo' style='display:block'></span></a>"
        f"{body}<div class='rail-spacer'></div><div class='rail-group'>{bottom}</div></nav>"
    )


def _fix_page(
    body: str,
    width: int,
    height: int,
    *,
    rail: bool = False,
    script: str = "",
    plexmix_js: bool = False,
) -> Iterator[Any]:
    """Yield a page (classic scrollbars) with ``body`` in the shell's ``.scroll``."""
    sync_api = pytest.importorskip("playwright.sync_api")
    doc = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        + "".join(f"<link rel='stylesheet' href='/{n}'>" for n in _LAYOUT_SHEETS)
        + ("<script src='/plexmix.js'></script>" if plexmix_js else "")
        + "</head><body><div class='shell'>"
        + (_rail_markup() if rail else "")
        + "<div class='main'><main class='scroll' id='main-content' tabindex='-1'>"
        + f"{body}</main></div></div>{script}</body></html>"
    )

    def serve(route: Any) -> None:
        url = route.request.url
        name = url.rsplit("/", 1)[-1]
        if not url.startswith(_FIX_ORIGIN) or name.endswith(".woff2"):
            route.abort()
        elif name in _LAYOUT_SHEETS:
            route.fulfill(body=css_text(name), content_type="text/css")
        elif name == "plexmix.js":
            body_js = _PLEXMIX_JS.read_text(encoding="utf-8")
            route.fulfill(body=body_js, content_type="application/javascript")
        else:
            route.fulfill(body=doc, content_type="text/html")

    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(**_CLASSIC_SCROLLBARS)
        except Exception as exc:  # browser build not installed
            pytest.skip(f"Chromium for Playwright is unavailable: {type(exc).__name__}")
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.route("**/*", serve)
            page.goto(f"{_FIX_ORIGIN}/fixture")
            yield page
        finally:
            browser.close()


def test_rail_item_and_its_focus_ring_fit_beside_the_rail_scrollbar() -> None:
    """D3 with the 6px bar: inset + item + focus ring (2px outline, 2px offset) fits left
    of the scrollbar at both rail widths (76px desktop, 56px at 480px and below)."""
    shared = declarations(REDESIGN, ".rail-item:focus-visible")
    assert ("outline", "2px solid var(--focus-ring)") in shared
    assert ("outline-offset", "2px") in shared
    ring = 2 + 2
    bar = _px(declared(REDESIGN, ".rail::-webkit-scrollbar", "width")[0])
    for media in (None, _W480):
        root = _shell_props(REDESIGN, ":root", media)
        rail_w, item_w = _px(root["--rail-w"]), _px(root["--rail-item-w"])
        inset = (rail_w - item_w - 1) / 2  # .rail padding-inline-start; 1px is the border
        assert inset - ring >= 0, media
        assert inset + item_w + ring <= rail_w - 1 - bar, media


@pytest.mark.parametrize("size", [(320, 256), (640, 400)])
def test_layout_scrolling_rail_never_clips_items_or_focus_rings(size: tuple[int, int]) -> None:
    """Spec "Rail controls stay reachable" with classic scrollbars: the rail's bar is the
    6px one, and Tab to every control keeps it and its focus ring inside the rail's visible
    box without scrolling the rail sideways."""
    for page in _fix_page("<div class='page'>Page</div>", *size, rail=True):
        info = page.evaluate(
            """() => { const rail = document.querySelector('.rail');
              return {bar: rail.offsetWidth - rail.clientWidth - rail.clientLeft - 1,
                      overflows: rail.scrollHeight > rail.clientHeight}; }"""
        )
        assert info == {"bar": 6, "overflows": True}
        page.evaluate("document.body.focus()")
        seen = 0
        for _ in range(12):
            page.keyboard.press("Tab")
            state = page.evaluate(
                """() => { const a = document.activeElement, rail = document.querySelector('.rail');
                  if (!rail.contains(a)) return null;
                  const r = a.getBoundingClientRect(), rr = rail.getBoundingClientRect();
                  const cs = getComputedStyle(a);
                  const reach = cs.outlineStyle === 'none' ? 0
                    : parseFloat(cs.outlineOffset) + parseFloat(cs.outlineWidth);
                  const left = rr.left + rail.clientLeft, right = left + rail.clientWidth;
                  return {name: a.getAttribute('aria-label') || 'logo', scrollLeft: rail.scrollLeft,
                          inside: r.left - reach >= left - 0.5 && r.right + reach <= right + 0.5 &&
                            r.top - reach >= rr.top - 0.5 && r.bottom + reach <= rr.bottom + 0.5}; }"""
            )
            if state is None:
                break
            seen += 1
            assert state["scrollLeft"] == 0, state
            assert state["inside"], state
        assert seen == 10  # the logo, 7 navigation items, Changelog and the theme toggle


# A stand-in for Radix Tooltip on each rail item: opens on focusin, closes when an element
# holding the trigger scrolls (TooltipContentImpl's window capture "scroll" listener) and
# on Escape (DismissableLayer, document capture, marking the Escape handled).
_TOOLTIP_STANDIN = """<script>
document.querySelectorAll('.rail-item').forEach((b) => {
  b.setAttribute('data-state', 'closed');
  b.addEventListener('focusin', () => b.setAttribute('data-state', 'instant-open'));
  b.addEventListener('focusout', () => b.setAttribute('data-state', 'closed'));
});
window.addEventListener('scroll', (e) => {
  for (const b of document.querySelectorAll('.rail-item[data-state$="open"]')) {
    if (e.target.contains && e.target.contains(b)) b.setAttribute('data-state', 'closed');
  }
}, true);
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  for (const b of document.querySelectorAll('.rail-item[data-state$="open"]')) {
    e.preventDefault(); b.setAttribute('data-state', 'closed');
  }
}, true);
</script>"""

_ACTIVE_STATE = (
    "() => [document.activeElement.getAttribute('aria-label'),"
    " document.activeElement.getAttribute('data-state')]"
)


def test_rail_label_reopens_after_a_focus_scroll() -> None:
    """Spec "Rail labels are never clipped" for a focused item in a scrolled rail: Tab to
    the theme toggle scrolls the rail, which closes the label the focus opened; plexmix.js
    block 7 reopens it once the rail stops scrolling."""
    js = _PLEXMIX_JS.read_text(encoding="utf-8")
    assert "/* 7. Rail labels survive a focus scroll" in js
    for page in _fix_page("", 320, 256, rail=True, script=_TOOLTIP_STANDIN, plexmix_js=True):
        page.evaluate("document.body.focus()")
        for _ in range(10):
            page.keyboard.press("Tab")
        page.wait_for_timeout(400)
        assert page.evaluate("document.querySelector('.rail').scrollTop") > 0
        assert page.evaluate(_ACTIVE_STATE) == ["Item 8", "instant-open"]
        # Dismissed with Escape: a later rail scroll leaves it closed.
        page.keyboard.press("Escape")
        page.evaluate("document.querySelector('.rail').scrollTop -= 3")
        page.wait_for_timeout(400)
        assert page.evaluate(_ACTIVE_STATE) == ["Item 8", "closed"]
        # Scrolled out of view (e.g. the wheel): not reopened beside an item you can't see.
        page.keyboard.press("Shift+Tab")
        assert page.evaluate(_ACTIVE_STATE) == ["Item 7", "instant-open"]
        page.evaluate("document.querySelector('.rail').scrollTop = 0")
        page.wait_for_timeout(400)
        assert page.evaluate(_ACTIVE_STATE) == ["Item 7", "closed"]


def test_rail_label_is_not_reopened_for_pointer_focus() -> None:
    """A rail control focused by a click (no :focus-visible) is left alone after a scroll."""
    for page in _fix_page("", 320, 256, rail=True, script=_TOOLTIP_STANDIN, plexmix_js=True):
        page.evaluate("document.querySelector('.rail').scrollTop = 1e4")
        page.click("[aria-label='Item 7']")
        page.mouse.move(300, 250)
        page.evaluate("document.querySelector('.rail').scrollTop -= 3")
        page.wait_for_timeout(400)
        assert page.evaluate("document.activeElement.matches(':focus-visible')") is False
        assert page.evaluate(_ACTIVE_STATE) == ["Item 7", "closed"]


def test_library_table_edge_shadows_only_where_the_table_can_overflow() -> None:
    """Spec "a visible scroll indication" on the first screen: edge shadows in tokens,
    covered (`local`) while the table sits against that edge, at 768px and below only."""
    assert declared(REDESIGN, ".tbl-scroll", "background") == []  # desktop: plain box
    section = zoom_fit_section()
    (background,) = [
        v
        for p, v in declarations_in(section, ".tbl-scroll", "(max-width: 768px)")
        if p == "background"
    ]
    layers = _split_selectors(background)  # top-level commas only, whitespace-normalised
    assert len(layers) == 5
    assert sum(layer.endswith(" local") for layer in layers) == 2  # the covers
    assert sum(layer.endswith(" scroll") for layer in layers) == 2  # the shadows
    assert all("var(--surface-raised)" in layer for layer in layers[:2])
    assert all("var(--border-strong)" in layer for layer in layers[2:4])
    assert layers[4] == "var(--surface-raised)"  # the box's own colour (.tbl-wrap's)
    assert declared(REDESIGN, ".tbl-wrap", "background") == ["var(--surface-raised)"]


_EMPTY_TABLE = (
    "<div class='page'><div class='tbl-wrap tbl-scroll' style='width:{w}px'><table class='tbl'>"
    "<tbody>"
    + "".join("<tr>" + "<td style='height:44px'></td>" * 5 + "</tr>" for _ in range(8))
    + "</tbody></table></div></div>"
)


def _edge_strips(page: Any) -> dict[str, bytes]:
    """PNGs of 6px strips at the box's left edge, centre and right edge, in one row."""
    box = page.evaluate(
        """() => { const w = document.querySelector('.tbl-scroll'), r = w.getBoundingClientRect();
          const row = w.querySelectorAll('tr')[3].getBoundingClientRect();
          return {left: r.left + w.clientLeft, width: w.clientWidth, top: row.top + 8}; }"""
    )
    strips = {}
    for name, x in (
        ("left", box["left"]),
        ("centre", box["left"] + box["width"] / 2 - 3),
        ("right", box["left"] + box["width"] - 6),
    ):
        clip = {"x": x, "y": box["top"], "width": 6, "height": 20}
        strips[name] = page.screenshot(clip=clip)
    return strips


def test_layout_library_table_shows_which_side_has_hidden_columns() -> None:
    for page in _fix_page(_EMPTY_TABLE.format(w=300), 390, 844):
        start = _edge_strips(page)
        assert start["right"] != start["centre"]  # more columns to the right
        assert start["left"] == start["centre"]  # nothing hidden on the left
        page.evaluate("document.querySelector('.tbl-scroll').scrollLeft = 1e4")
        end = _edge_strips(page)
        assert end["left"] != end["centre"]
        assert end["right"] == end["centre"]
    # A table that fits shows no shadow, and above 768px the box keeps its plain background.
    for page in _fix_page(_EMPTY_TABLE.format(w=700), 768, 600):
        fits = _edge_strips(page)
        assert fits["left"] == fits["centre"] == fits["right"]
    for page in _fix_page(_EMPTY_TABLE.format(w=300), 1024, 768):
        wide = _edge_strips(page)
        assert wide["left"] == wide["centre"] == wide["right"]


def _check_reflow() -> Any:
    """Import check_reflow.py from the repo root (it needs Playwright at import time)."""
    pytest.importorskip("playwright.sync_api")
    import importlib.util
    import sys

    name = "_plexmix_check_reflow"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / "check_reflow.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # its dataclass resolves annotations through sys.modules
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def test_check_reflow_measures_with_classic_scrollbars() -> None:
    """Headless Chromium hides scrollbars (``--hide-scrollbars``): the gate would measure
    every route about 10px roomier than desktop Chrome with classic scrollbars."""
    cr = _check_reflow()
    assert "--hide-scrollbars" in cr.BROWSER_IGNORE_DEFAULT_ARGS
    assert "--disable-features=OverlayScrollbar" in cr.BROWSER_ARGS
    main = ast.unparse(
        next(
            node
            for node in ast.parse((_REPO_ROOT / "check_reflow.py").read_text("utf-8")).body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
    )
    assert (
        "launch(args=list(BROWSER_ARGS), ignore_default_args=list(BROWSER_IGNORE_DEFAULT_ARGS))"
        in main
    )


def test_check_reflow_fixture_probes_gate() -> None:
    """Check 6 fails on what each fixture was built to catch, not only on page overflow."""
    cr = _check_reflow()
    ok_row = dict.fromkeys(("title", "artist", "album", "duration", "remove"), True)
    heads = {
        name: {"visible": name == "Title", "reachable": True}
        for name in ("Title", "Artist", "Album", "Enrichment")
    }
    good = {
        "thinkPill": {"bBoxLines": 2},
        "resultsTitle": {"boxLines": 2},
        "trackRows": [ok_row, ok_row],
        "libraryTable": {"overflowX": "auto", "tableWidth": 576, "headers": heads},
        "taggingRows": [{"titleColumnWidth": 192, "editVisible": True}],
    }
    before = {"libraryTable": {"tableWidth": 576}}
    assert cr.fixture_failures(good, before) == []
    bad = {
        "thinkPill": {"bBoxLines": 20},
        "resultsTitle": {"boxLines": 3},
        "trackRows": [dict(ok_row, remove=False)],
        "libraryTable": {"overflowX": "hidden", "tableWidth": 1806, "headers": heads},
        "taggingRows": [{"titleColumnWidth": 9, "editVisible": False}],
    }
    details = " | ".join(f["detail"] for f in cr.fixture_failures(bad, before))
    for needle in (
        "thinking pill prompt shows 20 lines",
        "results heading shows 3 lines",
        "remove not inside the results list",
        "widened the table from 576px to 1806px",
        "Library column Artist is neither visible nor reachable",
        "Library column Album is neither visible nor reachable",
        "title column 9px wide",
        "Edit off screen",
    ):
        assert needle in details, needle
    assert "Enrichment" not in details  # only Title, Artist and Album must stay reachable
    assert {f["check"] for f in cr.fixture_failures(bad, before)} == {"fixture"}


def test_check_reflow_rail_gate_covers_clipped_rings_and_missing_labels() -> None:
    cr = _check_reflow()
    rect = {"left": 10.0, "top": 60.0, "right": 45.0, "bottom": 102.0, "height": 42.0}
    clip = {"left": 0.0, "right": 49.0}
    control = {
        "name": "Doctor",
        "ok": True,
        "blockedByBadge": False,
        "insideRail": True,
        "railScrollLeft": 0,
        "railClip": clip,
        "rect": rect,
        "shellDisplacedBy": 0,
        "docDisplacedBy": 0,
    }
    step = {
        "el": "button.rail-item [Doctor]",
        "inRail": True,
        "rect": rect,
        "fullyVisible": True,
        "ringReach": 4.0,
        "railClip": clip,
        "ringInRail": True,
        "railScrollLeft": 0,
        "tooltipTrigger": True,
        "label": {"shown": True, "text": "Doctor · g x", "inViewport": True},
        "shellScrollTop": 0,
        "shellScrollLeft": 0,
        "rootScrollTop": 0,
        "docScrollTop": 0,
        "topbarTop": 0.0,
    }
    rail = {"railWidth": 56.0, "controls": [control]}
    vp = cr.Viewport(320, 256, False)
    assert cr.rail_failures(rail, {"enteredRail": True, "steps": [step]}, vp) == []
    bad_rail = {"railWidth": 56.0, "controls": [dict(control, insideRail=False, railScrollLeft=4)]}
    bad_steps = [
        dict(step, ringInRail=False),
        dict(step, el="button.rail-item [Changelog]", label={"shown": False}),
        dict(step, el="button.rail-item [Settings]", label={"shown": True, "inViewport": False}),
    ]
    details = " | ".join(
        f["detail"]
        for f in cr.rail_failures(bad_rail, {"enteredRail": True, "steps": bad_steps}, vp)
    )
    for needle in (
        "cut off by the rail's edge or scrollbar: Doctor",
        "rail scrolled 4px sideways",
        "focus ring cut off by the rail: button.rail-item [Doctor] (ring 6.0..49.0",
        "shows no label on keyboard focus: button.rail-item [Changelog]",
        "rail label not fully on screen: button.rail-item [Settings]",
    ):
        assert needle in details, needle


def test_check_reflow_gates_the_scroll_cues() -> None:
    """Check 7: a sideways-scrolling Library table shows its scrollbar and (at 768px and
    below) its edge shadows; an overflowing Settings tab strip shows its scrollbar."""
    cr = _check_reflow()
    table = {"scrollWidth": 576, "clientWidth": 290, "scrollbarHeight": 8, "edgeShadows": True}
    tabs = {"listOverflowX": 72, "listScrollbarHeight": 6}
    assert cr.scrollcue_failures({"libraryTable": table, "settingsTabs": tabs}, 390) == []
    hidden = {
        "libraryTable": dict(table, scrollbarHeight=0, edgeShadows=False),
        "settingsTabs": dict(tabs, listScrollbarHeight=0),
    }
    details = [f["detail"] for f in cr.scrollcue_failures(hidden, 390)]
    assert details == [
        "the Library table scrolls sideways with no scrollbar",
        "the Library table scrolls sideways with no edge shadows",
        "the Settings tab strip scrolls with no scrollbar",
    ]
    # Nothing to scroll, nothing to show; above 768px the shadows are not required.
    fits = {"libraryTable": dict(table, scrollWidth=290, scrollbarHeight=0, edgeShadows=False)}
    assert cr.scrollcue_failures(fits, 390) == []
    wide = {"libraryTable": dict(table, edgeShadows=False)}
    assert cr.scrollcue_failures(wide, 1024) == []
    assert cr.SCROLL_SHADOW_MAX_VW == 768
    assert "(max-width: 768px)" in zoom_fit_section()


# --- live-a ---
# Live verification (task 13.3, fresh-install profile): at 320 CSS px with a classic scrollbar
# the Dashboard setup checklist's widest step button ("Build search index", one line) widened
# its card 4px past .scroll. At 480px and below a step's button wraps its label instead.

_LIVE_W480 = "(max-width: 480px)"
_SETUP_STEP_PAGE = (
    "<div class='page'><div style='display:flex;flex-direction:column;align-items:flex-start;"
    "gap:20px'><section class='card setup' id='dash-setup-section'><ol class='setup-steps'>"
    "<li class='setup-step is-todo'><span class='setup-mark' aria-hidden='true'>4</span>"
    "<div class='setup-body'><div class='setup-title-row'><h3 class='setup-title'>"
    "Build search index</h3></div><p class='setup-desc'>Create embeddings so PlexMix can find "
    "tracks by vibe.</p></div><div class='setup-action'><button class='btn btn-3 btn-soft' "
    "type='button'><svg width='16' height='16' aria-hidden='true'></svg>Build search index"
    "</button></div></li></ol></section><div style='height:2000px'></div></div></div>"
)


def test_setup_step_buttons_wrap_their_label_at_480px_only() -> None:
    section = zoom_fit_section()
    assert dict(declarations_in(section, ".setup-action", _LIVE_W480))["max-width"] == "100%"
    button = dict(declarations_in(section, ".setup-action .btn", _LIVE_W480))
    assert button["white-space"] == "normal"
    assert button["max-width"] == "100%"
    tall = dict(declarations_in(section, ".setup-action .btn-3", _LIVE_W480))
    assert tall["height"] == "auto"
    assert tall["min-height"] == "40px"
    # Wider windows keep the one-line, 40px button.
    assert declared(REDESIGN, ".setup-action .btn", "white-space") == []
    assert declared(REDESIGN, ".btn", "white-space") == ["nowrap"]


def test_layout_setup_step_button_stays_inside_its_card_at_320() -> None:
    for page in _fix_page(_SETUP_STEP_PAGE, 320, 640, rail=True):
        info = page.evaluate(
            """() => {
              const step = document.querySelector('.setup-step');
              const card = step.closest('.setup');
              const btn = step.querySelector('.setup-action .btn');
              const cs = getComputedStyle(step), cc = getComputedStyle(card);
              const px = (s, ...props) => props.reduce((n, p) => n + (parseFloat(s[p]) || 0), 0);
              const r = btn.getBoundingClientRect();
              // The width a step's content gets in the column, whatever the button needs.
              const room = card.parentElement.clientWidth -
                px(cc, 'paddingLeft', 'paddingRight', 'borderLeftWidth', 'borderRightWidth') -
                px(cs, 'paddingLeft', 'paddingRight');
              const out = {right: r.right, height: r.height, room,
                inner: step.getBoundingClientRect().right - parseFloat(cs.paddingRight)};
              btn.style.whiteSpace = 'nowrap';
              btn.style.maxWidth = 'none';
              out.oneLine = btn.getBoundingClientRect().width;
              btn.style.whiteSpace = '';
              btn.style.maxWidth = '';
              return out; }"""
        )
        # The fixture is the case that broke: on one line the button is wider than the step.
        assert info["oneLine"] > info["room"] + 1, info
        assert _scroll_overflow(page) <= 0.5, info
        assert info["right"] <= info["inner"] + 0.5, info
        assert info["height"] >= 40, info
