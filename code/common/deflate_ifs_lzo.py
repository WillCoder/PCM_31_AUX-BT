#!/usr/bin/env python3
"""Re-compress a decompressed QNX/PCM3.1 IFS back into the LZO container.

Container shape (must match what inflate_ifs_lzo.py decodes and the PCM IPL
loads): startup section verbatim, then a stream of chunks, each

    [2-byte big-endian compressed length][LZO-compressed block]

terminated by a 0x0000 length word. Each block decompresses to a slice of the
imagefs byte stream. The terminator is followed by zero padding to a 32-bit
boundary and a 32-bit little-endian checksum word that makes the whole stored
IFS image sum to zero. Chunk *boundaries* don't affect decompression correctness
(the IPL concatenates blocks), but to stay byte-shape-identical to the original
build we reuse the exact decompressed-block-size sequence carried in a reference
.ifs (the stock image for this variant). Only the compressed bytes differ.

PCM3.1 IPL rejects images whose stored IFS image does not have this outer
sum-zero checksum. The separate IFS1_*.CRC32 files belong to SWDL/update media;
they are not a substitute for the checksum word embedded at the end of the .ifs.

Usage:
  deflate_ifs_lzo.py DECOMP --ref STOCK.ifs -o OUT.ifs [--method 999|1]
"""
from __future__ import annotations

import argparse
import ctypes
import struct
from pathlib import Path

import inflate_ifs_lzo as L  # reuse loader + decompress for boundary discovery & roundtrip


def chunk_decsizes(ref_ifs: Path, lib) -> list[int]:
    data = ref_ifs.read_bytes()
    startup_size = struct.unpack_from("<I", data, 0x20)[0]
    stored_size = struct.unpack_from("<I", data, 0x24)[0]
    pos = startup_size
    limit = min(stored_size, len(data)) if stored_size else len(data)
    sizes: list[int] = []
    while pos + 2 <= limit:
        clen = (data[pos] << 8) | data[pos + 1]
        pos += 2
        if clen == 0:
            break
        dec = L.decompress_chunk(lib, data[pos : pos + clen], len(sizes))
        sizes.append(len(dec))
        pos += clen
    return sizes


def bind_compress(lib, method: str):
    fn = lib.lzo1x_999_compress if method == "999" else lib.lzo1x_1_compress
    fn.argtypes = [
        ctypes.c_void_p,            # src
        ctypes.c_ulong,             # src_len
        ctypes.c_void_p,            # dst
        ctypes.POINTER(ctypes.c_ulong),  # dst_len
        ctypes.c_void_p,            # wrkmem
    ]
    fn.restype = ctypes.c_int
    return fn


def compress_block(fn, wrkmem, block: bytes) -> bytes:
    src = ctypes.create_string_buffer(block, len(block))
    dst_cap = len(block) + len(block) // 16 + 128
    dst = ctypes.create_string_buffer(dst_cap)
    dst_len = ctypes.c_ulong(dst_cap)
    rc = fn(src, len(block), dst, ctypes.byref(dst_len), wrkmem)
    if rc != 0:
        raise SystemExit(f"lzo compress failed rc={rc}")
    return dst.raw[: dst_len.value]


def sum32le(data: bytes | bytearray) -> int:
    pad = (-len(data)) % 4
    return sum(struct.unpack(f"<{(len(data) + pad) // 4}I", bytes(data) + b"\0" * pad)) & 0xFFFFFFFF


