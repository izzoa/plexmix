#!/usr/bin/env python3
"""Re-check the web UI audit's input-integrity findings against a running PlexMix UI.

The 2026-11-17 design audit found that text fields bound to the server dropped typed
characters (28 of 62 scripted typing trials ended corrupted), that the command palette
ran the wrong command, called the server on every key, leaked focus and broke the theme
command, plus related shortcut, contrast and bundle problems. The
``webui-input-responsiveness`` change fixed them. This script replays every one of those
checks in a real browser and prints a pass/fail table.

Usage (from the repo root, with the UI already running)::

    python check_input_integrity.py                       # full run, ~40 minutes
    python check_input_integrity.py --quick               # smaller typing matrix, ~6 minutes
    python check_input_integrity.py --server-log ui.log --json-out results.json
    python check_input_integrity.py --only palette-focus,generator

Start the UI with ``plexmix ui --dev`` (dev server) or ``plexmix ui`` (production build),
on a synced library with at least one track and one saved playlist. Run the script against
both. Where Generate is gated (its button is disabled and described by the readiness line,
e.g. on a fresh profile or a library without embeddings), the Generator row still runs its
typing checks but cannot submit, so it reports SKIP with the readiness line's text.

Requirements:

- ``pip install playwright && playwright install chromium``.
- The UI running at ``--base-url`` (default ``http://localhost:3000``). If it asks for a
  password, put the password in an environment variable and pass its name with
  ``--password-env``. The value is never printed.
- ``--server-log``: the file that holds the UI server's output, for the "no server
  traceback" checks. Without it they are reported as SKIP.
- ``--web-dir``: the Reflex build directory (default ``.web``) for the bundle check.

What it touches: it drives a headless Chromium over a pipe and binds no port. It never
reads PlexMix's config, data directory or the keyring. No AI or embedding provider is
called. The typing, palette and theme checks only filter or navigate. The Generator checks
intercept the submit in the browser, or submit a blank prompt, which the app rejects before
generating. Filter values typed during a check live only in that browser session's state.

How latency is injected: Playwright's ``route_web_socket`` relays the app's websocket
(``/_event``) and holds every frame for the given time in each direction, in order. The
audit's ``Network.emulateNetworkConditions`` latency is applied on top, as the audit did.

Exit status: 0 when no row fails, 1 when any row fails, 2 when the UI cannot be reached or
needs a password that was not given.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence, Union

try:
    from playwright.sync_api import Browser, CDPSession, Page, WebSocketRoute, sync_playwright
    from playwright.sync_api import Error as PlaywrightError
except ImportError:  # pragma: no cover - only without Playwright installed
    sys.stderr.write(
        "check_input_integrity.py needs Playwright: "
        "pip install playwright && playwright install chromium\n"
    )
    raise SystemExit(2)

# ── Constants ─────────────────────────────────────────────────────────

VIEWPORT = {"width": 1440, "height": 900}
LINUX_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# The typing matrix (tasks.md 9.3): every field, every phrase, every speed, every latency.
PHRASES = ("rainy day melancholy", "jazz", "coltrane")
DELAYS_MS = (40, 90, 120, 150, 220)
LATENCIES_MS = (0, 30, 60)
QUICK_PHRASES = ("rainy day melancholy",)
QUICK_DELAYS_MS = (40, 220)
QUICK_LATENCIES_MS = (0, 60)

AUDIT_PHRASE = "rainy day melancholy"
AUDIT_BASELINE = "28/62 corrupted"
# Audit timings: wait after clearing a field, and settle time after typing stops
# (race.py: 900/1500 ms; race_lat.py: 1200/2000 ms).
PRE_MS, SETTLE_MS = 900, 1500
PRE_LAT_MS, SETTLE_LAT_MS = 1200, 2000
PAGE_SETTLE_MS = 2500  # after networkidle, before the first keystroke (audit)

EVENT_PATH = "/_event"  # the Reflex websocket
PROMPT_MAX = 280
MIN_CONTRAST = 4.5
CHORD_ROUTE = "/library"
CHORD_ON_LOAD = "library_state.on_load"  # the route's load handler, as event_names() shows it

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

# Chromium's own warning about a dependency's unload listener; not an app error.
CONSOLE_NOISE = ("Permissions policy violation",)


@dataclass(frozen=True)
class Field:
    """A text field under test."""

    key: str
    label: str
    route: str
    selector: str
    setup: str = ""  # "palette": open ⌘K first; "options": open the Generator's Options
    blur_check: bool = True  # also re-read the value after the field loses focus


FIELDS: tuple[Field, ...] = (
    Field("palette", "Command palette search", "/dashboard", "#cmdk-input", "palette", False),
    Field("gen-prompt", "Generator prompt", "/generator", "#gen-prompt"),
    Field("gen-genre", "Generator Genre focus", "/generator", "#gen-genre", "options"),
    Field("lib-search", "Library search", "/library", "#lib-search"),
    Field("lib-genre", "Library genre", "/library", "#lib-genre"),
    Field("lib-tag", "Library tag", "/library", "#lib-tag"),
    Field("tag-artist", "Tagging artist", "/tagging", "#tag-filter-artist"),
    Field("tag-genre", "Tagging genre", "/tagging", "#tag-filter-genre"),
    Field("hist-search", "History search", "/history", "#hist-search"),
)
FIELDS_BY_KEY = {f.key: f for f in FIELDS}


@dataclass(frozen=True)
class AuditRun:
    """One of the audit's typing runs (``race.py`` / ``race_lat.py``)."""

    name: str
    fields: tuple[str, ...]
    delays: tuple[int, ...]
    trials: int
    latencies: tuple[int, ...]
    pre_ms: int
    settle_ms: int


# The audit's 62 trials, all typing "rainy day melancholy". Its "/library", "/history" and
# "/tagging" ".search input" cases are Library search, History search and Tagging artist.
_AUDIT_CASES = ("lib-search", "hist-search", "tag-artist", "gen-prompt", "palette")
AUDIT_RUNS: tuple[AuditRun, ...] = (
    # race.py, production build: 5 fields x 40/90/150 ms x 2 trials = 30
    AuditRun("race", _AUDIT_CASES, (40, 90, 150), 2, (0,), PRE_MS, SETTLE_MS),
    # race.py with PM_DELAYS=120 on the production build and on the dev server: 2 x 10 = 20
    AuditRun("race-120a", _AUDIT_CASES, (120,), 2, (0,), PRE_MS, SETTLE_MS),
    AuditRun("race-120b", _AUDIT_CASES, (120,), 2, (0,), PRE_MS, SETTLE_MS),
    # race_lat.py: Generator prompt and Library search x 0/30/60 ms x 150/220 ms = 12
    AuditRun(
        "race-latency",
        ("gen-prompt", "lib-search"),
        (150, 220),
        1,
        (0, 30, 60),
        PRE_LAT_MS,
        SETTLE_LAT_MS,
    ),
)

CHECK_IDS = (
    "bundle",
    "shortcut-label",
    "contrast",
    "palette-a11y",
    "palette-focus",
    "palette-theme",
    "palette-idle-cancel",
    "g-chord",
    "palette-wrong-page",
    "palette-traffic",
    "generator",
    "typing-audit",
    "typing-matrix",
)

