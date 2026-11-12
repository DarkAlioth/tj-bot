# console widget — design notes

## Result
- `widget.html`: 8 274 B raw / **4 052 B zlib-9** (limit 4 096; the 3 900 aim was not reachable
  with the full feature list — see "Size budget"). ≈ 140 nodes with 6 rows, 45 CSS rules, 1 keyframes,
  0 heavy elements (no opacity/filter), 2 SVG symbols (`a` arrow, `p` pause; rotation via CSS).
- Height at 320 px: 436 px list (+24 margins), 523 px with an accordion open, 468 px with the 5th
  «Ошибка» tab (chips wrap), 468 px with the pause-all confirm (pager wraps) — all ≤ 560.
- Verified in headless Chromium (Playwright chromium-1243 via `tools/shot.mjs`, Playwright MCP has no
  Chrome): 6 scenarios × 2 themes × 3 widths, accordion/actions/confirms, live ticks (sparklines grow,
  Шрек completes mid-run: icon/hue/column/counts switch), unavailable (bg FAILED → red dot + «обновлено
  —»), action error toast, reduced motion (`RM=1`). No console errors, no horizontal overflow.

## Layout decisions
- **Transfer panel** = card: two blocks (↓ / ↑) with the big tabular speed, a 44×16 sparkline (12
  points, flat line pre-filled from the first sample so it never shows a lone dot) and «N ГБ всего»;
  third line: «Свободно X» + thin capacity bar (used fraction, hue green/amber/red at 80 %/90 %) +
  connection dot + «обновлено HH:MM:SS». The whole panel is rebuilt from the template on every
  snapshot (≈ 25 nodes); bars transition because the template first draws the previous width (cache
  `PV`), forces one layout read, then sets the new width (one `offsetWidth` per draw, far below the
  128/task limit).
- **Rows**: 43 px — icon, 1-line ellipsis name, absolute 3 px bar under the name, right column speed +
  ETA/ratio/percent. Rows are `div[data-a]` not `<button>` (saves the reset CSS); the row's tap target
  is the full row.
- **Accordion**: one open at a time; while open the panel **folds** to the speed line and the tabs row
  hides (`.fold+div`) — this is what keeps 320 px under 560 with 6 rows + details + 2 action rows.
  Details come preformatted from the bot (`x.l`, `x.sp`) — moving ~150 B of formatting out of the
  widget. Tapping the open row again closes it; a tab/page tap closes it too.
- **Confirms** are inline state (`cfm`): 1 = «Все на паузу? Да/Нет» in the footer, 2 = «Отмена / Без
  файлов / С файлами» replacing the priority row.
- **Toast** lives in the footer (replaces the toggles for 1.6 s / 2.6 s for errors) instead of a
  fixed pill: zero positioning CSS, no overlap with the time badge.
- **Pending**: the tapped button gets `.w` (scale pulse) and `.bz` disables every `[data-a]` until the
  answer; the 3-dot `.dots` pattern from the brief was dropped for size (−90 B).
- **Safe area**: `body{margin:12px 14px}` only — the client's default body padding already includes
  the time band (widgets.txt §"The time in the corner"), and the harness stub does the same.

## Protocol decisions
- Rows are positional arrays: 6 rows × 36-char Cyrillic names fit in 990 B; objects with 1-char keys
  did not (1 086 B). Details are not in the state (`x:<h>` on expand).
- State codes are 1–2 chars (`d`, `ds`, `uf`, `p`, `c`, `ex`): the widget derives icon/hue/column from
  the first/second char with no label table (labels only in `x.l`).
- Action answers are patches (`row` + `x` + `cnt` + `tr`, ≤ 710 B) because a full snapshot plus
  details could exceed 1024 B; `del/alt/sa/ra` answer with a snapshot.
- `bg` never edits chat_data (no flood limits); the stored `tab`/`pg` tell the bot what to answer.
- Tab switching is **always a request** (`t:<tab>`): the local-switch mode (filter in the widget when
  all rows are in the state) was implemented and removed — 150 B that only saves one 300 ms round trip.

