/* PlexMix web UI — global page scripts.
 *
 * Loaded once from <head> via head_components in plexmix_ui/plexmix_ui.py.
 * These used to be rx.script() calls, which Reflex 0.8 compiles to
 * react-helmet; under React 19 StrictMode (dev mode, the default for
 * `plexmix ui`) Helmet drops them, so ⌘K, g+key and the saved
 * density/accent preferences silently never worked. Every block is
 * idempotent (window-flag guarded).
 */

/* 1. Before first paint: re-apply persisted density / accent intensity, and
 *    tag <html data-platform="mac"|"other"> so CSS shows the platform's
 *    shortcut label (.kbd-mac "⌘K" vs .kbd-other "Ctrl K"). */
(function(){
  try {
    var d=localStorage.getItem('pm_density'); if(d) document.documentElement.setAttribute('data-density', d);
    var a=localStorage.getItem('pm_accent'); if(a) document.documentElement.setAttribute('data-accent', a);
  } catch(e){}
  try {
    var nav=window.navigator||{};
    // userAgentData only exists in secure contexts (https, localhost); a plain
    // http LAN address falls back to navigator.platform, then the UA string.
    var plat=(nav.userAgentData&&nav.userAgentData.platform)||nav.platform||nav.userAgent||'';
    document.documentElement.dataset.platform=/mac|iphone|ipad|ipod/i.test(plat)?'mac':'other';
  } catch(e){}
})();

/* 2. Keyboard (webui-input-responsiveness D4, D7, D9, D12, D14).
 *
 * 2a. window.__pmPalette — the ⌘K palette controller. The palette is an
 *     uncontrolled Radix dialog (navbar.py): filtering, the highlight and
 *     Enter all run here in the browser, so typing never waits on the server.
 *     DOM contract: #cmdk-input (role=combobox) · #cmdk-list (role=listbox) ·
 *     role=group blocks (.cmdk-group) · role=option rows (.cmdk-item,
 *     id="cmdk-opt-<id>", data-search="<title sub keywords>") · the Generate
 *     row is the option holding the empty span.cmdk-echo · #cmdk-status
 *     (polite, sr-only). Python never renders hidden / aria-selected /
 *     aria-activedescendant, so React never overwrites what this sets.
 *     - init on focusin of the palette (Radix auto-focuses #cmdk-input): the
 *       highlight resets to the first visible row and `ran` to false; a
 *       MutationObserver on #cmdk-list (childList, subtree) re-applies the
 *       filter when rows arrive late (recent prompts, the cancel row), keeping
 *       the highlighted row while it is still visible. It is disconnected once
 *       the input has left the DOM.
 *     - filter: every whitespace-separated term of the trimmed, lower-cased
 *       query must occur in the row's data-search (its text if absent); other
 *       rows get `hidden`, and so does a group with no visible row. The
 *       Generate row shows exactly when the query is not empty and its echo
 *       gets the trimmed query.
 *     - highlight: one row has aria-selected="true" (the rest "false") and is
 *       the input's aria-activedescendant. It resets to the first visible row
 *       on open and on every query change; ArrowUp/ArrowDown wrap and scroll it
 *       into view; pointermove (not mouseover, so rows scrolling under a still
 *       pointer never steal it) moves it; Enter (never while composing) clicks
 *       it. Clicking any row marks `ran`.
 *     - closeAutoFocus(e): the dialog's onCloseAutoFocus (navbar.py binds it
 *       as a client-side callback, so it runs synchronously, never behind a
 *       server round trip). It calls restoreFocus() and, when that placed
 *       focus, preventDefault()s Radix's own focus of the trigger.
 *     - restoreFocus(): focuses `returnTo` (recorded by ⌘K and "/" in 2b) when
 *       it is still connected and no command ran, else the trigger when focus
 *       fell to <body>; a trusted click on the trigger clears `returnTo`, so
 *       the trigger keeps focus. It never takes back focus the user moved
 *       elsewhere, and a late call that arrives after the palette re-opened is
 *       ignored. Returns whether it placed focus.
 * 2b. Shortcuts: ⌘K / Ctrl+K and "/" open the palette by clicking .cmd-trigger
 *     (⌘K again closes it); g then d/g/l/t/h/x/s within 600 ms clicks the
 *     rail's [data-route="<path>"] item (client-side navigation, no reload;
 *     nothing happens if it is absent). Escape and outside clicks are Radix's.
 *     Guards: "/", "g" and the chord's second key are ignored with ⌘/Ctrl/Alt
 *     held, on key repeat, or while focus is in a text INPUT (any type but
 *     checkbox, radio, range, button-like, color, file and hidden), TEXTAREA,
 *     SELECT, contenteditable or [role=combobox]; every shortcut is ignored while IME
 *     composition runs or a [role=dialog]/[role=alertdialog] other than the
 *     palette is open; "/" and chords are also ignored while the palette is open.
 * 2c. Form helpers (never during IME composition, never on key repeat):
 *     - Enter in an input of form[data-enter-submit] submits the form
 *       (requestSubmit with native validation skipped: the filter setters
 *       sanitise partial values instead of blocking Enter with a bubble);
 *     - Enter inside [data-enter="none"] does nothing (no implicit submit);
 *     - ⌘/Ctrl+Enter in textarea[data-submit-shortcut] submits its form;
 *       plain and Shift+Enter keep inserting a newline;
 *     - like the browser's implicit submission, a disabled default (first
 *       submit) button blocks both, so a busy or not-ready Generate stays off;
 *     - on input in a field with data-counter="<id>" (and on compositionend
 *       instead of mid-composition), the first text node of #<id> becomes
 *       "<length>/<maxlength>" (the node React rendered, so React's own later
 *       writes still land);
 *     - on popstate (browser Back/Forward) and pagehide, a focused text field
 *       inside a form is blurred first, so its blur commit is sent before the
 *       route change unmounts it (compose drafts survive Back);
 *     - a click on a [data-clear-form] button empties its form's text fields at
 *       once (Library's Clear; the server resets state without rx.set_value);
 *     - typing into a field marked data-digits (the year and stale-days filters)
 *       only ever inserts ASCII digits; a paste keeps just its digits.
 */

