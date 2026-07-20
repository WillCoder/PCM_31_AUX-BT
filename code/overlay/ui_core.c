/*
 * ui_core.c — PCM 弹出层框架 · 共享渲染核心 + 描述解析器(v2)。
 *
 * **无 libc、无平台 I/O、纯 buffer** —— 台架引擎 coexist_ui.c 与 Mac 预览器 dev/ui_preview.c
 * 都 #include 它 ⇒ "预览所见 == 台架所画"。
 *
 * v2 核心升级: **每像素覆盖度模型**。popbuf(RGBA5551 颜色) + cov(u8 覆盖 0..255)。
 *   合成时 alpha = 全局渐变 × cov/256 -> 投影/圆角/文字/边缘全部软边(不再 1 位硬切)。
 * 像素格式 RGBA5551(R:15-11 G:10-6 B:5-1 A:bit0), 见 KB M-CANVAS-FMT。
 */
#ifndef UI_CORE_C
#define UI_CORE_C

#include "ui_font.h"    /* 离线烤字库: FONT_DIG 数字 + FONT_G/FONT_DATA 比例字体(ASCII+汉字) */

typedef unsigned short u16_;
typedef unsigned char  u8_;

/* 控件类型 */
enum { W_GAUGE=0, W_TOAST=1, W_DIALOG=2, W_WARN=3 };
/* 锚点 */
enum { A_FREE=0, A_TOPC, A_BOTC, A_CENTER, A_TOPR, A_TOPL };

typedef struct {
    int  valid, type, anchor;
    char name[16];
    char title[48], msg[64];      /* toast/dialog/warn 文本(UTF-8) */
    int  x,y,w,h,radius,shadow;
    u16_ panel, accent, fg;       /* 面板/强调色/文字 */
    int  alpha, fadein, hold, anim; /* anim: 0=淡 1=滑上 2=缩放 */
    int  bind, maxval;
    int  bar_x,bar_y,bar_w,bar_h; u16_ bar_track;
    int  num_x,num_y; int has_num;
    int  icon;                    /* 0=无 1=喇叭 2=静音 3=蓝牙 4=警告 */
    int  icon_x,icon_y; u16_ icon_color;
} UIWidget;

#define UI_MAXW 520
#define UI_MAXH 220
static u16_ ui_popbuf[UI_MAXW*UI_MAXH];   /* 颜色 */
static u8_  ui_cov   [UI_MAXW*UI_MAXH];    /* 覆盖度 0..255 */
static u16_ ui_bgbuf [UI_MAXW*UI_MAXH];    /* 存下的背景 */

/* ---------------- RGBA5551 + 混合 ---------------- */
static u16_ ui_rgb(int r,int g,int b){ return (u16_)((((r)>>3)<<11)|(((g)>>3)<<6)|(((b)>>3)<<1)|1); }
static u16_ ui_blend(u16_ bg,u16_ fg,int a){    /* a:0..256 */
    int br=(bg>>11)&31,bgc=(bg>>6)&31,bb=(bg>>1)&31, fr=(fg>>11)&31,fgc=(fg>>6)&31,fb=(fg>>1)&31;
    int r=(fr*a+br*(256-a))>>8,g=(fgc*a+bgc*(256-a))>>8,b=(fb*a+bb*(256-a))>>8;
    return (u16_)((r<<11)|(g<<6)|(b<<1)|1);
}

