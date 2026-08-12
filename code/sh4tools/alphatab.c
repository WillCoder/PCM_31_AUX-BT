/* alphatab <pid> [base_hex]  ——  读 gdcServerCarmine 自己的 alpha 平面分配账本
 *
 * 为什么要它: 2026-08-11 我拿 /gdc_shm_inform 的 idx 8..11 当"平面有没有被申请"的
 * 仪表, 故意用 panel_alpha=240 画一次去证伪 —— 四条记录纹丝不动。
 * ⇒ shm 那份是**影子/显示状态**, 不是分配账本, 那条观测路径作废。
 *
 * 真账本在服务端进程自己的 .bss(反汇编 gdcServerCarmine 得出):
 *   提交函数 0x0804d634:  this = 0x080dbd04 + disp * 0x0e24
 *                         *pend 的 bit5 置位时才调 set_XX_layer_blend_mode(this, layer)
 *   0x080500d8 里:        alphaidx = *(u32*)(this + 56*k + 88)
 *   分配器 0x0804f8ce:    for(k=8;k<=11;k++) if (*(u32*)(this+36 + 56*k + 52) > 11) return k;
 *                         (this+36+56k+52 == this+56k+88, 同一个数组)
 *
 * 语义: 表项 k
 *   k = 0..7   硬件层 -> 它用的 alpha 平面编号(12 = 没有)
 *   k = 8..11  alpha 平面 LA0..LA3 -> 绑给了哪个层(**> 11 = 空闲**)
 *
 * 判据: 我们弹一次窗前后各跑一遍。若某个 k∈[8,11] 从 >11 变成小值 = 我们申请走了一块;
 *       弹窗收起后它**不会**变回去(释放分支 0x080502ee 的守卫是 cmp/hi #3, 与分配值域
 *       8..11 不相交 ⇒ 永远执行不到)。
 *
 * 只读 /proc/<pid>/as, 不写一个字节。
 */
extern int open(const char*,int,...); extern int close(int);
extern int read(int,void*,unsigned); extern int write(int,const void*,unsigned);
extern long lseek(int,long,int);
#define O_RDONLY 0
#define O_RDWR   2

static int sl(const char*s){int n=0;while(s[n])n++;return n;}
static void o(const char*s){write(1,s,sl(s));}
static void ohx(unsigned v){char b[11];int i;b[0]='0';b[1]='x';
    for(i=0;i<8;i++)b[2+i]="0123456789abcdef"[(v>>((7-i)*4))&0xF];b[10]=0;write(1,b,10);}
static void odec(int v){char b[12];int n=0,i;if(v<0){o("-");v=-v;}
    if(!v)b[n++]='0'; while(v){b[n++]='0'+v%10;v/=10;}
    for(i=n;i>0;i--)write(1,&b[i-1],1);}
static unsigned us(const char*s){unsigned v=0;while(*s>='0'&&*s<='9'){v=v*10+(*s-'0');s++;}return v;}
static unsigned xs(const char*s){unsigned v=0;if(s[0]=='0'&&(s[1]=='x'||s[1]=='X'))s+=2;
    while(*s){v<<=4;if(*s>='0'&&*s<='9')v|=*s-'0';
        else if(*s>='a'&&*s<='f')v|=*s-'a'+10;
        else if(*s>='A'&&*s<='F')v|=*s-'A'+10;else break; s++;}return v;}
static void mkpath(char*d,unsigned pid){const char*a="/proc/",*b="/as";int i=0,j,n=0;
    char num[12],t[12];int k=0;while(a[i]){d[i]=a[i];i++;}
    if(pid==0)num[n++]='0';else{while(pid){t[k++]='0'+pid%10;pid/=10;}while(k)num[n++]=t[--k];}
    for(j=0;j<n;j++)d[i++]=num[j];j=0;while(b[j])d[i++]=b[j++];d[i]=0;}

/* 自检: 这张表每一项的合法取值是很窄的, 所以基址一旦错了立刻看得出来。
 *   k = 0..7  (硬件层)      -> 12 = 没绑 alpha, 或 8..11 = 用的哪块平面
 *   k = 8..11 (alpha 平面)  -> 0..7 = 绑给哪个层, 或 >11 = 空闲
 * 落在这之外的值 = 我们根本没在读这张表(基址/build 不对), 此时 FREE/TAKEN 全是噪声。
 * 宁可大声报错, 也不要安安静静给出一堆像模像样的垃圾。 */
static int implausible(int k,unsigned v){
    if(k<8)  return !(v==12u || (v>=8u && v<=11u));
    return !(v<=7u || v>11u);
}

int main(int argc,char**argv){
    char path[40]; int fd,disp,k,bad=0,seen=0; unsigned pid,base,va,val;
    if(argc<2){ o("alphatab <pid> [base_hex=0x080dbd04]\n"); return 1; }
    pid = us(argv[1]);
    base = (argc>2) ? xs(argv[2]) : 0x080dbd04u;
    mkpath(path,pid);
    fd = open(path,O_RDONLY,0);
    /* adump(dev/sh4tools) 当年是用 O_RDWR 打开 /proc/<pid>/as 的。这里先试只读
     * (我们一个字节都不写), 打不开再退回 O_RDWR —— 别为了一个打开模式白跑一趟。 */
    if(fd<0) fd = open(path,O_RDWR,0);
    if(fd<0){ o("ERR open "); o(path); o("\n"); return 2; }
    for(disp=0; disp<2; disp++){
        o("disp"); odec(disp); o(" base="); ohx(base + (unsigned)disp*0x0e24u); o("\n");
        for(k=0; k<12; k++){
            va = base + (unsigned)disp*0x0e24u + (unsigned)k*56u + 88u;
            val = 0xdeadbeefu;
            if(lseek(fd,(long)va,0)<0 || read(fd,&val,4)!=4){
                o("  k="); odec(k); o(" @"); ohx(va); o(" READFAIL\n"); continue;
            }
            o(k<8 ? "  layer" : "  LA");
            odec(k<8 ? k : k-8);
            o(" k="); odec(k); o(" @"); ohx(va); o(" val="); ohx(val);
            seen++;
            if(implausible(k,val)){ bad++; o("  IMPLAUSIBLE\n"); continue; }
            if(k>=8) o(val>11u ? "  FREE\n" : "  TAKEN\n"); else o("\n");
        }
    }
    close(fd);
    /* 这一行是给脚本 grep 的: SANITY=BAD 时上面的 FREE/TAKEN 一个都不能信。 */
    if(bad){ o("SANITY=BAD bad="); odec(bad); o(" of "); odec(seen);
             o("  (基址或 build 不对, 上面的 FREE/TAKEN 全部无效)\n"); }
    else   { o("SANITY=OK\n"); }
    o("alphatab done\n");
    return 0;
}
