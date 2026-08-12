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
extern void exit(int code);
extern long lseek(int fd, long o, int w);
extern int  unlink(const char *p);
extern int  getpid(void);            /* v29b 单实例锁用 */

#define LM_DEV      "/dev/layermanager"
#define LM_FLAGS    0x2002
#define LM_CHECKVER 0xc00c0506u

#define SCRW 800
#define SCRH 480
/* ★ v20 运行时选层(不再硬编码 gf5)。硬件层号 = 7 − gf层号。
 *
 * 2026-08-04 台架全层普查(221 秒重度操作, 1892 次记录变化)判定:
 *   gf5 = 硬件L2 —— **不是空闲层**! 原厂页面过渡时会把 d0L3 那个 800x480 全屏 surface
 *                   同时写进 L2 把我们顶掉; 车上雷达是持续 overlay 所以我们每帧都输
 *                   -> 弹窗按 pitch=800 扫我们 544 宽的内存 = 斜切噪点带。
 *   gf1 = 硬件L6 —— 普查里唯一【全程零变化、零占用】的 RGB 层, gftest 肉眼验证
 *                   红绿蓝白四色全对且盖在原厂画面之上。==> **首选**
 * 黑名单(永不选): gf6=硬件L1(视频采集层, 驱动占高6位 -> 红色出不来)
 *                 gf0=硬件L7(主帧缓冲) gf7=硬件L0 gf2/gf3/gf4(原厂常驻)
 * ⚠ 车是 9X1 变体, L6 在车上是否同样空闲【台架证不了】-> 位移探测器必须留着当安全网。 */
static const int GF_CAND[] = { 1, 5 };
#define GF_NCAND 2
static int GF_LAYER = 1;      /* 实际选中的 gf 层号, 由 pick_layer() 定 */

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
/* v17: 0x08a00000 太低。PCM3Root 是 16MB+ 大进程, 开一段路后堆增长, 音量对象会漂到
 * 0x09xxxxxx 以上 -> 旧窗口扫不到 -> 弹窗永久不出(两版共病, 见 vol_tick 的永不判死修)。
 * 放宽到 0x0c000000(base+64MB, 远低于 MMIO 0x38000000)。as_rd 遇未映射空洞会跳过, 便宜。*/
#define HEAP_HI  0x0c000000u
#define SCAN_CHUNK 0x10000
#define VOL_MAX  40

/* ---- 雷达/倒车检测(CPBackFacilitiesPresCtrl in PCM3Root) ----
 * 反汇编实锤(工作流 radar-overlay-holistic-fix, chn400 stock): 构造函数
 * @0x825637c 存 0x85b4b10->obj+0, 0x85b4ba8->obj+0x14。全 ELF 只 3 处 0x85b4b10
 * 且都在 0x0866e1fc 以下(扫描窗口外), 不会误命中。
 * 检测位(this 相对偏移, 全 u32 读):
 *   mDecoderActive @+0x4fc  —— 主门(它一关 RVC+PDC 都关, 窗口最宽, 最可能覆盖"雷达后持续")
 *   currentDisplayMode @+0x4f0 ∈{6,7,12} —— back-facilities 显示模式(12=Lastmode 粘滞)
 *   mRVCActiveFlag @+0x46c / mPdcActiveFlag @+0x474 —— 佐证
 * 车上 build 不匹配则扫不中 -> g_bf=0 -> 永不隐藏(安全降级回 v15 行为)。 */
#define BF_VT   0x085b4b10u
#define BF_VT2  0x085b4ba8u   /* u32@(X+0x14) */
#define BF_DECODER 0x4fc
#define BF_DISPMODE 0x4f0
#define BF_RVC  0x46c
#define BF_PDC  0x474
#define RADAR_MAXHIDE 600   /* v17 安全地板: 50ms/tick -> 隐藏最多 30s 就强制恢复+关检测 */

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
/* gf 句柄(文件级) —— die() / push_layer() / 隐藏 detach 都要用 */
static gf_dev_t     g_dev=0;
static gf_display_t g_disp=0;
static gf_layer_t   g_layer=0;
static gf_surface_t g_surf=0;
/* ★★★★★ v28 根因修复 —— 层序才是真凶。
 *
 * 2026-08-05 台架决定性实验(同一探针、同一块层、同一份内容, **只变层序**):
 *     恒等序 {0,1,..,7}        -> **不显示**
 *     自己排末尾 {其它.., 自己} -> **显示**
 * 所以 gf_display_set_layer_order 传的是 **z 序, 数组最后一个在最前面**,
 * 而它是**显示级**设置 —— 一调就重排【所有】层。
 *
 * 这一口气解释了之前所有解释不了的现象:
 *   · gftest 当初能显示、后来不能 —— 当初层序是引擎设的, 后来被它自己设成恒等序;
 *   · enable / alpha / "释放" 三轮修复全部无效 —— 它们都不是机制,
 *     层根本没被"挡住", 是**被排到别人后面去了**;
 *   · **真车大雷达探测区变黑而车模正常** —— 我们把自己顶到最前面, 原本在最前面
 *     画探测区的那层被挤到后面被盖住; 车模在另一层, 相对位置没受影响所以完好。
 *   · 为什么在车上是持久的 —— 我们不只显示时设, go_idle/go_dark 里也设,
 *     等于引擎只要活着就一直把层序按住。
 *
 * 修法: **只在真正显示弹窗时把自己排到最前面, 收起/让出立刻还原成恒等序。**
 * 还原成恒等序是安全的 —— 台架在恒等序下跑了很久, 原厂界面一直正常(实测, 非假设)。 */
static unsigned     g_order_front[8]={0,1,2,3,4,5,6,7};  /* 显示时: 自己排末尾 */
static unsigned     g_order_base [8]={0,1,2,3,4,5,6,7};  /* 空闲时: 还原恒等序 */
static int          g_detached=0;   /* 1=雷达期间已 detach 隐藏 */


/* 致命错误出口。原来这里是 hang()(无限 usleep) —— 那是错的:
 *   · 进程还活着 -> run.sh 的 kill -0 成功 -> 把失败报成 OVERLAY_RUNNING;
 *   · 而且会【一直占着 HW 层5 和 surface 不放】, 只能整机断电才能收回。
 * 现在改成: 记因 -> 反序释放 -> exit(1)。这样失败是可见的, 资源也还给系统。 */
static void go_dark(void);   /* 前向声明: die() 要先关层 */
static void go_idle(int claim);  /* v26: 空闲态(1px + 保持 enable); claim=1 才主张记录 */
static u8  *g_amap;          /* alpha 平面(定义在 blit 段, 这里前置声明给 go_idle 用) */
/* ★ v29: 初值必须是 1。旧版靠开机那次 go_idle(1) 把它置 1(:492), 取消开机认领后
 * 就没人置了 —— 第一次 push 会跳过 set_surfaces, 那块层还指着**别人的** surface,
 * 我们却去设视口/enable = 把原厂内容裁进我们的弹窗矩形, 比现在更糟。 */
static int  g_rebind = 1;    /* 同上: 定义在 push_layer 段 */
static void die(const char *why){
    L("ABORT: "); L(why); L("\n");
    /* ⚠ gf_layer_detach 是空操作(不关硬件层)。必须先 go_dark 真关掉, 否则退出后
     * 屏上会永久留一块冻结的弹窗(台架实证)。 */
    if(g_layer && g_surf) go_idle(0);
    if(g_surf)  gf_surface_free(g_surf);
    if(g_layer) gf_layer_detach(g_layer);
    if(g_disp)  gf_display_detach(g_disp);
    if(g_dev)   gf_dev_detach(g_dev);
    L("ABORT: 已释放 gf 资源并退出\n");
    exit(1);
}

/* ---------------- 小工具 ---------------- */
static int slurp(const char *path, char *buf, int cap){
    int fd=open(path,O_RDONLY,0); if(fd<0) return -1;
    int n=read(fd,buf,cap-1); close(fd);
    if(n<0) n=0; buf[n]=0; return n;
}

/* ============ /proc/<pid>/as 只读(不碰任何 IPC/IOC 通道 —— 那条挂死过车和台架) ============ */
static int g_as=-1; static u32 g_V=0, g_P=0, g_P2=0, g_bf=0;



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

/* ★★★ v29b 单实例锁 —— 2026-08-06 真车实测:同时跑起了【两个】引擎。
 *
 * 现场证据(diag/05_pop_log.txt): 两份启动日志逐行交织, 各自 purge/pick/建 surface;
 * 然后后起的那个标定失败 ——
 *   [watch] d0 L6 与本层 surface 对不上(记录 addr=0x025ddec0 / 期望 0x025a6e80)
 *           -> 探测器关闭, 退化为纯哑火保护
 * ⇒ **其中一个引擎全程没有让出保护**, 而且两个同时往同一块共享层写。
 *
 * 成因是竞态: debugTools.sh 有个守护循环(引擎一死就重启, 除非 /tmp/pop.stop 在);
 * 安装器的流程是"建 pop.stop 让旧引擎退出 -> 删 pop.stop -> 自己拉起新引擎"。
 * 守护每 5 秒查一次, 若正好在"删掉 pop.stop 之后"醒来, 它看到引擎已死且没有停止开关,
 * 就也拉起一个。
 *
 * 只改安装器的时序治标不治本(守护循环、手动启动、看门狗重启都可能撞上),
 * 所以把闸门放在引擎自己这儿: 谁都可以来启动, 但**第二个必须自己退出**。
 * 判据不是"锁文件存在"(崩溃会留下死锁文件), 而是**文件里那个 pid 现在还活着吗** ——
 * 用已有的 open_as() 试开 /proc/<pid>/as, 开得起来才算活。 */
static int single_instance_guard(void){
    char b[16]; int n, other=0, i=0, self=getpid();
    n = slurp("/tmp/pop.pid", b, sizeof(b)-1);
    if(n > 0){
        while(i<n && b[i]>='0' && b[i]<='9'){ other = other*10 + (b[i]-'0'); i++; }
        if(other > 0 && other != self){
            int fd = open_as(other);
            if(fd >= 0){                      /* 对方还活着 -> 我们是多余的那个 */
                close(fd);
                /* ★ 绝不能用 L() —— 它本进程第一次写时是 O_TRUNC, 会把**存活引擎**
                 * 已经写好的 [purge]/[pick]/zero-touch/开机快照全部冲掉。
                 * 2026-08-06 真车实测踩过: 装完 CLEAN_SLATE/ZERO_TOUCH 变 UNKNOWN,
                 * 而且丢掉了那条最想要的真车开机快照。多余实例只写自己的小文件。 */
                { int g = open("/tmp/pop.guard", O_WRONLY|O_CREAT|O_TRUNC, 0666);
                  if(g >= 0){ char m[64]; const char *s="second instance: live engine pid=";
                              int k=0,z=0,q=other; char t[12];
                              while(s[z]) m[k++]=s[z++];
                              if(!q) m[k++]='0'; else { int n2=0; while(q){ t[n2++]=(char)('0'+q%10); q/=10; }
                                                        while(n2) m[k++]=t[--n2]; }
                              m[k++]='\n'; write(g,m,(unsigned)k); close(g); } }
                return 0;
            }
            /* 开不起来 = 那个 pid 已经没了, 是崩溃/被杀留下的死锁文件, 继续 */
            L("[guard] /tmp/pop.pid 里的 pid="); Ld(other); L(" 已不存在, 视为死锁文件\n");
        }
    }
    { int fd = open("/tmp/pop.pid", O_WRONLY|O_CREAT|O_TRUNC, 0666);
      if(fd >= 0){ char o[12]; int k=0,m=0,q=self; char t[12];
                   if(!q) o[m++]='0'; else { while(q){ t[k++]=(char)('0'+q%10); q/=10; } while(k) o[m++]=t[--k]; }
                   o[m++]='\n'; write(fd,o,(unsigned)m); close(fd); } }
    return 1;
}
static int as_rd(u32 va, void *buf, int n){
    if(g_as<0) return -1;
    if(lseek(g_as,(long)va,0)!=(long)va) return -1;
    return read(g_as,buf,(unsigned)n);
}
static int rd_u32(u32 va,u32 *o){ u32 v=0; if(as_rd(va,&v,4)!=4) return -1; *o=v; return 0; }
static int rd_u8 (u32 va,u8  *o){ u8  v=0; if(as_rd(va,&v,1)!=1) return -1; *o=v; return 0; }

