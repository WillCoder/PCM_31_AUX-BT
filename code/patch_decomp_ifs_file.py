#!/usr/bin/env python3
"""Patch one same-sized file inside a decompressed QNX IFS image.

Unlike generic rebuild-style patchers, this preserves the existing imagefs
layout and directory table. It is intended for tiny binary edits where the
replacement file has exactly the same size as the original entry.
"""

from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path


IMAGE_SIG = b"imagefs"
S_IFMT = 0xF000
S_IFREG = 0x8000
S_IFDIR = 0x4000
S_IFLNK = 0xA000


@dataclass(frozen=True)
class Entry:
    path: str
    ftype: int
    abs_off: int
    rec_size: int
    file_offset: int | None = None
    file_size: int | None = None


def u16(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def w32(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", data, offset, value & 0xFFFFFFFF)


def compute_ifs_checksum(data: bytes | bytearray, start: int, size: int) -> int:
    total = 0
    for offset in range(start, start + size - 4, 4):
        total = (total + u32(data, offset)) & 0xFFFFFFFF
    return (~total + 1) & 0xFFFFFFFF


def verify_ifs_checksum(data: bytes | bytearray, start: int, size: int) -> int:
    total = 0
    for offset in range(start, start + size, 4):
        total = (total + u32(data, offset)) & 0xFFFFFFFF
    return total


def parse_entries(data: bytes | bytearray, ipos: int) -> list[Entry]:
    if data[ipos : ipos + 7] != IMAGE_SIG:
        raise SystemExit(f"no imagefs signature at offset 0x{ipos:x}")

    hdr_dir_size = u32(data, ipos + 12)
    dir_offset = u32(data, ipos + 16)
    offset = ipos + dir_offset
    end = ipos + hdr_dir_size
    entries: list[Entry] = []

    while offset < end:
        if offset + 24 > len(data):
            break
        rec_size = u16(data, offset)
        if rec_size == 0:
            break
        mode = u32(data, offset + 8)
        ftype = mode & S_IFMT

        file_offset = None
        file_size = None
        if ftype == S_IFREG:
            file_offset = u32(data, offset + 24)
            file_size = u32(data, offset + 28)
            path_start = offset + 32
        elif ftype == S_IFDIR:
            path_start = offset + 24
        elif ftype == S_IFLNK:
            path_start = offset + 28
        else:
            path_start = offset + 32

        path_end = data.find(b"\x00", path_start, offset + rec_size)
        if path_end < 0:
            path_end = offset + rec_size
        path = data[path_start:path_end].decode("utf-8", errors="replace")
        entries.append(
            Entry(
                path=path,
                ftype=ftype,
                abs_off=offset,
                rec_size=rec_size,
                file_offset=file_offset,
                file_size=file_size,
            )
        )
        offset += rec_size

    return entries


def parse_replace(spec: str) -> tuple[str, Path]:
    if "=" not in spec:
        raise SystemExit("--replace must be IFS_PATH=LOCAL_FILE")
    ifs_path, local_path = spec.split("=", 1)
    return ifs_path.lstrip("/"), Path(local_path)


def patch_file(input_path: Path, output_path: Path, replace_spec: str) -> None:
    data = bytearray(input_path.read_bytes())
    if len(data) < 0x34:
        raise SystemExit(f"{input_path} is too small to be a QNX IFS")
    if u32(data, 0) != 0x00FF7EEB:
        raise SystemExit(f"{input_path} does not start with QNX IFS magic")

    startup_size = u32(data, 0x20)
    ipos = startup_size
    image_size = u32(data, ipos + 8)
    trailer_offset = ipos + image_size - 4
    if trailer_offset + 4 > len(data):
        raise SystemExit("imagefs trailer is outside the input file")

    ifs_path, local_path = parse_replace(replace_spec)
    replacement = local_path.read_bytes()
    entries = parse_entries(data, ipos)
    matches = [entry for entry in entries if entry.path == ifs_path and entry.ftype == S_IFREG]
    if len(matches) != 1:
        raise SystemExit(f"expected one regular-file match for /{ifs_path}, found {len(matches)}")
    entry = matches[0]
    assert entry.file_offset is not None
    assert entry.file_size is not None
    if len(replacement) != entry.file_size:
        raise SystemExit(
            f"/{ifs_path} replacement size {len(replacement)} differs from "
            f"IFS entry size {entry.file_size}; refusing layout-preserving patch"
        )

    file_abs = ipos + entry.file_offset
    if file_abs + entry.file_size > len(data):
        raise SystemExit(f"/{ifs_path} content extends outside input file")

    before_checksum_total = verify_ifs_checksum(data, ipos, image_size)
    data[file_abs : file_abs + entry.file_size] = replacement
    checksum = compute_ifs_checksum(data, ipos, image_size)
    w32(data, trailer_offset, checksum)
    after_checksum_total = verify_ifs_checksum(data, ipos, image_size)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)

    print(f"input:  {input_path}")
    print(f"output: {output_path}")
    print(f"patched: /{ifs_path}")
    print(f"file_abs_offset: 0x{file_abs:x}")
    print(f"file_size: {entry.file_size}")
    print(f"old_checksum_total: 0x{before_checksum_total:08x}")
    print(f"new_trailer_checksum: 0x{checksum:08x}")
    print(f"new_checksum_total: 0x{after_checksum_total:08x}")
    print(f"output_size: {len(data)}")


def list_files(input_path: Path) -> None:
    data = input_path.read_bytes()
    startup_size = u32(data, 0x20)
    entries = parse_entries(data, startup_size)
    for entry in entries:
        if entry.ftype == S_IFREG:
            print(f"{entry.file_size:>10}  /{entry.path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--replace", metavar="IFS_PATH=LOCAL_FILE")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        list_files(args.input)
        return 0
    if not args.output:
        raise SystemExit("output path is required unless --list is used")
    if not args.replace:
        raise SystemExit("--replace is required unless --list is used")

    patch_file(args.input, args.output, args.replace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
