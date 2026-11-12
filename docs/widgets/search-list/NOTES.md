# search-list — notes

## 1. What it is

The list view that did not fit into the search card (card NOTES §1: list ≈ 320 B compressed on top of
a card already at 4 073 B). Its own HTML: **6 271 B raw / 3 031 B zlib-9** (cap 4 096, aim 2 500 —
see §4 for what the extra 500 B buy). Looks like the card: same body inset, `.g` ghost buttons, `.c`
chips (30 px pills), `.ch` badge, `.m` meta, `.el` ellipsis, `.sq` 36 px squares, `.dots` pulse,
error map, slide animation via `element.animate`, swipe on a `touch-action:pan-y` surface.

Screens (harness, 320 px): 10 rows **544 px**, last page with 2 rows 195 px, empty 211 px, done
~60 px (the harness reports a 150 px minimum). With the client's 22 px time band instead of the 10 px
minimum padding the full page is 556 px — under the 560 target, 44 px under the 600 hard clip. Checked
at 320/360/430, dark/light (`shots/`).

## 2. Layout decisions

- Header = «Результаты 11–20» (bold) + «из 302» (hint) + ‹ › squares on the right (the card's pager
  position). A second line in hint colour = «дюна» · Сиды ↓; this line doubles as the **toast slot**
  (success accent / error destructive, then the context comes back) — a separate footer cost 22 px
  that the 10-row page did not have.
- Rows: 22 px index pill (3-digit indexes grow into a pill), title `.95em/600` + optional ★ rating
  badge on the right, meta line `.82em` hint. `padding:4px 0`, 1 px separators (`.r:first-child` has
  none). Whole row is the tap target (`cursor:pointer`, `data-a=cd data-i=<index>`).
- Bottom chips: sort label (from `sl`), «Фильтры» (accent dot when `fa`), «Карточка» (= first row).
  Pager squares could not join this row at 320 px (293 px needed, 292 available) → they stay in the
  header.
- Done state: «✓ Открыта карточка 7» in hint colour, no controls, no header (one line as required).
- Empty state: «Ничего не найдено с этими фильтрами» + the chips row, pager disabled, header
  «Результаты 0».

## 3. Behaviour

- Row tap → `cd:ctx:<index>`; pending = the index pill shows the 3-dot pulse (`.ix.w{font-size:0}`
  hides the number, the dots keep their px size). Every `[data-a]` loses pointer events while a request
  is in flight (one request at a time).
- ‹ › → `lp:ctx:<page>`; the square's glyph is hidden under the dots while pending. The answer (full
  state) slides ±24 px with the content swapped at the midpoint, `selectionChanged` haptic.
- Swipe: `pointerdown` records x; the drag claims the pointer (`setPointerCapture`) only once it
  passes 10 px — **capturing on pointerdown broke row taps** (the click then targets the common
  ancestor `.sw`, not the row). ≥ 56 px commits, rubber factor .12 at the ends, `pointercancel`
  resets the transform. A committed swipe never also fires a row tap (the click lands on `.sw`).
- Sort chip → `ls:ctx` with the next code (SK cycle like the card); `fs` → `{"ok":1}` does nothing
  visible; `chatDataChanged` applied only when `r` grows.
- `prefers-reduced-motion`: no slide, no dot pulse.

## 4. Tried / rejected / could be cut

- Rows as objects `{"i","t","m","im"}` → arrays `[t,m,im]` and the index derived from `i0`: −20 B per
  row, −200 B per page of chat_data (the real constraint here, not the HTML).
- Pre-formatted `m` «45 ГБ · 01.03.2024 · ⇡1 543 · RuTracker» (50 B) → pipe fields (36 B): −14 B
  per row; the widget inserts « · » and ⇡. Pure layout, the values are still formatted by the bot.
- 8 rows per page (BRIEF §2) → kept **10**: today's `LIST_PAGE_SIZE`, and the page arithmetic in
  `lp:<page>` needs a fixed size. The byte budget is met by shrinking strings (PROTOCOL §1), never rows.
- Two rating pills (IMDb + КП) per row — no room at 320 px; one pill, IMDb preferred.
- Query text in the header line instead of a chip — a chip plus the sort label did not fit beside
  the pager at 320 px.
- Size budget beyond the 2 500 B aim: swipe ≈ 200 B, slide ≈ 120 B, error map ≈ 150 B. Drop the swipe
  first if room is ever needed; the chips/pager stay as the discoverable path.

## 5. widget-rules.md checklist

- No form controls, no images, no external resources, no `-webkit-line-clamp` (single-line
  `text-overflow:ellipsis` only), no `vh`, no `:focus`, no CSS nesting, no `:has()`.
- Colours: theme variables only (`--tg-theme-*`); accent dot = `--tg-theme-accent-text-color`.
- Heavy elements: none (dots animate `transform`; disabled buttons are dimmed by the client).
- Animations: 1 `@keyframes`, `element.animate` for the slide (Web Animations are in the profile).
- Gestures: `touch-action:pan-y` only on the rows surface; holds stay with the chat; controls start at
  14 px from the left edge inside the bubble (the leading 20 px band is the bubble's own margin area
  in the client, same as the card).
- `request()` only synchronously inside `click` / `pointerup` handlers.
- `env(safe-area-inset-bottom)` padding with `body{margin:0}`; the chips row ends ~70 px before the
  right corner where the time is drawn.
- `localStorage`/`sessionStorage` not used; nothing to restore — the state is fully in chat_data.
- 38 CSS rules, ≈ 95 nodes on a full page, no layout reads in hot paths (one `pg()` per event).
