/* mempoke_fix <pid> <start_hex> <end_hex>
 * 在 [start,end) 扫描唯一8字节签名,命中则把 签名+4 那字节(mov #1 的立即数)改成 0x07 */
extern int open(const char*,int,...); extern int close(int);
extern int read(int,void*,unsigned); extern int write(int,const void*,unsigned);
extern long lseek(int,long,int);
#define O_RDWR 2
static const unsigned char SIG[8]={0x02,0x8d,0x05,0x1e,0x01,0xe1,0x15,0x1e};
static int sl(const char*s){int n=0;while(s[n])n++;return n;}
static void o(const char*s){write(1,s,sl(s));}
static void hx(unsigned v){char b[11];int i;b[0]='0';b[1]='x';for(i=0;i<8;i++)b[2+i]="0123456789ABCDEF"[(v>>((7-i)*4))&0xF];b[10]='\n';write(1,b,11);}
static void hb(unsigned char v){char b[5];b[0]='0';b[1]='x';b[2]="0123456789ABCDEF"[(v>>4)&0xF];b[3]="0123456789ABCDEF"[v&0xF];b[4]='\n';write(1,b,5);}
static unsigned us(const char*s){unsigned v=0;while(*s>='0'&&*s<='9'){v=v*10+(*s-'0');s++;}return v;}
static unsigned xs(const char*s){unsigned v=0;if(s[0]=='0'&&(s[1]=='x'||s[1]=='X'))s+=2;while(*s){v<<=4;if(*s>='0'&&*s<='9')v|=*s-'0';else if(*s>='a'&&*s<='f')v|=*s-'a'+10;else if(*s>='A'&&*s<='F')v|=*s-'A'+10;s++;}return v;}
static void mkpath(char*d,unsigned pid){const char*a="/proc/",*b="/as";int i=0,j,n=0;char num[12],t[12];int k=0;while(a[i]){d[i]=a[i];i++;}if(pid==0)num[n++]='0';else{while(pid){t[k++]='0'+pid%10;pid/=10;}while(k)num[n++]=t[--k];}for(j=0;j<n;j++)d[i++]=num[j];j=0;while(b[j])d[i++]=b[j++];d[i]=0;}
int main(int argc,char**argv){
  char path[40]; unsigned char buf[4096]; int fd,n,i;
  unsigned pid,start,end,va,mapped=0;
  if(argc<4){o("mempoke_fix <pid> <start> <end>\n");return 1;}
  pid=us(argv[1]); start=xs(argv[2]); end=xs(argv[3]);
  mkpath(path,pid); o("path="); o(path); o("\n");
  o("scan "); hx(start); o(" .. "); hx(end);
  fd=open(path,O_RDWR,0); if(fd<0){o("ERR open\n");return 2;}
  va=start;
  while(va<end){
    if(lseek(fd,(long)va,0)<0){ va+=4096; continue; }
    n=read(fd,buf,sizeof(buf));
    if(n<8){ va+=4096; continue; }     /* unmapped/short -> skip page */
    mapped=1;
    for(i=0;i+8<=n;i++){
      int m=1,k; for(k=0;k<8;k++) if(buf[i+k]!=SIG[k]){m=0;break;}
      if(m){
        unsigned hit=va+i, patva=hit+4; unsigned char nv=0x07,rb=0;
        o("FOUND sig @"); hx(hit);
        o("patch @"); hx(patva);
        if(lseek(fd,(long)patva,0)>=0 && write(fd,&nv,1)==1){
          lseek(fd,(long)patva,0); read(fd,&rb,1);
          o("readback="); hb(rb);
          o(rb==0x07?"*** PATCHED OK 01->07 ***\n":"*** WRITE NOT STICK ***\n");
        } else o("write failed\n");
        close(fd); return 0;
      }
    }
    va += (n-7);   /* overlap 7 so boundary-spanning sig is caught */
  }
  o(mapped?"scan done: SIG NOT FOUND\n":"scan done: nothing mapped in range\n");
  close(fd); return 3;
}
