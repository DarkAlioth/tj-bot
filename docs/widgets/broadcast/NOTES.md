# broadcast — design notes

## Views

- **draft**: header «Рассылка» + chip «Черновик»; the text in a card exactly as it will be
  delivered (`white-space:pre-wrap`, selectable, clipped at 10 lines with a note); «Получат 23
  активных пользователя · ≈ 2 с (≈ 2 с на 30 человек)» with correct Russian plural/agreement
  (1 → «Получит 1 активный пользователь»); primary full-width «Отправить» → an inline confirm
  card «Отправить 23 пользователям?» [Да, отправить] [Нет]; ghost «Отмена».
- **run**: chip «Отправляется», the text collapsed to one line, big «12 из 23» (count-up),
  progress bar (400 ms width transition), «1 заблокировали бота» in destructive color,
  «Обновляется каждые 5 с». Updated in place (no DOM rebuild) on each `bg` answer.
- **done**: chip «Готово» (accent), «Доставлено 21 из 23», bar in accent, failures line,
  «Закрыть» → one-line summary (local, `localStorage`), tap to expand again.
- **cancel**: «Рассылка отменена» + the text muted in one line.

## Decisions

- **Confirm as a card with two full-width buttons** (first version was a one-line row; the
  question was truncated at 320–360 px).
- **Ghost buttons inside cards use the bubble background** (`.cd .g`) — otherwise «Нет» was
  invisible on the card's secondary background.
- **Polling**: `request('bg')` at script start when the state is `run` (the "one when it
  appears" request), 1.5 s after the `go` answer, then every 5 s while `isActive`; stopped on
  `deactivated` and whenever `st != run`. `RATE_LIMITED`/`TIMEOUT` on a tick are silent — the
  next tick is scheduled. Ticks never touch `chat_data`; `chatDataChanged` (draft→run→done)
  applies by revision.
- **Count-up** uses `requestAnimationFrame` with ≤ 20 steps; reduced motion → immediate.
- **Haptics**: `impactOccurred('light')` on confirm toggle / cancel / close, `notificationOccurred`
  success/warning on completion (warning when some failed), error on request errors.
- The text is rendered with `textContent`/escaped HTML — bot text is never injected as markup.

## Size budget

`widget.html`: 7 827 bytes raw, **3 312 bytes** zlib-9. CSS rules: 29. Keyframes: 1. Heavy
elements: 0. Layout reads per draw: 2 (`scrollHeight`/`clientHeight` of the preview, draft only).

chat_data: 416–420 bytes for the sample text; 832 bytes for the 420-char edge case; the bot
cuts `tx` to keep ≤ 1000 bytes (PROTOCOL.md).

## Heights (harness)

draft 418 px (360 px) / 418 px (320 px) → confirm 470 px; long-text edge 493 → 545 px with
the confirm; run 232 px; done 262 px; cancel 173 px; collapsed 60 px. Nothing above 560.

## Limitations / open points

- The widget cannot edit the text: retyping `/broadcast …` creates a new draft (BRIEF §4).
- Failure reasons are not split (blocked vs chat not found): the broadcaster logs them but
  returns only a bool.
- A finished broadcast still shows «Обновляется каждые 5 с» until the first tick after a
  recreate — at most 5 s.
- `localStorage` per message/device: «Закрыть» is remembered on this device only.

## Checklist against widget-rules.md

- Elements: div, b, span, button, i only; no forms, images, SVG, iframes, external resources.
- CSS: flex, grid-free, transitions on `width`/`transform`, one `@keyframes`, `max()` with the
  safe-area variable, `pre-wrap`, `overflow-wrap:anywhere`, `user-select:text` for the preview;
  no `vh`, sticky, line-clamp, `:has`, nesting, `!important`, opacity, filters, text-shadow.
- JS: ES2023, `request` only in the click handler or in the background timer; one request at a
  time (`busy`); `localStorage` in try/catch; `TelegramWidget` guarded; `ready()` after the
  first draw; `activated`/`deactivated` honoured.
- Nothing claims `touch-action`; controls start 14 px from the left edge.

## Files

- `widget.html`, `mock.json`, `PROTOCOL.md`, `NOTES.md` — deliverables.
- `gen/mock_gen.py` — regenerates `mock.json` and prints chat_data sizes (`python3 -I gen/mock_gen.py`).
- `gen/shot.js` — Playwright screenshot runner used for the harness checks.
- `preview/*.png` — harness screenshots (dark/light, 320–430 px).
