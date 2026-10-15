# Browser tests

End-to-end tests that send **real mouse, pen and keyboard input** to headless Microsoft Edge, against an **isolated** Asset Lab
(fake game, temp workspace, temp `ASSET_LAB_HOME`, empty `ASSET_LAB_RUNTIME`), and check **pixels**. They never touch the
live lab on port 8753, the real game or Steam.

## Run

From the repo root (PowerShell):

```powershell
$py = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$env:PYTHONPATH = 'src;tests'
& $py -B -X utf8 -m unittest discover -s tests/browser -v                          # every browser test
& $py -B -X utf8 -m unittest discover -s tests/browser -p test_harness_smoke.py -v  # one file
& $py -B -X utf8 -m unittest discover -s tests/browser -k brush -v                 # names containing "brush"
```

The main suite (`discover -s tests`) skips this folder on purpose: there is no `__init__.py` here, so do not add one.
Each test class starts its own lab server and Edge (about 2.5 s). The smoke tests take about 8 s in total.

## Write a test

Put it in `tests/browser/test_*.py`:

```python
import numpy as np
from harness import EditorTestCase, distance_to_path, rgba

class BrushTests(EditorTestCase):            # one lab server + Edge for the class, lab page already open
    def test_brush_paints_its_color(self):
        b = self.browser
        b.select_asset('edit-sprite.png')         # like a click on its card; waits until it is drawn
        b.click_selector('[data-tool="brush"]')   # a real mouse click on the button
        b.set_value('#paintColor', '#ff00ff')
        b.set_value('#brushSize', 6)
        before = b.editor_pixels()                # uint8 (H, W, 4)
        path = [(9.5, 9.5), (22.5, 9.5)]          # image coordinates
        b.drag_image(path, center=False)          # a real mouse drag along the path
        after = b.editor_pixels()
        d = distance_to_path(64, 64, path)
        self.assertPixelsClose(after, np.broadcast_to(rgba('#ff00ff'), after.shape), mask=d <= 2)
        self.assertPixelsClose(after, before, mask=d >= 4)
        b.key('Ctrl+Z')
        b.wait_for("S.ops.at(-1)?.tool === 'undo'")   # app.js's own "undo finished" signal
        self.assertPixelsClose(b.editor_pixels(), before)
```

Tests in a class share one page and run in name order, so each test opens its asset and sets the tool and options it
needs. After every test the harness releases held buttons and keys, and fails the test if the page logged errors during it.
`test_harness_smoke.py` shows every helper in use.

## The fake game (`fakegame.py`)

`make_editor_game(root)` is `tests/fixtures.make_game` plus the theme atlas and `assets.zip` of `test_theme.stale_game`,
plus four loose PNGs. `image_array(name)` returns the exact pixels written; the dicts name where each shape is.

| File | Size | Content | Coordinates |
|---|---|---|---|
| `edit-photo.png` | 256 x 256, opaque | R = x, G = y, B fades from the center; white square, aliased black disk, red triangle, gray bar, 1 px checker, noise | `PHOTO` |
| `edit-sprite.png` | 64 x 64, transparent | flat blue square (40, 120, 220), one-color red disk with an anti-aliased edge, half-transparent green bar | `SPRITE` |
| `edit-big.png` | 2048 x 2048, opaque | gradients, white 64 x 64 squares every 256 px | `BIG` |
| `edit-tiny.png` | 16 x 16 pixel art | heart, yellow and green blocks; alpha only 0 or 255 | `TINY_ROWS`, `TINY_PALETTE` |

## API (`harness.py`)

**`LabServer(maker=make_editor_game, keep=KEEP, timeout=60)`** starts `src/lab_server.py` on a free port (never 8753).
`.url .port .game .workspace .ledgers .home .temp .log` · `get(path, **query)` · `post(path, data)` (with the session token;
safe, it only touches the fake game) · `assets(q='', **params)` · `asset_key(name)` · `asset_path(name)` · `stop()` · usable with `with`.

**`Browser(width=1600, height=1000, keep=KEEP, gpu=GPU)`**: headless Edge with one page.

