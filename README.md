# PCM_31_AUX-BT

**Porsche PCM3.1 — Bluetooth audio fix, and a modding toolkit built around it**

> Porsche PCM3.1 (CHN) · QNX 6.3.x · SH-4A · 2026-07
> **✅ Bluetooth fix solved — flashed and confirmed on the MOPF bench *and* on the real car (911/9x1).**
>
> **English** · [简体中文](README.zh-CN.md)

> ⚠️ **Disclaimer**: For study/research only. Flashing can brick your device **beyond recovery** (a watchdog endless-reboot, too fast to ever hold the emergency shell) — **use at your own risk; don't blame me.** 仅供学习研究,刷写有砖机风险且可能救不回来,后果自负。 Full text: [DISCLAIMER.md](DISCLAIMER.md) · License: [GPL-3.0](LICENSE)

![working bench](images/01-bench-working-fm.jpg)

> **The cost**: two bench units were **bricked and unrecoverable** earlier — a watchdog endless-reboot too fast to ever hold the emergency shell. That paid for the methodology (two-segment sum-zero preflight, plus knowing a "stable" brick is serial-recoverable but a "watchdog" brick is not); nothing has bricked since. See the "Prologue" in the journey doc.

## What's in here

| | What it does | Ships as | Status |
|---|---|---|---|
| **Bluetooth fix** (lock-BT + boot-sound) | Bluetooth stays selected across a cold start — **and it makes sound**, with nothing to press | **IFS1 flash** | ✅ bench + real car (911/9x1) |
| **HW-layer overlay framework** | Draw your own popups (volume OSD, toasts, dialogs) on top of the untouched stock UI | runtime app, **no flash** | ✅ bench |
| **Bench serial toolchain** | 57600 root-shell dev loop — push a binary, run it, pull the log, kill it; no USB stick, no reflash | dev tooling | ✅ |
| **RE + build pipeline** | Reliable SH4 disassembly, `sh4emu` interpreter, IFS inflate/patch/deflate, pre-flash preflight gate | dev tooling | ✅ |

