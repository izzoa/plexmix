---
name: PlexMix
description: AI-powered Plex playlist generator using mood-based queries — a warm, tactile studio console over your own music library.
colors:
  amp-glow: "#F97316"
  amp-glow-hover: "#EA580C"
  burnt-copper: "#a5520f"
  burnt-copper-deep: "#5c2d0f"
  peach-wash: "#fceadd"
  peach-mist: "#fdf3ec"
  copper-edge: "#e09b5e"
  brand-ink: "#1f1208"
  focus-ring: "#c2410c"
  studio-paper: "#fcfcfc"
  studio-white: "#ffffff"
  studio-sunken: "#f0f0f0"
  border-subtle: "#e8e8e8"
  border-strong: "#d9d9d9"
  soot: "#202020"
  soot-secondary: "#646464"
  soot-muted: "#737373"
  soot-night: "#09090B"
  soot-panel: "#18181B"
  soot-well: "#27272A"
  copper-glow-dark: "#ffb366"
  success: "#22C55E"
  success-text: "#166534"
  warning: "#EAB308"
  warning-text: "#854d0e"
  error: "#EF4444"
  error-text: "#b91c1c"
  info: "#3B82F6"
  info-text: "#1d4ed8"
  info-solid: "#2563eb"
  purple: "#A855F7"
  purple-text: "#7e22ce"
typography:
  display:
    fontFamily: "Space Grotesk, system-ui, -apple-system, sans-serif"
    fontSize: "46px"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Space Grotesk, system-ui, -apple-system, sans-serif"
    fontSize: "32px"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Space Grotesk, system-ui, -apple-system, sans-serif"
    fontSize: "17px"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "IBM Plex Sans, system-ui, -apple-system, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.55
  body-small:
    fontFamily: "IBM Plex Sans, system-ui, -apple-system, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "IBM Plex Sans, system-ui, -apple-system, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0.08em"
  data:
    fontFamily: "IBM Plex Mono, JetBrains Mono, ui-monospace, monospace"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  stat:
    fontFamily: "IBM Plex Mono, JetBrains Mono, ui-monospace, monospace"
    fontSize: "28px"
    fontWeight: 700
    lineHeight: 1
rounded:
  sm: "8px"
  md: "12px"
  lg: "18px"
  xl: "22px"
  pill: "999px"
spacing:
  space-1: "4px"
  space-2: "8px"
  space-3: "12px"
  space-4: "16px"
  space-5: "20px"
  space-6: "24px"
  space-8: "32px"
  space-10: "40px"
  space-12: "48px"
  space-16: "64px"
components:
  button-primary:
    backgroundColor: "{colors.amp-glow}"
    textColor: "{colors.brand-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "{colors.amp-glow-hover}"
    textColor: "{colors.brand-ink}"
  button-soft:
    backgroundColor: "{colors.studio-white}"
    textColor: "{colors.soot}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "40px"
  button-outline:
    textColor: "{colors.soot}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "40px"
  button-outline-hover:
    textColor: "{colors.burnt-copper}"
  button-ghost:
    textColor: "{colors.soot-secondary}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "40px"
  input:
    backgroundColor: "{colors.studio-white}"
    textColor: "{colors.soot}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "10px 14px"
    height: "40px"
  card:
    backgroundColor: "{colors.studio-white}"
    rounded: "{rounded.lg}"
    padding: "20px"
  quick-action-primary:
    backgroundColor: "{colors.peach-mist}"
    textColor: "{colors.soot}"
    rounded: "{rounded.lg}"
    padding: "20px"
  badge-accent:
    backgroundColor: "{colors.peach-wash}"
    textColor: "{colors.burnt-copper}"
    rounded: "{rounded.pill}"
    padding: "3px 9px"
  badge-neutral:
    backgroundColor: "{colors.studio-sunken}"
    textColor: "{colors.soot-secondary}"
    rounded: "{rounded.pill}"
    padding: "3px 9px"
  rail-item:
    textColor: "{colors.soot-muted}"
    rounded: "{rounded.md}"
    width: "46px"
    height: "42px"
  rail-item-active:
    backgroundColor: "{colors.peach-wash}"
    textColor: "{colors.burnt-copper}"
    rounded: "{rounded.md}"
  segmented-option-on:
    backgroundColor: "{colors.studio-white}"
    textColor: "{colors.burnt-copper}"
    rounded: "{rounded.pill}"
  stat-number:
    textColor: "{colors.soot}"
    typography: "{typography.stat}"
