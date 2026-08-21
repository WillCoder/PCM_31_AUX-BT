#!/usr/bin/env python3
"""
patch_asix_pid.py — teach the PCM 3.1 ASIX USB-ethernet driver about a card whose
                    USB PID is not in its built-in chip table.

Symptom this fixes
------------------
`devn-asix.so` refuses an AX88772B (`0b95:772b`) with

    unable to init dll devn-asix: No such device

even though io-usb enumerates the card perfectly. That refusal comes from the
driver's own chip table, which lists exact VID/PID pairs and has no wildcard.

⚠️ This is only HALF the story. On the PCM the driver also has to be loaded into
an io-net that has the USB DDK available, or it SIGSEGVs long before it ever looks
at this table — `devn-asix.so`'s DT_NEEDED is `libc.so.2` only, while it imports
`udi_attach` / `udi_io` / `udi_enumerate` / ... from `libusbdi`. See
`code/usb-ethernet/README.md` and `usb-ethernet.md` for the LD_PRELOAD half.

The chip table
--------------
16-byte records, `[flags][vid][pid][chip marker]`, all little-endian u32. On the
bench binary the interesting stretch starts at file offset 0x107a0:

    0x107a0  001f1d1f  2001 3c05  00088772   D-Link DUB-E100
    0x107b0  000000b0  07d1 3c05  00088772
    0x107c0  000000b0  0b95 1720  00088172   AX88172
    0x107d0  001f1d1f  0b95 7720  00088772   AX88772
    0x107e0  000000b0  0b95 772a  0088172a   AX88772A   <- variant A retargets this one
    0x107f0  000000b0  1737 0039  00088178
    0x10800  3c1c3c8c  0846 1040  00088172   NetGear
    0x10810  00130103  13b1 0018  00088772

This script does NOT trust that offset — it locates the table by signature, so it
still works on a differently-built `devn-asix.so`.

Variants
--------
  A (default, the one measured working on the bench 2026-08-21)
      retarget the AX88772A record: PID 0x772a -> 0x772b.
      ONE byte changes. Keeps 772A's flags (0x000000b0) and chip marker (0x0088172a).
  B   retarget the plain AX88772 record: PID 0x7720 -> 0x772b.
      Also one byte, but it inherits 772A's *predecessor's* init path
      (flags 0x001f1d1f, chip marker 0x00088772). Built as a fallback; A worked, so
      B was never needed. Keep it if A ever stops linking up.

Usage
-----
    python3 patch_asix_pid.py --list  stock/devn-asix.so
    python3 patch_asix_pid.py stock/devn-asix.so out/devn-asixa.so
    python3 patch_asix_pid.py --variant B --pid 0x772b stock/devn-asix.so out/devn-asixb.so

The input is YOUR OWN `devn-asix.so`, extracted from YOUR OWN firmware dump. No
Porsche firmware — stock or patched — ships with this repository.
"""

import argparse
import struct
import sys

REC = 16  # bytes per chip-table record


def u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def record(buf, off):
    """(flags, vid, pid, chip) of the 16-byte record at `off`."""
    return struct.unpack_from("<4I", buf, off)


def looks_like_record(buf, off):
    if off < 0 or off + REC > len(buf):
        return False
    _flags, vid, pid, chip = record(buf, off)
    if not (0 < vid <= 0xFFFF) or not (0 < pid <= 0xFFFF):
        return False
    # every chip marker seen in the table is 0x0008xxxx or 0x0088xxxx
    return (chip & 0xFFFF0000) in (0x00080000, 0x00880000)


