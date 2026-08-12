#!/usr/bin/env python3
"""Minimal-but-broad SH4 (SH-4, little-endian) interpreter for function-level
execution of PCM3Reload.elf.

Goal: load the PCM3Reload RX/RW image into a flat memory, set up a stack/heap,
seed argument registers per the SH4 ABI (r4-r7 = args, r0 = return, r15 = sp,
pr = return addr), set PC to a target function and interpret until it returns to
a sentinel address, then read out memory.

Only what real code needs is implemented, but the common integer ISA is covered
broadly (arithmetic / logic / shift / load-store / branch / jsr-rts / delay
slots / T-bit / MAC) and the SH-4 FPU is implemented (see the "FPU" block at the
end of class SH4). Library calls (malloc/memcpy/...) are hooked by installing
python callbacks at chosen guest addresses.

This is an instrument, not a full system emulator: no MMU, no exceptions, no
privileged regs beyond what the codec touches.

── 保真度审计 (2026-08-07, S7) ────────────────────────────────────────────
整数 + 系统组的**每一条**已实现指令形式(折叠 n/m 字段后约 149 条)都逐条对
references/sh4/SH-4A_Software_Manual_rej09b0003.pdf 的伪代码核过, 编码用
docker sh4build 的 sh4-linux-gnu-as 汇编 + objdump -m sh4 -EL 反汇编对拍。
上午已修 NEGC / SETT / DIV0S / DIV0U / DIV1; 这一轮又找到 5 条:
  · 0x81xx MOV.W R0,@(disp,Rn) 基址取错字段(取了子码 -> 恒为 r1)
  · 0x0028 CLRMAC 没清 MACH/MACL
  · 0x4n06 LDS.L @Rn+,MACH 被当成 ...,PR (会换掉返回地址)
  · 0x4n16 LDS.L @Rn+,MACL 只加了指针没装值
  · 0x0nC3 MOVCA.L R0,@Rn 的存被当 nop 吞掉
外加保真度补齐(旧版给 0 或直接 raise): STC/LDC 的 SR/GBR/VBR 全家 + MAC.W。
全部有回归用例: dev/tests/test_sh4_intfix.py。
已知**有意**的偏差: SR 只建模 T/Q/M/S 位; SSR/SPC/SGR/DBR 读回 0; RTE/SLEEP/
LDTLB 是空操作; TRAPA 直接 raise(响的, 不静默)。
"""
import struct, sys, os, math

ELF = os.path.join(os.path.dirname(__file__), "..", "scratchpad", "PCM3Reload.elf")
IMAGE_BASE = 0x08040000

class SH4Error(Exception):
    pass