| Area | Methods |
|---|---|
| JavaScript | `eval(js, timeout=30)` console-style: statements, `await`, last value returned as JSON, promises awaited, `JSError` on throw · `wait_for(js, timeout=10, interval=0.025, message='')` returns the truthy value; errors while polling count as "not yet" · `settle(frames=2)` · `call(method, **params)` raw DevTools |
| Pages | `open_lab(server_or_url)` · `reload()` · `goto(url)` · `select_asset(name, discard=True)` returns the key · `set_viewport(w, h)` · `screenshot(path=None, clip=None)` returns the path (relative paths go to `output/`) · `screenshot_array(clip=None)` |
| Coordinates | `image_to_client(x, y)` · `image_points_to_client(points)` · `pixel_center(x, y)` = image (x + 0.5, y + 0.5) · `client_to_image(x, y)` |
| Mouse and pen | `mouse_move(x, y, modifiers=0, pointer='mouse', pressure=None, tilt=None)` · `mouse_down` / `mouse_up(x, y, button='left', modifiers=0, click_count=1, pointer='mouse', pressure=None, tilt=None)` · `click(x, y, button='left', modifiers=0, count=1)` · `double_click(x, y)` · `wheel(x, y, delta_y, delta_x=0)` · `drag(points, button='left', modifiers=0, steps_between=4, pointer='mouse', pressures=None, tilt=None, burst=False)` · `drag_image(points, center=True, **drag_args)`. `button` is `'left'`, `'right'`, `'middle'`, `'back'` or `'forward'`; `tilt=(x_degrees, y_degrees)`; `burst=True` sends the moves without waiting, like a fast tablet, so Chrome merges them (`getCoalescedEvents()`) |
| Page elements | `click_selector(css)` (fails if something covers it) · `center_of(css)` · `set_value(css, value)` (True/False for checkboxes) · `text(css)` · `set_files(css, paths)` picks files in an `<input type="file">` (e.g. `'#importFile'`) |
| Keyboard | `key(spec, modifiers=0)`: `'e'`, `'E'` (= Shift+e), `'7'`, `'Enter'`, `'Escape'`, `'Delete'`, `'Backspace'`, `'ArrowLeft'`, `'['`, `']'`, `'{'`, `'F2'`, `' '` or `'Space'`, `'Ctrl+Z'`, `'Ctrl+Shift+Z'`, `'Alt+Backspace'` · `key_down(spec, modifiers=0, repeat=0)` · `key_up(spec)` · `with hold(*specs):` · `type_text(text)` · bits `ALT=1 CTRL=2 META=4 SHIFT=8` |
| Pixels | `pixels(js_expression, region=None)` reads a canvas, OffscreenCanvas, ImageData, image, ImageBitmap or `{width, height, data}` into uint8 (H, W, 4) ((H, W) for one byte per pixel) · `editor_pixels(target='composite', region=None)` · `wait_for_pixels(predicate, target='composite', timeout=10)` · `push_pixels(array)` returns a JS expression naming an ImageData in the page · `load_editor_image(array)` |
| Errors | `errors` (dicts: `kind` console / exception / log / network / crash, `text`, `url`, `line`, `seq`) · `errors_since(seq)` · `seq` · `pump()` (collect events that already arrived) · `clear_errors()` · `error_report()` · `dialogs` · `accept_dialogs` (alert/confirm/prompt are dismissed unless True; "leave page?" is always accepted) |

**Functions**: `assert_pixels_close(actual, expected, max_abs=0, mean_abs=None, mask=None, msg='', diff_path=None)` · `psnr(a, b)` ·
`save_diff(a, b, path)` (actual | expected | difference PNG in `output/`) · `distance_to_path(w, h, points)` · `premultiplied(a)` ·
`rgba('#rrggbb')` · `rgba_b64(a)` / `from_b64(b64, w, h)` · `read_png(path)` / `write_png(a, path)` · `output_path(name)` · `parse_keys(spec)`.