/* 2a. Palette controller. */
(function () {
  if (window.__pmPaletteCtl__) return; window.__pmPaletteCtl__ = true;
  var P = window.__pmPalette || (window.__pmPalette = {});
  if (!('returnTo' in P)) P.returnTo = null;
  P.ran = false;

  var OPTION = '[role="option"]';
  var cur = null;                  // {input, list, obs, q, hi} while the palette is mounted
  var autoId = 0;

  function closestEl(el, sel) {
    return el && el.nodeType === 1 && el.closest ? el.closest(sel) : null;
  }
  function setText(el, text) { if (el && el.textContent !== text) el.textContent = text; }
  function setHidden(el, h) { if (el.hidden !== h) el.hidden = h; }
  function setAttr(el, name, v) { if (el.getAttribute(name) !== v) el.setAttribute(name, v); }
  function isGen(row) { return !!row.querySelector('.cmdk-echo'); }
  function shown(row, list) {    // not hidden itself nor inside a hidden group
    for (var n = row; n && n !== list; n = n.parentElement) { if (n.hidden) return false; }
    return !!n;
  }
  function visible() {
    if (!cur) return [];
    var rows = cur.list.querySelectorAll(OPTION), out = [];
    for (var i = 0; i < rows.length; i++) { if (shown(rows[i], cur.list)) out.push(rows[i]); }
    return out;
  }

  function teardown() {
    if (cur && cur.obs) cur.obs.disconnect();
    cur = null;
  }
  function live() {                 // the mounted palette, or null (tears down a stale one)
    if (cur && !cur.input.isConnected) teardown();
    return cur;
  }

  function highlight(row, scroll) {
    var c = cur; if (!c) return;
    var rows = c.list.querySelectorAll(OPTION);
    for (var i = 0; i < rows.length; i++) setAttr(rows[i], 'aria-selected', rows[i] === row ? 'true' : 'false');
    if (row) setAttr(c.input, 'aria-activedescendant', row.id);
    else if (c.input.hasAttribute('aria-activedescendant')) c.input.removeAttribute('aria-activedescendant');
    var moved = row !== c.hi;
    c.hi = row;
    if (row && moved && scroll && row.scrollIntoView) row.scrollIntoView({ block: 'nearest' });
  }

  // Filter the rows for the current query; reset=true moves the highlight to
  // the first visible row, otherwise it stays on its row while that is visible.
  function apply(reset) {
    var c = cur; if (!c) return;
    var raw = c.input.value || '';
    var q = raw.trim();
    var terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    var rows = c.list.querySelectorAll(OPTION);
    var matches = 0, hasGen = false, i;
    for (i = 0; i < rows.length; i++) {
      var r = rows[i], show;
      if (!r.id) r.id = 'cmdk-opt-auto-' + (++autoId);
      if (isGen(r)) {
        hasGen = true;
        show = terms.length > 0;
        setText(r.querySelector('.cmdk-echo'), q);
      } else {
        var hay = (r.getAttribute('data-search') || r.textContent || '').toLowerCase();
        show = true;
        for (var k = 0; k < terms.length; k++) { if (hay.indexOf(terms[k]) < 0) { show = false; break; } }
        if (show) matches++;
      }
      setHidden(r, !show);
    }
    var groups = c.list.querySelectorAll('[role="group"],.cmdk-group');
    for (i = 0; i < groups.length; i++) {
      var opts = groups[i].querySelectorAll(OPTION), any = false;
      for (var j = 0; j < opts.length; j++) { if (!opts[j].hidden) { any = true; break; } }
      setHidden(groups[i], !any);
    }
    var vis = visible();
    var hi = c.hi;
    if (reset || !hi || vis.indexOf(hi) < 0) hi = vis[0] || null;
    highlight(hi, true);
    c.q = raw;
    var msg;
    if (q && !matches) {
      msg = hasGen ? 'No commands match. Press Enter to generate a playlist for “' + q + '”.'
                   : 'No commands match.';
    } else {
      msg = matches + (matches === 1 ? ' command' : ' commands');
    }
    setText(document.getElementById('cmdk-status'), msg);
  }

  function init(input) {
    if (live() && cur.input === input) return;
    teardown();
    var list = document.getElementById('cmdk-list') || closestEl(input, '.cmdk');
    if (!list) return;
    cur = { input: input, list: list, obs: null, q: null, hi: null };
    P.ran = false;
    apply(true);
    if (typeof MutationObserver === 'function') {
      var c = cur;
      c.obs = new MutationObserver(function () {
        if (cur !== c || !live()) { c.obs.disconnect(); return; }
        apply(false);
        c.obs.takeRecords();         // drop the records of our own echo write
      });
      c.obs.observe(list, { childList: true, subtree: true });
    }
  }

  document.addEventListener('focusin', function (e) {
    var t = e.target;
    if (t && t.id === 'cmdk-input') { init(t); return; }
    if (closestEl(t, '.cmdk')) {
      var inp = document.getElementById('cmdk-input');
      if (inp) init(inp);
      return;
    }
    live();                          // focus left a closed palette: drop its observer
  }, true);

  document.addEventListener('input', function (e) {
    var t = e.target;
    if (!t || t.id !== 'cmdk-input') return;
    init(t);
    apply(true);
  }, true);

  document.addEventListener('keydown', function (e) {
    var t = e.target;
    if (!t || t.id !== 'cmdk-input' || e.defaultPrevented) return;
    var plain = !(e.altKey || e.ctrlKey || e.metaKey || e.shiftKey);
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && plain) {
      e.preventDefault();
      init(t);
      if (!cur) return;
      if (cur.q !== t.value) apply(true);
      var vis = visible(), n = vis.length;
      if (!n) return;
      var i = vis.indexOf(cur.hi);
      i = e.key === 'ArrowDown' ? (i + 1) % n : (i <= 0 ? n - 1 : i - 1);
      highlight(vis[i], true);
      return;
    }
    if (e.key === 'Enter' && plain) {
      if (e.isComposing || e.keyCode === 229) return;   // confirming an IME composition
      e.preventDefault();
      if (e.repeat) return;
      init(t);
      if (!cur) return;
      if (cur.q !== t.value) apply(true);
      var hi = cur.hi;
      if (hi && hi.isConnected && shown(hi, cur.list)) { P.ran = true; hi.click(); }
    }
  });

  document.addEventListener('pointermove', function (e) {
    if (!cur) return;
    var row = closestEl(e.target, OPTION);
    if (!row || row === cur.hi || !live() || !cur.list.contains(row) || !shown(row, cur.list)) return;
    highlight(row, false);
  }, { capture: true, passive: true });

  document.addEventListener('click', function (e) {
    if (!cur) return;
    var row = closestEl(e.target, OPTION);
    if (row && live() && cur.list.contains(row)) P.ran = true;
  }, true);

  P.restoreFocus = function () {
    // A call made after the palette has been opened again belongs to an earlier
    // close and must leave the new opener alone.
    if (document.getElementById('cmdk-input')) return false;
    var target = P.returnTo, ran = P.ran;
    P.returnTo = null;
    live();
    if (ran) return false;           // a command ran: focus belongs to its result
    var a = document.activeElement;
    var trigger = document.querySelector('.cmd-trigger');
    var fell = !a || a === document.body || a === document.documentElement || !a.isConnected;
    if (!fell && a !== trigger) return false;   // the user already moved focus on
    if (target && target.isConnected && target !== a) {
      try { target.focus(); } catch (err) {}
      if (document.activeElement === target) return true;
    }
    if (fell && trigger) {
      try { trigger.focus(); } catch (err) {}
      return document.activeElement === trigger;
    }
    return false;
  };
  // The dialog's onCloseAutoFocus, bound in navbar.py as a client-side callback (not
  // a queued Reflex event), so it runs synchronously when Radix would focus the
  // trigger. When restoreFocus() placed focus, or focus already sits on a real
  // element (moved on by the user, or the palette re-opened), it cancels Radix's own
  // move to the trigger. When a command ran, Radix focuses the trigger as before
  // and the command's result (e.g. the new page's heading) takes it from there.
  P.closeAutoFocus = function (e) {
    var keep = false;
    try { keep = P.restoreFocus(); } catch (err) {}
    if (!keep && !P.ran) {
      var a = document.activeElement;
      keep = !!(a && a !== document.body && a !== document.documentElement && a.isConnected);
    }
    if (keep && e && typeof e.preventDefault === 'function') e.preventDefault();
    return keep;
  };
  P.refresh = function () { if (live()) apply(false); };
})();

