# search-filters — notes

## 1. What it is

The filter sheet that did not fit into the search card (card NOTES §1: sheet ≈ 550 B compressed).
Own HTML: **5 221 B raw / 2 619 B zlib-9** (cap 4 096, aim 2 500 — the 120 B over the aim are the
in-place class toggling and the sessionStorage mirror, both worth keeping). Same family look as the
card and the list: `.g` ghost, `.c` chips, `.ac` accent, `.m` meta, `.hd` uppercase section
headings (BRIEF typography), `.dots` pulse, error map.

Heights (harness, 320 px): defaults 482 px, 10 categories at caps 534 px, no categories 315 px,
done ~60 px. With the client's 22 px time band: +12 px → 546 px worst case (≤ 560). Checked at
320/360/430, dark/light (`shots/`).

## 2. Layout decisions

- Header «Фильтры» + the live summary «Активно: …» (hint, wraps to 2 lines max, `max-height` clamp).
- Category chips in a **grid** (2 columns, 3 from 400 px) instead of flex-wrap: ten 18-char labels
  wrapped to one chip per row at 320 px and the sheet hit 678 px (clipped). The grid bounds it to
  5 rows (180 px); long labels get a single-line ellipsis inside the chip, the count stays visible.
  `repeat(auto-fill, minmax())` was avoided in favour of a width `@media` (explicitly in-profile).
- Three segmented controls: a secondary-bg track with 2 px gaps, transparent segments, the active one
  in button colour (`button.ac` — specificity had to beat `.sg button`, the first draft showed no
  active segment at all). Five size segments fit 320 px at `.78em` with 3 px padding.
- Buttons: «Сбросить» ghost (fixed width) + «Применить» primary (flex 1). Error/transport text in a
  footer line under the buttons, destructive colour, persistent until the next tap.
- Done state: «✓ Применено: …» one or two lines, hint colour, nothing else.

## 3. Behaviour

- Taps never rebuild the DOM: `mark()` toggles `.ac` on every `[data-a]` by comparing with the
  working selection, so the background transition (.15 s) plays and nothing jumps. `draw()` runs
  only on load, reset, state change and done.
- Working selection `w=[se,sz,dt]`, `wc=token`; saved to `sessionStorage` under
  `sf:<qh>:<f>:<c>` after every tap; restored on load when the key matches (verified with a page
  reload in the harness: «Активно: Фильмы HD · сиды 10+» survives). Cleared on a successful apply.
- «Применить» → `f:<ctx>:<code>:<cat>`; the button shows the 3-dot pulse, every control loses
  pointer events until the answer. `{"err"}` keeps the controls and shows the text; a done answer
  redraws as one line; `chatDataChanged` with a newer `r` does the same.
- Haptics: `selectionChanged` per segment/chip tap, `impactOccurred('light')` on reset,
  `notificationOccurred` success/error on the answer. All guarded.

## 4. Tried / rejected

- One request per tap (bot-side cycling like today's 🔁 buttons) — rejected: latency on every tap,
  5 requests to set 3 filters, and the BUSY window; local selection + one apply is the Widgets way.
- Disabling «Применить» when nothing changed — dropped (12 B of logic, and "apply the same" is a
  harmless no-op the user may want after an error).
- Showing `n` in the header — the «Все 302» chip already carries it.
- Flex-wrap chips — see §2 (clipped at 10 long labels).

## 5. widget-rules.md checklist

- No form controls (segments and chips are `<button>`s), no images, no external resources, no
  `:checked`/`:focus`, no nesting, no `:has()`, no `vh`.
- Colours: theme variables only. No opacity, no filters → 0 heavy elements.
- 1 `@keyframes`; transitions on `background`/`color`/`transform` only; `prefers-reduced-motion`
  turns them off.
- `request()` synchronously in the click handler; one request at a time (`busy` + `.p [data-a]`).
- `sessionStorage` reads/writes wrapped in try/catch (private window / quota).
- `env(safe-area-inset-bottom)` padding with `body{margin:0}`; the footer line is short so the time
  corner stays clear.
- 29 CSS rules, ≈ 60 nodes.
