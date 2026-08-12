# PCM_31_AUX-BT

**Porsche PCM3.1 — two shipped modifications, and the toolkit that produced them**

> Porsche PCM3.1 (CHN) · QNX 6.3.x · SH-4A · 2026-08
> **✅ Both features are running on a real car (911/9x1), not just on the bench.**
>
> **English** · [简体中文](README.zh-CN.md)

> ⚠️ **Disclaimer**: For study/research only. Flashing can brick your device **beyond recovery** (a watchdog endless-reboot, too fast to ever hold the emergency shell) — **use at your own risk; don't blame me.** 仅供学习研究,刷写有砖机风险且可能救不回来,后果自负。 Full text: [DISCLAIMER.md](DISCLAIMER.md) · License: [GPL-3.0](LICENSE)

![working bench](images/01-bench-working-fm.jpg)

> **The cost**: two bench units were **bricked and unrecoverable** early on — a watchdog endless-reboot too fast to ever hold the emergency shell. That paid for the methodology (two-segment sum-zero preflight, plus knowing a "stable" brick is serial-recoverable but a "watchdog" brick is not); nothing has bricked since. See the Prologue in [bluetooth-fix.md](bluetooth-fix.md).

## What's in here

Two features, and the tooling both of them are built on.

| | What it does | Ships as | Status |
|---|---|---|---|
| **1 · [Bluetooth fix](bluetooth-fix.md)** | Bluetooth stays selected across a cold start — **and it makes sound**, with nothing to press | **IFS1 flash** | ✅ bench + real car |
| **2 · [Volume OSD](volume-osd.md)** | Our own popup drawn on an independent hardware layer, over the untouched stock UI | runtime app, **no flash** | ✅ bench + real car |
| Bench serial toolchain | 57600 root-shell dev loop — push a binary, run it, pull the log, kill it; no USB stick, no reflash | dev tooling | ✅ |
| RE + build pipeline | Reliable SH4 disassembly, `sh4emu` interpreter, IFS inflate/patch/deflate, pre-flash preflight gate | dev tooling | ✅ |

