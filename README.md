# PCM_31_AUX-BT

**Porsche PCM3.1 Bluetooth Audio Fix**

> Porsche PCM3.1 (CHN/MOPF bench unit) · QNX 6.3.x · SH-4A · 2026-07
>
> **English** · [简体中文](README.zh-CN.md)

> ⚠️ **Disclaimer**: For study/research only. Flashing can brick your device **beyond recovery** (a watchdog endless-reboot, too fast to ever hold the emergency shell) — **use at your own risk; don't blame me.** 仅供学习研究,刷写有砖机风险且可能救不回来,后果自负。 Full text: [DISCLAIMER.md](DISCLAIMER.md) · License: [MIT](LICENSE)

![working bench](images/01-bench-working-fm.jpg)

> **The cost**: two bench units were **bricked and unrecoverable** earlier — a watchdog endless-reboot too fast to ever hold the emergency shell. That paid for the methodology (two-segment sum-zero preflight, plus knowing a "stable" brick is serial-recoverable but a "watchdog" brick is not); nothing has bricked since. See the "Prologue" in the journey doc.

Two chapters of my chain of pain:
- **① lock-BT** — playing Bluetooth, turn the car off; next trip it **always reverts to FM**, must manually re-select BT → fixed
- **② boot-sound** — with BT locked, the phone connects at boot **but there is no sound**, must manually AUX→BT → fixed

## Part 1 · lock-BT (the original pain)
- **Problem**: playing Bluetooth, turn the car off; next trip it **always reverts to FM**, must manually re-select Bluetooth.
- **Root cause / fix**: the real source arbitration is at runtime (not the OnOff/fallback dead-code layer). **fmguard = an 18-byte cave** (pool slot 0x082ac898) guarding: if the source being submitted is FM, don't submit → FM no longer auto-seizes. Confirmed by flashing on both the bench and the real car 911/9x1.

## Part 2 · boot-sound (Issue-1)
- **Problem**: with BT locked, the phone connects at boot **but there is no sound or track name**; must manually AUX→BT.
- **Root cause**: at boot, audio focus is never requested → the getter returns -2 → the establishment short-circuits → the DSP is not routed → silence. Only a genuine source change produces that request.
- **Solution**: **child-vtable shotgun** — instrument all 5 methods of the child object's vtable; whichever the connect event calls triggers, in the bug state, one AUX→BT → the establishment activates → sound.

**Outcome**: the two fixes combined into one clean firmware (stock + lock-BT + boot-sound, all failed experiments removed) + zero-serial USB autorun, confirmed by flashing on the bench.

📄 **Full journey — two chapters, every dead end and why: [journey_English.md](journey_English.md)** · 中文全过程见 [全过程_中文.md](全过程_中文.md)

---

## Deliverables

| | Location |
|---|---|
| **Code (tools + scripts)** | [`code/`](code/) — solution assembler, sh4emu, IFS pipeline, preflight, autorun scripts |
| The solution | [`code/build_shotgun_child.py`](code/build_shotgun_child.py) |
| Preflight gate | [`code/verify_ifs_flashable.py`](code/verify_ifs_flashable.py) |
| Clean firmware (cksum 4237630296) | **not included** — modified proprietary firmware; build your own from your dump with the tools in `code/` |

**Composition** = stock + fmguard (lock-BT) + child-shotgun (boot-sound)
**Next** = port to the real car 911/9x1

---

## Acknowledgments

Huge thanks to the following references — they gave this whole effort a solid foundation and direction:

- **[dspl1236/PCM-Forge](https://github.com/dspl1236/PCM-Forge)** — the PCM-Forge project and its reverse-engineering groundwork.
- **[Rennlist — "PCM3.1 reboot problem, repair attempt"](https://rennlist.com/forums/991/1484228-pcm3-1-reboot-problem-repair-attempt.html)** — the community thread on PCM3.1 reboot/repair.

And this was ultimately solved through the collaboration with **Claude (Anthropic)** — the reverse engineering, the tooling (reliable SH4 disassembly, `sh4emu`, the shotgun approach) and the final fix all came out of the back-and-forth, then tested hands-on on the bench. It took both halves to get here.