# ── Page-side scripts ────────────────────────────────────────────────

ACTIVE_JS = """() => { const e = document.activeElement;
  if (!e || e === document.body || e === document.documentElement)
    return {tag: e ? e.tagName : null};
  return {tag: e.tagName, id: e.id || '', cls: (e.className || '').toString().slice(0, 60),
          inCmdk: !!e.closest('.cmdk'), isTrigger: !!e.closest('.cmd-trigger')}; }"""

PALETTE_STATE_JS = """() => { const i = document.getElementById('cmdk-input');
  const opts = [...document.querySelectorAll('#cmdk-list [role=option]')];
  const shown = o => { for (let n = o; n && n.id !== 'cmdk-list'; n = n.parentElement)
    if (n.hidden) return false; return true; };
  const vis = opts.filter(shown);
  const title = o => ((o.querySelector('.ci-title') || o).textContent || '').trim();
  return {open: !!i, value: i ? i.value : null,
    activedescendant: i ? i.getAttribute('aria-activedescendant') : null,
    visible: vis.map(o => ({id: o.id, title: title(o)})),
    all: opts.map(o => ({id: o.id, title: title(o)})),
    selected: opts.filter(o => o.getAttribute('aria-selected') === 'true').map(o => o.id),
    status: (document.getElementById('cmdk-status') || {}).textContent || ''}; }"""

CONTRAST_JS = r"""(el) => {
  function parse(c) { const m = (c || '').match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1}; }
  function lum(c) { const f = v => { v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); }
  function blend(t, b) { const a = t.a;
    return {r: t.r * a + b.r * (1 - a), g: t.g * a + b.g * (1 - a),
            b: t.b * a + b.b * (1 - a), a: 1}; }
  const chain = []; for (let n = el; n; n = n.parentElement) chain.push(n);
  let bg = {r: 255, g: 255, b: 255, a: 1};
  for (let i = chain.length - 1; i >= 0; i--) {
    const c = parse(getComputedStyle(chain[i]).backgroundColor);
    if (c && c.a > 0) bg = blend(c, bg); }
  const ph = getComputedStyle(el, '::placeholder').color;
  const p0 = parse(ph);
  if (!p0) return {id: el.id, color: ph, empty: el.value === '', ratio: null};
  const fg = p0.a < 1 ? blend(p0, bg) : p0;
  const L1 = lum(fg), L2 = lum(bg);
  return {id: el.id, placeholder: el.getAttribute('placeholder'), empty: el.value === '',
          color: ph,
          background: `rgb(${Math.round(bg.r)}, ${Math.round(bg.g)}, ${Math.round(bg.b)})`,
          ratio: +((Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05)).toFixed(2)}; }"""

# Records every submit of the Generator's prompt form and stops it before React sees it,
# so a non-blank prompt can never start a generation. When `allowBlank` is set, a blank
# prompt is let through to the app (which rejects it with a hint, without generating).
SUBMIT_GUARD_JS = """(allowBlank) => {
  window.__pmCheckSubmits = [];
  if (window.__pmCheckGuard) window.removeEventListener('submit', window.__pmCheckGuard, true);
  window.__pmCheckGuard = (e) => {
    const f = e.target; if (!f || !f.querySelector || !f.querySelector('#gen-prompt')) return;
    const fd = new FormData(f); const prompt = String(fd.get('prompt') || '');
    const blocked = !(allowBlank && prompt.trim() === '');
    window.__pmCheckSubmits.push({prompt, genre: String(fd.get('genre') || ''), blocked});
    if (blocked) { e.preventDefault(); e.stopImmediatePropagation(); }
  };
  window.addEventListener('submit', window.__pmCheckGuard, true);
}"""

# The Generator's readiness line (webui-honest-status-and-first-run): Generate is disabled,
# and described by this line, only while a blocking readiness cause stops generation.
READY_LINE_ID = "gen-ready-line"
READY_LINE_LOADED_JS = f"() => !!document.querySelector('#{READY_LINE_ID} .rl-facts')"
READY_LINE_TIMEOUT_MS = 15_000

# The prompt form's submit button and the readiness line's text (see generate_gate_reason).
GENERATE_GATE_JS = """() => {
  const prompt = document.getElementById('gen-prompt');
  const form = prompt ? prompt.form : null;
  const btn = form ? form.querySelector('button[type=submit]') : null;
  const line = document.getElementById('gen-ready-line');
  return {found: !!btn, disabled: !!(btn && btn.disabled),
    describedby: btn ? (btn.getAttribute('aria-describedby') || '') : '',
    readyLine: line ? (line.innerText || line.textContent || '') : ''}; }"""


# ── Results ──────────────────────────────────────────────────────────


@dataclass
class Row:
    """One line of the pass/fail table."""

    check: str
    title: str
    expected: str
    observed: str = ""
    status: str = FAIL
    details: dict[str, Any] = field(default_factory=dict)


class SignInRequired(RuntimeError):
    """The UI shows its sign-in card and no password was given."""


def generate_gate_reason(gate: Mapping[str, Any]) -> str:
    """Why Generate can't be used, from :data:`GENERATE_GATE_JS` ("" when it can).

    Generate is gated only when its submit button is disabled *and* described by the
    readiness line, which says why: "Generate is gated: <readiness line text>". A missing
    button, a disabled button the readiness line doesn't describe, or an empty readiness
    line is not a gate, so the submit checks still run and report the real failure.
    """
    if not gate.get("found") or not gate.get("disabled"):
        return ""
    if READY_LINE_ID not in str(gate.get("describedby") or "").split():
        return ""
    text = " ".join(str(gate.get("readyLine") or "").split())
    return f"Generate is gated: {text}" if text else ""


def generator_row_status(results: Mapping[str, Mapping[str, Any]], gate_reason: str = "") -> str:
    """PASS, FAIL or SKIP for the Generator row from its sub-checks' ``ok`` values.

    A sub-check that ran and failed (``ok`` False) is a FAIL, gated or not. While
    Generate is gated (``gate_reason``) the submit sub-checks can't run (``ok`` None),
    so the row is a SKIP, never a PASS. Ungated, every sub-check must pass.
    """
    if not results or any(r.get("ok") is False for r in results.values()):
        return FAIL
    if gate_reason:
        return SKIP
    return PASS if all(r.get("ok") is True for r in results.values()) else FAIL


def log(msg: str) -> None:
    """Progress output (stderr), so stdout carries only the table."""
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


# ── Websocket latency relay ──────────────────────────────────────────