**How something ships is the first thing to know about it.** A flash patch can brick the unit and is the only way to change stock code ([why](#why-it-ships-as-a-flash)); a runtime app cannot brick anything and disappears on reboot.

Both feature documents are organised the same way, so you read either one the same way:

| | |
|---|---|
| **Part I — How it works** | how the stock system behaves, the hardware facts, the root cause. Conclusions only. |
| **Part II — The solution** | what was built, why it works, how it ships, how it was verified. |
| **Part III — Problems and dead ends** | every symptom, every dead end with the exact reason it failed, the traps, the lessons. |

---

# Feature 1 · Bluetooth — locked at boot, and it makes sound

📄 Full document: **[bluetooth-fix.md](bluetooth-fix.md)** · [简体中文](bluetooth-fix.zh-CN.md)

Two chapters of one chain of pain:

- **lock-BT** — playing Bluetooth, turn the car off; next trip it **always reverts to FM**, must manually re-select BT
- **boot-sound** — with BT locked, the phone connects at boot **but there is no sound**, must manually AUX→BT

**The end state, on the real car**: park, take the phone with you, come back, cold start — the phone reconnects and **the music comes back on its own, on Bluetooth, with nothing to press.**

## How it works

- **lock-BT root cause** — the real source arbitration is at runtime, not in the OnOff/fallback dead-code layer you reach for first. At boot (and when the phone disconnects) FM gets submitted as the source and displaces Bluetooth.
- **boot-sound root cause** — at boot, audio focus is never requested → the getter returns `-2` → the establishment short-circuits → the DSP is never routed → silence. Only a *genuine source change* produces that request, which is exactly why one manual AUX→BT fixes it.
- **The `+0x944` fork** — `entertSourceChanged` branches on `main+0x944`. **Zero** takes the durable child-dispatch leg (what a manual source tap uses — no teardown). **Non-zero** takes a fragile TLAM connect+retry leg whose async handler exhausts its retries ~5 s later and calls `switchAudio(Default)`, tearing the sound back down. The cave writes nothing to `+0x944`, so it always takes the durable leg.

### Why it ships as a flash

`/proc/<pid>/as` writes reach RW pages only and **fail on read-only code/rodata** — the CoW wall, proven on real hardware. Both caves live in the RX segment, so **runtime code injection is not possible on this hardware**. The serial poke loop is how the fix was *found*; flashing IFS1 is how it *ships*.

> The bench (MOPF / `IFS_G1_E2`) and the car (`IFS_9X1`) ship **byte-identical `PCM3Root` binaries** — only the surrounding imagefs differs, so the car got the exact same proven binary. Locate `PCM3Root` through the **imagefs directory** (`mnt/ifs1/HBproject/PCM3Root`), never by searching for the ELF header — every SH4 executable in the image starts with the same bytes, and you *will* extract the wrong file.

## The solution

**Composition** = stock `PCM3Root` + fmguard (lock-BT) + child-chain cave (boot-sound), delivered by **flashing IFS1**.

- **fmguard** — an **18-byte cave** on the arbitration pool slot `0x082ac898`: when the source being submitted is FM, don't submit it. FM can no longer auto-seize at boot or on disconnect. Manual source switching is unaffected.
- **child-vtable shotgun** — instrument all 5 methods of the child object's vtable; whichever one the connect event calls triggers, in the bug state, a single AUX→BT, which activates the establishment and produces sound.
- **The self-derived `main`** — the shotgun originally **hardcoded `main`** (the `CPSoundPresCtrl` singleton), which is a heap object that **drifts every boot** — six boots, six different addresses. A hardcoded address landing on an unmapped page faults on the *first* dereference → watchdog → the brick you cannot recover. The fix derives it from the one pointer that is always valid, the dispatch `this`:

  ```
  main = *(*(*(child + 0x38) + 0x08) + 0x70)
  ```

  Every hop is range-checked `[0x08600000, 0x08f00000)` **before** it is dereferenced, and the result is validated against the `CPSoundPresCtrl` vtable (`*(main) == 0x085c4c5c`), so an unpopulated or mid-relink state bails cleanly and retries on the next call instead of faulting. The chain is structural, not a coincidence: it resolves in every snapshot — bench playing / disconnected / AUX, the real `-2` connect-bug snapshot, the post-fix working state, and the real car — across all six distinct `main` addresses.

| | Location |
|---|---|
| **The solution** | [`code/bluetooth-fix/build_shotgun_child_chain.py`](code/bluetooth-fix/build_shotgun_child_chain.py) |
| Its predecessor (reference baseline) | [`code/bluetooth-fix/build_shotgun_child.py`](code/bluetooth-fix/build_shotgun_child.py) — same cave, hardcoded `main` |
| Offline proof | [`code/bluetooth-fix/validate_shotgun_child_chain.py`](code/bluetooth-fix/validate_shotgun_child_chain.py) — runs the cave in `sh4emu` against the real `-2` snapshot: self-derives `main`, fires, never faults (incl. deliberately corrupted pointers) |
| Preflight gate | [`code/common/verify_ifs_flashable.py`](code/common/verify_ifs_flashable.py) |
| USB autorun flasher | [`code/bluetooth-fix/autorun/`](code/bluetooth-fix/autorun/) — zero-serial deployment |
| Flashable firmware | **not included** — modified proprietary firmware; build your own from your own dump |

## Problems and dead ends

Six dead ends, each tested on real hardware, each with its exact cause: static field writes, hooking vtable `+0x44`, runtime code injection (the CoW wall), a precise patch via Ghidra decompilation (whose function entries are systematically misaligned on this binary), vtrace instrumentation, and a main-vtable shotgun that never fired once on reconnect.

**[→ Part III of bluetooth-fix.md](bluetooth-fix.md)** has all of them in the order they happened, plus the two bricked benches that opened the story and the lessons that came out of it.

---

# Feature 2 · Volume OSD on our own hardware layer

📄 Full document: **[volume-osd.md](volume-osd.md)** · [简体中文](volume-osd.zh-CN.md)

![volume OSD drawn on our own hardware layer, over the stock Jukebox page](images/06-overlay-volume-osd.jpg)

*Our own volume OSD, drawn on an independent Carmine hardware layer, sitting on top of the untouched stock UI. The stock page underneath keeps working normally — the progress bar advances, the clock ticks, the highlighted button stays highlighted. Nothing was flashed; the popup is pure runtime.*

## How it works

- **The approach** — become a *second* gf client and take a Carmine hardware layer that `layermanager` never allocates. Each HW layer has its own scanout buffer, so it **physically cannot** collide with the stock UI's buffer or locks; the ghosting and tearing of the old "share the stock surface" approach are gone at the hardware level.
- **You never own a hardware layer.** There is no ownership gate — the per-layer state is one shared record and the last writer wins.
- **Four hardware facts that each cost a day**: the driver **inverts layer numbers** (`hw = 7 − gf`); the pixel format is **RGBA5551 despite the API reporting ARGB1555**; **every `gf_layer_update` must be preceded by a full re-assert** or it deadlocks in `gdcServerCarmine`; and an alpha plane's **byte stride must be 64-byte aligned** or the whole panel shears into diagonal bands.

## The solution

- **A yield protocol** instead of layer ownership: watch your own layer's record every tick, and the moment the stock stack starts using it, *stop touching it entirely* until it goes idle again. This mirrors the OEM's own priority model and needs no knowledge of any vehicle's layer map — which matters, because **the layer map differs per vehicle model**. Hunting for "a layer nobody uses" is a dead end.
- **Engine / content split** — a resident engine plus a hot-reloadable `ui.def`. Changing layout or colour pushes 348 bytes of text: no recompile, no restart.
- **Verify with real screenshots, not by staring at the screen** — `pcmshot` grabs the framebuffer and `shotdiff` measures the popup's actual bounding box against what `ui.def` asked for.
- Code: [`code/volume-osd/`](code/volume-osd/)

> 🚨 **`panel_alpha` must be 255 on anything that drives.** `gdcServerCarmine` hands out alpha blending planes from a pool of four; a stock car already holds three at boot, and the release path is unreachable, so a plane once issued is **never returned until power-off**. Draw one translucent popup and the OEM parking-distance display cannot get a plane for the rest of the ignition cycle — its proximity zones render with correct geometry and soft edges, filled solid black. Measured on the bench by reading the server's own allocation ledger, not inferred; see **Trap 3** in [volume-osd.md](volume-osd.md).

## Problems and dead ends

The traps live next to the recipes they break, because that is where you need them. The genuinely dead ends — "pick a layer nobody uses", hiding a layer by painting it transparent, expecting `gf_layer_detach` to release anything — are collected in **[→ Part III of volume-osd.md](volume-osd.md)**.

---

# Shared tooling

Neither feature would exist without these, and both are built on them.

## Bench serial toolchain

The 57600 root-shell development loop — push a binary, run it, pull the log, kill it, **without a USB stick and without reflashing**.

- [`code/common/serial/`](code/common/serial/) — `ser_push.py` (chunked upload + cksum verify), `ser_pull.py`, `ser2.py` (run a command), `ser_kill.py`.
- [`code/common/sh4tools/SERIAL_LOOP.md`](code/common/sh4tools/SERIAL_LOOP.md) — the `/proc/<pid>/as` poke loop this pairs with, and **what it can and cannot reach**.

## Reverse-engineering + build

- [`code/common/`](code/common/) — `sh4emu.py` (an SH4 interpreter that runs real `PCM3Root` functions against a memory snapshot), the IFS inflate/patch/deflate pipeline, and `verify_ifs_flashable.py` (the pre-flash gate the two bricked benches paid for).
- [`code/common/sh4tools/`](code/common/sh4tools/) — on-device C tools: `mempoke.c` (the `mp2` byte reader/writer) and `alphatab.c` (the read-only alpha-plane ledger probe).
- Full index: [`code/README.md`](code/README.md).

---

## Repository layout

```
README.md / README.zh-CN.md        this index
bluetooth-fix.md   (+ .zh-CN.md)   feature 1, full document
volume-osd.md      (+ .zh-CN.md)   feature 2, full document
code/
  bluetooth-fix/                   caves, their offline validator, USB autorun flasher
  volume-osd/                      overlay engine, renderer, ui.def, screenshot verify
  common/                          sh4emu, IFS pipeline, serial toolchain, on-device C tools
images/                            bench photos and screenshots
```

## Adding a new feature

The layout absorbs new work without renumbering anything:

1. **Code** → its own `code/<feature>/` directory, with a short `README.md` inside covering the traps someone would otherwise rediscover the hard way. Anything a second feature could use goes in `code/common/` instead.
2. **Write-up** → `<feature>.md` at the repo root, plus `<feature>.zh-CN.md`. **English is the master, Chinese is the translation.**
3. **Use the same three parts** — *How it works* / *The solution* / *Problems and dead ends*. Readers should not have to learn a new shape for each feature.
4. **Index** → add one row to *What's in here* and one `#` section here, at the same level as the existing features. A shipped feature is never a subsection of another one.
5. **State how it ships** — runtime app or flash patch. That one fact sets the risk profile and is the first thing any reader needs.
6. **Keep the dead ends.** The "what I tried and why it failed" sections are the most valuable part of these documents; a feature without them will cost the next person the same days it cost you.

---

## Acknowledgments

Huge thanks to the following references — they gave this whole effort a solid foundation and direction:

- **[dspl1236/PCM-Forge](https://github.com/dspl1236/PCM-Forge)** — the PCM-Forge project and its reverse-engineering groundwork.
- **[Rennlist — "PCM3.1 reboot problem, repair attempt"](https://rennlist.com/forums/991/1484228-pcm3-1-reboot-problem-repair-attempt.html)** — the community thread on PCM3.1 reboot/repair.

And this was ultimately solved through the collaboration with **Claude (Anthropic)** — the reverse engineering, the tooling (reliable SH4 disassembly, `sh4emu`, the shotgun approach) and the final fixes all came out of the back-and-forth, then tested hands-on on the bench and in the car. It took both halves to get here.

---

## License

Copyright (C) 2026 WillCoder

This project is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public License v3.0** (or, at your option, any later version) — see [LICENSE](LICENSE). It is distributed in the hope that it will be useful, but **WITHOUT ANY WARRANTY** (see also [DISCLAIMER.md](DISCLAIMER.md)).