/* 读 BF 对象判断雷达/倒车是否激活。扫不到对象(g_bf=0, 车build不匹配)-> 恒 0(不隐藏)。*/
static int g_radar_off=0;   /* v17 安全地板: 检测到字段疑粘滞就永久关掉雷达检测 */
static int radar_active(void){
    if(g_radar_off) return 0;
    /* 台架测试钩子: 无真雷达时用 /tmp/pop.radar 强制触发, 验 detach/attach 机制。
     * 生产环境该文件不存在, 零副作用。 */
    { int tf=open("/tmp/pop.radar",O_RDONLY,0); if(tf>=0){ close(tf); return 1; } }
    if(!g_bf) return 0;
    u32 dec=0,rvc=0,pdc=0;
    rd_u32(g_bf+BF_DECODER,&dec);
    rd_u32(g_bf+BF_RVC,&rvc);
    rd_u32(g_bf+BF_PDC,&pdc);
    /* v17: 去掉粘滞的 dispmode(0x4f0=Lastmode 常停在 12 永不清 -> 会永久隐藏,
     * =用户"开一段路后完全不出"的另一诱因)。只用"正在激活"的三个 flag。*/
    return (dec!=0) || (rvc!=0) || (pdc!=0);
}

/* ============ v18 位移探测: 只读映射 gdcServerCarmine 的 /gdc_shm_inform ============
 *
 * 台架实证(2026-08-03): 服务端用 shm_open(...,O_RDWR|O_CREAT, 0777) 建 6816B 共享内存,
 * 任何进程可只读映射。记录 = base + 0xe28 + disp*0x5a0 + 硬件层号*120。
 * 字段实测: +0 状态 +4 **字节/像素**(2=我们RGBA5551, 4=32bpp 位移者) +8 忙
 *          +12 pitch(像素) +16 height +20/+24 物理地址(低28位; 真值 0xd0000000|该值)
 * 自标定实证: 引擎 paddr=0xd2683bc0 w=520 h=220 pitch=544 ⇔ 记录 d0L2
 *          `... 00000002 ... 00000220(544) 000000dc(220) 02683bc0` 逐字段吻合。
 *
 * ⚠ 只读、只 PROT_READ。服务端是 O_RDWR 映射的, 我们若误写会污染所有层(含原厂层)。
 * ⚠ 这是【加速器不是保证】: +4 是字节/像素不是颜色码, 16bpp↔16bpp 的换码看不见。
 *   永不出垃圾的保证由结构性哑火(go_dark)承担。
 * ⚠ 自标定必须唯一命中; 0 条或 ≥2 条 -> 探测器永久关闭(绝不 fail-open 去信一个错记录)。 */
extern unsigned alarm(unsigned sec);
typedef void (*sighandler_t)(int);
extern sighandler_t signal(int sig, sighandler_t h);
#define SIGALRM 14
extern int   shm_open(const char *n, int oflag, int mode);
extern void *mmap(void *a, unsigned len, int prot, int flags, int fd, long off);
#define PROT_READ    0x100
#define PROT_NOCACHE 0x800
#define MAP_SHARED   1
#define SHM_LEN      0x1aa0
#define SHM_RECBASE  0xe28
#define SHM_DSTRIDE  0x5a0
#define SHM_LSTRIDE  120
#define OUR_BPP      2          /* RGBA5551 = 2 字节/像素 */

/* shm 基址缓存 —— 选层/清残留(启动早期)和自标定(首次 push 后)都要用, 只映射一次。 */
static unsigned char *g_shm = 0;
static int g_shm_tried = 0;
static unsigned char *shm_map(void){
    if(g_shm_tried) return g_shm;
    g_shm_tried = 1;
    int fd = shm_open("/gdc_shm_inform", O_RDONLY, 0);
    if(fd < 0) return 0;
    unsigned char *B = (unsigned char*)mmap(0, SHM_LEN, PROT_READ|PROT_NOCACHE, MAP_SHARED, fd, 0);
    close(fd);
    if((long)B == -1 || B == 0) return 0;
    g_shm = B; return B;
}
/* disp0 第 hw 条记录; hw = 7 - gf层号 */
static volatile u32 *rec_of_hw(int hw){
    unsigned char *B = shm_map();
    if(!B || hw < 0 || hw > 11) return 0;
    return (volatile u32*)(B + SHM_RECBASE + hw*SHM_LSTRIDE);
}

/* ★ v29h 日志落盘 —— /tmp 是内存盘, **熄火即丢**。
 * 用户要开一段时间才复现, 中途必然熄火; 等他把 U 盘带回来时, 安装器抢救到的
 * 只是本次上电后刚 O_TRUNC 出来的新文件 —— 真正的现场早没了(今天已经栽过一次)。
 * 整份覆盖写(O_TRUNC), 所以文件始终是完整的一份, 不会越滚越大。
 * 往已挂载文件系统写普通文件无砖险(危险的是 flashit 写裸分区)。 */
static void persist_log(void){
    static char pbuf[4096];
    int fi, fo, n, tot=0;
    fi = open(LOG_PATH, O_RDONLY, 0); if(fi < 0) return;
    fo = open("/HBpersistence/overlay/pop_live.txt", O_WRONLY|O_CREAT|O_TRUNC, 0644);
    if(fo < 0){ close(fi); return; }
    while((n = read(fi, pbuf, sizeof(pbuf))) > 0 && tot < 512*1024){
        write(fo, pbuf, (unsigned)n); tot += n;
    }
    close(fi); close(fo);
}

/* ★ v29g: 把 8 块硬件层的记录全量打一遍。纯读, 零副作用。
 * 目的(2026-08-07): 我们手上只有"开机之后"的单层快照, 从没见过
 * **解锁待机、屏幕还黑着**那段时间各层长什么样。要判断"图形到底出来没有",
 * 就得先知道那两种状态在记录里的差别。启动时打一次、第一次绘制时再打一次,
 * 一次上车就能把这份对照带回来。 */
static void dump_layers(const char *tag){
    int hw;
    /* ★ v29j: 扫到 12 条, 不是 8。
     * 反编译 gdcServerCarmine 确认每个 display 有 **12** 条记录(步长 disp*0x5a0 = 12*120):
     *   idx 0..7  = 硬件层
     *   idx 8..11 = **LA0..LA3, 四块 alpha 混合平面**
     * 后四条才是本案的关键仪表: 服务端的归还分支永远执行不到(0x080502ee 的 cmp/hi #3
     * 与分配值域 8..11 不相交)⇒ **一块平面发出去就到断电都收不回**。
     * 判据: 我们全程不得让 idx 8..11 里任何一条的 +0(enable)从 0 变 1。
     * 字段名按库自带 printf 更正: +0 enable / +4 color / +8 **bank**(不是 busy) /
     *   +12 width / +16 height / +20,+24 addr0,addr1 / +28..+34 window position。 */
    if(!shm_map()){ L("[layers] "); L(tag); L(": shm 不可用\n"); return; }
    for(hw=0; hw<12; hw++){
        volatile u32 *r = rec_of_hw(hw);
        if(!r) continue;
        L("[layers] "); L(tag); L(hw<8 ? " hw L" : " ALPHA LA"); Ld(hw<8 ? hw : hw-8);
        L(" st=");    Lh(r[0]); L(" bpp="); Ld((int)r[1]);
        L(" busy=");  Ld((int)r[2]);
        L(" pitch="); Ld((int)r[3]); L(" h=");   Ld((int)r[4]);
        L(" a0=");    Lh(r[5]);     L(" a1=");   Lh(r[6]); L("\n");
    }
}

static volatile u32 *g_rec = 0;      /* 我们那条记录; 0 = 探测器关闭 */
static u32 g_ref_pitch=0, g_ref_h=0, g_ref_addr=0;   /* 自标定时的基线, 用来比对是否被改 */
static int g_watch_bad = 0;          /* 连续不符计数 */

/* ★★ v21 让出/恢复协议(优先级模型) ——
 *
 * 2026-08-04 真车实证: 我们占了 gf1(硬件L6), 而 **911 上那正是原厂画 PDC 雷达区和车模的层**
 * (台架是 Panamera 没有 ParkAssist, 所以 221 秒普查里它全程空闲 —— 台架证不了这件事)。
 * 症状: 雷达区全黑、弹窗一弹车模也没了。
 *
 * 真正的故障机制不是"我们盖住了它", 而是 **我们每次收起弹窗都 gf_layer_disable 把那块
 * 硬件层关掉** —— 原厂点亮一次, 被我们关一次, 于是它永远亮不起来。
 *
 * 正解不是继续猜哪块层空闲(车型不同分布就不同, 永远在赌), 而是照搬原厂自己的优先级语义:
 *   **高优先级功能一激活, 低优先级自动挂起。**
 *   检测到原厂开始用这块层 -> 干净哑火一次 -> 进入【让出态】: 之后【一根手指都不碰】
 *   (不 enable / 不 disable / 不 update / 不 set_surfaces), 让原厂完全接管;
 *   等它把层还回空闲 -> 自动恢复。
 * 这样我们不需要知道任何车型的层分布, 也不需要监控一堆状态。 */
static int g_yield = 0;              /* 1 = 已让出, 不再碰这块层 */
static int g_idle_ticks = 0;         /* 让出后连续观察到"空闲"的拍数 */
#define YIELD_RESUME_TICKS 40        /* 50ms/拍 -> 连续空闲 2 秒才恢复(防抖) */
#define REC_PLACEHOLDER 0x00100000u  /* 记录里的"未使用"占位物理地址 */

/* ★★★ v29 三态: 未认领 / 已认领 / 已让出 ——————————————————————————————
 *
 * 由来(2026-08-06, 反汇编真车那份二进制得出, 不是推断):
 * 车上装的是 **v22**(firmware-cache/usb-builds/overlay_install/coexist_pop 与
 * coexist_pop_v22.stripped 逐字节相同, cksum 404920996)。v22 的 main 在进主循环前
 * 无条件调 go_dark(), 而 go_dark 体内是:
 *     set_surfaces(我们的 surface) -> 视口塌 1×1 -> 写恒等层序 -> **disable** -> update
 * ⇒ **开机第一秒就把共享硬件层 L6 的 enable 位关成 0, 而且一直关着。**
 * 原厂 PDC 往这层写内容也点不亮 -> 大雷达全黑, 且与"弹窗有没有弹"无关。
 * 第二条同样致命: v22 的位移探测器只在**第一次 [draw] 之后**才武装 ->
 * 开机后没拧过音量的话探测器全程是瞎的 -> **让出协议在真车上根本没上过场**
 * (这就是"时好时坏"的成因)。
 *
 * 根本教训: 我们**读不回**原厂的 surface/视口/层序/enable 原值, 所以任何写入都是
 * 不可还原的破坏。唯一安全的姿势 = **在真要画之前, 一根手指都不碰。**
 *
 * ⚠️ 未证实(别当结论): "911 上 hw L6 就是画大雷达的那层"是从症状反推的, 没实测过。
 * 但本改动不依赖它 —— "不碰不属于自己的层"在任何层分布下都是对的。 */
static int g_claimed = 0;            /* 1 = 记录当前是我们的(push 过且没被抢/没释放) */
static u32 g_boot_rec[7];            /* 开机快照: 该层记录的原值 */
static int g_boot_ok = 0;

/* 该层是否回到空闲: 要么是占位地址(没人用), 要么还是我们自己的 surface。 */
static int rec_is_idle(void){
    u32 a;
    if(!g_rec) return 0;
    a = g_rec[5] & 0x0fffffffu;
    if(a == REC_PLACEHOLDER) return 1;
    if(g_ref_addr && g_rec[5] == g_ref_addr && g_rec[3] == g_ref_pitch) return 1;
    return 0;
}