class WsRelay:
    """Relays the Reflex websocket, holding each frame ``latency_ms`` in each direction.

    Frames keep their order: every timer forwards the oldest queued frame. The sync
    Playwright API runs these handlers on its event loop, which keeps running while the
    script waits inside any Playwright call (typing, ``wait_for_timeout``).
    """

    def __init__(self, latency_ms: int) -> None:
        self.delay_s = latency_ms / 1000.0
        self.sent: list[str] = []  # page -> server payloads
        self.received = 0

    def handle(self, ws: WebSocketRoute) -> None:
        server = ws.connect_to_server()
        loop = asyncio.get_running_loop()
        to_server: deque[Union[str, bytes]] = deque()
        to_page: deque[Union[str, bytes]] = deque()

        def flush_to_server() -> None:
            with contextlib.suppress(Exception):
                server.send(to_server.popleft())

        def flush_to_page() -> None:
            with contextlib.suppress(Exception):
                ws.send(to_page.popleft())

        def from_page(message: Union[str, bytes]) -> None:
            self.sent.append(message if isinstance(message, str) else "<binary>")
            to_server.append(message)
            loop.call_later(self.delay_s, flush_to_server)

        def from_server(message: Union[str, bytes]) -> None:
            self.received += 1
            to_page.append(message)
            loop.call_later(self.delay_s, flush_to_page)

        ws.on_message(from_page)
        server.on_message(from_server)


def is_event_frame(payload: str) -> bool:
    """A socket.io EVENT packet (a Reflex event), as opposed to engine.io pings/pongs."""
    return payload.startswith("42")


def event_names(payloads: Sequence[str]) -> list[str]:
    """The Reflex handler names in event frames (``state.handler``, shortened)."""
    names: list[str] = []
    for payload in payloads:
        for name in re.findall(r'"name":"([^"]+)"', payload):
            parts = name.split(".")
            state = parts[-2].split("____")[-1] if len(parts) > 1 else ""
            names.append(f"{state}.{parts[-1]}" if state else parts[-1])
    return names


# ── Browser sessions ─────────────────────────────────────────────────


@dataclass
class Session:
    """One browser context (one Reflex session) and what it observed."""

    page: Page
    cdp: CDPSession
    latency_ms: int
    relay: Optional[WsRelay]
    cdp_sent: list[str] = field(default_factory=list)
    http_requests: list[tuple[str, str]] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)

    def sent_frames(self) -> list[str]:
        """Websocket payloads the page has sent so far (relay or CDP, whichever saw them)."""
        return self.relay.sent if self.relay is not None else self.cdp_sent

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)