/* ---------------- 覆盖度式绘制(写 popbuf 颜色 + cov 覆盖) ---------------- */
static int  ui_W,ui_H;                    /* 当前渲染尺寸 */
static void ui_px(int x,int y,u16_ c,int cov){
    if(x<0||x>=ui_W||y<0||y>=ui_H||cov<=0) return;
    int i=y*ui_W+x;
    if(cov>=255){ ui_popbuf[i]=c; ui_cov[i]=255; return; }
    if(ui_cov[i]>=250){                      /* 已不透明底: 叠加混色, 覆盖不变 */
        ui_popbuf[i]=ui_blend(ui_popbuf[i],c,cov+(cov>>7));
    } else {                                 /* 透明/半透底: 设色 + 覆盖取大(避免混进黑底压黑) */
        ui_popbuf[i]=c;
        if(cov>ui_cov[i]) ui_cov[i]=(u8_)cov;
    }
}
static void ui_fill(int x,int y,int w,int h,u16_ c){
    int yy,xx; for(yy=y;yy<y+h;yy++) for(xx=x;xx<x+w;xx++) ui_px(xx,yy,c,255);
}
static void ui_tri(int x0,int y0,int x1,int y1,int x2,int y2,u16_ c){
    int miny=y0<y1?(y0<y2?y0:y2):(y1<y2?y1:y2), maxy=y0>y1?(y0>y2?y0:y2):(y1>y2?y1:y2);
    int px[3]={x0,x1,x2},py[3]={y0,y1,y2},y;
    for(y=miny;y<=maxy;y++){ int xa=-99999,xb=99999,e;
        for(e=0;e<3;e++){ int a=e,b=(e+1)%3;
            if((py[a]<=y&&py[b]>y)||(py[b]<=y&&py[a]>y)){ int xi=px[a]+(px[b]-px[a])*(y-py[a])/(py[b]-py[a]);
                if(xi<xb)xb=xi; if(xi>xa)xa=xi; } }
        if(xa>=xb){ int x; for(x=xb;x<=xa;x++) ui_px(x,y,c,255); } }
}
/* 圆角矩形(AA 角): 面板本体 */
static void ui_round(int x,int y,int w,int h,int R,u16_ c){
    int yy,xx; for(yy=0;yy<h;yy++) for(xx=0;xx<w;xx++){
        int cov=255;
        int cx=-1,cy=-1;
        if(xx<R&&yy<R){cx=R;cy=R;} else if(xx>=w-R&&yy<R){cx=w-R-1;cy=R;}
        else if(xx<R&&yy>=h-R){cx=R;cy=h-R-1;} else if(xx>=w-R&&yy>=h-R){cx=w-R-1;cy=h-R-1;}
        if(cx>=0){ int dx=xx-cx,dy=yy-cy; int d2=dx*dx+dy*dy;
            if(d2 > R*R) cov=0; else if(d2 > (R-1)*(R-1)) cov=140; }   /* 边缘 AA */
        if(cov) ui_px(x+xx,y+yy,c,cov);
    }
}
/* 软投影: 面板矩形外一圈黑色渐隐(offset 向下右, 制造深度) */
static void ui_shadow(int px,int py,int pw,int ph,int R,int sr){
    int ox=2, oy=3, i,j;                              /* 光源左上 -> 影子右下 */
    for(j=-sr;j<ph+sr;j++) for(i=-sr;i<pw+sr;i++){
        /* 到面板矩形(含圆角简化为矩形)的距离 */
        int dx=0,dy=0;
        if(i<R) dx=R-i; else if(i>pw-1-R) dx=i-(pw-1-R);
        if(j<R) dy=R-j; else if(j>ph-1-R) dy=j-(ph-1-R);
        int inside = (i>=0&&i<pw&&j>=0&&j<ph);
        int d = dx>dy?dx:dy;
        if(inside && d<R) continue;                    /* 面板内不画影 */
        int dd = i<0? -i : (i>=pw? i-pw+1:0);
        int de = j<0? -j : (j>=ph? j-ph+1:0);
        int dist = dd>de?dd:de;
        int cov = 90 - dist*90/sr;                     /* 渐隐 */
        if(cov>0) ui_px(px+i+ox, py+j+oy, ui_rgb(0,0,0), cov);
    }
}

/* ---------------- 抗锯齿数字(等宽) ---------------- */
static void ui_digit_aa(int x,int y,int d,u16_ c){
    if(d<0||d>9) return; int gy,gx; const u8_ *g=FONT_DIG[d];
    for(gy=0;gy<FONT_CH;gy++) for(gx=0;gx<FONT_CW;gx++){ int cov=g[gy*FONT_CW+gx]; if(cov) ui_px(x+gx,y+gy,c,cov); }
}
static void ui_num_aa(int rx,int y,int v,u16_ c){   /* 右对齐到 2 位字段 */
    int nd=(v>=10)?2:1, sx=rx+(2-nd)*FONT_CW;
    if(nd==2) ui_digit_aa(sx,y,(v/10)%10,c);
    ui_digit_aa(sx+(nd==2?FONT_CW:0),y,v%10,c);
}

