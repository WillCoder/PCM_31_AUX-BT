#!/usr/bin/env python3
# 🚨🚨 警告: 本文件的 CODE cave 地址 0x0856484c 【不是死区】🚨🚨
# 它是一张【活的】50x50 音频源仲裁矩阵(基址 0x0856421c, 2500B, 读取者 FUN_080eaa0c
# = B[row*50+col])的稀疏内部; cave 落在 行31(AUDIO_PAUSE, 活行) 列34。
# 往里写代码会污染音频源仲裁。childchain 那次刷进真车"能用"属侥幸, 不是安全。
# ✅ 新工作请用已证明安全的 cave: **0x0860CA58** (1920B, 任意对齐零引用 + 几何闭合 +
#    结构性排除, 内容=链接期删除函数遗留的孤儿调试字符串)。
# 详见 memory: cave-0x0856484c-is-NOT-dead-audio-arbitration-table.md

"""shotgun-CHILD-CHAIN: the PROVEN 2026-07-08 all-5 child-vtable cave, with its ONE
flaw fixed. INCREMENTAL over dev/build_shotgun_child.py — everything is byte-identical
EXCEPT how `main` is obtained.

WHY: the 07-08 cave produced LASTING boot sound on the bench (hook child vtable
0x085700e0 all 5 methods; gate child+0x68==-2 && main+0x94c==1; fire
entertSourceChanged(AUX)->(BT) down the durable +0x944==0 leg). Its ONLY defect was
r14 = HARDCODED main. main is a heap singleton that DRIFTS per boot; on a boot where
the hardcoded address is unmapped, the very first *(main) read FAULTS -> watchdog ->
brick. (A mapped-but-wrong address was already a safe no-op via the g_self check; the
killer was the *unmapped* case.)

FIX (verified byte-identical, main & *main, in all 3 live dumps BT_playing/disconnected/
AUX — see firmware-cache/bench-btstate-20260706): derive main from the REAL dispatch
`this` (r4 = child, guaranteed mapped) via a bounds-guarded pointer chain:
    main = *(*(*(child + 0x38) + 0x08) + 0x70)
Each hop is range-checked [LO,HI) BEFORE deref, so an un-populated/mid-relink case
BAILS cleanly (no fault, retry next child-vt call) instead of crashing. The existing
g_self check (*(main)==0x085c4c5c) then validates the chain result. No scan (the old
scan overloaded the hot path -> crash), no hardcode, no second hook, no stash global.

FIX-1 (adversarial-verify required): HI tightened 0x09000000 -> 0x08f00000 so that any
guarded pointer P satisfies P + max_offset(0x70) < 0x09000000 (top of the mapped 16MB
snapshot) — closes the only fault path. All real intermediates (X=0x086f1c84,
I=0x086ec324, main=0x086ec01c) are < 0x08f00000, so no false bail.

Fire-once: kept the PROVEN *(main+0x94c)=0 recursion guard (NOT a .bss latch). It fires
once per connect window AND re-fires on each later reconnect (the app re-arms 0x94c=1) —
which the boot-latch alternative would not. Avoids the latch's multithread/unused-word
risks entirely.

FIX-2 (already correct in the 07-08 base, preserved): the per-thunk ORIG method address
is stashed on the STACK at entry (predec r0) and reloaded (postinc r0) for the final
jmp @r0 — never left in r0-r7 (clobbered by the two entertSourceChanged jsr's) or
r8-r14 (must stay pristine for the caller). COMMON uses only r0-r3 + r14 as scratch.
"""
import struct

VT=0x085700e0            # child vtable (5 methods)
CODE=0x0856484c          # RX dead zone (all-zero in both MOPF and 9x1)
ESC=0x082a717c           # entertSourceChanged(this,&src,&flag)
VTAB=0x085c4c5c          # CPSoundPresCtrl vtable (identity check for derived main)
LO=0x08600000; HI=0x08f00000     # FIX-1: HI was 0x09000000
AUX=24; BT=40

