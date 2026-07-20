/*
 * coexist_pop.c — PCM 弹出层引擎 · 跑在【独立 Carmine HW 层】上(取代写原厂共享 surface 0x1f)。
 *
 * 与老的 coexist_ui.c 的区别 = 这次我们**独占一层**:
 *   · 不再 ui_savebg / ui_composite / ui_restorebg 去动原厂 0x1f 的像素;
 *   · 直接把 ui_popbuf 刷进自己的 layer surface -> 物理上不与原厂 UI 争用, 零残影零抢夺。
 *
 * 硬件事实(2026-07-20 实测+RE, 详见 KB gf-independent-hwlayer-overlay-path):
 *   · **gf 层号被驱动反转: 硬件层号 = 7 − gf层号**。用 gf5(=硬件 L2, 干净通用层)。
 *     gf6 其实是硬件 L1(视频采集层), 驱动往它 Reserved 位写值 -> 高6位全死, 红色永远出不来。
 *   · 像素格式 = **RGBA5551**(R[15:11] G[10:6] B[5:1] A@bit0), 与 ui_core.c 的 ui_rgb() 一字不差。
 *     gf 报的 0x1710(ARGB1555) 是谎报, 硬件按驱动写死的 LnEC=10 扫。
 *   · dst viewport 高度算法不对称: 宽=x2-x1+1, **高=y2-y1(无+1)**。
 *   · gf_layer_set_blending 只收 9 个 mode; SRC_PIXEL_ALPHA(0x00010102) **会被静默拒绝**。
 *     v1 不调它(默认=不透明), 层矩形 == 面板矩形, 所以逐像素透明用不上。
 *     v2 可走 GF_ALPHA_M1_MAP(0x00080102)拿真 8bit alpha 平面做软边/投影。
 *   · gf_layer_update 返回码是硬编码 0, **不能当成功判据**。
 *
 * 迭代方式: ui.def 热加载(每秒查 FNV, 变了就重解析重画) —— 改布局/配色只推 348 字节文本,
 *           不用重编重推 66KB 二进制。
 */
typedef unsigned int   u32;
typedef signed   int   s32;
typedef unsigned short u16;
typedef unsigned char  u8;
typedef unsigned long  size_t;

#include "gf_defs.h"
#include "ui_core.c"      /* 共享渲染核心(与 Mac 预览器同一份) */

#define O_RDONLY 0x000
#define O_RDWR   0x002
#define O_WRONLY 0x001
#define O_CREAT  0x100
#define O_TRUNC  0x200
#define O_APPEND 0x008

extern int  open(const char *p, int f, int m);
extern int  read(int fd, void *b, unsigned n);
extern int  write(int fd, const void *b, unsigned n);
extern int  close(int fd);
extern int  usleep(unsigned us);
extern int  devctl(int fd, int cmd, void *d, unsigned n, unsigned *i);
extern long lseek(int fd, long o, int w);

#define LM_DEV      "/dev/layermanager"
#define LM_FLAGS    0x2002
#define LM_CHECKVER 0xc00c0506u

#define SCRW 800
#define SCRH 480
#define GF_LAYER 5            /* = 硬件 L2 */

#define DEF_PATH "/tmp/ui.def"
#define VAL_PATH "/tmp/uival"   /* 单独一个文件放当前值 -> 串口 echo 就能改, 不用重推 */
#define LOG_PATH "/tmp/pop.txt"
#define PID_FILE "/tmp/p3pid"     /* PCM3Root 的 pid, 由外部 pidin 解出写入 */

/* ---- V4 音量链(【原样】抄自已在台架验证的 coexist_vol.c v37, 别改数) ----
 * 注意 0x218 在这里是**结构校验偏移**, 不是音量。真正的音量在 P+0x7c。
 * (KB 血教训: 曾把 0x218 当"主音量", 真相是短信提示音 slot9。) */
