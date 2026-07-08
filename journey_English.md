# PCM_31_AUX-BT — Porsche PCM3.1 Bluetooth Audio Fix, The Full Journey

> Porsche PCM3.1 head unit (CHN/MOPF variant, bench unit). QNX 6.3.x + SH-4A (SuperH, little-endian).
> This document records **an entire chain of real pain I lived through**, not a single bug. It starts from the most annoying everyday symptom, digs all the way down to SH4 disassembly and memory injection, and ends in one clean firmware — with two bricked bench units paid as tuition along the way.

![Live working bench: booted to FM, K-CUT serial (green LED) + USB + MacBook](images/01-bench-working-fm.jpg)
*The live working bench: PCM3.1 booted and running FM; bottom-right is a K-CUT USB-to-serial adapter (green LED on); the red/black/green jumpers are the 57600 serial line; the MacBook shows this project's task list.*

**The story in two chapters:**
- **Part 1** (the earliest, most annoying pain): playing Bluetooth, turn the car off; next time I get in, the head unit **always reverts to FM** and I have to manually switch back to Bluetooth audio. → Fixed by **lock-BT (fmguard)** (2026-07-06, confirmed by flashing on both the bench and the real car 911/9x1).
- **Part 2** (a new problem that surfaced once BT was locked): with Bluetooth held connected at boot, the phone connects **but there is no sound and no track name** — I have to manually do one AUX→BT to get sound. → Fixed by the **child-vtable shotgun** (2026-07-08, the main deep-dive of this document).
- Finally the two fixes are **combined into one clean firmware** (stock + lock-BT + boot-sound), deployed via a zero-serial USB autorun.

---

# Prologue: Two Bricked Benches (why I am so careful about flashing)

Before any of this, I had already **bricked two bench units in early flashing experiments — and could not recover them.**

They were not ordinary bricks — they **hit the watchdog**: after a bad image, the firmware fails to start, the watchdog times out and resets, and the unit **reboots endlessly**, resetting too fast to ever hold the IPL's emergency shell — no chance to reflash. Both units were dead for good, disassembled and scrapped.

![The bricked-bench graveyard: several disassembled PCM3.1 units](images/05-bench-graveyard.jpg)
*The cost: disassembled, scrapped bench units — metal chassis, the CD/DVD mechanism, main boards, and cooling fans scattered on the floor. A watchdog-driven endless-reboot brick cannot be recovered.*

That blood bought the methodology under which **nothing has bricked since** — the reason every "flash succeeded, no brick" in this document holds:

