# PCM 3.1 · independent hardware-layer overlay framework

Draw your own popups — a volume OSD, toasts, dialogs — **on top of the stock PCM 3.1 UI**,
with **no flash write at all**, no ghosting, and physical isolation from the stock UI.

> **English** · [简体中文](HW_overlay_framework.zh-CN.md)

Verified on the bench 2026-07-20: full colour, anti-aliased text, true rounded-corner
transparency, the bar tracking the volume knob live, auto-dismiss.

![volume OSD drawn on our own hardware layer, over the stock Jukebox page](images/06-overlay-volume-osd.jpg)

*The stock page underneath keeps working normally — the progress bar advances, the clock
ticks, the highlighted button stays highlighted. That coexistence is the entire point.*

---

## 1. The idea

Become a **second QNX Graphics Framework (gf) client** and take an idle Carmine hardware
layer that `layermanager` never allocates, then draw on it independently.

Every hardware layer has its **own scanout buffer**, so writing to our layer can never
touch the stock UI's buffer or its locks. The contention, ghosting and dynamic-page
flicker that plagued the old "share the stock surface (cid 0x1f)" approach disappear at
the hardware level rather than being worked around.

**Key architectural decision: the layer covers only the popup rectangle — not the whole
screen.** A full-screen 800×480 "transparent" layer measured as a full-screen *blackout*.
Sizing the surface to the popup and positioning it with `gf_layer_set_dst_viewport` means
the hardware does not composite us at all outside that rectangle, which is a stronger
isolation guarantee.

---

## 2. Hardware facts you must know

Every one of these was paid for in debugging time. **Skipping this section costs you a day.**

### 2.1 ★★★ The driver inverts layer numbers: `hw_layer = 7 − gf_layer`

`devg-carmine.so` does `neg rN,r1 / add #7,r1` in three independent places
(`carmine_layer_query@0x12cec`, `set_surface@0x12f86`, `set_dest_viewport@0x13124`).

| gf layer | hardware layer | usable? |
|---|---|---|
| **5** | **L2** | ✅ **use this one** — a clean, general-purpose direct-colour layer |
| 6 | L1 | ❌ the video-capture / W layer. The generic driver path unconditionally writes into `L1EM bits1:0`, which the manual (p.412) marks **Reserved** on L1 → **the top 6 bits of every pixel die, so red never appears**, and the image is vertically truncated |
| 7 | L0 | ❌ inside `layermanager`'s remit; it gets disabled repeatedly, seen as periodic flicker |

> We burned an entire night on "layer 6" asking why only blue, green and black rendered.
> This inversion was the answer — we thought we were on L6; we were on L1.

### 2.2 The pixel format is RGBA5551, **not** ARGB1555

`gf_layer_query` reporting `0x1710` (ARGB1555) is **a lie**. The driver hardwires
`LnEC=0b10` for all 16bpp, which the manual defines (p.430) as
"Direct color (16 bits/pixel) **RGBA** mode". §7.3.2 gives the layout:

```
bit15..11 = R    bit10..6 = G    bit5..1 = B    bit0 = A
```

with output "shifted 3 bits toward the MSB side" — matching our measurements exactly
(5-bit 15 → 120, 31 → 248).

```c
#define RGBA5551(r,g,b) ((u16)((((r)&0x1f)<<11)|(((g)&0x1f)<<6)|(((b)&0x1f)<<1)|1u))
/* red 0xF801 · green 0x07C1 · blue 0x003F · white 0xFFFF · transparent 0x0000 */
```

`ui_core.c`'s `ui_rgb()` already emits exactly this layout, so the renderer needed no
changes at all.

### 2.3 What transparency you actually get

