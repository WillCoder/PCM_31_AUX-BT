#!/usr/bin/env python3
"""Inflate PCM3.1/QNX LZO IFS images with liblzo2.

This helper is intentionally LZO-only. It produces the same container shape
expected by MMI3G-Toolkit's patch_ifs.py:

    startup section copied verbatim + uncompressed imagefs payload

Ground-truth robustness (2026-07-17): liblzo2 is located across several
well-known locations (homebrew ARM/Intel, ctypes.util.find_library, Linux
.so names, and the $LIBLZO2 env override) instead of a single hardcoded
macOS path. If no C library is present at all, the inflate path (NOT the
compress path in deflate_ifs_lzo.py) falls back to the repo's pure-python
kernel-faithful LZO1X port so the ground truth is always available and the
sh4_run_ipl self-check never silently SKIPs.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import os
import struct
import sys
from pathlib import Path


FLAGS1_COMPRESS_MASK = 0x1C
FLAGS1_COMPRESS_SHIFT = 2
COMPRESS_LZO = 2
# Kept for backward-compat (deflate_ifs_lzo.py uses it as an argparse default).
# It is only the FIRST candidate now; load_lzo() falls through to others.
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


def _candidate_lib_paths(explicit=None):
    """Ordered list of liblzo2 locations to try with ctypes.CDLL."""
    cands = []
    for c in (explicit, os.environ.get("LIBLZO2")):
        if c:
            cands.append(str(c))
    cands += [
        "/opt/homebrew/opt/lzo/lib/liblzo2.dylib",     # macOS ARM homebrew
        "/opt/homebrew/opt/lzo/lib/liblzo2.2.dylib",
        "/usr/local/opt/lzo/lib/liblzo2.dylib",         # macOS Intel homebrew
        "/usr/local/opt/lzo/lib/liblzo2.2.dylib",
    ]
    found = ctypes.util.find_library("lzo2")
    if found:
        cands.append(found)
    cands += [
        "liblzo2.so.2", "liblzo2.so",                   # Linux
        "liblzo2.2.dylib", "liblzo2.dylib",             # let dyld/ld search
    ]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def load_lzo(path=None) -> ctypes.CDLL:
    """Load and configure liblzo2 via ctypes, searching robust candidate paths.

    Returns a ctypes.CDLL with lzo1x_decompress bound (and usable for
    compression by callers such as deflate_ifs_lzo.py). Raises OSError if no
    liblzo2 can be located anywhere -- callers that require the C library
    (e.g. the compressor) must handle that."""
    tried = _candidate_lib_paths(path)
    lib = None
    last = None
    for cand in tried:
        try:
            lib = ctypes.CDLL(cand)
            break
        except OSError as e:
            last = e
    if lib is None:
        raise OSError(f"liblzo2 not found; tried {tried} (last error: {last})")

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


def _load_pyfallback():
    """Return the repo's pure-python kernel-faithful LZO1X module, or None.

    This is a faithful port of the Linux kernel lzo1x_decompress_safe.c and
    has been cross-validated byte-for-byte against liblzo2 on real stock IFS1
    chunks. It exists so the ground-truth decode works with zero external
    dependencies (no homebrew, no docker, no python-lzo)."""
    root = Path(__file__).resolve().parents[2]
    tools = root / "references/PCM-Forge-upstream/PCM4/tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        import lzo1x  # noqa: F401
        return lzo1x
    except Exception:
        return None


def resolve_decompressor(lzo_path=None):
    """Pick a (backend_name, decompress(chunk, index) -> bytes) pair.

    Prefers the authoritative liblzo2 C library; falls back to the pure-python
    kernel-faithful LZO1X port when no C library can be found so the inflate
    (ground-truth) path is always available."""
    try:
        lib = load_lzo(lzo_path)

        def _dec(chunk, index, _lib=lib):
            return decompress_chunk(_lib, chunk, index)

        return "liblzo2", _dec
    except OSError as lib_err:
        py = _load_pyfallback()
        if py is None:
            raise SystemExit(
                f"no LZO backend available: liblzo2 not found ({lib_err}) and the "
                f"pure-python lzo1x.py fallback could not be imported"
            )

        def _dec(chunk, index, _py=py):
            out, _ip, reason = _py.decompress_stream(chunk)
            if reason not in ("eof", "done"):
                raise SystemExit(f"chunk {index}: pure-python lzo1x failed ({reason})")
            return bytes(out)

        return "pure-python(lzo1x)", _dec


def inflate_ifs(input_path: Path, output_path: Path, lzo_path=None) -> str:
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

    backend, decompress = resolve_decompressor(lzo_path)
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
        dec = decompress(chunk, chunks)
        output.extend(dec)
        total += len(dec)
        chunks += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(output)

    print(
        f"flags1=0x{flags1:02x} compression=lzo backend={backend} "
        f"startup_size=0x{startup_size:x} stored_size=0x{stored_size:x} "
        f"imagefs_size=0x{imagefs_size:x}"
    )
    print(f"decompressed {chunks} chunks -> {total} imagefs bytes")
    if imagefs_size and total != imagefs_size:
        print(f"warning: header imagefs_size={imagefs_size}, decompressed={total}")
    print(f"wrote {len(output)} bytes -> {output_path}")
    return backend


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ifs", type=Path)
    parser.add_argument("-o", "--out", required=True, type=Path)
    parser.add_argument("--lzo", type=Path, default=None,
                        help="explicit liblzo2 path (default: auto-detect)")
    args = parser.parse_args()

    inflate_ifs(args.ifs, args.out, args.lzo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
