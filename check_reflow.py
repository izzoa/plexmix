#!/usr/bin/env python3
"""Check that the PlexMix web UI reflows at 320 CSS px and at 200% browser zoom.

The 2026-11-17 web UI audit found that the icon rail clipped and moved the whole shell at
200% zoom, that the ⌘K pill covered the page name, that every route but Settings scrolled
sideways at 320 CSS px, and that the palette ran off short windows (WCAG 1.4.4 and 1.4.10).
The ``webui-zoom-and-narrow-fit`` change fixes them; this script is its gate (design D12).
It loads every route in a headless Chromium and checks the layout in the browser.

Usage (from the repo root, with the UI already running)::

    python check_reflow.py                          # the gate: 3 viewports x 2 themes x 7 routes
    python check_reflow.py --out /tmp/reflow-run    # write the JSON somewhere specific
    python check_reflow.py --informational          # also 390x844, 320x256, 512x384, 1024x768
    python check_reflow.py --viewport 720x450 --routes /generator,/library --themes dark

Requirements:

- ``pip install playwright && playwright install chromium`` (Playwright is not a project
  dependency, like ``take_screenshots.py``).
- The UI running at ``--base`` (default ``http://localhost:3000``): ``plexmix ui`` (the
  production build) or ``plexmix ui --dev``. A synced library gives the Library, Tagging and
  History checks real rows; on an empty install those fixtures are reported as skipped.
- If the UI asks for a password, put it in the ``PLEXMIX_UI_PASSWORD`` environment
  variable. The value is never printed or written to the output.

Viewports: 320x640, 640x400 (1280x800 at 200% zoom) and 720x450 (1440x900 at 200% zoom),
each at device scale factor 2 (zoom is emulated as a half-size CSS viewport, as in the
audit), in the light and the dark theme (``localStorage.theme`` plus a reload), with
``prefers-reduced-motion: reduce`` so no transition is caught mid-flight. Chromium runs with
classic, always-present scrollbars (``--hide-scrollbars`` dropped, overlay scrollbars off):
the worst case, as desktop Chrome and Edge draw them on Windows and Linux, so every inner
scroller's bar takes its real width. With
``--informational`` it also runs 390x844, 320x256, 512x384 and 1024x768; their failures are
reported but never change the exit status. ``--viewport WxH`` (repeatable) replaces the
gate viewports with your own, which do gate.

Routes: /dashboard /generator /library /tagging /history /doctor /settings.

Checks, per route, viewport and theme (the ``check`` field of each failure):

1. ``overflow``: ``documentElement.scrollWidth <= clientWidth`` and
   ``.scroll.scrollWidth <= .scroll.clientWidth + 1``.
2. ``offright``: no visible, non-fixed element with opacity above 0 has its right edge past
   the viewport. Descendants of the inner scrollers ``.tbl-scroll``, ``.rt-BaseTabList``,
   ``.genlog``, ``textarea`` and ``input`` are exempt, and so is anything inside a
   ``position: fixed`` box (the skip link, toasts and Reflex's "Built with Reflex" badge).
3. ``topbar``: the page name (``.topbar-title .t``) intersects neither the ⌘K trigger nor
   any other top-bar control and stays on one line; every top-bar control lies within
   [0, viewport width] and inside the bar's height.
4. ``rail``: every ``nav.rail`` link or button, after ``scrollIntoView({block: "nearest"})``,
   is hit by ``elementFromPoint`` at its centre with the shell unscrolled and lies inside the
   rail's visible box (not under its scrollbar). A Tab walk from the skip link through the
   rail (no pointer, no scripted scroll) keeps ``.topbar`` at the top, ``.shell.scrollTop``
   at 0 and each focused control fully on screen with its focus ring inside the rail's
   visible box, and every control with a label (a Radix tooltip trigger) shows that label,
   fully on screen, while it has focus. At 480 CSS px wide or less the rail is at most 56
   CSS px wide. A hit blocked by the "Built with Reflex" badge is noted, not failed.
5. ``palette``: Ctrl+K (falling back to clicking ``.cmd-trigger``) opens the palette; its
   panel lies inside the viewport with a side margin, the search field is fully visible,
   scrolling ``#cmdk-list`` to the end shows the last result, the dialog overlay itself does
   not scroll, and Escape closes it.
6. ``fixture``: client-side DOM edits only, reusing the real classes: a 280-character
   prompt in a cloned ``.think-query`` and a ``.gen-results`` block with long titles (with
   the Options panel shown), a 70-character token in the first two Library rows, and a long
   title and tag list in the first two Recently tagged rows. Checks 1 and 2 run again, and
   each fixture's own result gates: the prompt is at most two lines in the pill and in the
   results heading; every part of a long track row lies inside the results list; the Library
   table is no wider than before the fixture (the values wrapped in their cells) and Title,
   Artist and Album stay visible or reachable in the scrolling table region; the Recently
   tagged title column keeps at least 120px and Edit stays on screen.
7. ``scrollcue``: a region that scrolls sideways shows it: the Library table region shows its
   scrollbar and (at 768 CSS px or less) its edge shadows, and the narrow Settings tab strip
   shows its scrollbar.

It also records informational probes that later tasks read (rail width; the label of the
last navigation item in a scrolled rail on hover and on keyboard focus, with a screenshot of
each; grid column counts; Library table geometry; the Tagging title column).

Output: one JSON file per viewport and theme (``reflow-<W>x<H>-<theme>.json``), plus
``summary.json`` and ``shots/`` in ``--out`` (default: a new temp directory, outside the
repo). The console shows a pass/fail line per route.

What it touches: it drives a headless Chromium over a pipe and binds no port. It never reads
PlexMix's config, data directory or the keyring. It only navigates, presses Tab, opens and
closes the palette (which refreshes its recent prompts) and edits the DOM in the browser. It
never clicks Save, Sync, Generate, a vibe pill or anything else that changes data.

Exit status: 0 when no gating check fails, 1 when any does, 2 when the UI cannot be reached
or asks for a password that was not given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    from playwright.sync_api import Browser, Page, sync_playwright
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover - only without Playwright installed
    sys.stderr.write(
        "check_reflow.py needs Playwright: pip install playwright && playwright install chromium\n"
    )
    raise SystemExit(2)

# ── Constants ─────────────────────────────────────────────────────────

DEFAULT_BASE = "http://localhost:3000"
PASSWORD_ENV = "PLEXMIX_UI_PASSWORD"
ROUTES: tuple[str, ...] = (
    "/dashboard",
    "/generator",
    "/library",
    "/tagging",
    "/history",
    "/doctor",
    "/settings",
)
THEMES: tuple[str, ...] = ("light", "dark")
DEVICE_SCALE_FACTOR = 2
GATE_SIZES: tuple[tuple[int, int], ...] = ((320, 640), (640, 400), (720, 450))
# 390x844 (phone) and 320x256 (the audit's 320 run) per design D12; 512x384 (1024x768 at
# 200%) and 1024x768 are the extra sizes tasks 5.1, 6.1 and 7.3 verify.
INFORMATIONAL_SIZES: tuple[tuple[int, int], ...] = ((390, 844), (320, 256), (512, 384), (1024, 768))
INNER_SCROLLERS: tuple[str, ...] = (
    ".tbl-scroll",
    ".rt-BaseTabList",
    ".genlog",
    "textarea",
    "input",
)
# Reflex's fixed "Built with Reflex" badge in production builds (reflex.components.core.sticky).
REFLEX_BADGE = 'a[href^="https://reflex.dev"]'
NARROW_RAIL_MAX_VW = 480
NARROW_RAIL_MAX_WIDTH = 56.0
PALETTE_SIDE_MARGIN = 4.0
MAX_RAIL_TABS = 30
SETTLE_MS = 700
# How long a focused rail control may take to show its label (plexmix.js reopens a label
# that a focus scroll closed ~120 ms after the rail stops scrolling).
LABEL_TIMEOUT_MS = 1500
# Fixture limits: the clamped prompt (pill and results heading) and the Recently tagged
# title column (design D6, D8).
CLAMP_MAX_LINES = 2
TAG_TITLE_MIN_WIDTH = 120.0
# The Library table's edge shadows apply at this CSS width or less (redesign.css, zoom-fit
# pages block): its 36rem table cannot overflow its box on anything wider.
SCROLL_SHADOW_MAX_VW = 768
# Headless Chromium hides scrollbars and macOS draws overlay ones, so the page would be
# measured about 10px roomier than desktop Chrome or Edge with classic, always-present
# scrollbars (Windows, Linux, macOS "always show"): the worst case is what is checked.
BROWSER_ARGS: tuple[str, ...] = ("--disable-features=OverlayScrollbar",)
BROWSER_IGNORE_DEFAULT_ARGS: tuple[str, ...] = ("--hide-scrollbars",)

# Long-string fixtures (D12 item 6).
PROMPT_280 = (
    "late night drive through a rain-soaked city after a long week, windows down, neon "
    "reflections on wet asphalt, something moody and cinematic with slow-building synths, "
    "a little melancholy but hopeful, no aggressive vocals, mostly instrumental with the "
    "occasional soft female voice."
)
TOKEN_70 = "the_quick_brown_fox_jumps_over_the_lazy_dog_extended_clubmix_final.mp3"
LONG_TITLE = (
    "Symphony No. 9 in D Minor, Op. 125 'Choral': IV. Presto - Allegro assai - Allegro assai "
    "vivace (alla marcia) - Andante maestoso (Live at Royal Albert Hall, 2012 Remaster)"
)
LONG_ARTISTS = "Berliner Philharmoniker, Herbert von Karajan, Wiener Singverein, Gundula Janowitz"
LONG_TAGS = (
    "dreamy,atmospheric,melancholic,late-night,cinematic,slow-building,synth-heavy,nostalgic,"
    "introspective,rainy-day,hopeful,downtempo,ambient,lo-fi,night-drive,neon,widescreen,"
    "bittersweet"
)

# ── Browser-side code ─────────────────────────────────────────────────

# Shared helpers, pasted at the top of every evaluated function body.
_JS_HELPERS = r"""
  const vw = window.innerWidth, vh = window.innerHeight;
  const R = (r) => r ? {left: +r.left.toFixed(1), top: +r.top.toFixed(1), right: +r.right.toFixed(1),
    bottom: +r.bottom.toFixed(1), width: +r.width.toFixed(1), height: +r.height.toFixed(1)} : null;
  const cls = (el) => ((typeof el.className === 'string') ? el.className
    : (el.getAttribute && el.getAttribute('class')) || '').trim();
  const desc = (el) => {
    if (!el || !el.tagName) return String(el);
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    const c = cls(el);
    if (c) s += '.' + c.split(/\s+/).slice(0, 4).join('.');
    const name = el.getAttribute('aria-label') || el.getAttribute('title');
    if (name) s += ' [' + name.slice(0, 40) + ']';
    else if (!['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) {
      const t = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40);
      if (t) s += ' "' + t + '"';
    }
    return s;
  };
  const shown = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const meets = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5 &&
    Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5;
  const textLines = (el) => {
    if (!el || !el.firstChild) return 0;
    const range = document.createRange();
    range.selectNodeContents(el);
    const tops = new Set();
    for (const r of range.getClientRects()) if (r.width > 0 && r.height > 0) tops.add(Math.round(r.top));
    return tops.size;
  };
  // The rail's visible box (its padding box: inside the border, left of a scrollbar).
  const clipBox = (el) => {
    const r = el.getBoundingClientRect();
    const left = r.left + el.clientLeft, top = r.top + el.clientTop;
    return {left, top, right: left + el.clientWidth, bottom: top + el.clientHeight,
      width: el.clientWidth, height: el.clientHeight};
  };
  // How far a focused control's outline reaches past its border box (0 without one).
  const ringReach = (el) => {
    const cs = getComputedStyle(el);
    if (cs.outlineStyle === 'none') return 0;
    return Math.max(0, (parseFloat(cs.outlineOffset) || 0) + (parseFloat(cs.outlineWidth) || 0));
  };
  const within = (r, box, pad) => r.left - pad >= box.left - 0.5 && r.right + pad <= box.right + 0.5 &&
    r.top - pad >= box.top - 0.5 && r.bottom + pad <= box.bottom + 0.5;
  // Height of an element's horizontal scrollbar: 0 when it has none, or it is hidden.
  const hbar = (el) => {
    const cs = getComputedStyle(el);
    return el.offsetHeight - el.clientHeight - (parseFloat(cs.borderTopWidth) || 0) -
      (parseFloat(cs.borderBottomWidth) || 0);
  };
  const boxLines = (el) => {
    if (!el) return 0;
    const cs = getComputedStyle(el);
    let lh = parseFloat(cs.lineHeight);
    if (!lh) lh = parseFloat(cs.fontSize) * 1.2;
    const h = el.getBoundingClientRect().height - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    return Math.round(h / lh);
  };