class Checker:
    """Runs the checks against one UI and collects the rows."""

    def __init__(self, browser: Browser, args: argparse.Namespace) -> None:
        self.browser = browser
        self.base = args.base_url.rstrip("/")
        self.quick: bool = args.quick
        self.password: Optional[str] = (
            os.environ.get(args.password_env) if args.password_env else None
        )
        self.server_log: Optional[Path] = Path(args.server_log) if args.server_log else None
        self.web_dir = resolve_web_dir(args.web_dir)
        self.rows: list[Row] = []
        self.trials: list[dict[str, Any]] = []

    # ── sessions ──

    @contextlib.contextmanager
    def session(
        self, theme: str = "light", user_agent: Optional[str] = None, latency_ms: int = 0
    ) -> Iterator[Session]:
        kwargs: dict[str, Any] = {"viewport": VIEWPORT, "device_scale_factor": 1}
        if user_agent:
            kwargs["user_agent"] = user_agent
        ctx = self.browser.new_context(**kwargs)
        ctx.add_init_script(f"try{{localStorage.setItem('theme','{theme}')}}catch(e){{}}")
        relay: Optional[WsRelay] = None
        if latency_ms:
            relay = WsRelay(latency_ms)
            ctx.route_web_socket(lambda url: EVENT_PATH in url, relay.handle)
        page = ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")
        s = Session(page=page, cdp=cdp, latency_ms=latency_ms, relay=relay)
        # Only the app's own socket counts (the dev server also runs Vite's HMR socket).
        app_sockets: set[str] = set()
        cdp.on(
            "Network.webSocketCreated",
            lambda e: app_sockets.add(e["requestId"]) if EVENT_PATH in e["url"] else None,
        )
        cdp.on(
            "Network.webSocketFrameSent",
            lambda e: (
                s.cdp_sent.append(e["response"].get("payloadData", ""))
                if e["requestId"] in app_sockets
                else None
            ),
        )
        cdp.on(
            "Network.requestWillBeSent",
            lambda e: s.http_requests.append((e.get("type") or "", e["request"]["url"])),
        )
        page.on(
            "console",
            lambda m: (
                s.console_errors.append(m.text[:300])
                if m.type == "error" and not m.text.startswith(CONSOLE_NOISE)
                else None
            ),
        )
        page.on("pageerror", lambda e: s.console_errors.append(f"pageerror: {str(e)[:300]}"))
        try:
            yield s
        finally:
            with contextlib.suppress(PlaywrightError):
                ctx.close()

    def goto(self, s: Session, route: str, ready: str = "#main-heading") -> None:
        """Open ``route``, sign in if asked, wait for ``ready`` and settle like the audit."""
        s.page.goto(self.base + route, wait_until="networkidle", timeout=120_000)
        s.page.wait_for_selector(".rail, #login-password", timeout=60_000)
        if s.page.locator("#login-password").count():
            if not self.password:
                raise SignInRequired("the UI asks for a password; pass --password-env")
            s.page.fill("#login-password", self.password)
            s.page.press("#login-password", "Enter")
            s.page.wait_for_selector(".rail", timeout=30_000)
            if s.page.evaluate("location.pathname") != route:
                s.page.goto(self.base + route, wait_until="networkidle", timeout=120_000)
                s.page.wait_for_selector(".rail", timeout=60_000)
        s.page.wait_for_selector(ready, timeout=60_000)
        s.wait(PAGE_SETTLE_MS)
        if s.latency_ms:
            # The audit's latency injection, kept for fidelity (it delays HTTP traffic).
            s.cdp.send(
                "Network.emulateNetworkConditions",
                {
                    "offline": False,
                    "latency": s.latency_ms,
                    "downloadThroughput": -1,
                    "uploadThroughput": -1,
                },
            )

    def open_palette(self, s: Session, key: str = "Control+k") -> None:
        s.page.keyboard.press(key)
        s.page.wait_for_function(
            "document.activeElement && document.activeElement.id === 'cmdk-input'",
            timeout=10_000,
        )

    def wait_palette_closed(self, s: Session) -> None:
        s.page.wait_for_function("!document.getElementById('cmdk-input')", timeout=10_000)

    def palette_state(self, s: Session) -> dict[str, Any]:
        result: dict[str, Any] = s.page.evaluate(PALETTE_STATE_JS)
        return result

    def active(self, s: Session) -> dict[str, Any]:
        result: dict[str, Any] = s.page.evaluate(ACTIVE_JS)
        return result

    def log_mark(self) -> Optional[int]:
        if self.server_log is None or not self.server_log.exists():
            return None
        return self.server_log.stat().st_size

    def log_tracebacks_since(self, mark: Optional[int]) -> Optional[list[str]]:
        """Traceback excerpts written to the server log since ``mark`` (None: not checked)."""
        if mark is None or self.server_log is None:
            return None
        time.sleep(1.0)  # let the server flush
        with self.server_log.open("rb") as fh:
            fh.seek(mark)
            text = fh.read().decode("utf-8", "replace")
        out = []
        for m in re.finditer(r"Traceback \(most recent call last\):", text):
            out.append(text[m.start() : m.start() + 1200])
        return out

    def add(self, row: Row) -> None:
        self.rows.append(row)
        log(f"{row.status:4}  {row.title}: {row.observed}")

    # ── checks ──

    def check_bundle(self) -> None:
        row = Row("bundle", "Bundle: no DynamicIcon", "0 jsx(DynamicIcon in app/routes/*.jsx")
        routes = self.web_dir / "app" / "routes"
        files = sorted(routes.rglob("*.jsx")) if routes.is_dir() else []
        if not files:
            row.observed = f"no compiled routes under {routes}"
            row.status = FAIL
        else:
            counts = {
                str(p.relative_to(routes)): p.read_text("utf-8").count("jsx(DynamicIcon")
                for p in files
            }
            total = sum(counts.values())
            row.details = {"web_dir": str(self.web_dir), "files": len(files), "per_file": counts}
            row.observed = f"{total} in {len(files)} route files"
            row.status = PASS if total == 0 else FAIL
        self.add(row)

    def check_shortcut_label(self) -> None:
        row = Row("shortcut-label", "Shortcut label", '"Ctrl K" on Linux, "⌘K" on macOS')
        seen: dict[str, Any] = {}
        for name, ua, want in (("linux", LINUX_UA, "Ctrl K"), ("mac", MAC_UA, "⌘K")):
            with self.session(user_agent=ua) as s:
                self.goto(s, "/dashboard")
                seen[name] = s.page.evaluate(
                    """() => { const t = document.querySelector('.cmd-trigger');
                    return {label: t.querySelector('.kbd').innerText.trim(),
                            platform: document.documentElement.dataset.platform,
                            keyshortcuts: t.getAttribute('aria-keyshortcuts')}; }"""
                )
                seen[name]["ok"] = seen[name]["label"] == want
        row.details = seen
        row.observed = f"linux={seen['linux']['label']!r} mac={seen['mac']['label']!r}"
        row.status = PASS if all(v["ok"] for v in seen.values()) else FAIL
        self.add(row)

    def check_contrast(self) -> None:
        row = Row("contrast", "Contrast: .minisel placeholder", ">= 4.5:1 in light and dark")
        found: list[dict[str, Any]] = []
        for theme in ("light", "dark"):
            with self.session(theme=theme) as s:
                for route, ready in (
                    ("/library", "#lib-search"),
                    ("/tagging", "#tag-filter-genre"),
                ):
                    self.goto(s, route, ready)
                    for el in s.page.locator("input.minisel[placeholder]").element_handles():
                        if not el.is_visible():
                            continue
                        res = el.evaluate(CONTRAST_JS)
                        res.update(theme=theme, route=route)
                        found.append(res)
        bad = [
            f for f in found if f["ratio"] is None or f["ratio"] < MIN_CONTRAST or not f["empty"]
        ]
        row.details = {"fields": found}
        if not found:
            row.observed = "no .minisel text fields found"
        else:
            worst = min(found, key=lambda f: f["ratio"] if f["ratio"] is not None else 0.0)
            row.observed = (
                f"{len(found)} placeholders, min {worst['ratio']}:1 "
                f"({worst['id']}, {worst['theme']})"
            )
        row.status = PASS if found and not bad else FAIL
        self.add(row)

    def check_palette_a11y(self) -> None:
        row = Row(
            "palette-a11y",
            "Palette: accessibility tree",
            'modal dialog "Command palette", combobox "Search commands", listbox of options, '
            "aria-activedescendant follows ArrowDown",
        )
        with self.session() as s:
            self.goto(s, "/dashboard")
            self.open_palette(s)
            s.wait(800)
            s.cdp.send("Accessibility.enable")
            nodes = s.cdp.send("Accessibility.getFullAXTree")["nodes"]
            dialog = _ax_find(nodes, "dialog", "Command palette")
            combo = _ax_find(nodes, "combobox", "Search commands")
            listbox = _ax_find(nodes, "listbox")
            options = _ax_descendants(nodes, listbox, "option") if listbox else []
            before = self.palette_state(s)
            s.page.keyboard.press("ArrowDown")
            s.wait(250)
            after = self.palette_state(s)
            nodes2 = s.cdp.send("Accessibility.getFullAXTree")["nodes"]
            combo2 = _ax_find(nodes2, "combobox", "Search commands")
            ad_node = _ax_related(combo2, "activedescendant") if combo2 else None
            selected_ax = [
                n.get("backendDOMNodeId")
                for n in nodes2
                if _ax_role(n) == "option" and _ax_prop(n, "selected") is True
            ]
            snapshot = s.page.locator(".cmdk").aria_snapshot()
        moved = (
            len(before["visible"]) > 1
            and before["activedescendant"] == before["visible"][0]["id"]
            and after["activedescendant"] == before["visible"][1]["id"]
        )
        checks = {
            "dialog named 'Command palette'": dialog is not None,
            "dialog is modal": dialog is not None and _ax_prop(dialog, "modal") is True,
            "combobox 'Search commands'": combo is not None,
            "combobox controls the listbox": combo is not None
            and _ax_related(combo, "controls") is not None,
            "listbox of options": listbox is not None and len(options) > 0,
            "ArrowDown moves the highlight to the 2nd option": moved,
            "one option aria-selected=true, the activedescendant": after["selected"]
            == [after["activedescendant"]],
            "AX: activedescendant is the selected option": ad_node is not None
            and selected_ax == [ad_node],
        }
        row.details = {
            "checks": checks,
            "options": len(options),
            "before": before["activedescendant"],
            "after": after["activedescendant"],
            "aria_snapshot": snapshot,
        }
        failed = [k for k, v in checks.items() if not v]
        row.observed = (
            f"modal dialog, combobox, listbox of {len(options)} options; ArrowDown "
            f"{before['activedescendant']} -> {after['activedescendant']}"
            if not failed
            else "missing: " + "; ".join(failed)
        )
        row.status = PASS if not failed else FAIL
        self.add(row)

    def check_palette_focus(self) -> None:
        row = Row(
            "palette-focus",
            "Palette: focus containment and return",
            "Tab/Shift+Tab x10 stay in .cmdk; Escape returns to the opener or the trigger",
        )
        res: dict[str, Any] = {}
        with self.session() as s:
            self.goto(s, "/library", "#lib-search")
            # 1. ⌘K from Library search: Tab/Shift+Tab stay inside, Escape returns to the field.
            s.page.locator("#lib-search").click()
            self.open_palette(s)
            trail = []
            for key in ["Tab"] * 10 + ["Shift+Tab"] * 10:
                s.page.keyboard.press(key)
                trail.append(self.active(s))
            s.page.keyboard.press("Escape")
            self.wait_palette_closed(s)
            s.wait(600)
            res["from_field"] = {
                "tab_trail_inside": all(a.get("inCmdk") for a in trail),
                "outside": [a for a in trail if not a.get("inCmdk")],
                "after_escape": self.active(s),
            }
            res["from_field"]["ok"] = (
                res["from_field"]["tab_trail_inside"]
                and res["from_field"]["after_escape"].get("id") == "lib-search"
            )
            # 2. Opened by clicking the trigger: Escape returns to the trigger.
            s.page.locator(".cmd-trigger").click()
            s.page.wait_for_function(
                "document.activeElement && document.activeElement.id === 'cmdk-input'"
            )
            trail2 = []
            for key in ["Tab"] * 3 + ["Shift+Tab"] * 3:
                s.page.keyboard.press(key)
                trail2.append(self.active(s))
            s.page.keyboard.press("Escape")
            self.wait_palette_closed(s)
            s.wait(600)
            after2 = self.active(s)
            res["from_trigger"] = {
                "tab_trail_inside": all(a.get("inCmdk") for a in trail2),
                "after_escape": after2,
                "ok": all(a.get("inCmdk") for a in trail2) and bool(after2.get("isTrigger")),
            }
            # 3. ⌘K with nothing focused: focus must not fall to <body> (the audit's finding).
            s.page.evaluate("document.activeElement && document.activeElement.blur()")
            self.open_palette(s)
            s.page.keyboard.press("Escape")
            self.wait_palette_closed(s)
            s.wait(600)
            after3 = self.active(s)
            res["from_body"] = {"after_escape": after3, "ok": bool(after3.get("isTrigger"))}
        row.details = res
        trail_ok = res["from_field"]["tab_trail_inside"] and res["from_trigger"]["tab_trail_inside"]
        row.observed = (
            f"Escape: field->{_focus_label(res['from_field']['after_escape'])}, "
            f"trigger->{_focus_label(res['from_trigger']['after_escape'])}, "
            f"body->{_focus_label(res['from_body']['after_escape'])}; "
            f"Tab trail inside: {trail_ok}"
        )
        row.status = PASS if all(v["ok"] for v in res.values()) else FAIL
        self.add(row)

    def check_palette_theme(self) -> None:
        row = Row(
            "palette-theme",
            "Palette: theme row (light)",
            "html.dark set, palette closed, no server traceback",
        )
        with self.session(theme="light") as s:
            self.goto(s, "/dashboard")
            was_dark = s.page.evaluate("document.documentElement.classList.contains('dark')")
            mark = self.log_mark()
            self.open_palette(s)
            s.page.keyboard.type("dark mode", delay=40)
            s.wait(300)
            st = self.palette_state(s)
            theme_id = "cmdk-opt-action-theme"
            title = next((o["title"] for o in st["visible"] if o["id"] == theme_id), None)
            for _ in range(len(st["visible"])):
                if self.palette_state(s)["activedescendant"] == theme_id:
                    break
                s.page.keyboard.press("ArrowDown")
            highlighted = self.palette_state(s)["activedescendant"] == theme_id
            s.page.keyboard.press("Enter")
            s.wait(1500)
            is_dark = s.page.evaluate("document.documentElement.classList.contains('dark')")
            closed = s.page.evaluate("!document.getElementById('cmdk-input')")
            stored = s.page.evaluate(
                "(() => { try { return localStorage.getItem('theme'); } "
                "catch (e) { return null; } })()"
            )
            errors = list(s.console_errors)
            tracebacks = self.log_tracebacks_since(mark)
        row.details = {
            "row_title": title,
            "highlighted_by_keyboard": highlighted,
            "dark_before": was_dark,
            "html_dark_after": is_dark,
            "palette_closed": closed,
            "localStorage_theme": stored,
            "console_errors": errors,
            "server_tracebacks": tracebacks,
        }
        ok = (
            not was_dark
            and title == "Switch to dark mode"
            and highlighted
            and is_dark
            and closed
            and not errors
        )
        tb = "not checked (no --server-log)" if tracebacks is None else f"{len(tracebacks)}"
        row.observed = (
            f"row {title!r} -> html.dark={is_dark}, closed={closed}, console errors="
            f"{len(errors)}, server tracebacks={tb}"
        )
        if not ok or (tracebacks is not None and tracebacks):
            row.status = FAIL
        else:
            row.status = PASS if tracebacks is not None else SKIP
        self.add(row)

    def check_palette_idle_cancel(self) -> None:
        row = Row("palette-idle-cancel", "Palette: idle cancel", 'no "Cancel running task" row')
        with self.session() as s:
            self.goto(s, "/dashboard")
            self.open_palette(s)
            s.wait(1500)  # the open refresh (recent prompts, running flag) has landed
            idle = self.palette_state(s)
            s.page.keyboard.type("cancel", delay=40)
            s.wait(300)
            typed = self.palette_state(s)

        def cancel_rows(st: dict[str, Any]) -> list[str]:
            return [
                o["id"]
                for o in st["all"]
                if o["id"] == "cmdk-opt-action-cancel" or "cancel running" in o["title"].lower()
            ]

        found = cancel_rows(idle) + cancel_rows(typed)
        row.details = {
            "idle_rows": [o["title"] for o in idle["visible"]],
            "typed_cancel_visible": [o["title"] for o in typed["visible"]],
            "cancel_rows": found,
        }
        row.observed = (
            "no cancel row (typing 'cancel' shows: "
            + ", ".join(o["title"] for o in typed["visible"])
            + ")"
            if not found
            else f"cancel row present: {found}"
        )
        row.status = PASS if not found and idle["visible"] else FAIL
        self.add(row)

    def check_g_chord(self) -> None:
        row = Row("g-chord", "g then l", f"navigates to {CHORD_ROUTE} with no document reload")
        with self.session() as s:
            self.goto(s, "/dashboard")
            s.page.evaluate("window.__pmCheckMarker = 'kept'")
            s.page.evaluate("document.activeElement && document.activeElement.blur()")
            docs_before = sum(1 for t, _ in s.http_requests if t == "Document")
            frames0 = len(s.sent_frames())
            s.page.keyboard.press("g")
            s.page.keyboard.press("l")
            with contextlib.suppress(PlaywrightError):
                s.page.wait_for_url(f"**{CHORD_ROUTE}", timeout=10_000)
            s.wait(PAGE_SETTLE_MS)
            path = s.page.evaluate("location.pathname")
            marker = s.page.evaluate("window.__pmCheckMarker || null")
            docs_after = sum(1 for t, _ in s.http_requests if t == "Document")
            rows = s.page.locator("table tbody tr").count()
            sent = event_names([f for f in s.sent_frames()[frames0:] if is_event_frame(f)])
        on_load = CHORD_ON_LOAD in sent
        row.details = {
            "path": path,
            "marker_kept": marker == "kept",
            "document_requests": docs_after - docs_before,
            "events_after_chord": sent,
            "library_rows": rows,
        }
        ok = path == CHORD_ROUTE and marker == "kept" and docs_after == docs_before and on_load
        row.observed = (
            f"{path}, document requests={docs_after - docs_before}, window state kept="
            f"{marker == 'kept'}, {CHORD_ON_LOAD} ran={on_load} ({rows} rows)"
        )
        row.status = PASS if ok else FAIL
        self.add(row)

    def check_palette_wrong_page(self) -> None:
        row = Row(
            "palette-wrong-page",
            "Palette: 'history' at 40 ms + Enter",
            "lands on /history (audit: /generator)",
        )
        attempts: list[dict[str, Any]] = []
        repeats = 1 if self.quick else 2
        for latency in (0, 60):
            with self.session(latency_ms=latency) as s:
                for rep in range(repeats):
                    for wait_open in (True, False):
                        self.goto(s, "/dashboard")
                        s.page.evaluate("document.activeElement && document.activeElement.blur()")
                        s.page.keyboard.press("Control+k")
                        if wait_open:
                            s.page.wait_for_function(
                                "document.activeElement && "
                                "document.activeElement.id === 'cmdk-input'",
                                timeout=10_000,
                            )
                        s.page.keyboard.type("history", delay=40)
                        s.page.keyboard.press("Enter")
                        with contextlib.suppress(PlaywrightError):
                            s.page.wait_for_url("**/history", timeout=8_000)
                        s.wait(1500)
                        attempts.append(
                            {
                                "latency_ms": latency,
                                "rep": rep,
                                "typed_after_open": wait_open,
                                "path": s.page.evaluate("location.pathname"),
                            }
                        )
        bad = [a for a in attempts if a["path"] != "/history"]
        row.details = {"attempts": attempts}
        row.observed = (
            f"{len(attempts) - len(bad)}/{len(attempts)} landed on /history "
            "(0/60 ms latency, typed right after ⌘K and after it opened)"
            if not bad
            else f"wrong page: {bad}"
        )
        row.status = PASS if not bad else FAIL
        self.add(row)

    def check_palette_traffic(self) -> None:
        row = Row(
            "palette-traffic",
            "Palette: server traffic while typing",
            "0 websocket events (and 0 HTTP requests) for 10 typed characters",
        )
        text = "rainy day "  # 10 characters
        with self.session() as s:
            self.goto(s, "/dashboard")
            self.open_palette(s)
            s.wait(1500)  # the open refresh round trip is over
            frames0, http0 = len(s.sent_frames()), len(s.http_requests)
            # The counter works: page load and the palette's open refresh were seen.
            seen_before = event_names([f for f in s.sent_frames() if is_event_frame(f)])
            s.page.keyboard.type(text, delay=90)
            s.wait(1000)  # a debounced send would have gone out by now
            frames = s.sent_frames()[frames0:]
            http = s.http_requests[http0:]
            value = s.page.evaluate("document.getElementById('cmdk-input').value")
        events = [f for f in frames if is_event_frame(f)]
        row.details = {
            "typed": text,
            "value": value,
            "events_seen_before_typing": seen_before,
            "frames_sent": frames,
            "event_frames": len(events),
            "http_requests": http,
        }
        row.observed = (
            f"{len(events)} websocket events, {len(frames)} frames in all, "
            f"{len(http)} HTTP requests; field reads {value!r} "
            f"(counter saw {len(seen_before)} events before typing)"
        )
        row.status = PASS if seen_before and not events and not http and value == text else FAIL
        self.add(row)

    def generate_gate(self, s: Session) -> dict[str, Any]:
        """The Generate button and readiness line, once the line has loaded its facts."""
        with contextlib.suppress(PlaywrightError):
            s.page.wait_for_function(READY_LINE_LOADED_JS, timeout=READY_LINE_TIMEOUT_MS)
        gate = s.page.evaluate(GENERATE_GATE_JS)
        return dict(gate) if isinstance(gate, dict) else {}

    def check_generator(self) -> None:
        row = Row(
            "generator",
            "Generator prompt",
            "⌘/Ctrl+Enter submits, 281st character rejected, counter live",
        )
        res: dict[str, Any] = {}
        with self.session() as s:
            self.goto(s, "/generator", "#gen-prompt")
            # A gated Generate (disabled, described by the readiness line) can't submit:
            # the submit checks are skipped with the line's reason, the rest still run.
            gate = self.generate_gate(s)
            gate_reason = generate_gate_reason(gate)
            prompt = s.page.locator("#gen-prompt")
            counter = s.page.locator("#gen-prompt-count")
            # Counter: live on every keystroke, with no server round trip.
            prompt.click()
            frames0 = len(s.sent_frames())
            prompt.press_sequentially("late", delay=40)
            mid = counter.inner_text().strip()
            prompt.press_sequentially(" night drive", delay=40)
            end = counter.inner_text().strip()
            events = [f for f in s.sent_frames()[frames0:] if is_event_frame(f)]
            res["counter"] = {
                "after_4": mid,
                "after_16": end,
                "events_while_typing": len(events),
                "ok": mid == f"4/{PROMPT_MAX}" and end == f"16/{PROMPT_MAX}" and not events,
            }
            if gate_reason:
                # The shortcut must not get past the gate (plexmix.js swallows it while
                # the default button is disabled), and plain Enter still types a newline.
                s.page.evaluate(SUBMIT_GUARD_JS, False)
                prompt.fill("")
                prompt.press_sequentially("late night drive", delay=40)
                for chord in ("Control+Enter", "Meta+Enter"):
                    prompt.press(chord)
                    s.wait(300)
                after_chords = prompt.input_value()
                prompt.press("Enter")
                s.wait(300)
                after_enter = prompt.input_value()
                gated_submits = s.page.evaluate("window.__pmCheckSubmits")
                res["gated_shortcut"] = {
                    "submits": gated_submits,
                    "text_after_shortcuts": after_chords,
                    "plain_enter_newline": after_enter == "late night drive\n",
                    "ok": not gated_submits
                    and after_chords == "late night drive"
                    and after_enter == "late night drive\n",
                }
                res["submit"] = {"skipped": gate_reason, "ok": None}
            else:
                # ⌘/Ctrl+Enter right after the last keystroke submit the text on screen
                # (the guard records the form data and stops the submit: nothing is
                # generated).
                s.page.evaluate(SUBMIT_GUARD_JS, False)
                for chord in ("Control+Enter", "Meta+Enter"):
                    prompt.fill("")
                    prompt.press_sequentially("late night drive", delay=40)
                    prompt.press(chord)
                    s.wait(300)
                submits = s.page.evaluate("window.__pmCheckSubmits")
                prompt.press("Enter")
                s.wait(300)
                after_enter = prompt.input_value()
                plain_enter_submits = len(s.page.evaluate("window.__pmCheckSubmits")) - len(submits)
                res["submit"] = {
                    "submits": submits,
                    "plain_enter_newline": after_enter == "late night drive\n",
                    "plain_enter_submits": plain_enter_submits,
                    "ok": [x["prompt"] for x in submits] == ["late night drive"] * 2
                    and plain_enter_submits == 0
                    and after_enter == "late night drive\n",
                }
            # Length cap: 280 characters fit, the 281st is refused.
            prompt.fill("")
            prompt.click()
            s.page.keyboard.insert_text("a" * PROMPT_MAX)
            at_max = len(prompt.input_value())
            prompt.press_sequentially("b", delay=20)
            value = prompt.input_value()
            res["max_length"] = {
                "length_after_280": at_max,
                "length_after_281st": len(value),
                "281st_kept": value.endswith("b"),
                "counter": counter.inner_text().strip(),
                "maxlength_attr": prompt.get_attribute("maxlength"),
            }
            res["max_length"]["ok"] = (
                at_max == PROMPT_MAX
                and len(value) == PROMPT_MAX
                and not value.endswith("b")
                and res["max_length"]["counter"] == f"{PROMPT_MAX}/{PROMPT_MAX}"
            )
            if gate_reason:
                res["blank_submit"] = {"skipped": gate_reason, "ok": None}
            else:
                # The real submit path, end to end: Ctrl+Enter on a blank prompt reaches
                # the server, which answers with the blank-prompt hint (no generation can
                # start: the guard still stops any non-blank prompt).
                prompt.fill("")
                s.page.evaluate(SUBMIT_GUARD_JS, True)
                blank = prompt.input_value() == ""
                prompt.press("Control+Enter")
                hint = ""
                with contextlib.suppress(PlaywrightError):
                    s.page.wait_for_function(
                        "(document.getElementById('gen-prompt-hint') || {}).innerText",
                        timeout=8_000,
                    )
                    hint = s.page.locator("#gen-prompt-hint").inner_text().strip()
                res["blank_submit"] = {
                    "prompt_was_blank": blank,
                    "hint": hint,
                    "submits": s.page.evaluate("window.__pmCheckSubmits"),
                    "ok": blank and bool(hint),
                }
        row.details = {**res, "generate_gate": gate}
        typing = (
            f"281st char kept={res['max_length']['281st_kept']}"
            f" ({res['max_length']['length_after_281st']} chars); counter "
            f"{res['counter']['after_4']} -> {res['counter']['after_16']} with "
            f"{res['counter']['events_while_typing']} server events"
        )
        if gate_reason:
            gated = res["gated_shortcut"]
            row.observed = (
                f"{gate_reason}; submit checks skipped; ⌘/Ctrl+Enter submitted "
                f"{len(gated['submits'])} times while gated (plain Enter newline="
                f"{gated['plain_enter_newline']}); {typing}"
            )
        else:
            submitted = [x["prompt"] for x in res["submit"]["submits"]]
            row.observed = (
                f"Ctrl+Enter and ⌘+Enter submitted {submitted} (plain Enter = newline); "
                f"{typing}; blank submit hint {res['blank_submit']['hint']!r}"
            )
        failed = [k for k, v in res.items() if v.get("ok") is False]
        if failed:
            row.observed = f"failed: {failed}; " + row.observed
        row.status = generator_row_status(res, gate_reason)
        self.add(row)

    # ── typing ──

    def prepare_field(self, s: Session, f: Field) -> None:
        # The palette input and the Genre focus field only exist after their setup step.
        ready = {"palette": ".cmd-trigger", "options": "#gen-prompt"}.get(f.setup, f.selector)
        self.goto(s, f.route, ready)
        if f.setup == "palette":
            self.open_palette(s)
        elif f.setup == "options":
            s.page.locator("button[aria-controls='gen-adv-panel']").click()
            s.page.wait_for_selector(f.selector, state="visible", timeout=10_000)
            s.wait(500)

    def typing_session(
        self,
        f: Field,
        latency_ms: int,
        phrases: Sequence[str],
        delays: Sequence[int],
        trials: int,
        pre_ms: int,
        settle_ms: int,
        tag: str,
    ) -> list[dict[str, Any]]:
        """Type each phrase at each speed into one field, in one session, like race.py."""
        out: list[dict[str, Any]] = []
        with self.session(latency_ms=latency_ms) as s:
            self.prepare_field(s, f)
            loc = s.page.locator(f.selector)
            for phrase in phrases:
                for delay in delays:
                    for trial in range(trials):
                        loc.click()
                        loc.fill("")
                        s.wait(pre_ms)
                        f0 = len(s.sent_frames())
                        loc.press_sequentially(phrase, delay=delay)
                        immediate = loc.input_value()
                        sent = [x for x in s.sent_frames()[f0:] if is_event_frame(x)]
                        s.wait(settle_ms)
                        settled = loc.input_value()
                        after_blur: Optional[str] = None
                        if f.blur_check:
                            loc.evaluate("el => el.blur()")
                            s.wait(400 + 2 * latency_ms)
                            after_blur = loc.input_value()
                        ok = immediate == phrase and settled == phrase
                        ok = ok and (after_blur is None or after_blur == phrase)
                        rec = {
                            "run": tag,
                            "field": f.key,
                            "phrase": phrase,
                            "delay_ms": delay,
                            "latency_ms": latency_ms,
                            "trial": trial,
                            "ok": ok,
                            "value_immediately": immediate,
                            "value_settled": settled,
                            "value_after_blur": after_blur,
                            "events_sent_while_typing": event_names(sent),
                        }
                        out.append(rec)
                        if not ok:
                            log(f"CORRUPTED {json.dumps(rec)}")
            if s.relay is not None:
                for rec in out:
                    rec["relay_frames"] = {
                        "to_server": len(s.relay.sent),
                        "to_page": s.relay.received,
                    }
        self.trials.extend(out)
        return out

    def check_typing_audit(self) -> None:
        row = Row(
            "typing-audit",
            "Typing: audit 62-trial replica",
            f"0 corrupted (audit: {AUDIT_BASELINE})",
        )
        if self.quick:
            row.status = SKIP
            row.observed = "skipped with --quick"
            self.add(row)
            return
        recs: list[dict[str, Any]] = []
        errors: list[str] = []
        for run in AUDIT_RUNS:
            for key in run.fields:
                for latency in run.latencies:
                    log(f"audit {run.name}: {key} at {latency} ms latency")
                    try:
                        recs += self.typing_session(
                            FIELDS_BY_KEY[key],
                            latency,
                            (AUDIT_PHRASE,),
                            run.delays,
                            run.trials,
                            run.pre_ms,
                            run.settle_ms,
                            f"audit:{run.name}",
                        )
                    except PlaywrightError as exc:
                        errors.append(f"{run.name}/{key}: {type(exc).__name__}: {str(exc)[:200]}")
        expected = sum(
            len(r.fields) * len(r.delays) * r.trials * len(r.latencies) for r in AUDIT_RUNS
        )
        bad = [r for r in recs if not r["ok"]]
        per_field = {
            key: sum(1 for r in recs if r["field"] == key) for key in dict.fromkeys(_AUDIT_CASES)
        }
        row.details = {
            "trials": len(recs),
            "per_field": per_field,
            "corrupted": bad,
            "errors": errors,
        }
        row.observed = f"{len(bad)}/{len(recs)} corrupted"
        if errors:
            row.observed += f"; {len(errors)} session errors: {errors[0]}"
        row.status = PASS if not bad and not errors and len(recs) == expected else FAIL
        self.add(row)

    def check_typing_matrix(self) -> None:
        phrases = QUICK_PHRASES if self.quick else PHRASES
        delays = QUICK_DELAYS_MS if self.quick else DELAYS_MS
        latencies = QUICK_LATENCIES_MS if self.quick else LATENCIES_MS
        shape = f"{len(phrases)} phrases x {len(delays)} speeds x {len(latencies)} latencies"
        for f in FIELDS:
            row = Row(
                "typing-matrix",
                f"Typing: {f.label}",
                f"0 corrupted ({shape})",
            )
            recs: list[dict[str, Any]] = []
            errors: list[str] = []
            for latency in latencies:
                log(f"matrix: {f.label} at {latency} ms latency")
                try:
                    recs += self.typing_session(
                        f,
                        latency,
                        phrases,
                        delays,
                        1,
                        PRE_MS if latency == 0 else PRE_LAT_MS,
                        SETTLE_MS if latency == 0 else SETTLE_LAT_MS,
                        "matrix",
                    )
                except PlaywrightError as exc:
                    errors.append(f"{latency} ms: {type(exc).__name__}: {str(exc)[:200]}")
            expected = len(phrases) * len(delays) * len(latencies)
            bad = [r for r in recs if not r["ok"]]
            row.details = {
                "field": f.selector,
                "trials": len(recs),
                "corrupted": bad,
                "errors": errors,
            }
            row.observed = f"{len(bad)}/{len(recs)} corrupted"
            if errors:
                row.observed += f"; {len(errors)} session errors: {errors[0]}"
            row.status = PASS if not bad and not errors and len(recs) == expected else FAIL
            self.add(row)

    def check_server_log(self, mark: Optional[int]) -> None:
        row = Row("server-log", "Server log", "no traceback while the checks ran")
        tracebacks = self.log_tracebacks_since(mark)
        if tracebacks is None:
            row.status = SKIP
            row.observed = "not checked (no --server-log)"
        else:
            row.details = {"tracebacks": tracebacks}
            row.observed = f"{len(tracebacks)} tracebacks"
            row.status = PASS if not tracebacks else FAIL
        self.add(row)

    def run(self, only: Sequence[str]) -> None:
        mark = self.log_mark()
        steps: dict[str, Callable[[], None]] = {
            "bundle": self.check_bundle,
            "shortcut-label": self.check_shortcut_label,
            "contrast": self.check_contrast,
            "palette-a11y": self.check_palette_a11y,
            "palette-focus": self.check_palette_focus,
            "palette-theme": self.check_palette_theme,
            "palette-idle-cancel": self.check_palette_idle_cancel,
            "g-chord": self.check_g_chord,
            "palette-wrong-page": self.check_palette_wrong_page,
            "palette-traffic": self.check_palette_traffic,
            "generator": self.check_generator,
            "typing-audit": self.check_typing_audit,
            "typing-matrix": self.check_typing_matrix,
        }
        for check in CHECK_IDS:
            if only and check not in only:
                continue
            log(f"-- {check}")
            try:
                steps[check]()
            except SignInRequired:
                raise
            except Exception as exc:  # one broken check must not hide the others
                self.add(
                    Row(check, check, "check runs", f"error: {type(exc).__name__}: {exc}"[:300])
                )
        self.check_server_log(mark)