def find_table(buf):
    """Locate the chip table by signature and return (start, count).

    Anchor on the AX88172 record (vid 0x0b95, pid 0x1720, chip 0x00088172): it is
    present in every build we have seen and neither patch variant touches it. Then
    grow the window in both directions for as long as the records validate.
    """
    anchor = None
    for off in range(0, len(buf) - REC, 4):
        _flags, vid, pid, chip = record(buf, off)
        if vid == 0x0B95 and pid == 0x1720 and chip == 0x00088172:
            anchor = off
            break
    if anchor is None:
        raise SystemExit(
            "!! chip table not found — is this really a devn-asix.so? "
            "(anchor record 0b95:1720 / chip 0x00088172 is missing)"
        )

    start = anchor
    while looks_like_record(buf, start - REC):
        start -= REC
    end = anchor + REC
    while looks_like_record(buf, end):
        end += REC
    return start, (end - start) // REC


def dump_table(buf, start, count):
    print(f"chip table: {count} records at file offset 0x{start:x}")
    print("   offset    flags     vid  pid   chip marker")
    for i in range(count):
        off = start + i * REC
        flags, vid, pid, chip = record(buf, off)
        print(f"  0x{off:06x}  {flags:08x}  {vid:04x} {pid:04x}  {chip:08x}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1].strip())
    ap.add_argument("infile", help="stock devn-asix.so from your own firmware dump")
    ap.add_argument("outfile", nargs="?", help="where to write the patched driver")
    ap.add_argument("--list", action="store_true", help="print the chip table and exit")
    ap.add_argument("--variant", choices=("A", "B"), default="A",
                    help="A (default) retargets the 0b95:772a record; B retargets 0b95:7720")
    ap.add_argument("--pid", default="0x772b",
                    help="PID to install (default 0x772b = AX88772B)")
    args = ap.parse_args()

    orig = open(args.infile, "rb").read()
    buf = bytearray(orig)
    start, count = find_table(buf)

    if args.list:
        dump_table(buf, start, count)
        return

    if not args.outfile:
        ap.error("outfile is required unless --list is given")

    new_pid = int(args.pid, 0)
    if not 0 < new_pid <= 0xFFFF:
        ap.error(f"--pid {args.pid} is not a 16-bit USB product id")

    old_vid, old_pid = (0x0B95, 0x772A) if args.variant == "A" else (0x0B95, 0x7720)

    target = None
    for i in range(count):
        off = start + i * REC
        _flags, vid, pid, _chip = record(buf, off)
        if (vid, pid) == (old_vid, old_pid):
            target = off
            break
    if target is None:
        raise SystemExit(
            f"!! no record for {old_vid:04x}:{old_pid:04x} — either this driver has a "
            f"different table, or it has already been patched. Run with --list."
        )

    for i in range(count):
        _flags, vid, pid, _chip = record(buf, start + i * REC)
        if (vid, pid) == (old_vid, new_pid):
            raise SystemExit(f"!! {old_vid:04x}:{new_pid:04x} is already in the table — nothing to do")

    pid_off = target + 8
    before = u32(buf, pid_off)
    if before != old_pid:
        raise SystemExit(f"!! guard failed: expected 0x{old_pid:08x} at 0x{pid_off:x}, found 0x{before:08x}")

    struct.pack_into("<I", buf, pid_off, new_pid)

    dump_table(buf, start, count)
    changed = [o for o in range(len(orig)) if orig[o] != buf[o]]
    print()
    print(f"variant {args.variant}: record 0x{target:06x}  "
          f"PID 0x{old_pid:04x} -> 0x{new_pid:04x}   ({len(changed)} byte(s) changed in the whole file)")
    for o in changed:
        print(f"  @0x{o:06x}  {orig[o]:02x} -> {buf[o]:02x}")
    print()
    print("on-device equivalent / undo, using code/usb-ethernet/fx.c:")
    print(f"  fx <driver> 0x{pid_off:x} 0x{old_pid:08x} 0x{new_pid:08x}   # apply")
    print(f"  fx <driver> 0x{pid_off:x} 0x{new_pid:08x} 0x{old_pid:08x}   # undo")

    with open(args.outfile, "wb") as f:
        f.write(buf)
    print(f"\nwrote {args.outfile} ({len(buf)} bytes)")


if __name__ == "__main__":
    sys.exit(main())