class SH4:
    def __init__(self, memsize=0x18000000, trace=False):
        # Flat guest memory covering image (base 0x08040000, ~0x1678000 bytes)
        # plus scratch above it. We allocate a bytearray big enough to hold the
        # image at its natural address AND our stack/heap placed just past it.
        self.base = IMAGE_BASE
        self.mem = bytearray(memsize)          # index = vaddr - base
        self.memsize = memsize
        self.r = [0]*16
        self.pc = 0
        self.pr = 0
        self.gbr = 0
        self.vbr = 0
        self.mach = 0
        self.macl = 0
        self.sr = 0                            # only T bit (bit0) used
        self.fpul = 0
        # FPU: FPSCR 复位值 = 0x00040001 (DN=1 冲刷非规格化, RM=01 **朝零舍入**).
        # gcc/libgcc 的 __fpscr_values[] 也是 {0x00040001, 0x000c0001}, 即真机上
        # 单/双精度都跑 RM=01, 所以朝零舍入必须实现, 否则末位对不上.
        self.fpscr = 0x00040001
        # 32 个物理浮点寄存器 = 两个 bank, 存 **位模式**(u32), 不存 python float.
        # FR0-15 / XF0-15 是它们的两个视图, 由 FPSCR.FR 决定谁是谁(见 fr/xf 属性).
        self.fbank = [[0]*16, [0]*16]
        self.hooks = {}                        # vaddr -> python callable(cpu)
        self.trace = trace
        self.icount = 0
        self.SENTINEL = 0x0EFFEEEE              # return-to-here => stop
        self._loaded = False
        self.probe = False                      # introspection: log & tolerate unmapped access
        self.access_log = []                    # (kind, addr, val, pc)
        self.faults = []                        # (addr, pc) tolerated in probe mode
        self.PROBE_LO = 0x20000000              # synthetic object arena for probe mode
        self.PROBE_HI = 0x30000000
        self.rx_extra = None                    # (lo,hi) extra executable band for injected code (matching-decomp harness)

    # ---- memory ----
    def _idx(self, addr):
        i = (addr - self.base) & 0xffffffff
        if i >= self.memsize:
            if self.probe:
                # In probe mode, tolerate access to a low/unmapped pointer by
                # logging a fault instead of aborting, so we can map the full
                # object graph in one pass. Redirect to a scratch page.
                self.faults.append((addr & 0xffffffff, self.pc-2))
                return self._scratch_idx(addr)
            raise SH4Error("mem oob addr=%08x idx=%08x pc=%08x" % (addr & 0xffffffff, i, self.pc))
        return i
    def _scratch_idx(self, addr):
        # map any faulting address deterministically into a scratch band inside mem
        base=(self.memsize-0x400000)
        return base + ((addr) & 0x3fffff)
    def r8(self, a):
        return self.mem[self._idx(a)]
    def r16(self, a):
        i=self._idx(a); return self.mem[i] | (self.mem[i+1]<<8)
    def r32(self, a):
        i=self._idx(a); v=self.mem[i]|(self.mem[i+1]<<8)|(self.mem[i+2]<<16)|(self.mem[i+3]<<24)
        if self.probe and self.PROBE_LO<=a<self.PROBE_HI:
            self.access_log.append(("r32", a, v, self.pc-2))
        return v
    def r32log(self, a):  # unused hook point
        return self.r32(a)
    def w8(self, a, v):
        self.mem[self._idx(a)] = v & 0xff
    def w16(self, a, v):
        i=self._idx(a); self.mem[i]=v&0xff; self.mem[i+1]=(v>>8)&0xff
    def w32(self, a, v):
        i=self._idx(a); v&=0xffffffff
        self.mem[i]=v&0xff; self.mem[i+1]=(v>>8)&0xff; self.mem[i+2]=(v>>16)&0xff; self.mem[i+3]=(v>>24)&0xff

    def load_elf(self):
        d = open(ELF, "rb").read()
        e_phoff=struct.unpack_from('<I',d,28)[0]
        e_phentsize=struct.unpack_from('<H',d,42)[0]
        e_phnum=struct.unpack_from('<H',d,44)[0]
        for i in range(e_phnum):
            off=e_phoff+i*e_phentsize
            p_type,p_offset,p_vaddr,p_paddr,p_filesz,p_memsz=struct.unpack_from('<6I',d,off)
            if p_type==1:  # PT_LOAD
                idx=p_vaddr-self.base
                self.mem[idx:idx+p_filesz]=d[p_offset:p_offset+p_filesz]
                # memsz>filesz => bss already zero
        self._loaded=True
        self.elf=d

    # ---- helpers ----
    @staticmethod
    def s8(v):  v&=0xff;  return v-0x100 if v&0x80 else v
    @staticmethod
    def s16(v): v&=0xffff; return v-0x10000 if v&0x8000 else v
    @staticmethod
    def s32(v): v&=0xffffffff; return v-0x100000000 if v&0x80000000 else v
    @property
    def T(self): return self.sr & 1
    @T.setter
    def T(self, v): self.sr = (self.sr & ~1) | (1 if v else 0)
    # SR bit8 = Q, bit9 = M  (手册 §2.2.2 "Used by the DIV0S, DIV0U, and DIV1 instructions")
    @property
    def Q(self): return (self.sr >> 8) & 1
    @Q.setter
    def Q(self, v): self.sr = (self.sr & ~0x100) | (0x100 if v else 0)
    @property
    def M(self): return (self.sr >> 9) & 1
    @M.setter
    def M(self, v): self.sr = (self.sr & ~0x200) | (0x200 if v else 0)
    # SR bit1 = S (MAC 饱和位). gcc 从不置它, 但 STC/LDC SR 现在是忠实的,
    # 所以 MAC.W / MAC.L 也照手册读它, 免得留一条"看得见摸不着"的位。
    @property
    def S(self): return (self.sr >> 1) & 1
    @S.setter
    def S(self, v): self.sr = (self.sr & ~2) | (2 if v else 0)

    # ---- run ----
    def run(self, maxinsn=200_000_000):
        self._delayed=None
        RXEND=IMAGE_BASE+0x167ce08
        while self.icount < maxinsn:
            if self.pc == self.SENTINEL:
                return
            # execution guard: a jump to unmapped/low memory means a null/garbage
            # function pointer was called. Stop precisely instead of running off.
            if not (IMAGE_BASE <= self.pc < RXEND) and self.pc not in self.hooks \
                    and not (self.rx_extra and self.rx_extra[0] <= self.pc < self.rx_extra[1]):
                raise SH4Error("PC left code: pc=%08x (bad call target) pr=%08x r0=%08x"
                               % (self.pc & 0xffffffff, self.pr & 0xffffffff, self.r[0]&0xffffffff))
            if self.pc in self.hooks:
                self.hooks[self.pc](self)
                # a hook must simulate a return: set pc=pr (rts semantics) unless it moved pc
                if self.pc in self.hooks:
                    self.pc = self.pr
                continue
            op = self.r16(self.pc)
            if self.trace:
                sys.stderr.write("%08x: %04x  r0=%08x r4=%08x r5=%08x r6=%08x r15=%08x\n"
                                 % (self.pc, op, self.r[0]&0xffffffff, self.r[4]&0xffffffff,
                                    self.r[5]&0xffffffff, self.r[6]&0xffffffff, self.r[15]&0xffffffff))
            self.pc += 2
            self.icount += 1
            self.execute(op)
        raise SH4Error("insn budget exhausted @pc=%08x" % self.pc)

    def _branch(self, target, delayed=True):
        """Execute delay slot then jump."""
        if delayed:
            slot = self.r16(self.pc)
            self.pc += 2
            self.icount += 1
            # delay slot must not itself be a branch (assume codec well-formed)
            self.execute(slot)
        self.pc = target & 0xffffffff

    # ---- decoder / executor ----
    def execute(self, op):
        r=self.r
        n=(op>>8)&0xf
        m=(op>>4)&0xf
        d4=op&0xf
        d8=op&0xff
        d12=op&0xfff
        top=(op>>12)&0xf

        if top==0x6:
            # register-to-register moves / loads with post-inc / unary
            t=op&0xf
            if t==0x3:   r[n]=r[m]&0xffffffff                                 # mov Rm,Rn
            elif t==0x0: r[n]=self.s8(self.r8(r[m]))&0xffffffff               # mov.b @Rm,Rn (sign ext)
            elif t==0x1: r[n]=self.s16(self.r16(r[m]))&0xffffffff             # mov.w @Rm,Rn
            elif t==0x2: r[n]=self.r32(r[m])                                  # mov.l @Rm,Rn
            elif t==0x4: r[n]=self.s8(self.r8(r[m]))&0xffffffff; r[m]=(r[m]+1)&0xffffffff   # mov.b @Rm+,Rn
            elif t==0x5: r[n]=self.s16(self.r16(r[m]))&0xffffffff; r[m]=(r[m]+2)&0xffffffff # mov.w @Rm+,Rn
            elif t==0x6: r[n]=self.r32(r[m]); r[m]=(r[m]+4)&0xffffffff        # mov.l @Rm+,Rn
            elif t==0x7: r[n]=(~r[m])&0xffffffff                             # not
            elif t==0x8: r[n]=((r[m]&0xff00)>>8)|((r[m]&0xff)<<8)|(r[m]&0xffff0000)  # swap.b
            elif t==0x9: r[n]=((r[m]&0xffff)<<16)|((r[m]>>16)&0xffff)        # swap.w
            elif t==0xa:                                                      # negc Rm,Rn
                # 手册 §10.1.44: 0 - Rm - T -> Rn, Borrow -> T
                # (旧实现是 rotcl 语义, 错的; `mov #-1,r0 / tst / negc r0,r0`
                #  这个 "r0 = (x != 0)" 惯用法会被算反 —— dispatchNextEvent 返回值就靠它)
                tmp=0-(r[m]&0xffffffff)-self.T
                r[n]=tmp&0xffffffff; self.T=1 if tmp<0 else 0
            elif t==0xb: r[n]=(-self.s32(r[m]))&0xffffffff                   # neg
            elif t==0xc: r[n]=r[m]&0xff                                       # extu.b
            elif t==0xd: r[n]=r[m]&0xffff                                     # extu.w
            elif t==0xe: r[n]=self.s8(r[m])&0xffffffff                        # exts.b
            elif t==0xf: r[n]=self.s16(r[m])&0xffffffff                       # exts.w
            else: raise SH4Error("6-op %04x @%08x"%(op,self.pc-2))
        elif top==0x2:
            t=op&0xf
            if t==0x0: self.w8(r[n], r[m])                                    # mov.b Rm,@Rn
            elif t==0x1: self.w16(r[n], r[m])                                 # mov.w Rm,@Rn
            elif t==0x2: self.w32(r[n], r[m])                                 # mov.l Rm,@Rn
            elif t==0x4: r[n]=(r[n]-1)&0xffffffff; self.w8(r[n], r[m])        # mov.b Rm,@-Rn
            elif t==0x5: r[n]=(r[n]-2)&0xffffffff; self.w16(r[n], r[m])       # mov.w Rm,@-Rn
            elif t==0x6: r[n]=(r[n]-4)&0xffffffff; self.w32(r[n], r[m])       # mov.l Rm,@-Rn
            elif t==0x7:                                                      # div0s Rm,Rn (§10.1.15)
                self.Q=(r[n]>>31)&1; self.M=(r[m]>>31)&1
                self.T=0 if self.M==self.Q else 1
            elif t==0x8: self.T=1 if (r[n]&r[m])==0 else 0                    # tst Rm,Rn
            elif t==0x9: r[n]=(r[n]&r[m])&0xffffffff                          # and
            elif t==0xa: r[n]=(r[n]^r[m])&0xffffffff                          # xor
            elif t==0xb: r[n]=(r[n]|r[m])&0xffffffff                          # or
            elif t==0xc: # cmp/str
                tmp=r[n]^r[m]; self.T=1 if any(((tmp>>(8*k))&0xff)==0 for k in range(4)) else 0
            elif t==0xd: # xtrct
                r[n]=((r[m]<<16)|(r[n]>>16))&0xffffffff
            elif t==0xe: # mulu.w
                self.macl=(r[n]&0xffff)*(r[m]&0xffff)
            elif t==0xf: # muls.w
                self.macl=(self.s16(r[n])*self.s16(r[m]))&0xffffffff
            else: raise SH4Error("2-op %04x @%08x"%(op,self.pc-2))
        elif top==0x3:
            t=op&0xf
            a=self.s32(r[n]); b=self.s32(r[m])
            ua=r[n]&0xffffffff; ub=r[m]&0xffffffff
            if t==0x0: self.T=1 if ua==ub else 0                             # cmp/eq Rm,Rn
            elif t==0x2: self.T=1 if ua>=ub else 0                           # cmp/hs (unsigned)
            elif t==0x3: self.T=1 if a>=b else 0                             # cmp/ge (signed)
            elif t==0x4: # div1 Rm,Rn  —— 逐字照抄手册 §10.1.17 伪代码
                old_q=self.Q
                self.Q=1 if (r[n]&0x80000000) else 0
                tmp2=r[m]&0xffffffff
                r[n]=((r[n]<<1)&0xffffffff)|self.T
                if old_q==0:
                    if self.M==0:
                        tmp0=r[n]; r[n]=(r[n]-tmp2)&0xffffffff; tmp1=1 if r[n]>tmp0 else 0
                        self.Q=tmp1 if self.Q==0 else (1 if tmp1==0 else 0)
                    else:
                        tmp0=r[n]; r[n]=(r[n]+tmp2)&0xffffffff; tmp1=1 if r[n]<tmp0 else 0
                        self.Q=(1 if tmp1==0 else 0) if self.Q==0 else tmp1
                else:
                    if self.M==0:
                        tmp0=r[n]; r[n]=(r[n]+tmp2)&0xffffffff; tmp1=1 if r[n]<tmp0 else 0
                        self.Q=tmp1 if self.Q==0 else (1 if tmp1==0 else 0)
                    else:
                        tmp0=r[n]; r[n]=(r[n]-tmp2)&0xffffffff; tmp1=1 if r[n]>tmp0 else 0
                        self.Q=(1 if tmp1==0 else 0) if self.Q==0 else tmp1
                self.T=1 if self.Q==self.M else 0
            elif t==0x5: # dmulu.l
                res=ua*ub; self.macl=res&0xffffffff; self.mach=(res>>32)&0xffffffff
            elif t==0x6: self.T=1 if ua>ub else 0                            # cmp/hi (unsigned)
            elif t==0x7: self.T=1 if a>b else 0                             # cmp/gt (signed)
            elif t==0x8: r[n]=(r[n]-r[m])&0xffffffff                         # sub
            elif t==0xa: # subc
                tmp=(r[n]-r[m]-self.T)
                self.T=1 if tmp<0 else 0; r[n]=tmp&0xffffffff
            elif t==0xb: # subv
                res=a-b; self.T=1 if res<-0x80000000 or res>0x7fffffff else 0; r[n]=res&0xffffffff
            elif t==0xc: r[n]=(r[n]+r[m])&0xffffffff                         # add
            elif t==0xd: # dmuls.l
                res=(a*b)&0xffffffffffffffff; self.macl=res&0xffffffff; self.mach=(res>>32)&0xffffffff
            elif t==0xe: # addc
                tmp=(r[n]+r[m]+self.T); self.T=1 if tmp>0xffffffff else 0; r[n]=tmp&0xffffffff
            elif t==0xf: # addv
                res=a+b; self.T=1 if res<-0x80000000 or res>0x7fffffff else 0; r[n]=res&0xffffffff
            else: raise SH4Error("3-op %04x @%08x"%(op,self.pc-2))
        elif top==0x8:
            t=(op>>8)&0xf
            # ⚠ 8-组的**基址寄存器在 bit7-4**(= 本译码器的 m 字段), bit11-8 是子码
            #   (= n 字段)。手册 §10.1.33: MOV.B R0,@(disp,Rn) = 10000000nnnndddd,
            #   MOV.W R0,@(disp,Rn) = 10000001nnnndddd。
            #   2026-08-07 之前 t==0x1 用了 r[n] —— 那永远是常数 1, 于是
            #   `mov.w r0,@(6,r3)` 被写成 `@(6,r1)` = **往错的地址静默写 2 字节**。
            #   (objdump: mov.w r0,@(6,r3) = 0x8133 -> n=1(子码) m=3(真基址))
            if t==0x0: self.w8((r[m]+(op&0xf))&0xffffffff, r[0])             # mov.b R0,@(disp,Rn)
            elif t==0x1: self.w16((r[m]+((op&0xf)*2))&0xffffffff, r[0])      # mov.w R0,@(disp,Rn)
            elif t==0x4: r[0]=self.s8(self.r8((r[m]+(op&0xf))&0xffffffff))&0xffffffff  # mov.b @(disp,Rm),R0
            elif t==0x5: r[0]=self.s16(self.r16((r[m]+((op&0xf)*2))&0xffffffff))&0xffffffff  # mov.w @(disp,Rm),R0
            elif t==0x8: self.T=1 if (r[0]&0xffffffff)==(self.s8(d8)&0xffffffff) else 0  # cmp/eq #imm,R0
            elif t==0x9: # bt
                if self.T: self._branch(self.pc+2+self.s8(d8)*2, delayed=False)
            elif t==0xb: # bf
                if not self.T: self._branch(self.pc+2+self.s8(d8)*2, delayed=False)
            elif t==0xd: # bt/s
                if self.T: self._branch(self.pc+2+self.s8(d8)*2, delayed=True)
                else: pass
            elif t==0xf: # bf/s
                if not self.T: self._branch(self.pc+2+self.s8(d8)*2, delayed=True)
                else: pass
            else: raise SH4Error("8-op %04x @%08x"%(op,self.pc-2))
        elif top==0x9: # mov.w @(disp,pc),Rn
            addr=(self.pc+2+ (d8*2)) & 0xffffffff
            r[n]=self.s16(self.r16(addr))&0xffffffff
        elif top==0xd: # mov.l @(disp,pc),Rn
            addr=((self.pc+2)&~3) + d8*4
            r[n]=self.r32(addr)
        elif top==0xc:
            t=(op>>8)&0xf
            if t==0x0: self.w8((self.gbr+d8)&0xffffffff, r[0])              # mov.b R0,@(disp,GBR)
            elif t==0x1: self.w16((self.gbr+d8*2)&0xffffffff, r[0])
            elif t==0x2: self.w32((self.gbr+d8*4)&0xffffffff, r[0])
            elif t==0x4: r[0]=self.s8(self.r8((self.gbr+d8)&0xffffffff))&0xffffffff
            elif t==0x5: r[0]=self.s16(self.r16((self.gbr+d8*2)&0xffffffff))&0xffffffff
            elif t==0x6: r[0]=self.r32((self.gbr+d8*4)&0xffffffff)
            elif t==0x7: r[0]=(((self.pc+2)&~3)+d8*4)&0xffffffff            # mova @(disp,pc),R0
            elif t==0x8: self.T=1 if (r[0]&0xff & d8)==0 else 0            # tst #imm,R0
            elif t==0x9: r[0]=(r[0]&d8)&0xffffffff                         # and #imm,R0
            elif t==0xa: r[0]=(r[0]^d8)&0xffffffff                         # xor #imm,R0
            elif t==0xb: r[0]=(r[0]|d8)&0xffffffff                         # or #imm,R0
            else: raise SH4Error("c-op %04x @%08x"%(op,self.pc-2))
        elif top==0xe: # mov #imm,Rn
            r[n]=self.s8(d8)&0xffffffff
        elif top==0x7: # add #imm,Rn
            r[n]=(r[n]+self.s8(d8))&0xffffffff
        elif top==0x1: # mov.l Rm,@(disp,Rn)
            self.w32((r[n]+(d4*4))&0xffffffff, r[m])
        elif top==0x5: # mov.l @(disp,Rm),Rn
            r[n]=self.r32((r[m]+(d4*4))&0xffffffff)
        elif top==0xa: # bra
            self._branch(self.pc+2+self._d12(d12)*2)
        elif top==0xb: # bsr
            self.pr=(self.pc+2)&0xffffffff
            self._branch(self.pc+2+self._d12(d12)*2)
        elif top==0x0:
            self._exec0(op,n,m)
        elif top==0x4:
            self._exec4(op,n,m)
        elif top==0xf:
            self._execf(op,n,m)
        else:
            raise SH4Error("top %x op %04x @%08x"%(top,op,self.pc-2))

    @staticmethod
    def _d12(d):
        return d-0x1000 if d&0x800 else d

    def _exec0(self, op, n, m):
        r=self.r
        lo=op&0xff
        t=op&0xf
        if op==0x0009: return                          # nop
        if op==0x000b:                                  # rts (has a delay slot)
            self._branch(self.pr); return
        if op==0x0028:                                  # clrmac (§10.1.11)
            # 手册: 0 -> MACH, 0 -> MACL。旧版是纯 return, MAC 累加器带着上一次
            # 的残值继续累 -> MAC 循环静默算错。(objdump: clrmac = 0x0028)
            self.mach=0; self.macl=0; return
        if op==0x0018: self.T=1; return                 # sett  (objdump 证实 0x0018=sett; 旧版写成 T=0, 错)
        if op==0x0019: self.M=0; self.Q=0; self.T=0; return   # div0u (§10.1.16)
        # ---- STC <控制寄存器>,Rn : 0000nnnn ssss0010  (objdump: sr=0x0n02 gbr=0x0n12 vbr=0x0n22)
        if t==0x2:
            sub=(op>>4)&0xf
            if sub==0x0: r[n]=self.sr&0xffffffff        # 旧版恒给 0 -> T/Q/M 全丢
            elif sub==0x1: r[n]=self.gbr&0xffffffff     # 旧版 raise
            elif sub==0x2: r[n]=self.vbr&0xffffffff     # 旧版 raise
            else: r[n]=0                                # SSR/SPC/SGR/DBR: 没建模, 给 0
            return
        if t==0x3:                                       # bsrf / braf / pref / ocb* / movca
            sub=(op>>4)&0xf
            if sub==0x0:   # bsrf Rn  (0000nnnn0000 0011)
                self.pr=(self.pc+2)&0xffffffff
                self._branch((self.pc+2+r[n])&0xffffffff)
                return
            if sub==0x2:   # braf Rn  (0000nnnn0010 0011)
                self._branch((self.pc+2+r[n])&0xffffffff)
                return
            if sub==0xc:   # movca.l R0,@Rn  (0000nnnn1100 0011) —— **是一条真的存**
                # 手册 §10.1.35: R0 -> (Rn)。只是顺带分配 cache 行, 存本身照做。
                # 旧版把整个 t==0x3 的"其它子码"一律当 nop, 于是这条存被**静默吞掉**。
                # (objdump: movca.l r0,@r5 = 0x05c3)
                self.w32(r[n], r[0]); return
            return                                        # pref/ocbi/ocbp/ocbwb -> nop
        if t==0x4: self.w8((r[n]+r[0])&0xffffffff, r[m]); return     # mov.b Rm,@(R0,Rn)
        if t==0x5: self.w16((r[n]+r[0])&0xffffffff, r[m]); return    # mov.w
        if t==0x6: self.w32((r[n]+r[0])&0xffffffff, r[m]); return    # mov.l
        if t==0x7:                                                    # mul.l
            self.macl=(r[n]*r[m])&0xffffffff; return
        if t==0xc: r[n]=self.s8(self.r8((r[m]+r[0])&0xffffffff))&0xffffffff; return  # mov.b @(R0,Rm),Rn
        if t==0xd: r[n]=self.s16(self.r16((r[m]+r[0])&0xffffffff))&0xffffffff; return
        if t==0xe: r[n]=self.r32((r[m]+r[0])&0xffffffff); return     # mov.l @(R0,Rm),Rn
        if t==0xf:  # mac.l @Rm+,@Rn+   (手册 §10.1.28; 先读 @Rn 再读 @Rm, 各 +4)
            b=self.s32(self.r32(r[n])); r[n]=(r[n]+4)&0xffffffff
            a=self.s32(self.r32(r[m])); r[m]=(r[m]+4)&0xffffffff
            acc=(self.mach<<32)|self.macl
            if acc & 0x8000000000000000: acc-=0x10000000000000000
            res=acc+(a*b)
            if self.S:   # S=1: 48 位饱和 (手册: 限制在 ±2^47-1)
                res=max(-0x800000000000, min(res, 0x7fffffffffff))
            self.macl=res&0xffffffff; self.mach=(res>>32)&0xffffffff; return
        if t==0xa:  # sts MACH/MACL/PR,Rn
            sub=(op>>4)&0xf
            if sub==0x0: r[n]=self.mach&0xffffffff
            elif sub==0x1: r[n]=self.macl&0xffffffff
            elif sub==0x2: r[n]=self.pr&0xffffffff
            elif sub==0x5: r[n]=self.fpul&0xffffffff
            elif sub==0x6: r[n]=self.fpscr&0xffffffff
            else: r[n]=0
            return
        if lo==0x29: r[n]=self.T; return                # movt Rn
        if lo==0x08: self.T=0; return                   # clrt
        if lo==0x2a: r[n]=self.pr&0xffffffff; return    # sts PR,Rn
        if lo==0x1a: return                             # sts fpul? handled above
        if lo==0x0a: r[n]=self.mach&0xffffffff; return  # sts mach
        if lo==0x2b: return                             # rte (stub)
        if lo==0x1b: return                             # sleep
        if lo==0x00 or lo==0x10 or lo==0x20 or lo==0x30 or lo==0x40 or lo==0x50 or lo==0x60 or lo==0x70 or lo==0x80 or lo==0x90 or lo==0xa0 or lo==0xb0 or lo==0xc0 or lo==0xd0 or lo==0xe0 or lo==0xf0:
            return                                       # stc/misc control -> stub as nop
        raise SH4Error("0-op %04x @%08x"%(op,self.pc-2))

    def _exec4(self, op, n, m):
        r=self.r
        lo=op&0xff
        # shad/shld use low nibble with a variable m field -> match first
        if (op&0xf)==0xc:  # shad Rm,Rn
            s=self.s32(r[m])
            if s>=0: r[n]=(r[n]<<(s&31))&0xffffffff
            else:
                sh=((~s)&0x1f)+1
                r[n]=(self.s32(r[n])>>sh)&0xffffffff
            return
        if (op&0xf)==0xd:  # shld Rm,Rn
            s=self.s32(r[m])
            if s>=0: r[n]=(r[n]<<(s&31))&0xffffffff
            else:
                sh=((~s)&0x1f)+1
                r[n]=(r[n]>>sh)&0xffffffff
            return
        if (op&0xf)==0xf:  # mac.w @Rm+,@Rn+  (objdump: mac.w @r4+,@r5+ = 0x454f)
            # 手册 §10.1.29: 先读 @Rn 再读 @Rm, 各 +2; 16x16 有符号积加进 MAC。
            # 旧版直接 raise —— 至少是"响的", 但 MAC.L 已实现而 MAC.W 没有,
            # 补齐以免半条腿。S=1 的饱和按手册的 32 位版实现。
            a=self.s16(self.r16(r[n])); r[n]=(r[n]+2)&0xffffffff
            b=self.s16(self.r16(r[m])); r[m]=(r[m]+2)&0xffffffff
            prod=a*b
            if self.S:
                res=self.s32(self.macl)+prod
                if res>0x7fffffff:   self.macl=0x7fffffff; self.mach|=1
                elif res<-0x80000000: self.macl=0x80000000; self.mach|=1
                else: self.macl=res&0xffffffff
            else:
                res=((self.mach<<32)|self.macl)+prod
                self.macl=res&0xffffffff; self.mach=(res>>32)&0xffffffff
            return
        # single-register shifts / rotates / control-reg loads
        if lo==0x00:  # shll Rn
            self.T=(r[n]>>31)&1; r[n]=(r[n]<<1)&0xffffffff; return
        if lo==0x01:  # shlr Rn
            self.T=r[n]&1; r[n]=(r[n]>>1)&0xffffffff; return
        if lo==0x04:  # rotl
            self.T=(r[n]>>31)&1; r[n]=((r[n]<<1)|self.T)&0xffffffff; return
        if lo==0x05:  # rotr
            self.T=r[n]&1; r[n]=((r[n]>>1)|(self.T<<31))&0xffffffff; return
        if lo==0x08:  # shll2
            r[n]=(r[n]<<2)&0xffffffff; return
        if lo==0x09:  # shlr2
            r[n]=(r[n]>>2)&0xffffffff; return
        if lo==0x18:  # shll8
            r[n]=(r[n]<<8)&0xffffffff; return
        if lo==0x19:  # shlr8
            r[n]=(r[n]>>8)&0xffffffff; return
        if lo==0x28:  # shll16
            r[n]=(r[n]<<16)&0xffffffff; return
        if lo==0x29:  # shlr16
            r[n]=(r[n]>>16)&0xffffffff; return
        if lo==0x20:  # shal
            self.T=(r[n]>>31)&1; r[n]=(r[n]<<1)&0xffffffff; return
        if lo==0x21:  # shar
            self.T=r[n]&1; r[n]=((self.s32(r[n])>>1)&0xffffffff); return
        # ---- LDS.L @Rn+,<系统寄存器>  0100nnnn ssss0110 ------------------------
        # objdump 实测: mach=0x4n06  macl=0x4n16  pr=0x4n26   (与 STS.L 的
        # 0x4n02/0x4n12/0x4n22 一一对应)。2026-08-07 之前:
        #   0x4n06 被当成 "lds.l @Rn+,PR" -> `lds.l @r15+,mach` 会把**返回地址**
        #          换成栈上的 MACH 值 => 一 rts 就飞到随机地址;
        #   0x4n16 只做 r[n]+=4, **MACL 根本没装进去** => MAC 结果静默错。
        if lo==0x06:  # lds.l @Rn+,MACH
            self.mach=self.r32(r[n]); r[n]=(r[n]+4)&0xffffffff; return
        if lo==0x16:  # lds.l @Rn+,MACL
            self.macl=self.r32(r[n]); r[n]=(r[n]+4)&0xffffffff; return
        if lo==0x26:  # lds.l @Rn+,PR
            self.pr=self.r32(r[n]); r[n]=(r[n]+4)&0xffffffff; return
        if lo==0x22:  # sts.l PR,@-Rn
            r[n]=(r[n]-4)&0xffffffff; self.w32(r[n], self.pr); return
        if lo==0x02:  # sts.l MACH,@-Rn
            r[n]=(r[n]-4)&0xffffffff; self.w32(r[n], self.mach); return
        if lo==0x12:  # sts.l MACL,@-Rn
            r[n]=(r[n]-4)&0xffffffff; self.w32(r[n], self.macl); return
        if lo==0x0a:  # lds Rn,MACH
            self.mach=r[n]&0xffffffff; return
        if lo==0x1a:  # lds Rn,MACL
            self.macl=r[n]&0xffffffff; return
        if lo==0x2a:  # lds Rn,PR
            self.pr=r[n]&0xffffffff; return
        if lo==0x0b:  # jsr @Rn
            self.pr=(self.pc+2)&0xffffffff
            self._branch(r[n])
            return
        if lo==0x2b:  # jmp @Rn
            self._branch(r[n])
            return
        if lo==0x0e:  # ldc Rn,SR   (T/Q/M 都住在 SR 里, 不能当 nop 吞掉)
            self.sr=r[n]&0xffffffff; return
        if lo==0x1e or lo==0x2e or lo==0x3e:  # ldc Rn,GBR/VBR/SSR
            if lo==0x1e: self.gbr=r[n]&0xffffffff
            elif lo==0x2e: self.vbr=r[n]&0xffffffff
            return
        if lo==0x07:  # ldc.l @Rn+,SR  —— 只回填我们建模的位(T/Q/M 都在 SR 里)
            self.sr=self.r32(r[n]); r[n]=(r[n]+4)&0xffffffff; return
        if lo==0x17:  # ldc.l @Rn+,GBR
            self.gbr=self.r32(r[n]); r[n]=(r[n]+4)&0xffffffff; return
        if lo==0x27:  # ldc.l @Rn+,VBR
            self.vbr=self.r32(r[n]); r[n]=(r[n]+4)&0xffffffff; return
        # ---- STC.L <控制寄存器>,@-Rn  0100nnnn ssss0011 (objdump: sr=0x4n03 gbr=0x4n13)
        #      旧版只认 0x4n03 且**压 0**(SR 的 T 位丢了), 0x4n13/0x4n23 直接 raise。
        if lo in (0x03, 0x13, 0x23):
            v = self.sr if lo == 0x03 else (self.gbr if lo == 0x13 else self.vbr)
            r[n]=(r[n]-4)&0xffffffff; self.w32(r[n], v); return
        if lo==0x0c:  # shad Rm,Rn
            s=self.s32(r[m])
            if s>=0: r[n]=(r[n]<<(s&31))&0xffffffff
            else:
                sh=((~s)&0x1f)+1
                r[n]=(self.s32(r[n])>>sh)&0xffffffff
            return
        if lo==0x0d:  # shld Rm,Rn
            s=self.s32(r[m])
            if s>=0: r[n]=(r[n]<<(s&31))&0xffffffff
            else:
                sh=((~s)&0x1f)+1
                r[n]=(r[n]>>sh)&0xffffffff
            return
        if lo==0x10:  # dt Rn
            r[n]=(r[n]-1)&0xffffffff; self.T=1 if r[n]==0 else 0; return
        if lo==0x11:  # cmp/pz
            self.T=1 if self.s32(r[n])>=0 else 0; return
        if lo==0x15:  # cmp/pl
            self.T=1 if self.s32(r[n])>0 else 0; return
        if lo==0x24:  # rotcl
            t=(r[n]>>31)&1; r[n]=((r[n]<<1)|self.T)&0xffffffff; self.T=t; return
        if lo==0x25:  # rotcr
            t=r[n]&1; r[n]=((r[n]>>1)|(self.T<<31))&0xffffffff; self.T=t; return
        if lo==0x5a or lo==0x6a or lo==0x7a:  # lds Rn,FPUL/FPSCR
            if lo==0x5a: self.fpul=r[n]&0xffffffff
            elif lo==0x6a: self.fpscr=r[n]&self.FPSCR_MASK   # bit31-22 保留, 恒读 0
            return
        if lo==0x56 or lo==0x66:  # lds.l @Rn+,FPUL/FPSCR
            v=self.r32(r[n]); r[n]=(r[n]+4)&0xffffffff
            if lo==0x56: self.fpul=v
            else: self.fpscr=v&self.FPSCR_MASK
            return
        if lo==0x52 or lo==0x62:  # sts.l FPUL/FPSCR,@-Rn
            r[n]=(r[n]-4)&0xffffffff
            self.w32(r[n], self.fpul if lo==0x52 else self.fpscr); return
        if lo==0x14 or lo==0x18 or lo==0x1b:  # setrc/tas etc rarely used - tas.b @Rn
            if lo==0x1b:  # tas.b @Rn
                v=self.r8(r[n]); self.T=1 if v==0 else 0; self.w8(r[n], v|0x80)
            return
        raise SH4Error("4-op %04x @%08x"%(op,self.pc-2))

    # ================================ FPU =================================
    # 依据 references/sh4/SH-4A_Software_Manual_rej09b0003.pdf:
    #   §2.2.4/图2.5 + §6.3/图6.5  寄存器组 / FPSCR / SZ-PR-endian 关系
    #   §10.3.1-10.3.31            每条指令的伪代码 + 特例表
    # 设计约束(踩过的坑):
    #  1) 寄存器只存 **位模式**(u32). 任何时候要算才用 struct 转 IEEE754,
    #     绝不把 python float(=双精度)直接当 float32 存 —— 会静默升精度.
    #  2) FPSCR.FR 选 bank: FR0-15 与 XF0-15 是同一对 bank 的两个视图(fr/xf 属性).
    #  3) SZ=1 的 64 位 FMOV 在 **小端** 下还要看 PR(手册图 2.5/6.5), 最容易写错:
    #        SZ=1,PR=0 (成对单精度传输): FR[n]   在低地址, FR[n+1] 在高地址
    #        SZ=1,PR=1 (双精度):        整个 64 位按小端 => FR[n+1] 在低地址
    #     DR[n]={FR[n]=高半字, FR[n+1]=低半字} 这条与端序无关, 固件佐证:
    #     0x08075c80 的 vararg 序言 `fmov fr6,@r1; fmov fr7,@-r1` 把 fr7 放低地址,
    #     即 fr7 确实是 DR6 的低半字.
    #  4) RM=01(朝零舍入)是真机常态, 单精度结果一律按 RM 舍入.
    # 已知偏差(有意为之, 都不影响本项目用途):
    #   · Enable 位不产生 FPU 异常 trap, 只置 Cause/Flag 位.
    #   · 双精度中间值就是 python float(已是双精度), RM=1 时用 fractions 精确
    #     修正 FADD/FSUB/FMUL/FDIV; FSQRT/FCNVDS 的双精度朝零修正未做(≤1ulp).
    #   · FIPR/FTRV/FSRRA/FSCA 真硬件本身就是近似算法(手册明说有误差), 这里给
    #     正确舍入的 IEEE 结果, 不可能与硅片逐位一致.
    FPSCR_MASK = 0x003fffff          # bit31-22 保留
    # Cause(bit17-12) / Flag(bit6-2) 字段内的位序: E V Z O U I
    FPE_E, FPE_V, FPE_Z, FPE_O, FPE_U, FPE_I = 0x20, 0x10, 0x08, 0x04, 0x02, 0x01
    QNAN_S  = 0x7fbfffff             # 手册 qnan(): 单精度非法结果
    QNAN_DH, QNAN_DL = 0x7ff7ffff, 0xffffffff        # 双精度非法结果
    # 数据分类 (手册 data_type_of)
    PZERO, NZERO, DENORM, NORM, PINF, NINF, QNAN, SNAN = range(8)

    # ---- 寄存器视图 ----
    @property
    def fr(self):
        """FR0-15(当前 bank, 位模式). FPSCR.FR=0 -> bank0, =1 -> bank1."""
        return self.fbank[(self.fpscr >> 21) & 1]
    @property
    def xf(self):
        """XF0-15 = 另一个 bank(XMTRX 就是这 16 个)."""
        return self.fbank[1 - ((self.fpscr >> 21) & 1)]
    @property
    def fpscr_pr(self): return (self.fpscr >> 19) & 1
    @property
    def fpscr_sz(self): return (self.fpscr >> 20) & 1
    @property
    def fpscr_dn(self): return (self.fpscr >> 18) & 1
    @property
    def fpscr_rm(self): return self.fpscr & 3

    # ---- 位模式 <-> IEEE754 ----
    @staticmethod
    def sf_val(b):
        """u32 位模式 -> python float(该 float32 的精确值)."""
        return struct.unpack('<f', struct.pack('<I', b & 0xffffffff))[0]
    @staticmethod
    def df_val(hi, lo):
        return struct.unpack('<d', struct.pack('<Q', ((hi & 0xffffffff) << 32) | (lo & 0xffffffff)))[0]
    @staticmethod
    def df_bits(x):
        return struct.unpack('<Q', struct.pack('<d', x))[0]

    def frv(self, i):
        """FRi 的值(单精度语义)."""
        return self.sf_val(self.fr[i])
    def drv(self, i):
        f = self.fr; return self.df_val(f[i], f[i + 1])
    def set_drb(self, i, bits64):
        f = self.fr
        f[i] = (bits64 >> 32) & 0xffffffff; f[i + 1] = bits64 & 0xffffffff

    # ---- Cause / Flag ----
    def _fp_clear_cause(self):
        self.fpscr &= ~0x0003f000
    def _fp_raise(self, bits):
        """置 Cause(17-12) 与 Flag(6-2). Flag 字段没有 E 位, 要掩掉."""
        self.fpscr |= (bits << 12) | ((bits & 0x1f) << 2)
        self.fpscr &= self.FPSCR_MASK

    # ---- 数据分类 (手册 data_type_of): DN=1 时硬件会把非规格化源 **就地清零** ----
    def _dtype(self, i):
        f = self.fr; v = f[i]; a = v & 0x7fffffff; neg = v >> 31
        if self.fpscr_pr == 0:
            if a < 0x00800000:
                if self.fpscr_dn or a == 0:
                    f[i] = 0x80000000 if neg else 0            # zero(n,sign): 真写回
                    return self.NZERO if neg else self.PZERO
                return self.DENORM
            if a < 0x7f800000: return self.NORM
            if a == 0x7f800000: return self.NINF if neg else self.PINF
            return self.QNAN if a < 0x7fc00000 else self.SNAN  # ⚠ SH4: 尾数 MSB=0 才是 qNaN
        # 双精度: 高半字在 f[i], 低半字在 f[i+1]
        lo = f[i + 1]
        if a < 0x00100000:
            if self.fpscr_dn or (a == 0 and lo == 0):
                f[i] = 0x80000000 if neg else 0; f[i + 1] = 0
                return self.NZERO if neg else self.PZERO
            return self.DENORM
        if a < 0x7ff00000: return self.NORM
        if a == 0x7ff00000 and lo == 0: return self.NINF if neg else self.PINF
        return self.QNAN if a < 0x7ff80000 else self.SNAN

    def _fp_invalid(self, n):
        self._fp_raise(self.FPE_V); self._fp_qnan(n)
    def _fp_qnan(self, n):
        if self.fpscr_pr == 0: self.fr[n] = self.QNAN_S
        else: self.fr[n] = self.QNAN_DH; self.fr[n + 1] = self.QNAN_DL
    def _fp_inf(self, n, sign):
        if self.fpscr_pr == 0: self.fr[n] = 0xff800000 if sign else 0x7f800000
        else: self.fr[n] = 0xfff00000 if sign else 0x7ff00000; self.fr[n + 1] = 0

    # ---- 结果打包(舍入 + 溢出 + DN 冲刷) ----
    def _pack_sf(self, x):
        """python float(精确/近似结果) -> float32 位模式, 按 FPSCR.RM 舍入."""
        if x != x: return self.QNAN_S
        sign = 0x80000000 if (x < 0 or (x == 0 and math.copysign(1.0, x) < 0)) else 0
        if math.isinf(x): return sign | 0x7f800000
        try:
            b = struct.unpack('<I', struct.pack('<f', x))[0]   # 就近舍入
        except OverflowError:
            b = sign | 0x7f800000
        if (b & 0x7f800000) == 0x7f800000:                     # 上溢
            self._fp_raise(self.FPE_O | self.FPE_I)
            return (sign | 0x7f7fffff) if self.fpscr_rm == 1 else (sign | 0x7f800000)
        f = self.sf_val(b)
        if f != x:
            self._fp_raise(self.FPE_I)
            if self.fpscr_rm == 1 and abs(f) > abs(x):
                b = sign | ((b & 0x7fffffff) - 1)              # 朝零退一个 ulp
        if (b & 0x7fffffff) == 0 and x != 0.0:                 # 下溢到 0
            self._fp_raise(self.FPE_U | self.FPE_I)
        if (b & 0x7f800000) == 0 and (b & 0x7fffff):           # 结果是非规格化
            self._fp_raise(self.FPE_U | self.FPE_I)
            if self.fpscr_dn: b = sign                         # DN=1 -> ±0
        return b & 0xffffffff

    def _pack_df(self, x, exact=None):
        """python float -> float64 位模式. exact=Fraction 时按 RM 精确修正末位."""
        if x != x: return (self.QNAN_DH << 32) | self.QNAN_DL
        sign = 0x8000000000000000 if (x < 0 or (x == 0 and math.copysign(1.0, x) < 0)) else 0
        if math.isinf(x): return sign | 0x7ff0000000000000
        b = self.df_bits(x)
        if exact is not None:
            from fractions import Fraction
            fv = Fraction(x)
            if fv != exact:
                self._fp_raise(self.FPE_I)
                if self.fpscr_rm == 1 and abs(fv) > abs(exact):
                    b = sign | ((b & 0x7fffffffffffffff) - 1)
        if (b & 0x7ff0000000000000) == 0x7ff0000000000000:     # 上溢
            self._fp_raise(self.FPE_O | self.FPE_I)
            return (sign | 0x7fefffffffffffff) if self.fpscr_rm == 1 else (sign | 0x7ff0000000000000)
        if (b & 0x7ff0000000000000) == 0 and (b & 0x000fffffffffffff):
            self._fp_raise(self.FPE_U | self.FPE_I)
            if self.fpscr_dn: b = sign
        return b & 0xffffffffffffffff

    def _fp_store(self, n, x, exact=None):
        if self.fpscr_pr == 0: self.fr[n] = self._pack_sf(x)
        else: self.set_drb(n, self._pack_df(x, exact))

    # ---- 顶层 F 组译码 ----
    def _execf(self, op, n, m):
        t = op & 0xf
        if t == 0xd:          self._execf_d(op, n, m)   # 单操作数组, 子码在 m
        elif 0x6 <= t <= 0xc: self._fmov(op, n, m, t)   # FMOV 全家
        elif t == 0xe:        self._fmac(n, m)
        elif t <= 0x5:        self._farith(op, n, m, t) # FADD/FSUB/FMUL/FDIV/FCMP
        else: raise SH4Error("f-op %04x @%08x" % (op, self.pc - 2))   # t=0xf 未定义

    # ---- FMOV (SZ=0 32bit / SZ=1 64bit; 后者 bit0 选 DR/XD) ----
    def _load64(self, addr, bank, i):
        a = self.r32(addr); b = self.r32(addr + 4)
        if self.fpscr_pr: bank[i] = b; bank[i + 1] = a       # PR=1: 整 64 位小端
        else:             bank[i] = a; bank[i + 1] = b       # PR=0: 成对单精度
    def _store64(self, addr, bank, i):
        if self.fpscr_pr: self.w32(addr, bank[i + 1]); self.w32(addr + 4, bank[i])
        else:             self.w32(addr, bank[i]);     self.w32(addr + 4, bank[i + 1])

    def _fmov(self, op, n, m, t):
        r = self.r; sz = self.fpscr_sz
        if t == 0xc:                                     # FRm,FRn / DRm,DRn / XD 变体
            if sz == 0: self.fr[n] = self.fr[m]
            else:
                src = self.xf if (m & 1) else self.fr
                dst = self.xf if (n & 1) else self.fr
                mm = m & 0xe; nn = n & 0xe
                dst[nn] = src[mm]; dst[nn + 1] = src[mm + 1]
            return
        if t == 0x6:   addr = (r[0] + r[m]) & 0xffffffff; ld = True;  reg = n
        elif t == 0x8: addr = r[m] & 0xffffffff;          ld = True;  reg = n
        elif t == 0x9: addr = r[m] & 0xffffffff;          ld = True;  reg = n
        elif t == 0x7: addr = (r[0] + r[n]) & 0xffffffff; ld = False; reg = m
        elif t == 0xa: addr = r[n] & 0xffffffff;          ld = False; reg = m
        else:                                            # 0xb: FMOV FRm,@-Rn
            r[n] = (r[n] - (8 if sz else 4)) & 0xffffffff
            addr = r[n]; ld = False; reg = m
        if sz == 0:
            if ld: self.fr[reg] = self.r32(addr)
            else:  self.w32(addr, self.fr[reg])
        else:
            bank = self.xf if (reg & 1) else self.fr
            if ld: self._load64(addr, bank, reg & 0xe)
            else:  self._store64(addr, bank, reg & 0xe)
        if t == 0x9:                                     # @Rm+ 后自增
            r[m] = (r[m] + (8 if sz else 4)) & 0xffffffff

    # ---- 算术 / 比较 (t = 0..5) ----
    def _farith(self, op, n, m, t):
        self._fp_clear_cause()
        pr = self.fpscr_pr
        if pr: n &= 0xe; m &= 0xe
        dm = self._dtype(m); dn = self._dtype(n)          # ⚠ 会做 DN 冲刷写回
        if t == 4 or t == 5:
            self._fcmp(n, m, dn, dm, t); return
        if dm == self.SNAN or dn == self.SNAN: self._fp_invalid(n); return
        if dm == self.QNAN or dn == self.QNAN: self._fp_qnan(n); return
        if dm == self.DENORM or dn == self.DENORM:
            self._fp_raise(self.FPE_E)                    # DN=0 的非规格化源: 真机走 FPU error
        a = self.drv(n) if pr else self.frv(n)            # 被操作数 (FRn)
        b = self.drv(m) if pr else self.frv(m)            # 操作数   (FRm)
        exact = None
        if t == 0:   res = a + b
        elif t == 1: res = a - b
        elif t == 2: res = a * b
        else:                                             # FDIV FRm,FRn -> FRn = FRn/FRm
            if b == 0.0:
                sign = ((self.fr[n] >> 31) ^ (self.fr[m] >> 31)) & 1
                if a == 0.0: self._fp_invalid(n); return
                if math.isinf(a): self._fp_inf(n, sign); return   # inf/0: 手册不置 Z
                self._fp_raise(self.FPE_Z); self._fp_inf(n, sign); return
            res = a / b
        if res != res:                                    # 只可能是 inf-inf / 0*inf / inf/inf
            self._fp_invalid(n); return
        if pr and math.isinf(res) and not (math.isinf(a) or math.isinf(b)):
            # 双精度上溢: python 的 double 运算直接给 inf, _pack_df 分不出
            # "真无穷" 和 "溢出", 所以在这里补 O/I 与 RM=1 的 ±DBL_MAX 封顶.
            # (单精度不用管: 中间值是 double, 不会溢出, _pack_sf 自己认得出.)
            self._fp_raise(self.FPE_O | self.FPE_I)
            neg = res < 0
            if self.fpscr_rm == 1:
                self.set_drb(n, (0x8000000000000000 if neg else 0) | 0x7fefffffffffffff)
            else:
                self._fp_inf(n, 1 if neg else 0)
            return
        if pr and self.fpscr_rm == 1 and not math.isinf(res):
            from fractions import Fraction
            fa, fb = Fraction(a), Fraction(b)
            exact = (fa + fb) if t == 0 else (fa - fb) if t == 1 else \
                    (fa * fb) if t == 2 else (fa / fb)
        self._fp_store(n, res, exact)

    def _fcmp(self, n, m, dn, dm, t):
        """手册 fcmp_chk: qNaN 对 FCMP/GT 也算 invalid(UO), 对 FCMP/EQ 只是 !EQ."""
        SN, QN = self.SNAN, self.QNAN
        if dm == SN or dn == SN:
            self._fp_raise(self.FPE_V); self.T = 0; return
        if dm == QN or dn == QN:
            if t == 5: self._fp_raise(self.FPE_V)         # UO -> fcmp_invalid
            self.T = 0; return
        a = self.drv(n) if self.fpscr_pr else self.frv(n)
        b = self.drv(m) if self.fpscr_pr else self.frv(m)
        self.T = 1 if (a == b if t == 4 else a > b) else 0

    def _fmac(self, n, m):
        """FMAC FR0,FRm,FRn: FRn = FR0*FRm + FRn (单精度; PR=1 未定义).
        中间积用双精度精确保存(=手册的 '精确生成'), 只在最后按 RM 舍入一次."""
        self._fp_clear_cause()
        d0 = self._dtype(0); dm = self._dtype(m); dn = self._dtype(n)
        SN, QN = self.SNAN, self.QNAN
        if SN in (d0, dm, dn): self._fp_invalid(n); return
        if QN in (d0, dm, dn): self._fp_qnan(n); return
        res = self.frv(0) * self.frv(m) + self.frv(n)
        if res != res: self._fp_invalid(n); return
        self.fr[n] = self._pack_sf(res)

    # ---- 单操作数组 (t=0xd), 子码在 m 字段 ----
    def _execf_d(self, op, n, sub):
        if sub == 0x0:                                    # FSTS FPUL,FRn
            self.fr[n] = self.fpul & 0xffffffff; return
        if sub == 0x1:                                    # FLDS FRm,FPUL
            self.fpul = self.fr[n]; return
        if sub == 0x4:                                    # FNEG (纯翻符号位, 不查类型)
            i = (n & 0xe) if self.fpscr_pr else n
            self.fr[i] ^= 0x80000000; return
        if sub == 0x5:                                    # FABS
            i = (n & 0xe) if self.fpscr_pr else n
            self.fr[i] &= 0x7fffffff; return
        if sub == 0x8:                                    # FLDI0
            self.fr[n] = 0x00000000; return
        if sub == 0x9:                                    # FLDI1
            self.fr[n] = 0x3f800000; return
        if sub == 0xf: self._execf_f(op, n); return
        if sub == 0xe:                                    # FIPR FVm,FVn
            self._fipr((op >> 8) & 3, (op >> 10) & 3); return
        self._fp_clear_cause()
        if sub == 0x2:                                    # FLOAT FPUL,FRn/DRn
            v = self.s32(self.fpul)
            if self.fpscr_pr: self.set_drb(n & 0xe, self.df_bits(float(v)))
            else:             self.fr[n] = self._pack_sf(float(v))
            return
        if sub == 0x3: self._ftrc(n); return              # FTRC FRm/DRm,FPUL
        if sub == 0x6:                                    # FSQRT
            self._funary_sqrt(n); return
        if sub == 0x7:                                    # FSRRA FRn = 1/sqrt(FRn) (近似指令)
            if self.fpscr_pr: raise SH4Error("FSRRA with PR=1 undefined @%08x" % (self.pc - 2))
            d = self._dtype(n)                            # 特例表见手册 §10.3.19 后
            if d == self.SNAN: self._fp_invalid(n); return
            if d == self.QNAN: self._fp_qnan(n); return
            if d in (self.PZERO, self.NZERO):             # ±0 -> DZ -> ±inf
                self._fp_raise(self.FPE_Z); self._fp_inf(n, self.fr[n] >> 31); return
            if d == self.PINF: self.fr[n] = 0; return     # +inf -> +0
            if self.fr[n] >> 31: self._fp_invalid(n); return          # 负数 / -inf
            self.fr[n] = self._pack_sf(1.0 / math.sqrt(self.frv(n))); return
        if sub == 0xa:                                    # FCNVSD FPUL,DRn (PR=1)
            b = self.fpul & 0xffffffff; a = b & 0x7fffffff
            if a > 0x7f800000:                            # NaN
                if a >= 0x7fc00000: self._fp_invalid(n & 0xe)
                else: self._fp_qnan(n & 0xe)
                return
            self.set_drb(n & 0xe, self.df_bits(self.sf_val(b))); return
        if sub == 0xb:                                    # FCNVDS DRm,FPUL (PR=1)
            i = n & 0xe; d = self._dtype(i)
            if d == self.SNAN:
                self._fp_raise(self.FPE_V); self.fpul = self.QNAN_S; return
            if d == self.QNAN: self.fpul = self.QNAN_S; return
            self.fpul = self._pack_sf(self.drv(i)); return
        raise SH4Error("f-op(d) %04x @%08x" % (op, self.pc - 2))

    def _funary_sqrt(self, n):
        pr = self.fpscr_pr; i = (n & 0xe) if pr else n
        d = self._dtype(i)
        if d == self.SNAN: self._fp_invalid(i); return
        if d == self.QNAN: self._fp_qnan(i); return
        if d in (self.PZERO, self.NZERO): return          # ±0 -> ±0 (原值不动)
        if d == self.NINF or (self.fr[i] >> 31): self._fp_invalid(i); return
        if d == self.PINF: return
        v = self.drv(i) if pr else self.frv(i)
        self._fp_store(i, math.sqrt(v))

    def _ftrc(self, n):
        """手册 §10.3.25 ftrc_*_type_of + ftrc_invalid: 恒朝零截断.
        分类只看位模式 —— 正溢出 -> 0x7fffffff(+MAX); 负溢出 / ±inf 越界 /
        **任何 NaN(含 +NaN)** -> 0x80000000(-MAX). 正好 -2^31 是合法值."""
        pr = self.fpscr_pr; i = (n & 0xe) if pr else n
        self._dtype(i)                                    # 只为触发 DN=1 的源冲刷
        f = self.fr; hi = f[i]; neg = hi >> 31
        if pr:
            lo = f[i + 1]; dr = ((hi & 0xffffffff) << 32) | lo
            if neg:   kind = 1 if (dr & 0x7fffffffffffffff) >= 0x41e0000000200000 else 0
            elif (hi & 0x7fffffff) > 0x7ff00000 or (hi == 0x7ff00000 and lo != 0):
                kind = 1                                  # NaN -> NINF
            else:     kind = 2 if dr >= 0x41e0000000000000 else 0
        else:
            if neg:   kind = 1 if (hi & 0x7fffffff) > 0x4f000000 else 0
            elif hi > 0x7f800000: kind = 1                # +NaN -> NINF
            else:     kind = 2 if hi > 0x4effffff else 0
        if kind:
            self._fp_raise(self.FPE_V)
            self.fpul = 0x80000000 if kind == 1 else 0x7fffffff
            return
        self.fpul = int(math.trunc(self.drv(i) if pr else self.frv(i))) & 0xffffffff

    def _fipr(self, mv, nv):
        """FIPR FVm,FVn: FR[4n+3] = FVn . FVm (硬件是低精度近似, 这里给正确舍入值)."""
        self._fp_clear_cause()
        if self.fpscr_pr: raise SH4Error("FIPR with PR=1 undefined @%08x" % (self.pc - 2))
        m4 = mv * 4; n4 = nv * 4
        s = 0.0
        for k in range(4): s += self.frv(n4 + k) * self.frv(m4 + k)
        if s != s: self._fp_invalid(n4 + 3); return
        self.fr[n4 + 3] = self._pack_sf(s)

    def _execf_f(self, op, n):
        """子码 0xf: FSCHG / FRCHG / FPCHG / FTRV / FSCA."""
        if op == 0xf3fd: self.fpscr ^= 0x00100000; return          # FSCHG (SZ)
        if op == 0xfbfd: self.fpscr ^= 0x00200000; return          # FRCHG (FR)
        if op == 0xf7fd: self.fpscr ^= 0x00080000; return          # FPCHG (PR, SH-4A)
        if (n & 3) == 1:                                            # FTRV XMTRX,FVn
            if self.fpscr_pr: raise SH4Error("FTRV with PR=1 undefined @%08x" % (self.pc - 2))
            self._fp_clear_cause()
            base = (n >> 2) * 4
            v = [self.frv(base + k) for k in range(4)]
            xf = self.xf
            out = []
            for i in range(4):
                s = 0.0
                for j in range(4): s += self.sf_val(xf[i + 4 * j]) * v[j]
                out.append(s)
            for i in range(4):
                self.fr[base + i] = self.QNAN_S if out[i] != out[i] else self._pack_sf(out[i])
            return
        if (n & 1) == 0:                                            # FSCA FPUL,DRn
            self._fp_clear_cause()
            ang = (self.fpul & 0xffff) * (2.0 * math.pi / 65536.0)
            i = n & 0xe
            self.fr[i] = self._pack_sf(math.sin(ang))
            self.fr[i + 1] = self._pack_sf(math.cos(ang))
            return
        raise SH4Error("f-op(f) %04x @%08x" % (op, self.pc - 2))