def predec(rn,rm): return 0x2006|(rn<<8)|(rm<<4)
def stspr(rn): return 0x4022|(rn<<8)
def ldspr(rn): return 0x4026|(rn<<8)
def postinc(rn,rm): return 0x6006|(rn<<8)|(rm<<4)
def movrr(rn,rm): return 0x6003|(rn<<8)|(rm<<4)
def movi(rn,i): return 0xe000|(rn<<8)|(i&0xff)
def addi(rn,i): return 0x7000|(rn<<8)|(i&0xff)
def cmpeq(rn,rm): return 0x3000|(rn<<8)|(rm<<4)     # T=(Rn==Rm)
def cmphs(rn,rm): return 0x3002|(rn<<8)|(rm<<4)     # cmp/hs Rm,Rn: T=(Rn>=Rm unsigned)
def jsr(rn): return 0x400b|(rn<<8)
def jmp(rn): return 0x402b|(rn<<8)
def nop(): return 0x0009
def movl_r0(rm,rn): return 0x000e|(rm<<8)|(rn<<4)   # mov.l @(r0,Rn),Rm
def movl_to_r0(rm,rn): return 0x0006|(rn<<8)|(rm<<4)# mov.l Rm,@(r0,Rn)

def build(binpath, main_addr=None):
    b=open(binpath,'rb').read(); BASE=0x08040000
    slots=[struct.unpack_from('<I',b,VT-BASE+i*4)[0] for i in range(5)]

    # ---- shared routine (r0=method on entry, r4=child=this) ----
    prog=[]
    prog+=[predec(15,7),predec(15,6),predec(15,5),predec(15,4),predec(15,0),predec(15,14),stspr(15)]

    # ---- derive main = *(*(*(child+0x38)+0x08)+0x70), child=r4, each hop bounds-guarded ----
    def guard(reg):   # BAIL to Lskip unless LO<=reg<HI
        return [('pc','Llo',0),cmphs(reg,0),('bf','Lskip'),
                ('pc','Lhi',0),cmphs(reg,0),('bt','Lskip')]
    prog+=[movi(0,0x38),movl_r0(1,4)]   + guard(1)   # r1 = X = *(child+0x38)
    prog+=[movi(0,0x08),movl_r0(2,1)]   + guard(2)   # r2 = I = *(X+0x08)
    prog+=[movi(0,0x70),movl_r0(14,2)]  + guard(14)  # r14 = main = *(I+0x70)

    # g_self: r1=*(main); if r1 != CPSoundPresCtrl vtable -> skip  (validates the chain)
    prog+=[movi(0,0),movl_r0(1,14),
           ('pc','Lvt',0),cmpeq(1,0),('bf','Lskip')]
    # g0: child=*(main+0x1f0); if child < LO -> skip   (faithful to proven gate)
    prog+=[('pc','Lx1f0',0),movl_r0(1,14),
           ('pc','Llo',0),cmphs(1,0),('bf','Lskip')]
    # g0b: if child >= HI -> skip
    prog+=[('pc','Lhi',0),cmphs(1,0),('bt','Lskip')]
    # g1: r2=*(child+0x68); if r2 != -2 -> skip
    prog+=[movi(0,0x68),movl_r0(2,1),
           movi(0,-2),cmpeq(2,0),('bf','Lskip')]
    # g2: if *(main+0x94c) != 1 -> skip
    prog+=[('pc','Lx94c',0),movl_r0(2,14),
           movi(0,1),cmpeq(2,0),('bf','Lskip')]
    # *(main+0x94c)=0   (recursion / fire-once guard; app re-arms on next reconnect)
    prog+=[movi(1,0),('pc','Lx94c',0),movl_to_r0(1,14)]
    def call_esc(src):
        return [movi(1,0),predec(15,1),movi(1,src),predec(15,1),
                movrr(4,14),movrr(5,15),movrr(6,15),addi(6,4),
                ('pc','Lesc',0),jsr(0),nop(),addi(15,8)]
    prog+=call_esc(AUX)+call_esc(BT)
    prog+=[('LABEL','Lskip'),ldspr(15),postinc(14,15),postinc(0,15),
           postinc(4,15),postinc(5,15),postinc(6,15),postinc(7,15),
           jmp(0),nop()]                                   # jmp @r0 (= restored method)

    # assemble shared with literal pool at end
    addr={};pc=0;seq=[]
    for x in prog:
        if isinstance(x,tuple) and x[0]=='LABEL': addr[x[1]]=pc
        else: seq.append((pc,x)); pc+=2
    code_end=pc; lit_base=(code_end+3)&~3
    LITS=['Lx1f0','Lx94c','Lesc','Llo','Lvt','Lhi']; litaddr={}; p=lit_base
    for n in LITS: litaddr[n]=p; p+=4
    shared_len=p
    out=bytearray(shared_len)
    def emit(o,hw): out[o]=hw&0xff; out[o+1]=(hw>>8)&0xff
    for pc,x in seq:
        if isinstance(x,tuple):
            if x[0]=='pc':
                la=litaddr[x[1]]; disp=(la-((pc&~3)+4))//4
                assert 0<=disp<=255 and (la-((pc&~3)+4))%4==0,(x,disp)
                emit(pc,0xd000|(x[2]<<8)|disp)
            elif x[0]=='bf':
                d=(addr[x[1]]-(pc+4))//2; assert -128<=d<=127,("bf range",x,d); emit(pc,0x8b00|(d&0xff))
            elif x[0]=='bt':
                d=(addr[x[1]]-(pc+4))//2; assert -128<=d<=127,("bt range",x,d); emit(pc,0x8900|(d&0xff))
        else: emit(pc,x)
    lv={'Lx1f0':0x1f0,'Lx94c':0x94c,'Lesc':ESC,'Llo':LO,'Lvt':VTAB,'Lhi':HI}
    for n in LITS: struct.pack_into('<I',out,litaddr[n],lv[n])
    code=bytearray(out)

    # ---- stubs (each: mov.l .Lm,r0 ; bra shared ; nop ; .long orig_method) ----
    stubs=[]
    for i,m in enumerate(slots):
        so=len(code); sv=CODE+so
        lm=(so+6+3)&~3
        disp=((CODE+lm)-((sv&~3)+4))//4
        assert 0<=disp<=255
        bdisp=(CODE-((sv+2)+4))//2; assert -2048<=bdisp<=2047,(i,bdisp)
        s=bytearray()
        s+=struct.pack('<H',0xd000|disp)               # mov.l .Lm,r0
        s+=struct.pack('<H',0xa000|(bdisp&0xfff))       # bra shared
        s+=struct.pack('<H',0x0009)
        while len(s)<(lm-so): s+=struct.pack('<H',0x0009)
        s+=struct.pack('<I',m)
        code+=s; stubs.append((sv,m))
    return bytes(code),CODE,stubs,slots,shared_len

