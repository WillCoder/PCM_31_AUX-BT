# Code

Grouped the same way as the [top-level README](../README.md): one directory per shipped
feature, and everything either of them could use in `common/`.

```
bluetooth-fix/   feature 1 — the caves, their offline validator, the USB autorun flasher
volume-osd/      feature 2 — the overlay engine, renderer, ui.def, screenshot verify
common/          shared — sh4emu, the IFS pipeline, the serial toolchain, on-device C tools
```

> ⚠️ **Firmware binaries are NOT included** — they are modified proprietary Porsche firmware.
> Build your own from your own firmware dump using these tools. Addresses here are for one
> specific bench MOPF binary; **re-locate them for your firmware** (the objdump recipe is in
> [`../bluetooth-fix.md`](../bluetooth-fix.md)).
>
> 固件二进制不在此仓库(改过的保时捷专有固件)。用这些工具 + 你自己 dump 的固件自行构建;
> 文中地址仅对特定台架固件有效,换机器要用 objdump 重新定位。

## `bluetooth-fix/` — 蓝牙修复

Ships by **flashing IFS1** — runtime injection is not possible for these caves (see
[`common/sh4tools/SERIAL_LOOP.md`](common/sh4tools/SERIAL_LOOP.md) § *What the loop can and can't reach*).
Full write-up: [`../bluetooth-fix.md`](../bluetooth-fix.md) ([简体中文](../bluetooth-fix.zh-CN.md)).

**The caves**
- **`build_shotgun_child_chain.py`** — **the final solution** (bench + real 9x1, cold-start verified). The child-vtable shotgun with the hardcoded `main` replaced by a runtime reverse-chain resolve `main = *(*(*(child+0x38)+0x08)+0x70)`, each hop bounds-guarded — no per-build address, and an unmapped intermediate bails cleanly instead of faulting.
- `build_shotgun_child.py` — its predecessor / reference baseline: same cave, but a hardcoded `main` that drifts per boot (its one flaw). Kept for comparison.
- `build_shotgun.py` — the main-vtable shotgun (diagnostic; imported by the child version).
- `build_auxkick_cave.py` — early AUX→BT cave template.
- `build_vtrace.py` — vtrace instrumentation (the tracer that hit the dead-buffer wall).
- `build_c1_vhook.py` — the vtable +0x44 hook cave (a dead end).

**Proving it, then flashing it**
- **`validate_shotgun_child_chain.py`** — runs the cave in `sh4emu` against the real `-2` connect-bug snapshot: self-derives `main`, fires, never faults (incl. deliberately corrupted pointers).
- `autorun/run.sh` + `copie_scr.sh` — the USB autorun flasher (verifies cksum + ARM file, runs `flashit -v`, one-shot). The `.ifs` payload is **not** included.
- The preflight gate and the IFS pipeline live in `common/` — they are not Bluetooth-specific.

## `volume-osd/` — 音量弹窗(独立硬件图层)

A separate app running alongside the stock software — **no flash write**, gone on reboot.
Full write-up: [`../volume-osd.md`](../volume-osd.md) ([简体中文](../volume-osd.zh-CN.md)).

Contains `coexist_pop.c` (engine), `ui_core.c` (renderer + `ui.def` parser), `ui_font.h`,
`ui.def`, `gf_defs.h`, `stub_libgf.c`, `build.sh`, and `verify/` (framebuffer grab + diff).

**Read [`volume-osd/README.md`](volume-osd/README.md) first** — the traps there each cost a day,
and one of them (`panel_alpha` must be 255) costs the driver their parking sensors.

## `common/` — shared tooling 共用工具

- **`sh4emu.py`** — an SH4 interpreter; runs real PCM3Root functions against a memory snapshot for offline dynamic validation. Every hand-written cave is validated here before flashing.
- `sh4_run_switch.py` — a source-switch harness on top of sh4emu (needs a memory snapshot you provide).
- **`verify_ifs_flashable.py`** — the **pre-flash preflight gate** (startup + imagefs each sum-to-zero). Run before every flash — this is the gate the two bricked benches paid for.
- IFS pipeline: `inflate_ifs_lzo.py` (decompress IFS1 → raw) → `patch_decomp_ifs_file.py` (exact-size file replacement) → `deflate_ifs_lzo.py` (recompress, block-preserving + outer sum-to-zero).
- [`serial/`](common/serial/) — **bench serial toolchain**: `ser_push.py` (chunked upload + cksum verify), `ser_pull.py`, `ser2.py` (run a command), `ser_kill.py` (answers `slay`'s interactive prompts). The 57600 root-shell loop, no USB stick, no reflash.
- [`sh4tools/`](common/sh4tools/) — on-device C tools:
  - `mempoke.c` (+ `_start.S`, `start_stack.S`) — the `mp2` 1-byte reader/writer for `/proc/<pid>/as` (build with a QNX SH4 toolchain; use `start_stack.S` so argc/argv are passed).
  - `mempoke_fix.c` — range-scan variant: sweeps `[start,end)` for an 8-byte signature and flips the `mov #1` immediate `01→07`. ⚠️ **A scanning tool, not the fix** — the lever it targets (`0x082b65e0`, desiredApp) was an unverified early experiment; the diff confirmed it does not belong to lock-BT and it was removed from the final image.
  - `alphatab.c` — read-only probe that prints `gdcServerCarmine`'s own alpha-plane allocation ledger out of `/proc/<pid>/as`, with a `SANITY=OK/BAD` self-check so a wrong base address reports an error instead of returning plausible noise. This is the instrument behind Trap 3 in the volume-osd write-up.
  - **[`SERIAL_LOOP.md`](common/sh4tools/SERIAL_LOOP.md)** — the live-serial no-reflash iterate loop, and the CoW wall that bounds it.

## Adding a new feature

Give it its own `code/<feature>/` directory with a short `README.md` inside; put anything a
second feature could reuse in `common/` instead. Then follow the checklist in the
[top-level README](../README.md#adding-a-new-feature).

## Reproduce

Read [`../bluetooth-fix.md`](../bluetooth-fix.md) and [`../volume-osd.md`](../volume-osd.md)
for how these fit together. Every cave is hand-assembled SH4 and validated in `sh4emu.py`
before flashing; every flash passes `verify_ifs_flashable.py` first.