**How something ships is the first thing to know about it.** A flash patch can brick the unit and is the only way to change stock code (see [why](#why-the-bluetooth-fix-ships-as-a-flash)); a runtime app cannot brick anything and disappears on reboot.

---

# The Bluetooth fix

Two chapters of my chain of pain:
- **① lock-BT** — playing Bluetooth, turn the car off; next trip it **always reverts to FM**, must manually re-select BT → fixed
- **② boot-sound** — with BT locked, the phone connects at boot **but there is no sound**, must manually AUX→BT → fixed

**The end state, on the real car**: park, take the phone with you, come back, cold start — the phone reconnects and **the music comes back on its own, on Bluetooth, with nothing to press.**

## Part 1 · lock-BT (the original pain)
- **Problem**: playing Bluetooth, turn the car off; next trip it **always reverts to FM**, must manually re-select Bluetooth.
- **Root cause / fix**: the real source arbitration is at runtime (not the OnOff/fallback dead-code layer). **fmguard = an 18-byte cave** (pool slot 0x082ac898) guarding: if the source being submitted is FM, don't submit → FM no longer auto-seizes. Confirmed by flashing on both the bench and the real car 911/9x1.

## Part 2 · boot-sound (Issue-1)
- **Problem**: with BT locked, the phone connects at boot **but there is no sound or track name**; must manually AUX→BT.
- **Root cause**: at boot, audio focus is never requested → the getter returns -2 → the establishment short-circuits → the DSP is not routed → silence. Only a genuine source change produces that request.
- **Solution**: **child-vtable shotgun** — instrument all 5 methods of the child object's vtable; whichever the connect event calls triggers, in the bug state, one AUX→BT → the establishment activates → sound.

### Part 2b · the shotgun's one flaw, and the fix that made it real

The shotgun made sound on the bench, but it **hardcoded `main`** (the `CPSoundPresCtrl` singleton). `main` is a heap object that **drifts every boot** — six boots, six different addresses (`0x086ec01c`, `0x086ed694`, `0x086ed01c`, `0x086ef01c`, …). A hardcoded address that lands on an unmapped page faults on the *first* dereference → watchdog → the brick you cannot recover. Scanning the heap for it instead was worse: it overloaded the hot path and crashed.

**The fix** — derive `main` from the one pointer that is always valid: the real dispatch `this` (= the child object). There is a structural reverse chain from child back to main:

```
main = *(*(*(child + 0x38) + 0x08) + 0x70)
```

Every hop is range-checked `[0x08600000, 0x08f00000)` **before** it is dereferenced, and the result is validated against the `CPSoundPresCtrl` vtable (`*(main) == 0x085c4c5c`). An unpopulated or mid-relink state therefore **bails cleanly and retries on the next call** instead of faulting. No scan, no hardcode, no drift — and the upper bound is `0x08f00000`, not `0x09000000`, so that `pointer + offset` can never read past the mapped window (the one fault path an adversarial review found).

The chain is **structural, not a coincidence**: it resolves in every snapshot — bench playing / disconnected / AUX, the real `-2` connect-bug snapshot, the post-fix working state, and the **real car** — across all six distinct `main` addresses.

One more trap worth knowing: `entertSourceChanged` forks on `main+0x944`. **Zero** takes the durable child-dispatch leg (the one a manual source tap uses — no teardown). **Non-zero** takes a fragile TLAM connect+retry leg whose async handler exhausts its retries ~5 s later and calls `switchAudio(Default)`, tearing the sound back down. The cave writes nothing to `+0x944`, so it always takes the durable leg.

**Outcome**: stock + lock-BT + boot-sound in one clean firmware (all failed experiments removed) + zero-serial USB autorun — **flashed and confirmed on the bench and on the real car (911/9x1)**. Park, take the phone, come back, cold start, the phone reconnects: **the sound comes back on its own and Bluetooth stays selected. Zero manual operation.**

📄 **Full journey — two chapters, every dead end and why: [journey_English.md](journey_English.md)** · 中文全过程见 [全过程_中文.md](全过程_中文.md)

## What ships

**Composition** = stock `PCM3Root` + fmguard (lock-BT) + child-chain cave (boot-sound), delivered by **flashing IFS1**.

| | Location |
|---|---|
| **The solution** | [`code/build_shotgun_child_chain.py`](code/build_shotgun_child_chain.py) — the child-vtable cave with the self-derived `main` |
| Its predecessor (kept as the reference baseline) | [`code/build_shotgun_child.py`](code/build_shotgun_child.py) — same cave, but with the hardcoded `main` that drifts |
| Offline proof | [`code/validate_shotgun_child_chain.py`](code/validate_shotgun_child_chain.py) — runs the cave in `sh4emu` against the real `-2` connect-bug snapshot: it self-derives `main`, fires, and never faults (incl. deliberately corrupted pointers) |
| Preflight gate | [`code/verify_ifs_flashable.py`](code/verify_ifs_flashable.py) |
| Flashable firmware | **not included** — modified proprietary firmware; build your own from your dump with the tools in `code/` |

### Why the Bluetooth fix ships as a flash

`/proc/<pid>/as` writes reach RW pages only and **fail on read-only code/rodata** — the CoW wall, proven on real hardware ([journey, Dead end ③](journey_English.md)). Both caves live in the RX segment, so **runtime code injection is not possible on this hardware**. The serial poke loop is how the fix was *found*; flashing IFS1 is how it *ships*.

> Note: the bench (MOPF / `IFS_G1_E2`) and the car (`IFS_9X1`) ship **byte-identical `PCM3Root` binaries** — only the surrounding imagefs differs. So the car got the exact same proven binary. Locate `PCM3Root` through the **imagefs directory** (`mnt/ifs1/HBproject/PCM3Root`), never by searching for the ELF header — every SH4 executable in the image starts with the same bytes, and you *will* extract the wrong file.

---

# Runtime features

Separate apps that run alongside the stock software. They **never write flash**, so they cannot brick anything and they vanish on reboot.

## HW-layer overlay framework (2026-07-20, shipped to a real car 2026-08-04)

![volume OSD drawn on our own hardware layer, over the stock Jukebox page](images/06-overlay-volume-osd.jpg)

*Our own volume OSD, drawn on an independent Carmine hardware layer, sitting on top of the untouched stock UI. Note that the stock page underneath keeps working normally — the progress bar advances, the clock ticks, the highlighted button stays highlighted. Nothing was flashed; the popup is pure runtime.*

- **Goal**: draw our own popups (volume OSD, toasts, dialogs) on top of the stock UI, without flashing anything.
- **Approach**: become a *second* gf client and take an idle Carmine hardware layer that `layermanager` never allocates. Each HW layer has its own scanout buffer → physically cannot collide with the stock UI's buffer or locks, so the ghosting/tearing of the old "share the stock surface" approach is gone at the hardware level.
- **Status**: running on a real car. Translucent Material-style panel with a real 8-bit
  alpha plane (soft rounded corners, elevation shadow), the bar tracking the volume knob
  live, and — the part that took longest — coexisting correctly with the parking-radar and
  reversing-camera displays.
- **The lesson that mattered most**: you never own a hardware layer. There is no ownership
  gate; the per-layer state is one shared record and the last writer wins. Hunting for "a
  layer nobody uses" is a dead end because **the layer map differs per vehicle model** —
  the layer that measured completely idle across 221 s on a Panamera bench turned out to be
  the PDC radar layer on a 911. The answer is a **yield protocol**: watch your own layer's
  record every tick, and the moment the stock stack starts using it, *stop touching it
  entirely* until it goes idle again. That mirrors the OEM's own priority model and needs
  no knowledge of any vehicle's layer map. See §2.6 of the write-up.
- **Traps that each cost a day**: the driver **inverts layer numbers** (`hw = 7 − gf`); the
  pixel format is **RGBA5551 despite the API reporting ARGB1555**; **every
  `gf_layer_update` must be preceded by a full re-assert** or it deadlocks in
  `gdcServerCarmine`; and an alpha plane's **byte stride must be 64-byte aligned** or the
  whole panel shears into diagonal bands.
- Code: [`code/overlay/`](code/overlay/) · Full write-up: [`HW_overlay_framework.md`](HW_overlay_framework.md) ([简体中文](HW_overlay_framework.zh-CN.md))

---

# Tooling

## Bench serial toolchain

The 57600 root-shell development loop — push a binary, run it, pull the log, kill it, **without a USB stick and without reflashing**. Layout-only changes push 348 bytes instead of a 66 KB binary.

- [`code/serial/`](code/serial/) — `ser_push.py` (chunked upload + cksum verify), `ser_pull.py`, `ser2.py` (run a command), `ser_kill.py`.
- [`code/sh4tools/SERIAL_LOOP.md`](code/sh4tools/SERIAL_LOOP.md) — the `/proc/<pid>/as` poke loop this pairs with, and **what it can and cannot reach**.

## Reverse-engineering + build

- [`code/`](code/) — cave assemblers, `sh4emu.py` (an SH4 interpreter that runs real `PCM3Root` functions against a memory snapshot), the IFS inflate/patch/deflate pipeline, and `verify_ifs_flashable.py` (the pre-flash gate the two bricked benches paid for).
- Full index: [`code/README.md`](code/README.md).

---

## Repository layout

```
README.md / README.zh-CN.md        this index
journey_English.md / 全过程_中文.md  the Bluetooth fix, full story + every dead end
HW_overlay_framework.md (+.zh-CN)  the overlay framework write-up
code/
  build_*.py, sh4emu.py, ...       the Bluetooth fix: assemblers, emulator, IFS pipeline
  overlay/                         HW-layer overlay framework
  serial/                          bench serial toolchain
  sh4tools/                        on-device C tools (mempoke) + SERIAL_LOOP.md
  autorun/                         USB autorun flasher scripts
images/                            bench photos and screenshots
```

## Adding a new feature

The layout absorbs new work without renumbering anything:

1. **Code** → its own `code/<feature>/` directory (like `overlay/` and `serial/`), with a short `README.md` inside covering the traps someone would otherwise rediscover the hard way.
2. **Write-up** → `<Feature>.md` at the repo root, plus `<Feature>.zh-CN.md`. **English is the master, Chinese is the translation** — same convention as this README.
3. **Index** → add one row to *What's in here*, and one `##` section under **Runtime features** (or under **The Bluetooth fix** style of its own top-level section, if it ships by flashing).
4. **State how it ships** — runtime app or flash patch. That one fact sets the risk profile and is the first thing any reader needs.
5. Keep the dead ends. The "what I tried and why it failed" sections are the most valuable part of these docs; a feature without them will cost the next person the same days it cost you.

---

## Acknowledgments

Huge thanks to the following references — they gave this whole effort a solid foundation and direction:

- **[dspl1236/PCM-Forge](https://github.com/dspl1236/PCM-Forge)** — the PCM-Forge project and its reverse-engineering groundwork.
- **[Rennlist — "PCM3.1 reboot problem, repair attempt"](https://rennlist.com/forums/991/1484228-pcm3-1-reboot-problem-repair-attempt.html)** — the community thread on PCM3.1 reboot/repair.

And this was ultimately solved through the collaboration with **Claude (Anthropic)** — the reverse engineering, the tooling (reliable SH4 disassembly, `sh4emu`, the shotgun approach) and the final fix all came out of the back-and-forth, then tested hands-on on the bench. It took both halves to get here.

---

## License

Copyright (C) 2026 WillCoder

This project is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public License v3.0** (or, at your option, any later version) — see [LICENSE](LICENSE). It is distributed in the hope that it will be useful, but **WITHOUT ANY WARRANTY** (see also [DISCLAIMER.md](DISCLAIMER.md)).
