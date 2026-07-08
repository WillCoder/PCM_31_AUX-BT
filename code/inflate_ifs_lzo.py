#!/usr/bin/env python3
"""Inflate PCM3.1/QNX LZO IFS images with liblzo2.

This helper is intentionally LZO-only. It produces the same container shape
expected by MMI3G-Toolkit's patch_ifs.py:

    startup section copied verbatim + uncompressed imagefs payload
"""

from __future__ import annotations

import argparse
import ctypes
import struct
from pathlib import Path


FLAGS1_COMPRESS_MASK = 0x1C
FLAGS1_COMPRESS_SHIFT = 2
COMPRESS_LZO = 2
DEFAULT_LZO_DYLIB = Path("/opt/homebrew/opt/lzo/lib/liblzo2.dylib")


class LzoCallback(ctypes.Structure):
    _fields_ = [
        ("nalloc", ctypes.c_void_p),
        ("nfree", ctypes.c_void_p),
        ("nprogress", ctypes.c_void_p),
        ("user1", ctypes.c_void_p),
        ("user2", ctypes.c_ulong),
        ("user3", ctypes.c_ulong),
    ]


def load_lzo(path: Path) -> ctypes.CDLL:
    lib = ctypes.CDLL(str(path))

    init = lib.__lzo_init_v2
    init.argtypes = [
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    init.restype = ctypes.c_int

    lib.lzo_version.argtypes = []
    lib.lzo_version.restype = ctypes.c_uint

    rc = init(
        lib.lzo_version(),
        ctypes.sizeof(ctypes.c_short),
        ctypes.sizeof(ctypes.c_int),
        ctypes.sizeof(ctypes.c_long),
        ctypes.sizeof(ctypes.c_uint32),
        ctypes.sizeof(ctypes.c_ulong),
        ctypes.sizeof(ctypes.c_void_p),
        ctypes.sizeof(ctypes.c_char_p),
        ctypes.sizeof(ctypes.c_void_p),
        ctypes.sizeof(LzoCallback),
    )
    if rc != 0:
        raise SystemExit(f"lzo init failed: {rc}")

    lib.lzo1x_decompress.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_void_p,
    ]
    lib.lzo1x_decompress.restype = ctypes.c_int
    return lib


def decompress_chunk(lib: ctypes.CDLL, chunk: bytes, chunk_index: int) -> bytes:
    out_capacity = 4 * 1024 * 1024
    src = ctypes.create_string_buffer(chunk)
    dst = ctypes.create_string_buffer(out_capacity)
    out_len = ctypes.c_ulong(out_capacity)

    rc = lib.lzo1x_decompress(
        src,
        len(chunk),
        dst,
        ctypes.byref(out_len),
        None,
    )
    if rc != 0:
        raise SystemExit(f"chunk {chunk_index}: lzo1x_decompress failed rc={rc}")
    return dst.raw[: out_len.value]


def inflate_ifs(input_path: Path, output_path: Path, lzo_path: Path) -> None:
    data = input_path.read_bytes()
    if len(data) < 0x34:
        raise SystemExit(f"{input_path} is too small to be a QNX IFS")
    if struct.unpack_from("<I", data, 0)[0] != 0x00FF7EEB:
        raise SystemExit(f"{input_path} does not start with QNX IFS magic")

    flags1 = data[6]
    cmptype = (flags1 & FLAGS1_COMPRESS_MASK) >> FLAGS1_COMPRESS_SHIFT
    startup_size = struct.unpack_from("<I", data, 0x20)[0]
    stored_size = struct.unpack_from("<I", data, 0x24)[0]
    imagefs_size = struct.unpack_from("<I", data, 0x2C)[0]
    if cmptype != COMPRESS_LZO:
        raise SystemExit(f"unsupported compression type {cmptype}; this helper is LZO-only")

    lib = load_lzo(lzo_path)
    output = bytearray(data[:startup_size])

    pos = startup_size
    chunks = 0
    total = 0
    limit = min(stored_size, len(data)) if stored_size else len(data)
    while pos + 2 <= limit:
        clen = (data[pos] << 8) | data[pos + 1]
        pos += 2
        if clen == 0:
            break
        if pos + clen > limit:
            raise SystemExit(f"chunk {chunks}: length {clen} exceeds stored_size")
        chunk = data[pos : pos + clen]
        pos += clen
        dec = decompress_chunk(lib, chunk, chunks)
        output.extend(dec)
        total += len(dec)
        chunks += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)

    print(
        f"flags1=0x{flags1:02x} compression=lzo startup_size=0x{startup_size:x} "
        f"stored_size=0x{stored_size:x} imagefs_size=0x{imagefs_size:x}"
    )
    print(f"decompressed {chunks} chunks -> {total} imagefs bytes")
    if imagefs_size and total != imagefs_size:
        print(f"warning: header imagefs_size={imagefs_size}, decompressed={total}")
    print(f"wrote {len(output)} bytes -> {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ifs", type=Path)
    parser.add_argument("-o", "--out", required=True, type=Path)
    parser.add_argument("--lzo", type=Path, default=DEFAULT_LZO_DYLIB)
    args = parser.parse_args()

    inflate_ifs(args.ifs, args.out, args.lzo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