/* 2b. Global shortcuts. */
if (!window.__plexmix_keys__) {
  window.__plexmix_keys__ = true;
  (function () {
    var P = window.__pmPalette || (window.__pmPalette = { returnTo: null, ran: false });
    var DIALOGS = '[role="dialog"],[role="alertdialog"]';
    var ROUTES = { d: '/dashboard', g: '/generator', l: '/library', t: '/tagging',
                   h: '/history', x: '/doctor', s: '/settings' };
    var CHORD_MS = 600;
    var gAt = 0;                     // when a "g" started a chord (0: none pending)

    function closestEl(el, sel) {
      return el && el.nodeType === 1 && el.closest ? el.closest(sel) : null;
    }
    // <input> types that take no typed text: "/" and g-chords still work on them.
    var NON_TEXT = { checkbox: 1, radio: 1, range: 1, button: 1, submit: 1, reset: 1,
                     color: 1, file: 1, image: 1, hidden: 1 };
    function editable(t) {
      if (!t || t.nodeType !== 1) return false;
      var tag = t.tagName;
      if (tag === 'INPUT') return !NON_TEXT[(t.type || 'text').toLowerCase()];
      if (tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable) return true;
      return !!closestEl(t, '[role="combobox"]');
    }
    function isPaletteDialog(d) {
      try { return !!(d.closest('.cmdk') || d.querySelector('.cmdk')); } catch (e) { return false; }
    }
    // Which dialogs are open: {palette, other}. Radix keeps a closing dialog
    // mounted with data-state="closed" for its exit animation.
    function dialogs() {
      var st = { palette: false, other: false };
      var all = document.querySelectorAll(DIALOGS);
      for (var i = 0; i < all.length; i++) {
        var d = all[i];
        if (d.getAttribute('data-state') === 'closed' || d.hidden || !d.getClientRects().length) continue;
        if (isPaletteDialog(d)) st.palette = true; else st.other = true;
      }
      return st;
    }
    function remember() {
      var a = document.activeElement;
      if (closestEl(a, '.cmdk')) return;          // ⌘K inside the palette closes it: keep the opener
      P.returnTo = (a && a !== document.body && a !== document.documentElement) ? a : null;
    }
    function openPalette(e) {
      var trg = document.querySelector('.cmd-trigger');
      if (!trg) return;
      e.preventDefault();
      if (e.repeat) return;
      remember();
      trg.click();
    }
    function routeEl(path) {
      var all = document.querySelectorAll('[data-route]');
      for (var i = 0; i < all.length; i++) {
        if (all[i].getAttribute('data-route') === path && !closestEl(all[i], '.cmdk')) return all[i];
      }
      return null;
    }

    // A real click on the trigger (pointer, or Enter/Space on it): Radix's
    // own return to the trigger is the right one, so forget older openers.
    document.addEventListener('click', function (e) {
      if (e.isTrusted && closestEl(e.target, '.cmd-trigger')) P.returnTo = null;
    }, true);

    document.addEventListener('keydown', function (e) {
      if (e.defaultPrevented || e.isComposing || e.keyCode === 229) return;
      var key = e.key;
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && (key === 'k' || key === 'K')) {
        gAt = 0;
        if (dialogs().other) return;
        openPalette(e);
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || editable(e.target)) { gAt = 0; return; }
      if (gAt) {                     // second key of a g-chord
        var fresh = Date.now() - gAt <= CHORD_MS;
        gAt = 0;
        var path = fresh && !e.repeat ? ROUTES[key] : null;
        if (path) {
          var st = dialogs();
          if (st.other || st.palette) return;
          var target = routeEl(path);
          if (target) { e.preventDefault(); target.click(); }
          return;
        }
      }
      if (e.repeat) return;
      if (key === '/') {
        var s1 = dialogs();
        if (s1.other || s1.palette) return;
        openPalette(e);
        return;
      }
      if (key === 'g') {
        var s2 = dialogs();
        if (!s2.other && !s2.palette) gAt = Date.now();
      }
    });
  })();
}