#define V4_VVT   0x085c76fcu   /* u32@V  = vtable */
#define V4_OVT   0x085c4c5cu   /* u32@(V-0x218) */
#define V4_BACK  0x160         /* u32@(V+0x160) == V-0x218  <- 校验 */
#define V4_DELTA 0x218
#define V4_PROXY 0x168         /* P = u32@(V+0x168) */
#define V4_OK    0xc8          /* u32@(P+0xc8) == 2 = DATA_OK */
#define V4_SRC   0x74          /* s32; ∈{34,35} = 铃声, 要丢掉 */
#define V4_VOL   0x7c          /* u8, 0..40 <- 主音量 */
#define V2_VT    0x08619818u
#define V2_VT2   0x08619934u
#define V2_VOL   0x60
#define HEAP_LO  0x0866e200u   /* 只扫堆: .text 里有 vtable 字面量, 栈上有析构残留 */
#define HEAP_HI  0x08a00000u
#define SCAN_CHUNK 0x10000
#define VOL_MAX  40

/* ---------------- 日志 ---------------- */
static int g_first=1;
static unsigned slen(const char *s){ unsigned n=0; while(s[n])n++; return n; }
static void L(const char *s){
    int fd=open(LOG_PATH, g_first?(O_WRONLY|O_CREAT|O_TRUNC):(O_WRONLY|O_CREAT|O_APPEND), 0644);
    g_first=0; if(fd>=0){ write(fd,s,slen(s)); close(fd); }
}
static void Lh(u32 v){ const char *h="0123456789abcdef"; char b[11]; int i;
    b[0]='0'; b[1]='x'; for(i=0;i<8;i++) b[2+i]=h[(v>>(28-i*4))&0xf]; b[10]=0; L(b); }
static void Ld(int v){ char b[13]; int i=12,neg=0; b[12]=0;
    if(v<0){neg=1;v=-v;} if(!v)b[--i]='0'; else while(v){b[--i]=(char)('0'+v%10);v/=10;} if(neg)b[--i]='-'; L(b+i); }
static void hang(void){ for(;;) usleep(5000000); }

/* ---------------- 小工具 ---------------- */
static int slurp(const char *path, char *buf, int cap){
    int fd=open(path,O_RDONLY,0); if(fd<0) return -1;
    int n=read(fd,buf,cap-1); close(fd);
    if(n<0) n=0; buf[n]=0; return n;
}

/* ============ /proc/<pid>/as 只读(不碰任何 IPC/IOC 通道 —— 那条挂死过车和台架) ============ */
static int g_as=-1; static u32 g_V=0, g_P=0, g_P2=0;

/* gf 句柄(文件级, 供 push_layer 用) */
static gf_display_t g_disp=0; static gf_layer_t g_layer=0; static gf_surface_t g_surf=0;
static unsigned g_order[8]={0,1,2,3,4,5,6,7};

/* 唯一的上屏出口。**每次 update 之前必须走完整重申序列** ——
 * 实测只调 gf_layer_update 会永久 REPLY-block 在 gdcServerCarmine(pid 4104):
 * 没有 pending 变更时它在等一个永远不来的 vsync。
 * 之前跑了 6500+ 次循环没事的 gflayer 探针, 每次 update 前都做了完整重申。 */
static void push_layer(int x,int y,int w,int h){
    gf_layer_set_surfaces(g_layer,&g_surf,1);
    gf_layer_set_src_viewport(g_layer, 0, 0, w-1, h-1);
    /* RE: 驱动 宽=x2-x1+1, 高=y2-y1(无+1) */
    gf_layer_set_dst_viewport(g_layer, x, y, x+w-1, y+h);
    gf_display_set_layer_order(g_disp, g_order, 0);
    gf_layer_enable(g_layer);
    gf_layer_update(g_layer, 0);
}

