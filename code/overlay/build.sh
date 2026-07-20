#!/bin/bash
# Build coexist_vol (floating volume OSD). Same link recipe as
# build_coexist_lm.sh -- it links libgf.so.1 (real on-car = libgdcApiCarmine.so,
# a BIND_NOW C++ library) whose OWN deps (libm/libz/libecpp-ne C++ runtime)
# must ALSO be NEEDED by our process so the dynamic linker binds them eagerly
# at load; otherwise the process DIES BEFORE main() (confirmed 2026-07-15:
# built with the 2-lib build_coexist_car.sh -> "毛都没看到", process VOL_DEAD
# at t=0, no log written = loader failure). PLUS -lgcc for the SH4 software
# division helper (__sdivsi3_i4i) used by the per-pixel renderer (fill_tri).
#
# Real on-car library names (realcar-runtime-dump pidin_mem): libm.so.2,
# libz.so, libecpp-ne.so.4. We link build-time-only stubs (--no-as-needed
# forces the NEEDED entry even though our code never calls their symbols).
set -e
cd "$(dirname "$0")/.."

SRC="${1:-coexist-app/mvp/coexist_vol.c}"
OUT="${2:-coexist-app/mvp/coexist_vol}"

docker run --rm -v "$PWD:/work" sh4build:latest sh -c "
  cd /work
  sh4-linux-gnu-gcc -O2 -shared -nostdlib -fPIC -Wl,-soname,libc.so.2        dev/sh4tools/stub_libc.c  -o /tmp/libc.so.2
  sh4-linux-gnu-gcc -O2 -shared -nostdlib -fPIC -Wl,-soname,libgdcApiCarmine.so dev/sh4tools/stub_libgf.c -o /tmp/libgdcApiCarmine.so
  sh4-linux-gnu-gcc -O2 -shared -nostdlib -fPIC -Wl,-soname,libm.so.2        dev/sh4tools/stub_libm.c  -o /tmp/libm.so.2
  sh4-linux-gnu-gcc -O2 -shared -nostdlib -fPIC -Wl,-soname,libz.so          dev/sh4tools/stub_libz.c  -o /tmp/libz.so
  sh4-linux-gnu-gcc -O2 -shared -nostdlib -fPIC -Wl,-soname,libecpp-ne.so.4  dev/sh4tools/stub_ecpp.c  -o /tmp/libecpp-ne.so.4
  sh4-linux-gnu-gcc -O2 -Wall -nostdlib \
    -Wl,--dynamic-linker=/usr/lib/ldqnx.so.2 \
    dev/sh4tools/_start.S '$SRC' \
    -Wl,--no-as-needed /tmp/libc.so.2 /tmp/libm.so.2 /tmp/libz.so /tmp/libecpp-ne.so.4 /tmp/libgdcApiCarmine.so \
    -lgcc \
    -o '$OUT'
  sh4-linux-gnu-strip '$OUT' -o '$OUT'.stripped
" 2>&1 | grep -v 'executable stack\|deprecated' | tee /tmp/sh4build.log
if grep -q 'error:' /tmp/sh4build.log; then
  echo "!!! 编译失败 —— 旧二进制未被替换, 别推台架 !!!" >&2
  exit 1
fi

echo "Built $OUT.stripped"
docker run --rm -v "$PWD:/work" sh4build:latest sh -c "
  cd /work
  echo '--- NEEDED entries (expect 5: libc/libm/libz/libecpp-ne/libgdcApiCarmine) ---'
  sh4-linux-gnu-readelf -d '$OUT' 2>/dev/null | grep NEEDED
  ls -l '$OUT'.stripped | awk '{print \"size: \"\$5\" bytes\"}'
" 2>&1 | grep -v 'executable stack\|deprecated'