**`EditorTestCase`**: `self.server`, `self.browser` · `assertNoBrowserErrors(allow=(), everything=False)` (errors during this
test; `everything=True` includes the page load) · `assertPixelsClose(...)` · `original(name)` (pixels of a fake game file).
Class settings: `viewport = (1600, 1000)`, `game_maker`, `open_on_start = True`, `fresh_page = False` (reload before each test),
`check_errors = True`, `allowed_errors = ()`.

## Hooks for the new editor core

The harness uses these when the page defines them, and today's `app.js` (`S.draft`, `transform`, `#view`) otherwise:

- `Ed.view.toClient(x, y)` returns `{x, y}` in page CSS pixels for image point (x, y); `Ed.view.toImage(x, y)` is the inverse.
- `Ed.test.pixels(target)` returns a canvas, ImageData or `{width, height, data}` (typed array or base64), or a promise of one.
  `target` is `'composite'`, a layer index or `'selection'` (a mask with one byte per pixel comes back as (H, W)).
- `Ed.test.load(width, height, base64Rgba)` replaces the document (may return a promise).
- `Ed.test.ready()` (optional) resolves once an opened asset is fully shown; `select_asset` awaits it.

## Gotchas

- **Coordinates.** Input methods take page (client) CSS pixels; `*_image` methods take image pixels. Integer image
  coordinates are pixel corners: pixel (x, y) covers x to x + 1, so its center is x + 0.5 (`pixel_center`, `drag_image` with
  `center=True`). Compute positions right before the input, because zoom and pan move the image.
- **Input lands on whatever is on top**: a toast, the context menu, or an open `<dialog>` (which makes the rest of the page
  ignore the mouse). `click_selector` checks what it hits; drags do not.
- **Focus.** Keys go to the focused element. A clicked toolbar button keeps focus (Space would press it, as in a real
  browser). app.js ignores shortcuts while a text field has focus. Clicking the canvas moves focus to the page.
- **Modifiers.** `modifiers=SHIFT` on a mouse call only sets `shiftKey` on those events. To also press the key (for code
  that watches keydown/keyup, like "hold Alt for the eyedropper"), use `with b.hold('Alt'): ...`; held keys apply to every
  mouse and key event inside the block.
- **Clipboard.** Ctrl+C / Ctrl+X / Ctrl+V fire real copy, cut and paste events. Headless Edge keeps its own clipboard, so
  tests never touch the Windows clipboard.
- **Pen.** `pointer='pen'` gives PointerEvents with `pointerType 'pen'`, `pressure` as given (float32, so compare with
  `assertAlmostEqual`), `buttons 1` while touching, and pressure 0 when hovering or lifted. A pressed mouse reports 0.5.
  Each move you send arrives as its own `pointermove` with fractional `clientX`/`clientY`, unless `burst=True`: then
  Chrome merges quick moves and the samples are in `getCoalescedEvents()`, as with a real fast pen.
- **Timing.** Each input call returns after the page handled the event, but async work (image decoding, fetches, the
  requestAnimationFrame render) finishes later. Wait for a real completion signal (`wait_for`, `wait_for_pixels`), not an
  early flag: app.js updates its undo stacks before the undo image is drawn back. `settle()` waits two frames. Do not sleep.
- **Pixels.** Canvas pixels are un-premultiplied, so half-transparent pixels may differ from the PNG file by rounding: take
  "before" pixels from the canvas, or compare `premultiplied(...)`. `pixels()` takes an expression, not statements.
- **eval.** Declarations stay local to the call; wrap object literals in parentheses; DOM nodes come back as `{}`.
- **Rendering.** Edge draws with SwiftShader (a software GPU), so results do not depend on the graphics card or its
  driver. `ASSET_LAB_TEST_GPU=1` uses the real graphics card. The device scale factor is 1.
- **Debugging.** `ASSET_LAB_TEST_KEEP=1` keeps the temp lab folder and the Edge profile (paths printed) and saves a
  screenshot of every failure into `tests/browser/output/` (git-ignored). Failed pixel checks always write a diff PNG there.
- **Safety and cleanup.** `goto` refuses port 8753. Edge and the server join a Windows job object tied to the test
  process, so even a killed run leaves no Edge or server behind. Set `ASSET_LAB_EDGE` if msedge.exe is elsewhere; without
  Edge the tests are skipped.