---

# Design System: PlexMix

## Overview

**Creative North Star: "The Studio Console"**

PlexMix is an instrument panel for your own music library. Readouts are precise. Track counts, durations, model ids and timestamps sit in IBM Plex Mono. Status dots, phase trackers and progress bars report what the pipeline is actually doing. One lit signal, Amp Glow orange, marks what is live or primary. The controls around those readouts are warm and tactile. Corners are soft (12px controls, 18px cards). Surfaces rest on warm-tinted shadows. Buttons rise on hover and squash on press with a small spring. The primary action glows like a powered-on amp.

The web UI is an Operate surface. Scanability and consistency come before expression, and the personality lives in the details: the copper-on-peach active state, the glowing primary button, mono numerals, the springy press. It is light-first, with a full dark mode that keeps the same structure. Density is comfortable by default, with a compact preference. The Generator is the one showpiece: an animated "AI thinking" stage (particle vector field, four-phase tracker, streaming terminal log). Everything else supports it.

Rejected and removed: dark-mode brand values leaking into light mode (dark-brown active states and a pale `#ffb366` wordmark on white), orange `#F97316` used as text, and colored left-edge stripes on cards.

**Key Characteristics:**
- Light-first warm off-white, with one orange signal and copper for orange text.
- Soft radii: 8 / 12 / 18 / 22px, plus pills for badges, segmented controls and filter chips.
- Layered depth: white surfaces resting on warm two-layer shadows, lifting on hover.
- Tactile spring motion on transforms and shadows; quick 150ms color changes.
- Mono for every number, duration, id and command.
- A glass icon rail and top bar, with a ⌘K command palette as the navigation backbone.

## Colors

One warm orange signal over clean near-neutral studio surfaces, with semantic colors split into fills and readable text shades.

### Primary
- **Amp Glow Orange** (`amp-glow`): the brand's single signal. Primary button fills (custom and Radix solid), the phase tracker's active dot, the logo tile, and the avatar gradient. It is a fill color for large shapes. It is never used for text on light surfaces (2.7:1) or for thin indicators.
- **Amp Glow Hover** (`amp-glow-hover`): primary button hover/pressed fill. It is also the orange for thin **indicators**, which must reach 3:1 against light tracks: progress fills, the loading bar, phase-line fills, the active-rail bar, the selected command-palette chip, the selected-segment ring, Radix progress/slider/switch tracks, spinners, and native checkbox/range accents. It is the only orange allowed as large display text on light surfaces (3.5:1 at ≥24px), for example the accent word in the Generator headline.
- **Burnt Copper** (`burnt-copper`): all orange text and orange icons in light mode, including the PlexMix wordmark, active nav icons, links ("View all"), selected segment text and accent badges. 5.4:1 on paper, 4.7:1 on Peach Wash.
- **Peach Wash** (`peach-wash`): selected/active backgrounds: active rail item, selected command-palette row, accent badges, icon chips for orange stats.
- **Peach Mist** (`peach-mist`): the warm tint that marks the primary quick action, and hover fill for vibe pills and toggled option buttons.
- **Copper Edge** (`copper-edge`): hover borders on soft buttons, inputs, vibe pills and the ⌘K trigger, plus the 1px ring around the active rail item.
- **Brand Ink** (`brand-ink`): text and icons on any Amp Glow fill (6.5:1). White on orange is 2.8:1, so it is never used.
- **Focus Ring** (`focus-ring`): the 2px focus outline and focused input border in light mode (5.2:1). In dark mode the ring is Amp Glow (7.1:1 on Soot Night).

### Neutral
- **Studio Paper** (`studio-paper`): app background.
- **Studio White** (`studio-white`): raised surfaces: cards, tiles, tables, inputs, top bar, menus.
- **Studio Sunken** (`studio-sunken`): wells and hover fills: segmented-control tracks, kbd keys, row hover, ghost-button hover.
- **Border Subtle / Border Strong** (`border-subtle`, `border-strong`): dividers and table rules / input and soft-button outlines.
- **Soot** (`soot`): primary text. **Soot Secondary** (`soot-secondary`): secondary text, helper copy, inactive segment labels. **Soot Muted** (`soot-muted`): meta text, placeholders, idle rail icons. Muted text passes AA only on Paper and White (4.6:1).

