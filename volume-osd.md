# PCM 3.1 · Volume OSD — built on an independent hardware-layer overlay framework

Draw your own popups — a volume OSD, toasts, dialogs — **on top of the stock PCM 3.1 UI**,
with **no flash write at all**, no ghosting, and physical isolation from the stock UI.

> **English** · [简体中文](volume-osd.zh-CN.md)

Verified on the bench 2026-07-20: full colour, anti-aliased text, true rounded-corner
transparency, the bar tracking the volume knob live, auto-dismiss.

![volume OSD drawn on our own hardware layer, over the stock Jukebox page](images/06-overlay-volume-osd.jpg)

*The stock page underneath keeps working normally — the progress bar advances, the clock
ticks, the highlighted button stays highlighted. That coexistence is the entire point.*

| Part | What is in it |
|---|---|
| [Part I — How it works](#part-i--how-it-works) | §1 the idea · §2 the hardware facts · §3 the live volume chain |
| [Part II — The solution](#part-ii--the-solution) | §4 layer arbitration · §5 code layout · §6 the bench loop · §7 screenshot verification |
| [Part III — Problems and dead ends](#part-iii--problems-and-dead-ends) | §8 index of the inline traps · §9 dead ends |
| [Appendix](#appendix) | References |

---

## Part I — How it works

The background the rest of this document assumes: the approach, the hardware facts it rests
on, and where the displayed volume value comes from. Traps are kept beside the recipe they
qualify rather than moved out of it; §8 indexes them.

### 1. The idea

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

### 2. Hardware facts you must know

Every one of these was paid for in debugging time. **Skipping this section costs you a day.**

#### 2.1 ★★★ The driver inverts layer numbers: `hw_layer = 7 − gf_layer`

`devg-carmine.so` does `neg rN,r1 / add #7,r1` in three independent places
(`carmine_layer_query@0x12cec`, `set_surface@0x12f86`, `set_dest_viewport@0x13124`).

| gf layer | hardware layer | usable? |
|---|---|---|
| **5** | **L2** | ✅ **use this one** — a clean, general-purpose direct-colour layer |
| 6 | L1 | ❌ the video-capture / W layer. The generic driver path unconditionally writes into `L1EM bits1:0`, which the manual (p.412) marks **Reserved** on L1 → **the top 6 bits of every pixel die, so red never appears**, and the image is vertically truncated |
| 7 | L0 | ❌ inside `layermanager`'s remit; it gets disabled repeatedly, seen as periodic flicker |

> We burned an entire night on "layer 6" asking why only blue, green and black rendered.
> This inversion was the answer — we thought we were on L6; we were on L1.

#### 2.2 The pixel format is RGBA5551, **not** ARGB1555

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

#### 2.3 What transparency you actually get

| capability | available? | notes |
|---|---|---|
| Per-pixel **on/off** transparency | ✅ **free** | With no `set_blending` call (default mode), writing `0x0000` is genuinely transparent and the stock UI shows through. Rounded corners, cut-outs, arbitrary shapes all work |
| Anti-aliasing / gradients **inside** the panel | ✅ **full 8-bit** | That is *software* blending against our own panel colour (`ui_core.c`'s coverage model), so the hardware limit does not apply. Text edges measured perfect |
| 8-bit **graded** translucency (translucent panel, soft shadows, soft rounded corners) | ✅ **works** | Via `GF_ALPHA_M1_MAP` (mode `0x00080102`) plus a separate `GF_FORMAT_BYTE` alpha-plane surface. **Recipe and its one fatal trap in §2.3.1** |

`gf_layer_set_blending` **accepts only 9 modes**:
`{0, 0x00040102, 0x00040201, 0x00080102, 0x00080201, 0x04000408, 0x04000804, 0x08000408, 0x08000804}`.
Passing `SRC_PIXEL_ALPHA` (`0x00010102`) falls through to an error path that returns 9 and
**never sends the message** — a silent no-op. Note that `0x00080102` (M1_MAP) **is** on the
whitelist, which is why the alpha-plane route below works.

#### 2.3.1 ★★★ The alpha plane: recipe, and the trap that costs you a day

> ⛔ **Read Trap 3 before you use any of this in a car.** Allocating an alpha plane is
> permanent and there are only four of them; on a stock 911 taking one disables the OEM
> parking-distance display for the rest of the ignition cycle. Ship `panel_alpha = 255`.

This gets you a translucent panel with **opaque** text on it, anti-aliased rounded corners
and a real drop shadow — none of which the 1-bit path can express.

```c
/* 1. main surface, then read back its PIXEL pitch */
gf_surface_create_layer(&surf, &layer, 1, 0, W, H, 0x1710, 0, 0);
gf_surface_get_info(surf, &si);
int pitch = si.stride / 2;              /* pixels, not bytes */

/* 2. alpha plane — width MUST be the main surface's pixel pitch */
gf_surface_create(&asurf, dev, pitch, H + 1, GF_FORMAT_BYTE /* =8 */, 0, 0);
gf_surface_get_info(asurf, &ai);        /* ai.stride == pitch, 1 byte per pixel */

/* 3. bind */
gf_alpha_t al = {0};
al.mode = 0x00080102;                   /* M1_MAP | BLEND_SRC_M1 | BLEND_DST_1mM1 */
al.map  = asurf;  al.m1 = 255;  al.m2 = 255;
gf_layer_set_blending(layer, &al);
```

**🚨 Trap 1 — the alpha plane's byte stride must be 64-byte aligned.**
The driver pads an RGB surface's *byte* stride to a multiple of 64 on your behalf, but the
alpha plane is 1 byte per pixel, so building it at width = pitch makes its stride *equal*
to the pitch — and nothing pads it. A pitch that is not a multiple of 64 shears the alpha
fetch one row at a time and you get **diagonal banding across the whole panel**.

Measured with a 2×2 factorial (pitch × viewport), each cell judged by eye on the bench:

| main surface | pixel pitch | alpha stride | src viewport | result |
|---|---|---|---|---|
| 500×200 | **512** (=8×64) | 512 | full | **clean** |
| 500×200 | **512** | 512 | sub-rect 372×76 | **clean** |
| 520×220 | **544** (=8.5×64) | 544 | full | **banded** |
| 520×220 | 544 | 372 | sub-rect | banded |

So a **sub-rectangle source viewport is fine** — alignment is the whole story. Choose a
surface width whose pitch lands on a multiple of 64 (512 and 768 both do; 520 and 600 do
not). Fill the padding columns (`x >= W`) of the alpha plane with 0.

**🚨 Trap 2 — re-assert the blending on every push.**
`gf_layer_set_surfaces` drops the blending/alpha-map binding. Bind once at startup and the
hardware will be reading a stale alpha pointer by the next frame — same banding symptom,
different cause. The order inside `push_layer()` must be:

```
set_surfaces → set_blending → set_src_viewport → set_dst_viewport
  → set_layer_order → enable → update
```

**Who decides each pixel's alpha:** have the *renderer* emit it, one byte per pixel
(`ui_core.c`'s `ui_al[]`). Do **not** try to infer it from colour (e.g. "this pixel equals
the panel colour, so it is background") — the moment you add a gradient, most panel pixels
no longer equal the panel colour and the whole background turns opaque.

**🚨🚨🚨 Trap 3 — allocating an alpha plane is permanent, and there are only four.**

This one does not cost you a day. It costs the driver their parking sensors.

`gdcServerCarmine` hands out alpha blending planes from a pool of four (LA0..LA3). On a
stock car the OEM already holds **three** of them at boot, so exactly one is free. And the
release path is **unreachable**: the allocator hands out indices `8..11`, while the guard on
the release branch is `cmp/hi #3`. The two ranges do not intersect, so a plane that is
issued is never returned until the unit is powered off.

So the first time your popup draws with `panel_alpha < 255`, it takes the last free plane
and holds it for the whole ignition cycle. Engage reverse after that and the OEM
parking-distance display cannot get a plane: it draws its proximity zones with the correct
kidney geometry and soft edges, filled **solid black**. Do it in the other order — radar
first, popup second — and everything is fine all cycle, because the OEM got there first.
That order-dependence is the signature; if you ever see it, suspect a one-shot resource.

Measured on the bench by reading the server's own allocation table out of `/proc/<pid>/as`
(`ledger[k] = *(u32*)(0x080dbd04 + disp*0x0e24 + 56*k + 88)`; `k = 0..7` are the hardware
layers, `k = 8..11` are LA0..LA3, and a value `> 11` means free):

| | LA0 | LA1 | LA2 | LA3 |
|---|---|---|---|---|
| at boot | layer 0 | layer 4 | layer 5 | **free** |
| after two draws at `panel_alpha = 255` | layer 0 | layer 4 | layer 5 | **free** |
| after one draw at `panel_alpha = 240` | layer 0 | layer 4 | layer 5 | **layer 6 — ours** |

The third row was read *after* the popup had already hidden, and after the code had issued
an all-zero `gf_layer_set_blending` to unbind. The plane does not come back. (That all-zero
call is harmless in the other direction: `mode = 0` makes the client clear the byte the
server's first gate tests, so an unbind never allocates. Only the `M1_MAP` modes do.)

⇒ **Ship `panel_alpha = 255` in any unit fitted to a car.** You lose the translucent panel
and the anti-aliased corners; you keep the parking sensors. Our USB bundle builder refuses
to package a `ui.def` that says anything else, and that guard has been tested by
deliberately feeding it a bad value.

The probe is `code/common/sh4tools/alphatab.c` — read-only, ~4.8 KB, prints the whole table plus a
`SANITY=OK/BAD` line that catches a wrong base address instead of quietly returning noise.

#### 2.4 ★★★ Deadlock rule: every `gf_layer_update` needs a full re-assert first

```
gf_layer_set_surfaces → set_src_viewport → set_dst_viewport
  → gf_display_set_layer_order → gf_layer_enable → gf_layer_update
```

- **A bare `gf_layer_update` (no re-assert) blocks forever, REPLY-blocked on
  `gdcServerCarmine` (pid 4104)** — with no pending change it waits for a vsync that never
  arrives.
- In practice, funnel every screen update through a **single exit point `push_layer()`**
  and never bypass it.

> #### ⚠️ Correction (2026-08-02/04) — earlier revisions of this document were wrong here
>
> This section used to say *"`gf_layer_disable` + `update` deadlocks the same way, so an
> OSD must not hide itself with `disable`; fill the area with `0x0000` instead"*. Both
> halves are wrong, and the wrong advice caused real bugs on the car:
>
> - **`disable` + `update` does not deadlock.** The deadlock only happens when there is
>   *no dirty state* — and `disable` sets a dirty bit, so it is always answered. The old
>   claim was extrapolated from the bare-`update` case, never measured.
> - **`disable` + `update` is the *only* thing that truly hides the layer.** Measured by
>   sampling the gdc enable bitfield at `0x080dbd1c`, bit `16+hw_layer`:
>   `gf_layer_detach` → bit unchanged (**it is a no-op**, 58 bytes, zero `jsr`);
>   `disable` alone → bit unchanged (registered, not committed);
>   `disable` + `update` → **bit clears**.
> - **Hiding by writing `0x0000` is not hiding.** It relies on the blending configuration
>   staying put. Any other gf client touching display-wide state (e.g.
>   `gf_display_set_layer_order`) resets it, and those "transparent" pixels are then
>   scanned out as an **opaque black rectangle** — which is exactly what the car showed.
> - **Process death does not release the layer either.** After `kill -9` the enable bit
>   stays 1 forever; the popup freezes on screen until something explicitly clears it.
>   Install/update tooling must therefore assume a predecessor left a layer enabled and
>   purge it (see §4.5).

Diagnosing: `pidin | grep <name>` and read the **Blocked column** — `REPLY 4104` means
stuck in gdc; `REPLY 3` means stuck on the serial driver `devc-sersci` (a different trap,
see §6).

#### 2.5 Everything else

- **The dst viewport is symmetric after all: width *and* height are `x2-x1+1`.**
  ⚠️ Earlier revisions claimed height was `y2-y1` with no `+1`. Measured 2026-08-04 by
  capturing a real screenshot with the popup hidden and with it shown and diffing the two:
  the changed region's bounding box came out **372 × 77** where `ui.def` asked for
  **372 × 76**. Width matched exactly, height was one row over — so passing `y2 = y1+H`
  renders `H+1` rows. Pass `y2 = y1+H-1`. (That stray row was visible on the car as a
  ~1 px overhang along the bottom edge of the panel.)
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

### 3. The live volume chain (V4)

The data source that makes the bar track the knob. **Taken verbatim from `coexist_vol.c`
v37, which was already proven on the bench — not one constant was changed.**

> `coexist_vol.c` was the standalone volume-OSD predecessor; it is **not shipped in this
> repo** — `code/volume-osd/coexist_pop.c` supersedes it. It is named here only to record
> where these constants came from and that they were not re-derived.

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

#### Four guards in the value layer

| guard | why |
|---|---|
| **Debounce** (accept only after 2 identical reads) | when idle, the cache oscillates between 19 and 20 → without this the popup pops up on its own with nobody touching the knob |
| **Seed on boot** (record the first valid value without drawing) | otherwise boot, and every `ui.def` edit, triggers a popup |
| **Override-state logging** | a leftover `/tmp/uival` from testing **permanently shadows the live chain** with no indication whatsoever |
| **Re-locate on loss** (rescan only after 10 s of failed reads) | the threshold must exceed one incoming call, or a call wastes a full 3.57 MB heap scan |

> ⚠️ Do not copy `coexist_vol.c`'s rescan guard — **that code is dead**: `int vol=-1;` is
> never assigned, so `vol<0` is always true and it rescans the whole heap unconditionally.

---

## Part II — The solution

What was built on those facts: how the layer is *kept* rather than merely acquired, how the
popup is described and rendered, and how a change reaches the bench and is verified there.

### 4. ★★★★★ Layer arbitration — the part that actually decides whether this ships

Everything above is about *drawing*. This section is about *keeping the layer*, and it is
where every real-car failure of this framework has come from.

#### 4.1 There is no ownership. The layer state is last-writer-wins.

`gf_layer_attach(..., GF_LAYER_ATTACH_PASSIVE)` **succeeds for two clients at once**. The
per-layer state the hardware scans out — pixel format, pitch, height, base address,
viewports, enable bit — lives in one shared shadow record inside gdc's
`/gdc_shm_inform`, and whoever writes last wins. There is no arbitration and no error.

Record address: `base + 0xe28 + display*0x5a0 + hw_layer*120`, fields at
`+0` status, `+4` bytes/pixel, `+8` busy, `+12` pitch (pixels), `+16` height,
`+20`/`+24` physical address (low 28 bits; real address is `0xd0000000 | value`).
`shm_open("/gdc_shm_inform", O_RDONLY)` works from any process — the server creates it
`0777` — so **you can watch your own layer's record and tell, per frame, whether you still
own it.** That read-only check is the foundation of everything below.

#### 4.2 The failure mode this produces

When another client reprograms the record while your layer is enabled, the hardware keeps
scanning **your** memory with **their** pitch/format. A 544-pixel-wide buffer read as
800 shears into a diagonal noise band; a cleared buffer read as opaque becomes a black
rectangle. On the car this looked like "the volume popup breaks after the parking radar
appears" and took a long time to pin down because the popup's *geometry* stayed correct.

Reproduced on the bench with nothing but stock code paths: run the engine, then start a
second gf client that attaches any layer. Log:

```
[watch] calibrated on d0 L2  bpp=2 pitch=544 h=220     <- ours
[watch] displaced  code=2  bpp=2 pitch=800 h=480       <- someone else's, same numbers
                                                          the car showed
```

#### 4.3 ❌ Dead end: "pick a layer nobody uses"

The obvious fix is to find an idle layer. **It does not generalise, because the layer map
differs per vehicle model.**

A 221-second census on the bench (a Panamera head unit, heavy UI operation throughout)
said hardware L6 was the only RGB layer with *zero* changes and *zero* occupancy — a
perfect candidate. Shipped it to the car (a 911) and the parking-radar zones went black
and the car model vanished, because **on the 911, hardware L6 is the layer the stock
firmware draws the PDC radar zones and the car model on.** The bench simply has no
ParkAssist, so it could never have shown this.

The deeper trap: the damage was not occlusion. **It was our `disable`.** Our idle state
called `gf_layer_disable` on every popup hide; the stock lit the layer once when PDC
started and we switched it off again — which is why the radar zones were black *even when
no popup was showing*.

> **Rule: never disable a hardware layer you do not exclusively own — and you never
> exclusively own one.**

#### 4.4 ✅ The answer: a yield protocol (borrow the OEM's own priority model)

Instead of asking "which layer is free", mirror what the stock stack already does: **when
a higher-priority feature activates, lower-priority ones suspend.**

| state | behaviour |
|---|---|
| normal | poll our shadow record **every tick** — including while idle |
| record displaced | **stop touching the layer, immediately** |
| yielded | no `enable`, no `disable`, no `update`, no `set_surfaces`, no re-assert. The stock owns it completely |
| record idle again for 2 s | resume automatically (the debounce stops us fighting the stock frame by frame) |

"Idle" = the record's base address is back to the unused placeholder `0x00100000`, or is
still our own surface.

**🚨 The yield action must be *nothing*. Do not "hide cleanly" first.**
The tempting version is `go_dark()` (disable + update) and *then* stop touching. That
reintroduces the exact bug: if the stock lights the layer once at feature start and our
yield happens just after, our final `disable` switches it off and we have promised never
to touch it again — so it stays off forever. By the time displacement is detected the
record already belongs to the stock and the screen is already showing *their* content, so
simply ceasing to commit is both sufficient and safe.

Result on the car: PDC zones and the reversing camera behave exactly like stock; the popup
disappears while the radar owns the layer and comes back on its own afterwards. **This
also removes the need to know any vehicle's layer map.**

#### 4.5 Startup must assume a predecessor left a mess

Because a killed process does not release its layer (§2.4), every start purges first:
walk the candidate layers, and for any whose record still carries *our* signature
(height == `UI_MAXH` and bytes/pixel == 2 — stock layers are 480 or 1088 tall, so this
cannot collide), attach it and do one `disable` + `update`.

```
[purge] cleared predecessor residue: gf1 (hw L6)  h=220 pitch=512
[purge] cleared predecessor residue: gf5 (hw L2)  h=220 pitch=544
[pick]  selected gf1 = hw L6
```

Also trap `SIGTERM`/`SIGINT`/`SIGHUP`/`SIGQUIT`/`SIGSEGV`/`SIGBUS` and turn the layer off
before exiting. `kill -9` cannot be caught — that case is what the purge exists for.

#### 4.6 Hiding: order matters

Turn the layer **off first**, *then* clear the buffers:

```c
go_dark();                 /* disable + update  -> hardware stops scanning us */
clear_rgb(); clear_alpha();/* now nothing can be scanned out mid-clear        */
```

The reverse order flashes a **black panel-shaped box for one frame** on the way out: while
you are zeroing the RGB the layer is still live, and the *alpha plane still holds the
panel's opaque values* — so the hardware renders "panel shape, all-black colour". Clearing
pixels at all is a leftover habit from the 1-bit era, when clearing *was* the hiding
mechanism; with `disable` doing the hiding it is only defence in depth, and it must happen
after the layer is off. Clear the alpha plane too.

---

### 5. Code layout

```
code/volume-osd/
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

#### Writing a `ui.def`

`w`/`h` describe the **widget box**; the shadow margin is carved out of it, so the visible
panel is `w - 2*shadow` × `h - 2*shadow`, centred. (Earlier revisions carved the margin off
only the left and bottom, which made the panel sit off-centre and left a visible pale strip
along the bottom — that asymmetry is gone.) All element coordinates (`icon_*`, `bar_*`,
`num_*`) are relative to the **panel**, not the widget box, so they do not change when you
adjust `shadow`.

Note that `ui_icon` centres on `y + 8`, so `icon_y` is its *top*, not its centre.

Keys added while getting the Material-style look right — all hot-reloadable, no recompile:

| key | meaning |
|---|---|
| `panel_alpha` | 0-255 opacity of the panel background. `255` = fully opaque and no alpha plane is allocated (identical to the 1-bit path). Anything less turns on the alpha plane of §2.3.1 — **which permanently consumes one of the four alpha planes and disables the OEM parking-distance display for the rest of the ignition cycle. Keep this at 255 in a car. See Trap 3.** |
| `panel2` | bottom colour of a vertical gradient (`panel` is the top). Set equal to `panel` for a flat Material surface |
| `shadow_a` | shadow opacity 0-255 |
| `shadow_dx` / `shadow_dy` | shadow offset. Material-ish is `dx=0`, `dy` a few px |
| `num_scale` | digit size in percent. The baked font is 23×28; the renderer box-filters it down, which looks far better than nearest-neighbour |

**Shadows: use two layers, not one.** A single shadow reads as a hard dark outline on a
light background no matter how you tune it. Material stacks an *ambient* pass (tight,
slightly stronger, no horizontal offset) and a *key-light* pass (wide, faint, offset
downward); compositing them by max coverage reproduces the elevation falloff. Two traps
found by sampling real screenshots:

- Use a **rounded-rectangle distance** (`q = max(|p-centre| - (half - R), 0); d = |q| - R`),
  not a rectangular one, or a capsule-shaped panel gets a visibly square halo.
- Cast the shadow from the **translated shape** and draw it *under* the panel. Translating
  the finished ring instead moves its *inner* edge outward too and leaves a 2-3 px gap of
  clean background between panel and shadow (measured: panel ended at x=390, x=391-393 were
  pure background, shadow only started at x=394).

#### Render pipeline

```
ui_render()  →  ui_popbuf (RGBA5551 colour)
             +  ui_cov    (8-bit coverage — anti-aliasing)
             +  ui_al     (8-bit target opacity — panel vs content)
             →  blit_layer():  colour|1  and  alpha = ui_al * ui_cov / 255
                               (1-bit fallback when panel_alpha == 255:
                                cv >= 128 ? colour|1 : 0x0000)
             →  push_layer():  full re-assert + set_blending + update
```

`ui_cov` and `ui_al` are deliberately separate: coverage is *shape* (what makes edges
smooth), opacity is *intent* (panel background translucent, text on top of it opaque).
Multiplying them at blit time is what yields soft rounded corners on a see-through panel.

The surface is created once at `UI_MAXW×UI_MAXH` (520×220) and then **cropped by the src
viewport and positioned by the dst viewport** — so changing the geometry in `ui.def`
never requires recreating the surface.

---

### 6. Bench development loop (no USB stick)

```bash
# Build (the script aborts on `error:` instead of leaving a stale binary to fool you)
bash code/volume-osd/build.sh code/volume-osd/coexist_pop.c code/volume-osd/coexist_pop

# Push (~97 s for 66 KB), verifies cksum automatically
python3 code/common/serial/ser_push.py code/volume-osd/coexist_pop.stripped /tmp/coexist_pop 192

# Launch — all three redirections are mandatory (see below)
python3 code/common/serial/ser2.py 'chmod 755 /tmp/coexist_pop; /tmp/coexist_pop </dev/null >/dev/null 2>/dev/null &'

# Pull the log
python3 code/common/serial/ser_pull.py /tmp/pop.txt pop.txt

# Layout/colour only: push 348 bytes, no recompile
python3 code/common/serial/ser_push.py code/volume-osd/ui.def /tmp/ui.def 192

# Force a displayed value (to test rendering); delete the file to return to live volume
python3 code/common/serial/ser2.py 'echo 30 > /tmp/uival'
python3 code/common/serial/ser2.py 'rm -f /tmp/uival'
```

#### Serial-specific traps

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

### 7. Verify with real screenshots, not by staring at the screen

The unit can hand you the **composited** frame — stock UI *and* your overlay — so almost
every UI question is answerable offline instead of by asking someone to photograph a
screen.

```c
gf_surface_create(&snap, dev, 800, 480, di.format, 0, 0);
gf_display_snapshot(disp, 0, 0, 0, 799, 479, snap);   /* x2,y2 inclusive */
```

The snapshot lands as **BGRA8888** (`fmt 0x1420`, stride 3200) regardless of what the
layers themselves use. Dump it as hex text — 57600 baud serial makes binary transfers
fragile — and convert on the host.

```bash
/tmp/pcmshot /tmp/s.hex 200 190 400 100     # region, full resolution
python3 scratchpad/ser_pull.py /tmp/s.hex /tmp/s_raw.txt
```

**Measure geometry by differencing two frames.** Capture once with the popup hidden and
once with it shown; the bounding box of the changed pixels *is* the overlay's true extent:

```bash
python3 dev/shotdiff.py /tmp/bg_raw.txt /tmp/fg_raw.txt 200 190 372 76
#   actual: 372 x 76      expected: 372 x 76      width OK / height OK
```

That one command is what found the off-by-one row in the dst viewport formula (§2.5) — a
defect a human eye reports as "maybe a pixel of overhang" and a diff reports exactly.

**What a snapshot cannot show you:** artefacts introduced during hardware scanout, such as
the alpha-plane banding of §2.3.1. Those live in the display controller, not in any buffer,
so that one class of bug still needs a photograph. Say so explicitly when you ask for one.

---

## Part III — Problems and dead ends

The traps and the approaches that were tried and abandoned. Traps that cannot be separated
from the recipe they qualify stay with it and are only indexed here; the dead ends are
stated in full.

### 8. Index of the traps documented inline

Each of these is written where it applies, not here. This table is navigation only.

| trap | documented in |
|---|---|
| 2.1 ★★★ The driver inverts layer numbers: `hw_layer = 7 − gf_layer` | Part I §2.1 |
| 2.2 The pixel format is RGBA5551, **not** ARGB1555 | Part I §2.2 |
| 🚨 Trap 1 — the alpha plane's byte stride must be 64-byte aligned | Part I §2.3.1 |
| 🚨 Trap 2 — re-assert the blending on every push | Part I §2.3.1 |
| 🚨🚨🚨 Trap 3 — allocating an alpha plane is permanent, and there are only four | Part I §2.3.1 |
| 2.4 ★★★ Deadlock rule: every `gf_layer_update` needs a full re-assert first | Part I §2.4 |
| ⚠️ Correction (2026-08-02/04) — earlier revisions of this document were wrong here | Part I §2.4 |
| 2.5 Everything else | Part I §2.5 |
| 4.3 ❌ Dead end: "pick a layer nobody uses" — kept with the yield protocol it motivates | Part II §4.3 |
| 🚨 The yield action must be *nothing*. Do not "hide cleanly" first | Part II §4.4 |
| Serial-specific traps | Part II §6 |

---

### 9. Dead ends — do not retry

| approach | verdict |
|---|---|
| `layermanager.cfg`'s `reserveLayerForCid` | **Dead end.** The looked-up value is stored at `rec+0x2c` and **no code ever reads it again**. Flashed four times, no effect |
| `layerOrder` | Gated by chip type. Only applied when `graphicChip==1`; this unit is CARMINE16 (=8) → identity is forced |
| `lastAvailableLayer` set to `0-7` | **Blacks out the screen.** It is a *lower bound*, not an upper bound — the direction is inverted, so this leaves only slot 7 renderable |
| Sharing the stock surface 0x1f | Dynamic-page ghosting is structural; superseded by this framework |
| BGRA8888 / 32bpp | Not supported by the layer. It "succeeds" only because the library never validates |
| Guessing channel/byte order | One `0xFFFF` measurement settles it: an all-ones pixel must render saturated white under *any* bit arrangement |
| **Choosing a layer from a bench census** | **Dead end.** The layer map is per vehicle model. Hardware L6 measured completely idle across 221 s of heavy operation on a Panamera bench, and is the PDC radar/car-model layer on a 911. Use the yield protocol (§4.4) instead of trying to find a free layer |
| Hiding by clearing pixels to `0x0000` | Only works while nothing resets the blending config. Another gf client touching display-wide state turns your "transparent" pixels into an opaque black rectangle. Hide with `disable` + `update` (§2.4) |

---

## Appendix

### References

- Chip manual: MB86297A (layer registers around p.430-432).
  ⚠️ **Not** the MB86296S CORAL-PA spec — that is the previous generation with only L0-L5.
- Disassembly recipe (both `.so` files have stripped section headers, so plain
  `objdump -d` produces nothing):
  ```bash
  docker run --rm -v "$PWD:/work" sh4gdb:latest \
    objdump -D -b binary -m sh4 -EL --start-address=0x... /work/<path>
  ```