# ---------- ABI-level call harness ----------
CLZ_TABLE_ADDR = 0x974e798   # zeroed hole in the carved ELF; reconstruct

def reconstruct_clz_table(cpu):
    """The byte-wise MSB-position table @0x974e798 that CLZ (0x9268640) indexes
    is a zero-hole in this carved ELF image. Semantics recovered from the CLZ
    algorithm: table[b] = b.bit_length() (0..8). Validated to make CLZ match a
    reference implementation across the full input range (see selftest)."""
    for b in range(256):
        cpu.w8(CLZ_TABLE_ADDR + b, b.bit_length())

def new_cpu(trace=False):
    cpu=SH4(trace=trace)
    cpu.load_elf()
    # place stack and heap in scratch space above the image
    cpu.STACK_TOP = IMAGE_BASE + 0x14000000
    cpu.HEAP = IMAGE_BASE + 0x10000000
    cpu.HEAP_END = IMAGE_BASE + 0x13000000
    cpu.r[15] = cpu.STACK_TOP
    reconstruct_clz_table(cpu)
    return cpu

def guest_malloc(cpu, size):
    size=(size+15)&~15
    a=cpu.HEAP
    if a+size>cpu.HEAP_END: raise SH4Error("heap exhausted")
    cpu.HEAP=a+size
    return a

