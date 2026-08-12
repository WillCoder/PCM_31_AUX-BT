#!/usr/bin/env python3
"""Run PCM3Root source-switch functions in the SH4 interpreter against the
REAL car memory snapshot (proc_PCM3Root_12316.as). This is an offline testbed
for the bench "can't switch source" problem: reproduce the async TLAM switch,
find the exact revert/confirm point by EXECUTION (not static guessing), and
validate mock patches before flashing.

Car dump = full snapshot of vaddr 0x08000000..0x09000000 (code + live object
@0x086ef01c). IPC/log calls are stubbed; stubbed IPC = "no amp response" = the
bench scenario. Make a stub return a "connected" reply to test the mock.
"""
import sys, struct, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import sh4emu

DUMP = os.path.join(os.path.dirname(__file__), "..",
    "firmware-cache/realcar-persistency-memdump-2026-07-01/proc_PCM3Root_12316.as")
OBJ = 0x086ef01c   # live CPSoundPresCtrl object (car, on FM: src@0x860=11, state@0x124=2)

def new_cpu():
    d = open(DUMP, "rb").read()
    cpu = sh4emu.SH4()
    cpu.mem[0:len(d)-0x40000] = d[0x40000:]      # vaddr 0x08040000.. -> index 0
    cpu.STACK_TOP = 0x08040000+0x14000000
    cpu.HEAP = 0x08040000+0x10000000
    cpu.HEAP_END = 0x08040000+0x13000000
    cpu.r[15] = cpu.STACK_TOP
    return cpu

def strat(cpu, va):
    try:
        o = va-0x08040000
        if 0 <= o < len(cpu.mem):
            e = cpu.mem.index(0, o)
            if e-o < 120: return cpu.mem[o:e].decode('latin1')
    except Exception: pass
    return None

def install_stubs(cpu, trace):
    def logstub(c):
        parts = [s for rr in (4,5,6) for s in [strat(c, c.r[rr]&0xffffffff)]
                 if s and len(s) >= 4 and s.isprintable()]
        if parts: trace.append(" | ".join(parts))
        c.r[0] = 0
    def noop(c): c.r[0] = 0
    for a in (0x08076e4c,0x08077438,0x08076df8,0x08077374,0x080772b0): cpu.hooks[a] = logstub
    for a in (0x081102f0,0x08110298,0x08110998,0x0809415c,0x08091a14,
              0x082b4d68,0x082b5e70,0x082a3ba4,0x08110d84): cpu.hooks[a] = noop

def dw(cpu, va): return struct.unpack_from('<I', cpu.mem, va-0x08040000)[0]

if __name__ == "__main__":
    cpu = new_cpu(); tr = []; install_stubs(cpu, tr)
    print("sourceToString(11) =", strat(cpu, sh4emu.call(cpu, 0x080eaac4, [11])))
    psrc = sh4emu.guest_malloc(cpu, 4); cpu.w32(psrc, 18)   # CDA
    pfg  = sh4emu.guest_malloc(cpu, 4); cpu.w32(pfg, 18)
    print("before: mEntertSource@0x860 =", dw(cpu, OBJ+0x860))
    sh4emu.call(cpu, 0x082a717c, [OBJ, psrc, pfg])           # entertSourceChanged
    print("after : mEntertSource@0x860 =", dw(cpu, OBJ+0x860), " (unchanged = switch pending, no confirm)")
    for t in tr: print("   trace:", t)