| capability | available? | notes |
|---|---|---|
| Per-pixel **on/off** transparency | ✅ **free** | With no `set_blending` call (default mode), writing `0x0000` is genuinely transparent and the stock UI shows through. Rounded corners, cut-outs, arbitrary shapes all work |
| Anti-aliasing / gradients **inside** the panel | ✅ **full 8-bit** | That is *software* blending against our own panel colour (`ui_core.c`'s coverage model), so the hardware limit does not apply. Text edges measured perfect |
| 8-bit **graded** translucency (soft shadows, fades) | ❌ extra work | Requires `GF_ALPHA_M1_MAP` (mode `0x00080102`) plus a separate `GF_FORMAT_BYTE` alpha-plane surface |

`gf_layer_set_blending` **accepts only 9 modes**:
`{0, 0x00040102, 0x00040201, 0x00080102, 0x00080201, 0x04000408, 0x04000804, 0x08000408, 0x08000804}`.
Passing `SRC_PIXEL_ALPHA` (`0x00010102`) falls through to an error path that returns 9 and
**never sends the message** — a silent no-op.

### 2.4 ★★★ Deadlock rule: every `gf_layer_update` needs a full re-assert first

```
gf_layer_set_surfaces → set_src_viewport → set_dst_viewport
  → gf_display_set_layer_order → gf_layer_enable → gf_layer_update
```

- **A bare `gf_layer_update` (no re-assert) blocks forever, REPLY-blocked on
  `gdcServerCarmine` (pid 4104)** — with no pending change it waits for a vsync that never
  arrives.
- **`gf_layer_disable` + `update` deadlocks the same way.** So an OSD **must not hide
  itself with `disable`**; fill the visible area with `0x0000` and push instead, leaving
  the layer enabled.
- In practice, funnel every screen update through a **single exit point `push_layer()`**
  and never bypass it.

Diagnosing: `pidin | grep <name>` and read the **Blocked column** — `REPLY 4104` means
stuck in gdc; `REPLY 3` means stuck on the serial driver `devc-sersci` (a different trap,
see §5).

### 2.5 Everything else

- **The dst viewport height formula is asymmetric**: in
  `carmine_layer_set_dest_viewport@0x13100`, width = `x2-x1+1` but height = **`y2-y1`
  (no +1)**. To get H rows you must pass `y2 = y1+H`.
- **`gf_layer_update`'s return code is hardcoded 0** (`8873c: mov #0,r1`) — it is
  **not** a success indicator.
- **The library never validates the pixel format.** `gf_layer_choose_format@0x88844` is a
  stub (`mov #0,r0; rts`). `gf_surface_create_layer` returning 0 with a plausible stride
  **only proves client-side arithmetic**, not hardware acceptance. Passing BGRA8888
  "succeeds" and then the hardware scans it out in its own mode, producing garbage.
- **There is no `libgf.so.1` on the device.** `/proc/boot/` only has
  `libgdcApiCarmine.so`, and that library has **no SONAME entry**, so the dynamic linker
  matches purely on the NEEDED filename. The stub's soname must therefore be
  `libgdcApiCarmine.so`, or the binary stops loading after a reboot.
- **No alignment requirement on the destination X**: the dst x path has no masking
  anywhere, so any pixel position is fine.
- 💣 **Never call `gf_layer_set_chroma`.** `carmine_layer_program@0x13640` packs the
  transparent colour as **ARGB1555** in the RGBA branch before writing LnETC, which
  disagrees with the RGBA5551 the hardware actually scans — the key value is guaranteed to
  be wrong.

---

## 3. Code layout

```
code/overlay/
  coexist_pop.c    the engine (layer setup, hot reload, live volume, show/hide)
  ui_core.c        shared renderer + ui.def parser — the same file the Mac previewer uses
  ui_font.h        offline-baked font (digits + proportional, incl. CJK)
  ui.def           popup description (geometry/colour/animation/binding) — plain text
  gf_defs.h        gf constants/structs/prototypes, rebuilt from the on-device library
  stub_libgf.c     link-time stub
  build.sh         freestanding SH4 build (no QNX SDK required)
```

**Engine / content split**: layout, colour and position live in a **348-byte `ui.def`**.
The engine hashes it once a second and re-parses on change — **no recompile, no 66 KB
binary transfer**. That is an order-of-magnitude difference in iteration speed.

### Writing a `ui.def` — one gotcha

**The panel's drawable height is `h - shadow`, not `h`** (`ui_render`: `ph = H - sh`), and
likewise its width is `w - shadow`. The shadow is carved out of the bottom and right edges.

So to centre an element of height `eh` vertically you want `(h - shadow - eh) / 2`, not
`(h - eh) / 2`. Getting this wrong pushes everything down by `shadow/2` — which is exactly
the bug we shipped first: with `h=76, shadow=12` the panel is only 64 tall, but the element
positions had been computed against 76, so the whole layout sat low and the progress bar
had roughly twice as much padding above it as below.

Note also that `ui_icon` centres on `y + 8`, so an icon's `icon_y` is its *top*, not its
centre.

### Render pipeline

```
ui_render()  →  ui_popbuf (RGBA5551 colour) + ui_cov (8-bit coverage)
             →  blit_layer():  cv>=128 ? (colour|1) : 0x0000
             →  push_layer():  full re-assert + update
```

The surface is created once at `UI_MAXW×UI_MAXH` (520×220) and then **cropped by the src
viewport and positioned by the dst viewport** — so changing the geometry in `ui.def`
never requires recreating the surface.

---

## 4. The live volume chain (V4)

The data source that makes the bar track the knob. **Taken verbatim from `coexist_vol.c`
v37, which was already proven on the bench — not one constant was changed.**

```
scan the heap for  u32 == 0x085c76fc
  and  u32@(X+0x160) == X-0x218
  and  u32@(X-0x218) == 0x085c4c5c        → this is V
P      = u32@(V+0x168)
ok     = u32@(P+0xc8) == 2                 (DATA_OK)
volume = u8@(P+0x7c)                       (0..40)   ★
src    = u32@(P+0x74) ∈ {34,35}            → ringtone, discard
```

Access is `open /proc/<PCM3Root pid>/as` + `lseek` + `read` — **read-only, and it never
touches an IPC/IOC channel** (that path once hung both the bench and the real car). Heap
scan range `0x0866e200 – 0x08a00000`, 64 KB at a time.

> ⚠️ **The `0x218` here is a structural validation offset, not the volume.**
> The discredited "0x218 = main volume" was an offset in a *persistence file* (it actually
> turned out to be the SMS notification tone, slot 9). Two namespaces, same number —
> **do not "helpfully correct" it.**

### Four guards in the value layer

| guard | why |
|---|---|
| **Debounce** (accept only after 2 identical reads) | when idle, the cache oscillates between 19 and 20 → without this the popup pops up on its own with nobody touching the knob |
| **Seed on boot** (record the first valid value without drawing) | otherwise boot, and every `ui.def` edit, triggers a popup |
| **Override-state logging** | a leftover `/tmp/uival` from testing **permanently shadows the live chain** with no indication whatsoever |
| **Re-locate on loss** (rescan only after 10 s of failed reads) | the threshold must exceed one incoming call, or a call wastes a full 3.57 MB heap scan |

> ⚠️ Do not copy `coexist_vol.c`'s rescan guard — **that code is dead**: `int vol=-1;` is
> never assigned, so `vol<0` is always true and it rescans the whole heap unconditionally.

---

## 5. Bench development loop (no USB stick)

```bash
# Build (the script aborts on `error:` instead of leaving a stale binary to fool you)
bash dev/build_coexist_vol.sh coexist-app/mvp/coexist_pop.c coexist-app/mvp/coexist_pop

# Push (~97 s for 66 KB), verifies cksum automatically
python3 code/serial/ser_push.py coexist-app/mvp/coexist_pop.stripped /tmp/coexist_pop 192

# Launch — all three redirections are mandatory (see below)
python3 code/serial/ser2.py 'chmod 755 /tmp/coexist_pop; /tmp/coexist_pop </dev/null >/dev/null 2>/dev/null &'

# Pull the log
python3 code/serial/ser_pull.py /tmp/pop.txt pop.txt

# Layout/colour only: push 348 bytes, no recompile
python3 code/serial/ser_push.py coexist-app/mvp/ui.def /tmp/ui.def 192

# Force a displayed value (to test rendering); delete the file to return to live volume
python3 code/serial/ser2.py 'echo 30 > /tmp/uival'
python3 code/serial/ser2.py 'rm -f /tmp/uival'
```

### Serial-specific traps

1. **Always launch with `</dev/null >/dev/null 2>/dev/null`.**
   If the serial cable is attached and nobody drains it, `devc-sersci` (pid 3) fills its
   buffer and **any process that writes to the console blocks on REPLY forever**,
   perfectly disguised as "some library call hangs". This one had us wrongly blaming
   `gf_dev_attach` and disassembling an entire MsgSend chain for hours.

2. **Always compare the cksum after a push** — a matching size does not mean matching
   content. `ser_push.py` had a latent bug: if a chunk's **first byte happened to be `-`**
   (printable, so passed through raw), the shell stripped the quotes and `print` took it
   as an option → `print: -\: unknown option` → the whole chunk vanished silently. Fixed
   by always escaping the first byte of every chunk.
   **Locating it**: implement POSIX cksum locally, then enumerate "what would the cksum be
   with chunk N removed" and match against what the device reported. That names the exact
   chunk in one pass — far faster than bisecting.

3. **The bench's `grep` does not support `|` alternation** (`grep -i "a\|b"` silently
   returns nothing). Use separate greps or `grep -E`. This one made us wrongly conclude
   the graphics stack had not started.