/* 自标定: 用 surface 的物理地址低 28 位 + 高度找唯一记录。必须在首次成功 push 之后调。 */
static void watch_calibrate(u32 paddr, u32 height){
    /* ★ v20 改为【直接定位】而不是全表搜索。
     * 我们自己选的层是已知的 -> 硬件层 = 7 − GF_LAYER, 直接取那一条再核对即可。
     * 为什么必须改: 2026-08-04 台架实测, purge 只清了前任层的 enable 位, **记录里的旧值
     * (pitch/height/物理地址)还留着**; 而新 surface 又复用了同一块物理内存 ->
     * 全表搜索命中 2 条(旧 L2 + 新 L6)-> 判 "需恰好1" -> **探测器被永久关闭**。
     * 直接定位对陈旧重复项免疫。 */
    if(!shm_map()){ L("[watch] shm 映射失败 -> 探测器关闭(退化为纯哑火保护)\n"); return; }
    int hw = 7 - GF_LAYER;
    volatile u32 *r = rec_of_hw(hw);
    if(!r){ L("[watch] 取不到记录 -> 探测器关闭\n"); return; }
    u32 want = paddr & 0x0fffffffu;      /* 记录里不含 0xd0000000 的 Carmine PCI 基址 */
    if((r[5] & 0x0fffffffu) == want && r[4] == height){
        g_rec = r;
        g_ref_pitch = r[3]; g_ref_h = r[4]; g_ref_addr = r[5];
        L("[watch] 自标定命中 d0 L"); Ld(hw);
        L(" bpp="); Ld((int)r[1]); L(" pitch="); Ld((int)r[3]); L(" h="); Ld((int)r[4]); L("\n");
    } else {
        L("[watch] d0 L"); Ld(hw); L(" 与本层 surface 对不上(记录 h="); Ld((int)r[4]);
        L(" addr="); Lh(r[5]); L(" / 期望 h="); Ld((int)height); L(" addr="); Lh(want);
        L(") -> 探测器关闭, 退化为纯哑火保护\n");
    }
}

/* ★ v29 开机快照 —— 取代"先认领再标定"。
 * 我们自己选了哪个 gf 层是已知的, 记录地址 = rec_of_hw(7-GF_LAYER), **不需要先写一次**。
 * 把开机那一刻的整条记录存下来, 之后"记录是否一字未变"就是"这层有没有人动过"的判据。 */
static void watch_snapshot(void){
    int hw = 7 - GF_LAYER, k;
    volatile u32 *r;
    if(!shm_map()){ L("[watch] shm 映射失败 -> 认领门永久关闭(绝不 fail-open)\n"); return; }
    r = rec_of_hw(hw);
    if(!r){ L("[watch] 取不到记录 -> 认领门永久关闭\n"); return; }
    for(k=0;k<7;k++) g_boot_rec[k] = r[k];
    g_rec = r; g_boot_ok = 1;
    L("[watch] 开机快照 d0 L"); Ld(hw);
    L(" st=");    Lh(r[0]); L(" bpp="); Ld((int)r[1]);
    L(" pitch="); Ld((int)r[3]); L(" h="); Ld((int)r[4]);
    L(" addr=");  Lh(r[5]); L("\n");
}

/* ★ 认领门: 这块层此刻**在扫谁的内容**?
 *
 * ⚠️ 2026-08-06 台架复现踩到的坑: 最初写成"记录 7 个字全等于开机快照才放行"。
 * 结果拿 probe 在【另一块层】上做一次 gf_display_set_layer_order(显示级调用),
 * 我们这块层的影子记录也被一起改了(KB 早有实证), 门就此永久关死 ——
 * 日志 `[hold] 该层已非开机原样 -> 不认领, 不显示`, **弹窗再也不出来**。
 * 而蓝牙播放页恰恰是显示级调用最频繁的地方 ⇒ 那个判据是颗定时炸弹。
 *
 * 正确的问题不是"记录变没变", 而是"这层在扫谁的内存":
 *   物理地址 == 占位值(0x00100000) -> 没人在用, 可以认领
 *   物理地址 == 我们自己的 surface  -> 本来就是我们的
 *   其它                           -> 有人正在用它, 一根手指都不碰
 * 这正是 rec_is_idle() 的语义。失效方向仍然是【该认领没认领】= 弹窗不出, 绝不伤原厂。 */
static int may_claim(void){
    if(g_claimed) return 1;          /* 已经是我们的了 */
    if(!g_boot_ok) return 0;         /* 没有记录 -> 宁可不显示 */
    if(rec_is_idle()) return 1;      /* 占位地址 or 我们自己的 */
    /* 开机快照本身就是空闲的话, 地址没变也算空闲(覆盖 g_ref_addr 还没建立的开机段) */
    if(g_rec[5] == g_boot_rec[5]) return 1;
    return 0;
}

/* 返回 1 = 我们的层被别人改了(格式不再是我们的)。探测器关闭时恒返 0。 */
/* ★ v19: 不只看字节/像素 —— 真车实测踩了盲区。
 * 真车现象: 拧旋钮时弹窗画出来是【又细又长的噪点带】, 不是规整矩形
 * ⇒ 典型的 pitch/几何被改(内容按错误行宽扫描, 斜着糊成一条), 而
 *   字节/像素仍是 2 ⇒ 只查 bpp 的旧版【完全看不见】。
 * shm 记录里本来就有 pitch(+12) / height(+16) / 物理地址(+20), 一并比对。 */
static int watch_displaced(void){
    /* ★ v29: 没认领过就谈不上"被位移" —— 此时记录本来就是别人的,
     * 下面第一条 bpp 判据是**无条件**比常量 2, 不加这道门会在开机第一拍就误判成让出。 */
    if(!g_rec || !g_claimed) return 0;
    if(g_rec[1] != OUR_BPP)      return 1;   /* 字节/像素被改 */
    if(g_ref_pitch && g_rec[3] != g_ref_pitch) return 2;   /* 行宽被改 -> 会糊成噪点带 */
    if(g_ref_h     && g_rec[4] != g_ref_h)     return 3;   /* 高度被改 */
    if(g_ref_addr  && g_rec[5] != g_ref_addr)  return 4;   /* 扫的不是我们的内存了 */
    return 0;
}

#include "flightrec.h"

/* 唯一的上屏出口。**每次 update 之前必须走完整重申序列** ——
 * 实测只调 gf_layer_update 会永久 REPLY-block 在 gdcServerCarmine(pid 4104):
 * 没有 pending 变更时它在等一个永远不来的 vsync。
 * fr_push_enter() 必须在【调 gf 之前】落盘: 卡住时日志最后一行就是 'P' 且没有配对的 'p',
 * "卡在哪" 直接读出来, 不用猜。 */
/* ★ v18 Ⅲ 挂死转重启 —— 看门狗。
 * gf 序列理论上不死锁(disable/enable 必置脏位 -> 服务端必回复), 但"理论"不够:
 * 单线程 gdc 若因任何原因卡住, 我们会永久 REPLY-block, 屏上留一块 -> 正是要杜绝的形态。
 * 做法: 每条 gf 序列前 alarm(3), 完成后 alarm(0)。真卡住 -> SIGALRM -> exit(3)。
 * 进程死亡触发 gdc 的客户端清理(唯一实测能干净还层的路径, 见 die()), 由启动钩子
 * debugTools.sh 拉起。⇒ 死锁最坏 = 弹窗消失几秒后自动恢复, 绝不变成屏上持久黑。 */
static void wd_fire(int sig){
    (void)sig;
    L("[wd] gf 序列超时3秒 -> 判定 REPLY-block, 退出让自启拉起(层由客户端清理归还)\n");
    exit(3);
}
static void wd_arm(void){ alarm(3); }
static void wd_disarm(void){ alarm(0); }

/* ★ v20: 异常退出也要还层。
 * 台架实证(2026-08-04): 进程被 kill -9 后硬件 enable 位仍为 1, 屏上留一块。
 * kill -9 / 断电 拦不住, 但 SIGTERM(正常停)和 SIGSEGV/SIGBUS(自己崩)都能拦,
 * 这是"绝不在屏上留垃圾"的最后一道网。
 * 先 alarm(3): 万一在 gf 调用里崩、go_dark 又卡住, 3 秒后 SIGALRM 强制退出。*/
#define SIGHUP 1
#define SIGINT 2
#define SIGQUIT 3
#define SIGBUS 10
#define SIGSEGV 11
#define SIGTERM 15
static void bail_fire(int sig){
    L("[bail] 收到信号 "); Ld(sig); L(" -> 关层再退出\n");
    alarm(3);
    if(g_layer) go_idle(0);
    exit(4);
}

/* ★ v20: alpha 平面绑定必须【每次 push 都重申】。
 * 台架实证(2026-08-04): 只在 ui.def 重载时调一次 set_blending, 屏上是横向条纹 + 错位 ——
 * 层记录本身是对的(pitch/h/地址都是我们的), 说明层没被抢, 是 alpha map 绑定失效了。
 * 根因: push_layer 每次都调 gf_layer_set_surfaces, 它会把 blending/alpha-map 的绑定冲掉,
 * 硬件于是拿着失效的 alpha 指针取样 -> 条纹。
 * (gfalpha2 探针之所以完美, 是因为它 set_surfaces 只调一次, set_blending 在其之后。)
 * 所以顺序固定为: set_surfaces -> set_blending -> viewports -> order -> enable -> update。 */
static u8  g_alphablk[64];      /* 预备好的 gf_alpha_t */
static int g_alpha_on = 0;      /* 1 = 走 M1_MAP alpha 平面 */

/* ★ v24: surface/blending 只在【需要时】重绑, 不再每帧都调。
 *
 * 真车实测: 拧旋钮时(= 每次值变都重绘 = 每次都 push)**投影区在"半透阴影"和"纯黑"之间稳定闪烁**。
 * 机制: `gf_layer_set_surfaces` 会把 blending/alpha-map 绑定冲掉, 到下一句 `set_blending`
 * 补回之间存在一个窗口; 硬件在窗口里找不到 alpha map 就按【全不透明】扫 ——
 *   · 投影区 = 黑色 + 很低 alpha  -> 变纯黑, 反差极大, 一眼可见
 *   · 面板区 = 深色 + alpha 240   -> 本来就接近不透明, 几乎看不出
 *   · 文字/进度条 = alpha 255     -> 毫无变化
 * 所以现象正是"**只有阴影区闪, 而且拧得越勤闪得越稳定**"。
 *
 * 而每帧绑的本来就是同一块 surface —— 绑一次就够。之后只推视口/enable/update,
 * 这些都置脏位, 不会触发"无脏位 -> 裸 update 死锁"那条。
 * 需要重绑的时机只有三个: 首次显示、alpha 配置变了、从让出态恢复。 */


static void push_layer(int x,int y,int w,int h){
    wd_arm();
    fr_push_enter();
    /* 两者分开处理, 因为它们的性质不同:
     *   set_surfaces —— 会冲掉 blending 绑定, 所以【只在必须时】调(制造窗口的就是它);
     *   set_blending —— 不冲掉任何东西, 幂等, 所以【每帧都调】。
     * 每帧重申 blending 的意义: 万一是**原厂**那边碰了显示状态把我们的 alpha-map 冲掉
     * (v15 时代实测过别的 gf 客户端能做到这件事), 我们下一帧就自动补回来 ——
     * 而位移探测器查的是 bpp/pitch/高度/地址, 它对"绑定丢了"这件事是瞎的。 */
    if(g_rebind){
        gf_layer_set_surfaces(g_layer,&g_surf,1);
        g_rebind = 0;
    }
    if(g_alpha_on) gf_layer_set_blending(g_layer,(gf_alpha_t*)g_alphablk);
    gf_layer_set_src_viewport(g_layer, 0, 0, w-1, h-1);
    /* ★ v20 更正: 宽和高【都是 x2-x1+1】。
     * 旧注释说"高=y2-y1(无+1)"是错的, 于是传 y2=y+h -> 实际渲染 h+1 行 ->
     * 面板下方多出一行(用户肉眼看到的"约1像素溢出")。
     * 2026-08-04 用 pcmshot 抓"显示帧-隐藏帧"差分实测: 变化区外接框
     * x 214..585(宽 372, 正好) / y 202..278(高 **77**, 多 1) —— 宽对高错, 铁证。 */
    gf_layer_set_dst_viewport(g_layer, x, y, x+w-1, y+h-1);
    gf_display_set_layer_order(g_disp, g_order_front, 0);   /* 显示期间才排到最前 */
    gf_layer_enable(g_layer);
    gf_layer_update(g_layer, 0);
    fr_push_exit();
    wd_disarm();
}