/* ---------------- 比例文字(UTF-8 + 字形表) ---------------- */
static int ui_utf8(const char *s,const char *e,int *cp){
    unsigned char c=(unsigned char)*s;
    if(c<0x80){ *cp=c; return 1; }
    if((c>>5)==6 && s+1<e){ *cp=((c&31)<<6)|(s[1]&63); return 2; }
    if((c>>4)==14 && s+2<e){ *cp=((c&15)<<12)|((s[1]&63)<<6)|(s[2]&63); return 3; }
    if((c>>3)==30 && s+3<e){ *cp=((c&7)<<18)|((s[1]&63)<<12)|((s[2]&63)<<6)|(s[3]&63); return 4; }
    *cp=c; return 1;
}
static const FontGlyph* ui_glyph(int cp){
    int lo=0,hi=FONT_NG-1; while(lo<=hi){ int m=(lo+hi)/2;
        if(FONT_G[m].cp==cp) return &FONT_G[m]; if(FONT_G[m].cp<cp) lo=m+1; else hi=m-1; } return 0;
}
static int ui_text_w(const char *s){
    int w=0,cp,k; const char *e=s; while(*e) e++;
    while(s<e){ k=ui_utf8(s,e,&cp); const FontGlyph*g=ui_glyph(cp); if(g)w+=g->adv; s+=k; } return w;
}
/* ytop=文本顶(ascender线); align 0左 1中 2右(以 x 为基准) */
static void ui_text(int x,int ytop,const char *s,u16_ c,int align){
    int w=ui_text_w(s); if(align==1) x-=w/2; else if(align==2) x-=w;
    const char *e=s; while(*e) e++; int cp,k;
    while(s<e){ k=ui_utf8(s,e,&cp); const FontGlyph*g=ui_glyph(cp);
        if(g){ int gy,gx; const u8_ *d=FONT_DATA+g->off;
            for(gy=0;gy<g->h;gy++) for(gx=0;gx<g->w;gx++){ int cov=d[gy*g->w+gx];
                if(cov) ui_px(x+g->xo+gx, ytop+g->yo+gy, c, cov); }
            x+=g->adv; } s+=k; }
}

/* ---------------- 图标(矢量绘制, 小集; 以后可换烤图集) ---------------- */
static void ui_icon(int which,int x,int y,u16_ c){
    int cy=y+8;
    if(which==1||which==2){                    /* 喇叭 */
        ui_fill(x,cy-3,4,6,c); ui_tri(x+4,cy-7,x+4,cy+7,x+11,cy,c);
        if(which==1){ ui_fill(x+14,cy-3,2,6,c); ui_fill(x+17,cy-6,2,12,c); }   /* 有声波 */
        else { ui_tri(x+14,cy-6,x+20,cy+6,x+20,cy+6,c); ui_tri(x+14,cy+6,x+20,cy-6,x+14,cy-6,c);
               ui_fill(x+13,cy-1,9,2,ui_rgb(230,80,80)); }                     /* 静音斜杠 */
    } else if(which==3){                        /* 蓝牙(简化) */
        ui_fill(x+7,cy-8,2,16,c); ui_tri(x+7,cy-8,x+13,cy-3,x+7,cy,c); ui_tri(x+7,cy,x+13,cy+3,x+7,cy+8,c);
    } else if(which==4){                        /* 警告三角 + ! */
        ui_tri(x+9,cy-8,x,cy+8,x+18,cy+8,c); ui_fill(x+8,cy-2,2,6,ui_rgb(20,20,20)); ui_fill(x+8,cy+5,2,2,ui_rgb(20,20,20));
    }
}

/* ---------------- 锚点 -> (x,y) ---------------- */
static void ui_anchor(UIWidget *w,int SW,int SH){
    int m=18;                                  /* 边距 */
    if(w->anchor==A_TOPC){ w->x=(SW-w->w)/2; w->y=m; }
    else if(w->anchor==A_BOTC){ w->x=(SW-w->w)/2; w->y=SH-w->h-m-30; }
    else if(w->anchor==A_CENTER){ w->x=(SW-w->w)/2; w->y=(SH-w->h)/2; }
    else if(w->anchor==A_TOPR){ w->x=SW-w->w-m; w->y=m; }
    else if(w->anchor==A_TOPL){ w->x=m; w->y=m; }
    /* A_FREE: 用描述里的 x,y */
}

