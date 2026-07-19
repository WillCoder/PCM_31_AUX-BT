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

```
# read one byte
mempoke <pid> 0x085c4c5c
# → old=0x5c   (whatever is live right now)

# write one byte, with readback
mempoke <pid> 0x085c4c5c 0x07
# → old=0x5c  new=0x07

# observe on the head unit, decide, poke again — seconds per turn, no reflash
```

Because it only ever `lseek`+`read`/`write`s `/proc/<pid>/as`, nothing is
flashed. Change → observe → revert → repeat. This is how the CPSoundPresCtrl
gate fields (`child+0x68`, `main+0x128`, …) were mapped bug-vs-working without a
single reflash.

Notes:
- **Don't `cat`/`dd` `/proc/<pid>/as`** on this QNX — it faults. `mempoke` does a
  bounded `lseek`+`read` of exactly the bytes you ask for, which is the safe way.
- **One byte at a time** by design — smallest blast radius. For a *range* scan
  (locate a code signature, then poke the immediate), see **`mempoke_fix.c`**: it
  sweeps `[start,end)` for the unique 8-byte FM-index-store signature and flips
  the `mov #1` immediate `01→07` in one shot — the signature-located, per-build-
  address-free form of the `{0,10}→FM` reroute.

## Pairs with the shotgun

For the boot-sound half, resolve `main` live off the dispatch object with the
runtime reverse-chain (no hardcoded, per-boot-drifting address):

```
main = *(*(*(child + 0x38) + 0x08) + 0x70)
```

then read the gate fields and poke — same loop, same `/proc/as` primitive.
`build_shotgun_child_chain.py` assembles the flashed version of that hook; the
loop here is how you dial it in before you commit it to flash.
