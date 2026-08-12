# porsche-pcm31-mods

**Porsche PCM3.1 —— 两个已上车的改装,以及做出它们的那套工具箱**

> Porsche PCM3.1(CHN)· QNX 6.3.x · SH-4A · 2026-08
> **✅ 两个 feature 都已在真车(911/9x1)上运行,不只是台架。**
>
> [English](README.md) · **简体中文**

> ⚠️ **免责声明**:仅供学习研究,**刷写有砖机风险且可能救不回来(撞看门狗无限重启,快到停不进 emergency shell),后果自负,别刷挂了怪我。** 全文见 [DISCLAIMER.md](DISCLAIMER.md) · 授权 [GPL-3.0](LICENSE)

![工作台架](images/01-bench-working-fm.jpg)

> **代价**:早期**报废两台台架、救不回来**——撞看门狗、无限重启,快到停不进 emergency shell。这套方法论是那两台换来的:startup+imagefs 两段各 sum-zero 预检门,加上认清"稳定砖能串口救、看门狗砖救不了",之后再没砖过。详见 [bluetooth-fix.zh-CN.md](bluetooth-fix.zh-CN.md) 开篇的"前情"。

## 这个仓库里有什么

两个 feature,以及它们共同踩在上面的工具。

| | 做什么 | 交付方式 | 状态 |
|---|---|---|---|
| **1 · [蓝牙修复](bluetooth-fix.zh-CN.md)** | 冷启动后蓝牙保持选中 —— **而且出声**,一个键都不用按 | **刷 IFS1** | ✅ 台架 + 真车 |
| **2 · [音量弹窗](volume-osd.zh-CN.md)** | 自绘弹窗画在独立硬件图层上,压在完全没动过的原厂 UI 之上 | runtime app,**不刷写** | ✅ 台架 + 真车 |
| 台架串口工具链 | 57600 root shell 开发闭环 —— 推二进制、跑、拉日志、杀掉;不插 U 盘、不重刷 | 开发工具 | ✅ |
| 逆向 + 构建管线 | 可靠 SH4 反汇编、`sh4emu` 解释器、IFS inflate/patch/deflate、刷前预检门 | 开发工具 | ✅ |