"""


def _js(body: str) -> str:
    """Wrap ``body`` as a one-argument function expression with the shared helpers."""
    return "(args) => {" + _JS_HELPERS + body + "}"


# Ready when the shell is up, the page's loading bar is gone and nothing in main is busy
# (Dashboard/Doctor scans, skeletons, the Library table load).
SETTLED_JS = r"""() => {
  if (!document.querySelector('.shell')) return false;
  const main = document.querySelector('main#main-content') || document.querySelector('.scroll');
  if (!main) return false;
  if (document.querySelector('.shell .loadbar:not(.doctor-fix-loadbar)')) return false;
  return !main.querySelector('[aria-busy="true"]');
}"""

RESET_JS = r"""() => {
  for (const el of [document.querySelector('.shell'), document.getElementById('root'),
                    document.scrollingElement, document.querySelector('.scroll')]) {
    if (el) { el.scrollTop = 0; el.scrollLeft = 0; }
  }
  const rail = document.querySelector('nav.rail') || document.querySelector('.rail');
  if (rail) rail.scrollTop = 0;
  const a = document.activeElement;
  if (a && a !== document.body && a.blur) a.blur();
  return true;
}"""

# Checks 1 and 2.
MEASURE_JS = _js(
    r"""
  const de = document.documentElement;
  const sc = document.querySelector('.scroll');
  const out = {vw, vh, dpr: window.devicePixelRatio,
    docOverflowX: de.scrollWidth - de.clientWidth,
    scrollOverflowX: sc ? sc.scrollWidth - sc.clientWidth : null,
    offRight: [], offRightCount: 0, exemptFixed: 0, exemptScroller: 0};
  const allow = args.allow.join(',');
  const fixedMemo = new Map(), hiddenMemo = new Map();
  const inFixed = (el) => {
    if (!el || el === document.documentElement) return false;
    if (fixedMemo.has(el)) return fixedMemo.get(el);
    const v = getComputedStyle(el).position === 'fixed' || inFixed(el.parentElement);
    fixedMemo.set(el, v);
    return v;
  };
  const transparent = (el) => {
    if (!el || el === document.documentElement) return false;
    if (hiddenMemo.has(el)) return hiddenMemo.get(el);
    const v = parseFloat(getComputedStyle(el).opacity) === 0 || transparent(el.parentElement);
    hiddenMemo.set(el, v);
    return v;
  };
  const offenders = new Set();
  for (const el of document.body.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.right <= vw + 0.5) continue;
    if (r.width === 0 || r.height === 0 || (r.width <= 1 && r.height <= 1)) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') continue;
    if (el.closest(args.badge) || inFixed(el)) { out.exemptFixed++; continue; }
    if (el.parentElement && el.parentElement.closest(allow)) { out.exemptScroller++; continue; }
    if (transparent(el)) continue;
    offenders.add(el);
  }
  out.offRightCount = offenders.size;
  for (const el of offenders) {
    if (el.parentElement && offenders.has(el.parentElement)) continue;
    const r = el.getBoundingClientRect();
    out.offRight.push({el: desc(el), right: +r.right.toFixed(1), width: +r.width.toFixed(1)});
    if (out.offRight.length >= 40) break;
  }
  return out;
"""
)

# Check 3.
TOPBAR_JS = _js(
    r"""
  const bar = document.querySelector('.topbar');
  if (!bar) return {error: 'no .topbar'};
  const br = bar.getBoundingClientRect();
  const t = document.querySelector('.topbar-title .t');
  const tr = t ? t.getBoundingClientRect() : null;
  const trigger = document.querySelector('.cmd-trigger');
  const controls = [];
  for (const c of bar.querySelectorAll('button, a[href], input, select, [role=button]')) {
    if (!shown(c)) continue;
    const r = c.getBoundingClientRect();
    if (r.width <= 1 && r.height <= 1) continue;
    controls.push({el: desc(c), rect: R(r), isTrigger: c === trigger,
      onCanvas: r.left >= -0.5 && r.right <= vw + 0.5,
      inBar: r.top >= br.top - 1 && r.bottom <= br.bottom + 1,
      overlapsTitle: !!(tr && tr.width > 0 && meets(tr, r))});
  }
  return {bar: R(br), title: R(tr), titleText: t ? (t.textContent || '').trim() : null,
    titleLines: t ? textLines(t) : 0, titleTitleAttr: t ? t.getAttribute('title') : null,
    trigger: trigger ? R(trigger.getBoundingClientRect()) : null,
    triggerOverlap: !!(tr && trigger && tr.width > 0 && meets(tr, trigger.getBoundingClientRect())),
    controls};