4. The bench has **no** `dd` / `cp` / `head` / `wc` / `touch`, and `slay` is interactive
   (`-f` does not suppress it) — use `ser_kill.py`, which answers the prompts.

---

## 6. Dead ends — do not retry

| approach | verdict |
|---|---|
| `layermanager.cfg`'s `reserveLayerForCid` | **Dead end.** The looked-up value is stored at `rec+0x2c` and **no code ever reads it again**. Flashed four times, no effect |
| `layerOrder` | Gated by chip type. Only applied when `graphicChip==1`; this unit is CARMINE16 (=8) → identity is forced |
| `lastAvailableLayer` set to `0-7` | **Blacks out the screen.** It is a *lower bound*, not an upper bound — the direction is inverted, so this leaves only slot 7 renderable |
| Sharing the stock surface 0x1f | Dynamic-page ghosting is structural; superseded by this framework |
| BGRA8888 / 32bpp | Not supported by the layer. It "succeeds" only because the library never validates |
| Guessing channel/byte order | One `0xFFFF` measurement settles it: an all-ones pixel must render saturated white under *any* bit arrangement |

---

## 7. References

- Chip manual: MB86297A (layer registers around p.430-432).
  ⚠️ **Not** the MB86296S CORAL-PA spec — that is the previous generation with only L0-L5.
- Disassembly recipe (both `.so` files have stripped section headers, so plain
  `objdump -d` produces nothing):
  ```bash
  docker run --rm -v "$PWD:/work" sh4gdb:latest \
    objdump -D -b binary -m sh4 -EL --start-address=0x... /work/<path>
  ```