**一个东西"怎么交付"是关于它最该先知道的事。** 刷写补丁能把机器变砖,而且是改原厂代码的唯一途径([为什么](#为什么必须靠刷写));runtime app 砖不了机器,重启就没了。

两份 feature 文档用的是同一套骨架,所以你读哪一份都是同一个读法:

| | |
|---|---|
| **第一部分 · 技术解释** | 原厂系统怎么工作、硬件事实、根因。只写结论。 |
| **第二部分 · 技术方案** | 做了什么、为什么管用、怎么交付、怎么验证。 |
| **第三部分 · 涉及的问题** | 所有症状、每一条死路及其失败的确切原因、各种坑、教训。 |

---

# Feature 1 · 蓝牙 —— 开机锁定,而且出声

📄 完整文档:**[bluetooth-fix.zh-CN.md](bluetooth-fix.zh-CN.md)** · [English](bluetooth-fix.md)

一条痛点链的两章:

- **锁BT** —— 蓝牙放着歌熄火,下次上车**每次都回到 FM**,要手动切回蓝牙
- **开机出声** —— 锁住蓝牙后,开机手机连上了**却没声音**,要手动 AUX→BT

**真车上的最终效果**:熄火、带着手机走人、回来冷启动 —— 手机自动重连,**音乐自己就回来了,还在蓝牙上,一个键都不用按。**

## 技术解释

- **锁BT 的根因** —— 真正的源仲裁在运行时,不在你第一反应会去翻的 OnOff/fallback 死代码层。开机(以及手机断开)时,FM 会被提交为源、把蓝牙顶掉。
- **开机出声的根因** —— 开机时从不"申请音频焦点" → getter 返 `-2` → establishment 短路 → DSP 不路由 → 无声。只有**真正的源切换**才会产生这次申请,这正是手动切一次 AUX→BT 就好了的原因。
- **`+0x944` 分叉** —— `entertSourceChanged` 在 `main+0x944` 上分叉。**0** 走耐久的 child-dispatch 腿(手动切源走的就是这条,不会被拆);**非 0** 走脆弱的 TLAM 连接+重试腿,它的异步处理器约 5 秒后重试耗尽,调 `switchAudio(Default)` 把声音又拆掉。cave 对 `+0x944` 一个字节都不写,所以永远走耐久腿。

### 为什么必须靠刷写

`/proc/<pid>/as` 只写得进 RW 页,**写只读 code/rodata 会失败** —— 这就是 CoW 墙,真机实证。两个 cave 都在 RX 段,所以这硬件上**做不了运行时代码注入**。串口 poke 循环是**找到**修复的手段,刷 IFS1 才是**交付**它的手段。

> 注:台架(MOPF / `IFS_G1_E2`)和真车(`IFS_9X1`)的 `PCM3Root` **二进制字节完全相同**,只有周围的 imagefs 不同,所以真车进的就是台架上验过的那一份。定位 `PCM3Root` 要走 **imagefs 目录**(`mnt/ifs1/HBproject/PCM3Root`),**别用 ELF 头去搜** —— 镜像里每个 SH4 可执行文件开头都一样,你一定会抽错文件。

## 技术方案

**组成** = 原厂 `PCM3Root` + fmguard(锁BT)+ child-chain cave(开机出声),交付方式是**刷 IFS1**。

- **fmguard** —— 挂在仲裁池槽 `0x082ac898` 上的一段 **18 字节 cave**:当"要提交的源"是 FM 时,就不提交。于是 FM 无法在开机/断开时自动抢占。手动切源不受影响。
- **child-vtable shotgun** —— 插桩 child 对象 vtable 的全部 5 个方法;连接事件调到哪一个,哪一个就在 bug 态下触发一次 AUX→BT,让 establishment 激活、出声。
- **`main` 自派生** —— shotgun 最初把 `main`(`CPSoundPresCtrl` 单例)**写死了**。它是堆对象,**每次开机都漂** —— 六个 boot,六个不同地址。写死的地址一旦落在未映射页,**第一次解引用就 fault** → 看门狗 → 救不回来的那种砖。改法是从那个永远有效的指针出发,即分发的 `this`:

  ```
  main = *(*(*(child + 0x38) + 0x08) + 0x70)
  ```

  每一跳在解引用**之前**先做区间检查 `[0x08600000, 0x08f00000)`,拿到结果再用 vtable 校验(`*(main) == 0x085c4c5c`),所以对象图还没建好/正在重连的状态会**干净地 bail、下次调用再试**,而不是 fault。这条链是**结构性的,不是巧合**:台架 playing/disconnected/AUX、真实 `-2` 连接bug快照、修好后的工作态、以及**真车** —— 六个不同的 `main` 地址,全部解得对。

| | 位置 |
|---|---|
| **解法** | [`code/bluetooth-fix/build_shotgun_child_chain.py`](code/bluetooth-fix/build_shotgun_child_chain.py) |
| 它的前身(作为参照基线保留) | [`code/bluetooth-fix/build_shotgun_child.py`](code/bluetooth-fix/build_shotgun_child.py) —— 同一个 cave,但 `main` 写死会漂 |
| 离线证明 | [`code/bluetooth-fix/validate_shotgun_child_chain.py`](code/bluetooth-fix/validate_shotgun_child_chain.py) —— 在 `sh4emu` 里对真实 `-2` 快照跑 cave:自派生 `main`、开火、且(含故意喂坏指针)绝不 fault |
| 预检门 | [`code/common/verify_ifs_flashable.py`](code/common/verify_ifs_flashable.py) |
| USB autorun 刷写 | [`code/bluetooth-fix/autorun/`](code/bluetooth-fix/autorun/) —— 零串口部署 |
| 可刷固件 | **不含** —— 改过的专有固件;用这些工具 + 你自己 dump 的固件自行构建 |

## 涉及的问题

六条死路,每一条都在真机上试过,每一条都有确切成因:静态写字段、钩 vtable `+0x44`、运行时注入代码(CoW 墙)、用 Ghidra 反编译精确改代码(它对这个二进制的函数入口是系统性错位的)、vtrace 动态插桩,以及重连时一次都没开火的主 vtable shotgun。

**[→ bluetooth-fix.zh-CN.md 第三部分](bluetooth-fix.zh-CN.md)** 按事情发生的顺序全部记着,还有开篇那两台报废的台架,以及由此得到的教训。

---

# Feature 2 · 画在自己硬件图层上的音量弹窗

📄 完整文档:**[volume-osd.zh-CN.md](volume-osd.zh-CN.md)** · [English](volume-osd.md)

![音量 OSD 画在独立硬件图层上, 压在原厂蓝牙播放页之上](images/06-overlay-volume-osd.jpg)

*真车实拍(不是台架):我们自绘的音量 OSD 画在独立的 Carmine 硬件图层上,压在完全没动过的原厂蓝牙播放页之上。底下的原厂页面照常工作 —— 曲名和歌手还在滚动、02:42 / 05:13 的进度在走、"Track order" 按钮仍然高亮。全程零刷写,弹窗纯运行时。*

*拍摄于 2026-08-06,当时面板还是半透明的;现在交付配置已改为不透明(`panel_alpha = 255`),半透明的代价见陷阱 3。*

## 技术解释

- **做法** —— 作为**第二个 gf 客户端**,抢一个 `layermanager` 从不分配的 Carmine 硬件层。各硬件层有各自独立的 scanout 缓冲,所以**物理上碰不到**原厂 UI 的缓冲和锁;老方案"共享原厂 surface"导致的残影/撕裂在硬件层面消失。
- **硬件层你永远不可能独占。** 系统没有归属权机制 —— 逐层状态是同一份共享记录、后写者赢。
- **各值一天的四条硬件事实**:驱动把**层号反转**(`硬件层 = 7 − gf层`);像素格式是 **RGBA5551 而不是 API 报的 ARGB1555**;**每次 `gf_layer_update` 前必须完整重申**,否则永久死锁在 `gdcServerCarmine`;alpha 平面的 **byte stride 必须 64 字节对齐**,否则整个面板会斜切成条纹。

## 技术方案

- **让出协议**取代"独占":每拍监视自己那条记录,一旦原厂开始用它,就**彻底停手**,直到它重新空闲。这照搬了原厂自己的优先级模型,而且不需要知道任何车型的层分布 —— 这一点很要紧,因为**层分布随车型而变**。去找"一块没人用的层"是死路。
- **引擎 / 内容分离** —— 常驻引擎 + 热加载的 `ui.def`。改布局或配色只推 348 字节文本:不重编、不重启。
- **用真截图验证,别盯着屏幕看** —— `pcmshot` 抓帧缓冲,`shotdiff` 量出弹窗的实际外接框,和 `ui.def` 要求的值对拍。
- 代码:[`code/volume-osd/`](code/volume-osd/)

> 🚨 **凡是装在车上的,`panel_alpha` 必须是 255。** `gdcServerCarmine` 从一个四块的池子里发放 alpha 混合平面,原厂开机就占着三块,而且**释放路径根本走不到** —— 一块平面发出去,**到断电为止收不回来**。只要画出一次半透明弹窗,原厂倒车距离显示在这一整个点火周期里就申请不到平面:探测区几何完好、边缘柔和,但填充是纯黑。这是**读服务端自己的分配账本量出来的,不是推断**;见 [volume-osd.zh-CN.md](volume-osd.zh-CN.md) 的**陷阱 3**。

## 涉及的问题

各种坑都紧挨着它们所破坏的那份配方放着,因为那才是你需要它的地方。真正的死路 —— "找一块没人用的层"、靠把层刷成透明来隐藏、指望 `gf_layer_detach` 能释放什么 —— 收在 **[→ volume-osd.zh-CN.md 第三部分](volume-osd.zh-CN.md)**。

---

# 共用工具

两个 feature 都是踩在这些上面长出来的,缺了它们哪个都做不成。

## 台架串口工具链

57600 root shell 开发闭环 —— 推二进制、跑、拉日志、杀掉,**不用插 U 盘、不用重刷**。

- [`code/common/serial/`](code/common/serial/) —— `ser_push.py`(分块上传 + cksum 校验)、`ser_pull.py`、`ser2.py`(执行命令)、`ser_kill.py`。
- [`code/common/sh4tools/SERIAL_LOOP.md`](code/common/sh4tools/SERIAL_LOOP.md) —— 与之配套的 `/proc/<pid>/as` poke 循环,以及**它够得着什么、够不着什么**。

## 逆向 + 构建

- [`code/common/`](code/common/) —— `sh4emu.py`(在内存快照上真执行 `PCM3Root` 函数的 SH4 解释器)、IFS inflate/patch/deflate 管线,以及 `verify_ifs_flashable.py`(两台报废台架换来的刷前门)。
- [`code/common/sh4tools/`](code/common/sh4tools/) —— 设备端 C 工具:`mempoke.c`(`mp2` 字节读写器)和 `alphatab.c`(只读的 alpha 平面账本探针)。
- 完整索引见 [`code/README.md`](code/README.md)。

---

## 仓库结构

```
README.md / README.zh-CN.md        本索引
bluetooth-fix.md   (+ .zh-CN.md)   feature 1 完整文档
volume-osd.md      (+ .zh-CN.md)   feature 2 完整文档
code/
  bluetooth-fix/                   cave、离线验证器、USB autorun 刷写脚本
  volume-osd/                      overlay 引擎、渲染器、ui.def、截图验证
  common/                          sh4emu、IFS 管线、串口工具链、设备端 C 工具
images/                            台架照片与截图
```

## 加新 feature 的约定

这套结构能吸收新东西,**不需要重新编号**:

1. **代码** → 自己的 `code/<feature>/` 目录,里面放一个短 `README.md`,写清那些不写下来就得让下一个人重新踩的坑。任何第二个 feature 也能用的东西,放 `code/common/`。
2. **文档** → 仓库根目录 `<feature>.md` + `<feature>.zh-CN.md`。**英文是主文档,中文是译本。**
3. **用同一套三段** —— *技术解释* / *技术方案* / *涉及的问题*。别让读者每换一个 feature 就要重新学一种排法。
4. **索引** → 在*这个仓库里有什么*加一行,并在这里加一个 `#` 小节,**和现有 feature 同级**。已交付的 feature 永远不做另一个的子小节。
5. **写明怎么交付** —— runtime app 还是刷写补丁。这一条决定风险画像,是任何读者最先需要知道的。
6. **把死路留下。** "我试过什么、为什么不行"是这些文档里最值钱的部分;一个没有死路记录的 feature,会让下一个人再付一遍你付过的那些天。

---

## 致谢

特别感谢以下参考——是它们让整个开发有了好的基础和思路:

- **[dspl1236/PCM-Forge](https://github.com/dspl1236/PCM-Forge)** —— PCM-Forge 项目及其逆向工程的奠基工作。
- **[Rennlist —— "PCM3.1 reboot problem, repair attempt"](https://rennlist.com/forums/991/1484228-pcm3-1-reboot-problem-repair-attempt.html)** —— PCM3.1 重启/维修的社区讨论帖。

而这个问题最终,是与 **Claude(Anthropic)** 的协作一点点磨出来的——逆向、工具(可靠 SH4 反汇编、`sh4emu`、shotgun 打法)、直到最后的修复,都是这一来一回配合的结果,再上台架和真车实测。缺了哪一半,都到不了这儿。

---

## 授权

版权 © 2026 WillCoder

本项目是自由软件:你可以在 **GNU General Public License v3.0**(或你选择的任意更新版本)条款下重新分发和/或修改它——见 [LICENSE](LICENSE)。发布本项目是希望它有用,但**不作任何担保**(另见 [DISCLAIMER.md](DISCLAIMER.md))。