/* 2c. Form helpers. */
(function () {
  if (window.__pmForms__) return; window.__pmForms__ = true;
  var BUTTON_TYPES = { button: 1, submit: 1, reset: 1, image: 1, file: 1 };
  // <input> types that hold no typed text.
  var TEXTLESS = { checkbox: 1, radio: 1, range: 1, color: 1, hidden: 1, button: 1,
                   submit: 1, reset: 1, image: 1, file: 1 };
  function textField(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.tagName === 'TEXTAREA') return true;
    return el.tagName === 'INPUT' && !TEXTLESS[(el.type || 'text').toLowerCase()];
  }

  function closestEl(el, sel) {
    return el && el.nodeType === 1 && el.closest ? el.closest(sel) : null;
  }
  // The form's default button: its first submit button in tree order.
  function defaultButton(form) {
    var els = form.elements;
    for (var i = 0; i < els.length; i++) {
      var t = (els[i].type || '').toLowerCase();
      if ((els[i].tagName === 'BUTTON' && t === 'submit') ||
          (els[i].tagName === 'INPUT' && (t === 'submit' || t === 'image'))) return els[i];
    }
    return null;
  }
  function submit(form, skipValidation) {
    var def = defaultButton(form);
    if (def && def.disabled) return;
    var nv = form.noValidate;
    if (skipValidation) form.noValidate = true;
    try {
      if (typeof form.requestSubmit === 'function') form.requestSubmit();
      else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    } finally {
      if (skipValidation && !nv) form.noValidate = false;
    }
  }

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || e.defaultPrevented || e.isComposing || e.keyCode === 229) return;
    var t = e.target;
    if (!t || t.nodeType !== 1) return;
    if (closestEl(t, '[data-enter="none"]')) { e.preventDefault(); return; }
    var tag = t.tagName;
    if (tag === 'TEXTAREA') {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && t.hasAttribute('data-submit-shortcut') && t.form) {
        e.preventDefault();
        if (!e.repeat) submit(t.form, false);
      }
      return;
    }
    if (tag !== 'INPUT' || BUTTON_TYPES[(t.type || '').toLowerCase()]) return;
    var form = t.form;
    if (!form || !form.hasAttribute('data-enter-submit')) return;
    e.preventDefault();
    if (!e.repeat) submit(form, true);
  });

  function count(t) {
    var id = t && t.nodeType === 1 ? t.getAttribute('data-counter') : null;
    var out = id ? document.getElementById(id) : null;
    if (!out) return;
    var len = (t.value || '').length, max = t.maxLength;
    var text = max > 0 ? len + '/' + max : String(len);
    var node = document.createTreeWalker(out, NodeFilter.SHOW_TEXT).nextNode();
    if (node) { if (node.nodeValue !== text) node.nodeValue = text; }
    else out.appendChild(document.createTextNode(text));
  }
  // Composition updates are skipped; compositionend counts the committed text
  // (Chrome sends no plain input event after it).
  document.addEventListener('input', function (e) { if (!e.isComposing) count(e.target); }, true);
  document.addEventListener('compositionend', function (e) { count(e.target); }, true);

  // Browser Back/Forward unmounts the page without moving focus first, and React
  // drops any blur fired while it commits that unmount, so a compose field's blur
  // commit (the Generator prompt, a tag editor field) would never be sent and its
  // draft would be lost. This listener is added from <head>, before React Router's,
  // so blurring the focused form field here lets React send that commit before the
  // route changes. pagehide does the same, best effort, for a reload or a close.
  function commitFocusedField() {
    var a = document.activeElement;
    if (textField(a) && a.form) { try { a.blur(); } catch (err) {} }
  }
  window.addEventListener('popstate', commitFocusedField);
  window.addEventListener('pagehide', commitFocusedField);

  // A button marked data-clear-form empties its form's text fields right in the
  // click, in the browser. The server resets the filters (and reloads the list) a
  // moment later and returns no rx.set_value for them: one arriving a round trip
  // after the click would wipe whatever the user had typed in between.
  document.addEventListener('click', function (e) {
    var b = closestEl(e.target, '[data-clear-form]');
    if (!b || b.disabled) return;
    var form = b.form || closestEl(b, 'form');
    if (!form) return;
    for (var i = 0; i < form.elements.length; i++) {
      var el = form.elements[i];
      if (textField(el) && el.value !== '') el.value = '';
    }
  }, true);

  // Fields marked data-digits take ASCII digits only: type="number" alone still lets
  // "-", "+", "." and "e" in (Chrome) or any text (Firefox), which the server then
  // ignores without a word. Typed or dropped text with any other character is
  // refused; a paste keeps just its digits (inserted as typed text, so React's change
  // handler still runs). IME composition cannot be cancelled and is left to the
  // server-side sanitiser.
  var NOT_DIGIT = /[^0-9]/;
  function digitsField(t) {
    return !!(t && t.nodeType === 1 && t.hasAttribute && t.hasAttribute('data-digits'));
  }
  document.addEventListener('paste', function (e) {
    var t = e.target;
    if (!digitsField(t) || !e.clipboardData) return;
    var text = e.clipboardData.getData('text/plain') || '';
    if (!NOT_DIGIT.test(text)) return;
    e.preventDefault();
    var digits = text.replace(/[^0-9]+/g, '');
    if (digits) { try { document.execCommand('insertText', false, digits); } catch (err) {} }
  }, true);
  document.addEventListener('beforeinput', function (e) {
    var t = e.target;
    if (!digitsField(t) || (e.inputType || '').indexOf('insert') !== 0) return;
    if (e.inputType === 'insertCompositionText') return;
    var data = e.data;
    if (data == null && e.dataTransfer) {
      try { data = e.dataTransfer.getData('text/plain'); } catch (err) { data = null; }
    }
    if (data && NOT_DIGIT.test(data)) e.preventDefault();
  }, true);
})();

