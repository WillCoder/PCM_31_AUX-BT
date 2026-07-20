# Code

Reverse-engineering and patch-building tools for the PCM_31_AUX-BT fixes.

> ⚠️ **Firmware binaries are NOT included** — they are modified proprietary Porsche firmware. Build your own from your own firmware dump using these tools. Addresses here are for one specific bench MOPF binary; **re-locate them for your firmware** (objdump recipe is in the journey doc).
>
> 固件二进制不在此仓库(改过的保时捷专有固件)。用这些工具 + 你自己 dump 的固件自行构建;文中地址仅对特定台架固件有效,换机器要用 objdump 重新定位。

## The fix 解法
- **`build_shotgun_child_chain.py`** — **the final solution** (bench + real 9x1, cold-start verified). The child-vtable shotgun with the hardcoded `main` replaced by a runtime reverse-chain resolve `main = *(*(*(child+0x38)+0x08)+0x70)`, each hop bounds-guarded — no per-build address, and an unmapped intermediate bails cleanly instead of faulting.
- `build_shotgun_child.py` — its predecessor / reference baseline: same cave, but a hardcoded `main` that drifts per boot (its one flaw). Kept for comparison.
- `build_shotgun.py` — the main-vtable shotgun (diagnostic; imported by the child version).
- `build_auxkick_cave.py` — early AUX→BT cave template.
- `build_vtrace.py` — vtrace instrumentation (the tracer that hit the dead-buffer wall).
- `build_c1_vhook.py` — the vtable +0x44 hook cave (a dead end).

## Tooling 工具
- **`sh4emu.py`** — an SH4 interpreter; runs real PCM3Root functions against a memory snapshot for offline dynamic validation.
- `sh4_run_switch.py` — a source-switch harness on top of sh4emu (needs a memory snapshot you provide).
- **`verify_ifs_flashable.py`** — the **pre-flash preflight gate** (startup + imagefs each sum-to-zero). Run before every flash — this is the gate the two bricked benches paid for.

## IFS pipeline
- `inflate_ifs_lzo.py` — decompress a PCM3.1 IFS1 → raw.
- `patch_decomp_ifs_file.py` — replace one file inside the decompressed IFS (exact size).
- `deflate_ifs_lzo.py` — recompress, block-preserving + outer sum-to-zero (imports `inflate_ifs_lzo`).

## On-device / autorun
- `sh4tools/mempoke.c` (+ `_start.S`, `start_stack.S`) — the `mp2` 1-byte reader/writer for `/proc/<pid>/as` (build with a QNX SH4 toolchain; use `start_stack.S` so argc/argv are passed).
- `sh4tools/mempoke_fix.c` — range-scan variant: sweeps `[start,end)` for an 8-byte signature and flips the `mov #1` immediate `01→07`. ⚠️ **A scanning tool, not the fix** — the lever it targets (`0x082b65e0`, desiredApp) was an unverified early experiment; the diff confirmed it does not belong to lock-BT and it was removed from the final image.
- **`sh4tools/SERIAL_LOOP.md`** — the live-serial no-reflash iterate loop (57600 root shell + `mempoke`, seconds per turn). This is the primitive the whole fix was found with.
- `autorun/run.sh` + `copie_scr.sh` — the USB autorun flasher (verifies cksum + ARM file, runs `flashit -v`, one-shot). The `.ifs` payload is **not** included.

## HW-layer overlay 弹窗框架
Draw your own popups (volume OSD, toasts, dialogs) over the stock UI by taking an idle Carmine hardware layer — a separate runtime app, **no flash write involved**. Unrelated to the BT fix above.
- [`overlay/`](overlay/) — `coexist_pop.c` (engine), `ui_core.c` (renderer + `ui.def` parser), `ui_font.h`, `ui.def`, `gf_defs.h`, `stub_libgf.c`, `build.sh`.
- See [`overlay/README.md`](overlay/README.md) for the three traps that each cost a day, and [`HW_overlay_framework.md`](../HW_overlay_framework.md) for the full write-up.

## Bench serial toolchain
The 57600 root-shell development loop — push a binary, run it, pull the log, kill it, **without a USB stick and without reflashing**.
- [`serial/`](serial/) — `ser_push.py` (chunked upload + cksum verify), `ser_pull.py`, `ser2.py` (run a command), `ser_kill.py` (answers `slay`'s interactive prompts).
- The `/proc/<pid>/as` poke loop this pairs with: [`sh4tools/SERIAL_LOOP.md`](sh4tools/SERIAL_LOOP.md).

## Reproduce
Read [journey_English.md](../journey_English.md) for how these fit together. Every cave is hand-assembled SH4 and validated in `sh4emu.py` before flashing; every flash passes `verify_ifs_flashable.py` first.