def apply_to(binpath):
    """Return the patched binary bytes: cave at CODE, 5 child-vt slots redirected to thunks."""
    b=bytearray(open(binpath,'rb').read()); BASE=0x08040000
    code,cbase,stubs,slots,slen=build(binpath)
    co=cbase-BASE
    assert bytes(b[co:co+len(code)])==b'\x00'*len(code),"dead zone 0x%08x not empty (len %d)"%(cbase,len(code))
    b[co:co+len(code)]=code
    for i,(sv,m) in enumerate(stubs):
        struct.pack_into('<I',b,VT-BASE+i*4,sv)   # slot[i] -> thunk
    return bytes(b),code,cbase,stubs

if __name__=="__main__":
    import sys
    binpath=sys.argv[1] if len(sys.argv)>1 else "firmware-cache/patch-lab/chn-fmguard-boot-MOPF/PCM3Root.CHN.fmboot"
    code,base,stubs,slots,slen=build(binpath)
    print("shotgun-CHILD-CHAIN @0x%08x total=%d shared=%d  ESC=0x%08x  HI=0x%08x"%(
        base,len(code),slen,ESC,HI))
    print("child slots:",[hex(s) for s in slots])
    print("thunks:",[hex(sv) for sv,_ in stubs])
    open("dev/_child_chain.bin","wb").write(code[:slen])
    print("disasm shared: docker run --rm -v $PWD:/w sh4gdb:latest objdump -D -b binary -m sh4 -EL --adjust-vma=0x%08x /w/dev/_child_chain.bin"%base)