"""
)

# Check 4, pointer half: scroll each rail control into view, then hit-test it with the
# shell put back where it belongs.
RAIL_HIT_JS = _js(
    r"""
  const rail = document.querySelector('nav.rail') || document.querySelector('.rail');
  if (!rail) return {error: 'no nav.rail'};
  const shell = document.querySelector('.shell'), root = document.getElementById('root');
  const topbar = document.querySelector('.topbar');
  const reset = () => {
    for (const el of [shell, root, document.scrollingElement]) if (el) { el.scrollTop = 0; el.scrollLeft = 0; }
  };
  const rr = rail.getBoundingClientRect(), rcs = getComputedStyle(rail);
  const out = {rail: R(rr), railWidth: +rr.width.toFixed(1), railOverflowY: rcs.overflowY,
    railScrollHeight: rail.scrollHeight, railClientHeight: rail.clientHeight, controls: []};
  rail.scrollTop = 0;
  for (const c of rail.querySelectorAll('a[href], button')) {
    if (!shown(c)) continue;
    const name = c.getAttribute('aria-label') || c.getAttribute('title') ||
      (c.textContent || '').trim().slice(0, 30) || desc(c);
    c.scrollIntoView({block: 'nearest', inline: 'nearest'});
    const shellTop = shell ? shell.scrollTop : 0;
    const docTop = (document.scrollingElement ? document.scrollingElement.scrollTop : 0) +
      (root ? root.scrollTop : 0);
    const topbarTop = topbar ? topbar.getBoundingClientRect().top : 0;
    reset();
    const r = c.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    let hit = null;
    if (cx >= 0 && cx < vw && cy >= 0 && cy < vh) hit = document.elementFromPoint(cx, cy);
    const ok = !!hit && (hit === c || c.contains(hit));
    // Inside the rail's visible box, and the rail not scrolled sideways to get it there
    // (overflow-x: hidden still scrolls programmatically, shifting every item).
    const clip = clipBox(rail), railScrollLeft = rail.scrollLeft;
    out.controls.push({name, ok, blockedByBadge: !ok && !!hit && !!hit.closest(args.badge),
      insideRail: within(r, clip, 0) && Math.abs(railScrollLeft) < 0.5, railClip: R(clip),
      railScrollLeft, hit: hit ? desc(hit) : null, rect: R(r), railScrollTop: rail.scrollTop,
      shellDisplacedBy: shellTop, docDisplacedBy: docTop, topbarTopDuringScroll: +topbarTop.toFixed(1)});
    rail.scrollLeft = 0;
  }
  rail.scrollTop = 0;
  reset();
  return out;
"""
)

# Check 4, keyboard half: where the Tab walk starts (the skip link, the shell's first stop).
TAB_START_JS = r"""() => {
  const rail = document.querySelector('nav.rail') || document.querySelector('.rail');
  if (rail) rail.scrollTop = 0;
  const a = document.activeElement;
  if (a && a !== document.body && a.blur) a.blur();
  const skip = document.querySelector('.skip-link');
  if (skip) { skip.focus(); return 'skip-link'; }
  return 'body';
}"""

FOCUS_STATE_JS = _js(
    r"""
  const a = document.activeElement;
  const rail = document.querySelector('nav.rail') || document.querySelector('.rail');
  const shell = document.querySelector('.shell'), root = document.getElementById('root');
  const topbar = document.querySelector('.topbar');
  const r = a ? a.getBoundingClientRect() : null;
  const inRail = !!(a && rail && rail.contains(a));
  const ring = inRail ? ringReach(a) : 0;
  return {el: a ? desc(a) : null, inRail, rect: R(r),
    fullyVisible: !!r && r.top >= -0.5 && r.bottom <= vh + 0.5 && r.left >= -0.5 && r.right <= vw + 0.5,
    ringReach: ring, railClip: inRail ? R(clipBox(rail)) : null,
    ringInRail: inRail ? within(r, clipBox(rail), ring) && Math.abs(rail.scrollLeft) < 0.5 : null,
    railScrollLeft: rail ? rail.scrollLeft : 0,
    tooltipTrigger: inRail && a.hasAttribute('data-state'),
    shellScrollTop: shell ? shell.scrollTop : 0, shellScrollLeft: shell ? shell.scrollLeft : 0,
    rootScrollTop: root ? root.scrollTop : 0,
    docScrollTop: document.scrollingElement ? document.scrollingElement.scrollTop : 0,
    railScrollTop: rail ? rail.scrollTop : 0,
    topbarTop: topbar ? +topbar.getBoundingClientRect().top.toFixed(1) : null};
"""
)

# Check 4, labels: the label (Radix tooltip) of the focused rail control, found through the
# trigger's aria-describedby (Radix sets it only while the label is open).
RAIL_FOCUS_LABEL_READY_JS = r"""() => {
  const a = document.activeElement;
  const id = a && a.getAttribute('aria-describedby');
  const el = id && document.getElementById(id);
  const tip = el && el.closest('.rt-TooltipContent');
  if (!tip) return false;
  const r = tip.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}"""

RAIL_FOCUS_LABEL_JS = _js(
    r"""
  const a = document.activeElement;
  const id = a && a.getAttribute('aria-describedby');
  const el = id && document.getElementById(id);
  const tip = el && el.closest('.rt-TooltipContent');
  if (!tip || !shown(tip)) return {shown: false, triggerState: a ? a.getAttribute('data-state') : null};
  const r = tip.getBoundingClientRect();
  const text = tip.querySelector('.rt-TooltipText') || tip;
  return {shown: true, text: (text.textContent || '').trim(), rect: R(r),
    inViewport: r.left >= -0.5 && r.right <= vw + 0.5 && r.top >= -0.5 && r.bottom <= vh + 0.5};
"""
)

# Informational: put the last navigation item in view in a scrolled rail, for the label probe.
RAIL_LABEL_PREP_JS = _js(
    r"""
  const rail = document.querySelector('nav.rail') || document.querySelector('.rail');
  if (!rail) return null;
  const items = [...rail.querySelectorAll('[data-route]')].filter(shown);
  const item = rail.querySelector('[data-route="/settings"]') || items[items.length - 1];
  if (!item) return null;
  rail.scrollTop = rail.scrollHeight;
  item.scrollIntoView({block: 'nearest'});
  const shell = document.querySelector('.shell');
  const displaced = shell ? shell.scrollTop : 0;
  if (shell) shell.scrollTop = 0;
  const r = item.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  return {route: item.getAttribute('data-route'), name: item.getAttribute('aria-label'),
    x, y, onScreen: x >= 0 && x < vw && y >= 0 && y < vh, railScrollTop: rail.scrollTop,
    shellDisplacedBy: displaced};
"""
)

# Informational: focus that navigation item from script (the browser scrolls it into view).
RAIL_FOCUS_JS = _js(
    r"""
  const rail = document.querySelector('nav.rail') || document.querySelector('.rail');
  const item = rail && rail.querySelector('[data-route="' + args.route + '"]');
  if (!item) return {focused: false};
  item.focus();
  const shell = document.querySelector('.shell'), topbar = document.querySelector('.topbar');
  const r = item.getBoundingClientRect();
  return {focused: document.activeElement === item, focusVisible: item.matches(':focus-visible'),
    rect: R(r), fullyVisible: r.top >= -0.5 && r.bottom <= vh + 0.5,
    railScrollTop: rail.scrollTop, shellScrollTop: shell ? shell.scrollTop : 0,
    topbarTop: topbar ? +topbar.getBoundingClientRect().top.toFixed(1) : null};
"""
)

RAIL_LABEL_JS = _js(
    r"""
  const rail = document.querySelector('nav.rail') || document.querySelector('.rail');
  if (!rail) return {kind: null};
  const item = rail.querySelector('[data-route="' + args.route + '"]');
  let label = null, kind = null;
  for (const el of document.querySelectorAll('.rt-TooltipContent')) {
    if (shown(el)) { label = el; kind = 'radix-tooltip'; }
  }
  if (!label && item) {
    const tip = item.querySelector('.rail-tip');
    if (tip && shown(tip) && parseFloat(getComputedStyle(tip).opacity) > 0.5) { label = tip; kind = 'rail-tip'; }
  }
  if (!label) return {kind: null};
  const r = label.getBoundingClientRect(), rr = rail.getBoundingClientRect();
  const clips = getComputedStyle(rail).overflowX !== 'visible';
  return {kind, text: (label.textContent || '').trim(), rect: R(r),
    inViewport: r.left >= -0.5 && r.right <= vw + 0.5 && r.top >= -0.5 && r.bottom <= vh + 0.5,
    clippedByRail: kind === 'rail-tip' && clips && (r.right > rr.right + 0.5 || r.left < rr.left - 0.5)};
