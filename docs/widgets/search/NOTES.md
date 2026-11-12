# search widget — notes

## 1. The scope decision (read this first)

The brief asked for one HTML with three internal views (card, list, filter sheet). Measured with
`zlib.compress(html, 9)` the way the server counts:

| variant (hand-minified, no comments) | raw | zlib-9 |
|---|---|---|
| card + list + sheet + swipe, first full draft | 17 852 | 7 312 |
| same, rewritten ultra-compact (unquoted attrs, helpers, no SVG symbols) | 13 187 | 6 205 |
| card only, formatting still in JS (fS/fI/fD/CAT/TRK maps) | 10 505 | 5 112 |
| **card only, bot pre-formats strings, glyph icons (shipped)** | **8 505** | **4 073** |

Marginal compressed cost of each piece in the compact draft: filter sheet ≈ 550 B, list ≈ 320 B,
swipe ≈ 210 B, error map 150 B, ring 115 B, mosaic 135 B, plot/«ещё» 150 B, dots 60 B. Cyrillic
text is only ≈ 600 B of the total (UTF-8 Cyrillic costs ≈ 1.2 B/char after deflate; encoding it
as ASCII saved 250 B minus a 200 B decoder — not worth it). The code itself is the budget.

So even a bare card does not fit together with the list and the sheet; the list and the sheet
had to leave the widget. They become hand-off requests (`ls:`, `fs:`) answered by separate
messages — exactly what the current text bot does with `render_list` / `build_filter_menu`, so the
handler author can reuse them today and replace them with dedicated widgets later. When filters
are applied elsewhere, the bot edits this card's chat_data and the card redraws itself.

Consequences for the brief's scenario list: «list view start state» and «filter sheet state»
cannot exist inside this widget; the mock covers the states that do.

## 2. What is in the widget

- Header: tracker badge (accent chip), ‹ position › pager (disabled at the ends).
- Hero: ambient backdrop = 4×6 mosaic cells under one `filter:blur(18px)` container (the only
  heavy element), or a category-hue gradient (`hu`) when there is no mosaic; bottom gradient to
  `--tg-theme-bg-color` so the title sits on the bubble colour in both themes. Category chip and
  «⚡ кэш» sit in the band's top-right corner (moved out of the header — at 320 px the header
  could not hold tracker + category + cache + pager). Title (2-line clamp via fixed max-height),
  English title, year/quality chips, ★ IMDb / ★ КП pills.
- Meta, people, audio lines (1-line ellipsis each), «загружаем детали …» when `en:1`.
- Stat tiles: health ring (SVG stroke-dasharray, hue 0→140 by seeders/(seeders+peers), 0 seeders
  → red «нет сидов»), size, date (+ relative label).
- Plot: 2-line clamp, «ещё» with a fade; first tap loads `more:<h>` when `pm:1`, next taps toggle
  locally; expanded = 5 lines and the people/audio lines collapse so the card stays ≤ 560 px.
- Actions: «Скачать .torrent», ☆/★ favorite (optimistic, reverted on error), «На сервер» (admin),
  «↗» open on tracker (https only). Chips: sort (label from the bot, next code computed), «Фильтры»
  (accent dot when active), «Список».
- Pending: tapped button gets a 3-dot pulse, every `[data-a]` loses pointer events (one request at
  a time). Toasts in the footer (success accent / error destructive), error-code map per brief.
- Paging: ‹ › and horizontal swipe on the hero+meta+tiles surface (`touch-action:pan-y`,
  pointer capture, ≥ 10 px starts the drag, ≥ 56 px commits, rubber factor at the ends); every
  page change slides the body ±24 px via `element.animate` (220 ms, brief easing) and swaps the
  content at the midpoint; `prefers-reduced-motion` → no slide, no pulse.
- Haptics: selectionChanged on page change, impact light on toggles, notification on outcomes;
  all calls guarded by try/catch.
- Revision guard: `chatDataChanged` applied only when `r` > newest seen.

Measured heights (harness, 320 px): 498 px full card, 515 px with the plot expanded, 425 px
series without metadata, 378 px game, 223 px empty state. Width 360/430 and light/dark verified
(screenshots in `shots/`).

## 3. Tried and rejected

- Per-cell `--h/--s/--l` custom properties so that enrichment morphs the gradient into the
  mosaic with a colour transition — 140 B, dropped; enrichment now redraws in place.
- Count-up on the seeders number (brief §2) — 150 B, dropped.
- SVG `<symbol>` icon set (star, chevron, external) — 120 B + markup; replaced with the text
  glyphs ☆ ★ ‹ › ↗ (not emoji, render on all platforms). Deviation from the brief's "SVG icons"
  rule, flagged here.
- In-widget footer «обновлено 12:40 · обновить» + `rf` — dropped; the «⚡ кэш» chip remains and
  the caption keyboard keeps 🔄.
- Formatting helpers in JS — moved to the bot (`sz`, `dt`, `dr`, `ml`, `pp`, `aa`, `sl`, `cn`,
  `im/kp` as strings). Saved ≈ 450 B and keeps formatting in one place (`services/formatting.py`).

## 4. widget-rules.md checklist (borderline items)

- `filter:blur(18px)` on one container only (heavy count 1); the dots use `transform` scale, not
  opacity; disabled buttons are dimmed by the client (free).
- No `-webkit-line-clamp`: clamps are `max-height` + `overflow:hidden`; `text-overflow:ellipsis`
  only on single lines.
- Unquoted attribute values (`class=mz`, `data-a=more`) — standard HTML5; quoted where the value
  contains spaces or `:`.
- `light-dark()` not used (removed with the per-cell palette); colours are theme vars + fixed
  `hsl()` for the health ring / category gradient, as the brief allows.
- `element.animate` with keyframe `offset` — Web Animations are in the profile.
- `openLink` is called synchronously in the click handler; `request()` likewise (also from
  `pointerup` for the swipe, which the rules allow).
- `setPointerCapture`, `pointermove`, `pointerup` only; vertical drags never start a transform
  (the 10 px threshold is horizontal), so there is nothing to undo on `pointercancel`.
- Unknown-attribute risk: only `data-*` is used for actions.
- `env(safe-area-inset-bottom)` in `body` padding with `body{margin:0}` → one time band.
- Fonts: inherited system font only; sizes in em.
- `document.body.className` is rewritten in `draw()`; the pending class `p` is re-added by `rq`
  before each request, so a redraw inside an answer cannot leave the widget unlocked.

## 5. Size budget

8 505 raw / 4 073 zlib-9 (cap 4 096, 23 B margin; the brief's 3 900 target was not reachable
with the required card content — see §1). 42 CSS rules, ≈ 110 nodes on a full card.
If more room is ever needed: the empty-state suffix «с этими фильтрами» (−40 B), the «загружаем
детали» hint (−45 B), the rubber-band factor (−20 B).