### Semantic
Each semantic hue has a **solid** for fills, dots and bars, and a **text** shade for words and icons on light surfaces and on the hue's own tint:

| Hue | Solid (fills, dots) | Text/icon (light) | Text/icon (dark) | Tint |
|---|---|---|---|---|
| Success | `success` | `success-text` | `#4ade80` | `rgba(34,197,94,.14)` (dark .18) |
| Warning | `warning` | `warning-text` | `#facc15` | `rgba(234,179,8,.16)` (dark .20) |
| Error | `error` | `error-text` | `#f87171` | `rgba(239,68,68,.12)` (dark .18) |
| Info | `info` | `info-text` | `#60a5fa` | `rgba(59,130,246,.12)` (dark .18) |
| Purple (MusicBrainz) | `purple` | `purple-text` | `#c084fc` | `rgba(168,85,247,.14)` (dark .18) |

Green also fills the "Save Locally" button (with `#06240f` ink). Blue buttons ("Save to Plex", "Export to Plex") fill with `info-solid`, a deeper blue that keeps white text at 5.2:1. `info` itself is 3.7:1 with white.

### Dark mode
Dark is opt-in via `.dark` on `<html>`. Surfaces become `soot-night` (app), `soot-panel` (raised), `soot-well` (sunken), with borders `#3F3F46` / `#71717A`. Text becomes `#FAFAFA` / `#b0b0b0` / `#939393` (muted passes 4.9:1 even on the well). Orange text becomes `copper-glow-dark`. Peach Wash becomes `#3d2212`, Peach Mist `#2a180d`, and Amp Glow Hover lightens to `#f5933d`. Amp Glow itself stays `#F97316` in both themes.

### Named Rules
**The Copper Text Rule.** Orange words and icons are Burnt Copper (Copper Glow in dark). Amp Glow is a fill for large shapes, and thin indicators use Amp Glow Hover. The single text exception is display-size type in Amp Glow Hover.

**The One Orange Rule.** Radix components use the PlexMix ramp, not Radix's own orange. `styles.css` maps Radix `--accent-1…12`, the alpha steps `--accent-a1…a12`, `--accent-surface`, `--accent-contrast` (Brand Ink), the indicator/track colors and `--focus-8` onto brand tokens. It also maps Radix `--radius-1…6` onto the PlexMix radius scale. Style Radix parts through these mappings, never with per-component `border-radius` or color overrides.

**The Light-Is-Light Rule.** Dark token values apply only under `.dark` / `[data-theme="dark"]` on `<html>`. Never key them to the Radix theme root (`[data-is-root-theme]`), which exists in both color modes. That selector is how the light theme once rendered dark-brown active states.

**The Solid-or-Text Rule.** `--pm-success|warning|error|info|purple` paint fills, dots and bars. Words and icons use the matching `--*-fg` token.

**The Muted Floor Rule.** Muted text (`soot-muted`) sits only on Paper or White. On wells, tints, Peach Wash or hover fills it lifts to Soot Secondary.

## Typography

**Display Font:** Space Grotesk (with system-ui, -apple-system, sans-serif)
**Body Font:** IBM Plex Sans (with system-ui, -apple-system, sans-serif)
**Label/Mono Font:** IBM Plex Mono (with JetBrains Mono, ui-monospace, monospace)

**Character:** Space Grotesk's slightly geometric, characterful headings give the product its voice. IBM Plex Sans keeps UI text neutral and legible. Plex Mono turns every number into an instrument readout. All three are self-hosted latin-subset woff2 in `assets/fonts/`.

