# Code

Reverse-engineering and patch-building tools for the PCM_31_AUX-BT fixes.

> ⚠️ **Firmware binaries are NOT included** — they are modified proprietary Porsche firmware. Build your own from your own firmware dump using these tools. Addresses here are for one specific bench MOPF binary; **re-locate them for your firmware** (objdump recipe is in the journey doc).
>
> 固件二进制不在此仓库(改过的保时捷专有固件)。用这些工具 + 你自己 dump 的固件自行构建;文中地址仅对特定台架固件有效,换机器要用 objdump 重新定位。

## The fix 解法
- **`build_shotgun_child.py`** — **the solution**: the child-vtable shotgun assembler (hand-written SH4). Instruments the 5 child-vtable methods so that whichever fires at BT-connect triggers one AUX→BT.
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
- `sh4tools/mempoke.c` (+ `_start.S`, `start_stack.S`) — the `mp2` 1-byte writer for `/proc/<pid>/as` (build with a QNX SH4 toolchain; use `start_stack.S` so argc/argv are passed).
- `autorun/run.sh` + `copie_scr.sh` — the USB autorun flasher (verifies cksum + ARM file, runs `flashit -v`, one-shot). The `.ifs` payload is **not** included.

## Reproduce
Read [journey_English.md](../journey_English.md) for how these fit together. Every cave is hand-assembled SH4 and validated in `sh4emu.py` before flashing; every flash passes `verify_ifs_flashable.py` first.
