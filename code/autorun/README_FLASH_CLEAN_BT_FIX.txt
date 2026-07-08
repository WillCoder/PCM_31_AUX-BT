干净固件 = stock + fmguard(锁BT,断开不掉FM) + child-shotgun(开机保持蓝牙自动出声)
无失败实验(skip-a2dp/desiredApp/c1vhook/auxkick 全清)。
用法: 插台架 USB -> autorun 触发 copie_scr.sh -> run.sh 校验cksum+ARM -> flashit -v 刷 -> 删ARM(一次性) -> 断电重启。
payload cksum=4237630296 size=10230208。日志见 flash_clean_bt_fix.log / pcm_ran.txt。
