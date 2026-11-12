# search-filters — notes

## 1. What it is

The filter sheet that did not fit into the search card. Own HTML: **5 470 B raw / 2 709 B zlib-9**
(cap 4 096). Same family look as the card and the list: `.g` ghost, `.c` chips, `.ac` accent, `.m` meta,
`.hd` uppercase section headings, `.dots` pulse, error map.

## 2. Real-device round (2026-11-12) — what changed

- **«Закрыть» ghost chip** in the header, right of the «Фильтры» title → request `x`; pending = dots in
  the chip. The bottom row stays «Сбросить» + «Применить» (a third button there did not fit 320 px).
- **New apply flow**: «Применить» → `f:…` → `{"ok":1}` → the widget clears its `sessionStorage` entry
  and stays as is; the bot sends a fresh card below and deletes the sheet. The done line «✓ Применено:
  …» / «✓ Закрыто» is the fallback (mock scenarios 1 and 3). `rq()` is now shared by `f` and `x`.
- **No overflow**: `*{min-width:0}`, `body{overflow-wrap:anywhere}`, ellipsis on every button; category
  chips are flex rows (label `.el` shrinks, count `flex:none`) — with plain inline-block buttons the count
  was pushed outside the chip (audit: `i [outside 12]`). 20-char no-space labels and 6-digit counts
  (mock scenario 7) ellipsise inside the chip. Bottom padding includes
  `var(--tg-content-safe-area-inset-bottom,0px)` so the stand shows the client's 22 px band.

Heights (stand, 22 px band, 320 px): defaults 503 px, 10 categories at caps 555, no categories 336,
done 150 (stand minimum); 430 px: 503 → 451 (3-column grid). Audit at 320/360/430: no problems.

## 3. Layout decisions

- Header «Фильтры» + «Закрыть»; live summary «Активно: …» (hint, 2-line clamp).
- Category chips in a **grid** (2 columns, 3 from 400 px): ten 18-char labels in a flex-wrap hit 678 px.
  The grid bounds it to 5 rows; long labels get a single-line ellipsis, the count stays visible.
- Three segmented controls: a secondary-bg track with 2 px gaps, transparent segments, the active one in
  button colour (`button.ac` beats `.sg button`). Five size segments fit 320 px at `.78em`.
- Buttons: «Сбросить» ghost (fixed width) + «Применить» primary (flex 1). Error/transport text in a
  footer line under the buttons, destructive colour, persistent until the next tap.

## 4. Behaviour

- Taps never rebuild the DOM: `mark()` toggles `.ac` on every `[data-a]`; `draw()` runs only on load,
  state change and done.
- Working selection `w=[se,sz,dt]`, `wc=token`; saved to `sessionStorage` under `sf:<qh>:<f>:<c>` after
  every tap; restored on load when the key matches; cleared on a successful apply (`ok` or done).
- «Применить» → `f:<ctx>:<code>:<cat>`; «Закрыть» → `x`; every control loses pointer events until the
  answer. `{"err"}` keeps the controls and shows the text; `{"ok":1}` does nothing visible; a state with
  `v`/`done` redraws (`fin()`); `chatDataChanged` with a newer `r` does the same.
- Haptics: `selectionChanged` per segment/chip tap, `impactOccurred('light')` on reset,
  `notificationOccurred` success/error on the answer. All guarded.

## 5. Tried / rejected

- One request per tap (bot-side cycling) — rejected: latency on every tap, 5 requests to set 3 filters.
- «Закрыть» as a third bottom button — «Закрыть» + «Сбросить» + «Применить» did not fit 320 px without
  ellipsis; the header slot is the sheet idiom anyway.
- Flex-wrap chips — clipped at 10 long labels (see §3).

## 6. widget-rules.md checklist

- No form controls (segments and chips are `<button>`s), no images, no external resources, no
  `:checked`/`:focus`, no nesting, no `:has()`, no `vh`; `max()` with `env()`/`var()` is in profile.
- Colours: theme variables only. No opacity, no filters → 0 heavy elements. 1 `@keyframes`.
- `request()` synchronously in the click handler; one request at a time (`busy` + `.p [data-a]`).
- `sessionStorage` reads/writes wrapped in try/catch.
- `env(safe-area-inset-bottom)` padding with `body{margin:0}`; the footer line is short, the «Закрыть»
  chip is at the top — nothing tappable near the time corner.
- 31 CSS rules, ≈ 65 nodes.

`mock.json` was edited directly in the real-device round (new `x` answers, `ok` flow, stress scenario); `shots/mock_gen.py` predates it and would overwrite those changes — do not run it without porting them.