/* ★ v18 结构性哑火态 —— 空闲时【真正关掉硬件 enable 位】。
 *
 * 台架硬件实证(2026-08-03, 读 gdc 的 enable 位域 0x080dbd1c bit(16+硬件层)):
 *   · gf_layer_detach            -> bit 纹丝不动(12秒全程) = **空操作**, 层继续扫描;
 *   · gf_layer_disable 单独      -> bit 仍不动 = 只登记未提交;
 *   · gf_layer_disable + update  -> **bit 清零** = 唯一真隐藏, 且 update 正常返回【不死锁】。
 *     (旧注释"disable+update 会 REPLY-block"是从"裸 update 死锁"外推的, 已实测推翻:
 *      死锁只发生在没有脏位时——disable 自己必置脏位, 所以必被回复。)
 *
 * 为什么这是"永不出黑块"的主保证: enable 位=0 后, 该硬件层对合成器的贡献恒为零 ——
 * 不管格式影子被谁翻成 32bpp、几何被谁改、surface 指向哪里, 屏上都不可能出现我们的东西。
 * v16/v17 的真车黑块正是因为 detach 后 enable 仍=1, 全零 surface 被按 32bpp 扫成不透明黑。
 *
 * 塌几何到 1×1 是纵深防御: 万一 disable 在某条路径上没提交, 误扫面积上限 1 像素。 */
/* ★★ v26 空闲态 = go_idle() —— 缩成 1 像素, 但【永不关灯】。
 *
 * 2026-08-05 台架决定性实验(contend 探针, 刻意不调 enable, 忠实模拟原厂):
 *   引擎持有 gf1 且处于 go_dark(disable) 态时, 竞争者持续写记录, 屏上**什么都不出现**,
 *   使能位一直是 0 —— 完整复现了真车"大雷达探测区全黑而车模正常"。
 *
 * 结论: 真正的病根不是"让出得不够彻底", 而是**我们一开始就不该关掉这块层**。
 * 用户的原则说得更准: "如果系统在用这个图层, 我们就直接释放, 优先系统使用。"
 * 最彻底的实现其实比"检测到再让出"更简单 —— **永远不关灯**:
 *   · 系统随时能用它, 灯一直亮着, 它写完记录内容立刻就出来;
 *   · 我们根本没有"需要让出"的时刻, 也就不依赖探测器是否武装;
 *   · 最坏情况的垃圾从"整个面板黑块"降到"角落 1 个像素"(而且那个像素写成全透明)。
 *
 * 保留 go_dark()(真 disable)只给一个用途: 清理【前任实例】留下的层(purge),
 * 那时记录带着我们自己的签名, 确定不是系统在用。 */
/* claim 语义(这个区分很要紧):
 *   claim=1 —— 启动时用。此刻没人在用这块层, 我们主张它(set_surfaces), 记录才会指向
 *              我们的 surface, 位移探测器也才有得标定。
 *   claim=0 —— 收起/退出时用。**绝不 set_surfaces** —— 万一系统已经接管了记录,
 *              再 set_surfaces 就是把它抢回来, 正好违背"优先系统使用"。 */
/* ★★★ v27 补上最后一块: **空闲时必须解绑 alpha 混合**。
 *
 * 2026-08-05 台架实证(v26 仍然复现失败): 记录已经是竞争者的(引擎读到它的 pitch/h)、
 * 使能位也是 1(灯亮着), 竞争者画的东西**依然完全不显示**。
 * 唯一还能让像素消失的就是 alpha —— 我们给这层绑了 M1_MAP, alpha 平面指向**我们自己的**
 * 那块内存, 而空闲时它被我们清成全 0。**绑定不会随我们停手而消失**,
 * 于是别人画什么都被乘以 alpha=0 -> 全透明 -> 屏上什么都没有。
 *
 * 这正好解释真车那张照片: **PDC 探测区整片不见, 而车模完好**(车模在别的层上)。
 *
 * ⇒ "释放"必须包括**释放混合配置**, 不只是 surface 和 enable 位。
 * mode=0 也在驱动的 9 值白名单内, 就是"不混合"。重新显示时 push_layer 每帧都会重绑, 不受影响。 */
static void clear_blending(void){
    static u8 zb[64]; int q;
    /* ★★★ v29k: 从没绑过 alpha 就【一次都不要调】。
     *
     * 本函数的用途是"解绑我们自己的 M1_MAP"。panel_alpha=255 时 g_alpha_on 恒 0,
     * 我们从来没建过平面、没绑过混合 —— 这里没有任何东西需要解。
     * 而它偏偏是 255 配置下**唯一残留的 gf_layer_set_blending 调用点**
     * (收起 / 让出 / 退出三条路各一处), 每次弹窗收起都会打一发。
     *
     * gdcServerCarmine 的 set_gf_layer_blend_mode(0x080500d8) 会向 LA0..LA3 申请一块
     * 混合平面, 而释放分支(0x080502ee)的守卫是 `cmp/hi #3`, 与分配值域 8..11 不相交
     * ⇒ 发出去就到断电为止收不回。"全零结构体一定不会走到分配"是**推断, 没实测**
     * (2026-08-11 拿 /gdc_shm_inform idx 8..11 当仪表, 故意用 240 证伪, 仪表毫无反应
     *  ⇒ 那条观测路径作废, 分配与否目前**测不出来**)。
     *
     * 测不出来的时候, 正确做法是**不去赌**: 直接不调。
     * 代价 = 0(没绑过的东西不需要解绑), 收益 = 255 配置下对服务端的混合调用降到零。 */
    if(!g_alpha_on) return;
    for(q=0;q<(int)sizeof(zb);q++) zb[q]=0;      /* mode=0 = 不混合 */
    gf_layer_set_blending(g_layer,(gf_alpha_t*)zb);
}

static void go_idle(int claim){
    /* ★★ v29: 从未认领过 -> 一根手指都不碰。
     * 这一道门同时管住四个调用点: die() / bail_fire() / pop.stop 退出 / 保持到期收起。
     * 旧版这四处无条件执行, 意味着"开机后没拧过音量就退出/被杀"也会往共享层写一遍。
     * claim=1 的用法在 v29 已经没有了(开机不再认领), 保留形参只为不动函数签名。 */
    if(!claim && !g_claimed) return;
    wd_arm();
    fr_push_enter();
    if(claim) gf_layer_set_surfaces(g_layer,&g_surf,1);
    clear_blending();               /* ★ 解绑我们的 alpha 平面, 别人才画得出东西 */
    g_rebind = 1;                   /* 下次显示要重绑 surface + blending */
    /* 把左上角那一个像素写成全透明, 万一混合配置被谁重置, 它也只是 1 px */
    if(g_surf){ static u8 zi[128];
        if(gf_surface_get_info(g_surf,(gf_surface_info_t*)zi)){
            gf_surface_info_t *z=(gf_surface_info_t*)zi;
            if(z->vaddr) *(u16*)(size_t)z->vaddr = 0x0000u; } }
    if(g_amap) g_amap[0] = 0;
    gf_layer_set_src_viewport(g_layer, 0, 0, 0, 0);
    gf_layer_set_dst_viewport(g_layer, 0, 0, 0, 0);   /* 1x1(宽高都是 x2-x1+1) */
    gf_display_set_layer_order(g_disp, g_order_base, 0);  /* ★ 还原层序, 别按着原厂 */
    gf_layer_enable(g_layer);       /* 保持点亮 */
    gf_layer_update (g_layer, 0);
    fr_push_exit();
    wd_disarm();
}

static void go_dark(void){
    wd_arm();
    fr_push_enter();
    gf_layer_set_surfaces(g_layer,&g_surf,1);
    gf_layer_set_src_viewport(g_layer, 0, 0, 0, 0);
    gf_layer_set_dst_viewport(g_layer, 0, 0, 0, 0);   /* 1x1 */
    gf_display_set_layer_order(g_disp, g_order_base, 0);  /* 还原层序 */
    gf_layer_disable(g_layer);      /* 置脏位 */
    gf_layer_update (g_layer, 0);   /* 提交 -> 硬件 enable 位清零 */
    fr_push_exit();
    wd_disarm();
    g_rebind = 1;                   /* 这里自己调了 set_surfaces -> 下次显示必须重绑 */
}

/* ★★ v20 残留清除 —— 用户硬要求:"v15 的问题一定要清掉"。
 *
 * v15 的隐藏是"把像素清成 A=0 靠透明混合看不见", **硬件 enable 位从来没关过**。
 * 只要有别的 gf 客户端做一次显示级调用(gf_display_set_layer_order 之类), 我们那层的
 * 格式/混合配置就被重置 -> 那块"透明"内存立刻按不透明扫出来 = **弹窗大小的纯黑矩形**。
 * 2026-08-04 台架实证: 跑 gftest 当第二个客户端, v15 那层当场变黑块; 读 0x080dbd1c
 * bit(16+硬件层) 确认 enable 位一直是 1。而且【kill 掉进程也不会归还层】。
 *
 * 所以 v20 启动时必须主动去把前任留下的层关掉。
 * 判据(只认我们自己的, 绝不误关原厂层): 该层记录的 height == UI_MAXH(220) 且
 * 字节/像素 == 2。原厂层是 480 / 1088 高, 不可能撞上。
 * 关的手段只用 disable + update(实测唯一真关; detach 是空操作)。 */
static void layer_kill(gf_layer_t lyr){   /* 形参别叫 L —— L() 是日志函数 */
    wd_arm();
    gf_layer_set_src_viewport(lyr, 0, 0, 0, 0);
    gf_layer_set_dst_viewport(lyr, 0, 0, 0, 0);
    /* 清前任残留也用恒等序 —— 绝不趁机把全局层序按成我们的 */
    gf_display_set_layer_order(g_disp, g_order_base, 0);
    gf_layer_disable(lyr);
    gf_layer_update (lyr, 0);
    wd_disarm();
}

/* 遍历候选层, 把"看起来是我们前任留下的"层真正关掉。返回清掉几条。 */
static int purge_stale_layers(gf_display_t disp){
    int i, n=0;
    for(i=0;i<GF_NCAND;i++){
        int gfl = GF_CAND[i], hw = 7 - gfl;
        volatile u32 *r = rec_of_hw(hw);
        if(!r) continue;
        if(r[4] != (u32)UI_MAXH || r[1] != OUR_BPP) continue;   /* 不是我们的形状 -> 不碰 */
        gf_layer_t lyr=0;
        if(gf_layer_attach(&lyr, disp, (unsigned)gfl, GF_LAYER_ATTACH_PASSIVE) != GF_ERR_OK) continue;
        layer_kill(lyr);
        gf_layer_detach(lyr);
        n++;
        L("[purge] 清掉前任残留: gf"); Ld(gfl); L(" (硬件L"); Ld(hw);
        L(") h="); Ld((int)r[4]); L(" pitch="); Ld((int)r[3]); L("\n");
    }
    return n;
}

/* 选层: 按候选顺序取第一个能 attach 且当前不是原厂全屏面的。 */
static int pick_layer(gf_display_t disp, gf_layer_t *out){
    int i;
    for(i=0;i<GF_NCAND;i++){
        int gfl = GF_CAND[i], hw = 7 - gfl;
        volatile u32 *r = rec_of_hw(hw);
        /* ★ v29i: 把判据的三项都打出来 —— 只看结论("选中/跳过")无法判断
         * 到底是"这层真空闲"还是"它是原厂层、只是还没拿到缓冲"。 */
        if(r){ L("[pick] gf"); Ld(gfl); L(" (硬件L"); Ld(hw); L(") pitch="); Ld((int)r[3]);
               L(" h="); Ld((int)r[4]); L(" addr="); Lh(r[5]);
               L((r[3]==800&&r[4]==480) ? " [形状=原厂800x480]" : " [形状非原厂]");
               L(((r[5]&0x0fffffffu)==0x00100000u) ? " [无缓冲]\n" : " [有缓冲]\n"); }
        if(r && r[3]==800 && r[4]==480 && (r[5]&0x0fffffffu)!=0x00100000u){
            L("[pick] gf"); Ld(gfl); L(" 当前是原厂 800x480 面, 跳过\n");
            continue;
        }
        gf_layer_t Lz=0;
        if(gf_layer_attach(&Lz, disp, (unsigned)gfl, GF_LAYER_ATTACH_PASSIVE) != GF_ERR_OK){
            L("[pick] gf"); Ld(gfl); L(" attach 失败, 试下一个\n");
            continue;
        }
        *out = Lz; GF_LAYER = gfl;
        L("[pick] 选中 gf"); Ld(gfl); L(" = 硬件 L"); Ld(hw); L("\n");
        return 1;
    }
    return 0;
}