/* Blocks 3–4 observe document.body, which does not exist yet while <head>
 * scripts run, so they start once the DOM is parsed. */
(function () {
  function onBody(fn) {
    if (document.body) fn();
    else document.addEventListener('DOMContentLoaded', fn, { once: true });
  }

  /* 3. Generator "AI thinking" canvas: a body-level observer starts the
   *    particle field when #thinkviz mounts and stops it on unmount; phase
   *    comes from .viz-wrap[data-phase], theme from <html>. */
  onBody(function () {
    (function(){
      if (window.__thinkviz__) return; window.__thinkviz__ = true;
      var raf=0, parts=[], W=0, H=0, ro=null, po=null, so=null, cv=null, ctx=null;
      // Lost connection (pages/generator.py sets .think.is-offline): nothing is being
      // computed, so the field holds still until the stage is live again.
      function offline(){return !!(cv&&cv.closest&&cv.closest('.is-offline'));}
      function rand(a,b){return a+Math.random()*(b-a);}
      function phase(){var w=document.querySelector('.viz-wrap');return w?parseInt(w.getAttribute('data-phase')||'0',10):0;}
      function assign(){
        var ph=phase(), cx=W/2, cy=H/2;
        parts.forEach(function(p,i){
          if(ph===0){var a=rand(0,Math.PI*2),r=rand(0,46);p.tx=cx+Math.cos(a)*r;p.ty=cy+Math.sin(a)*r;p.hl=i<26;p.dim=0.5;}
          else if(ph===1){p.tx=rand(W*0.08,W*0.92);p.ty=rand(H*0.14,H*0.86);var d=Math.hypot(p.tx-cx,p.ty-cy);p.hl=d<Math.min(W,H)*0.34;p.dim=p.hl?1:0.28;}
          else if(ph===2){p.hl=i%6===0;p.dim=p.hl?1:0.08;}
          else {if(p.hl){var idx=Math.floor(i/6);p.tx=W*0.1+(idx/7)*W*0.8;p.ty=cy-Math.sin((idx/7)*Math.PI*1.6)*H*0.26;p.dim=1;}else{p.dim=0.05;}}
        });
      }
      function size(){
        if(!cv) return; var r=cv.getBoundingClientRect(); var dpr=Math.min(window.devicePixelRatio||1,2);
        cv.width=r.width*dpr; cv.height=r.height*dpr; ctx.setTransform(dpr,0,0,dpr,0,0); W=r.width; H=r.height;
        if(!parts.length){parts=Array.from({length:300},function(){return {x:rand(0,W),y:rand(0,H),tx:rand(0,W),ty:rand(0,H),hl:false,dim:0.4,size:rand(1.1,2.4)};});}
        assign();
      }
      function frame(){
        if(!cv||!document.body.contains(cv)){stop();return;}
        if(offline()){raf=0;return;}
        var reduce=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        var dark=document.documentElement.classList.contains('dark');
        var base=dark?'120,120,135':'150,150,160';
        ctx.clearRect(0,0,W,H); ctx.lineWidth=1;
        for(var i=0;i<parts.length;i++){var p=parts[i];var k=reduce?1:0.08;p.x+=(p.tx-p.x)*k;p.y+=(p.ty-p.y)*k;
          if(p.hl){for(var j=i+1;j<parts.length;j++){var q=parts[j];if(!q.hl)continue;var d=Math.hypot(p.x-q.x,p.y-q.y);
            if(d<74){ctx.strokeStyle='rgba(249,115,22,'+(0.12*(1-d/74))+')';ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke();}}}}
        for(var m=0;m<parts.length;m++){var pp=parts[m];ctx.beginPath();ctx.arc(pp.x,pp.y,pp.hl?pp.size+0.7:pp.size,0,Math.PI*2);
          ctx.fillStyle=pp.hl?'rgba(249,115,22,'+pp.dim+')':'rgba('+base+','+pp.dim+')';ctx.fill();}
        raf=requestAnimationFrame(frame);
      }
      function start(c){
        cv=c; ctx=cv.getContext('2d'); parts=[]; size();
        if(ro) ro.disconnect(); ro=new ResizeObserver(size); ro.observe(cv);
        var w=document.querySelector('.viz-wrap');
        if(po) po.disconnect();
        if(w){po=new MutationObserver(assign); po.observe(w,{attributes:true,attributeFilter:['data-phase']});}
        var st=cv.closest?cv.closest('.think'):null;
        if(so) so.disconnect();
        if(st){so=new MutationObserver(function(){if(!offline()&&!raf&&cv) frame();}); so.observe(st,{attributes:true,attributeFilter:['class']});}
        cancelAnimationFrame(raf); raf=0; frame();
      }
      function stop(){cancelAnimationFrame(raf);raf=0;if(ro){ro.disconnect();ro=null;}if(po){po.disconnect();po=null;}if(so){so.disconnect();so=null;}cv=null;ctx=null;parts=[];}
      var watch=new MutationObserver(function(){
        var c=document.getElementById('thinkviz');
        if(c && c!==cv){start(c);} else if(!c && cv){stop();}
      });
      watch.observe(document.body,{childList:true,subtree:true});
      var c0=document.getElementById('thinkviz'); if(c0) start(c0);
    })();
  });

  /* 4. Keep the Generator's streaming log scrolled to the newest line. */
  onBody(function () {
    (function(){
      if (window.__genlog_obs__) return; window.__genlog_obs__ = true;
      var obs=new MutationObserver(function(){var g=document.querySelector('.genlog');if(g)g.scrollTop=g.scrollHeight;});
      obs.observe(document.body,{subtree:true,childList:true,characterData:true});
    })();
  });
})();