static int open_as(int pid){
    if(pid<=0) return -1;
    char p[40]; const char *a="/proc/", *b="/as"; int i=0,j,k=0,n=0; char t[12],num[12];
    while(a[i]){ p[i]=a[i]; i++; }
    { int q=pid; if(!q) num[n++]='0'; else { while(q){ t[k++]=(char)('0'+q%10); q/=10; } while(k) num[n++]=t[--k]; } }
    for(j=0;j<n;j++) p[i++]=num[j];
    j=0; while(b[j]) p[i++]=b[j++]; p[i]=0;
    int fd=open(p,O_RDONLY,0); if(fd<0) fd=open(p,O_RDWR,0);   /* QNX /proc/as 可能要 RDWR; 我们只读 */
    return fd;
}
static int as_rd(u32 va, void *buf, int n){
    if(g_as<0) return -1;
    if(lseek(g_as,(long)va,0)!=(long)va) return -1;
    return read(g_as,buf,(unsigned)n);
}
static int rd_u32(u32 va,u32 *o){ u32 v=0; if(as_rd(va,&v,4)!=4) return -1; *o=v; return 0; }
static int rd_u8 (u32 va,u8  *o){ u8  v=0; if(as_rd(va,&v,1)!=1) return -1; *o=v; return 0; }

static u8 g_scan[SCAN_CHUNK];
static void locate(void){
    u32 va; g_V=g_P=g_P2=0;
    for(va=HEAP_LO; va<HEAP_HI && !(g_V&&g_P2); va+=SCAN_CHUNK-4){
        int got=as_rd(va,g_scan,SCAN_CHUNK); if(got<8) continue;
        int j;
        for(j=0;j+3<got;j+=4){
            u32 w=(u32)g_scan[j]|((u32)g_scan[j+1]<<8)|((u32)g_scan[j+2]<<16)|((u32)g_scan[j+3]<<24);
            u32 X=va+(u32)j,t1,t2;
            if(w==V4_VVT && !g_V){
                if(rd_u32(X+V4_BACK,&t1)==0 && t1==X-V4_DELTA &&
                   rd_u32(X-V4_DELTA,&t2)==0 && t2==V4_OVT) g_V=X;
            } else if(w==V2_VT && !g_P2){
                if(rd_u32(X+0xd0,&t1)==0 && t1==V2_VT2) g_P2=X;
            }
        }
    }
    if(g_V) rd_u32(g_V+V4_PROXY,&g_P);
    L("[V4] V="); Lh(g_V); L(" P="); Lh(g_P); L(" P2="); Lh(g_P2); L("\n");
}
/* 返回 0..40, 或 -1 表示读不到 */
static int read_vol(void){
    int v4=-1,v2=-1;
    if(g_P){ u32 ok=0; u8 b=0;
        if(rd_u32(g_P+V4_OK,&ok)==0 && ok==2 && rd_u8(g_P+V4_VOL,&b)==0 && b<=VOL_MAX){
            s32 src=0;
            if(rd_u32(g_P+V4_SRC,(u32*)&src)==0 && (src==34||src==35)) v4=-1;  /* 铃声, 丢 */
            else v4=(int)b; } }
    if(g_P2){ u8 b=0; if(rd_u8(g_P2+V2_VOL,&b)==0 && b<=VOL_MAX) v2=(int)b; }
    return v4>=0? v4 : v2;      /* V4 为准, V2 兜底 */
}

/* ============ 取值层: 去抖 + 掉线重定位 + 覆盖检测 ============ */

/* 去抖: 连续 2 次同值才认。coexist_vol.c 记录 idle 时 volumeInfo 缓存会在 19/20 之间
 * 自己抖 —— 没有去抖的话没人碰旋钮弹窗也会每几秒自己蹦一次。返回 0..40 或 -1(未稳)。*/
static int g_cand=-1, g_stable=0;

/* 掉线重定位: 连续 10s(100 拍 @100ms)读不到才重扫堆。
 * 阈值要比一次来电长 —— 铃声期间 read_vol() 按 src∈{34,35} 合法返 -1,
 * 太短会被来电白白触发一次 3.57MB 全堆扫。
 * ⚠ 不要照抄 coexist_vol.c 的重扫守卫, 那段是死代码(vol 从未赋值 -> 恒真 -> 无条件全扫)。*/
