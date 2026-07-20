# PCM_31_AUX-BT

**Porsche PCM3.1 Bluetooth Audio Fix**

> Porsche PCM3.1 (CHN) · QNX 6.3.x · SH-4A · 2026-07
> **✅ Solved — flashed and confirmed on the MOPF bench *and* on the real car (911/9x1).**
>
> **English** · [简体中文](README.zh-CN.md)

> ⚠️ **Disclaimer**: For study/research only. Flashing can brick your device **beyond recovery** (a watchdog endless-reboot, too fast to ever hold the emergency shell) — **use at your own risk; don't blame me.** 仅供学习研究,刷写有砖机风险且可能救不回来,后果自负。 Full text: [DISCLAIMER.md](DISCLAIMER.md) · License: [GPL-3.0](LICENSE)

![working bench](images/01-bench-working-fm.jpg)

> **The cost**: two bench units were **bricked and unrecoverable** earlier — a watchdog endless-reboot too fast to ever hold the emergency shell. That paid for the methodology (two-segment sum-zero preflight, plus knowing a "stable" brick is serial-recoverable but a "watchdog" brick is not); nothing has bricked since. See the "Prologue" in the journey doc.

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

---

## Part 3 · HW-layer overlay framework (2026-07-20)

![volume OSD drawn on our own hardware layer, over the stock Jukebox page](images/06-overlay-volume-osd.jpg)

*Our own volume OSD, drawn on an independent Carmine hardware layer, sitting on top of the untouched stock UI. Note that the stock page underneath keeps working normally — the progress bar advances, the clock ticks, the highlighted button stays highlighted. Nothing was flashed; the popup is pure runtime.*

- **Goal**: draw our own popups (volume OSD, toasts, dialogs) on top of the stock UI, **without flashing anything**.
- **Approach**: become a *second* gf client and take an idle Carmine hardware layer that `layermanager` never allocates. Each HW layer has its own scanout buffer → physically cannot collide with the stock UI's buffer or locks, so the ghosting/tearing of the old "share the stock surface" approach is gone at the hardware level.
- **Status**: verified on the bench — full colour, anti-aliased text, true rounded-corner transparency, the bar tracking the volume knob live, auto-dismiss after 1.4 s. Zero flash writes.
- **Three traps that each cost a day**: the driver **inverts layer numbers** (`hw = 7 − gf`, so use gf 5); the pixel format is **RGBA5551 despite the API reporting ARGB1555**; and **every `gf_layer_update` must be preceded by a full re-assert** or it deadlocks in `gdcServerCarmine` forever.
- Code: [`code/overlay/`](code/overlay/) · Full write-up: [`HW_overlay_framework.md`](HW_overlay_framework.md) ([简体中文](HW_overlay_framework.zh-CN.md))

## Deliverables

| | Location |
|---|---|
| **Code (tools + scripts)** | [`code/`](code/) — solution assembler, sh4emu, IFS pipeline, preflight, autorun scripts |
| **The solution** | [`code/build_shotgun_child_chain.py`](code/build_shotgun_child_chain.py) — the child-vtable cave with the self-derived `main` |
| Its predecessor (kept as the reference baseline) | [`code/build_shotgun_child.py`](code/build_shotgun_child.py) — same cave, but with the hardcoded `main` that drifts |
| Offline proof | [`code/validate_shotgun_child_chain.py`](code/validate_shotgun_child_chain.py) — runs the cave in `sh4emu` against the real `-2` connect-bug snapshot: it self-derives `main`, fires, and never faults (incl. deliberately corrupted pointers) |
| Preflight gate | [`code/verify_ifs_flashable.py`](code/verify_ifs_flashable.py) |
| Flashable firmware | **not included** — modified proprietary firmware; build your own from your dump with the tools in `code/` |

**Composition** = stock + fmguard (lock-BT) + child-chain cave (boot-sound)
**Status** = ✅ flashed and confirmed on the **bench** and on the **real car (911/9x1)**

> Note: the bench (MOPF / `IFS_G1_E2`) and the car (`IFS_9X1`) ship **byte-identical `PCM3Root` binaries** — only the surrounding imagefs differs. So the car got the exact same proven binary. Locate `PCM3Root` through the **imagefs directory** (`mnt/ifs1/HBproject/PCM3Root`), never by searching for the ELF header — every SH4 executable in the image starts with the same bytes, and you *will* extract the wrong file.

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