/* ★★ v25 真释放 —— 把层【还给】系统, 而不是"拿着但不碰"。
 *
 * v21~v24 的让出是"停手": 不再 enable/disable/update/set_surfaces。听起来够了, 实际不够 ——
 * **我们是把灯关着走开的**。哑火(收起弹窗)时 go_dark 会 disable 那块硬件层;
 * 之后系统要用它, 我们检测到、停手, 但那层仍停在【我们关掉】的状态,
 * 系统把内容写进去也点不亮 -> 2026-08-05 真车现象: **大雷达探测区全黑, 而车模正常**
 * (车模在别的层上)。用户原话:"如果系统在用这个图层, 我们就直接释放, 优先系统使用。"
 *
 * 所以释放必须【包含把 enable 位还回去】。这里有一点逻辑上很干净:
 * 触发让出的判据就是"这条记录已经不是我们的了" —— 也就是说这一刻记录里已经是**系统的**
 * surface/几何, 所以 enable 点亮的是他们的画面, 不会是我们的残留。
 *
 * 只调 enable+update, **不调 set_surfaces / 不动视口** —— 那些都是共享状态, 现在归他们。 */
static void release_layer(void){
    wd_arm();
    fr_push_enter();
    gf_layer_enable(g_layer);       /* 把 enable 位还回去(记录已是系统的) */
    gf_layer_update(g_layer, 0);    /* 提交 */
    fr_push_exit();
    wd_disarm();
}

static u8 g_scan[SCAN_CHUNK];
static void locate(void){
    u32 va; g_V=g_P=g_P2=0; g_bf=0;
    for(va=HEAP_LO; va<HEAP_HI && !(g_V&&g_P2&&g_bf); va+=SCAN_CHUNK-4){
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
            } else if(w==BF_VT && !g_bf){
                if(rd_u32(X+0x14,&t1)==0 && t1==BF_VT2) g_bf=X;
            }
        }
    }
    if(g_V) rd_u32(g_V+V4_PROXY,&g_P);
    L("[V4] V="); Lh(g_V); L(" P="); Lh(g_P); L(" P2="); Lh(g_P2); L(" BF="); Lh(g_bf); L("\n");
}
static int g_why=-1;   /* 上次丢弃原因: 0=正常 1=提示音src 2=ok!=2; 只在变化时记日志 */

/* ★★ v29g: V4 proxy 数据是否有效(ok==2)。
 * 这就是"音频子系统真的起来了"的信号 —— 而让屏幕在解锁待机期黑着的,
 * 和让这里 ok!=2 的, 是同一件事(系统还没真正进运行态)。
 * 用途见主循环: 无效期间**只播种、绝不绘制**, 从根上消掉"点火自己弹一下"。 */
static int g_v4ok=0;
/* g_readok: 本次是否成功读到了 proxy 数据(不管有没有被 src 过滤)。
 * 必须把"读不到"(链断了, 该重定位)和"读到了但被过滤"(倒车提示音, 一切正常)分开 ——
 * 否则一挂倒挡, 10 秒后就会去全堆扫 3.57MB, 纯属浪费还可能卡顿。 */
static int g_readok=0;

/* 每拍复验 V 还在, 并从 V 重新取 P。
 * ⚠ 保留是因为【便宜且能挡住 V 消失】, **不是**已证实的修复:
 *   RE 实锤(2026-07-21): P=u32@(V+0x168) 由构造函数 @0x82af374 一次性写入
 *   (值来自按名字 "AudioManagementDevCtrl" 的注册表 get-or-create)。
 *   全 .text 扫描该类区域 **27 次读 +0x168, 0 次写** —— 切音源不会换 proxy。
 *   所以"倒车后拧旋钮失效"不是 proxy 变了。真因待明天飞行记录仪定案。
 *   (曾在注释里写成"真车实证", 那是把推测当事实 —— 与 0x218 那次同类错误, 已更正。)
 * 残留风险: V 是堆对象(operator new(0x960)+ctor, 非静态全局)。若被析构重建,
 *   freed 堆里仍留着旧 vtable 字, 下面的 vt!=V4_VVT 检查会**通过** -> 日志要记 V 和原值。 */
static int revalidate_V(void){
    u32 vt=0, np=0;
    if(!g_V) return 0;
    if(rd_u32(g_V,&vt)!=0 || vt!=V4_VVT) return 0;      /* V 本身没了 */
    if(rd_u32(g_V+V4_PROXY,&np)==0 && np && np!=g_P){
        L("[V4] proxy 变了 "); Lh(g_P); L(" -> "); Lh(np); L(" (多半是音源切换)\n");
        g_P = np;
    }
    return 1;
}