### Hierarchy
- **Display** (700, 46px, 1.05, -0.025em): the Generator hero question only.
- **Headline** (700, 32px, 1.15, -0.02em): page titles; playlist result titles use 30px.
- **Title** (700, 17px, 1.3): section heads ("Quick Actions", "Recent Playlists"). The top-bar page name and wordmark use 19px. Card and quick-action titles use Space Grotesk 600 at 15–16px.
- **Body** (400, 14px, 1.55): default UI text. Inputs use 14px, the Generator prompt 19px, the command-palette input 16px.
- **Body Small** (400, 13px, 1.5): helper text, meta lines, descriptions, badges at 11–12px.
- **Label** (600, 11px, 0.08em, uppercase): table headers and command-palette group labels. Never placed as a kicker above a heading.
- **Data / Stat** (Plex Mono 400–500 at 11–14px; 700 at 28px for stat tiles): counts, durations, timestamps, percentages, model ids, dimensions, commands.

### Named Rules
**The Mono-for-Data Rule.** Anything you could measure or type into a terminal is set in Plex Mono: `12,480`, `3:42`, `2026-05-29 14:08`, `gemini-3.5-flash`, `384d`, `plexmix create "…"`. Prose, labels and buttons never are.

## Layout

The app shell is a 76px glass icon rail on the left and a 60px glass top bar. The top bar holds the `PlexMix / Page` breadcrumb, the ⌘K trigger, the theme toggle and an avatar. Content scrolls in a single column below it. The shell itself never scrolls: the rail scrolls on its own when the window is too short. Pages center at max-width 1240px (1400px for wide pages like Library), with 36px × 44px padding (24px × 32px compact, 20px × 16px at ≤640px in either density). Sections are separated by 32px (22px compact). Card padding is 20px (14px compact) and table row padding 12px (7px compact). Spacing follows a 4px grid (`space-1` … `space-16`).

Recurring grids are intrinsic, never a fixed column count. Stat tiles use `repeat(auto-fit, minmax(min(190px, 100%), 1fr))`, which gives four-up on desktop. Quick actions use the same pattern with a 300px minimum (two-up), and playlist cards use `auto-fill` with a 248px minimum. The `min(…, 100%)` floor lets each grid fall to one column in a narrow window. The advanced-options panel is two columns (one at ≤640px). The Generator idle state is a centered hero (max 720px) that gives way to a 860px thinking stage, then a 1100px results view.

The UI is desktop-first and zoom-friendly. Every page works at 200% browser zoom and in windows down to 320 CSS px wide with no sideways page scrolling (`check_reflow.py` at the repo root checks this). A dedicated phone layout is not planned (PRODUCT.md). Breakpoints are in CSS px, so they track browser zoom:
- **`max-width: 768px`:** compact top bar (icon-only ⌘K trigger, subtitle hidden). The Settings tabs stack above the section as one horizontal strip that keeps a thin scrollbar.
- **`max-width: 640px`:** 20px × 16px page padding, the `PlexMix /` prefix visually hidden, and a 17px page name. Generator rows (prompt footer, ordering segments, results header, track rows) wrap and the Options panel goes to one column. The playlist dialog gets 16px gutters, an 18px title and 32px footer buttons that keep their labels.
- **`max-width: 480px`:** the rail narrows to 56px and the top-bar buttons to 32px. In the playlist dialog each track's duration and Move buttons go to a second line.
- **`max-height: 600px`:** the rail compacts. The playlist dialog takes the viewport height minus 32px, and its track list is the scroller.

Content reflows instead of clipping. Data tables scroll sideways inside their own box (`.tbl-scroll`), never the page; at ≤768px a soft edge shadow marks each side that has hidden columns. Long unbroken text wraps (`overflow-wrap: anywhere`). A long prompt clamps to two lines and keeps the full text in `title`. Rows of text and controls (Doctor checks, Recently tagged rows, the Library filters and pagination, the Dashboard status strip) wrap instead of overflowing. Never use `overflow: hidden` to mask overflow.

