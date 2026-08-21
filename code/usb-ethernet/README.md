# USB Ethernet — an AX88772B on a whitelist that does not list it

Get a network cable onto the bench instead of a 57600-baud serial line, using a card the
stock firmware refuses. **No flash write** — the patched driver is an ordinary file on the
HDD, loaded into a second `io-net` instance you start and can kill.

Full write-up: [`../../usb-ethernet.md`](../../usb-ethernet.md)
([简体中文](../../usb-ethernet.zh-CN.md)).

## Files

| file | what |
|---|---|
| `patch_asix_pid.py` | builds the patched `devn-asix.so` from **your own** stock copy — locates the chip table by signature, guards on the old value, prints every changed byte |
| `fx.c` | on-device file byte reader/writer with an old-value guard, so a one-byte experiment costs no USB round trip |

> ⚠️ **The driver binary is not in this repository** — `devn-asix.so` is proprietary
> Porsche firmware. Extract it from your own dump; `patch_asix_pid.py` takes it as input
> and writes a patched copy next to it.
>
> 驱动二进制不在此仓库(保时捷专有固件)。从你自己的 dump 里抠出来当输入。

## Four traps you would otherwise rediscover the hard way

### 1. ★★★ Two independent failures are stacked, and the outer one hides the inner one

`mount -Tio-net devn-asix.so` does not print an error — it takes `io-net` down with a
**SIGSEGV**. That crash is **not** about your unlisted card, and going straight for the PID
table is a trap: the patched driver crashes identically, and you conclude the PID patch
does not work.

**Mount the unmodified driver first and watch it crash the same way, same faulting `ip`.**
One minute, and it splits an impossible problem into two easy ones.

### 2. The driver's `DT_NEEDED` does not list what it needs

`devn-asix.so` declares only `libc.so.2`, but imports the whole USB DDK symbol set
(`udi_attach`, `udi_io`, `udi_enumerate`, `udi_dma_memory_alloc`, `udi_abort_pipe`, …).
It is only ever loaded by the stock path, which brings the DDK along; nothing in the module
says so. Load it yourself and you must supply it:

```sh
LD_PRELOAD=/lib/libusbdi.so.2 LD_LIBRARY_PATH=/mnt/data/drv \
  /proc/boot/io-net -i1 -dasixa speed=10,duplex=1 -ptcpip-v4 &
ifconfig en0 192.168.0.90 netmask 255.255.255.0 up
```

Use `-i1` (a **second** instance, `/dev/io-net1`) so the failure stays inside a process you
started. With the DDK present the crash becomes a *sentence* —
`unable to init dll devn-asix: No such device` — which is the driver's own chip table
talking, and that is what `patch_asix_pid.py` fixes.

### 3. The bench has one USB port

The network card and the USB stick cannot be plugged in at the same time. **Put the driver
on `/mnt/data` (the HDD, persistent) before you swap the stick for the card**, or you are
holding a patched driver you cannot deliver. `fx` exists for exactly this: once the file is
on the HDD, further byte edits need no stick at all.

### 4. `fx` reads `argc`/`argv`, so build it with a start stub that pulls them off the stack

Use [`../common/sh4tools/start_stack.S`](../common/sh4tools/start_stack.S) (or the
equivalent `crt.S` in an SH4 cross-build), **not** a `_start` that assumes `r4`/`r5` are
already set — those silently drop every argument, and `fx` then runs against whatever the
defaults are instead of telling you anything is wrong.

## Using the patcher

```sh
# see the chip table in your own driver
python3 patch_asix_pid.py --list stock/devn-asix.so

# build the patched driver (variant A — the one measured working on the bench)
python3 patch_asix_pid.py stock/devn-asix.so out/devn-asixa.so
```

It prints the table, the one byte it changed, and the `fx` command to undo it on-device.
Variant B (`--variant B`) retargets the plain AX88772 record instead; it was built as a
fallback and never needed.

## Using `fx` on the device

```
fx <file> <hex_off> <nwords>                  # read
fx <file> <hex_off> <expect_old_u32> <new>    # write; refuses if the old value differs
```

The write path prints the old value, verifies it against what you said to expect, writes,
reads back, and prints the exact command that undoes it. All values are little-endian u32.