/* 返回 0..40, 或 -1 表示读不到 */
static int read_vol(void){
    int v4=-1,v2=-1;
    g_readok=0; g_v4ok=0;
    if(g_P){ u32 ok=0; u8 b=0;
        int okr = (rd_u32(g_P+V4_OK,&ok)==0 && ok==2);
        int bor = (rd_u8(g_P+V4_VOL,&b)==0 && b<=VOL_MAX);
        if(okr && bor){
            g_readok=1;                              /* 数据读到了, 后面即使过滤也不算故障 */
            s32 src=0;
            if(rd_u32(g_P+V4_SRC,(u32*)&src)==0 && (src==34||src==35)){
                v4=-1;                                   /* 铃声/提示音, 丢 */
                if(g_why!=1){ g_why=1; L("[V4] 丢弃: src="); Ld((int)src); L(" (提示音类音源)\n"); }
            } else { v4=(int)b; if(g_why!=0){ g_why=0; L("[V4] 恢复正常读数\n"); } }
        } else if(g_why!=2){ g_why=2;
            L("[V4] 丢弃: ok="); Ld((int)ok); L(" (!=2 表示该 proxy 数据无效)\n"); } }
    if(g_P2){ u8 b=0; if(rd_u8(g_P2+V2_VOL,&b)==0 && b<=VOL_MAX){ v2=(int)b; g_readok=1; } }
    /* ★★ v29h: 标志的含义是【本拍的值是谁给的】, 不是"proxy 有没有效"。
     * 上一版写成 okr&&bor 就置 1 —— 错。因为紧接着 src∈{34,35}(提示音)会把 v4 置 -1,
     * 于是下面 return 落到 **V2 兜底**, 而标志已经是 1 了 ⇒ 来源明明切了, 门却看不见。
     * 审查给出的致命时序: 点火那拍若正在放开机提示音, 翻转被一个 V2 读数消耗掉;
     * 提示音一结束值切回 V4, 标志不翻转 -> 照样弹 -> 认领 hw L6 -> 雷达继续黑。
     * 改成按实际返回值判定后, V4<->V2 每一次切换(含提示音进/出两个方向)都必然触发重播种。 */
    g_v4ok = (v4 >= 0);
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
/* v17: 去掉 g_noscan 永久停扫。旧逻辑一次重扫没中就永久放弃 -> 音量对象漂出窗口后
 * 弹窗永久不出、重启才恢复(=用户"开一段路后完全不出"的真凶之一)。
 * 新逻辑: 永不永久停; 连丢多次只把重扫间隔拉长省 CPU, 但一直重试。 */
static int g_fail=0, g_miss=0;
static int g_reloc_period=200;     /* 动态重扫间隔(tick); 连丢会拉长, 但不清 0 */
#define RELOC_FAIL_TICKS 200   /* 50ms/tick -> 10s */
#define RELOC_SLOW_TICKS 1200  /* 连丢 >=5 次后拉到 60s 一扫 */

/* shown=1 表示弹窗正显示着, 即用户正在拧 -> 立即跟随, 不去抖。
 * 去抖只用来把关"从隐藏状态弹出来"这一下 —— 它防的是没人碰时缓存在 19/20 之间
 * 自己抖导致弹窗乱蹦, 而不是防拧旋钮。连续拧时每拍值都在变, 去抖会一直不满足,
 * 结果就是"转的过程中完全不更新, 停手才画", 手感上就是不跟手。 */
static int vol_tick(int shown){
    if(g_V && !revalidate_V()){
        L("[V4] V 失效(对象漂移/重建?) 旧V="); Lh(g_V); L(" -> 触发重扫\n");
        g_V=0; g_P=0; g_fail=g_reloc_period; fr_mark('0');   /* V 没了 -> 下一拍就重扫 */
    }
    int raw = read_vol();
    if(raw < 0){
        if(g_readok){          /* 读到了, 只是被 src 过滤(倒车提示音等) —— 正常, 不算故障 */
            g_fail = 0;
            return -1;
        }
        if(g_fail < g_reloc_period) g_fail++;
        if(g_fail >= g_reloc_period && g_as >= 0){
            g_fail = 0;
            L("[V4] 读不到 -> 重扫堆(第"); Ld(g_miss+1); L("次, 窗口"); Lh(HEAP_LO); L("-"); Lh(HEAP_HI); L(")\n");
            fr_mark('R');
            locate(); g_cand=-1; g_stable=0;
            if(!g_P && !g_P2){
                g_miss++;
                /* v17: 永不永久停。连丢 >=5 次就把间隔拉到 60s 省 CPU, 但一直重试。*/
                if(g_miss>=5) g_reloc_period=RELOC_SLOW_TICKS;
                L("[V4] 重扫仍无命中(累计"); Ld(g_miss); L("次), 拉长间隔继续重试, 不放弃\n");
            } else {
                L("[V4] 重扫命中! V="); Lh(g_V); L(" P="); Lh(g_P); L(" P2="); Lh(g_P2); L("\n");
                g_miss=0; g_reloc_period=RELOC_FAIL_TICKS;
            }
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


/* ★★ v20 alpha 平面(真 8 位逐像素 alpha) —— 2026-08-04 台架肉眼验证通过。
 *
 * 旧结论"只有 1bit alpha, 没有软边/渐变"是【错的】。驱动的 9 值白名单里
 * 0x00080102 = M1_MAP|SRC_M1|DST_1mM1 是被接受的, 配一块 GF_FORMAT_BYTE 平面即可。
 *
 * 🚨 头号坑: **alpha 平面的宽度必须按主 surface 的【像素 pitch】建, 不是按 w!**
 *    主 surface w=500 时 stride=1024B -> pitch=512; 若按 w=500 建 alpha,
 *    astride=500 而硬件按 512 逐行取 -> 每行错开 12 像素 -> **斜条纹**(第一次就踩了)。
 *    按 pitch 建则 astride==pitch, 干净。pitch 填充区(x>=w)的 alpha 填 0。
 *
 * 开关: ui.def 的 `panel_alpha`(0..255)。==255 -> 不建平面、不开混合, 行为与 v19 完全一致
 * (逐像素二值透明, 默认模式下 A=0 本来就真透)。<255 -> 建平面 + M1_MAP。 */
static gf_surface_t g_asurf = 0;
static int  g_astride = 0;
static int  g_panel_a = 255;     /* 由 ui.def panel_alpha 覆盖 */

static void blit_layer(UIWidget *w, u16 *dst, int pitch){
    int y,x,W=w->w,H=w->h;
    for(y=0;y<H;y++){
        u16  *d  = dst + (size_t)y*(size_t)pitch;
        u16_ *pb = ui_popbuf + y*W;
        u8_  *cv = ui_cov    + y*W;
        if(g_amap){
            /* alpha 平面模式: 颜色照写, 透明度写进平面 —— 软圆角/半透面板都成立 */
            u8 *ad = g_amap + (size_t)y*(size_t)g_astride;
            u8_ *al = ui_al + y*W;
            for(x=0;x<W;x++){
                d[x] = (u16)(pb[x]|1u);
                /* ★ v20: 目标不透明度由渲染器逐像素给出(ui_al), 再乘覆盖度得到软边。
                 * 不能再用"颜色==panel 就算面板底"去反推 —— 面板一加竖向渐变,
                 * 大部分像素颜色就不等于 panel 了, 会被误判成内容而全不透明。 */
                ad[x] = (u8)(((int)al[x] * (int)cv[x]) / 255);
            }
            for(x=W; x<g_astride; x++) ad[x]=0;     /* pitch 填充区全透 */
        } else {
            for(x=0;x<W;x++)
                d[x] = (cv[x]>=128) ? (u16)(pb[x]|1u) : (u16)0x0000u;  /* A=0 = 真透明 */
        }
        d[W] = 0x0000u;                          /* 右邻一列: 防硬件多扫出未写内存 */
    }
    { u16 *d = dst + (size_t)H*(size_t)pitch;    /* 下邻一行: 同上 */
      for(x=0;x<=W;x++) d[x] = 0x0000u; }
    if(g_amap){                                   /* 下邻一行 alpha 也清 */
        u8 *ad = g_amap + (size_t)H*(size_t)g_astride;
        for(x=0;x<g_astride;x++) ad[x]=0;
    }
}

int main(void){
    /* ★ 横幅必须带**唯一的 ASCII 版本串** —— 安装器 run.sh 只能是纯 ASCII(生成器按 ascii 写盘),
     * 没法 grep 中文日志行。装车时"新版真进去了 / 老版真没了"这个保证就靠这一行的 v29/v2x 判据。
     * 以前一直没改横幅(v22~v28 全打印 v20), 结果脚本层面根本区分不了版本。 */
    /* ★★ 单实例锁必须是 main 的【第一句】—— 要在任何 L() 之前。
     * L() 本进程首次写用 O_TRUNC, 多余实例只要打一行横幅就会把存活引擎的日志冲掉
     * (2026-08-06 真车实测: 因此丢了开机快照, CLEAN_SLATE/ZERO_TOUCH 变 UNKNOWN)。 */
    if(!single_instance_guard()) return 0;

    L("COEXIST_POP v29k — 开机零接触+认领门+让出不写层序+alpha混合调用归零\n");

    /* 0. layermanager 握手(跑通的代码都是这个顺序; 少了它 gf_dev_attach 会阻塞) */
    int lmfd=open(LM_DEV, LM_FLAGS, 0);
    L("lmfd="); Ld(lmfd); L("\n");
    if(lmfd>=0){ int cv[3]={0,0,0}; devctl(lmfd,(int)LM_CHECKVER,cv,12,0); }

    /* 1. 设备 + 显示 */
    static u8 devinfo[512]; gf_dev_t dev=0;
    int _dr=gf_dev_attach(&dev, GF_DEVICE_INDEX(0), (gf_dev_info_t*)devinfo); g_dev=dev;
    if(_dr!=GF_ERR_OK){ die("dev_attach"); }
    static u8 dispinfo[512]; gf_display_t disp=0;
    int _sr2=gf_display_attach(&disp, dev, 0, (gf_display_info_t*)dispinfo); g_disp=disp;
    if(_sr2!=GF_ERR_OK){ die("display_attach"); }
    gf_display_info_t *di=(gf_display_info_t*)dispinfo;
    L("display "); Ld((int)di->xres); L("x"); Ld((int)di->yres); L(" nlayers="); Ld((int)di->nlayers); L("\n");

    /* ★★★ v29i: 在【碰任何层之前】先把 8 层记录全打一遍。
     *
     * 2026-08-07 用户确认的现象: 解锁后 PCM 已完整启动(点火时不再显示盾徽), 此时触发雷达
     * 探测区是黑的; 解锁后马上点火则一切正常。而真车开机快照是
     *     hw L6  pitch=800  h=480  addr=0x00100000(占位)
     * —— pitch/h **是原厂的形状**(我们是 512×220), 只是还没分配缓冲。
     * 而 pick_layer 的跳过判据要求 pitch/h/地址**三条同时**成立, 地址是占位就不跳过
     * ⇒ **我们把一块"原厂的、只是还没拿到缓冲"的层拿走了**。
     * 这可能才是主因, 而"弹窗自己闪一下"只是"引擎起得早"的伴随现象。
     *
     * 之前的 dump("boot") 打在 attach + surface_create **之后**, 拍到的已经被我们动过。
     * 这一份是真正的动手前状态 —— 长间隔/短间隔各跑一次, 一对照就能定案。 */
    dump_layers("prepick");

    /* ★ v20 2a. 先清前任残留(v15/v18/v19 崩溃或被 kill 后留在屏上的层), 再选层。
     * 顺序不能反: 万一前任占的正好是我们要选的那块, 得先关掉。 */
    { int killed = purge_stale_layers(disp);
      if(killed) { L("[purge] 共清掉 "); Ld(killed); L(" 条残留层\n"); }
      else        L("[purge] 无残留\n"); }

    /* 2b. 运行时选层(首选 gf1=硬件L6, 退路 gf5=硬件L2) */
    gf_layer_t layer=0;
    if(!pick_layer(disp,&layer)) die("所有候选层都抢不到");
    g_layer=layer;

    /* 3. 一次建足够大的 surface(UI_MAXW x UI_MAXH), 之后靠 src/dst viewport 裁剪定位
     *    -> ui.def 改几何时不需要重建 surface。 */
    gf_surface_t surf=0;
    { int r=gf_surface_create_layer(&surf,&layer,1,0, UI_MAXW, UI_MAXH, (gf_format_t)0x1710, (void*)0, 0);
      g_surf=surf;
      L("surface_create_layer("); Ld(UI_MAXW); L("x"); Ld(UI_MAXH); L(") r="); Ld(r); L("\n");
      if(r!=GF_ERR_OK){ die("建 surface 失败"); } }
    static u8 sinfo[128];
    if(!gf_surface_get_info(surf,(gf_surface_info_t*)sinfo)){ die("get_info NULL"); }
    gf_surface_info_t *si=(gf_surface_info_t*)sinfo;
    if(!si->vaddr){ die("vaddr=0"); }
    int pitch=(int)si->stride/2;
    /* 整块 surface 先清零: 我们只用左上角一小块, 但 dst viewport 的高度算法不对称
     * (RE: 高=y2-y1 无+1), 硬件有可能多扫一行/一列。没清过的地方是未初始化内存,
     * 扫出来就是屏上一条杂色残留线(实测: 弹窗消失后下方留一条白线)。 */
    { u16 *z=(u16*)(size_t)si->vaddr; int n;
      for(n=0; n<pitch*UI_MAXH; n++) z[n]=0x0000u; }
    L("surf vaddr="); Lh((u32)si->vaddr); L(" stride="); Ld((int)si->stride); L(" pitch="); Ld(pitch); L("\n");
    /* T0-2 自标定: 打印 paddr/w/h/sid, 用来和 /gdc_shm_inform 记录逐字段对上 */
    L("surf paddr="); Lh((u32)si->paddr); L(" sid="); Lh((u32)si->sid);
    L(" w="); Ld((int)si->w); L(" h="); Ld((int)si->h); L(" fmt="); Lh((u32)si->format); L("\n");

    /* 3b. 接活体音量: /proc/<PCM3Root pid>/as 只读 */
    { char pb[16]; int n=slurp(PID_FILE,pb,sizeof(pb)); int pid=-1;
      if(n>0){ int v=0,i=0,any=0; while(pb[i]>='0'&&pb[i]<='9'){ v=v*10+(pb[i]-'0'); i++; any=1; }
               if(any) pid=v; }
      L("[V4] pid="); Ld(pid); L("\n");
      if(pid>0){ g_as=open_as(pid); L("[V4] /proc/as fd="); Ld(g_as); L("\n");
                 if(g_as>=0) locate(); }
      if(!g_P && !g_P2){
          /* v17: 不再永久停。运行时 vol_tick 会按慢速间隔一直重试(连丢 5 次后 60s 一扫),
           * 万一是启动时序问题(对象还没建好)也能自愈。 */
          L("[V4] 首次定位无命中 -> 稍后按慢速间隔重试(不永久放弃); 暂只接受 /tmp/uival 手动值\n"); } }

    signal(SIGALRM, wd_fire);   /* v18 Ⅲ: 看门狗 */
    /* v20: 异常退出兜底(kill -9 拦不住, 其余都拦) */
    signal(SIGTERM, bail_fire); signal(SIGINT,  bail_fire);
    signal(SIGHUP,  bail_fire); signal(SIGQUIT, bail_fire);
    signal(SIGSEGV, bail_fire); signal(SIGBUS,  bail_fire);
    /* ★★★ v29: 开机【绝不认领】。
     *
     * 旧版在这里调 go_idle(1)/go_dark() —— set_surfaces + 塌 1×1 + 写层序 + enable/disable。
     * 那是**不可还原的共享状态写入**: 我们读不回原厂的 surface/视口/层序/enable 原值,
     * 一旦写下去就再也还不回去。真车实证的后果见 :288 那段注释。
     *
     * 前任残留怎么办? 已经由 purge_stale_layers() 处理了(它在选层之前跑, 判据是
     * "记录高度==220 且 bpp==2" = 只认我们自己的形状, 绝不误碰原厂层)。这里不需要再关一次。
     *
     * 探测器怎么武装? 见下面的 watch_snapshot() —— 直接按 7−GF_LAYER 定位记录并存快照,
     * 根本不需要"先写一次让记录变成我们的"。v26 那条"必须在这里武装"的要求依然满足,
     * 而且比旧法更早、更干净。 */
    /* ASCII 标签 zero-touch 是给安装器 grep 的(run.sh 只能纯 ASCII) */
    L("[init] v29k zero-touch noblend: 开机未认领任何层\n");
    watch_snapshot();
    /* ★ v29g: 启动时(=解锁待机、屏幕还黑着)把 8 层记录全打一遍。
     * 这是我们第一次能拿到"黑屏待机态"的层分布 —— 和第一次绘制时那份对照,
     * 就能知道"图形出来没有"在记录里长什么样, 将来要不要加第二道门有据可依。 */
    dump_layers("boot");
    fr_open();
    fr_mark('B');

    /* 句柄在各自获取处即刻登记, 见上 */

    /* 4. 主循环: 热加载 ui.def + 读值 + 渲染 + 刷层 */
    static char defbuf[4096];
    static UIWidget W;
    unsigned last_def_hash=0; int last_val=-99999; int have=0;
    { int k,w2=0,no=(int)di->nlayers; if(no<1||no>8) no=8;
      for(k=0;k<no;k++) if(k!=GF_LAYER) g_order_front[w2++]=(unsigned)k;
      if(w2<8) g_order_front[w2++]=(unsigned)GF_LAYER;
      for(;w2<8;w2++) g_order_front[w2]=(unsigned)w2; }

    int t=0, shown=0, hold_left=0, last_push=-99, cooldown=0;
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
                        /* ★ v20: panel_alpha 变了就切换硬件混合。<255 = 建 alpha 平面走 M1_MAP;
                         * ==255 = 复位成不混合(逐像素二值透明, 同 v19)。 */
                        if(W.panel_alpha != g_panel_a){
                            g_panel_a = W.panel_alpha;
                            L("[alpha] panel_alpha="); Ld(g_panel_a); L("\n");
                            if(g_panel_a < 255){
                                if(!g_asurf){
                                    /* 🚨 宽度按【像素 pitch】建, 不是按 UI_MAXW —— 否则斜条纹 */
                                    int ar=gf_surface_create(&g_asurf,g_dev,pitch,UI_MAXH+1,
                                                             (gf_format_t)GF_FORMAT_BYTE,(void*)0,0);
                                    L("[alpha] 建平面 w="); Ld(pitch); L(" r="); Ld(ar); L("\n");
                                    if(ar==GF_ERR_OK){
                                        static u8 ai2[128];
                                        if(gf_surface_get_info(g_asurf,(gf_surface_info_t*)ai2)){
                                            gf_surface_info_t *aip=(gf_surface_info_t*)ai2;
                                            g_astride=(int)aip->stride; g_amap=(u8*)(size_t)aip->vaddr;
                                            { int q; for(q=0;q<g_astride*(UI_MAXH+1);q++) g_amap[q]=0; }
                                            L("[alpha] stride="); Ld(g_astride);
                                            L(g_astride==pitch?" (==pitch 对齐)\n":" (!=pitch 会条纹!)\n");
                                        }
                                    } else { g_asurf=0; L("[alpha] 建平面失败 -> 退回二值透明\n"); }
                                }
                                if(g_amap){
                                    int q; gf_alpha_t *alp=(gf_alpha_t*)g_alphablk;
                                    for(q=0;q<(int)sizeof(g_alphablk);q++) g_alphablk[q]=0;
                                    alp->mode=0x00080102u;      /* M1_MAP|SRC_M1|DST_1mM1, 在驱动白名单内 */
                                    alp->map=g_asurf; alp->m1=255; alp->m2=255;
                                    g_alpha_on=1; g_rebind=1;   /* 配置变了 -> 下次 push 重绑 */
                                    /* ★★★ v29e: 这里【绝对不能】调 gf_layer_set_blending。
                                     *
                                     * 2026-08-06 真车实证(用户全程没碰音量旋钮, 雷达照样黑):
                                     * 这一句在【第一次加载 ui.def】时执行 = 主循环第 0 拍 = **开机**,
                                     * 与有没有绘制无关。它把我们那块【上一行刚 memset 成全 0】的
                                     * alpha 平面绑成了这块**共享硬件层**的混合源。
                                     * 原厂照常在这层画 PDC 探测区(所以几何完全正确), 但硬件合成走
                                     * 我们的 alpha 映射 -> 颜色被乘没 -> **填成黑色**。
                                     * 用户照片: 两块肾形探测区形状完好、纯黑填充 —— 逐条吻合。
                                     *
                                     * 它也是 v29"开机零接触"唯一的漏洞: 认领/视口/enable/层序都管住了,
                                     * 只剩这一个 gf 写调用不需要拧旋钮就会跑。
                                     * 冗余性: push_layer(:525) 每帧都会 `if(g_alpha_on) set_blending`,
                                     * 所以真正要显示时绑定一定建得起来, 这里一句纯属多余。 */
                                }
                            } else if(g_amap){
                                int q; gf_alpha_t *alp=(gf_alpha_t*)g_alphablk;
                                for(q=0;q<(int)sizeof(g_alphablk);q++) g_alphablk[q]=0;  /* mode=0 也在白名单 */
                                g_alpha_on=0; g_rebind=1;
                                /* ★ v29e 同理: 只有【我们确实认领着这层】时才有资格去解绑。
                                 * 没认领就调 = 往原厂正在用的层上写混合配置(和上面那处一个性质)。 */
                                if(g_claimed){ wd_arm(); gf_layer_set_blending(g_layer,alp); wd_disarm(); }
                                g_amap=0;
                                L("[alpha] 已复位为不混合\n");
                            }
                        }
                    } else L("[def] 解析失败\n");
                }
            }
        }

        /* --- 停止开关: 建 /tmp/pop.stop 就干净退出 ---
         * 车上除了断电本来没有别的办法停掉它(拔U盘不行, 它跑在 /tmp)。
         * 退出前先把层刷成全透明, 免得屏上留一块。 */
        { int sf=open("/tmp/pop.stop",O_RDONLY,0);
          if(sf>=0){ close(sf);
            /* v22: 同"收起"路径 —— 必须【先关层再清缓冲】, 否则退出瞬间闪一个黑框
             * (清 RGB 时层还开着, 而 alpha 平面还是面板形状 -> 扫出黑色面板框)。*/
            go_idle(0);                     /* v26: 缩 1px 但不关灯, 也不抢记录 */
            { int yy,xx, ch=W.h+2, cw=W.w+2;
              if(ch>UI_MAXH) ch=UI_MAXH;
              if(cw>pitch)   cw=pitch;
              { u16 *d0=(u16*)(size_t)si->vaddr;
                for(yy=0;yy<ch;yy++){ u16 *d=d0+(size_t)yy*(size_t)pitch;
                    for(xx=0;xx<cw;xx++) d[xx]=0x0000u; } }
              if(g_amap){
                  for(yy=0;yy<ch;yy++){ u8 *a=g_amap+(size_t)yy*(size_t)g_astride;
                      for(xx=0;xx<g_astride;xx++) a[xx]=0; } } }
            fr_mark('X');
            die("收到 /tmp/pop.stop, 主动退出"); } }

        /* --- 取值: 手动覆盖优先, 否则活体去抖值 --- */
        int val = read_override();
        int from_override = (val >= 0);          /* /tmp/uival = 人为显式指定, 不受下面的抑制规则约束 */
        if(val < 0) val = vol_tick(shown);

        /* ★★★ v29f: 只放行【像人拧的】变化 —— 系统自发的跳变一律只重播种, 不弹窗。
         *
         * 2026-08-07 用户实测的复现条件(这条是他发现的, 我没想到):
         *   钥匙解锁 -> PCM 上电开始跑(屏还黑着) -> 隔几分钟才点火
         *   -> **没碰旋钮, 点火瞬间弹窗自己闪一下** -> 之后开车雷达就黑
         *   解锁后马上点火则完全正常。
         * 机制: 音量是我们每 50ms 去 PCM3Root 内存里【轮询】出来的, 不是事件。
         *   待机期先播种(如 20), 点火时音频子系统初始化把音量恢复成实际值 -> 值变了
         *   -> 主循环那句 `val != last_val` 不管是谁改的, 当场 push_layer -> **认领 hw L6**。
         *   间隔短则引擎还没读到第一个值(开机脚本要等图形栈+8s), 播种播的就是最终值 -> 不弹。
         * 后果: 那一次认领把我们的 surface/alpha映射/1×1视口留在 L6 上,
         *   之后原厂画 PDC 探测区 -> 几何是它的、合成走我们的 -> **填成纯黑**(用户照片)。
         * ⇒ **不是"进程在跑"会坏, 是"画过一次"就坏。** 这也反过来证实了
         *   "911 上 hw L6 与 PDC 显示绑在一起"这条此前一直没验证的前提。
         *
         * ---- 修法 (v29g 待机门) ----
         *
         * 判据不看数值, 看**读数来自哪个来源** —— 所以不存在"两边数值刚好接近就漏判"。
         * read_vol() 有两个来源: V4(主, 要 ok==2)和 V2(兜底, 无有效性判据)。
         * 解锁待机、屏幕还黑着那段时间 V4 无效, 我们吃的是 V2;
         * 点火后 V4 变有效, 读数**从 V2 切到 V4** —— 两个不同对象里的两个数字,
         * 没有理由相等 ⇒ 主循环那句 `val != last_val` 当场判成"用户拧了旋钮"并弹窗。
         * 那一弹就认领了 hw L6, 之后原厂的 PDC 探测区就黑了。
         *
         * 规则:
         *   · V4 未就绪期间 —— 持续跟随读数, 但**一次都不画**(黑屏时画了也没人看见, 纯有害)
         *   · 就绪/失效**切换的那一刻** —— 重新播种(可能要等几拍才拿到有效值, 用 need_reseed 兜住)
         *   · 之后 V4 一直有效、值再变, 那才是真的有人拧旋钮 -> 正常弹
         * /tmp/uival 手动通道不受此门约束(人为显式指定)。 */
        { static int prev_ok = -1; static int need_reseed = 0;
          if(!from_override){
              if(g_v4ok != prev_ok){
                  L(g_v4ok ? "[src] 取值来源=V4 -> 重新播种, 之后正常响应旋钮\n"
                           : "[src] 取值来源=V2兜底 -> 只播种, 不弹窗\n");
                  dump_layers("flip");   /* 来源翻转那一拍的全层记录 —— 门生效时 firstdraw 永远不会出现 */
                  prev_ok = g_v4ok; need_reseed = 1;
              }
              if(!g_v4ok && val >= 0) last_val = val;          /* 未就绪: 跟随但永不弹 */
              if(need_reseed && val >= 0){ last_val = val; need_reseed = 0; fr_mark('s'); }
          } }

        /* ★★ v29h 心跳(10s 一条) —— 这道门押在"待机期 V4 不出值"这个**从未观测到**的前提上。
         * 两种坏结局在现象上无法区分, 必须靠日志分开:
         *   · 前提不成立(待机期 V4 就出值)-> 门形同虚设, 照样弹, 雷达照黑
         *   · V4 在 9x1 上根本不出值      -> g_v4ok 恒 0 -> 一次都不画 = **弹窗永久失效**
         * 把判定所需的全部状态摊开, 回来一眼能定案。 */
        if((t % 200) == 0){
            u32 ok=0, sr=0, ra=0; u8 b4=0, b2=0;
            if(g_P){ rd_u32(g_P+V4_OK,&ok); rd_u32(g_P+V4_SRC,(u32*)&sr); rd_u8(g_P+V4_VOL,&b4); }
            if(g_P2){ rd_u8(g_P2+V2_VOL,&b2); }
            if(g_rec) ra = g_rec[5];
            L("[hb] t=");     Ld(t);
            L(" P=");         Lh(g_P);       L(" ok=");    Ld((int)ok);   L(" src=");  Ld((int)sr);
            L(" v4=");        Ld((int)b4);   L(" v2=");    Ld((int)b2);   L(" srcv4="); Ld(g_v4ok);
            L(" val=");       Ld(val);       L(" last=");  Ld(last_val);
            L(" shown=");     Ld(shown);     L(" claim="); Ld(g_claimed); L(" yield="); Ld(g_yield);
            L(" L6a=");       Lh(ra);        L("\n");
            persist_log();                  /* 顺带落盘, 10s 一次 */
        }

        fr_sample(shown);      /* 无条件采样: 读数冻结时也要留痕, 否则完全静默拍不到 */

        /* --- v18: 雷达检测【降级为遥测】, 不再当控制输入 ---
         * 理由: (a) 旧的 detach 隐藏经台架实证是空操作, 从没生效过;
         *      (b) 即便换成真隐藏, 检测雷达也是打地鼠 —— 格式影子无归属门后写者赢,
         *          摄像头/来电/任何 gf 客户端都能复现同样的抢夺。
         * 真正的保证是结构性哑火(空闲关 enable 位) + 位移探测。这里只记日志, 供分析。 */
        { static int rad_prev=-1;
          int ra = radar_active();
          if(ra != rad_prev){
              rad_prev = ra;
              u32 dec=0,rvc=0,pdc=0;
              if(g_bf){ rd_u32(g_bf+BF_DECODER,&dec); rd_u32(g_bf+BF_RVC,&rvc); rd_u32(g_bf+BF_PDC,&pdc); }
              L("[radar-telemetry] active="); Ld(ra);
              L(" dec="); Lh(dec); L(" rvc="); Lh(rvc); L(" pdc="); Lh(pdc); L("\n");
              fr_mark(ra?'r':'R');
          }
        }

        /* --- 值变了: 画 + 显示 + 重置保持计时 --- */
        if(!g_yield && have && val>=0 && last_val==-99999){
            last_val = val;               /* 首个有效值只播种, 开机/热重载不弹窗 */
            L("[seed] val="); Ld(val); L("\n"); fr_mark('S');
        } else if(!g_yield && have && val>=0 && val!=last_val && (t-last_push)>=2 && cooldown<=0
                  && !may_claim()){
            /* ★ v29 认领门: 这块层自开机以来被人动过 -> 原厂正在用它 -> 本次不画。
             * 宁可不显示弹窗, 也绝不往别人的层上写。 */
            last_val = val;                              /* 吃掉这个值, 不弹 */
            { static int nag=0; if(!nag){ nag=1;
                L("[hold] 该层已非开机原样(原厂在用) -> 不认领, 不显示\n"); fr_mark('H'); } }
        } else if(!g_yield && have && val>=0 && val!=last_val && (t-last_push)>=2 && cooldown<=0){
            last_val=val; last_push=t;
            ui_render(&W, val);
            blit_layer(&W,(u16*)(size_t)si->vaddr,pitch);
            /* ★ v29g: 在【认领之前】打一次全层记录 —— 与 boot 那份对照, 就能看出
             * "屏幕已经在显示东西"时各层什么样。必须在 push_layer 之前, 否则拍到的是我们自己。 */
            if(!g_claimed) dump_layers("firstdraw");
            push_layer(W.x,W.y,W.w,W.h);
            /* ★ v29: 到这一刻记录才真正变成我们的 —— 标定基线在此建立(不是开机时) */
            if(!g_claimed){ g_claimed=1; watch_calibrate((u32)si->paddr,(u32)si->h); }
            shown=1; hold_left=hold_ticks;
            L("[draw] val="); Ld(val); L("\n"); fr_mark('D');
            /* ★ v19 画后立验: 真车是【画出来那一下就是噪点】, 等下一拍已经来不及。
             * push 刚把我们的格式/几何写进去, 立刻回读记录核对; 不符 = 有人在跟我们抢,
             * 当场关层(宁可不显示, 也不显示垃圾)。 */
            { int w = (!g_yield) ? watch_displaced() : 0;
              if(w){
                  L("[watch] 画后立验失败 code="); Ld(w);
                  L(" bpp="); Ld((int)g_rec[1]); L(" pitch="); Ld((int)g_rec[3]);
                  L(" h="); Ld((int)g_rec[4]); L(" (基线 pitch="); Ld((int)g_ref_pitch);
                  L(" h="); Ld((int)g_ref_h); L(") -> 当场哑火\n");
                  fr_mark('W');
                  shown=0; hold_left=0;
                  g_yield=1; g_idle_ticks=0; g_watch_bad=0;
                  wd_arm();
                  clear_blending();
                  wd_disarm();                 /* v29: 不再写层序(见下方让出分支同款注释) */
                  g_rebind = 1;
                  release_layer();
                  g_claimed = 0;               /* ★ v29 */
                  L("[yield] 原厂要用这块层 -> 立刻停手, 之后不再触碰(等它空闲再恢复)\n");
              } }
            /* v26: 自标定已提前到启动处(见 [init] 附近的长注释) —— 这里不再重复。
             * 万一启动时没标定成功(shm 打不开等), 补一次机会, 但只补一次。 */
            { static int calib=0;
              if(!calib && !g_rec){ calib=1; watch_calibrate((u32)si->paddr, (u32)si->h); } }
        }

        /* ★ v20 像素导出钩子 —— 免去"让用户拍照"这一步。
         * 建 /tmp/pop.grab 即导出当前层内容, 然后自动删触发文件(一次性):
         *   /tmp/pop.rgb : w*h 个 u16(RGBA5551), 逐行紧凑(不含 pitch 填充)
         *   /tmp/pop.a   : w*h 个 u8 (alpha 平面; 未开混合时全 255)
         *   /tmp/pop.geo : 文本 "w h pitch panel_alpha"
         * 拉回 Mac 用 dev/render_pop_grab.py 合成 PNG -> 布局/内边距/投影/配色/透明度
         * 全部可以离线核对。⚠ 硬件扫描阶段的问题(条纹之类)导不出来, 那只能靠拍照。 */
        { int gf2=open("/tmp/pop.grab",O_RDONLY,0);
          if(gf2>=0){ close(gf2); unlink("/tmp/pop.grab");
            int gw=W.w, gh=W.h, yy;
            int fd1=open("/tmp/pop.rgb",O_WRONLY|O_CREAT|O_TRUNC,0644);
            if(fd1>=0){ u16 *src=(u16*)(size_t)si->vaddr;
                for(yy=0;yy<gh;yy++) write(fd1, src+(size_t)yy*(size_t)pitch, (unsigned)(gw*2));
                close(fd1); }
            int fd2=open("/tmp/pop.a",O_WRONLY|O_CREAT|O_TRUNC,0644);
            if(fd2>=0){
                if(g_amap){ for(yy=0;yy<gh;yy++) write(fd2, g_amap+(size_t)yy*(size_t)g_astride, (unsigned)gw); }
                else { static u8 ones[UI_MAXW]; int q; for(q=0;q<gw;q++) ones[q]=255;
                       for(yy=0;yy<gh;yy++) write(fd2, ones, (unsigned)gw); }
                close(fd2); }
            int fd3=open("/tmp/pop.geo",O_WRONLY|O_CREAT|O_TRUNC,0644);
            if(fd3>=0){ char gb[64]; int n=0;
                { int v[4]; int k; v[0]=gw; v[1]=gh; v[2]=pitch; v[3]=g_panel_a;
                  for(k=0;k<4;k++){ int x2=v[k], d2=100000, st=0;
                    if(!x2){ gb[n++]='0'; }
                    else { while(d2){ int dg=(x2/d2)%10; if(dg||st){ gb[n++]=(char)('0'+dg); st=1; } d2/=10; } }
                    gb[n++]=(k==3)?'\n':' '; } }
                write(fd3,gb,(unsigned)n); close(fd3); }
            L("[grab] 已导出 "); Ld(gw); L("x"); Ld(gh); L(" 到 /tmp/pop.rgb + pop.a\n");
          } }

        /* --- v18 位移探测: 显示期每拍校验, 连续2拍不符 -> 认输哑火 + 10s 冷却 ---
         * 认输而不是搏斗: v15 的 250ms 重申在真车打不赢持续写者。而"雷达期间没有音量弹窗"
         * = 原厂行为 = 正确。垃圾可见上限 = 2 拍 ≈ 100ms, 且只一次不闪。 */
        /* ★ v21: 【始终】监视, 不再只在显示期看。
         * 真车教训: 我们哑火着也照样在"占用"这块层(go_dark 会 disable 硬件层),
         * 原厂的 PDC 因此永远点不亮。所以哑火期同样要能察觉"原厂想用它"并让出。 */
        if(g_rec){
            if(!g_yield){
                if(watch_displaced()){
                    if(++g_watch_bad >= 2){
                        L("[yield] 原厂开始用这块层 code="); Ld(watch_displaced());
                        L(" bpp="); Ld((int)g_rec[1]); L(" pitch="); Ld((int)g_rec[3]);
                        L(" h="); Ld((int)g_rec[4]); L(" -> 让出\n");
                        fr_mark('W');
                        /* 🚨 这里【绝对不能】go_dark —— go_dark 就是 disable。
                         * 若原厂是"功能启动时点亮一次"的模式, 而我们的让出恰好发生在
                         * 它点亮之后, 这最后一次 disable 就把它关死了, 之后我们再也不碰,
                         * 它就永远黑着 —— 这正是 2026-08-04 真车 PDC 全黑的形态。
                         * 检测到位移时记录已经是原厂的, 屏上显示的本来就是他们的内容;
                         * 我们只要【立刻停手】, 就绝不可能是它变黑的原因。 */
                        shown=0; hold_left=0; g_watch_bad=0;
                        g_yield=1; g_idle_ticks=0;
                        /* ★ 释放 = 解绑混合 + 交还 enable。若是在"显示中"被抢的,
                         * 此刻 alpha 平面还绑着, 不解绑就会把对方画的一切乘成全透明。
                         * ★ v29: 层序不再还原 —— 写恒等层序本身就是一次共享显示状态覆盖,
                         *   而记录此刻已经是原厂的了, 我们没有任何理由再动全局 z 序。 */
                        wd_arm();
                        clear_blending();
                        wd_disarm();
                        g_rebind = 1;
                        release_layer();
                        g_claimed = 0;          /* ★ v29: 已交还, 之后一根手指都不碰 */
                    }
                } else g_watch_bad=0;
            } else {
                /* 让出态: 只看不碰。连续 2 秒观察到空闲才恢复(防抖, 免得跟原厂来回抢) */
                if(rec_is_idle()){
                    if(++g_idle_ticks >= YIELD_RESUME_TICKS){
                        g_yield=0; g_idle_ticks=0; last_val=-99999; cooldown=0;
                        g_rebind=1;              /* 原厂用过这块层 -> 我们的绑定肯定没了 */
                        /* ★ v29: 认领门比的是"开机快照", 而原厂用过之后记录已经不是开机那份了。
                         * 既然这一刻判定为空闲(没人用), 就把它当作新的基线 —— 否则
                         * may_claim() 永远为假, 让出一次之后就再也不显示了。 */
                        { int k; for(k=0;k<7;k++) g_boot_rec[k]=g_rec[k]; g_boot_ok=1; }
                        L("[yield] 这块层已空闲 -> 刷新基线, 恢复接管\n");
                        fr_mark('R');
                    }
                } else g_idle_ticks=0;
            }
        }
        if(cooldown>0) cooldown--;

        /* --- 保持到期 -> 收起 --- */
        if(shown && !g_yield){
            /* ★★ v29c: 保持期【每拍重申混合配置】。
             *
             * 2026-08-06 真车现象: 弹窗的**阴影区变成纯黑框在闪**, 而且**只在蓝牙播放页**。
             * 机制: 阴影的 RGB 本来就接近黑, 全靠 alpha 平面(M1_MAP)才显示成半透;
             * 一旦绑定被冲掉, 硬件就把它按**不透明**扫出来 = 一个黑框。
             * 为什么偏偏蓝牙页: 那是动态页(进度条/封面在刷), 原厂频繁做显示级调用,
             * 而"别的 gf 客户端做一次显示级调用就会重置我们这层的格式/混合配置"是已实证的。
             * 静态页原厂不刷, 所以看不见。
             *
             * 旧代码只在 push_layer 里重申(=只有值变化那一拍), 保持期整段裸奔。
             * set_blending 按本文件 :517 的实证是**幂等且不冲掉任何东西**, 每拍调是安全的;
             * 必须跟一次 update 才会落到硬件(:439 实证: 只登记不提交是不生效的)。
             * 只在【我们正显示、且没让出】时做 —— 不显示时一根手指都不碰(v29 的底线)。 */
            /* ★ v29j 删除: 原本这里每拍重申一次 set_blending(v29c 为治阴影黑框加的)。
             * 两个理由都没了:
             *   ① 阴影已经去掉, 那个故障不存在了;
             *   ② 反编译坐实 set_blending 会向 gdcServerCarmine 申请一块 **alpha 混合平面**,
             *      而服务端的归还分支(0x080502ee, cmp/hi #3)与分配值域(8..11)不相交 ->
             *      **发出去就永远收不回**。每拍申请一次是往枪口上撞。 */
            if(hold_left>0) hold_left--;
            else {
                /* ★ v22 顺序修正 —— 真车实测"弹窗消失瞬间闪一个黑框"就是这里。
                 * 旧顺序是【先清像素、再 go_dark】。但清像素那一刻**层还开着、硬件还在扫**,
                 * 而且我们只清 RGB、**没清 alpha 平面** -> alpha 仍是面板那圈不透明值,
                 * 硬件就按"面板形状 + 全黑颜色"扫了出来 = 黑色面板框闪一帧, 然后才关掉。
                 * (这段清像素是 1bit alpha 时代的遗留 —— 那时"隐藏"就是把像素变透明;
                 *  现在隐藏靠 disable, 它不但多余, 还正好制造了这一闪。)
                 * 正确顺序: **先关层, 再清缓冲** —— 纵深防御保留, 闪烁消失。 */
                go_idle(0);
                shown=0; L("[idle]\n"); fr_mark('h');
                { int yy,xx, ch=W.h+2, cw=W.w+2;
                  if(ch>UI_MAXH) ch=UI_MAXH;
                  if(cw>pitch)   cw=pitch;
                  { u16 *d0=(u16*)(size_t)si->vaddr;
                    for(yy=0;yy<ch;yy++){ u16 *d=d0+(size_t)yy*(size_t)pitch;
                        for(xx=0;xx<cw;xx++) d[xx]=0x0000u; } }
                  /* alpha 平面同样要清: 否则下次万一有一帧抢跑, 又是同样的黑框 */
                  if(g_amap){
                      for(yy=0;yy<ch;yy++){ u8 *a=g_amap+(size_t)yy*(size_t)g_astride;
                          for(xx=0;xx<g_astride;xx++) a[xx]=0; } } }
            }
        }

        /* --- 显示期间低频重申层序, 防被 layermanager 挤掉 --- */
        t++;
        /* 显示期间周期重申 —— 频率提到 250ms(每 5 拍)。
         * 根因确诊(2026-07-21 台架 gfcontend 复现): 另一个 gf 客户端对 hwL2 调
         * set_surfaces 会抢改我们层的格式影子寄存器 LnEC(无客户端归属门, 后写者赢),
         * 我们同一块 RGBA5551 内存被按错误格式扫成花屏/黑, 且几何精确对上。
         * 我们的 push_layer 每次都重推导 LnEC -> 一次 push 即自愈。
         * 所以对策 = 显示期间够快地重申, 把被外部翻转到自愈的窗口压到最短。
         * ⚠ v12 删掉重申是错的(当时误判 lm 拉锯): 删了变成一直花。
         * ⚠ 不能到 50ms —— 会打满单线程 gdc 服务端(REPLY 死锁)。250ms 是安全上限附近。 */
        /* ★ v23: 【周期性重申已删除】。
         *
         * 它是 v15"抢回来"时代的产物 —— 那时的策略是被别人翻了格式就每 250ms 重申一次夺回来。
         * v21 起策略换成了【检测到就让出】, 两者互斥: 位移会走让出分支, 根本轮不到重申去修。
         * 留着它唯一的实际效果是**制造闪烁**:
         *   `push_layer` 第一步 `gf_layer_set_surfaces` 会把 blending/alpha-map 绑定冲掉,
         *   随后虽然立刻 `set_blending` 补回, 但中间存在一个窗口; 硬件在这个窗口里找不到
         *   alpha map 就按【不透明】扫 —— 于是**投影区在"半透阴影"和"纯黑"之间每 250ms 闪一次**
         *   (真车实测现象)。
         * 稳态本来就不需要重申: 画一次提交一次, 之后没人动就一直对。 */
        usleep(50000);          /* 20Hz 轮询 */
    }
    return 0;
}