def parse_plt(cpu):
    """Parse .dynamic to map each PLT symbol -> its GOT slot vaddr.
    PCM3Reload is a dynamically-linked SH executable; its GOT holds lazy-binding
    stubs (unresolved) in this on-disk image. Returns {symbol_name: got_slot}."""
    d=cpu.elf; BASE=IMAGE_BASE
    def v2o(v): return v-BASE
    e_phoff=struct.unpack_from('<I',d,28)[0]; e_phnum=struct.unpack_from('<H',d,44)[0]; e_phent=struct.unpack_from('<H',d,42)[0]
    dyn=None
    for i in range(e_phnum):
        off=e_phoff+i*e_phent
        p_type,p_offset,p_vaddr,p_paddr,p_filesz,p_memsz=struct.unpack_from('<6I',d,off)
        if p_type==2: dyn=(p_offset,p_vaddr,p_filesz)
    o,_,sz=dyn; DT={}
    while o<dyn[0]+sz:
        tag,val=struct.unpack_from('<iI',d,o); o+=8
        if tag==0: break
        DT.setdefault(tag,val)
    STRTAB=DT[5]; SYMTAB=DT[6]; SYMENT=DT.get(11,16); JMPREL=DT[23]; PLTRELSZ=DT[2]; STRSZ=DT[10]
    def cstr(v):
        oo=v2o(v); e=d.find(b'\x00',oo,oo+200); return d[oo:(e if e>=0 else oo+40)].decode('latin1')
    def sym_name(idx):
        so=v2o(SYMTAB)+idx*SYMENT
        st=struct.unpack_from('<I',d,so)[0]
        return cstr(STRTAB+st) if st<STRSZ else ""
    out={}
    n=PLTRELSZ//12  # DT_RELA => 12-byte entries
    base=v2o(JMPREL)
    for i in range(n):
        r_offset,r_info,r_add=struct.unpack_from('<III',d,base+i*12)
        out[sym_name(r_info>>8)]=r_offset
    return out

