#!/bin/ksh
# PCM3.1 MOPF clean BT-fix flasher (fmguard lock-BT + child-shotgun boot-sound, no failed experiments).
USBROOT="$1"
if [ -z "$USBROOT" ] || [ ! -d "$USBROOT" ]; then
    for d in /fs/usb0 /fs/usb1 /fs/usb /media/usb0 /mnt/umass20100t11; do
        [ -d "$d/payload" ] && USBROOT="$d" && break
    done
fi
[ -z "$USBROOT" ] && USBROOT="/fs/usb0"
export PATH="/sbin:/usr/sbin:/bin:/usr/bin:/proc/boot:/HBbin:/HNbin:/mnt/data/tools:$PATH"

LOG="$USBROOT/flash_clean_bt_fix.log"
PAYLOAD="$USBROOT/payload/PCM3_IFS1_MOPF.CHN.clean.ifs"
ARM="$USBROOT/ARM_FLASH_CLEAN_BT_FIX.txt"
EXPECTED_CKSUM="4237630296"
EXPECTED_SIZE="10230208"
FLASH_OFFSET="0x001C0000"

echo "PCM_CLEAN_BTFIX_STARTED" > "$USBROOT/pcm_ran.txt" 2>/dev/null
RESULT_RC=0
{
    echo "=== PCM3.1 MOPF Clean BT-fix Flasher ==="
    echo "Date: $(date 2>/dev/null)"
    echo "USBROOT=$USBROOT  Target=/dev/fs0 offset $FLASH_OFFSET"
    echo "Payload=stock + fmguard(lock-BT) + child-shotgun(boot-sound), no failed experiments"
    echo ""
    echo "--- Tools ---"
    ls -la /proc/boot/flashit /dev/fs0 /HBpersistence/QNXTools/cksum 2>&1
    [ ! -x /proc/boot/flashit ] && echo "ERROR: no flashit" && RESULT_RC=22
    [ ! -e /dev/fs0 ] && echo "ERROR: no /dev/fs0" && RESULT_RC=23
    [ ! -x /HBpersistence/QNXTools/cksum ] && echo "ERROR: no cksum" && RESULT_RC=24
    echo ""
    echo "--- Payload verify ---"
    ls -la "$PAYLOAD" 2>&1
    if [ ! -f "$PAYLOAD" ]; then
        echo "ERROR: missing payload"; RESULT_RC=25
    else
        set -- $(/HBpersistence/QNXTools/cksum "$PAYLOAD" 2>/dev/null)
        echo "payload_cksum=$1 payload_size=$2 (expect $EXPECTED_CKSUM / $EXPECTED_SIZE)"
        if [ "$1" != "$EXPECTED_CKSUM" ] || [ "$2" != "$EXPECTED_SIZE" ]; then
            echo "ERROR: checksum/size mismatch"; RESULT_RC=26
        else
            echo "PAYLOAD_OK=1"
        fi
    fi
    echo ""
    echo "--- Arm ---"
    ARMED=0
    if [ -f "$ARM" ]; then
        [ "$(cat "$ARM" 2>/dev/null)" = "I_UNDERSTAND_FLASH_CLEAN_BT_FIX" ] && ARMED=1 && echo "ARM_OK=1" || { echo "ERROR: arm mismatch"; RESULT_RC=27; }
    else
        echo "NO_ARM_FILE=1 (preflight only, no flash)"
    fi
    if [ "$RESULT_RC" != "0" ]; then
        echo "RESULT=ABORT rc=$RESULT_RC"
    elif [ "$ARMED" = "1" ]; then
        echo ""
        echo "--- FLASHING clean BT-fix IFS1 ---"
        /proc/boot/flashit -v -p /dev/fs0 -a "$FLASH_OFFSET" -d -f "$PAYLOAD" 2>&1
        FLASH_RC=$?
        echo "flashit_rc=$FLASH_RC"
        if [ "$FLASH_RC" = "0" ]; then
            echo "RESULT=FLASH_OK_REBOOT_REQUIRED"
            rm -f "$ARM" 2>/dev/null && echo "ARM removed (one-shot; re-create $ARM to re-flash)"
        else
            echo "RESULT=FLASH_FAILED"; RESULT_RC=$FLASH_RC
        fi
    fi
} > "$LOG" 2>&1
if [ "$RESULT_RC" = "0" ]; then echo "PCM_CLEAN_BTFIX_DONE rc=0" > "$USBROOT/pcm_ran.txt"; else echo "PCM_CLEAN_BTFIX_ERROR rc=$RESULT_RC" > "$USBROOT/pcm_ran.txt"; fi
echo "LOG=$LOG" >> "$USBROOT/pcm_ran.txt"; date >> "$USBROOT/pcm_ran.txt" 2>/dev/null
exit "$RESULT_RC"
