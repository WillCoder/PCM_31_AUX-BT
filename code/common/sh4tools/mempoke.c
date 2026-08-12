/*
 * mempoke.c — read (and optionally write) a byte in a running process's memory
 *             via QNX /proc/<pid>/as.  For PCM 3.1 (SH4A / QNX 6.3).
 *
 * Build (cross .o on Linux):
 *   sh4-linux-gnu-gcc -c -O2 -ffreestanding -nostdlib -o mempoke.o mempoke.c
 * Link ON TARGET (PCM):
 *   /usr/bin/cc -o mempoke mempoke.o          # preferred (adds crt + libc)
 *   # or: /usr/bin/ld -o mempoke /usr/lib/crt1.o mempoke.o -lc \
 *   #        -dynamic-linker /usr/lib/ldqnx.so.2
 *
 * Usage:
 *   mempoke <pid> <hexaddr>            # READ 1 byte, print it
 *   mempoke <pid> <hexaddr> <hexval>  # WRITE 1 byte (after printing old)
 *
 * Example (read the {0,10}->FM map immediate at 0x82b65e0, expect 0x01):
 *   mempoke 12345 0x82b65e0
 * Example (patch mov #1 -> mov #7 to make BT(10) route to A2DP):
 *   mempoke 12345 0x82b65e0 0x07
 *
 * Part of PCM-Forge FM-boot fix research.
 */

/* QNX libc syscalls — resolved at on-target link with -lc */
extern int   open(const char *path, int oflag, ...);
extern int   close(int fd);
extern int   write(int fd, const void *buf, unsigned nbytes);
extern int   read(int fd, void *buf, unsigned nbytes);
extern long  lseek(int fd, long off, int whence);

#define O_RDWR   2
#define O_RDONLY 0
#define SEEK_SET 0

static int slen(const char *s){int n=0;while(s[n])n++;return n;}
static void out(const char *s){write(1,s,slen(s));}
static void err(const char *s){write(2,s,slen(s));}
static void puthex(unsigned v){
    char b[11]; int i; b[0]='0'; b[1]='x';
    for(i=0;i<8;i++) b[2+i]="0123456789ABCDEF"[(v>>((7-i)*4))&0xF];
    b[10]='\n'; write(1,b,11);
}
static void puthex2(unsigned char v){
    char b[5]; b[0]='0';b[1]='x';
    b[2]="0123456789ABCDEF"[(v>>4)&0xF];
    b[3]="0123456789ABCDEF"[v&0xF]; b[4]='\n';
    write(1,b,5);
}
static unsigned xstr(const char *s){
    unsigned v=0;
    if(s[0]=='0'&&(s[1]=='x'||s[1]=='X')) s+=2;
    while(*s){
        v<<=4;
        if(*s>='0'&&*s<='9') v|=*s-'0';
        else if(*s>='a'&&*s<='f') v|=*s-'a'+10;
        else if(*s>='A'&&*s<='F') v|=*s-'A'+10;
        s++;
    }
    return v;
}
static unsigned ustr(const char *s){ /* decimal */
    unsigned v=0; while(*s>='0'&&*s<='9'){v=v*10+(*s-'0');s++;} return v;
}
/* build "/proc/<pid>/as" */
static void mkpath(char *dst, unsigned pid){
    const char *a="/proc/"; const char *b="/as";
    int i=0,j; char num[12]; int n=0;
    while(a[i]){dst[i]=a[i];i++;}
    if(pid==0){num[n++]='0';}
    else{ char t[12]; int k=0; while(pid){t[k++]='0'+pid%10;pid/=10;} while(k)num[n++]=t[--k]; }
    for(j=0;j<n;j++)dst[i++]=num[j];
    j=0; while(b[j])dst[i++]=b[j++];
    dst[i]=0;
}

int main(int argc, char **argv){
    char path[32];
    unsigned char byte;
    int fd, doWrite;
    unsigned pid, addr, val;

    if(argc < 3){
        out("mempoke <pid> <hexaddr> [hexval]\n");
        return 1;
    }
    pid  = ustr(argv[1]);
    addr = xstr(argv[2]);
    doWrite = (argc >= 4);
    val  = doWrite ? xstr(argv[3]) : 0;

    mkpath(path, pid);
    out("path="); out(path); out("\n");

    fd = open(path, doWrite ? O_RDWR : O_RDONLY, 0);
    if(fd < 0){ err("ERR open /proc/<pid>/as failed\n"); return 2; }

    if(lseek(fd, (long)addr, SEEK_SET) < 0){ err("ERR lseek failed\n"); close(fd); return 3; }
    if(read(fd, &byte, 1) != 1){ err("ERR read failed\n"); close(fd); return 4; }
    out("old="); puthex2(byte);

    if(doWrite){
        unsigned char nb = (unsigned char)val;
        if(lseek(fd, (long)addr, SEEK_SET) < 0){ err("ERR lseek2 failed\n"); close(fd); return 5; }
        if(write(fd, &nb, 1) != 1){ err("ERR write failed\n"); close(fd); return 6; }
        /* read back */
        if(lseek(fd, (long)addr, SEEK_SET) >= 0 && read(fd, &byte, 1) == 1){
            out("new="); puthex2(byte);
        }
    }
    close(fd);
    return 0;
}