/* 5. Focus continuity (pmFocus) — webui-keyboard-and-semantics.
 *    window.pmFocus = { restore, when, lost }:
 *    - restore(): after a dialog closes, focus its opener (the element focused
 *      before focus entered the dialog) or, if that is gone, #main-heading.
 *      An opener whose id changed was re-used in place by React for another
 *      item (index-keyed lists), so it counts as gone too.
 *      Dialog contents call it from on_close_auto_focus.
 *    - when(selectors, {force, exclude, frames}): poll up to `frames` (~60)
 *      animation frames for the first matching selector and focus it, only if
 *      force or lost(). `exclude` is an element never matched while polling
 *      (focused only if it is still connected when the poll expires).
 *    - lost(): focus fell to <body> because the last focused element was removed.
 *    Also: pathname route changes focus the new page's #main-heading (only when
 *    focus was lost; the outgoing page's heading is excluded), Escape inside
 *    [data-pm-escape-cancel] clicks its [data-pm-cancel] (also when an open
 *    tooltip, the only layer open, closed on that Escape), and .skip-link
 *    focuses #main-content without a #hash.
 *    The ⌘K palette (.cmdk) is skipped: it restores its own focus.
 *    Document-level listeners only, so it runs from <head> without waiting for body. */
(function () {
  if (window.__pmFocus__) return; window.__pmFocus__ = true;

  var DIALOGS = '[role="dialog"],[role="alertdialog"]';
  var INTERACTIVE = 'button,a[href],input,select,textarea,summary,[tabindex]';
  var MAX_FRAMES = 60;
  var last = null;                 // last focused (or clicked) element
  var returnStack = [];            // [{opener, id, dialog}] — one entry per dialog entered
  var entered = typeof WeakSet === 'function' ? new WeakSet() : null;

  function closestEl(el, sel) {
    return el && el.nodeType === 1 && el.closest ? el.closest(sel) : null;
  }
  function isPalette(dlg) {
    try { return !!(dlg.closest('.cmdk') || dlg.querySelector('.cmdk')); } catch (e) { return false; }
  }
  function isClosed(dlg) {
    return !dlg.isConnected || dlg.getAttribute('data-state') === 'closed';
  }
  function focusEl(el) {
    if (!el || !el.isConnected) return false;
    if (!el.hasAttribute('tabindex') && el.tabIndex < 0) el.setAttribute('tabindex', '-1');
    try { el.focus(); } catch (e) { return false; }
    return document.activeElement === el;
  }
  function focusIsGone() {
    var a = document.activeElement;
    if (!a || a === document.body || a === document.documentElement || !a.isConnected) return true;
    var d = closestEl(a, DIALOGS);
    return !!(d && isClosed(d));
  }

  function lost() {
    var a = document.activeElement;
    return (!a || a === document.body) && !!last && !last.isConnected;
  }

  function note(el) {
    var prev = last;
    last = el;
    var dlg = closestEl(el, DIALOGS);
    if (!dlg || isPalette(dlg) || !prev || prev === el || dlg.contains(prev)) return;
    if (entered) { if (entered.has(dlg)) return; entered.add(dlg); }
    else if (dlg.__pmEntered__) { return; } else { dlg.__pmEntered__ = true; }
    returnStack.push({ opener: prev, id: prev.id || '', dialog: dlg });
  }

  document.addEventListener('focusin', function (e) {
    var t = e.target;
    if (t && t.nodeType === 1 && t !== document.body) note(t);
  }, true);
  // Safari does not focus buttons on click: treat the clicked control as the
  // last focused element so it can still be restored as a dialog's opener.
  document.addEventListener('pointerdown', function (e) {
    var t = closestEl(e.target, INTERACTIVE);
    if (t && t !== document.activeElement) note(t);
  }, true);

  function restore() {
    var target = null;
    while (returnStack.length) {
      var top = returnStack[returnStack.length - 1];
      if (!isClosed(top.dialog)) break;         // belongs to a dialog that is still open
      returnStack.pop();
      if (entered) entered.delete(top.dialog); else top.dialog.__pmEntered__ = false;
      // React may re-use a removed item's node for its neighbour (lists keyed by
      // index): a changed id means the recorded opener no longer exists.
      if (!target && top.opener && top.opener.isConnected &&
          (!top.id || top.opener.id === top.id)) {
        var od = closestEl(top.opener, DIALOGS);
        if (!od || !isClosed(od)) target = top.opener;
      }
    }
    if (!focusIsGone()) return false;           // the user (or Radix) already moved focus
    if (target && focusEl(target)) return true;
    return focusEl(document.getElementById('main-heading'));
  }

  var pollGen = 0;                 // a newer when() supersedes any pending poll
  function when(selectors, opts) {
    var list = typeof selectors === 'string' ? [selectors] : (selectors || []);
    var force = !!(opts && opts.force);
    var exclude = (opts && opts.exclude) || null;
    var max = (opts && opts.frames > 0) ? opts.frames : MAX_FRAMES;
    var frames = 0;
    var gen = ++pollGen;
    function find(sel) {
      var all;
      try { all = document.querySelectorAll(sel); } catch (e) { return null; }
      for (var j = 0; j < all.length; j++) { if (all[j] !== exclude) return all[j]; }
      return null;
    }
    function tick() {
      if (gen !== pollGen) return;
      for (var i = 0; i < list.length; i++) {
        var el = find(list[i]);
        if (el) {
          if (force || lost()) { focusEl(el); return; }
          break;                                  // found, but focus is not lost (yet)
        }
      }
      if (++frames < max) { requestAnimationFrame(tick); return; }
      // Expired: the excluded element survived (re-used in place), so accept it.
      if (exclude && exclude.isConnected && (force || lost())) focusEl(exclude);
    }
    tick();
  }

  // Route changes: pathname only (Settings' replaceState of ?section= is ignored).
  // React Router pushes the URL before it renders the new route, so the outgoing
  // page's heading is still in the DOM here: exclude it, or a focus that was
  // already lost would land on it and drop to <body> when it unmounts. The
  // longer budget (~3s) covers a lazily loaded route module.
  var ROUTE_FRAMES = 180;
  var path = location.pathname;
  function checkRoute() {
    if (location.pathname === path) return;
    path = location.pathname;
    when('#main-heading', { exclude: document.getElementById('main-heading'), frames: ROUTE_FRAMES });
  }
  ['pushState', 'replaceState'].forEach(function (m) {
    var orig = history[m];
    if (typeof orig !== 'function') return;
    history[m] = function () {
      var r = orig.apply(this, arguments);
      try { checkRoute(); } catch (e) {}
      return r;
    };
  });
  window.addEventListener('popstate', checkRoute);

  // Escape inside an inline editor cancels it, unless a dialog, menu or popover
  // took that Escape first (Radix layers mark it handled). A hover label (a Radix
  // tooltip, e.g. a rail item's under the resting pointer) marks it handled too as
  // it closes; when tooltips were the only layers open, the editor still cancels.
  // Radix listens on document in the capture phase, so the open layers are
  // checked on window in the capture phase, before any of them has closed.
  var tooltipOnlyEscape = null;
  function onlyTooltipsOpen() {
    if (document.querySelector('[role="dialog"]:not([data-state="closed"]),' +
                               '[role="alertdialog"]:not([data-state="closed"])')) return false;
    var layers = document.querySelectorAll('[data-radix-popper-content-wrapper]');
    if (!layers.length) return false;
    for (var i = 0; i < layers.length; i++) {
      if (!layers[i].querySelector('[role="tooltip"]')) return false;
    }
    return true;
  }
  window.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') tooltipOnlyEscape = onlyTooltipsOpen() ? e : null;
  }, true);
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || e.isComposing) return;
    if (e.defaultPrevented && e !== tooltipOnlyEscape) return;
    var box = closestEl(e.target, '[data-pm-escape-cancel]');
    var btn = box && box.querySelector('[data-pm-cancel]');
    if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
  });

  // Skip link: move focus into <main> without leaving #main-content in the URL.
  document.addEventListener('click', function (e) {
    var a = closestEl(e.target, '.skip-link');
    var main = a && document.getElementById('main-content');
    if (!main) return;
    e.preventDefault();
    focusEl(main);
  }, true);

  window.pmFocus = { restore: restore, when: when, lost: lost };
})();

