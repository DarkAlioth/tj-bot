# search widget — notes

## 1. Scope (unchanged)

One HTML cannot hold the card + the list + the filter sheet under the 4 096-byte zlib cap (measured
history: 7 312 → 6 205 → 5 112 → 4 073 B for the card alone with the old hero). The list and the sheet are
the sibling widgets `search-list` and `search-filters`, sent as separate messages. Since the real-device
round the hand-back is also a *new message*: the bot sends a fresh card and deletes the list/sheet
(PROTOCOL.md). «list view state» / «filter sheet state» therefore do not exist inside this widget.

## 2. Real-device round (2026-11-12) — what changed and why

Product owner decisions, all applied:
- **No images at all.** The ambient mosaic (4×6 blurred cells) and the category gradient hero are gone,
  with the keys `m`, `hu`, `hp`, `img`. The card is a typographic composition: tracker (accent chip) +
  category chip + «⚡ кэш» + «i / n» header → title block (Russian title 1.2em/700, 2-line clamp; English
  subtitle in hint colour) → year + quality chips + ★ rating pills → meta / people / audio lines → stat
  tiles (health ring, size, date) → plot with «ещё» → actions → nav row → footer. One accent (tracker chip
  + primary button), surfaces in `--tg-theme-secondary-bg-color`, 8–10 px rhythm.
- **No caption**: the message is the widget. `padding-bottom:max(12px,env(safe-area-inset-bottom),
  var(--tg-content-safe-area-inset-bottom,0px))` keeps the time band clear (the `var()` makes the preview
  stand, where `env()` is 0, draw the same 22 px band as the clients); the only thing on the last line is
  the «Загрузил X» text.
- **Text never overflows**: `*{min-width:0}`, `body{overflow-wrap:anywhere}`, 1-line ellipsis on every
  line that must stay one line (`.el`, chips, buttons, tile values/labels, footer), `max-height` clamps on
  the title (2 lines), the chip row (2 rows) and the plot (2 / 7 lines), chips `max-width:100%`.
  Buttons are plain inline-blocks (not inline-flex) so `text-overflow:ellipsis` works on their labels.
- **Nothing outside the bubble at rest**: the mosaic cells (`inset:-20%`, reported at ±262 px by the
  audit) were the culprit; the body now sits in `#w{overflow:hidden;margin:0 -14px;padding:0 14px}`, so
  the 220 ms slide (±24 px) and the swipe drag are clipped at the bubble edge. `pointercancel` resets
  the drag transform (missing before).
- **Nav row**: `‹ [sort] [Фильтры] [Список] ›` — pager squares at the ends, the three chips stretched
  between them (`.nv .c{flex:1}`), one 36 px row. The position «12 / 302» lives in the header (hint
  colour, tabular, thin-space thousands). A second «‹ 12 / 302 ›» row was prototyped and rejected: it
  duplicated the header index and cost 42 px; a single row is also what the list widget does.
- Polish bought with the freed bytes: `button:active{transform:scale(.97)}` (was missing on the card),
  `pointercancel`, thin-space thousands in the header index, rating pills in text colour (hierarchy
  against the hint-coloured quality chips), tile values `.9em` nowrap (the date «01.03.2024» wrapped at
  320 px with the old `.95em`), admin row `.9em`/4 px padding and `flex:auto` (the two text buttons share the row by content,
  `button.sq` stays 36 px) so «★ В избранном» + «На сервер» + «↗» fit 320 px even in a wide fallback font, nav chips `.76em`/4 px so «Размер ↑» and «Фильтры •» fit 65 px chips.

## 3. Behaviour (unchanged unless noted)

- Paging: ‹ › and the horizontal swipe on the card body (`.sw`, `touch-action:pan-y`, pointer capture,
  ≥ 10 px starts, ≥ 56 px commits, rubber factor .12 at the ends); every page change slides `#b` ±24 px
  via `element.animate` (220 ms) and swaps the content at the midpoint; `prefers-reduced-motion` → no
  slide, no pulse, no `:active` transition.
- Plot: 2-line clamp with the «ещё» fade; first tap loads `more:<h>` when `pm:1`, later taps toggle
  locally; expanded = **7 lines** (was 5; a 360-char plot fits at 360 px+) and the people/audio lines
  collapse. Expanded typical card ≈ 540 px at 320 px.
- Pending: the tapped button gets the 3-dot pulse, every `[data-a]` loses pointer events; toasts in the
  footer (success accent / error destructive), error-code map per brief; `en:1` → «загружаем детали …».
- Haptics guarded by try/catch; revision guard on `chatDataChanged`.

## 4. Measured (preview stand, 22 px band, 320 px)

Typical card 475 px, series with `en:1` 399, game without metadata 350, music `ti`-only 375, books 370,
empty 221, stress card (60-char word, 12 chips, 300-char plot, admin) 496, raw `ti` 120 chars 376.
360 / 430 px lower. Audit (`node widget_audit.mjs search,search-list,search-filters --widths=320,360,430`):
no problems. Screenshots in `shots/` (old hero-era shots moved to `shots/_old/`).

## 5. Tried and rejected

- Mosaic / gradient hero — removed by product decision after the real-device test (looked like a
  placeholder without a real poster, and it was the only thing outside the bubble).
- «‹ 12 / 302 ›» pager row under the actions (see §2) and «‹ Пред.» / «След. ›» labelled buttons — one
  row of squares + chips is more compact and consistent with the list widget.
- Uppercase letter-spaced tile labels (brief headings style) — «9 ДН. НАЗАД» did not fit a 75 px tile.
- Count-up on the seeders number, SVG symbol icons — still out (bytes); glyphs ☆ ★ ‹ › ↗ ⚡ stay.

## 6. widget-rules.md checklist

- No images, no `filter`/`backdrop-filter`/opacity → 0 heavy elements; dots animate `transform`.
- No `-webkit-line-clamp`: clamps are `max-height` + `overflow:hidden`; `text-overflow:ellipsis` only
  on single lines. `overflow-wrap:anywhere` is standard CSS (in profile: box model / text).
- `max()` with `env()` + `var()` fallback — calc/min/max, custom properties and `env(safe-area-*)` are in
  the profile.
- `element.animate` with keyframe `offset` (Web Animations), `setPointerCapture`, pointer events only.
- `openLink` / `request` are called synchronously in `click` / `pointerup` handlers; one request at a
  time (`busy` + `.p [data-a]{pointer-events:none}`).
- Theme variables only; the health ring uses a fixed `hsl()` ramp (meaning colour, allowed).
- `#w{overflow:hidden}` is on a div, not on body/html (overflow on body would propagate to the viewport).
- Fonts inherited (system), sizes in em; ≈ 43 CSS rules, ≈ 110 nodes on a full card.

## 7. Size budget

8 182 raw / **3 961 B zlib-9** (cap 4 096  135 B headroom; the old hero card was 4 073). If room is ever
needed: the empty-state suffix «с этими фильтрами» (−40 B), the «загружаем детали» hint (−45 B).
