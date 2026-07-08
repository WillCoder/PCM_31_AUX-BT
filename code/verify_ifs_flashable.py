#!/usr/bin/env python3
"""Pre-flash safety gate for PCM3.1 IFS images.

Checks the exact conditions the PCM3.1 IPL enforces during its flash scan before
it will accept an image as the bootable application IFS. Reversed from the bench
IPL (fs0p0.bin @ base 0x80000000):

    scan 0x80003a80 -> load/validate 0x80005318 -> checksum wrapper 0x8000527a
    -> kernel 0x8000523a  (a plain sum of little-endian u32 words)

The IPL requires BOTH of these independently:

    sum32le(startup[0 : startup_size])          == 0
    sum32le(imagefs[startup_size : stored_size]) == 0

A single whole-file sum-zero is NOT sufficient -- that is exactly what bricked
bench2 (rewriting stored_size@0x24 left the startup region summing to 8, so the
IPL rejected it with "Could not find an application IFS" and boot-looped).

Run this on any .ifs BEFORE flashing. PASS => the device scan gate accepts it.
This is a deterministic offline check; it does not require liblzo2 or the device.
"""
from __future__ import annotations

import argparse
import struct
import sys


def sum32le(b: bytes) -> int:
    pad = (-len(b)) % 4
    b = bytes(b) + b"\0" * pad
    return sum(struct.unpack(f"<{len(b) // 4}I", b)) & 0xFFFFFFFF


def check(path: str) -> bool:
    d = open(path, "rb").read()
    if len(d) < 0x34 or struct.unpack_from("<I", d, 0)[0] != 0x00FF7EEB:
        print("  FAIL: not a QNX IFS (bad magic @0x00)")
        return False
    ss = struct.unpack_from("<I", d, 0x20)[0]
    stored = struct.unpack_from("<I", d, 0x24)[0]
    if not (0 < ss < stored <= len(d)):
        print(f"  FAIL: bad startup_size/stored_size (0x{ss:x}/0x{stored:x}, file {len(d)})")
        return False
    # 4-byte alignment is REQUIRED. The device checksum kernel (0x8000523a) reads
    # full words via `mov.l @Rm+`; if a segment length is not a multiple of 4, the
    # device sums real flash bytes PAST the segment end, while sum32le() below
    # zero-pads the tail -- so a non-aligned image can pass here yet FAIL on-device
    # (adversarial-review gap #1, 2026-07-06). Refuse rather than give a false PASS.
    if ss % 4 != 0 or stored % 4 != 0:
        print(f"  FAIL: startup_size/stored_size not 4-byte aligned (0x{ss:x}/0x{stored:x}); "
              "device sums bytes past the segment -> offline sum32le is NOT trustworthy here")
        return False
    s0 = sum32le(d[:ss])
    s1 = sum32le(d[ss:stored])
    sw = sum32le(d[:stored])
    print(f"  startup_size = 0x{ss:x}   stored_size = 0x{stored:x}   file = {len(d)}")
    print(f"  sum(startup [0:0x{ss:x}))          = 0x{s0:08x}   {'PASS' if s0 == 0 else 'FAIL <-- device rejects'}")
    print(f"  sum(imagefs [0x{ss:x}:0x{stored:x})) = 0x{s1:08x}   {'PASS' if s1 == 0 else 'FAIL <-- device rejects'}")
    print(f"  sum(whole)                          = 0x{sw:08x}   (device does NOT check this alone)")
    if s0 == 0 and s1 == 0:
        print("  RESULT: PASS -- device IPL scan gate will accept this image")
        return True
    print("  RESULT: FAIL -- device IPL rejects with 'Could not find an application IFS' (this bricked bench2)")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ifs", nargs="+", help="one or more .ifs images to check")
    args = ap.parse_args()
    all_ok = True
    for p in args.ifs:
        print(f"=== {p} ===")
        all_ok &= check(p)
        print()
    print("OVERALL:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
