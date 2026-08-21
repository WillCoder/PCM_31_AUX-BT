/* fx —— 台架上的文件字节读/写工具, 为了不用每改一个字节就跑一趟 U 盘。
 *   读: fx <file> <hex_off> <nwords>
 *   写: fx <file> <hex_off> <expect_old_u32> <new_u32>       旧值对不上就拒绝
 * 写完回读校验。全部按小端 u32。
 */
typedef unsigned u32;
#define O_RDONLY 0
#define O_RDWR   2
extern int  open(const char*,int,...); extern int close(int);
extern long lseek(int,long,int);       extern int read(int,void*,int);
extern int  write(int,const void*,int);
static void o(const char*s){int n=0;while(s[n])n++;write(1,s,n);}
static void ox(u32 v){char b[11];int i;b[0]='0';b[1]='x';
    for(i=0;i<8;i++)b[2+i]="0123456789abcdef"[(v>>((7-i)*4))&0xF];b[10]=0;o(b);}
static void od(u32 v){char b[12];int i=0,j;if(!v){o("0");return;}
    while(v){b[i++]=(char)('0'+v%10);v/=10;}for(j=i-1;j>=0;j--)write(1,&b[j],1);}
static u32 us(const char*s){u32 v=0,h=0;if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')){s+=2;
    while(*s){char c=*s;if(c>='0'&&c<='9')h=h*16+(u32)(c-'0');
      else if(c>='a'&&c<='f')h=h*16+(u32)(c-'a'+10);
      else if(c>='A'&&c<='F')h=h*16+(u32)(c-'A'+10);else break;s++;}return h;}
    while(*s>='0'&&*s<='9'){v=v*10+(u32)(*s-'0');s++;}return v;}
static u32 wb[64];
int main(int argc,char**argv){
    int fd; u32 off,n,i,want,val,old=0,back=0;
    if(argc<4){o("fx <file> <hex_off> <nwords>            读\n"
                 "fx <file> <hex_off> <old_u32> <new_u32>  写(旧值必须对得上)\n");return 1;}
    off=us(argv[2]);
    if(argc==4){
        n=us(argv[3]); if(n>64) n=64;
        fd=open(argv[1],O_RDONLY,0); if(fd<0){o("ERR open\n");return 2;}
        if(lseek(fd,(long)off,0)<0){o("ERR lseek\n");close(fd);return 3;}
        i=(u32)read(fd,wb,(int)(n*4));
        if(i<4){o("ERR read\n");close(fd);return 4;}
        for(n=0;n<i/4;n++){ if((n%4)==0){o("\n");ox(off+n*4);o(":");} o(" ");ox(wb[n]); }
        o("\n"); close(fd); return 0;
    }
    want=us(argv[3]); val=us(argv[4]);
    fd=open(argv[1],O_RDWR,0); if(fd<0){o("ERR open(rw) ");o(argv[1]);o("\n");return 2;}
    if(lseek(fd,(long)off,0)<0||read(fd,&old,4)!=4){o("ERR 读旧值\n");close(fd);return 3;}
    o("旧值 @");ox(off);o(" = ");ox(old);o("\n");
    if(old!=want){o("‼️ 旧值 != 期望 ");ox(want);o(" -> 拒绝写\n");close(fd);return 5;}
    if(lseek(fd,(long)off,0)<0||write(fd,&val,4)!=4){o("ERR 写失败\n");close(fd);return 6;}
    if(lseek(fd,(long)off,0)<0||read(fd,&back,4)!=4){o("ERR 回读\n");close(fd);return 7;}
    o("新值回读 = ");ox(back); o(back==val?"  ✅\n":"  ‼️ 不符\n");
    o("撤销: fx ");o(argv[1]);o(" ");ox(off);o(" ");ox(val);o(" ");ox(old);o("\n");
    close(fd); return back==val?0:8;
}
