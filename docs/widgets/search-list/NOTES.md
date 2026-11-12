# search-list — notes

## 1. What it is

The list view that did not fit into the search card. Own HTML: **6 358 B raw / ≈ 3 130 B zlib-9**
(cap 4 096). Looks like the card: same body inset, `.g` ghost buttons, `.c` chips, `.ch` badge, `.m`
meta, `.el` ellipsis, `.sq` 36 px squares, `.dots` pulse, error map, slide animation via
`element.animate`, swipe on a `touch-action:pan-y` surface.

## 2. Real-device round (2026-11-12) — what changed

- **«Закрыть» ghost chip** in the header (right of «Результаты 1–10 из 302») → request `x`; the bot
  deletes the message. Pending = dots inside the chip.
- **Pager moved to the bottom row**: `‹ [sort] [Фильтры] [Карточка] ›` — squares at the ends, chips
  stretched between (`.nv .c{flex:1}`), the same row the card has. Chips `.76em` / 4 px padding so
  «Размер ↑» fits a 65 px chip at 320 px. The header had no room for «Закрыть» + ‹ › at 320 px.
- **New hand-back flow**: a row tap / «Карточка» → `cd` → the bot sends a fresh card below and deletes
  the list; `{"ok":1}` leaves the widget as is (it is about to disappear). The done state «✓ Открыта
  карточка 7» / «✓ Закрыто» stays as the fallback (mock scenarios 1 and 4).
- **No overflow**: `*{min-width:0}`, `body{overflow-wrap:anywhere}`, ellipsis on buttons/chips/title/meta,
  the rating badge `flex:none`, index pill grows for 3-digit indexes. Buttons are inline-blocks so their
  labels can ellipsise. `pointercancel` resets the swipe. Bottom padding includes
  `var(--tg-content-safe-area-inset-bottom,0px)` so the stand shows the client's 22 px band.
- Rating badge 18 px (was 20): with a rating on every row the page was 569 px; now every row is 42 px.

Heights (stand, 22 px band, 320 px): 10 rows **548 px** (558 before the badge change), stress page
with 3-digit indexes and 10 ratings 549, last page with 2 rows 209, empty 225, done 150 (stand
minimum). Audit at 320/360/430: no problems.

## 3. Layout

- Header = «Результаты 11–20» (bold) + «из 302» (hint, thin-space thousands) + «Закрыть» ghost chip.
  Second line in hint colour = «дюна» · Сиды ↓; it doubles as the **toast slot**.
- Rows: 22 px index pill, title `.95em/600` + optional ★ rating badge, meta line `.82em` hint.
  `padding:4px 0`, 1 px separators (`.r:first-child` has none). Whole row is the tap target.
- Bottom row: ‹ (disabled on page 0), sort label, «Фильтры» (accent dot when `fa`), «Карточка»
  (= first row), › (disabled on the last page).
- Done state: one hint-coloured line, no controls, no header. Empty state: «Ничего не найдено с этими
  фильтрами» + the bottom row.

## 4. Behaviour

- Row tap → `cd:ctx:<index>`; pending = the index pill shows the dots (`.ix.w{font-size:0}`).
  Every `[data-a]` loses pointer events while a request is in flight.
- ‹ › → `lp:ctx:<page>`; full-state answers slide ±24 px with the content swapped at the midpoint.
- Swipe: `pointerdown` records x; the drag claims the pointer only after 10 px (capturing on
  pointerdown broke row taps); ≥ 56 px commits, rubber factor .12 at the ends; `pointercancel` resets.
- Sort chip → `ls:ctx` with the next code; `fs` / `x` / `cd` → `{"ok":1}` does nothing visible, a state
  with `v` (done) is applied; `chatDataChanged` applied only when `r` grows.
- Stand artifact: after a done answer the iframe keeps its old height (the stand never shrinks a frame);
  the clients size the bubble to the content.

## 5. widget-rules.md checklist

- No form controls, no images, no external resources, no `-webkit-line-clamp`, no `vh`, no `:focus`,
  no CSS nesting, no `:has()`; `max()` with `env()`/`var()` is in profile.
- Colours: theme variables only. No opacity/filter → 0 heavy elements. 1 `@keyframes`.
- `request()` only synchronously inside `click` / `pointerup`; one request at a time.
- `env(safe-area-inset-bottom)` padding with `body{margin:0}`; the bottom row ends above the band.
- ≈ 40 CSS rules, ≈ 95 nodes on a full page, no layout reads in hot paths.

`mock.json` was edited directly in the real-device round (new `x` answers, `ok` flow, stress scenario); `shots/mock_gen.py` predates it and would overwrite those changes — do not run it without porting them.