Dialogs fit the viewport too. The layer a Radix dialog opens in never scrolls sideways. One global rule, `.rt-BaseDialogScrollPadding { min-width: 0 }`, lets a dialog shrink to the window minus two 16px gutters. It is the only rule allowed on a Radix dialog class without a PlexMix class beside it, and a test pins it. Never size a dialog with Radix's `--max-width`, which is always 600px. The changelog notes wrap long paths and links, and preformatted blocks and images stay inside them. The playlist dialog compacts at `max-width: 640px` or `max-height: 600px` (16px gutters, an 18px title, 32px footer buttons that keep their labels); at `max-height: 600px` it takes the viewport height minus 32px and its track list is the scroller; at `max-width: 480px` each track's duration and Move buttons go to a second line; its track list never collapses while it has rows; a long name or prompt clamps to two lines with the full text in `title` (the title keeps `padding-top: .5em` with an equal negative `margin-top`, because Radix pulls its first line above its box, and the title and the prompt keep `padding-inline: .5em` with an equal negative `margin-inline`, because their box is only as wide as their text; without that room the clamp's `overflow: hidden` would cut the tops and the sides of letters such as Ï and j), and a cut track title or artist keeps its full text in `title` too.

## Elevation & Depth

Layered. Every raised surface (cards, tiles, tables, top bar, inputs) rests on a warm-tinted, two-layer shadow over a white fill, so depth reads without borders. Interaction lifts: cards rise 3px into the large shadow, buttons rise 1px. Floating layers (command palette, modals, toasts) sit on the large shadow above a blurred scrim; tooltips rest on the card shadow. The primary button carries an orange glow with a real vertical offset, not a flat halo. Dark mode swaps the warm shadows for deep black ones plus a 1px white-alpha ring, because shadow alone disappears on near-black.

### Shadow Vocabulary
- **Rest small** (`0 1px 2px rgba(60,30,10,.06)`; dark `0 1px 2px rgba(0,0,0,.5)`): inputs, soft buttons, vibe pills, album art.
- **Rest card** (`0 1px 2px rgba(60,30,10,.06), 0 4px 14px rgba(60,30,10,.07)`; dark adds a 1px `rgba(255,255,255,.06)` ring and `0 6px 18px rgba(0,0,0,.5)`): cards, tiles, tables, top bar, selected segments.
- **Lifted / floating** (`0 4px 8px rgba(60,30,10,.06), 0 18px 40px rgba(60,30,10,.12)`; dark ring `.09` + `0 18px 44px rgba(0,0,0,.65)`): hovered cards, palette, modals, toasts.
- **CTA glow** (`0 0 0 1px rgba(234,88,12,.30), 0 6px 18px rgba(249,115,22,.32)`; hover `… .36`, `0 10px 26px … .42`): primary buttons (custom `.btn-primary` and solid orange Radix buttons) and the active phase dot. The Settings "Accent intensity" preference swaps it for a plain small shadow (subtle) or a stronger glow (vivid). Containers that hold a primary button (the prompt box, modals) must not use `overflow: hidden`, or the glow is clipped.

### Named Rules
**The Resting Shadow Rule.** Raised surfaces always carry a shadow at rest, and deepen it on interaction. A raised surface with only a border is the old flat system.

**The Glass-Is-Chrome Rule.** Backdrop blur belongs to the rail, the top bar and overlay scrims. Content surfaces are opaque.

## Shapes

Soft and friendly, never sharp. The scale is 8px for small things (kbd keys, album art, tooltips), 12px for controls (buttons, inputs, rail items, option buttons, callouts, track rows), 18px for cards, tiles and tables, and 22px for dialogs, the command palette and the Generator prompt box. Pills (999px) are used for badges, segmented controls, settings tabs, vibe pills, the ⌘K trigger, kbd hints and progress bars. Icon chips (stat tiles, quick actions, Doctor checks) and icon-only buttons are full circles. Radix parts take these radii through the Radix scale mapping. Compact size-1 Radix controls and checkboxes use 5px so they don't become pills. Cards keep a 1px transparent border, so box sizes stay stable and edges reappear in forced-colors mode.

## Components

### Buttons
Tactile and confident: they rise on hover, squash on press, and spring back.
- **Shape:** gently rounded (12px), heights 32 / 40 / 50px.
- **Primary:** Amp Glow fill, Brand Ink text, CTA glow at rest. Hover: Amp Glow Hover, deeper glow, −1px rise. Press: scale .96.
- **Soft (secondary):** Studio White fill, Border Strong outline, small shadow. Hover: Copper Edge border, card shadow, −1px rise.
- **Outline:** transparent with a Border Strong outline. Hover: Amp Glow border, Burnt Copper text, −1px rise.
- **Ghost:** transparent, Soot Secondary text. Hover: Studio Sunken fill.
- **Solid semantic:** green "Save Locally" (dark ink) and blue "Save to Plex" (white), each with a small shadow and a hover rise.
- **Danger (`.btn-danger`):** `--error-bg` tint fill, `--error-fg` text, a 1px `--pm-error` edge and the small shadow; hover lifts to the card shadow with a −1px rise. Text contrast is 5.55:1 in light and 5.27:1 in dark. Use it only for the destructive action in a confirmation (Delete, Cancel task), at `btn-3` size beside a soft safe option. It never uses the CTA glow or an orange fill.
- **Focus:** 2px Focus Ring outline, 2px offset, on every custom control.
- **Motion:** transform and shadow 250ms `cubic-bezier(.175,.885,.32,1.275)`; color 150ms.

### Chips & Badges
- **Style:** pill, 11px/500 text, 3px × 9px padding. The tint matches the semantic hue, and text uses the hue's text shade.
- **Accent badge:** Peach Wash with Burnt Copper (mood tags). **Neutral:** Studio Sunken with Soot Secondary.
- **Vibe pills:** pill outline on Studio White with a small shadow. Hover: Peach Mist, Copper Edge, Burnt Copper text, and a −2px rise.

### Cards / Containers
- **Corner Style:** 18px.
- **Background:** Studio White (Soot Panel in dark). The primary quick action uses Peach Mist instead of a side stripe.
- **Shadow Strategy:** rest card shadow, lifting to the floating shadow and −3px on hover where the card is interactive.
- **Border:** 1px transparent.
- **Internal Padding:** 20px (14px compact). Stat tiles stack a circular icon chip, a 28px mono number, a 13px muted label, and an optional mono delta.

### Inputs / Fields
- **Style:** Studio White fill, Border Strong outline, 12px radius, 10px × 14px padding, small shadow. Labels are 13px/500 Soot Secondary, with 12px muted helper text below.
- **Focus:** Focus Ring border plus a 4px Amp Glow wash ring (`rgba(249,115,22,.15)`).
- **Search fields:** the same treatment in a 40px bar with a leading search icon, taking the focus ring via `:focus-within`. The Generator prompt box is the 22px-radius large variant, with 19px text.

### Navigation
- **Icon rail (76px, glass):** 46 × 42px items, 12px radius, idle icons in Soot Muted. Items are real `<button>`s with `aria-label`s. Hover: Studio Sunken fill, Soot icon, scale 1.05. Active: Peach Wash fill, Burnt Copper icon, a 1px Copper Edge ring with a soft orange glow, and a 4px Amp Glow Hover bar on the rail's left edge (the bar is the 3:1 cue). Items are grouped Main / Tools / System, with 28px hairline separators.
  - **Short and narrow windows:** the rail is its own scroll container, so on a short viewport it scrolls and every item stays reachable. Focusing an item never scrolls the shell or moves the top bar. At `max-height: 600px` it compacts: 36px items, 3px gaps, 4px separator margins, 10px padding and a 34px logo. At ≤480px it narrows to 56px with 35px items. Its content is start-aligned at a fixed inset, and its scrollbar is the thin 6px one, so a scrollbar only takes the trailing inset and never clips the active bar, an item or its focus ring.
  - **Tooltips:** every rail button has a Radix tooltip, portalled so the scrolling rail never clips it. It opens to the right after 150ms, on hover and on keyboard focus, and comes back once the rail stops scrolling when focusing an item scrolled it. Soot fill with Paper text (the gray-12/gray-1 pair, so it inverts in dark mode), body font at 12px/500, 8px radius and the card shadow. Navigation items read "Label · g key" (for example "Library · g l"). Changelog and Log out show their label, and the theme toggle shows "Dark mode" or "Light mode".
- **Top bar (60px, glass):** Burnt Copper "PlexMix" wordmark (Space Grotesk 19/700), a muted slash, then the page name and a muted subtitle. The ⌘K trigger is a 36px pill with a mono kbd hint. The page name stays on one line and ends in an ellipsis when space runs out, with the full name in `title`. The trailing controls never shrink.
  - **Compact (≤768px):** the ⌘K trigger becomes a 36px icon-only circle without the kbd hint. Its "Search or jump to…" label is visually hidden, not removed, so it stays the button's accessible name. The subtitle is hidden.
  - **Narrow (≤640px):** the `PlexMix /` prefix is visually hidden but still read out, so the breadcrumb's accessible text stays "PlexMix / Page". The page name drops to 17px, and page gutters shrink to 16px. At ≤480px the trigger, theme toggle and avatar are 32px.
- **Command palette:** a 560px, 22px-radius panel on a blurred 40% scrim. It opens `min(12vh, 80px)` from the top and is capped to the viewport height with 16px to spare, so on a short window only the result list (up to 380px) scrolls. Rows are 12px-radius. The selected row gets Peach Wash, an Amp Glow Hover icon chip with Brand Ink icon, a Burnt Copper title and Soot Secondary sub-text.

### Segmented Controls & Tabs
A pill track in Studio Sunken. The selected option is a white pill with the card shadow and a 1px inset Amp Glow Hover ring (the 3:1 selected cue). Text is Burnt Copper in segments, Soot in settings tabs. Press squashes to .96.

### Generator pipeline (signature)
The "AI thinking" stage: the query in a pill, a 240px canvas of drifting orange particles (Amp Glow at varying alpha, honoring reduced motion), and a four-node phase tracker (embed → vector search → diversity → order). The active node is an Amp Glow dot with Brand Ink icon and CTA glow; done nodes are success-tinted. A mono percentage and a dark terminal log (Soot fill, Amp Glow prompt glyph, success/warn colored lines) stream progress. Results show a 2×2 album mosaic, the playlist title at 30px, mono stats, and track rows. Rows are 12px-radius and on hover fill Studio Sunken, nudge 2px right, and reveal the remove button; touch devices always show it. The track number stays visible on hover, and keyboard focus inside a row also reveals the remove button. At ≤640px the album moves to a second line under the title (at ≤480px the decorative art swatch steps aside). The query pill and the results title wrap and clamp to two lines, with the full prompt in `title`.

## Do's and Don'ts

### Do:
- **Do** use Burnt Copper (`--accent-fg` / `--brand-11`) for every orange word or icon, and Amp Glow (`--brand-9`) only for fills, bars and indicators.
- **Do** pair every semantic solid with its text shade: `--success-fg`, `--warning-fg`, `--error-fg`, `--info-fg`, `--purple-fg`.
- **Do** give raised surfaces a resting shadow (`--shadow-md`) and lift interactive ones to `--shadow-lg` with a −3px rise on hover.
- **Do** spring transforms and shadows (250ms `--ease-spring`) and keep color changes at 150ms. `prefers-reduced-motion` disables all of it.
- **Do** set numbers, durations, timestamps, model ids and commands in IBM Plex Mono.
- **Do** use the 2px `--focus-ring` outline with 2px offset on every interactive element.
- **Do** keep dark values under `.dark` on `<html>` only.
- **Do** use Amp Glow Hover (`--brand-10`) for thin indicators (progress, tracks, selection rings, active bars) so they clear 3:1 on light surfaces.
- **Do** make every clickable element a real `<button>` or `<a>` (with an `aria-label` when it is icon-only). A clickable `<div>` cannot take keyboard focus.

### Don't:
- **Don't** use `#F97316` as text or icon color on light surfaces (2.7:1), or white text on it (2.8:1).
- **Don't** put muted text (`--fg-3`) on Studio Sunken, Peach Wash, Peach Mist or semantic tints. Use `--fg-2`.
- **Don't** mark emphasis with a colored left or right border stripe on cards, rows or callouts. Use a tint, the glow or weight.
- **Don't** use a zero-offset colored halo as a shadow. Glows carry a vertical offset.
- **Don't** target `[data-is-root-theme]` or other always-present Radix attributes for theme-specific tokens.
- **Don't** add gradients beyond the incumbent few (the orange logo tile, the login and Generator hero glows, the avatar, album-art placeholders).
- **Don't** add new kicker pills or eyebrow labels above headings. The Generator's "AI Playlist Generator" kicker is the one incumbent instance.
- **Don't** use emoji in app chrome. Icons are Lucide line icons at a 2px stroke.