def deflate(decomp_path: Path, ref_path: Path, out_path: Path, method: str, lzo_path: Path) -> None:
    lib = L.load_lzo(lzo_path)
    # decompress is needed for roundtrip + boundary discovery
    lib.lzo1x_decompress.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p,
    ]
    lib.lzo1x_decompress.restype = ctypes.c_int
    cfn = bind_compress(lib, method)
    wrkmem = ctypes.create_string_buffer(16 * 1024 * 1024)  # generous for 999

    decomp = decomp_path.read_bytes()
    startup_size = struct.unpack_from("<I", decomp, 0x20)[0]
    startup = bytearray(decomp[:startup_size])
    imagefs = decomp[startup_size:]

    sizes = chunk_decsizes(ref_path, lib)
    if sum(sizes) != len(imagefs):
        raise SystemExit(
            f"reference block sizes sum to {sum(sizes)} but decomp imagefs is "
            f"{len(imagefs)}; ref .ifs does not match this decomp layout"
        )

    body = bytearray()
    off = 0
    for i, dsz in enumerate(sizes):
        block = imagefs[off : off + dsz]
        off += dsz
        comp = compress_block(cfn, wrkmem, block)
        if len(comp) > 0xFFFF:
            raise SystemExit(f"chunk {i} compressed length {len(comp)} exceeds 16-bit field")
        # verify this chunk roundtrips before committing
        back = L.decompress_chunk(lib, comp, i)
        if back != block:
            raise SystemExit(f"chunk {i} failed roundtrip self-check")
        body += struct.pack(">H", len(comp))
        body += comp
    body += struct.pack(">H", 0)

    out = bytearray(startup) + body
    out += b"\0" * ((-len(out)) % 4)
    final_size = len(out) + 4
    struct.pack_into("<I", out, 0x24, final_size)  # stored_size includes the checksum word
    # The PCM3.1 IPL validates TWO independent regions during its flash scan
    # (reversed: IPL 0x80005318 -> checksum wrapper 0x8000527a -> kernel 0x8000523a,
    # a plain sum of little-endian u32 words). It requires BOTH:
    #   sum32le(startup[0:startup_size]) == 0   AND
    #   sum32le(imagefs[startup_size:stored_size]) == 0
    # A single whole-file sum-zero is NOT sufficient: bench2 bricked exactly here.
    # Rewriting stored_size@0x24 sits inside the startup region, so it left startup
    # summing to 8, and the IPL rejected the image with "Could not find an
    # application IFS". Re-zero the startup checksum word (last u32 of the startup
    # section) after the 0x24 rewrite, then seal the imagefs region with its own
    # trailing checksum word.
    cw_off = startup_size - 4
    cw = struct.unpack_from("<I", out, cw_off)[0]
    struct.pack_into("<I", out, cw_off, (cw - sum32le(out[:startup_size])) & 0xFFFFFFFF)
    out += struct.pack("<I", (-sum32le(out[startup_size:])) & 0xFFFFFFFF)
    if sum32le(out[:startup_size]) != 0 or sum32le(out[startup_size:]) != 0:
        raise SystemExit("per-region IFS checksum generation failed")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out)

    # full roundtrip: inflate the produced file and compare to input decomp
    tmp = out_path.with_suffix(out_path.suffix + ".roundtrip.decomp")
    L.inflate_ifs(out_path, tmp, lzo_path)
    rt = bytearray(tmp.read_bytes())
    tmp.unlink()
    # Legitimate differences vs the input decomp: stored_size(0x24), which we
    # rewrote to the new compressed length, and the startup checksum word, which
    # we adjusted to re-zero the startup region. Normalize both before comparing so
    # the check verifies the decompressed image is otherwise bit-identical.
    ref = bytearray(decomp)
    struct.pack_into("<I", ref, 0x24, len(out))
    struct.pack_into("<I", ref, startup_size - 4,
                     struct.unpack_from("<I", out, startup_size - 4)[0])
    ok = bytes(rt) == bytes(ref)

    print(f"method=lzo1x_{method}")
    print(f"input decomp: {decomp_path} ({len(decomp)} bytes)")
    print(f"reference:    {ref_path} ({len(sizes)} chunks)")
    print(f"output ifs:   {out_path} ({len(out)} bytes)")
    print(f"stored_size set to 0x{len(out):x}")
    print("outer u32 checksum: 0x00000000")
    print(f"FULL ROUNDTRIP {'OK (inflate == input decomp)' if ok else 'FAILED'}")
    if not ok:
        raise SystemExit("roundtrip mismatch; refusing to trust this image")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("decomp", type=Path)
    ap.add_argument("--ref", required=True, type=Path, help="stock .ifs for this variant (boundary source)")
    ap.add_argument("-o", "--out", required=True, type=Path)
    ap.add_argument("--method", choices=["999", "1"], default="999")
    ap.add_argument("--lzo", type=Path, default=L.DEFAULT_LZO_DYLIB)
    a = ap.parse_args()
    deflate(a.decomp, a.ref, a.out, a.method, a.lzo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
