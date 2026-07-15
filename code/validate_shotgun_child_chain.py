#!/usr/bin/env python3
"""Offline sh4emu validation of the CHAIN child-shotgun (build_shotgun_child_chain)
against the REAL bench -2 connect-bug snapshot btdump_1. The chain version takes NO
hardcoded main; it derives main = *(*(*(child+0x38)+0x08)+0x70) from the real dispatch
this=child. Proves, on the exact state where the proven cave fired:

  T1 FIRES : dispatch stub0 with this=child(0x086dca64) in the native armed -2 bug
             state. The chain must derive main=0x086ed694 on its own, gates pass,
             and it calls entertSourceChanged(main,24)+(main,40) and clears main+0x94c.
             NO main address is supplied — the whole point is that it finds main itself.
  T2 SAFE  : dispatch with this = the MAIN object (0x086ed694, not a child). Its +0x38
             chain does not resolve to a CPSoundPresCtrl-vtable object -> g_self rejects
             -> no ESC, no fault.
  T3 FAULT-PREVENTION : corrupt *(child+0x38) to an OOB pointer (>= memsize). The hop
             guard [LO,HI) must BAIL before dereferencing it -> no SH4Error. Without the
             guard this deref would fault (the watchdog-brick path).
"""
import sys, os, struct
sys.path.insert(0, os.path.dirname(__file__))
import sh4emu, build_shotgun_child_chain as B

DUMP = os.path.join(os.path.dirname(__file__), "..",
    "firmware-cache/bench-btstate-issue1-20260707/btdump_1/proc_PCM3Root_12316.as")
BIN  = os.path.join(os.path.dirname(__file__), "..",
    "firmware-cache/patch-lab/chn-fmguard-boot-MOPF/PCM3Root.CHN.fmboot")
BASE = 0x08040000
REAL_MAIN  = 0x086ed694    # CPSoundPresCtrl in btdump_1 (vtable 0x085c4c5c, +0x94c=1, -2 bug state)
GOOD_CHILD = 0x086dca64    # child = *(main+0x1f0); *child=0x085700e0; child+0x68==-2; chain->REAL_MAIN
OOB_PTR    = 0x25000000    # >= BASE+memsize: dereferencing it would fault
ESC = 0x082a717c

def load():
    d = open(DUMP, "rb").read()
    cpu = sh4emu.SH4()
    cpu.mem[0:len(d)-0x40000] = d[0x40000:]
    cpu.STACK_TOP = BASE+0x14000000; cpu.HEAP = BASE+0x10000000; cpu.HEAP_END = BASE+0x13000000
    cpu.r[15] = cpu.STACK_TOP
    return cpu

def dw(cpu, va): return struct.unpack_from('<I', cpu.mem, va-BASE)[0]

def install(cpu):
    code, cbase, stubs, slots, slen = B.build(BIN)         # chain build: no main_addr
    cpu.mem[cbase-BASE:cbase-BASE+len(code)] = code
    for i,(sv,_) in enumerate(stubs):
        struct.pack_into('<I', cpu.mem, B.VT-BASE+i*4, sv)
    return stubs, slots

def run_case(this_ptr, prep=None):
    cpu = load()
    stubs, slots = install(cpu)
    calls = []
    def esc_obs(c):
        this=c.r[4]&0xffffffff; src=dw(c,c.r[5]&0xffffffff); flag=dw(c,c.r[6]&0xffffffff)
        calls.append((this,src,flag)); c.r[0]=0
    def orig_noop(c): c.r[0]=0
    cpu.hooks[ESC] = esc_obs
    cpu.hooks[slots[0]] = orig_noop
    if prep: prep(cpu)
    fault=None
    try:
        sh4emu.call(cpu, stubs[0][0], [this_ptr])          # vtable dispatch -> stub0(this=this_ptr)
    except sh4emu.SH4Error as e:
        fault=str(e)
    return cpu, calls, fault

if __name__ == "__main__":
    ok=True
    s = load()
    ch = dw(s, REAL_MAIN+0x1f0)
    c1=dw(s,GOOD_CHILD+0x38); c2=dw(s,c1+0x08); c3=dw(s,c2+0x70)
    print("btdump_1 graph: main=0x%08x *main=0x%08x  child=0x%08x *child=0x%08x child+0x68=%d"
          % (REAL_MAIN, dw(s,REAL_MAIN), ch, dw(s,ch), dw(s,ch+0x68)-(1<<32)))
    print("CHAIN(child): +0x38=0x%08x +0x08=0x%08x +0x70=0x%08x  == main? %s"
          % (c1,c2,c3, c3==REAL_MAIN))

    # T1 FIRES: this=child, chain derives main itself, native -2 bug state.
    cpu, calls, fault = run_case(GOOD_CHILD)
    fired=[(hex(t),s2,f) for t,s2,f in calls]
    a94c = dw(cpu, REAL_MAIN+0x94c)
    t1 = (fault is None and len(calls)==2 and calls[0]==(REAL_MAIN,24,0)
          and calls[1]==(REAL_MAIN,40,0) and a94c==0)
    print("\nT1 FIRES (this=child, main SELF-DERIVED): ESC =", fired, " main+0x94c ->", a94c,
          " fault=", fault, " =>", "PASS" if t1 else "FAIL")
    ok &= t1

    # T2 SAFE: this = the main object (not a child). Chain must not resolve to a VTAB main.
    cpu2, calls2, fault2 = run_case(REAL_MAIN)
    t2 = (fault2 is None and len(calls2)==0)
    print("T2 SAFE (this=main, not a child): ESC =", calls2, " fault=", fault2,
          " =>", "PASS" if t2 else "FAIL")
    ok &= t2

    # T3 FAULT-PREVENTION: corrupt *(child+0x38) to OOB; the [LO,HI) hop-guard must bail.
    def prep_oob(c):
        struct.pack_into('<I', c.mem, GOOD_CHILD+0x38-BASE, OOB_PTR)
    would_fault = not (0 <= (OOB_PTR+0x08-BASE) < sh4emu.SH4().memsize)
    cpu3, calls3, fault3 = run_case(GOOD_CHILD, prep_oob)
    t3 = (fault3 is None and len(calls3)==0)
    print("T3 FAULT-PREVENTION (*(child+0x38)=0x%08x OOB=%s): ESC =%s fault=%s => %s"
          % (OOB_PTR, would_fault, calls3, fault3, "PASS" if t3 else "FAIL"))
    ok &= t3

    print("\n%s" % ("ALL PASS — chain shotgun self-derives main from child, fires on the "
                    "real -2 state, and its hop-guards prevent any OOB fault."
                    if ok else "*** FAIL ***"))
    sys.exit(0 if ok else 1)
