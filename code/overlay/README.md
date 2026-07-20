# HW-layer overlay framework

Draw your own popups (volume OSD, toasts, dialogs) **on top of the stock PCM 3.1 UI**,
with **no flash write at all** and no interference with the stock UI.

It works by becoming a **second QNX Graphics Framework client** and taking an idle Carmine
hardware layer that `layermanager` never allocates. Every hardware layer has its own
scanout buffer, so we physically cannot touch the stock UI's buffer or its locks — the
tearing/ghosting you get from sharing the stock surface disappears at the hardware level.

Verified on the bench 2026-07-20: full colour, anti-aliased text, true rounded-corner
transparency, live volume tracking the knob, auto-dismiss.

## Files

| file | what |
|---|---|
| `coexist_pop.c` | the engine (layer setup, hot-reload, live volume, show/hide) |
| `ui_core.c` | shared renderer + `ui.def` parser — the *same file* the Mac previewer uses |
| `ui_font.h` | offline-baked font (digits + proportional, incl. CJK) |
| `ui.def` | popup description (geometry/colour/animation/binding) — plain text, hot-reloaded |
| `gf_defs.h` | gf constants/structs/prototypes, rebuilt by disassembling the on-device library |
| `stub_libgf.c` | link-time stub; the real library on the device is `libgdcApiCarmine.so` |
| `build.sh` | freestanding SH4 build (no QNX SDK needed) |

## Three traps that will cost you a day each

1. **The driver inverts layer numbers: `hw = 7 − gf`.** Use **gf layer 5** (= hardware L2).
   gf 6 is hardware L1, the video-capture layer — the generic driver path writes into bits
   the manual marks *Reserved* on L1, which kills the top 6 bits of every pixel: red never
   appears, and the image is vertically truncated.

2. **The pixel format is RGBA5551, not ARGB1555**, even though `gf_layer_query` reports
   `0x1710`. The driver hardwires `LnEC=0b10` = "Direct color (16bpp) **RGBA** mode":
   `R[15:11] G[10:6] B[5:1] A[0]`, output shifted 3 bits toward the MSB.

3. **Every `gf_layer_update` must be preceded by the full re-assert sequence**
   (`set_surfaces` → viewports → `set_layer_order` → `enable`). A bare `gf_layer_update`
   blocks *forever* in `gdcServerCarmine` waiting for a vsync that never comes. Same for
   `gf_layer_disable` + update — so "hide" must be done by filling the visible area with
   `0x0000`, never by disabling the layer.

Full write-up: [`HW_overlay_framework.md`](../../HW_overlay_framework.md) ([简体中文](../../HW_overlay_framework.zh-CN.md))

## Iterating

Layout/colour changes only need the **348-byte `ui.def`** pushed over serial — the engine
re-reads it every second. No recompile, no 66 KB binary transfer.