static int g_fail=0;
#define RELOC_FAIL_TICKS 200   /* 50ms/tick -> 10s */

/* shown=1 表示弹窗正显示着, 即用户正在拧 -> 立即跟随, 不去抖。
 * 去抖只用来把关"从隐藏状态弹出来"这一下 —— 它防的是没人碰时缓存在 19/20 之间
 * 自己抖导致弹窗乱蹦, 而不是防拧旋钮。连续拧时每拍值都在变, 去抖会一直不满足,
 * 结果就是"转的过程中完全不更新, 停手才画", 手感上就是不跟手。 */
static int vol_tick(int shown){
    int raw = read_vol();
    if(raw < 0){
        if(g_fail < RELOC_FAIL_TICKS) g_fail++;
        if(g_fail >= RELOC_FAIL_TICKS && g_as >= 0){
            g_fail = 0; L("[V4] 连续 10s 读不到 -> 重扫堆\n");
            locate(); g_cand=-1; g_stable=0;
        }
        return -1;
    }
    g_fail = 0;
    if(raw == g_cand){ if(g_stable < 9) g_stable++; }
    else { g_cand = raw; g_stable = 0; }
    if(shown) return raw;                    /* 正在拧: 立即跟随 */
    return (g_stable >= 1) ? g_cand : -1;    /* 从隐藏弹出: 要连续两次同值 */
}

/* /tmp/uival 手动覆盖(测渲染用, 不去抖 —— 要跟手)。覆盖态翻转时打日志,
 * 免得测试残留的文件把活体链永久锁死却毫无提示。*/
static int g_ovr=-1;
static int read_override(void){
    char vb[32]; int m = slurp(VAL_PATH, vb, sizeof(vb));
    int on = (m>0 && vb[0]>='0' && vb[0]<='9');
    if(on != g_ovr){
        g_ovr = on;
        L(on ? "[uival] 手动覆盖【开】—— 活体音量已旁路, 删掉 /tmp/uival 才恢复\n"
             : "[uival] 手动覆盖【关】-> 回到活体 V4 链\n");
    }
    if(!on) return -1;
    int v=0,i=0; for(; vb[i]>='0'&&vb[i]<='9'; i++) v=v*10+(vb[i]-'0');
    return (v<=VOL_MAX)? v : VOL_MAX;
}


/* 把 popbuf(颜色) + cov(8bit 覆盖度) 刷进层 surface。
 * 层矩形 == 面板矩形且不开 blending => 整层不透明, 覆盖度只用于二值化边界。
 * 阈值取 128: 面板内部 cov=255 全部原样(文字抗锯齿是在面板底色上软件混好的, 不受影响),
 * 只有面板外缘/圆角那一圈会被切成硬边 —— 这是 1bit alpha 的固有代价。 */
static void blit_layer(UIWidget *w, u16 *dst, int pitch){
    int y,x,W=w->w,H=w->h;
    for(y=0;y<H;y++){
        u16  *d  = dst + (size_t)y*(size_t)pitch;
        u16_ *pb = ui_popbuf + y*W;
        u8_  *cv = ui_cov    + y*W;
        for(x=0;x<W;x++)
            d[x] = (cv[x]>=128) ? (u16)(pb[x]|1u) : (u16)0x0000u;  /* A=0 = 真透明 */
        d[W] = 0x0000u;                          /* 右邻一列: 防硬件多扫出未写内存 */
    }
    { u16 *d = dst + (size_t)H*(size_t)pitch;    /* 下邻一行: 同上 */
      for(x=0;x<=W;x++) d[x] = 0x0000u; }
}

