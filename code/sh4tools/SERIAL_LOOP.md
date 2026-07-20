# Live-serial mempoke loop — no-reflash iteration on the bench

The primitive the whole BT/AUX fix was found with: instead of building a patch,
flashing it (minutes, and a brick risk), and rebooting to test one hypothesis,
you poke the byte straight into **live** `PCM3Root` over a serial root shell and
watch the result. Each turn is seconds, nothing touches flash, and every change
is instantly reversible. `mempoke.c` is that poke; this is the loop around it.

> Bench only. The bench is serial-recoverable, so an exploratory poke that wedges
> the process is a reboot away from clean. **Do not** do exploratory pokes on a
> car you cannot serial-rescue — on the car, resolve/verify offline first, then
> flash a vetted patch.

## Setup (once)

1. **Serial root shell.** Debug UART at **57600 8N1**, no flow control → you land
   on a root shell on the bench PCM.
2. **Build `mempoke`.** Cross-compile on Linux, no QNX SDK needed:
   ```
   sh4-linux-gnu-gcc -c -O2 -ffreestanding -nostdlib -o mempoke.o mempoke.c
   ```
   then link *on the target* so it picks up crt + libc, passing `start_stack.S`
   so `argc`/`argv` actually arrive:
   ```
   cc -o mempoke start_stack.o mempoke.o          # /usr/bin/cc on the PCM
   ```
3. **Get it onto the box.** The bench debug NIC (ASIX AX88772 USB-ethernet) comes
   up as root telnet/ftp — `ftp` the binary over, `chmod +x mempoke`.
4. **Find the PID.** `pidin ar | grep PCM3Root` (or `ls /proc`). That number is
   the `<pid>` below; it changes every boot.

## The loop

Pick an **RW** address — a heap object field or `.data` (see the next section for
why that matters). Heap addresses drift every boot, so resolve them live rather
than hardcoding; the values below are from one particular boot.

```
# read one byte — here main+0x128, the "is it silent" field
mempoke <pid> 0x086ec144
# → old=0xfe   (-2: silent, the bug state)

# write one byte, with readback
mempoke <pid> 0x086ec144 0x28
# → old=0xfe  new=0x28

# observe on the head unit, decide, poke again — seconds per turn, no reflash
```

Because it only ever `lseek`+`read`/`write`s `/proc/<pid>/as`, nothing is
flashed. Change → observe → revert → repeat. This is how the CPSoundPresCtrl
gate fields (`child+0x68`, `main+0x128`, …) were mapped bug-vs-working without a
single reflash.

## What the loop can and can't reach

`/proc/<pid>/as` writes land on **RW pages only** — `.data`, heap. They **fail on
read-only code/rodata** with `ERR write failed`. That is the CoW wall, proven on
real hardware (see *Dead end ③* in the journey).

So this loop is for **observing and mapping live state**: reading fields, flipping
RW values, watching what the app does. It is **not** a way to install a fix. Both
caves live in the RX segment, so **runtime code injection is not possible on this
hardware** — no `mprotect`/`mmap`/`devctl` trick was found, and the bench has no
gdb/pdebug. Every cave ships by flashing IFS1.

Notes:
- **Don't `cat`/`dd` `/proc/<pid>/as`** on this QNX — it faults. `mempoke` does a
  bounded `lseek`+`read` of exactly the bytes you ask for, which is the safe way.
- **One byte at a time** by design — smallest blast radius. For a *range* scan
  (locate a code signature, then poke the immediate), see **`mempoke_fix.c`**: it
  sweeps `[start,end)` for an 8-byte signature and flips the `mov #1` immediate
  `01→07` in one shot.

  > ⚠️ **`mempoke_fix.c` is a scanning tool, not the fix.** The lever it targets
  > (`0x082b65e0`, the desiredApp immediate) was an **early experiment here — never
  > verified**. The clean-firmware diff confirmed it does **not** belong to lock-BT,
  > and it was **removed** from the final image. Use the tool for locating and
  > poking; do not treat that address as a working fix.

## Pairs with the shotgun

For the boot-sound half, resolve `main` live off the dispatch object with the
runtime reverse-chain (no hardcoded, per-boot-drifting address):

```
main = *(*(*(child + 0x38) + 0x08) + 0x70)
```

then read the gate fields and watch them move — same loop, same `/proc/as`
primitive. That is how the bug-vs-working field sets were mapped.

To be explicit about where the loop stops: **poking those fields does not produce
the fix.** The app never re-requests audio focus on its own, so writing the fields
*downstream* of the request doesn't un-stick it — the focus getter keeps returning
`-2`. The working fix is a cave that actually **calls** `entertSourceChanged`
inside the process, and per the CoW wall above that cave can only be installed by
flashing IFS1 (`build_shotgun_child_chain.py` assembles it). The loop is how you
dial it in; the flash is how you ship it.