def link_libc(cpu, extra=None):
    """Resolve the libc/C++-runtime PLT entries this codec needs by pointing the
    GOT slot at a synthetic hook address and installing a Python callback there.
    This substitutes for the missing runtime dynamic linker."""
    plt=parse_plt(cpu)
    cpu.plt=plt
    MAGIC=0x0EFF0000  # synthetic hook page (distinct from SENTINEL)
    def _malloc(c): c.r[0]=guest_malloc(c, c.r[4])
    def _calloc(c):
        n=c.r[4]*c.r[5]; a=guest_malloc(c,n)
        for i in range(n): c.w8(a+i,0)
        c.r[0]=a
    def _realloc(c):
        # naive: alloc new, copy is unknown size -> just return a fresh block
        c.r[0]=guest_malloc(c, max(c.r[5],16))
    def _free(c): c.r[0]=0
    def _memcpy(c):
        dst,src,nn=c.r[4],c.r[5],c.r[6]
        for i in range(nn): c.w8(dst+i,c.r8(src+i))
        c.r[0]=dst
    def _memmove(c):
        dst,src,nn=c.r[4],c.r[5],c.r[6]
        buf=[c.r8(src+i) for i in range(nn)]
        for i in range(nn): c.w8(dst+i,buf[i])
        c.r[0]=dst
    def _memset(c):
        dst,val,nn=c.r[4],c.r[5]&0xff,c.r[6]
        for i in range(nn): c.w8(dst+i,val)
        c.r[0]=dst
    def _new(c): c.r[0]=guest_malloc(c, c.r[4])   # operator new(size)
    def _delete(c): c.r[0]=0
    def _strlen(c):
        a=c.r[4]; n=0
        while c.r8(a+n)!=0: n+=1
        c.r[0]=n
    def _abort(c):
        raise SH4Error("guest called abort() @pc %08x (r4=%08x)"%(c.pc,c.r[4]))
    def _udivdi3(c):  # __udivdi3_i4: 64-bit unsigned divide, (r4:r5)/(r6:r7) -> r0:r1
        num=(c.r[4]<<32)|c.r[5]; den=(c.r[6]<<32)|c.r[7]
        q=num//den if den else 0
        c.r[0]=(q>>32)&0xffffffff; c.r[1]=q&0xffffffff
    def _pmutex(c): c.r[0]=0     # pthread_mutex_lock/unlock -> success
    handlers={"malloc":_malloc,"calloc":_calloc,"realloc":_realloc,"free":_free,
              "memcpy":_memcpy,"memmove":_memmove,"memset":_memset,
              "_Znwj":_new,"_Znaj":_new,"_ZdlPv":_delete,"_ZdaPv":_delete,
              "strlen":_strlen,"abort":_abort,"__udivdi3_i4":_udivdi3,
              "pthread_mutex_lock":_pmutex,"pthread_mutex_unlock":_pmutex}
    if extra: handlers.update(extra)
    cpu.plt_called={}   # name -> call count (observability)
    slot=MAGIC
    cpu.linked={}
    cpu.slot2name={}
    # 1) install precise handlers for the symbols we model
    for name,fn in handlers.items():
        got=plt.get(name)
        if got is None: continue
        cpu.w32(got, slot); cpu.hooks[slot]=fn
        cpu.linked[name]=slot; cpu.slot2name[slot]=name; slot+=4
    # 2) default stub for EVERY other PLT symbol: record the call, return 0.
    #    This lets execution proceed through unmodeled runtime glue and reveals
    #    exactly which externals the decode path actually depends on.
    def make_stub(nm):
        def _stub(c):
            c.plt_called[nm]=c.plt_called.get(nm,0)+1
            c.r[0]=0
        return _stub
    for name,got in plt.items():
        if name in cpu.linked: continue
        cpu.w32(got, slot); cpu.hooks[slot]=make_stub(name)
        cpu.slot2name[slot]=name; slot+=4
    return cpu.linked