int main(void){
    L("COEXIST_POP v7 — 清掉收起后的残留白线(surface 清零 + 多清一行一列)\n");

    /* 0. layermanager 握手(跑通的代码都是这个顺序; 少了它 gf_dev_attach 会阻塞) */
    int lmfd=open(LM_DEV, LM_FLAGS, 0);
    L("lmfd="); Ld(lmfd); L("\n");
    if(lmfd>=0){ int cv[3]={0,0,0}; devctl(lmfd,(int)LM_CHECKVER,cv,12,0); }

    /* 1. 设备 + 显示 */
    static u8 devinfo[512]; gf_dev_t dev=0;
    if(gf_dev_attach(&dev, GF_DEVICE_INDEX(0), (gf_dev_info_t*)devinfo)!=GF_ERR_OK){ L("ABORT dev_attach\n"); hang(); }
    static u8 dispinfo[512]; gf_display_t disp=0;
    if(gf_display_attach(&disp, dev, 0, (gf_display_info_t*)dispinfo)!=GF_ERR_OK){ L("ABORT display_attach\n"); hang(); }
    gf_display_info_t *di=(gf_display_info_t*)dispinfo;
    L("display "); Ld((int)di->xres); L("x"); Ld((int)di->yres); L(" nlayers="); Ld((int)di->nlayers); L("\n");

    /* 2. 抢 gf5(=硬件 L2) */
    gf_layer_t layer=0;
    { int r=gf_layer_attach(&layer, disp, GF_LAYER, GF_LAYER_ATTACH_PASSIVE);
      L("layer_attach(gf"); Ld(GF_LAYER); L(") r="); Ld(r); L("\n");
      if(r!=GF_ERR_OK){ L("ABORT 抢不到层\n"); hang(); } }

    /* 3. 一次建足够大的 surface(UI_MAXW x UI_MAXH), 之后靠 src/dst viewport 裁剪定位
     *    -> ui.def 改几何时不需要重建 surface。 */
    gf_surface_t surf=0;
    { int r=gf_surface_create_layer(&surf,&layer,1,0, UI_MAXW, UI_MAXH, (gf_format_t)0x1710, (void*)0, 0);
      L("surface_create_layer("); Ld(UI_MAXW); L("x"); Ld(UI_MAXH); L(") r="); Ld(r); L("\n");
      if(r!=GF_ERR_OK){ L("ABORT 建 surface 失败\n"); hang(); } }
    static u8 sinfo[128];
    if(!gf_surface_get_info(surf,(gf_surface_info_t*)sinfo)){ L("ABORT get_info NULL\n"); hang(); }
    gf_surface_info_t *si=(gf_surface_info_t*)sinfo;
    if(!si->vaddr){ L("ABORT vaddr=0\n"); hang(); }
    int pitch=(int)si->stride/2;
    /* 整块 surface 先清零: 我们只用左上角一小块, 但 dst viewport 的高度算法不对称
     * (RE: 高=y2-y1 无+1), 硬件有可能多扫一行/一列。没清过的地方是未初始化内存,
     * 扫出来就是屏上一条杂色残留线(实测: 弹窗消失后下方留一条白线)。 */
    { u16 *z=(u16*)(size_t)si->vaddr; int n;
      for(n=0; n<pitch*UI_MAXH; n++) z[n]=0x0000u; }
    L("surf vaddr="); Lh((u32)si->vaddr); L(" stride="); Ld((int)si->stride); L(" pitch="); Ld(pitch); L("\n");

    /* 3b. 接活体音量: /proc/<PCM3Root pid>/as 只读 */
    { char pb[16]; int n=slurp(PID_FILE,pb,sizeof(pb)); int pid=-1;
      if(n>0){ int v=0,i=0,any=0; while(pb[i]>='0'&&pb[i]<='9'){ v=v*10+(pb[i]-'0'); i++; any=1; }
               if(any) pid=v; }
      L("[V4] pid="); Ld(pid); L("\n");
      if(pid>0){ g_as=open_as(pid); L("[V4] /proc/as fd="); Ld(g_as); L("\n");
                 if(g_as>=0) locate(); }
      if(!g_P && !g_P2) L("[V4] 没定位到音量对象 -> 只能用 /tmp/uival 手动值\n"); }

    g_disp=disp; g_layer=layer; g_surf=surf;

    /* 4. 主循环: 热加载 ui.def + 读值 + 渲染 + 刷层 */
    static char defbuf[4096];
    static UIWidget W;
    unsigned last_def_hash=0; int last_val=-99999; int have=0;
    { int k,w2=0,no=(int)di->nlayers; if(no<1||no>8) no=8;
      for(k=0;k<no;k++) if(k!=GF_LAYER) g_order[w2++]=(unsigned)k;
      if(w2<8) g_order[w2++]=(unsigned)GF_LAYER; }

    int t=0, shown=0, hold_left=0;
    int hold_ticks = 28;                 /* 50ms/tick -> 1.4s, 之后按 def 的 hold 覆盖 */
    for(;;){
        /* --- ui.def 热加载(每 ~1s 查一次即可, 不用每 100ms) --- */
        if((t%20)==0){
            int n=slurp(DEF_PATH, defbuf, sizeof(defbuf));
            if(n>0){
                unsigned h=ui_fnv(defbuf,n);
                if(h!=last_def_hash){
                    last_def_hash=h;
                    ui_defaults(&W);
                    if(ui_parse(defbuf,n,&W)){
                        if(W.w>UI_MAXW) W.w=UI_MAXW;
                        if(W.h>UI_MAXH) W.h=UI_MAXH;
                        ui_anchor(&W,SCRW,SCRH);
                        if(W.hold>0) hold_ticks = W.hold/50;
                        have=1; last_val=-99999;
                        L("[def] 重载 "); Ld(W.w); L("x"); Ld(W.h);
                        L(" @("); Ld(W.x); L(","); Ld(W.y); L(") hold="); Ld(hold_ticks); L("tick\n");
                    } else L("[def] 解析失败\n");
                }
            }
        }

        /* --- 取值: 手动覆盖优先, 否则活体去抖值 --- */
        int val = read_override();
        if(val < 0) val = vol_tick(shown);

        /* --- 值变了: 画 + 显示 + 重置保持计时 --- */
        if(have && val>=0 && last_val==-99999){
            last_val = val;               /* 首个有效值只播种, 开机/热重载不弹窗 */
            L("[seed] val="); Ld(val); L("\n");
        } else if(have && val>=0 && val!=last_val){
            last_val=val;
            ui_render(&W, val);
            blit_layer(&W,(u16*)(size_t)si->vaddr,pitch);
            push_layer(W.x,W.y,W.w,W.h);
            shown=1; hold_left=hold_ticks;
            L("[draw] val="); Ld(val); L("\n");
        }

        /* --- 保持到期 -> 收起 --- */
        if(shown){
            if(hold_left>0) hold_left--;
            else {
                /* ⚠ 不能用 gf_layer_disable + update 收起 —— 实测会永久 REPLY-block 在
                 * gdcServerCarmine(pid 4104)。改成把可见区刷成全透明, 层保持 enable。 */
                /* 多清 2 行 2 列: 硬件可能比我们认为的多扫一行(见上面的 off-by-one 说明) */
                int yy,xx, ch=W.h+2, cw=W.w+2;
                if(ch>UI_MAXH) ch=UI_MAXH;
                if(cw>pitch)   cw=pitch;
                u16 *d0=(u16*)(size_t)si->vaddr;
                for(yy=0;yy<ch;yy++){ u16 *d=d0+(size_t)yy*(size_t)pitch;
                    for(xx=0;xx<cw;xx++) d[xx]=0x0000u; }
                push_layer(W.x,W.y,W.w,W.h);
                shown=0; L("[hide]\n");
            }
        }

        /* --- 显示期间低频重申层序, 防被 layermanager 挤掉 --- */
        t++;
        if(shown && (t%20)==0) push_layer(W.x,W.y,W.w,W.h);
        usleep(50000);          /* 20Hz 轮询 */
    }
    return 0;
}
