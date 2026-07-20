# Code

Tools and sources for everything in this repo. Grouped the same way as the [top-level README](../README.md): the **Bluetooth fix** (ships by flashing), **runtime features** (never touch flash), and shared **tooling**.

> ⚠️ **Firmware binaries are NOT included** — they are modified proprietary Porsche firmware. Build your own from your own firmware dump using these tools. Addresses here are for one specific bench MOPF binary; **re-locate them for your firmware** (objdump recipe is in the journey doc).
>
> 固件二进制不在此仓库(改过的保时捷专有固件)。用这些工具 + 你自己 dump 的固件自行构建;文中地址仅对特定台架固件有效,换机器要用 objdump 重新定位。

## Bluetooth fix 蓝牙修复

Ships by **flashing IFS1** — runtime injection is not possible for these caves (see [`sh4tools/SERIAL_LOOP.md`](sh4tools/SERIAL_LOOP.md) § *What the loop can and can't reach*).

**The caves**
- **`build_shotgun_child_chain.py`** — **the final solution** (bench + real 9x1, cold-start verified). The child-vtable shotgun with the hardcoded `main` replaced by a runtime reverse-chain resolve `main = *(*(*(child+0x38)+0x08)+0x70)`, each hop bounds-guarded — no per-build address, and an unmapped intermediate bails cleanly instead of faulting.
- `build_shotgun_child.py` — its predecessor / reference baseline: same cave, but a hardcoded `main` that drifts per boot (its one flaw). Kept for comparison.
- `build_shotgun.py` — the main-vtable shotgun (diagnostic; imported by the child version).
- `build_auxkick_cave.py` — early AUX→BT cave template.
- `build_vtrace.py` — vtrace instrumentation (the tracer that hit the dead-buffer wall).
- `build_c1_vhook.py` — the vtable +0x44 hook cave (a dead end).

**Proving it, then flashing it**
- **`validate_shotgun_child_chain.py`** — runs the cave in `sh4emu` against the real `-2` connect-bug snapshot: self-derives `main`, fires, never faults (incl. deliberately corrupted pointers).
- **`verify_ifs_flashable.py`** — the **pre-flash preflight gate** (startup + imagefs each sum-to-zero). Run before every flash — this is the gate the two bricked benches paid for.
- IFS pipeline: `inflate_ifs_lzo.py` (decompress IFS1 → raw) → `patch_decomp_ifs_file.py` (exact-size file replacement) → `deflate_ifs_lzo.py` (recompress, block-preserving + outer sum-to-zero).
- `autorun/run.sh` + `copie_scr.sh` — the USB autorun flasher (verifies cksum + ARM file, runs `flashit -v`, one-shot). The `.ifs` payload is **not** included.

## Runtime features

Separate apps running alongside the stock software — **no flash write**, gone on reboot.

- [`overlay/`](overlay/) — **HW-layer overlay framework**: draw your own popups (volume OSD, toasts, dialogs) over the untouched stock UI by taking an idle Carmine hardware layer. Contains `coexist_pop.c` (engine), `ui_core.c` (renderer + `ui.def` parser), `ui_font.h`, `ui.def`, `gf_defs.h`, `stub_libgf.c`, `build.sh`.
  Read [`overlay/README.md`](overlay/README.md) first — three traps that each cost a day. Full write-up: [`HW_overlay_framework.md`](../HW_overlay_framework.md).

## Tooling 工具

- **`sh4emu.py`** — an SH4 interpreter; runs real PCM3Root functions against a memory snapshot for offline dynamic validation. Every hand-written cave is validated here before flashing.
- `sh4_run_switch.py` — a source-switch harness on top of sh4emu (needs a memory snapshot you provide).
- [`serial/`](serial/) — **bench serial toolchain**: `ser_push.py` (chunked upload + cksum verify), `ser_pull.py`, `ser2.py` (run a command), `ser_kill.py` (answers `slay`'s interactive prompts). The 57600 root-shell loop, no USB stick, no reflash.
- [`sh4tools/`](sh4tools/) — on-device C tools:
  - `mempoke.c` (+ `_start.S`, `start_stack.S`) — the `mp2` 1-byte reader/writer for `/proc/<pid>/as` (build with a QNX SH4 toolchain; use `start_stack.S` so argc/argv are passed).
  - `mempoke_fix.c` — range-scan variant: sweeps `[start,end)` for an 8-byte signature and flips the `mov #1` immediate `01→07`. ⚠️ **A scanning tool, not the fix** — the lever it targets (`0x082b65e0`, desiredApp) was an unverified early experiment; the diff confirmed it does not belong to lock-BT and it was removed from the final image.
  - **[`SERIAL_LOOP.md`](sh4tools/SERIAL_LOOP.md)** — the live-serial no-reflash iterate loop, and the CoW wall that bounds it.

## Adding a new feature

Give it its own `code/<feature>/` directory with a short `README.md` inside, then follow the checklist in the [top-level README](../README.md#adding-a-new-feature).

## Reproduce
Read [journey_English.md](../journey_English.md) for how these fit together. Every cave is hand-assembled SH4 and validated in `sh4emu.py` before flashing; every flash passes `verify_ifs_flashable.py` first.