/* ---------------- 渲染一个控件(按类型分派)-> popbuf + cov ---------------- */
static void ui_render(UIWidget *w, int value){
    int i, W=w->w, H=w->h; ui_W=W; ui_H=H;
    if(W<1||H<1||W>UI_MAXW||H>UI_MAXH) return;          /* 防越界(审查 critical 兜底) */
    for(i=0;i<W*H;i++){ ui_popbuf[i]=0; ui_cov[i]=0; }
    int sh=w->shadow, px=sh, py=0, pw=W-sh, ph=H-sh;   /* 面板内缩留投影 */
    if(sh>0) ui_shadow(px,py,pw,ph,w->radius,sh);
    ui_round(px,py,pw,ph,w->radius,w->panel);
    ui_px(px,py,w->panel,0);                            /* no-op 保持 W/H */
    /* 顶边细高光 */
    { int x; for(x=w->radius;x<pw-w->radius;x++) ui_px(px+x,py+1,ui_blend(w->panel,ui_rgb(80,90,105),120),200); }

    if(w->type==W_GAUGE){
        if(w->icon) ui_icon(w->icon,px+w->icon_x,py+w->icon_y,w->icon_color);
        if(w->bar_w>0){ ui_fill(px+w->bar_x,py+w->bar_y,w->bar_w,w->bar_h,w->bar_track);
            int mv=w->maxval>0?w->maxval:40, cv=value<0?0:(value>mv?mv:value), fw=w->bar_w*cv/mv;
            if(fw>0) ui_fill(px+w->bar_x,py+w->bar_y,fw,w->bar_h,w->accent); }
        if(w->has_num) ui_num_aa(px+w->num_x,py+w->num_y,value,w->fg);
    } else if(w->type==W_TOAST){
        if(w->icon) ui_icon(w->icon,px+16,py+(ph-16)/2,w->icon_color);
        ui_text(px+(w->icon?46:20), py+(ph-FONT_CH)/2+2, w->msg, w->fg, 0);
    } else if(w->type==W_DIALOG){
        ui_text(px+pw/2, py+16, w->title, w->fg, 1);
        ui_text(px+pw/2, py+52, w->msg, ui_blend(w->fg,w->panel,60), 1);
        /* 底部两个按钮位(仅显示) */
        int by=ph-40, bw=(pw-48)/2;
        ui_round(px+16,py+by,bw,30,8, ui_blend(w->panel,ui_rgb(255,255,255),30));
        ui_round(px+32+bw,py+by,bw,30,8, w->accent);
        ui_text(px+16+bw/2, py+by+7, "\345\217\226\346\266\210", w->fg, 1);       /* 取消 */
        ui_text(px+32+bw+bw/2, py+by+7, "\347\241\256\345\256\232", ui_rgb(20,20,20), 1); /* 确定 */
    } else if(w->type==W_WARN){
        if(w->icon) ui_icon(w->icon,px+18,py+(ph-16)/2,w->icon_color);
        ui_text(px+(w->icon?52:20), py+(ph-FONT_CH)/2+2, w->msg, w->fg, 0);
    }
}