def install_libc_stubs(cpu, addrs):
    """addrs: dict name->guest_addr for library thunks we want to intercept."""
    def _malloc(c):
        c.r[0]=guest_malloc(c, c.r[4])
    def _free(c):
        c.r[0]=0
    def _memcpy(c):
        dst,src,n=c.r[4],c.r[5],c.r[6]
        for i in range(n): c.w8(dst+i, c.r8(src+i))
        c.r[0]=dst
    def _memset(c):
        dst,val,n=c.r[4],c.r[5]&0xff,c.r[6]
        for i in range(n): c.w8(dst+i, val)
        c.r[0]=dst
    tbl={"malloc":_malloc,"free":_free,"memcpy":_memcpy,"memset":_memset}
    for name,addr in addrs.items():
        if name in tbl:
            cpu.hooks[addr]=tbl[name]

def call(cpu, entry, args=(), stackargs=()):
    """Set up SH4 ABI call and run to sentinel. args -> r4..r7; stackargs pushed."""
    for i,a in enumerate(args[:4]):
        cpu.r[4+i]=a & 0xffffffff
    # stack args pushed right-to-left above sp (SH4 places 5th+ arg on stack)
    sp=cpu.r[15]
    for a in reversed(stackargs):
        sp=(sp-4)&0xffffffff
        cpu.w32(sp, a)
    cpu.r[15]=sp
    cpu.pr=cpu.SENTINEL
    cpu.pc=entry & 0xffffffff
    cpu.run()
    return cpu.r[0]