# ── Helpers ──────────────────────────────────────────────────────────


def _focus_label(active: dict[str, Any]) -> str:
    if active.get("isTrigger"):
        return "trigger"
    return str(active.get("id") or active.get("tag"))


def _ax_role(node: dict[str, Any]) -> Optional[str]:
    role: Optional[str] = (node.get("role") or {}).get("value")
    return role


def _ax_name(node: dict[str, Any]) -> Optional[str]:
    name: Optional[str] = (node.get("name") or {}).get("value")
    return name


def _ax_prop(node: dict[str, Any], name: str) -> Any:
    for prop in node.get("properties", []):
        if prop.get("name") == name:
            return (prop.get("value") or {}).get("value")
    return None


def _ax_related(node: dict[str, Any], name: str) -> Optional[int]:
    """The first related DOM node id of an idref property (``activedescendant``, ...)."""
    for prop in node.get("properties", []):
        if prop.get("name") == name:
            related = (prop.get("value") or {}).get("relatedNodes") or []
            if related:
                node_id: Optional[int] = related[0].get("backendDOMNodeId")
                return node_id
    return None


def _ax_find(
    nodes: list[dict[str, Any]], role: str, name: Optional[str] = None
) -> Optional[dict[str, Any]]:
    for node in nodes:
        if node.get("ignored"):
            continue
        if _ax_role(node) == role and (name is None or _ax_name(node) == name):
            return node
    return None