/* ---------------- 存/合成/恢复(覆盖度模型) ---------------- */
static void ui_savebg(UIWidget *w,u16_ *src,int pitch,int sw,int sh){
    int y,x,W=w->w,H=w->h; for(y=0;y<H;y++){ int py=w->y+y; if(py<0||py>=sh) continue;
        u16_ *s=src+py*pitch,*bg=ui_bgbuf+y*W;
        for(x=0;x<W;x++){ int px=w->x+x; bg[x]=(px>=0&&px<sw)?s[px]:0; } }
}
static void ui_composite(UIWidget *w,u16_ *dst,int pitch,int alpha,int sw,int sh){
    int y,x,W=w->w,H=w->h; for(y=0;y<H;y++){ int py=w->y+y; if(py<0||py>=sh) continue;
        u16_ *d=dst+py*pitch,*pb=ui_popbuf+y*W,*bg=ui_bgbuf+y*W; u8_ *cv=ui_cov+y*W;
        for(x=0;x<W;x++){ int px=w->x+x; if(px<0||px>=sw) continue;
            int a=alpha*cv[x]/256; d[px]=a>0?ui_blend(bg[x],pb[x],a):bg[x]; } }
}
static void ui_restorebg(UIWidget *w,u16_ *dst,int pitch,int sw,int sh){
    int y,x,W=w->w,H=w->h; for(y=0;y<H;y++){ int py=w->y+y; if(py<0||py>=sh) continue;
        u16_ *d=dst+py*pitch,*bg=ui_bgbuf+y*W;
        for(x=0;x<W;x++){ int px=w->x+x; if(px>=0&&px<sw) d[px]=bg[x]; } }
}

/* ---------------- 描述解析(无 libc) ---------------- */
static int ui_atoi(const char *s,const char *e){ int v=0,ng=0; if(s<e&&*s=='-'){ng=1;s++;}
    while(s<e&&*s>='0'&&*s<='9'){ v=v*10+(*s-'0'); s++; } return ng?-v:v; }
static int ui_hx(int c){ if(c>='0'&&c<='9')return c-'0'; if(c>='a'&&c<='f')return c-'a'+10;
    if(c>='A'&&c<='F')return c-'A'+10; return -1; }
static u16_ ui_color(const char *s,const char *e){ int h[6],n=0; while(s<e&&n<6){ int d=ui_hx(*s); if(d>=0)h[n++]=d; s++; }
    if(n<6) return ui_rgb(255,255,255); return ui_rgb(h[0]*16+h[1],h[2]*16+h[3],h[4]*16+h[5]); }
static int ui_keq(const char *ks,const char *ke,const char *l){ int i=0;
    while(ks+i<ke&&l[i]){ if(ks[i]!=l[i])return 0; i++; } return (ks+i==ke)&&l[i]==0; }
