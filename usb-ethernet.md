# PCM 3.1 · USB Ethernet — an AX88772B on a whitelist that does not list it

Replace the 57600-baud serial line and the USB-stick round trips with a **network cable**,
using a card the stock firmware refuses to touch. **Nothing is written to flash** — the
patched driver lives on the HDD and is loaded into a second `io-net` instance.

> **English** · [简体中文](usb-ethernet.zh-CN.md)

Measured on the bench 2026-08-21: `en0` up at 192.168.0.90, link negotiated to the Mac.

| Part | What is in it |
|---|---|
| [Part I — How it works](#part-i--how-it-works) | §1 why the bench needs this · §2 the whitelist · §3 two independent failures, stacked |
| [Part II — The solution](#part-ii--the-solution) | §4 bring the DDK in · §5 one byte in the chip table · §6 the recipe · §7 what it looks like working · §8 the tools |
| [Part III — Problems and dead ends](#part-iii--problems-and-dead-ends) | §9 dead ends · §10 lessons |

---

## Part I — How it works

### 1. Why the bench needs this

The bench unit's only debug channel is a **57600-baud serial console**. It works — the
whole serial toolchain in [`code/common/serial/`](code/common/serial/) is built on it — but
it is slow enough that pushing a 66 KB binary takes about a minute and a half, and fiddly
enough that every push has to be checksummed because a chunk can vanish silently (the
serial traps are catalogued in [volume-osd.md](volume-osd.md)). Everything else means
walking a USB stick over to the unit and back.

The firmware is already set up for the alternative: `/mnt/ifs1/etc/inetd.conf` runs **ftp
and telnet as root**, gated by the `/HBpersistence/DBGModeActive` marker file, which is
present on our bench. There is exactly one USB ethernet driver in the image,
`devn-asix.so` (`devn-rtl.so` is the PCI RTL8139, not USB). So the whole thing comes down
to getting one USB ethernet card recognised.

### 2. The whitelist matches exact VID/PID, with no wildcard

`/mnt/ifs1/etc/config/umass-enum-{1000,500}mA.cfg` binds USB ethernet by **exact VID/PID**,
four entries and no pattern:

| VID:PID | card |
|---|---|
| `0b95:7720` | AX88772 |
| `0b95:1720` | AX88172 |
| `2001:3c05` | D-Link DUB-E100 |
| `0846:1040` | NetGear |

The cfg's own comment names what will not work: *RTL8150, Pegasus, MCS7830 not supported*.

The card on the bench is an **AX88772B**, which reports `0b95:772b`. Not in the list. That
is not a fringe case: most cards sold today as "AX88772" are 772B, and the Apple USB
Ethernet adapter (`05ac:1402`, ASIX inside) is not in the list either.

`io-usb` enumerates the 772B perfectly — the `usb` command shows `0b95:772b AX88772B`, so
the cable, the hub and the port are all fine. It simply never gets bound to a driver.

**The 2026-07 verdict on this was "buy a card that natively matches one of the four; don't
chew on the 772B."** That verdict stood for six weeks. This document is the retraction.

### 3. ★★★ Two independent failures, stacked

This is the whole reason the July attempt stalled. Loading the driver by hand fails **for
two unrelated reasons at once**, and the outer one hides the inner one completely.

#### 3.1 The control group is the entire trick

`mount -Tio-net devn-asix.so` does not print an error. It takes `io-net` down with a
**SIGSEGV**. Three attempts, three crashes, **the same faulting `ip` (`0x7827391e`) every
time**.

The obvious story — "the driver rejected our unlisted card and died" — is wrong, and the
obvious next move — go patch the PID table — is a trap: the patched driver crashes exactly
the same way, and you conclude *the PID patch does not work* and abandon a route that was
fine.

What settles it costs one minute: **mount the unmodified driver and watch it crash
identically, same `ip`.** The crash has nothing to do with the PID table. Only then is it
worth asking what else is broken.

#### 3.2 Problem one — the driver's `DT_NEEDED` does not list what it needs

`devn-asix.so`'s only `DT_NEEDED` entry is **`libc.so.2`**. But it imports an entire USB
DDK symbol set:

```
udi_attach  udi_detach  udi_io  udi_enumerate  udi_frame  udi_hcd_info
udi_dma_memory_alloc  udi_dma_memory_free  udi_memory_info
udi_abort_pipe  udi_enumeration
```

Mounted into an `io-net` that has no `libusbdi` loaded, none of those resolve. The first
call through one of them jumps to a garbage address — which is the constant `ip` in every
one of the three crashes.

This is not a broken driver so much as one that never had to declare the dependency: the
stock load path brings the DDK along, so nothing ever forced the issue. Load it any other
way and you have to supply the DDK yourself.

#### 3.3 Problem two — the chip table has no 772B

With the DDK present, the driver initialises properly and then refuses at the **business
layer**, which is a completely different message from a crash:

```
unable to init dll devn-asix: No such device
```

That refusal comes from the driver's own chip table — a second, independent whitelist
inside the module, separate from the `umass-enum` cfg of §2. It lists exact VID/PID pairs
too, and it does not list `0b95:772b`.

---

## Part II — The solution

### 4. Bring the DDK in with `LD_PRELOAD`, in a second `io-net`

```
LD_PRELOAD=/lib/libusbdi.so.2 ...  /proc/boot/io-net -i1 ...
```

`-i1` starts **instance 1** (`/dev/io-net1`), so whatever is already running is left alone;
the whole experiment is confined to a process you started and can kill.

With `libusbdi` preloaded the SIGSEGV is gone and the driver reaches its own initialisation
— which is exactly what surfaces the §3.3 refusal. **A better error message is the
progress here**; the crash was hiding it.

### 5. One byte in the chip table

The table is **16-byte records, `[flags][vid][pid][chip marker]`, little-endian u32**. On
the bench binary it sits at file offset `0x10790`; the interesting stretch:

```
0x107a0  001f1d1f  2001 3c05  00088772   D-Link DUB-E100
0x107b0  000000b0  07d1 3c05  00088772
0x107c0  000000b0  0b95 1720  00088172   AX88172
0x107d0  001f1d1f  0b95 7720  00088772   AX88772
0x107e0  000000b0  0b95 772a  0088172a   AX88772A   <- retarget this one
0x107f0  000000b0  1737 0039  00088178
0x10800  3c1c3c8c  0846 1040  00088172   NetGear
0x10810  00130103  13b1 0018  00088772
```

**The patch: `@0x107e8`, PID `0x772a` → `0x772b`. One byte.** It retargets the AX88772**A**
record rather than adding a new one, which means the 772B inherits 772A's flags
(`0x000000b0`) and chip marker (`0x0088172a`) — the closest sibling in the table, and no
record count or terminator to keep consistent.

A second variant retargets the plain AX88772 record instead (`@0x107d8`, `0x7720 →
0x772b`, inheriting `0x001f1d1f` / `0x00088772`). It was built as a fallback and never
needed — variant A worked.

> Do not hardcode `0x107e8` for your own firmware.
> [`code/usb-ethernet/patch_asix_pid.py`](code/usb-ethernet/patch_asix_pid.py) locates the
> table by signature, checks the old value before writing, and reports every byte it
> changed. Both variants it emits are byte-for-byte identical to the images that ran on the
> bench.

### 6. The recipe

Zero flash writes. The driver is an ordinary file on the HDD.

```sh
# on the unit — the patched driver, under a NEW name so the stock one is untouched
cp <patched driver> /mnt/data/drv/devn-asixa.so

# second io-net instance, with the USB DDK preloaded and the HDD on the library path
LD_PRELOAD=/lib/libusbdi.so.2 LD_LIBRARY_PATH=/mnt/data/drv \
  /proc/boot/io-net -i1 -dasixa speed=10,duplex=1 -ptcpip-v4 &

ifconfig en0 192.168.0.90 netmask 255.255.255.0 up
```

```sh
# on the Mac — needs admin
sudo ifconfig <iface> inet 192.168.0.20 netmask 255.255.255.0 up
```

> 🚨 **The bench has one USB port.** The network card and the USB stick cannot be plugged
> in at the same time, so the driver has to be **on `/mnt/data` (the HDD, persistent)
> before you swap the stick for the card**. Get that order wrong and you are holding a
> patched driver you cannot deliver. This is also why `fx` (§8) exists — once the file is
> on the HDD, further byte edits need no stick at all.

### 7. What it looks like when it works

```
/dev/io-net1: en0 ip0 ip_en
en0: flags=8a43<UP,BROADCAST,RUNNING,ALLMULTI,SIMPLEX,MULTICAST> mtu 1500
     address: 00:6f:00:01:01:be
     inet 192.168.0.90 netmask 0xffffff00 broadcast 192.168.0.255
```

and on the Mac side:

```
media: autoselect (10baseT/UTP <full-duplex>)   status: active
```

`status: active` is the one that matters — the PHY negotiated with the other end, so this
is a real link, not just an interface record that exists.

### 8. The tools

| | |
|---|---|
| [`code/usb-ethernet/patch_asix_pid.py`](code/usb-ethernet/patch_asix_pid.py) | builds the patched driver from **your own** `devn-asix.so`: locates the chip table by signature, refuses if the old value does not match, prints every changed byte and the `fx` command to undo it |
| [`code/usb-ethernet/fx.c`](code/usb-ethernet/fx.c) | on-device file byte reader/writer with an old-value guard, so a one-byte experiment does not cost a USB round trip |
| Driver binary | **not included** — `devn-asix.so` is proprietary Porsche firmware. Extract it from your own dump; the patcher takes it as input |

---

## Part III — Problems and dead ends

### 9. Dead ends

| approach | verdict |
|---|---|
| **"Buy a card that natively matches one of the four PIDs"** (the 2026-07 conclusion) | **Retracted.** It is sound advice if you want to spend nothing; it is not true that the 772B cannot work. The route was abandoned for six weeks on the strength of a symptom that had two causes |
| Patch the PID table first, before anything else | **The trap.** The patched driver crashes identically, because the crash is the missing DDK. You conclude the PID patch failed and drop a correct fix |
| Change the mount parameters — `busnum` / `devnum`, `speed` / `duplex`, `verbose` | **No effect, three attempts, same `ip` every time.** Parameters cannot fix an unresolved symbol; the crash happens on the first call through one |
| Mount the driver into the existing `io-net` | Don't. Use `-i1` — a second instance keeps the failure inside a process you started |
| Read the crash as "the driver rejected our card" | Wrong on its face once you look: a rejection is `unable to init dll devn-asix: No such device`, printed by a live process. A SIGSEGV is not a policy decision |

### 10. Lessons

- **Run the control group before you fix anything.** Mounting the *unmodified* driver and
  watching it fail the same way costs one minute and is what split one impossible problem
  into two easy ones. Without it, the correct PID patch looks like a failed PID patch.
- **A stacked failure hides the inner one completely.** Nothing about the SIGSEGV hinted at
  a chip table, and nothing about the chip table hinted at a linking problem. The only
  signal that the first fix worked was that the *error message changed* — from a crash to a
  sentence.
- **`DT_NEEDED` is not the dependency list.** It is the list somebody remembered to
  declare. A module that is only ever loaded by one host can get away with declaring
  nothing, and it will fault, not complain, when you load it another way.
- **One byte, and it was on the HDD.** Nothing here touched flash, so the cost of being
  wrong was killing a process — which is why it was worth trying at all after six weeks of
  "just buy a different card".

---

## Appendix

### References

- Prior state of this question: the 2026-07 bench survey that produced the four-PID
  whitelist and the "buy a matching card" verdict.
- The serial loop this replaces: [`code/common/serial/`](code/common/serial/) and
  [`code/common/sh4tools/SERIAL_LOOP.md`](code/common/sh4tools/SERIAL_LOOP.md).