## Tried and rejected (size)
Eleven drafts; the first complete one was 7 542 B compressed. Terser on the final JS saves only ~8 %,
so the cuts were structural: per-row DOM reconciliation → template rebuild with a width cache;
in-widget state labels → `x.l` from the bot; 11 SVG symbols → 2 (priority ⤒/⤓ = arrow + CSS
`border-top`); `ERR` code map → only TIMEOUT is distinguished (BUSY cannot happen behind the widget's
own `busy` flag, NOT_ALLOWED cannot happen in the private chat, RATE_LIMITED/FAILED/INVALID_ANSWER →
«Ошибка связи»); count-up tweens (−140 B, bars still animate); 3-dot pulse → scale pulse; fixed toast →
footer toast; `selectionChanged` haptics → one `impactOccurred('light')` per tap + `notificationOccurred`
per outcome; local tab mode; pager disabled states (the bot clamps). Cyrillic copy costs ≈ 550 B
compressed and is irreducible.

## Known limitations / open questions
- `aim ≤ 3900` not met: 4 052 B leaves 44 B for fixes. Candidates if more room is needed: drop the
  `:active` scale (−15), the «Ошибка» tab (−30), the pause-all confirm (−55).
- Count-ups are gone; the big speeds just change (bars, sparkline, hue still animate/transition).
- Pager arrows are always drawn (`pg:-1` possible) — bot clamps.
- Stale detection compares the bot's `t` with the device clock; a skewed phone clock shows red.
- A tap during a pending `bg` tick is refused by the client (BUSY); the widget does not surface it as
  BUSY specifically («Ошибка связи») — rare (ticks last ~300 ms every 5 s).
- 5 tabs («Ошибка» present) wrap to a second line at 320 px (+32 px, still ≤ 560).
- `toLocaleTimeString('ru')` / `toLocaleDateString` rely on the interpreter having the `ru` locale; a
  fallback locale would only change the time format.
- Harness: `mock.json` carries `t = generation time + 1 h` so the stale meta stays green during tests;
  regenerate with `python3 -I tools/gen_console_mock.py` when it goes red.

## widget-rules.md audit (final file)
HTML: div/span/button/svg/symbol/use/path/polyline/i/b/br only. CSS: flex only (no grid), no `vh`,
no `position:fixed/sticky`, no opacity, no filters, no `:focus/:disabled/:has`, no nesting, no
`line-clamp`, `overflow:hidden` only, `hsl(h,s%,l%)` comma syntax, `@media (prefers-reduced-motion)`
disables transitions/animations, `transform:rotate/scale` 2D only, system font inherited. JS: ES2020
(`??`, spread), `closest/dataset/innerHTML/querySelectorAll`, one layout read per draw, `setInterval`
5.2 s stopped on `deactivated`, `request()` only in click handlers or the bg timer, no `focus()`, no
storage, `TelegramWidget` guarded (`W&&`), haptics in try/catch. No `[disabled]` attributes used.

## QA pass 2026-11-12

- Tab and page changes now slide the list (`.sl`, 200 ms, `translateX(±24px)`, the pager follows the tap
  direction, tabs always come from the right — the per-tab direction cost 37 B the budget did not have).
  Background ticks never animate (the class is set only after a `t:`/`pg:` answer).
- Overflow-proofing: the error view (`.ms`) gets `overflow-wrap:anywhere`; the accordion's detail line is
  clamped to 2 lines (`.ac .n{max-height:2.7em}`) — a 140-char line wrapped to 4 lines and pushed the
  open page to 570 px at 320 (now 529); the pager is one flex group (`.pg`, `margin-left:auto`) so «›» no
  longer wraps alone to a third line.
- Byte budget (4 052 → 4 089 with the slide): dropped `button:active` scale, `stroke-linecap:round`,
  `line-height` on `.nm`, the sparkline pre-fill (the line simply appears from the second sample), «Нет
  активных задач» → «Нет задач», `toLocaleTimeString('ru')` → `toTimeString().slice(0,8)`; sprite hidden
  with `.df{display:none}`.
- Caption below: `body{margin:12px 14px}` + the client's default bottom padding (0 with a caption below).
- `mock.json`: top-level `caption`; #6 «Стресс» (36-char names without spaces at the cap, `<`/`&` in a
  name, 5 tabs incl. «Ошибка», page 50 / 99, disk 99 % full, 60-char save path without digits, 140-char
  detail line); #7 «Стресс · ошибка без пробелов»; #4's `bg` rejection is delayed 1,8 s.
- Size: 8 310 B raw / **4 089 B** zlib-9. Heights at 320: 524 (6 rows, 5 tabs on 2 lines), 529 open.
