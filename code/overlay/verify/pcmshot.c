/* pcmshot — PCM 3.1 真·整屏截图(合成后的画面), 独立工具版。
 *
 * 之前这套能力嵌在 gflayer.c 里(/tmp/shot 触发), 不方便随时用, 结果我一直让用户
 * 盯屏幕拍照。这里抽成独立命令。
 *
 * 原理: gf_display_snapshot(disp,0, x1,y1,x2,y2, snap) 把**显示控制器合成后**的画面
 * 抓进我们自建的 surface -> 所以原厂 UI + 我们的独立硬件层 overlay 都在里面。
 *
 * 输出 P5HEX 文本(不是二进制): 台架只有 57600 串口, 二进制要转义/易丢字节,
 * 纯 hex + 换行最傻但最稳(dev/shot2png.py 转 PNG)。
 *   首行 "P5HEX <w> <h>", 其后每行 w 个像素, 16bpp 每像素 4 位 hex / 32bpp 8 位 hex。
 *   像素格式 RGBA5551: R[15:11] G[10:6] B[5:1] A@bit0 (🚨不是 ARGB1555, 见 shot2png.py 注释)
 *
 * 用法: pcmshot <out.hex> [x y w h] [step]
 *   pcmshot /tmp/s.hex                 整屏 800x480, step=2 (400x240, 串口约 40 秒)
 *   pcmshot /tmp/s.hex 214 202 372 76  弹窗区全分辨率 (约 25 秒)
 *   pcmshot /tmp/s.hex 0 0 800 480 1   整屏全分辨率 (约 5 分钟, 慎用)
 */
typedef unsigned int   u32;
typedef unsigned short u16;
typedef unsigned char  u8;
typedef unsigned long  size_t;

#include "gf_defs.h"

extern int  open(const char *p, int f, int m);
extern int  write(int fd, const void *b, unsigned n);
extern int  close(int fd);
extern int  devctl(int fd, int cmd, void *d, unsigned n, unsigned *i);
extern void exit(int);
void _exit(int c){ exit(c); }

#define O_WRONLY 0x001
#define O_CREAT  0x100
#define O_TRUNC  0x200
#define LM_DEV      "/dev/layermanager"
#define LM_FLAGS    0x2002
#define LM_CHECKVER 0xc00c0506u
#define SCRW 800
#define SCRH 480

static void o(const char*s){int n=0;while(s[n])n++;write(1,s,n);}
static void dec(int v){char b[12];int i=11,neg=0;b[11]=0;if(v<0){neg=1;v=-v;}
    if(!v)b[--i]='0';else while(v){b[--i]=(char)('0'+v%10);v/=10;} if(neg)b[--i]='-'; o(b+i);}
static unsigned us(const char*s){unsigned v=0;while(*s>='0'&&*s<='9'){v=v*10+(*s-'0');s++;}return v;}

static char row[SCRW*9+4];

int main(int argc,char**argv){
    static u8 devinfo[512], dispinfo[512], sinfo[128];
    gf_dev_t dev=0; gf_display_t disp=0; gf_surface_t snap=0;
    int x0=0,y0=0,w=SCRW,h=SCRH,step=2, r, bpp, stride; u32 va;
    const char *out = (argc>1)? argv[1] : "/tmp/shot_screen.hex";

    if(argc>=6){ x0=(int)us(argv[2]); y0=(int)us(argv[3]); w=(int)us(argv[4]); h=(int)us(argv[5]); step=1; }
    if(argc>=7) step=(int)us(argv[6]);
    if(step<1) step=1;
    if(x0<0||y0<0||w<1||h<1||x0+w>SCRW||y0+h>SCRH){ o("区域越界\n"); return 1; }

    { int lmfd=open(LM_DEV,LM_FLAGS,0);
      if(lmfd>=0){ int cv[3]={0,0,0}; devctl(lmfd,(int)LM_CHECKVER,cv,12,0); close(lmfd);} }
    if(gf_dev_attach(&dev,GF_DEVICE_INDEX(0),(gf_dev_info_t*)devinfo)!=GF_ERR_OK){o("dev_attach 失败\n");return 2;}
    if(gf_display_attach(&disp,dev,0,(gf_display_info_t*)dispinfo)!=GF_ERR_OK){o("display_attach 失败\n");return 3;}
    { gf_display_info_t *di=(gf_display_info_t*)dispinfo;
      r=gf_surface_create(&snap,dev,SCRW,SCRH,di->format,(void*)0,0);
      o("snapshot surface r="); dec(r); o(" fmt="); dec((int)di->format); o("\n");
      if(r!=GF_ERR_OK) return 4; }
    if(!gf_surface_get_info(snap,(gf_surface_info_t*)sinfo)){o("get_info 失败\n");return 5;}
    { gf_surface_info_t *s=(gf_surface_info_t*)sinfo;
      va=(u32)s->vaddr; stride=(int)s->stride; bpp=(stride>=SCRW*3)?4:2; }
    if(!va){o("snapshot vaddr=0\n");return 6;}

    r = gf_display_snapshot(disp,0, 0,0, SCRW-1,SCRH-1, snap);   /* x2,y2 含端点 */
    o("gf_display_snapshot r="); dec(r); o("\n");
    if(r!=GF_ERR_OK){ o(">>> 抓屏失败\n"); return 7; }

    { int fd=open(out,O_WRONLY|O_CREAT|O_TRUNC,0644);
      if(fd<0){o("打不开输出文件\n");return 8;}
      const char *H="0123456789abcdef";
      int ow=(w+step-1)/step, oh=(h+step-1)/step, y,x,k,i;
      { char hd[48]; k=0; { const char*p="P5HEX "; for(i=0;p[i];i++) hd[k++]=p[i]; }
        { int v=ow,d[6],n=0; if(!v)d[n++]=0; while(v){d[n++]=v%10;v/=10;} while(n) hd[k++]=(char)('0'+d[--n]); }
        hd[k++]=' ';
        { int v=oh,d[6],n=0; if(!v)d[n++]=0; while(v){d[n++]=v%10;v/=10;} while(n) hd[k++]=(char)('0'+d[--n]); }
        hd[k++]='\n'; write(fd,hd,(unsigned)k); }
      for(y=0;y<h;y+=step){
        k=0;
        for(x=0;x<w;x+=step){
            if(bpp==4){ u32 *fb=(u32*)(size_t)va; u32 p=fb[(size_t)(y0+y)*(size_t)(stride/4)+(size_t)(x0+x)];
                        for(i=7;i>=0;i--) row[k++]=H[(p>>(i*4))&0xF]; }
            else      { u16 *fb=(u16*)(size_t)va; u32 p=fb[(size_t)(y0+y)*(size_t)(stride/2)+(size_t)(x0+x)];
                        for(i=3;i>=0;i--) row[k++]=H[(p>>(i*4))&0xF]; }
        }
        row[k++]='\n'; write(fd,row,(unsigned)k);
      }
      close(fd);
      o("已写 "); o(out); o("  "); dec(ow); o("x"); dec(oh);
      o("  bpp="); dec(bpp); o(" stride="); dec(stride); o("\n"); }
    return 0;
}