"""
)

# Check 5. The palette is a Radix Themes dialog whose content carries the class "cmdk".
PALETTE_SELECTOR = ".cmdk"
PALETTE_JS = _js(
    r"""
  const c = document.querySelector('.cmdk');
  if (!c) return {open: false};
  const r = c.getBoundingClientRect();
  const input = document.getElementById('cmdk-input') || c.querySelector('input');
  const ir = input ? input.getBoundingClientRect() : null;
  const list = document.getElementById('cmdk-list') || c.querySelector('.cmdk-list');
  const overlay = c.closest('.rt-BaseDialogScroll, .rt-DialogScroll');
  const out = {open: true, vw, vh, panel: R(r), input: R(ir),
    inputVisible: !!ir && ir.top >= -0.5 && ir.bottom <= Math.min(vh, r.bottom) + 0.5 && ir.height > 0,
    overlayOverflowY: overlay ? overlay.scrollHeight - overlay.clientHeight : 0,
    list: null, lastItem: null, lastItemVisible: null};
  if (list) {
    list.scrollTop = list.scrollHeight;
    const lr = list.getBoundingClientRect();
    const items = [...list.querySelectorAll('.cmdk-item')]
      .filter((e) => !e.closest('[hidden]') && e.getBoundingClientRect().height > 0);
    out.list = Object.assign(R(lr), {scrollHeight: list.scrollHeight, clientHeight: list.clientHeight,
      items: items.length});
    if (items.length) {
      const lastEl = items[items.length - 1], li = lastEl.getBoundingClientRect();
      out.lastItem = Object.assign(R(li), {el: desc(lastEl)});
      out.lastItemVisible = li.top >= lr.top - 1 && li.bottom <= lr.bottom + 1 &&
        li.top >= -0.5 && li.bottom <= vh + 0.5;
    }
    list.scrollTop = 0;
  }
  return out;
"""
)

# Informational probes (no failures): what later tasks verify against.
PROBES_JS = _js(
    r"""
  const out = {};
  const rail = document.querySelector('nav.rail') || document.querySelector('.rail');
  if (rail) out.railWidth = +rail.getBoundingClientRect().width.toFixed(1);
  const bar = document.querySelector('.topbar');
  if (bar) out.topbarHeight = +bar.getBoundingClientRect().height.toFixed(1);
  out.grids = [];
  for (const sel of ['.stat-grid', '.qa-grid', '.pl-grid']) {
    for (const g of document.querySelectorAll(sel)) {
      if (!shown(g)) continue;
      const cols = getComputedStyle(g).gridTemplateColumns.split(/\s+/).filter(Boolean).length;
      out.grids.push({sel, el: desc(g), columns: cols, items: g.children.length,
        overflowX: g.scrollWidth - g.clientWidth, width: +g.getBoundingClientRect().width.toFixed(1)});
    }
  }
  const tabs = [...document.querySelectorAll('.settings-tab')];
  if (tabs.length) {
    const list = tabs[0].closest('[role=tablist]');
    /* innerText: the visible label only (Radix renders a display:none duplicate span). */
    out.settingsTabs = {labels: tabs.map((t) => (t.innerText || '').trim()),
      listOverflowX: list ? list.scrollWidth - list.clientWidth : null,
      listScrollbarHeight: list ? hbar(list) : null,
      listScrollbarWidth: list ? getComputedStyle(list).scrollbarWidth : null,
      listScrollSnapType: list ? getComputedStyle(list).scrollSnapType : null};
  }
  const table = document.querySelector('.tbl');
  if (table) {
    const wrap = table.parentElement, wr = wrap.getBoundingClientRect();
    const heads = {};
    for (const th of table.querySelectorAll('thead th')) {
      const name = (th.textContent || '').trim();
      if (!name) continue;
      const r = th.getBoundingClientRect();
      heads[name] = Object.assign(R(r), {inside: r.left >= wr.left - 0.5 && r.right <= wr.right + 0.5});
    }
    out.libraryTable = {wrapper: desc(wrap), overflowX: getComputedStyle(wrap).overflowX,
      clientWidth: wrap.clientWidth, scrollWidth: wrap.scrollWidth, tableWidth: table.scrollWidth,
      scrollbarHeight: hbar(wrap),
      edgeShadows: getComputedStyle(wrap).backgroundAttachment.split(',').some((a) => a.trim() === 'local'),
      headers: heads};
  }
  const edit = document.querySelector('button[id^="tag-edit-btn-"]');
  if (edit) {
    const row = edit.parentElement;
    out.taggingRow = {titleColumnWidth: +row.children[0].getBoundingClientRect().width.toFixed(1),
      tagsWidth: row.children[1] ? +row.children[1].getBoundingClientRect().width.toFixed(1) : null,
      row: desc(row)};
  }
  const glow = document.querySelector('.gen-hero-glow');
  if (glow) out.heroGlow = R(glow.getBoundingClientRect());
  return out;
"""
)

# Check 6 fixtures. Each returns "ok" or "skipped: <why>".
GENERATOR_FIXTURE_JS = _js(
    r"""
  const stage = document.querySelector('.gen-stage');
  if (!stage) return 'skipped: no .gen-stage';
  const adv = document.getElementById('gen-adv-panel');
  if (adv && adv.hidden) adv.hidden = false;
  // Thinking-stage pill: a clone of the real one when it is on screen, else the same markup.
  let pill;
  const realPill = document.querySelector('.think-query');
  if (realPill) {
    pill = realPill.cloneNode(true);
    pill.removeAttribute('id');
    pill.removeAttribute('tabindex');
  } else {
    pill = document.createElement('h1');
    pill.className = 'think-query';
    pill.innerHTML = '<svg width="15" height="15" aria-hidden="true"></svg>Curating <b></b>';
  }
  let b = pill.querySelector('b');
  if (!b) { b = document.createElement('b'); pill.appendChild(b); }
  b.textContent = '"' + args.prompt + '"';
  if (pill.hasAttribute('title')) pill.setAttribute('title', args.prompt);
  const think = document.createElement('div');
  think.className = 'think';
  think.setAttribute('data-reflow-fixture', 'think');
  think.innerHTML = '<div class="think-inner"><div class="think-head"></div>' +
    '<div class="gen-progress"><div class="pbar" role="progressbar"><div class="pfill" style="width:40%">' +
    '</div></div><span class="pct">40%</span><button class="btn btn-sm btn-ghost" type="button">Cancel' +
    '</button></div></div>';
  think.querySelector('.think-head').appendChild(pill);
  // Results: a clone of the real block when shown, else the markup generator.py renders.
  let res;
  const realRes = document.querySelector('.gen-results');
  if (realRes) {
    res = realRes.cloneNode(true);
    res.querySelectorAll('[id]').forEach((e) => e.removeAttribute('id'));
    res.querySelectorAll('.trk').forEach((e, i) => { if (i > 1) e.remove(); });
  } else {
    const row = (n) => '<li class="trk"><span class="tnum">' + n + '</span><div style="width:38px;' +
      'height:38px;border-radius:var(--radius-sm);background:linear-gradient(135deg, var(--brand-7), ' +
      'var(--brand-10));flex-shrink:0"></div><div class="tinfo"><div class="ttitle"></div>' +
      '<div class="tartist"></div></div><span class="talbum"></span><span class="tdur">3:42</span>' +
      '<button class="icon-btn trm" type="button" title="Remove" aria-label="Remove">x</button></li>';
    res = document.createElement('div');
    res.className = 'gen-results';
    res.innerHTML = '<div class="result-head"><span class="result-cover cover-v0" aria-hidden="true">' +
      '<span></span><span></span><span></span><span></span></span><div class="result-meta">' +
      '<div class="rk">Generated playlist</div><h1></h1><div class="result-stats"><span class="mono">' +
      '50 tracks</span> · <span class="mono">3 h 12 min</span> · ordered by <span class="badge ' +
      'badge-orange">Similarity</span></div><div class="result-actions">' +
      '<button class="btn btn-3 btn-blue" type="button">Save to Plex</button>' +
      '<button class="btn btn-3 btn-green" type="button">Save Locally</button>' +
      '<button class="btn btn-3 btn-soft" type="button">Export M3U</button>' +
      '<button class="btn btn-3 btn-outline" type="button">Regenerate</button></div></div></div>' +
      '<ol class="tbl-wrap trk-list" role="list" style="padding:6px">' + row(1) + row(2) + '</ol>';
  }
  res.setAttribute('data-reflow-fixture', 'results');
  const h1 = res.querySelector('.result-meta h1');
  if (h1) {
    h1.textContent = args.prompt;
    if (h1.hasAttribute('title')) h1.setAttribute('title', args.prompt);
  }
  const rows = res.querySelectorAll('.trk');
  const put = (row, sel, text) => { const e = row && row.querySelector(sel); if (e) e.textContent = text; };
  put(rows[0], '.ttitle', args.long); put(rows[0], '.tartist', args.artists); put(rows[0], '.talbum', args.long);
  put(rows[1], '.ttitle', args.token); put(rows[1], '.tartist', args.token); put(rows[1], '.talbum', args.token);
  stage.appendChild(think);
  stage.appendChild(res);
  return 'ok';