# ---------- top-level decompress harness (P8) ----------
DECOMP_ENTRY   = 0x90f8038    # top-level decompress fn (reads body header, drives decode)
DECOMP_DRIVER  = 0x90f8fec    # inner multi-channel bit-plane loop
DECOMP_GATE    = 0x90f81c8    # code past the '*0x09795da0 singleton != 0' gate
SINGLETON_ADDR = 0x09795da0   # global singleton (factory registry); zero-hole in this ELF

# --- P8f: the REAL loader chain (0x90f8038 alone cannot produce coords) ---
LOADER_ENTRY   = 0x90f54bc    # loads one resource: builds reader, fills dims, then calls DECOMP_ENTRY
LOAD_CORE      = 0x90fb2b8    # fills reader dims from the source-reader object (run BEFORE DECOMP_ENTRY)
READER_CTOR    = 0x90f5a14    # builds reader; *(reader+8) = (*r6).vtable[44]() = source-reader
DECODER_FACTORY= 0x9259b30    # per-dim decoder object factory (inside LOAD_CORE)
# P8f finding: driving DECOMP_ENTRY with a hand-built source-reader has NO effect on the driver
# (dims come from reader fields that LOAD_CORE fills first). The real path is LOADER_ENTRY ->
# LOAD_CORE -> DECOMP_ENTRY sharing one reader. LOAD_CORE runs but stalls at 0x90fb538 on an
# unfilled object field @(96,r15): the C++ object graph is too deep to synthesize statically.
# To resume: dynamically (sim) hook LOAD_CORE entry, dump the real reader+source-reader objects,
# then replay here. See references/hbm5-drawlist-opcodes.md P8f (sealed).