static void ui_cpy(char *d,int cap,const char *s,const char *e){ int i=0; while(s<e&&i<cap-1) d[i++]=*s++; d[i]=0; }
static int ui_anchorname(const char *s,const char *e){
    if(ui_keq(s,e,"top-center"))return A_TOPC; if(ui_keq(s,e,"bottom-center"))return A_BOTC;
    if(ui_keq(s,e,"center"))return A_CENTER; if(ui_keq(s,e,"top-right"))return A_TOPR;
    if(ui_keq(s,e,"top-left"))return A_TOPL; return A_FREE;
}
static void ui_defaults(UIWidget *w){
    int i; for(i=0;i<16;i++) w->name[i]=0; w->title[0]=0; w->msg[0]=0;
    w->valid=1; w->type=W_GAUGE; w->anchor=A_CENTER; w->bind=0; w->maxval=40; w->anim=0;
    w->x=214; w->y=176; w->w=372; w->h=76; w->radius=18; w->shadow=10;
    w->panel=ui_rgb(16,20,27); w->accent=ui_rgb(242,168,40); w->fg=ui_rgb(240,243,247);
    w->alpha=248; w->fadein=160; w->hold=1400;   /* ms: 淡入160ms, 停留1.4s */
    w->bar_x=64; w->bar_y=36; w->bar_w=228; w->bar_h=12; w->bar_track=ui_rgb(51,58,68);
    w->num_x=300; w->num_y=24; w->has_num=1;
    w->icon=1; w->icon_x=26; w->icon_y=30; w->icon_color=ui_rgb(204,210,218);
}
static int ui_parse(const char *txt,int len,UIWidget *w){
    ui_defaults(w); int i=0;
    while(i<len){ int ls=i; while(i<len&&txt[i]!='\n') i++; int le=i; if(i<len)i++;
        const char *p=txt+ls,*e=txt+le; while(p<e&&(*p==' '||*p=='\t'||*p=='\r'))p++;
        if(p>=e||*p=='#') continue;
        if(*p=='['){ p++; const char *ts=p; while(p<e&&*p!=' '&&*p!=']')p++;
            if(ui_keq(ts,p,"toast"))w->type=W_TOAST; else if(ui_keq(ts,p,"dialog"))w->type=W_DIALOG;
            else if(ui_keq(ts,p,"warn"))w->type=W_WARN; else if(ui_keq(ts,p,"gauge")||ui_keq(ts,p,"popup"))w->type=W_GAUGE;
            while(p<e&&*p==' ')p++; { int k=0; while(p<e&&*p!=']'&&k<15) w->name[k++]=*p++; w->name[k]=0; } continue; }
        const char *ks=p,*ke=p; while(ke<e&&*ke!='='&&*ke!=' '&&*ke!='\t')ke++;
        const char *q=ke; while(q<e&&(*q==' '||*q=='\t'))q++; if(q>=e||*q!='=')continue; q++;
        while(q<e&&(*q==' '||*q=='\t'))q++;
        const char *vs=q,*ve=e; while(ve>vs&&(ve[-1]==' '||ve[-1]=='\t'||ve[-1]=='\r'))ve--;
        int iv=ui_atoi(vs,ve);
        if(ui_keq(ks,ke,"anchor")) w->anchor=ui_anchorname(vs,ve);
        else if(ui_keq(ks,ke,"x")) w->x=iv; else if(ui_keq(ks,ke,"y")) w->y=iv;
        else if(ui_keq(ks,ke,"w")) w->w=iv; else if(ui_keq(ks,ke,"h")) w->h=iv;
        else if(ui_keq(ks,ke,"radius")) w->radius=iv; else if(ui_keq(ks,ke,"shadow")) w->shadow=iv;
        else if(ui_keq(ks,ke,"alpha")) w->alpha=iv; else if(ui_keq(ks,ke,"fadein")) w->fadein=iv;
        else if(ui_keq(ks,ke,"hold")) w->hold=iv; else if(ui_keq(ks,ke,"anim")) w->anim=iv;
        else if(ui_keq(ks,ke,"maxval")) w->maxval=iv; else if(ui_keq(ks,ke,"bind")) w->bind=iv;
        else if(ui_keq(ks,ke,"panel")) w->panel=ui_color(vs,ve);
        else if(ui_keq(ks,ke,"accent")) w->accent=ui_color(vs,ve);
        else if(ui_keq(ks,ke,"fg")) w->fg=ui_color(vs,ve);
        else if(ui_keq(ks,ke,"title")) ui_cpy(w->title,48,vs,ve);
        else if(ui_keq(ks,ke,"msg")) ui_cpy(w->msg,64,vs,ve);
        else if(ui_keq(ks,ke,"icon")) w->icon=iv;
        else if(ui_keq(ks,ke,"icon_x")) w->icon_x=iv; else if(ui_keq(ks,ke,"icon_y")) w->icon_y=iv;
        else if(ui_keq(ks,ke,"icon_color")) w->icon_color=ui_color(vs,ve);
        else if(ui_keq(ks,ke,"bar_x")) w->bar_x=iv; else if(ui_keq(ks,ke,"bar_y")) w->bar_y=iv;
        else if(ui_keq(ks,ke,"bar_w")) w->bar_w=iv; else if(ui_keq(ks,ke,"bar_h")) w->bar_h=iv;
        else if(ui_keq(ks,ke,"bar_track")) w->bar_track=ui_color(vs,ve);
        else if(ui_keq(ks,ke,"num")) w->has_num=iv;
        else if(ui_keq(ks,ke,"num_x")) w->num_x=iv; else if(ui_keq(ks,ke,"num_y")) w->num_y=iv;
    }
    if(w->w<1)w->w=1; if(w->h<1)w->h=1;                 /* 下界 floor: 防负值越界(审查 critical) */
    if(w->w>UI_MAXW)w->w=UI_MAXW; if(w->h>UI_MAXH)w->h=UI_MAXH;
    if(w->maxval<1)w->maxval=40; if(w->radius<0)w->radius=0; if(w->shadow<0)w->shadow=0;
    return 1;
}
static unsigned ui_fnv(const char *b,int n){ unsigned h=2166136261u; int i;
    for(i=0;i<n;i++){ h^=(unsigned char)b[i]; h*=16777619u; } return h; }

#endif