"""
)

LIBRARY_FIXTURE_JS = _js(
    r"""
  const table = document.querySelector('.tbl');
  if (!table) return 'skipped: no track table';
  const rows = table.querySelectorAll('tbody tr');
  if (rows.length < 2) return 'skipped: fewer than 2 track rows';
  const heads = [...table.querySelectorAll('thead th')].map((th) => (th.textContent || '').trim().toLowerCase());
  const col = (name) => heads.findIndex((h) => h.startsWith(name));
  const ti = col('title'), ar = col('artist'), al = col('album');
  if (ti < 0 || ar < 0 || al < 0) return 'skipped: no Title/Artist/Album columns';
  const set = (row, idx, text) => { const td = row.children[idx]; if (td) td.textContent = text; };
  set(rows[0], ti, args.token); set(rows[0], al, args.token);
  set(rows[1], ti, args.long); set(rows[1], ar, args.token);
  rows[0].setAttribute('data-reflow-fixture', 'row');
  rows[1].setAttribute('data-reflow-fixture', 'row');
  return 'ok';
"""
)

TAGGING_FIXTURE_JS = _js(
    r"""
  const edits = [...document.querySelectorAll('button[id^="tag-edit-btn-"]')];
  if (edits.length < 2) return 'skipped: fewer than 2 Recently tagged rows';
  const fill = (btn, title, tags) => {
    const row = btn.parentElement;
    const titleCol = row.children[0];
    const titleEl = titleCol && titleCol.children[0];
    const tagsEl = row.children[1];
    if (titleEl) titleEl.textContent = title;
    if (tagsEl && tagsEl !== btn) tagsEl.textContent = tags;
    row.setAttribute('data-reflow-fixture', 'row');
  };
  // Write the tag list the way the page itself shows tags: comma-joined as stored, or
  // ", "-joined once the row renders a display string (design D8).
  const shownTags = [...document.querySelectorAll('button[id^="tag-edit-btn-"]')]
    .map((btn) => btn.parentElement.children[1])
    .filter((el) => el && !el.matches('button'))
    .map((el) => el.textContent || '');
  const spaced = shownTags.some((t) => /,\s/.test(t)) && !shownTags.some((t) => /,\S/.test(t));
  const tags = spaced ? args.tags.split(',').join(', ') : args.tags;
  fill(edits[0], args.long, tags);
  fill(edits[1], args.token, args.token);
  return 'ok';
"""
)

FIXTURE_JS: dict[str, str] = {
    "/generator": GENERATOR_FIXTURE_JS,
    "/library": LIBRARY_FIXTURE_JS,
    "/tagging": TAGGING_FIXTURE_JS,
}

FIXTURE_PROBE_JS = _js(
    r"""
  const out = {};
  const pill = document.querySelector('[data-reflow-fixture="think"] .think-query');
  if (pill) {
    const b = pill.querySelector('b');
    out.thinkPill = {rect: R(pill.getBoundingClientRect()), title: pill.getAttribute('title'),
      bBoxLines: boxLines(b), bTextLines: textLines(b), whiteSpace: getComputedStyle(pill).whiteSpace};
  }
  const res = document.querySelector('[data-reflow-fixture="results"]');
  if (res) {
    const h1 = res.querySelector('.result-meta h1');
    if (h1) out.resultsTitle = {rect: R(h1.getBoundingClientRect()), boxLines: boxLines(h1),
      title: h1.getAttribute('title')};
    // Each part inside the results list's visible box (the list clips: .tbl-wrap is
    // overflow: hidden) and on screen.
    out.trackRows = [...res.querySelectorAll('.trk')].map((row) => {
      const box = clipBox(row.closest('.trk-list') || row.parentElement);
      const parts = {};
      for (const [k, sel] of [['title', '.ttitle'], ['artist', '.tartist'], ['album', '.talbum'],
                              ['duration', '.tdur'], ['remove', '.trm']]) {
        const e = row.querySelector(sel);
        const r = e ? e.getBoundingClientRect() : null;
        parts[k] = !!r && r.width > 0 && r.height > 0 && r.left >= box.left - 0.5 &&
          r.right <= box.right + 0.5 && r.left >= -0.5 && r.right <= vw + 0.5;
      }
      return parts;
    });
  }
  const libRows = document.querySelectorAll('tr[data-reflow-fixture]');
  if (libRows.length) {
    const table = libRows[0].closest('table'), wrap = table.parentElement, box = clipBox(wrap);
    const heads = {};
    for (const th of table.querySelectorAll('thead th')) {
      const name = (th.textContent || '').trim();
      if (!name) continue;
      const r = th.getBoundingClientRect();
      heads[name] = {visible: r.left >= box.left - 0.5 && r.right <= box.right + 0.5,
        reachable: r.left >= box.left - wrap.scrollLeft - 0.5 &&
          r.right <= box.left - wrap.scrollLeft + wrap.scrollWidth + 0.5,
        width: +r.width.toFixed(1)};
    }
    out.libraryTable = {wrapper: desc(wrap), overflowX: getComputedStyle(wrap).overflowX,
      clientWidth: wrap.clientWidth, scrollWidth: wrap.scrollWidth, tableWidth: table.scrollWidth,
      headers: heads,
      cellWidths: [...libRows[0].children].map((td) => +td.getBoundingClientRect().width.toFixed(1))};
  }
  const tagRows = [...document.querySelectorAll('[data-reflow-fixture="row"]')].filter((r) => r.querySelector('button[id^="tag-edit-btn-"]'));
  if (tagRows.length) {
    out.taggingRows = tagRows.map((row) => {
      const btn = row.querySelector('button[id^="tag-edit-btn-"]').getBoundingClientRect();
      return {titleColumnWidth: +row.children[0].getBoundingClientRect().width.toFixed(1),
        tagsWidth: row.children[1] ? +row.children[1].getBoundingClientRect().width.toFixed(1) : null,
        editVisible: btn.width > 0 && btn.left >= -0.5 && btn.right <= vw + 0.5};
    });
  }
  return out;
"""
)


# ── Data ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Viewport:
    """One CSS viewport; ``gate`` is False for the informational sizes."""

    width: int
    height: int
    gate: bool

    @property
    def label(self) -> str:
        return f"{self.width}x{self.height}"


class UiUnreachable(RuntimeError):
    """The UI did not answer or never rendered its shell."""


class SignInRequired(RuntimeError):
    """The UI shows its sign-in card and no password was given."""


def _failure(check: str, detail: str) -> dict[str, Any]:
    """One failure entry (route, viewport, theme and gate are added by the caller)."""
    return {"check": check, "detail": detail}


# ── Browser steps ─────────────────────────────────────────────────────


def open_route(page: Page, url: str, password: Optional[str]) -> None:
    """Load ``url`` and wait for the authenticated shell, signing in when asked."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightError as exc:
        raise UiUnreachable(f"{url} did not load: {exc.message.splitlines()[0]}") from None
    ensure_shell(page, url, password)


def ensure_shell(page: Page, url: str, password: Optional[str]) -> None:
    """Wait for the app shell; fill the sign-in card from ``PLEXMIX_UI_PASSWORD`` if shown."""
    try:
        page.wait_for_selector(".shell, #login-password", timeout=60_000)
    except PlaywrightTimeoutError:
        raise UiUnreachable(f"{url} never rendered the app shell or the sign-in card") from None
    if page.locator(".shell").count() == 0 and page.locator("#login-password").count():
        if not password:
            raise SignInRequired(f"the UI asks for a password; set {PASSWORD_ENV}")
        page.fill("#login-password", password)
        page.press("#login-password", "Enter")
        try:
            page.wait_for_selector(".shell", timeout=30_000)
        except PlaywrightTimeoutError:
            raise SignInRequired(f"signing in with {PASSWORD_ENV} did not open the app") from None


def settle(page: Page, timeout_s: float) -> Optional[str]:
    """Wait until the page has finished loading; returns a note when it never did.

    The ready test runs twice, ``SETTLE_MS`` apart, so a scan that an ``on_load`` handler
    starts just after the first pass (the Dashboard and Doctor checks) is waited out too.
    """
    note: Optional[str] = None
    for _ in range(2):
        try:
            page.wait_for_function(SETTLED_JS, timeout=timeout_s * 1000, polling=250)
        except PlaywrightTimeoutError:
            note = f"page still busy after {timeout_s:.0f}s (loading bar or aria-busy in main)"
            break
        page.evaluate("() => document.fonts ? document.fonts.ready.then(() => true) : true")
        page.wait_for_timeout(SETTLE_MS)
    return note


def apply_theme(page: Page, url: str, theme: str, password: Optional[str]) -> bool:
    """Store ``theme`` in localStorage, reload, and report whether the page shows it."""
    open_route(page, url, password)
    page.evaluate("(t) => { try { localStorage.setItem('theme', t); } catch (e) {} }", theme)
    page.reload(wait_until="domcontentloaded")
    ensure_shell(page, url, password)
    is_dark = bool(page.evaluate("() => document.documentElement.classList.contains('dark')"))
    return is_dark == (theme == "dark")


