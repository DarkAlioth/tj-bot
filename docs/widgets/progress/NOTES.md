# progress — notes

## Size budget
`widget.html` 8 989 B raw / **4 054 B** zlib-9 (limit 4 096; the 3 900 aim was not reachable with
the full feature list — see "cut to fit"). Russian copy (state words, error map, labels) is most
of the incompressible part.

## Layout
- Hero: 84 px SVG ring (track + progress circle, `stroke-dasharray:214`, `stroke-dashoffset`
  transition 400 ms, rotated with the SVG `transform` attribute — no CSS transform-origin
  dependency) with the percent as SVG `<text>` (one decimal so 5 s ticks visibly move) or a check
  path when done; right: state label (accent / hint / destructive) + 2-line title. Thin 4 px bar
  under the hero.
- 2 × 4 stat tiles: ↓ speed (with a 12-point sparkline polyline, absolutely positioned top-right so
  the value never truncates at 320 px), ↑ speed, ETA, downloaded, seeds · peers, ratio, files,
  added. The total size moved from the downloaded tile to the state line («Загружается · 60,4 ГБ»)
  because «62,8 МБ / 51,1 ГБ» did not fit a 118 px tile.
- Actions: primary morphs (Пауза ↔ Возобновить ↔ Остановить/Раздавать ↔ Повторить проверку);
  «Форсировать» is a ghost button that turns accent when `fo` = 1; second row «Удалить» (inline
  two-step confirm with «Только из клиента» / «С файлами») and «Консоль».
- Footer meta line is itself the refresh control (`rf`), destructive + «нет связи» when stale.
- Height 494 px at 320 px for every scenario, 150 px for `gone`.

## Motion
- Ring/bar: CSS transitions; percent: rAF count-up (≈ 17 steps, 0 when reduced motion);
  completion: `pop` keyframes on the ring (scale 1.08, 600 ms) + haptic success, only on a
  transition observed by this widget instance (`f.pr > 0 && !was && done`), never on first draw.
- Pending: the tapped button shows «•••» blinking via `color` (not opacity → not a heavy element).

## Background ticks
`tick()` at script start, `setInterval(tick, 5500)` while active; `activated` → immediate tick +
restart, `deactivated` → clear. One request in flight at a time (`bgf` for bg, `busy` for taps;
a tap during a bg request gets the client's BUSY → «Подождите…» toast). `NOT_ALLOWED` stops the
timer (not a private chat); `RATE_LIMITED` is ignored; any other failure or `{"err"}` → stale
line without toast/haptics (no buzzing every 5 s).

## Cut to fit (what was removed from the first draft, in order)
1. Indeterminate ring animation for `metaDL` (the ring is empty with «—» now).
2. Count-up of the downloaded tile (only the percent counts up).
3. Three animated dots → blinking «•••» text.
4. Error detail text `e` under the meta line (key is reserved in the schema).
5. `STATE_VIEW` words for `allocating` and `moving` (raw state shown; rare).
6. «Консоль» in the `gone` view; theme-variable aliases (no gain under zlib).
Re-adding any of these needs the same number of bytes removed elsewhere.

## Verification
- Harness scenarios 0–8: downloading with 6 moving ticks (sparkline grows, ring/percent move),
  stalled, paused, magnet metaDL → metadata arrives on tick 2 (+ chatDataChanged), completed /
  seeding (stop ↔ seed), missingFiles (destructive, «Повторить проверку»), finishing → completed
  (celebration + haptic logged), bg FAILED → stale line, gone. Screenshots in `shots/`.
- Mock caveat: static answers cannot carry a current `t`, so answers omit it (the widget stamps
  receipt time) and `chatData` edits in the mock omit `t` too; the real bot sends `t`.
- Profile audit clean: 41 CSS rules, 2 keyframes, no opacity/filter/images/inputs/focus; all
  colors from `--tg-theme-*`.
