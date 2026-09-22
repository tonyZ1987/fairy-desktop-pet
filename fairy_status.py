# -*- coding: utf-8 -*-
"""fairy 桌宠体检。给 check_fairy.vbs 用（弹窗显示），也可命令行直接跑。

刻意只依赖标准库 + ctypes（不 import numpy/PIL），所以是秒级返回。
结果同时写到 fairy_status.txt（UTF-16，方便 VBS 读成 Unicode 弹窗）。
"""
import ctypes
from ctypes import wintypes as wt
import os
import subprocess
import time

BASE = os.path.dirname(os.path.abspath(__file__))
BEAT = os.path.join(BASE, "heartbeat.txt")
ERRLOG = os.path.join(BASE, "pet_error.log")
POKE = os.path.join(BASE, "poke.txt")
STOP = os.path.join(BASE, "stop.txt")
STATE = os.path.join(BASE, "state.json")
POS = os.path.join(BASE, "pos.json")
OUT = os.path.join(BASE, "fairy_status.txt")

u = ctypes.windll.user32
k = ctypes.windll.kernel32

L = []
L.append("fairy 桌宠体检   %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
L.append("-" * 46)

# ---------- 1. 心跳 ----------
beat_age = None
beat = ""
if os.path.exists(BEAT):
    beat_age = time.time() - os.path.getmtime(BEAT)
    beat = open(BEAT, encoding="utf-8", errors="replace").read().strip()
    L.append("心跳   : %.1f 秒前" % beat_age)
    L.append("         " + beat)
else:
    L.append("心跳   : (没有 heartbeat.txt)")

alive = beat_age is not None and beat_age < 5.0

# 从心跳里取 pid / px / win（用于判断存活与算本体位置）
bpid, bpx, bwin = None, None, None
for tok in beat.split():
    try:
        if tok.startswith("pid="):
            bpid = int(tok[4:])
        elif tok.startswith("px="):
            bpx = int(tok[3:])
        elif tok.startswith("win="):
            bwin = int(tok[4:])
    except ValueError:
        pass

# ---------- 2. 进程 ----------
pids = []
try:
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/FO", "CSV", "/NH"],
                       capture_output=True, timeout=8)
    for line in r.stdout.decode("gbk", "replace").splitlines():
        if not line.strip():
            continue
        c = [x.strip('"') for x in line.split('","')]
        if len(c) >= 5:
            pids.append((int(c[1]), c[4]))
except Exception as e:
    L.append("取进程失败: %r" % (e,))
L.append("进程   : " + (", ".join("pid=%d(%s)" % p for p in pids) if pids else "没有 pythonw 在跑"))
if bpid:
    h = k.OpenProcess(0x1000, False, bpid)      # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        k.CloseHandle(h)
        L.append("         心跳里的 pid=%d 存在 ✅" % bpid)
    else:
        L.append("         心跳里的 pid=%d **已不存在**（进程没了，心跳是残留）" % bpid)

# ---------- 3. 窗口 ----------
wins = []
CB = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.LPARAM)


def cb(hwnd, lp):
    b = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(hwnd, b, 64)
    if b.value == "FairyPetV2":
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        wins.append(dict(hwnd=hwnd, pid=pid.value, vis=bool(u.IsWindowVisible(hwnd)),
                         rc=(r.left, r.top, r.right, r.bottom)))
    return 1


u.EnumWindows(CB(cb), 0)
if wins:
    for w in wins:
        l, t, rr, b2 = w["rc"]
        L.append("窗口   : hwnd=0x%X  %s  %dx%d  左上=(%d,%d)  进程 %d"
                 % (w["hwnd"], "可见" if w["vis"] else "**不可见**",
                    rr - l, b2 - t, l, t, w["pid"]))
else:
    L.append("窗口   : 不存在（class=FairyPetV2 没找到）")

# ---------- 4. 其它文件 ----------
L.append("环境   : state.json=%s" % (
    open(STATE, encoding="utf-8", errors="replace").read().replace("\n", " ")[:80]
    if os.path.exists(STATE) else "(缺)"))
L.append("         pos.json=%s" % (
    open(POS, encoding="utf-8", errors="replace").read().strip()
    if os.path.exists(POS) else "(缺)"))
L.append("         poke.txt=%s   stop.txt=%s"
         % ("有（说明有次双击没被响应）" if os.path.exists(POKE) else "无",
            "有" if os.path.exists(STOP) else "无"))
if os.path.exists(ERRLOG):
    tail = open(ERRLOG, encoding="utf-8", errors="replace").read().strip().splitlines()
    L.append("错误日志: 有 %d 行，最后两行：" % len(tail))
    for x in tail[-2:]:
        L.append("         " + x[:160])
else:
    L.append("错误日志: 无")

# ---------- 5. 结论 ----------
L.append("-" * 46)
if not os.path.exists(BEAT):
    L.append("结论：**没有在运行**。")
    L.append("      → 双击 restart_fairy.vbs（推荐）或 start_fairy.vbs。")
elif not alive:
    L.append("结论：**进程已停止**（心跳停在上面那个时间）。")
    L.append("      → 双击 restart_fairy.vbs 重新启动。")
elif wins and all(not w["vis"] for w in wins):
    L.append("结论：进程在跑，但**窗口不可见**（异常）。")
    L.append("      → 双击 start_fairy.vbs：它会自愈，把窗口亮回来并归位。")
elif not wins:
    L.append("结论：进程在跑，但**没有窗口**（异常）。")
    L.append("      → 双击 restart_fairy.vbs 彻底重来一遍。")
else:
    w = wins[0]
    l, t, rr, b2 = w["rc"]
    # 本体（可见的那个圆）比窗口小：四周留了 (win - px)/2 的辉光发散空间
    mg = ((bwin - bpx) // 2) if (bwin and bpx) else 81
    L.append("结论：**正常在运行**。")
    L.append("      窗口左上=(%d, %d)，边长 %d；本体约在 (%d, %d) 到 (%d, %d) 之间。"
             % (l, t, rr - l, l + mg, t + mg, rr - mg, b2 - mg))
    L.append("      若屏幕上找不到，右击它 →「归位」，就会回到右下角。")
# 最近崩过就点出来（错误日志只记事件、不记"是不是刚刚"，所以看文件时间）
if os.path.exists(ERRLOG) and os.path.getmtime(ERRLOG) > time.time() - 600:
    L.append("      ⚠ 最近 10 分钟内有错误/崩溃记录 —— 上面的错误日志要看一眼，发我也行。")

txt = "\n".join(L)
open(OUT, "w", encoding="utf-16").write(txt)
try:
    print(txt)
except Exception:
    pass