/* 6. Sticky bars never hide focus (WCAG 2.2 SC 2.4.11) — webui-zoom-and-narrow-fit.
 *    The Library bulk bar (.bulkbar) and the Settings save bar (.savebar) are
 *    position: sticky at the bottom of the .scroll container, so one that appears
 *    can cover the control that made it appear: the row checkbox near the bottom
 *    of the table, or the field being edited. While a bar is shown this
 *    - keeps --pm-sticky-bar-h (its height) on its .scroll, which redesign.css
 *      turns into scroll-padding-bottom, so Tab and scrollIntoView stop short of it;
 *    - when the bar appears, changes height or finishes its slide-in, and when
 *      focus moves, scrolls the focused control (or, when focus did not follow
 *      the pointer, e.g. Safari buttons, the control pressed in the last few
 *      seconds) into view with block: 'nearest', only if the bar covers it. A
 *      table row comes into view whole. Nothing scrolls when nothing is covered,
 *      and the bar is rendered after the rows, so rows above it never move. */
(function () {
  if (window.__pmStickyFocus__) return; window.__pmStickyFocus__ = true;

  var BARS = '.bulkbar, .savebar';
  var PRESSABLE = 'button, input, select, textarea, label, [role="checkbox"]';
  var GAP = 8;          // matches the 8px in redesign.css's scroll-padding-bottom
  var PICK_MS = 4000;   // how long a pointer press counts as "the control just used"
  var bar = null, ro = null, raf = 0, picked = null, pickedAt = 0;

  function closestEl(el, sel) {
    return el && el.nodeType === 1 && el.closest ? el.closest(sel) : null;
  }
  function usable(el, scroller) {
    return !!(el && el.isConnected && scroller.contains(el) && !bar.contains(el));
  }
  function current(scroller) {
    var a = document.activeElement;
    if (a && a !== document.body && usable(a, scroller)) return a;
    if (Date.now() - pickedAt < PICK_MS && usable(picked, scroller)) return picked;
    return null;
  }

  function reveal() {
    raf = 0;
    if (!bar || !bar.isConnected) return;
    var scroller = closestEl(bar, '.scroll');
    if (!scroller) return;
    var b = bar.getBoundingClientRect();
    scroller.style.setProperty('--pm-sticky-bar-h', Math.ceil(b.height) + 'px');
    var t = current(scroller);
    if (!t) return;
    var el = closestEl(t, 'tr') || t;
    var r = el.getBoundingClientRect();
    if (r.bottom > b.top - GAP + 0.5 && r.top < b.bottom) {
      try { el.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } catch (e) { el.scrollIntoView(false); }
    }
  }
  function schedule() { if (!raf) raf = requestAnimationFrame(reveal); }

  function watch() {
    var b = document.querySelector(BARS);
    if (b === bar) return;
    if (ro) { ro.disconnect(); ro = null; }
    bar = b;
    if (!bar) return;
    if (typeof ResizeObserver === 'function') { ro = new ResizeObserver(schedule); ro.observe(bar); }
    bar.addEventListener('animationend', schedule);
    schedule();
  }

  document.addEventListener('pointerdown', function (e) {
    var t = closestEl(e.target, PRESSABLE);
    if (t) { picked = t; pickedAt = Date.now(); }
  }, true);
  // After the browser's own focus scroll (a fallback where scroll-padding is ignored).
  document.addEventListener('focusin', function () { if (bar) schedule(); }, true);

  function start() {
    new MutationObserver(watch).observe(document.body, { childList: true, subtree: true });
    watch();
  }
  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start, { once: true });
})();