1. **Mandatory pre-flash preflight** (`dev/verify_ifs_flashable.py`): the PCM3.1 IPL requires the **startup segment and the imagefs segment to each independently sum-to-zero** (early on I only zeroed the whole file → that broke the startup segment → the IPL rejects it → a major source of bricking). Any non-zero segment = never flash.
2. **Knowing which bricks are recoverable and which are not**: it was later proven that a brick that **stays stable and does not hit the watchdog** (stuck on the logo / dead USB port but a stable IPL) can be revived over a 57600 serial line — reach the emergency shell, start io-usb + devb-umass, mount the USB, `flashit` the stock image back; but a **watchdog endless-reboot brick cannot be saved** — so I would rather have the preflight block it than gamble.
3. **FAT USB writes occasionally corrupt**: always cksum-verify after `cp` to the USB (during this very wrap-up a write did corrupt, cksum 3367337659 ≠ 4237630296, and was caught on the spot by run.sh's cksum gate).

![Debug / recovery setup: K-CUT serial + USB hub + the MacBook](images/02-bench-serial-debug.jpg)
*My debug/recovery setup today: the K-CUT USB-to-serial (green LED) goes through a USB hub to the MacBook; the red/black/green jumpers are the 57600 serial TX/RX/GND — this serial line is the lifeline that can recover a "stable" brick.*

![A replacement bench unit boxed, a](images/03-bench-boxed-a.jpg)
![A replacement bench unit boxed, b](images/04-bench-boxed-b.jpg)
*Replacement units procured after the two were bricked — only then could the project continue.*

> Keep this in mind — **watchdog endless-reboot = unrecoverable**. Throughout Parts 1 and 2 below, every flash was pressed only after passing the preflight and leaving the serial-recovery option open.

---

# Part 1: The Original Pain — Reverts to FM Every Time (lock-BT)

## 1.1 My original problem

Playing Bluetooth, turn the car off; next time I get in, the head unit **automatically reverts to the FM radio** every single time, and I have to dig into the menu to switch back to Bluetooth audio — **every single trip**. This was the starting point of the entire project.

## 1.2 The investigation (this chapter had its own dead ends)

- **First theory: an "A2DP→FM fallback master gate"** (0x082a4156): the belief that boot ran this code and forced the source back to FM. **Disproven**: that code is **dead** for boot — CPOnOffPresCtrl decides "not MME" and bypasses it entirely, so it never runs.
- **The LastMode theory**: on power-off, the head unit saves LastMode = 10 (Default) rather than 7 (BT); at boot, CPOnOffPresCtrl reads 10 → "not MME" → falls to FM. A "Solution D = read-side remap 10 → 7" was drafted.
- **Flashing A+B on the bench → still reverts to FM**: this flash was the turning point — it **proved that the Publishing/fallback layer is dead code for boot**, and that the real decision happens in the runtime **source-arbitration funnel** (inside CPSoundPresCtrl), not in the OnOff/fallback layer. It corrected a long-standing "patching the wrong layer" mistake (the same lesson Issue-1 would later lean on: **reproduce on live hardware, don't reason statically**).

## 1.3 Root cause + fix: fmguard (a source-arbitration guard)

The effective point is the runtime source arbitration: at boot (and when the phone disconnects), FM gets submitted as the source and displaces Bluetooth. **fmguard = an 18-byte cave**, hung on the arbitration pool slot **0x082ac898** (repointed to the cave) with the cave body at 0x083f2908~2918: **when the source being submitted is FM, don't submit it** — so FM can no longer auto-seize the source at boot/disconnect, and Bluetooth holds. Manual source switching is unaffected.

## 1.4 Confirmation

- **Flashed on the bench** and **flashed on the real car (911/9x1)**, both confirmed: disconnecting the phone / restarting the car **no longer drops back to FM**; Bluetooth holds.
- This is the origin of the **`lock-BT`** half of the clean firmware in this document.

> But once Bluetooth was locked, a subtler problem surfaced — **with Bluetooth held connected at boot, there is no sound**. That is Part 2.

---

# Part 2: BT Held, But Silent (Issue-1)

## 1. The Problem (Issue-1)

**Symptom**: When Bluetooth is held connected across a boot, the phone connects — the screen shows the "Bluetooth connected" popup and the phone name — **but there is no sound and no track name**.

**Two key clues I noticed** (which turned out to be the keys to the whole case):

1. **Only a genuine source-CHANGE restores sound**: In the bug state (silent), manually switching to **AUX and then back to Bluetooth (AUX→BT)** restores sound; but **re-selecting Bluetooth (BT→BT) does not**. So audio activation only fires on a real "source change."

2. **A precondition that exists only after being used once**: In the normal (sound-on) state, disconnecting the phone (the page holds, doesn't drop to FM) and reconnecting **recovers playback**; but in the bug state, reconnecting does **not** recover. So there is some state that is not established at boot but is present once the source has been used once.

**Goal**: Make Bluetooth auto-play with sound at boot (equivalent to automatically doing one AUX→BT).

---

## 2. System Background

- **Audio-source control architecture** (three layers):
  - L1 App layer: source enum (7=A2DP/BT, 11=FM, 24=AUX, …)
  - L2 SourceSinkSupervisor (a child sub-object): manages source-sink mapping and connections
  - L3 per-source EntertFSM: activation state machine
- **CPSoundPresCtrl** (the main object): control plane only (the SH4 side never touches the audio data stream).
- **Amplifier**: type is decided by an empty marker file `/HBpersistence/audioAmp{ASK,BOSE,BURMESTER}`. The bench has no physical amp, so an `audioAmpASK` (built-in) marker must exist for the asynchronous TLAM handshake of a source switch to complete and produce sound.
- **Bench vs. real car**: bench = Panamera (G1 hardware board); real car = 911 (9x1). Software cannot turn one into the other (hardware self-reports). This fix targets the bench MOPF binary.

---

## 3. Root Cause (decompile + live bench + sh4emu all agree)

**At boot, BT is set as the source but audio focus is never requested (`requestRequestAudioFocus`).** The chain:

```
manual AUX→BT
  → source-change function entertSourceChanged(0x082a717c) / vtable+0x34 (0x082a4854)
  → internally calls requestRequestAudioFocus
  → drives a request through the activation FSM to AUDIBLE (state = 5)

getter FUN_082a46b0:
  returns the source of the first list element whose state == 5 (AUDIBLE), else -2

establishment FUN_082a4e8c:
  GATE 1 = getter() != -2
  only then does it set up TLAM, requestRequestAudioFocus (state 7), command the DSP → sound
```

**How the bug arises**: at boot no source-change runs → the request list has no AUDIBLE element → getter returns **-2** → establishment **short-circuits** → no audio focus → **silent**.

**In one sentence**: a single "audio focus request" is missing, and that request is only produced by a genuine source change. This explains why manual AUX→BT works (it IS a genuine source change) and why re-selecting BT does not (it is not a real change).

---

## 4. The Investigation (every dead end, with the exact reason it failed)

> This section is the core value of the document — every dead end was tested on real hardware, each with its exact cause. **Do not walk these again.**

### Dead end ① Static field writes (mp2 writing heap directly)
Using mp2, I wrote all 6 fields that AUX→BT changes (`CHILD+0x68 -2→40` connected source, `CHILD+0x6c`, `MAIN+0x864`, `MAIN+0x86c`, `MAIN+0x94c`, `CHILD+0xc4` media cursor) to their normal-state values.
**Result: no sound.** And across disconnect + reconnect the fields **held** and it was still silent — hard proof.
**Reason**: those 6 fields are **results, not causes** of AUX→BT. The actual sound is a **side effect of the establishment commanding the DSP**; static field writes cannot reproduce it.

### Dead end ② Hooking vtable +0x44
I assumed the source-change was at main-object vtable +0x44 (0x082a7b40 = processCurrentEntertainmentSource). I built a cave replicating AUX→BT and flashed it.
**Result: never fired.** After BT connects at boot, +0x44 is simply not called.
**Reason**: wrong slot — the source-change is actually at **+0x34** (0x082a4854), and the connect event does not go through the main vtable at all (see dead end ⑥).

### Dead end ③ Runtime code injection (no flash)
I tried injecting the cave directly into the running process memory to avoid a flash.
**Result: `ERR write failed`.**
**Reason: the CoW wall (proven on real HW)**. mp2 (via /proc lseek+write) can write RW pages (.data/heap) but **fails on read-only code/rodata pages**. The bench also has no gdb/pdebug. So the cave can only go into flash, never a runtime injection.

### Dead end ④ Precise patch via Ghidra decompilation
I tried to patch the getter/establishment surgically.
**Result: couldn't proceed safely.**
**Reason: Ghidra's decompilation is unreliable** for this binary — function entry addresses are systematically misaligned (the real source-change entry is 0x082a4854, not the decompiler name 0x082a4838 — the first 28 bytes are data/a jump table); pool labels are wrong (a "pointer to the getter" actually pointed to a string). **Had to switch to a reliable tool** (objdump, §5).

### Dead end ⑤ vtrace dynamic instrumentation (log which method fires)
Plan: instrument all 20 main-vtable slots, each logging to a .data ring buffer; after connect, read the buffer to see which method fired.
**Result: nowhere to put the buffer.**
**Reason**: the entire .data segment is used at runtime by the program ("all zeros in the IFS" is an illusion — .data is filled at runtime), so there is no dead buffer region; and the system logger requires a complex context object that is too heavy to replicate in a cave.
**Turning point**: switch to the shotgun approach (below), using an existing field `main+0x94c` as the gate — no buffer needed.

### Dead end ⑥ Main-vtable shotgun (instrument all + gate, fix directly)
Instead of "locate then fix," instrument all 20 main-vtable methods, each gated (if in the bug state, trigger AUX→BT). Whichever one is called triggers the fix — bypassing the localization problem.
**Result: after BT connects at boot, main+0x94c stayed 1 = none of them fired.**
**But this was a valuable diagnostic**: doing a manual AUX→BT flipped main+0x94c to 0 = the main vtable IS exercised by the source-change path = **the shotgun mechanism works**.
**Conclusion**: **the connect handler is NOT in the main vtable.** The connect path and the source-change path are two independent code paths, and the connect does not trigger the main source-change — which is exactly the bug.

### ✅ Success ⑦ Child-vtable shotgun
Since the connect goes through the child (SourceSinkSupervisor) object (it modified `CHILD+0xc4`, the media cursor, on connect), **instrument the child's vtable**.
**Result: success!** At boot, holding BT → it connects → one of the child's vtable methods is called (in the bug state) → the gate passes → forced AUX→BT → the establishment activates → **sound**.

---

## 5. Tooling Breakthroughs (reusable going forward)

### 5.1 Reliable SH4 disassembly = Debian binutils-multiarch objdump
capstone garbles SH4 and Ghidra misaligns functions — only GNU objdump (2.35+, with SH support) is ground truth, and it even resolves pool values and string addresses.
```bash
# Build the image (once)
docker build --platform linux/386 -t sh4gdb:latest - <<'EOF'
FROM debian:bullseye-slim
RUN apt-get update -qq && apt-get install -y -qq libncurses5 binutils-multiarch
EOF
# Prep: carve PCM3Root's LOAD1 segment (vaddr 0x08040000) to raw text.bin
# Disassemble (--adjust-vma maps fileoff 0 → 0x08040000)
objdump -D -b binary -m sh4 -EL --adjust-vma=0x08040000 \
  --start-address=0x082a4854 --stop-address=0x082a4900 text.bin
```

### 5.2 sh4emu (an SH4 interpreter)
`dev/sh4emu.py` + `dev/sh4_run_switch.py`. Actually **executes arbitrary PCM3Root functions** against a 16 MB memory snapshot of the real car / bench — offline dynamic validation (cures blind static-RE misreads, costs no flash). Every hand-written cave is validated here (transparency, gate logic, recursion safety) before flashing.

### 5.3 IFS build pipeline
PCM3Root is per-file LZO-compressed inside the IFS1. `emulator-lab/bin/`: inflate → patch-file (exact-size replacement) → deflate (`--ref` preserves block shape + adds the outer sum-to-zero). **Preflight gate** `dev/verify_ifs_flashable.py`: both the startup segment and the imagefs segment must each sum32le to 0 — **this is the very gate paid for by the two bricked benches in the Prologue**; any non-zero segment is never flashed. Every flash later in this document passes it first.

### 5.4 Bench memory read/write
- Read: over serial, `hd -s <addr> -n <len> /proc/12316/as` (PID is always 12316).
- Write (RW only): `mp2` = `dev/sh4tools/mempoke_stack`, one byte per call.

---

## 6. The Solution: child-vtable shotgun

**Core idea**: do not locate "which specific method is the connect handler" (too hard); instead **instrument all 5 child-vtable methods and gate each** — whichever one is called in the bug state triggers one fix. Broad instrumentation bypasses the localization problem.

**Structure**: each vtable slot → a 12-byte stub (load the original method address into r0, `bra` to the shared routine) → the shared routine:

```
shared routine (r0 = original method, r4 = child):
  save r4-r7 / r0 / r14 / pr        ; transparent: preserve the original method's args
  r14 = 0x086ed694                  ; hardcoded main (deterministic address, fixed every boot)
  child = *(main+0x1f0)
  if child < 0x08600000  → skip     ; null / uninitialized safety
  if *(child+0x68) != -2 → skip     ; only in the "BT connected but not activated" bug state
  if *(main+0x94c) != 1  → skip     ; one-shot + recursion gate
  *(main+0x94c) = 0                 ; set 0 FIRST: entertSourceChanged re-enters instrumented
                                    ; child methods; setting 0 makes re-entrant calls skip,
                                    ; preventing infinite recursion
  entertSourceChanged(main, &24, &0)   ; switch to AUX
  entertSourceChanged(main, &40, &0)   ; switch back to BT
skip:
  restore pr / r14 / r0 / r4-r7
  jmp @r0                           ; transparent tail-call the original method
                                    ; (pr untouched, the method rts to the real caller)
```

**Why it works**: when BT connects at boot, the connect event calls one of the child's vtable methods. At that moment `child+0x68 == -2` (connected but not activated) and `main+0x94c == 1` (bug state), so the gate passes → one forced AUX→BT → the establishment activates → sound. `main+0x94c` doubles as the one-shot gate (goes to 0 after activation, never re-triggers) and the recursion gate.

**Confirmed on the bench**: main+0x94c=0 (fired once), child+0x68=40 (BT activated), source fields = BT, I heard sound.

Tool: `dev/build_shotgun_child.py` (assembler, adapted from the main-vtable `dev/build_shotgun.py`: vtable = 0x085700e0 / 5 slots / hardcoded main).

---

## 7. The Clean Final Firmware + USB autorun

### Composition
I wanted a clean version that keeps only the effective changes. Diffing the proven fmguard.ifs confirmed that lock-BT needs only 4 patches. So the clean firmware =

```
stock PCM3Root
  + fmguard (lock-BT, doesn't drop to FM on disconnect): pool slot 0x082ac898 → cave 0x083f2908~2918
  + child-shotgun (boot sound): 5 child-vtable slots + cave 0x0856484c
```

**Failed experiments removed**: `0x082a4147` (skip-a2dp, long proven to be boot dead code) and `0x082b65e0` (desiredApp lever, an unverified experiment) — the diff confirmed neither belongs to lock-BT, so they are safe to remove.

Clean IFS: `PCM3_IFS1_MOPF.CHN.clean.ifs`, cksum **4237630296**, size 10230208.

### USB autorun (zero-serial deployment)
Bundle = `copie_scr.sh` (the trigger, XOR-encoded, seed 0x001BE3AC) + `run.sh` (cksum gate + ARM gate + `flashit -v` + one-shot ARM deletion on success + logging) + `ARM_FLASH_CLEAN_BT_FIX.txt` (armed) + `payload/clean.ifs`.

**Timing is critical**: the USB must be inserted **after boot** to trigger autorun (a drive already present at boot is treated as plain storage and does not trigger). On insert → `proc_scriptlauncher` runs copie_scr.sh → run.sh verifies + flashes → writes `pcm_ran.txt = PCM_CLEAN_BTFIX_DONE`.

**Confirmed**: the autorun flashed fully automatically with `flashit_rc=0`, no serial.

> `flashit -a 0x001C0000` erases and rewrites the entire IFS1 region — it is not an additive patch — so flashing this clean version zeroes out ALL prior experiments (auxkick / c1vhook / the various shotguns / the two byte experiments) in one shot; the bench's PCM3Root becomes exactly stock + lock-BT + boot-sound. `/HBpersistence` (audioAmpASK, etc.) is outside the flashed region and must be preserved.

---

## 8. Key Addresses (bench MOPF binary, vaddr)

| Item | Address / value |
|---|---|
| PCM3Root base | vaddr 0x08040000, fileoff = vaddr − 0x08040000 |
| Main object CPSoundPresCtrl | **0x086ed694** (vtable 0x085c4c5c), **fixed every boot** |
| child (SourceSinkSupervisor) | *(main+0x1f0), **varies per boot** (seen 0x086dc19c / 0x086e2cfc) |
| child vtable | **0x085700e0** (5 real methods: 0x08110a84 / 081109c8 / 08111068 / 080930a4 / 0811096c) |
| entertSourceChanged (source-change) | **0x082a717c** (args: this, &src, &flag) |
| source-change real entry | 0x082a4854 (= main vtable +0x34; the decompiler name 0x082a4838 is data — wrong) |
| getter | 0x082a46b0 (returns the AUDIBLE source or -2) |
| establishment | around 0x082a4e8c (GATE 1: getter() != -2) |
| gate field child+0x68 | -2 = bug (connected but not activated) / 40 = activated |
| gate field main+0x94c | 1 = bug (not activated; also one-shot/recursion gate) / 0 = activated |
| source ids | AUX=24, BT=40 (eSRC_BT_A2DP) |
| fmguard lock-BT | pool slot 0x082ac898 + cave 0x083f2908~2918 |
| shotgun cave dead zone | 0x0856484c (869 free bytes in the RX segment) |

---

## 9. Lessons Learned

1. **Runtime code injection is infeasible on real HW** — read-only code pages are unwritable (CoW/RO), and without gdb the only path is a flash.
2. **Static field writes cannot reproduce function side effects** — the sound is a runtime action of the establishment commanding the DSP, not something a few state fields can fake.
3. **Ghidra's decompilation is unreliable for this binary** — misaligned entries / pool labels; objdump must be the ground truth.
4. **The shotgun approach is powerful**: when locating the exact handler is too hard, instrument an entire class's vtable and gate each — whichever fires in the target state triggers the fix. It bypasses localization, and a single flash is both the probe and the potential fix.
5. **Verify every assumption on live hardware**: the hunt for a dead buffer eventually revealed .data is fully used at runtime; the buffer, whether fields are live, the CoW wall — all were discovered only by measuring, never by static assumption.
6. **A recursion gate is mandatory**: the trigger function (entertSourceChanged) re-enters the instrumented vtable methods; without a one-shot gate it recurses infinitely and overflows the stack.
7. **Always pass preflight before flashing** (both segments sum-to-zero) — this is the root cause of bricking; FAT USB writes occasionally corrupt, so always cksum-verify after `cp`.

---

## 10. Next Step (the ultimate goal)

**Port to the real car, 911/9x1.** Bench = Panamera MOPF; the real car = 911 9x1, with different binary offsets. Use objdump to **re-locate** in the 9x1 binary: the main object address, the child vtable, entertSourceChanged, the gate-field offsets, and the dead-code zone; then apply the same fmguard + child-shotgun structure to rebuild a 9x1 IFS. A 9x1 lock-BT baseline (car9x1-fmboot) already exists as a reference.

---

## Appendix: Key Files

- `dev/build_shotgun_child.py` — child-vtable shotgun assembler (the solution)
- `dev/build_shotgun.py` — main-vtable version (diagnostic)
- `dev/build_auxkick_cave.py` — early AUX→BT cave template
- `dev/sh4emu.py` / `dev/sh4_run_switch.py` — SH4 interpreter + source-switch harness
- `dev/verify_ifs_flashable.py` — pre-flash preflight gate
- `emulator-lab/bin/{inflate,deflate}_ifs_lzo.py`, `patch_decomp_ifs_file.py` — IFS pipeline
- `firmware-cache/patch-lab/chn-clean-MOPF/PCM3_IFS1_MOPF.CHN.clean.ifs` — clean final firmware (cksum 4237630296)
- `firmware-cache/usb-builds/flash_clean_bt_fix/` — USB autorun bundle

---

## Acknowledgments

Huge thanks to the following references — they gave this whole effort a solid foundation and direction:

- **[dspl1236/PCM-Forge](https://github.com/dspl1236/PCM-Forge)** — the PCM-Forge project and its reverse-engineering groundwork.
- **[Rennlist — "PCM3.1 reboot problem, repair attempt"](https://rennlist.com/forums/991/1484228-pcm3-1-reboot-problem-repair-attempt.html)** — the community thread on PCM3.1 reboot/repair.

And this was ultimately solved through the collaboration with **Claude (Anthropic)** — the reverse engineering, the tooling (reliable SH4 disassembly, `sh4emu`, the shotgun approach) and the final fix all came out of the back-and-forth, then tested hands-on on the bench. It took both halves to get here.