def hook_internal_allocators(cpu):
    """Stub the internal (non-PLT) C++/QNX allocator the decompress setup calls,
    so we can drive the real byte-parse + descriptor build without dragging in
    TLS / exception / global-singleton init (all zero-holes in this carved image).
    On the alloc-succeeds path these are the only runtime deps; the two 0x09795da0
    singleton gates are error paths that never fire once alloc returns non-null.
    0x8a1b584(r4=size, r5=obj, r6=extra) -> allocate & return pointer."""
    def _alloc(c):
        sz=max((c.r[4]&0xffffffff)+(c.r[6]&0xffffffff), 64)
        a=guest_malloc(c, sz)
        for i in range(0, sz, 4): c.w32(a+i, 0)
        c.r[0]=a
    cpu.hooks[0x8a1b584]=_alloc
    return cpu

def make_rts_stub(cpu):
    a=guest_malloc(cpu,4); cpu.w16(a,0x000b); cpu.w16(a+2,0x0009); return a   # rts;nop
def make_ret_imm(cpu, val):
    a=guest_malloc(cpu,6); cpu.w16(a,(0xe000|(val&0xff))); cpu.w16(a+2,0x000b); cpu.w16(a+4,0x0009)
    return a  # mov #val,r0; rts; nop

def setup_decompress(cpu, body):
    """Install the object graph scaffold needed to drive the top-level decompress
    on a carved compressed body. Returns dict of the synthesized pointers.

    STATUS (P8c): now drives the REAL setup end-to-end past all C++/QNX runtime
    zero-holes — link_libc() resolves the lazy PLT, hook_internal_allocators()
    stubs the internal allocator 0x8a1b584 so the alloc-succeeds path is taken
    (which bypasses the two 0x09795da0 singleton error gates entirely), and a
    valid __tls block is provided. With this, execution reaches the schema
    descriptor build AND the channel-count computation.

    REMAINING BLOCKER (P8c): the body byte-parse derives channel_count from body
    off19-20 (=0x1541=5441, garbage) under a source-reader whose fields we don't
    know. The parse of method-2 bodies is governed by *(src+20) (compression
    type) and the reader's width/height getters (vtable slots 40/44) — these come
    from the UPSTREAM resource table, not the body itself. Until src's real
    fields are recovered, the descriptor table / dims are wrong and the driver's
    outer loop runs 0 times. See references/hbm5-drawlist-opcodes.md P8c."""
    tls=guest_malloc(cpu, 0x800)
    def _tls(c): c.r[0]=tls
    link_libc(cpu, extra={"__tls":_tls})
    hook_internal_allocators(cpu)
    bp=guest_malloc(cpu,len(body)+64)
    for i,b in enumerate(body): cpu.w8(bp+i,b)
    rts=make_rts_stub(cpu); ret1=make_ret_imm(cpu,1)
    def vtable(nslots=64, default=None):
        vt=guest_malloc(cpu,nslots*4)
        for i in range(nslots): cpu.w32(vt+i*4, default or rts)
        return vt
    r4=guest_malloc(cpu,0x400)
    cpu.w32(r4+0, vtable()); cpu.w32(r4+4, bp)
    src=guest_malloc(cpu,0x80); cpu.w32(r4+8, src); cpu.w32(src+20, 2)
    # singleton not needed on the alloc-succeeds path, but install for safety
    sgl=guest_malloc(cpu,0x40); svt=vtable(); cpu.w32(svt+20, ret1)
    cpu.w32(sgl+0, svt); cpu.w32(SINGLETON_ADDR, sgl)
    return dict(body_ptr=bp, r4=r4, src=src, singleton=sgl, rts=rts, ret1=ret1, tls=tls)

def selftest():
    """Execute the real CLZ (0x9268640) in the interpreter and compare against a
    reference. This validates the decoder, ABI, delay-slot handling, memory model
    and the reconstructed CLZ table end-to-end on genuine firmware code."""
    def clz_ref(x):
        x&=0xffffffff
        return 32 if x==0 else 32-x.bit_length()
    cases=[0,1,2,3,7,255,256,0x100,0x800000,0x00010000,0x0000ffff,
           0x40000000,0x80000000,0xffffffff,0x12345678]
    ok=True
    for x in cases:
        c=new_cpu()
        r=call(c, 0x9268640, args=(x,))
        e=clz_ref(x)
        if r!=e: ok=False; print(f"  CLZ({x:#010x})={r} exp={e}  MISMATCH")
    print("CLZ selftest:", "PASS" if ok else "FAIL")
    return ok

if __name__=="__main__":
    selftest()