def focused_label(page: Page) -> dict[str, Any]:
    """The label the focused rail control shows, after up to ``LABEL_TIMEOUT_MS``."""
    try:
        page.wait_for_function(RAIL_FOCUS_LABEL_READY_JS, timeout=LABEL_TIMEOUT_MS, polling=50)
    except PlaywrightTimeoutError:
        pass
    label: dict[str, Any] = page.evaluate(RAIL_FOCUS_LABEL_JS, {})
    return label


def tab_walk(page: Page) -> dict[str, Any]:
    """Tab from the skip link through the whole rail, recording the shell after each step.

    No pointer and no scripted scrolling: the rail scrolls only as the browser brings each
    focused control into view, so every label is checked the way a keyboard user meets it.
    """
    start = page.evaluate(TAB_START_JS)
    steps: list[dict[str, Any]] = []
    entered = False
    for _ in range(MAX_RAIL_TABS):
        page.keyboard.press("Tab")
        page.wait_for_timeout(60)
        state: dict[str, Any] = page.evaluate(FOCUS_STATE_JS, {})
        if state.get("tooltipTrigger"):
            state["label"] = focused_label(page)
        steps.append(state)
        if state.get("inRail"):
            entered = True
        elif entered:
            break
    return {"start": start, "enteredRail": entered, "steps": steps}


def _rail_shot(page: Page, shot: Path) -> str:
    """Screenshot the rail and the space beside it (where its labels appear)."""
    size = page.viewport_size or {"width": 320, "height": 640}
    page.screenshot(
        path=str(shot),
        clip={"x": 0, "y": 0, "width": min(size["width"], 420), "height": size["height"]},
    )
    return str(shot)


def rail_label_probe(page: Page, shot: Optional[Path]) -> dict[str, Any]:
    """Hover, then focus, the last navigation item in a scrolled rail (informational).

    Records the label each one shows (``label`` on hover, ``labelOnFocus`` on keyboard
    focus) and, unless screenshots are off, saves ``shot`` and ``<shot>-focus.png``.
    """
    target: Optional[dict[str, Any]] = page.evaluate(RAIL_LABEL_PREP_JS, {})
    if not target:
        return {"error": "no rail navigation item"}
    route = str(target["route"])
    result: dict[str, Any] = {"target": target}
    if target.get("onScreen"):
        page.mouse.move(float(target["x"]), float(target["y"]))
        page.wait_for_timeout(900)
        result["label"] = page.evaluate(RAIL_LABEL_JS, {"route": route})
        if shot is not None:
            result["screenshot"] = _rail_shot(page, shot)
    page.mouse.move(1, 1)
    page.wait_for_timeout(300)
    # Keyboard modality first, so the programmatic focus matches :focus-visible.
    page.keyboard.press("Shift")
    result["focus"] = page.evaluate(RAIL_FOCUS_JS, {"route": route})
    page.wait_for_timeout(900)
    result["labelOnFocus"] = page.evaluate(RAIL_LABEL_JS, {"route": route})
    if shot is not None:
        result["focusScreenshot"] = _rail_shot(page, shot.with_name(shot.stem + "-focus.png"))
    page.evaluate(RESET_JS)
    page.wait_for_timeout(200)
    return result