/* 7. Rail labels survive a focus scroll — webui-zoom-and-narrow-fit.
 *    Rail labels are Radix tooltips (navbar.py), and Radix closes an open
 *    tooltip whenever an element holding its trigger scrolls. When Tab (or a
 *    focus restore) lands on a rail control that is not fully in view, the
 *    browser scrolls the rail just after the focus event, so the label that
 *    the focus opened closes at once and stays closed. Once the rail has
 *    stopped scrolling (SETTLE_MS without a scroll event), this reopens the
 *    label of the rail control that has keyboard focus (:focus-visible),
 *    when that control is fully inside the rail's visible box: it sends the
 *    focusin that React's onFocus (Radix's open-on-focus) listens to. A label
 *    the user dismissed with Escape stays closed until focus moves on.
 *    Document-level listeners only, so it runs from <head>. */
(function () {
  if (window.__pmRailTips__) return; window.__pmRailTips__ = true;

  var SETTLE_MS = 120;
  var timer = 0, dismissed = null;

  function railOf(el) {
    return el && el.nodeType === 1 && el.closest ? el.closest('.rail') : null;
  }
  function reopen() {
    timer = 0;
    var a = document.activeElement;
    var rail = railOf(a);
    if (!rail || a === dismissed || a.getAttribute('data-state') !== 'closed') return;
    var keyboard = true;
    try { keyboard = a.matches(':focus-visible'); } catch (e) {}
    if (!keyboard) return;
    var r = a.getBoundingClientRect(), box = rail.getBoundingClientRect();
    var top = box.top + rail.clientTop, bottom = top + rail.clientHeight;
    if (r.top < top - 0.5 || r.bottom > bottom + 0.5) return;
    a.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
  }

  document.addEventListener('scroll', function (e) {
    var t = e.target;
    if (!t || t.nodeType !== 1 || !t.classList || !t.classList.contains('rail')) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(reopen, SETTLE_MS);
  }, true);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && railOf(document.activeElement)) dismissed = document.activeElement;
  }, true);
  document.addEventListener('focusin', function (e) {
    if (e.target !== dismissed) dismissed = null;
  }, true);
})();

/* 8. Generator lost-connection notice: focus (pages/generator.py, #gen-reconnect-region).
 *    While the page cannot reach the server, the thinking stage withdraws its progress
 *    row (.gen-progress, with the Cancel button) and shows a quiet line, then an alert,
 *    in its place. Neither notice is a focus target. If keyboard focus was on Cancel,
 *    it falls to <body> when the row leaves: this moves it to the stage heading
 *    (#main-heading). pmFocus.when acts only when focus was lost, so focus the user
 *    still has is never touched. A watcher on <body> looks for one change: a .think stage
 *    that stays while its row has just left. Nothing happens when the row returns, when
 *    the alert replaces the quiet line, or when the whole stage leaves; a reconnect
 *    rebuilds the page and the stage's own mount script places focus. */
(function () {
  if (window.__pmOfflineFocus__) return; window.__pmOfflineFocus__ = true;
  var rowShown = false;
  function check() {
    var stage = document.querySelector('.think');
    var row = !!(stage && stage.querySelector('.gen-progress'));
    var left = rowShown && !!stage && !row;
    rowShown = row;
    if (left && window.pmFocus) window.pmFocus.when('#main-heading');
  }
  function start() {
    new MutationObserver(check).observe(document.body, { childList: true, subtree: true });
    check();
  }
  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start, { once: true });
})();