def _ax_descendants(
    nodes: list[dict[str, Any]], root: dict[str, Any], role: str
) -> list[dict[str, Any]]:
    by_id = {n["nodeId"]: n for n in nodes}
    out: list[dict[str, Any]] = []
    stack = list(root.get("childIds", []))
    while stack:
        node = by_id.get(stack.pop())
        if node is None:
            continue
        if _ax_role(node) == role and not node.get("ignored"):
            out.append(node)
        stack.extend(node.get("childIds", []))
    return out


def resolve_web_dir(web_dir: str) -> Path:
    """``--web-dir`` as given, or next to this script when a relative path is not found."""
    path = Path(web_dir)
    if path.is_absolute() or path.exists():
        return path
    return Path(__file__).resolve().parent / path


def print_table(rows: Sequence[Row], header: str) -> None:
    """Print the pass/fail table (plain text, Markdown-compatible)."""
    print(header)
    print()
    print("| # | Check | Expected | Observed | Result |")
    print("|---|---|---|---|---|")
    for i, row in enumerate(rows, 1):
        observed = row.observed.replace("|", "/").replace("\n", " ")
        print(f"| {i} | {row.title} | {row.expected} | {observed} | {row.status} |")
    fails = sum(1 for r in rows if r.status == FAIL)
    skips = sum(1 for r in rows if r.status == SKIP)
    print()
    print(f"{len(rows) - fails - skips} passed, {fails} failed, {skips} skipped")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-check the web UI audit's input-integrity findings in a real browser.",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:3000",
        help="where the UI runs (default: %(default)s)",
    )
    parser.add_argument(
        "--server-log",
        help="the UI server's output file, checked for tracebacks (otherwise SKIP)",
    )
    parser.add_argument(
        "--web-dir",
        default=".web",
        help="Reflex build directory for the bundle check (default: %(default)s)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="smaller typing matrix (1 phrase x 2 speeds x 2 latencies; no audit replica)",
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated check ids to run: " + ", ".join(CHECK_IDS),
    )
    parser.add_argument("--json-out", help="write every row and typing trial to this JSON file")
    parser.add_argument(
        "--password-env",
        help="name of an environment variable holding the UI password, if it asks for one",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser")
    args = parser.parse_args(argv)
    only = [c.strip() for c in args.only.split(",") if c.strip()]
    unknown = [c for c in only if c not in CHECK_IDS]
    if unknown:
        parser.error(f"unknown check id(s): {', '.join(unknown)}")
    args.only = only
    return args


def ui_reachable(base_url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/", timeout=10) as resp:
            return 200 <= int(resp.status) < 500
    except Exception:
        return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.server_log and not Path(args.server_log).is_file():
        sys.stderr.write(f"--server-log {args.server_log} is not a file.\n")
        return 2
    if not ui_reachable(args.base_url):
        sys.stderr.write(f"The UI is not reachable at {args.base_url}; start it first.\n")
        return 2
    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        checker = Checker(browser, args)
        try:
            checker.run(args.only)
        except SignInRequired as exc:
            sys.stderr.write(f"{exc}\n")
            return 2
        finally:
            browser.close()
    minutes = (time.time() - started) / 60
    mode = "quick" if args.quick else "full"
    print_table(
        checker.rows, f"Input integrity checks against {args.base_url} ({mode}, {minutes:.1f} min)"
    )
    if args.json_out:
        payload = {
            "base_url": args.base_url,
            "mode": mode,
            "minutes": round(minutes, 1),
            "rows": [vars(r) for r in checker.rows],
            "trials": checker.trials,
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=1, default=str), "utf-8")
    return 1 if any(r.status == FAIL for r in checker.rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
