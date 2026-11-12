# dashboard — notes

## Result
- `widget.html`: 9 380 B raw, **4 072 B zlib-9** (limit 4 096; the 3 900 aim was not reachable with three tabs + the async check flow + the mandated error map, see “Size budget”).
- Height at 320 px: Состояние 473 px (with the enrichment tile), Статистика 466–480 px (8 top rows), Индексеры 440–457 px (2 errors expanded). All ≤ 560.
- Verified in Chromium via the harness (`shots/`): dark/light, 320/360/430, both tabs, bg ticks applied in place, check-now pending → chatDataChanged → «Проверено», chk error, q:<i> ok/err toasts, 45 s timeout path by code review (same `setChk(2)` branch as the screenshot of the retry button).

## Design decisions
- Three tabs are chips (design system); tab switch is local (no request, no chat_data edit) — `a` in chat_data only chooses the start tab for a re-created widget. Slide animation was dropped to fit the size budget (the content swaps instantly; `button:active` scale feedback remains).
- Состояние tiles: a 3-column row (Аптайм / БД / Диск+bar), qBittorrent tile (active/total, ↓↑ speeds, free-space bar, «недоступен» state), Индексеры tile (count + 7 dots, «проверено», tap → tab), Обогащение tile only when `h.sc` is present. The whole tab is re-rendered from a template on every `bg` answer (no entrance animation, bars have no transition so there is no flash); the count-up was dropped for size.
- Statistics: SVG bars (`preserveAspectRatio="none"`, viewBox 70×50) with HTML value/weekday rows above/below (SVG text would scale with the bubble width). Weekday labels come from `t` via `toLocaleDateString('ru',{weekday:'short'})`; today is bold. Counters are a two-line text under the chart (tiles cost 56 px more and pushed the 8-row list past 560 px). Top rows are buttons with a proportional CSS-gradient bar (`--p`), tap → `q:<i>`.
- Индексеры: rows with a dot, name, results; error text in one line (ellipsis), tap toggles `.x` (wrap). «Проверить сейчас» → disabled with the 3-dot pulse and «Проверяю… до 30 с»; cleared by `chatDataChanged` (r newer); 45 s → toast «Не дождался ответа» + «Повторить».
- Header right = «обновлено HH:MM» (from `t`), doubles as the toast line (success/error, 1.6/2.6 s) and as the manual refresh control; after 90 s without a successful tick it reads «обновить» in the destructive color.
- Freshness is tracked by the time of the last successful `bg` answer (not by `t`), so the harness with static mocks never shows stale, while a real bot that stops answering does.

## Tried / rejected
- In-place per-field updates with ids + count-up: ~400 B compressed more than the template rebuild; rejected.
- Horizontal slide on tab change (WAAPI and CSS-transition variants): −100 B when removed; rejected for size.
- Hue ramp (green→red) on capacity bars: −40 B; bars use `--tg-theme-button-color`.
- Jackett-down flag (`h.je`) and qBittorrent «не настроен» state: removed; the bot encodes Jackett-down as 7 error rows and not-configured as `q:null`.
- `localStorage` memory of the chosen tab: removed for size (the bot's `a` + the client re-creating the widget rarely).
- 4 KPI tiles on Статистика: replaced by a text line (height).

## Size budget (zlib-9 marginal costs)
Russian copy ≈ 600 B of the 4 072 (473 non-ASCII chars); CSS (44 rules) ≈ 1 050 B; the three renderers ≈ 850 B; ERR map + protocol/pending/toast plumbing ≈ 900 B; rest helpers. Any new feature must pay for itself by removing something.

## Known limitations
- Bubble width < 320 px: the three small tiles get tight («3 д 15 ч» ≈ 70 px) — values are single-line with ellipsis.
- The harness fires `chatDataChanged` 150 ms after an answer; the real 3-second gap of the check flow is emulated with `"delay":3000` on the `chk` answer (scenario 4). Scenario 5 keeps `pending` forever to exercise the 45 s path.
- `q:<i>` indexes into the `top` list the bot stored for this message; if the bot rebuilt `top` since, it must map by stored text, not by the current list.
- Mock timestamps are fixed (UTC on the server); the weekday row in the harness therefore shows the week ending 12.11.2026 regardless of the real date.

## Rules checklist (widget-rules.md)
- No form controls, no `:disabled`/`:focus`, no opacity/filter/box-shadow blur (0 heavy elements), no `vh`, no `:has`, no line-clamp, no nesting, no fonts, no `img`, SVG only `rect`/`symbol`-free inline, `el.animate` unused. 44 CSS rules, 1 `@keyframes`, ≤ ~130 nodes per tab.
- `request()` only inside the click handler (synchronous) or as a background tick in the private chat; one in flight at a time; ticks ≥ 5 s apart and stopped on `deactivated`.
- `touch-action` left at auto everywhere (no horizontal controls); controls start ≥ 14 px from the left edge, tiles are not gesture-claiming.
- Body padding keeps `env(safe-area-inset-bottom)` clear; the bot sends a caption anyway.

## QA pass 2026-11-12

- Tab switch animates: `#c` slides in 200 ms (`.an`, `translateX(24px)` → 0, restarted with one layout
  read); `bg` ticks still re-render in place without animation. Direction-following slide was tried and
  dropped for size (−45 B).
- Overflow-proofing: expanded indexer errors wrap with `overflow-wrap:anywhere`; the 7-day grid uses
  `repeat(7,minmax(0,1fr))` so 4-digit days cannot widen a column past the bubble.
- Reduced motion: `!important` rule.
- Byte budget (4 072 → 4 088): `toLocaleTimeString(…)` → `toTimeString().slice(0,5)`, `cursor:pointer`,
  `*{box-sizing}` (buttons are border-box by default), «Проверяю… до 30 с» → «Проверяю…», «Не дождался
  ответа» → ERR.TIMEOUT «Бот не ответил», `.nm` margin.
- `mock.json`: #7–#9 «Стресс» (100-day uptime, disk 98 % full, 3 indexers down with 40-char errors incl.
  one without spaces, 8 × 24-char top queries, 5-digit counters); #6's `bg` TIMEOUT is delayed 1,8 s.
- Size: 9 419 B raw / **4 088 B** zlib-9. Height ≤ 473 px at 320 in every tab.