def palette_check(page: Page) -> dict[str, Any]:
    """Open the palette, measure it, scroll its list to the end, and close it with Escape."""
    page.evaluate(RESET_JS)
    opened_by = "Control+K"
    page.keyboard.press("Control+K")
    try:
        page.wait_for_selector(PALETTE_SELECTOR, state="visible", timeout=3000)
    except PlaywrightTimeoutError:
        opened_by = "click .cmd-trigger"
        page.evaluate(
            "() => { const t = document.querySelector('.cmd-trigger'); if (t) t.click(); }"
        )
        try:
            page.wait_for_selector(PALETTE_SELECTOR, state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return {"open": False, "openedBy": None, "closed": True}
    page.wait_for_timeout(450)
    result: dict[str, Any] = page.evaluate(PALETTE_JS, {})
    result["openedBy"] = opened_by
    closed = False
    for _ in range(2):
        page.keyboard.press("Escape")
        try:
            page.wait_for_selector(PALETTE_SELECTOR, state="detached", timeout=3000)
            closed = True
            break
        except PlaywrightTimeoutError:
            continue
    result["closed"] = closed
    page.wait_for_timeout(150)
    page.evaluate(RESET_JS)
    return result


# ── Turning measurements into failures ────────────────────────────────


def overflow_failures(measure: dict[str, Any], check: str = "overflow") -> list[dict[str, Any]]:
    """Check 1: neither the document nor ``.scroll`` scrolls sideways."""
    out: list[dict[str, Any]] = []
    doc = measure.get("docOverflowX") or 0
    if doc > 0:
        out.append(_failure(check, f"document scrolls sideways by {doc}px"))
    scroll = measure.get("scrollOverflowX")
    if scroll is None:
        out.append(_failure(check, "no .scroll content area found"))
    elif scroll > 1:
        out.append(_failure(check, f".scroll scrolls sideways by {scroll}px"))
    return out


def offright_failures(measure: dict[str, Any], check: str = "offright") -> list[dict[str, Any]]:
    """Check 2: no visible element past the right edge (outside the inner scrollers)."""
    count = measure.get("offRightCount") or 0
    if not count:
        return []
    vw = measure.get("vw")
    worst = sorted(measure.get("offRight", []), key=lambda e: -e["right"])[:4]
    shown = "; ".join(f"{e['el']} right={e['right']}" for e in worst)
    return [_failure(check, f"{count} element(s) past the right edge (vw {vw}): {shown}")]


def topbar_failures(topbar: dict[str, Any]) -> list[dict[str, Any]]:
    """Check 3: page name clear of every control, one line; controls on canvas, in the bar."""
    if topbar.get("error"):
        return [_failure("topbar", str(topbar["error"]))]
    out: list[dict[str, Any]] = []
    title = topbar.get("titleText")
    if topbar.get("triggerOverlap"):
        out.append(_failure("topbar", f"page name '{title}' overlaps the ⌘K trigger"))
    others = [c["el"] for c in topbar["controls"] if c["overlapsTitle"] and not c["isTrigger"]]
    if others:
        out.append(_failure("topbar", f"page name '{title}' overlaps {', '.join(others)}"))
    if (topbar.get("titleLines") or 0) > 1:
        out.append(
            _failure("topbar", f"page name '{title}' wraps onto {topbar['titleLines']} lines")
        )
    off = [
        f"{c['el']} ({c['rect']['left']}..{c['rect']['right']})"
        for c in topbar["controls"]
        if not c["onCanvas"]
    ]
    if off:
        out.append(_failure("topbar", "top-bar control off canvas: " + "; ".join(off)))
    below = [c["el"] for c in topbar["controls"] if not c["inBar"]]
    if below:
        out.append(
            _failure("topbar", "top-bar control outside the bar's height: " + "; ".join(below))
        )
    return out


def rail_failures(rail: dict[str, Any], walk: dict[str, Any], vp: Viewport) -> list[dict[str, Any]]:
    """Check 4: every rail control hittable; the Tab walk never moves the shell or top bar."""
    if rail.get("error"):
        return [_failure("rail", str(rail["error"]))]
    out: list[dict[str, Any]] = []
    missed = [c for c in rail["controls"] if not c["ok"] and not c["blockedByBadge"]]
    if missed:
        parts = []
        for c in missed:
            centre = c["rect"]["top"] + c["rect"]["height"] / 2
            shift = c["shellDisplacedBy"] or c["docDisplacedBy"]
            note = f", revealing it scrolled the shell {shift}px" if shift else ""
            parts.append(f"{c['name']} (centre y={centre:.0f}{note})")
        out.append(_failure("rail", "rail control not reachable by pointer: " + "; ".join(parts)))
    clipped = [
        f"{c['name']} ({c['rect']['left']}..{c['rect']['right']} of {c['railClip']['right']}"
        + (f", rail scrolled {c['railScrollLeft']}px sideways" if c.get("railScrollLeft") else "")
        + ")"
        for c in rail["controls"]
        if c.get("insideRail") is False
    ]
    if clipped:
        out.append(
            _failure(
                "rail",
                "rail control cut off by the rail's edge or scrollbar: " + "; ".join(clipped),
            )
        )
    if vp.width <= NARROW_RAIL_MAX_VW and rail["railWidth"] > NARROW_RAIL_MAX_WIDTH + 0.5:
        out.append(
            _failure(
                "rail",
                f"rail is {rail['railWidth']}px wide at {vp.width}px "
                f"(at most {NARROW_RAIL_MAX_WIDTH:.0f}px)",
            )
        )
    if not walk.get("enteredRail"):
        out.append(_failure("rail", "Tab never reached the rail"))
        return out
    moved: list[str] = []
    hidden: list[str] = []
    ring_cut: list[str] = []
    unlabeled: list[str] = []
    label_off: list[str] = []
    for step in walk["steps"]:
        if not step.get("inRail"):
            continue
        if step.get("ringInRail") is False:
            rect, clip = step["rect"], step["railClip"]
            ring_cut.append(
                f"{step['el']} (ring {rect['left'] - step['ringReach']}.."
                f"{rect['right'] + step['ringReach']}, rail shows {clip['left']}..{clip['right']}"
                + (
                    f", rail scrolled {step['railScrollLeft']}px sideways"
                    if step.get("railScrollLeft")
                    else ""
                )
                + ")"
            )
        if step.get("tooltipTrigger"):
            label = step.get("label") or {}
            if not label.get("shown"):
                unlabeled.append(str(step["el"]))
            elif not label.get("inViewport"):
                label_off.append(f"{step['el']}: '{label.get('text')}' at {label.get('rect')}")
        shifts = []
        if step["shellScrollTop"] or step["shellScrollLeft"]:
            shifts.append(f"shell scrollTop {step['shellScrollTop']}")
        if step["rootScrollTop"] or step["docScrollTop"]:
            shifts.append(f"document scrollTop {step['rootScrollTop'] + step['docScrollTop']}")
        if step["topbarTop"] is not None and abs(step["topbarTop"]) > 0.5:
            shifts.append(f"top bar at y={step['topbarTop']}")
        if shifts:
            moved.append(f"{step['el']}: {', '.join(shifts)}")
        if not step["fullyVisible"]:
            hidden.append(str(step["el"]))
    if moved:
        out.append(_failure("rail", "focusing a rail control moved the shell: " + "; ".join(moved)))
    if hidden:
        out.append(
            _failure("rail", "focused rail control not fully on screen: " + "; ".join(hidden))
        )
    if ring_cut:
        out.append(
            _failure(
                "rail",
                "focused rail control's focus ring cut off by the rail: " + "; ".join(ring_cut),
            )
        )
    if unlabeled:
        out.append(
            _failure(
                "rail", "rail control shows no label on keyboard focus: " + "; ".join(unlabeled)
            )
        )
    if label_off:
        out.append(_failure("rail", "rail label not fully on screen: " + "; ".join(label_off)))
    return out


def palette_failures(palette: dict[str, Any]) -> list[dict[str, Any]]:
    """Check 5: the open palette fits the viewport and only its list scrolls."""
    if not palette.get("open"):
        return [_failure("palette", "the palette did not open (Ctrl+K and .cmd-trigger click)")]
    out: list[dict[str, Any]] = []
    vw, vh, panel = palette["vw"], palette["vh"], palette["panel"]
    if panel["bottom"] > vh + 0.5:
        out.append(_failure("palette", f"palette bottom {panel['bottom']} > viewport height {vh}"))
    if panel["top"] < -0.5:
        out.append(_failure("palette", f"palette top {panel['top']} is above the viewport"))
    if panel["left"] < PALETTE_SIDE_MARGIN or panel["right"] > vw - PALETTE_SIDE_MARGIN:
        out.append(
            _failure(
                "palette",
                f"palette spans {panel['left']}..{panel['right']} of {vw}px "
                f"(needs a {PALETTE_SIDE_MARGIN:.0f}px margin on both sides)",
            )
        )
    if not palette.get("inputVisible"):
        out.append(_failure("palette", f"search field not fully visible: {palette.get('input')}"))
    if palette.get("lastItemVisible") is False:
        last = palette.get("lastItem") or {}
        out.append(
            _failure(
                "palette",
                f"last result not visible after scrolling the list to the end "
                f"({last.get('el')}, bottom {last.get('bottom')}, vh {vh})",
            )
        )
    elif palette.get("lastItemVisible") is None:
        out.append(_failure("palette", "no visible results in #cmdk-list"))
    overlay = palette.get("overlayOverflowY") or 0
    if overlay > 1:
        out.append(_failure("palette", f"the dialog overlay scrolls by {overlay}px"))
    if not palette.get("closed"):
        out.append(_failure("palette", "the palette did not close on Escape"))
    return out


def scrollcue_failures(probes: dict[str, Any], vw: int) -> list[dict[str, Any]]:
    """Check 7: an inner region that scrolls sideways shows that it does.

    The Library table region shows its scrollbar and, at ``SCROLL_SHADOW_MAX_VW`` or less
    (where it can overflow), edge shadows on the side with hidden columns (the scrollbar sits
    under the last row, often below the fold); the narrow Settings tab strip shows its
    scrollbar. Checked with classic scrollbars, so a bar that is hidden reads as 0px.
    """
    out: list[dict[str, Any]] = []
    table = probes.get("libraryTable")
    if table and table.get("scrollWidth", 0) > table.get("clientWidth", 0) + 1:
        if not table.get("scrollbarHeight"):
            out.append(
                _failure("scrollcue", "the Library table scrolls sideways with no scrollbar")
            )
        if vw <= SCROLL_SHADOW_MAX_VW and not table.get("edgeShadows"):
            out.append(
                _failure("scrollcue", "the Library table scrolls sideways with no edge shadows")
            )
    tabs = probes.get("settingsTabs")
    if tabs and (tabs.get("listOverflowX") or 0) > 1 and not tabs.get("listScrollbarHeight"):
        out.append(_failure("scrollcue", "the Settings tab strip scrolls with no scrollbar"))
    return out


def fixture_failures(
    probe: dict[str, Any], before: Optional[dict[str, Any]] = None
) -> list[dict[str, Any]]:
    """Check 6, beyond checks 1 and 2: what each long-string fixture was built to catch.

    - The 280-character prompt stays within ``CLAMP_MAX_LINES`` lines in the thinking pill
      and in the results heading.
    - Every part of a long track row (title, artist, album, duration, remove) lies inside
      the results list's visible box.
    - The long Library values wrap in their cells: the table is no wider than before the
      fixture, and the Title, Artist and Album columns stay visible or reachable inside a
      scrolling table region.
    - The Recently tagged title column keeps at least ``TAG_TITLE_MIN_WIDTH`` px and Edit
      stays on screen.

    ``before`` is the route's informational probe (``PROBES_JS``) from before the fixture.
    """
    out: list[dict[str, Any]] = []
    pill = probe.get("thinkPill")
    if pill and pill.get("bBoxLines", 0) > CLAMP_MAX_LINES:
        out.append(
            _failure("fixture", f"thinking pill prompt shows {pill['bBoxLines']} lines (max 2)")
        )
    heading = probe.get("resultsTitle")
    if heading and heading.get("boxLines", 0) > CLAMP_MAX_LINES:
        out.append(
            _failure("fixture", f"results heading shows {heading['boxLines']} lines (max 2)")
        )
    for index, row in enumerate(probe.get("trackRows") or []):
        missing = [part for part, ok in row.items() if not ok]
        if missing:
            out.append(
                _failure(
                    "fixture",
                    f"track row {index + 1}: {', '.join(missing)} not inside the results list",
                )
            )
    table = probe.get("libraryTable")
    if table:
        width_before = ((before or {}).get("libraryTable") or {}).get("tableWidth")
        width_after = table.get("tableWidth")
        if width_before is not None and width_after is not None and width_after > width_before + 1:
            out.append(
                _failure(
                    "fixture",
                    f"a long Library value widened the table from {width_before}px to "
                    f"{width_after}px instead of wrapping in its cell",
                )
            )
        scrolls = table.get("overflowX") in ("auto", "scroll")
        for name, head in (table.get("headers") or {}).items():
            if not name.lower().startswith(("title", "artist", "album")):
                continue
            if not head.get("visible") and not (scrolls and head.get("reachable")):
                out.append(
                    _failure("fixture", f"Library column {name} is neither visible nor reachable")
                )
    for index, row in enumerate(probe.get("taggingRows") or []):
        width = row.get("titleColumnWidth") or 0
        if width < TAG_TITLE_MIN_WIDTH:
            out.append(
                _failure(
                    "fixture",
                    f"Recently tagged row {index + 1}: title column {width}px wide "
                    f"(at least {TAG_TITLE_MIN_WIDTH:.0f}px)",
                )
            )
        if not row.get("editVisible"):
            out.append(_failure("fixture", f"Recently tagged row {index + 1}: Edit off screen"))
    return out


# ── Per route / per viewport ──────────────────────────────────────────


def check_route(
    page: Page,
    base: str,
    route: str,
    vp: Viewport,
    password: Optional[str],
    settle_timeout: float,
    label_shot: Optional[Path],
    probe_label: bool,
) -> dict[str, Any]:
    """Run checks 1-7 on one route; returns its measurements and failures."""
    open_route(page, base + route, password)
    result: dict[str, Any] = {"url": page.url, "notes": []}
    busy = settle(page, settle_timeout)
    if busy:
        result["notes"].append(busy)
    page.evaluate(RESET_JS)
    failures: list[dict[str, Any]] = []
    badge_args = {"allow": list(INNER_SCROLLERS), "badge": REFLEX_BADGE}

    measure: dict[str, Any] = page.evaluate(MEASURE_JS, badge_args)
    result["measure"] = measure
    failures += overflow_failures(measure) + offright_failures(measure)

    topbar: dict[str, Any] = page.evaluate(TOPBAR_JS, {})
    result["topbar"] = topbar
    failures += topbar_failures(topbar)

    result["probes"] = page.evaluate(PROBES_JS, {})
    failures += scrollcue_failures(result["probes"], vp.width)

    rail: dict[str, Any] = page.evaluate(RAIL_HIT_JS, badge_args)
    walk = tab_walk(page)
    page.evaluate(RESET_JS)
    result["rail"] = rail
    result["tabWalk"] = walk
    failures += rail_failures(rail, walk, vp)
    blocked = [c["name"] for c in rail.get("controls", []) if c.get("blockedByBadge")]
    if blocked:
        result["notes"].append(
            "rail control under the Built with Reflex badge: " + ", ".join(blocked)
        )

    if probe_label:
        result["railLabel"] = rail_label_probe(page, label_shot)

    palette = palette_check(page)
    result["palette"] = palette
    failures += palette_failures(palette)

    if route in FIXTURE_JS:
        args = {
            "prompt": PROMPT_280,
            "token": TOKEN_70,
            "long": LONG_TITLE,
            "artists": LONG_ARTISTS,
            "tags": LONG_TAGS,
        }
        status = str(page.evaluate(FIXTURE_JS[route], args))
        fixture: dict[str, Any] = {"status": status}
        if status == "ok":
            page.wait_for_timeout(250)
            fmeasure: dict[str, Any] = page.evaluate(MEASURE_JS, badge_args)
            fixture["measure"] = fmeasure
            fprobe: dict[str, Any] = page.evaluate(FIXTURE_PROBE_JS, {})
            fixture["probe"] = fprobe
            failures += overflow_failures(fmeasure, "fixture") + offright_failures(
                fmeasure, "fixture"
            )
            failures += fixture_failures(fprobe, result["probes"])
        else:
            result["notes"].append(f"fixture {status}")
        result["fixture"] = fixture

    result["failures"] = failures
    return result


def run_viewport(
    browser: Browser,
    args: argparse.Namespace,
    vp: Viewport,
    theme: str,
    routes: Sequence[str],
    password: Optional[str],
    out_dir: Path,
) -> dict[str, Any]:
    """Every route at one viewport and theme, in its own browser context."""
    context = browser.new_context(
        viewport={"width": vp.width, "height": vp.height},
        device_scale_factor=DEVICE_SCALE_FACTOR,
        reduced_motion="reduce",
    )
    page = context.new_page()
    page.set_default_timeout(15_000)
    report: dict[str, Any] = {
        "tool": "check_reflow.py",
        "base": args.base,
        "viewport": {
            "width": vp.width,
            "height": vp.height,
            "deviceScaleFactor": DEVICE_SCALE_FACTOR,
            "gate": vp.gate,
        },
        "theme": theme,
        "scrollbars": "classic",
        "routes": {},
        "failures": [],
    }
    try:
        report["themeApplied"] = apply_theme(page, args.base + routes[0], theme, password)
        if not report["themeApplied"]:
            report["failures"].append(
                {
                    "check": "theme",
                    "detail": f"the {theme} theme did not apply",
                    "route": routes[0],
                    "viewport": vp.label,
                    "theme": theme,
                    "gate": vp.gate,
                }
            )
        for index, route in enumerate(routes):
            shot = None
            if not args.no_screenshots:
                shot = out_dir / "shots" / f"rail-label-{vp.label}-{theme}.png"
                shot.parent.mkdir(parents=True, exist_ok=True)
            route_result = check_route(
                page,
                args.base,
                route,
                vp,
                password,
                args.settle_timeout,
                shot,
                probe_label=index == 0,
            )
            for failure in route_result["failures"]:
                failure.update(route=route, viewport=vp.label, theme=theme, gate=vp.gate)
            report["routes"][route] = route_result
            report["failures"].extend(route_result["failures"])
    finally:
        context.close()
    report["passed"] = not report["failures"]
    path = out_dir / f"reflow-{vp.label}-{theme}.json"
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


# ── CLI ───────────────────────────────────────────────────────────────


def parse_size(text: str) -> tuple[int, int]:
    """``"720x450"`` -> ``(720, 450)``."""
    try:
        width, height = (int(part) for part in text.lower().split("x", 1))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, got {text!r}") from None
    if width < 100 or height < 100:
        raise argparse.ArgumentTypeError(f"viewport {text!r} is too small")
    return width, height


def parse_list(text: str) -> list[str]:
    """A comma-separated option value -> its non-empty items."""
    return [item.strip() for item in text.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    """The command-line interface."""
    parser = argparse.ArgumentParser(
        description="Check PlexMix web UI reflow at 320 CSS px and 200% zoom (design D12).",
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"UI URL (default {DEFAULT_BASE})")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="directory for the JSON and screenshots (default: a new temp directory)",
    )
    parser.add_argument(
        "--informational",
        action="store_true",
        help="also run 390x844, 320x256, 512x384 and 1024x768 (reported, never gating)",
    )
    parser.add_argument(
        "--viewport",
        action="append",
        type=parse_size,
        default=None,
        metavar="WxH",
        help="gate on this CSS viewport instead of the default three (repeatable)",
    )
    parser.add_argument(
        "--routes", type=parse_list, default=list(ROUTES), help="comma-separated route subset"
    )
    parser.add_argument(
        "--themes", type=parse_list, default=list(THEMES), help="comma-separated: light,dark"
    )
    parser.add_argument(
        "--settle-timeout",
        type=float,
        default=45.0,
        help="seconds to wait for a page to finish loading (default 45)",
    )
    parser.add_argument(
        "--no-screenshots", action="store_true", help="skip the rail-label screenshots"
    )
    return parser


def viewports_for(args: argparse.Namespace) -> list[Viewport]:
    """The gate sizes (or ``--viewport``), plus the informational ones when asked."""
    sizes = args.viewport or list(GATE_SIZES)
    out = [Viewport(w, h, True) for w, h in sizes]
    if args.informational:
        out += [Viewport(w, h, False) for w, h in INFORMATIONAL_SIZES if (w, h) not in sizes]
    return out


def print_report(report: dict[str, Any]) -> None:
    """One line per route, then its failures."""
    vp = report["viewport"]
    tag = "" if vp["gate"] else " (informational)"
    print(f"\n== {vp['width']}x{vp['height']} {report['theme']}{tag}")
    for route, result in report["routes"].items():
        fails = result["failures"]
        status = "PASS" if not fails else f"FAIL ({len(fails)})"
        notes = f"  [{'; '.join(result['notes'])}]" if result["notes"] else ""
        print(f"  {route:<11} {status}{notes}")
        for failure in fails:
            detail = failure["detail"]
            if len(detail) > 220:
                detail = detail[:217] + "..."
            print(f"      {failure['check']}: {detail}")
    for failure in report["failures"]:
        if failure["check"] == "theme":
            print(f"  theme: {failure['detail']}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the matrix; 0 when nothing gating fails, 1 on any gating failure, 2 if no UI."""
    args = build_parser().parse_args(argv)
    args.base = args.base.rstrip("/")
    bad_routes = [r for r in args.routes if not r.startswith("/")]
    bad_themes = [t for t in args.themes if t not in THEMES]
    if bad_routes or bad_themes or not args.routes or not args.themes:
        sys.stderr.write("check_reflow.py: routes start with '/', themes are light and dark\n")
        return 2
    out_dir: Path = args.out or Path(tempfile.mkdtemp(prefix="plexmix-reflow-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    password = os.environ.get(PASSWORD_ENV) or None
    viewports = viewports_for(args)
    started = time.time()
    reports: list[dict[str, Any]] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                args=list(BROWSER_ARGS), ignore_default_args=list(BROWSER_IGNORE_DEFAULT_ARGS)
            )
            try:
                for vp in viewports:
                    for theme in args.themes:
                        report = run_viewport(
                            browser, args, vp, theme, args.routes, password, out_dir
                        )
                        print_report(report)
                        reports.append(report)
            finally:
                browser.close()
    except (UiUnreachable, SignInRequired) as exc:
        sys.stderr.write(f"check_reflow.py: {exc}\n")
        return 2
    gating = [f for r in reports for f in r["failures"] if f["gate"]]
    informational = [f for r in reports for f in r["failures"] if not f["gate"]]
    exit_code = 1 if gating else 0
    summary = {
        "tool": "check_reflow.py",
        "base": args.base,
        "routes": list(args.routes),
        "themes": list(args.themes),
        "viewports": [{"width": v.width, "height": v.height, "gate": v.gate} for v in viewports],
        "seconds": round(time.time() - started, 1),
        "gatingFailures": gating,
        "informationalFailures": informational,
        "exitCode": exit_code,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"\n{len(gating)} gating failure(s), {len(informational)} informational; "
        f"JSON in {out_dir}"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
