# -*- coding: utf-8 -*-
"""fairy v2 · 桌面常驻主程序

形象与动效依据：Chengzhibense/Fairy-DSH 官方 SVG 源码（见 `STEP1B_官方源码依据.md`）
渲染：`fairy_layers.FastMascot`（图层预栅格化，260px 实测 3.1 ms/帧 ⇒ 60fps 有余量）

两个状态
--------
  idle    睁眼（官方的 normal）
  working 半睁眼工作态（官方的 thinking）

状态来源（优先级从高到低）
  1. 右键菜单手动指定（选过就自动关掉"跟随"）
  2. 本目录下的 `state.json`（我每次开工写 working、收工写 idle）：
         {"state": "working", "ts": "...", "ttl": 2700, "note": "..."}
     · state: "working"/"idle"（也认 thinking/busy/normal）
     · ttl  : 秒；超过这个时长没更新就自动回落到 idle（防我忘了写）
             不写 ttl 则用 AUTO_TIMEOUT_S
  3. 都没有 → idle

其它开关（都在本目录下）
  stop.txt      放进去 → 程序自行退出并删掉它（脚本化关闭，不用碰鼠标）
  heartbeat.txt 心跳：每 60 帧写一次 tick/时间/位置/状态/实际帧率
  pet_error.log 出错才写（pythonw 会吞掉一切输出，排障第一步看心跳）
  pos.json      窗口位置记忆

启动：双击 `start_fairy.vbs`（内容纯 ASCII；不要用 python 直接跑，会弹黑框）
自检：python fairy_pet.py --selftest    只建窗口不显示、渲 120 帧、报帧时
      python fairy_pet.py --smoke 6     显示 6 秒后自动退出（临时看一眼）
"""
import os
import sys
import json
import time
import random
import subprocess      # ★ 只为"按 pid 结束赖着不走的旧实例"（见 _retire_existing）
import ctypes
from ctypes import wintypes

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import dsh_mascot as M
import fairy_bar as FB                     # ★ 工作态进度条（唯一真源，2026-09-22 接入）
from fairy_layers import FastMascot, bgr, canvas_px
import fairy_screen as SCR       # ★ 屏幕测量：默认档位 / 居中点 / 默认落点（2026-09-23）
import fairy_activity as FA      # ★ 会话活动侦测：主人一发指令就半睁（不靠我记得发信号）
try:
    import fairy_boot as BOOT        # ★ 开机动画（独立窗口：倒计时 → CRT 开机 → 拨片）
except Exception as _e:              #   ★ 兜底：动画模块出问题**绝不能让桌宠起不来**
    BOOT = None
    print("[fairy] 开机动画模块不可用：%r" % (_e,))

# ---------------------------------------------------------------- 路径
STATE_FILE = os.path.join(BASE, "state.json")
POS_FILE = os.path.join(BASE, "pos.json")
BEAT = os.path.join(BASE, "heartbeat.txt")
ERRLOG = os.path.join(BASE, "pet_error.log")
STOP_FILE = os.path.join(BASE, "stop.txt")
POKE_FILE = os.path.join(BASE, "poke.txt")      # ★ 再次双击启动时，用它叫醒已在运行的实例
NOTIFY_FILE = os.path.join(BASE, "notify.json")  # ★ 任务完成通知：我干完活写它，桌宠弹通知卡
PROGRESS_FILE = os.path.join(BASE, "progress.json")   # ★ 工作进度：我上报 0~100，工作态显示进度条
CACHE_DIR = os.path.join(BASE, "_cache")

# ---------------------------------------------------------------- 可调参数
SIZE = 260                  # 显示边长 px（改这里；缓存按尺寸另存，首次会烘 ~7 s）
FPS = 60.0                  # 60 = 本机屏幕刷新率，即上限（1920×1080 @ 60Hz）
FOLLOW_DPI = True           # True 时按系统缩放把 SIZE 放大
T_CLOSE = 0.70              # 睁 → 半睁（眼睑下落）秒数
T_OPEN = 0.55               # 半睁 → 睁（眼睑抬起）秒数，略快
AUTO_TIMEOUT_S = 2700       # state.json 里 working 的兜底超时（45 min）
FLICKER_GAP_WORK = (2.6, 5.2)   # 工作态闪烁间隔（官方只在 normal 闪，这里拉长也闪）
SPEAK = [                   # 双击时的一句（照官方语态：系统语言、短句、不喊口号）
    "收到。",
    "行动目标：待定。",
    "保持待机。",
    "已记录至日志。",
    "唤醒响应正常。",
    "需要我推测吗。",
]
SIZES = [200, 260, 320]

# ★★★ 通知卡字体档位（主人 2026-09-23：「C 和 D 都要」⇒ 做成菜单里可切换的档）。
#   段序**由细到粗**（中 → 粗 → 特粗）；每档自带**降级候选**：
#   先找 `pet-v2\fonts\`（**随包分发**的那份 ⇒ 任何机器字形一致），再找系统字体，
#   最后退 PIL 默认 ⇒ ★ **缺字体只会降级、不会崩**。
FONT_DIRS = (os.path.join(BASE, "fonts"), "C:/Windows/Fonts")
FONT_CHOICES = (
    # key,      段名,   右侧显示名,          候选 (字体文件, 可变字体实例名)
    # ★★ 候选顺序 = **仓库自带那份优先**：`pet-v2\fonts\NotoSansSC-VF.ttf` 一个文件
    #    覆盖「中 / 粗 / 特粗」三档 ⇒ **任何机器上字形完全一致**（主人 2026-09-23：
    #    "有需要的话字体一起打包吧"）。系统静态字体只当降级备份，最后退 PIL 默认。
    ("medium", u"中",   u"思源黑体 Medium",
     (("NotoSansSC-VF.ttf", "Medium"), ("Noto Sans SC Medium (TrueType).otf", None),
      ("msyh.ttc", None))),
    ("bold",   u"粗",   u"思源黑体 Bold",
     (("NotoSansSC-VF.ttf", "Bold"), ("Noto Sans SC Bold (TrueType).otf", None),
      ("msyhbd.ttc", None))),
    ("black",  u"特粗", u"思源黑体 Black",
     (("NotoSansSC-VF.ttf", "Black"), ("NotoSansSC-VF.ttf", "Bold"),
      ("msyhbd.ttc", None))),
)
FONT_KEYS = [c[0] for c in FONT_CHOICES]
FONT_DEFAULT = "bold"     # ★ 默认档：13 px 下既够厚又不像 Black 那样糊字（见 step108）


def font_meta(key):
    u"""key → `(段名, 右侧显示名, 候选表)`；不认识的 key 一律回默认档。"""
    for c in FONT_CHOICES:
        if c[0] == key:
            return c[1], c[2], c[3]
    for c in FONT_CHOICES:
        if c[0] == FONT_DEFAULT:
            return c[1], c[2], c[3]
    return u"粗", u"思源黑体 Bold", ()
# ★★ 2026-09-23：**正在后台预烘的尺寸**（px）。`_prebake_others()` 加、烘完删；
#   `_switch_size()` 见到它就只排队、不自己再烘一遍（否则两处同时写同一个 .npz）。
_PREBAKING = set()

# ---- 进度条的出现动画 + 开机动画结束后的"登场"（2026-09-22 主人定）----
BAR_REVEAL_S = 0.90         # ★ 主人 2026-09-23：改用 PPT 式淡入 ⇒ 0.90 s
BAR_REHIDE_S = 2.0          # 离开工作态超过这么久才算"这一轮结束"（短暂空档不重播动画）
#   ★★ 2026-09-23 主人定：「输出完成后应该过 10 秒左右自动切回正常态」。
#     判据 = `progress.json` 里的 `settle`（只有 `--reply` 走到 100% 才写这个键）。
DONE_SETTLE_S = 180.0       # 交付到 100% 后，**最多**挂这么久等主人开口（**兜底上限**）
                            #   ★★ 2026-09-23：14:2x 主人抓到「从你输出到**我看到**你的完整回复，
                            #    这段时间已经从工作态变回正常态了」⇒ 改 60 s；14:5x 又抓到
                            #    **60 s 也不够**（「你的回复卡了很久，导致 60 秒的工作态退出了」）。
                            #    根因：`--reply` 是我做的**最后一件事**，回复正文在那之后才生成、
                            #    才流到界面 ⇒ **按秒数计时必然在他读到之前到期**。
                            #   ⇒ 主判据改成**主人开口**（他发下一句指令就让位，见 tick 的 settle 段），
                            #    这个数退化成"他走开了也不会一直挂着"的兜底。
                            #   ★ 另一条复位路径：我下次 `--work` 写的进度不带 done ⇒ `_settled` 立刻复位。
PCT_EASE_RATE = 60.0        # ★★ 2026-09-23 主人：「90% 以内的进度条和百分比数字，应该按**最小 1% 的阶梯**做」。
                            #   上报值（`--item K P`）是**目标**，可能一下跳 45 个点；
                            #   **显示值**按 60%/s 追上去（60 fps 下正好 **1%/帧**）
                            #   ⇒ 条与数字一格一格往上走，不再整段闪现。
                            #   只**向上**缓动；目标变小（新一轮归零）⇒ 立刻吸附，不做倒退动画。
# ---- ★★ 2026-09-23 主人定的**收尾让位**的第二条路：主人一开始打字就退 ----
#   原话：「工作态的退出我觉得不用等 60 秒或者我下一轮输入，或者你能监控到输入窗口
#          我在打字，就退出工作态也许，**但仅限于 100% 跑完之后的**，
#          中间我插嘴的话，你是不能退出工作态的。」
#   ⇒ 判据写进 tick 的 settle 段（那一支只在 `prog_done and prog_settle` 时才走到），
#     所以"任务中途插嘴"**天然**不可能误退（那时 `prog_done` 为假，根本进不去）。
#   ★ 为什么不用「等第二轮指令」：那句回复我还要生成、渲染，他**读到**之前我这边早就完成了；
#     而他一旦开始敲字，就说明他已经看完了 ⇒ 这才是"他看见了"的第一手信号。
WB_EXE = "workbuddy.exe"    # 前台窗口属这个进程 ⇒ 主人正对着 WorkBuddy（2026-09-23 实测进程名）
TYPING_IDLE_MS = 1200       # 多久没有键鼠输入就算"没在打字"（打字节奏远快于 1.2 s）
MOVE_S = 1.60               # 开机动画结束后，主体从屏幕中心走到目标位的时长
MOVE_DELAY = 1.00           # ★ 主人 2026-09-22 23:1x：「在中心**待 1 秒**后，再去右下角」
APPEAR_S = 0.30             # 主体登场淡入（接住白光收束末帧的余辉，别"啪"地跳出来）

# ---- 通知卡配色：**三组**（干活 / 问答 / 闲聊）----
#   主人 2026-09-22 13:06：\"这里面的对话框干脆你给分三种颜色好了，
#   干活的一种，问答的一种，闲聊的一种。\"
#   分组规则见 `note_group()` —— 卡片只是换配色，文字一律来自 notify.json 的 text。
NOTE_GROUPS = {
    # bg = 卡片底色（带一点同色倾向，让三组的区别一眼可辨）
    # accent = 左侧强调竖线 ｜ dot = 头像圆点 ｜ line = 分隔线 ｜ head = 头部小字
    "work":   {"bg": "#12141c", "accent": "#3f6bd8", "dot": "#5b8cf0",
               "line": "#3f4a70", "head": "#8fa0c0"},
    "answer": {"bg": "#101b1b", "accent": "#1f9c8f", "dot": "#3fd0bd",
               "line": "#2c5a57", "head": "#7fc4bd"},
    "chat":   {"bg": "#151223", "accent": "#7a5cf0", "dot": "#a98cf5",
               "line": "#474172", "head": "#a79cd6"},
}


def note_group(kind):
    """把 notify.json 里的 kind 归到三个配色组。

    work / research / review / done / 未知 → \"work\"（干活：一并以冷蓝收尾）
    answer → \"answer\"（问答：青绿，理性）
    chat   → \"chat\"（闲聊：紫，放松）
    """
    k = (kind or "").strip().lower()
    if k == "answer":
        return "answer"
    if k == "chat":
        return "chat"
    return "work"


# ---- 菜单（滑块式，2026-09-22 主人要求）----
# 结构（行数会随"自动/手动"变化）：
#   fairy · 自动                    ← 标题
#   尺寸   [小][中][大]      260 px  ← 三档滑块 + 右侧标注实际像素
#   模式   [自动][手动]              ← 两档滑块
#   状态   [常态][工作态]            ← **手动时**才出现
#   自动   [跟随 WorkBuddy][60 秒循环] ← **自动时**才出现
#   归位（右下角） / 退出 fairy
MENU_TITLE_H = 22
MENU_ROW_H = 26
MENU_PAD = 10
SEG_H = 19                  # 滑块分段高度
SEG_PAD = 11                # 分段内左右留白
#   悬停提示（主人 2026-09-23："鼠标移上去解释作用，5 秒后淡出，纯文字、不要底色"）
TIP_HOLD_S = 5.0             # 显示满 5 秒
TIP_FADE_S = 0.45            # 然后淡出
TIP_PAD_X = 12               # 文字左右各留（按**画布**算，提示比菜单面板宽）
TIP_MAX_LINES = 2            # 最多两行 —— 再多会压到通知卡
TIP_LINE_H = 17
#   折行时**优先在这些字符之后断开**（宁可长短不齐，也不要把词劈开）
TIP_BREAK_CHARS = u"；;：:，,、。！？/| \u3000"
#   ★ **避头尾**：这些字符**不许落在行首**（中文排版规矩）。
#     没有它，折行会断成「开 = 你一发指令我就半睁（读会话记录）」+「；关 = …」——
#     第一版出图后看出来的。收尾括号（`）】」》`）已从"可断点"里去掉：在它们后面断最容易出事。
TIP_NO_START = u"；;：:，,、。！？)）】」》…·"  
AUTO_LOOP_S = 60.0          # 「60 秒循环」周期：自动在常态/工作态之间来回切（演示用）
# ★★ 2026-09-22 第五批：气泡（`_say`）**必须有长度上限**。
#   主人截到的「黑框文字」就是这个：`fairy_notify.py --done` 会把整条通知文案写进
#   `state.json` 的 `note`，主程序读到状态变化就 `_say(note)` ⇒ 长文本 ⇒ 旧的
#   `bw = tw + 26` 没有上限 ⇒ 矩形起点 `(S-bw)//2` 成负数 ⇒ 背景出画布、只剩文字（首字被裁）。
BUBBLE_MAX_CHARS = 22       # 气泡最多显示的字符数（超出截断加省略号）
BUBBLE_MAX_W_PAD = 20       # 气泡左右至少留这么多 px（绘制时的硬 clamp）


# ---------------------------------------------------------------- 黑匣子
def out(msg=""):
    """安全输出。★ pythonw 下 sys.stdout/stderr 都是 None：
    print() 遇到 None 会静默返回（安全），但 sys.stdout.flush() 会抛
    AttributeError 把整个进程崩掉 —— 本机实测窗口一闪就没。所有输出走这里。"""
    try:
        if sys.stdout is not None:
            sys.stdout.write(str(msg) + "\n")
            sys.stdout.flush()
    except Exception:
        pass


def log_err(where, exc=None):
    try:
        with open(ERRLOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s  %r\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), where, exc))
    except Exception:
        pass


def _hook(t, v, tb):
    import traceback
    log_err("UNCAUGHT", "".join(traceback.format_exception(t, v, tb)))


sys.excepthook = _hook

# ---------------------------------------------------------------- Win32
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WS_POPUP = 0x80000000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
HTTRANSPARENT = -1
HTCLIENT = 1
WM_DESTROY = 0x0002
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_NCHITTEST = 0x0084
SPI_GETWORKAREA = 0x0030


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE_T(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


# ★ 凡是传 HANDLE/指针的函数都必须声明签名，否则 HANDLE 被当 C int → OverflowError
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT),
                                       ctypes.POINTER(SIZE_T), wintypes.HDC,
                                       ctypes.POINTER(POINT), wintypes.DWORD,
                                       ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                   wintypes.DWORD]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDeviceCaps.restype = ctypes.c_int
gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ShowWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.SetCapture.argtypes = [wintypes.HWND]
user32.ReleaseCapture.restype = wintypes.BOOL
# ★ 全局按键状态：用来判"窗口之外点左键"（菜单常开时，窗口外那一下我们收不到任何消息）
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
VK_LBUTTON = 0x01
user32.PeekMessageW.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                         ctypes.c_void_p, wintypes.UINT]
user32.SetProcessDPIAware.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]


# ---- ★★ 2026-09-23「主人一开始打字就退工作态」用的几个 API ----
#   ★ 判据取**前台窗口的 exe 名**，不取窗口标题（标题会随文件名/文档名变），
#     也不比较 pid（WorkBuddy 是多进程架构，主窗口 pid 每次启动都不同）。
class LASTINPUTINFO(ctypes.Structure):
    u""""最近一次输入（键鼠任一）距现在多久" —— 系统级空闲时间。"""
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


kernel32.GetTickCount.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetLastInputInfo.restype = wintypes.BOOL
user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def smoothstep(u):
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


# ================================================================ 主程序
class FairyPet:
    def __init__(self, size=SIZE, bake_progress=None):
        self.dpi = self._dpi_scale()
        self.px = int(round(size * self.dpi)) if FOLLOW_DPI else int(size)
        self.px = max(120, min(640, self.px))
        # 渲染器（大小变化时重建；缓存命中则秒开）
        # ★★ 2026-09-23：`bake_progress` 直通 `FastMascot` —— 首次烘焙要 90~100 s，
        #   `__main__` 会先竖一块倒计时窗（`fairy_boot.Splash`），这里每报一个点就刷一帧倒计时。
        #   回调签名是 `(fraction, label)`（见 `fairy_layers._bake`）。
        self.R = FastMascot(size=self.px, ss=2, cache_dir=CACHE_DIR, verbose=True,
                            bake_progress=bake_progress)
        self.win = self.R.N      # ★ 窗口/画布边长；比 self.px 大（四周是辉光的发散空间）
        self.win = self.R.N      # ★ 窗口/画布边长；比 self.px 大（四周是辉光的发散空间）

        # ★ 启动预热：先渲染一帧**工作态**（纯 CPU —— 不建窗口、不上屏）。
        #   工作态要现建"数字字形图集"，不预热的话**第一次切工作态会卡一帧**
        #   （实测单帧间隔 73 ms，而它恰好发生在"主人刚发指令"那一刻 —— 最不该卡的时候）。
        #   代价：启动多花 ~60 ms；收益：切换永远顺滑。
        try:
            self.R.draw(t_ms=0.0, open_t=0.0, flicker_op=0.0)
        except Exception as e:
            log_err("prewarm", e)

        (self.x, self.y, self.topmost, self.sense,
         self.boot_anim, self.font_key) = self._load_pos()   # ★ 置顶/感知/开机动画/字体档 同文件
        # ★★ 菜单当前页："" = 主菜单，"display" = 二级「显示」页（尺寸 / 字体）。
        #   主人 2026-09-23：「正好给右键菜单分个级，大小、字体显示一类的设置，单独放二级菜单里」。
        self.menu_page = ""
        # ★ 会话活动侦测：主人一发指令 → 会话记录立刻落盘 → 我这边 0.5 s 内切工作态。
        #   不依赖我记得写 state.json（我漏过一次），这正是主人 2026-09-22 要的时机。
        self.act = FA.ActivityWatch()
        # ★★ 2026-09-23：pid → exe 全路径的缓存（收尾让位判据 `_typing_in_wb` 用；
        #   收尾期每 0.5 s 要问一次"前台窗口是谁"，不缓存就得反复 OpenProcess）
        self._exe_cache = {}
        self.t0 = time.perf_counter()

        # 状态机
        self.follow = True          # 跟随 state.json
        self.manual = None          # 手动指定的 state（follow=False 时生效）
        self.auto_state = "idle"
        self.cur_state = "idle"
        self.open_t = 1.0           # 1=睁眼 0=半睁
        self.trans = None           # (t_start, from, to)
        self.state_read_at = 0.0

        # 闪烁
        self.flickers = []
        self.next_flicker = time.perf_counter() + random.uniform(*M.FLICKER_GAP_MS) / 1000.0

        # 交互
        self.dragging = False
        self.drag_off = (0, 0)
        self.menu_open = False
        self.menu_at = 0.0
        self.hover = (-1, -1)       # (行下标, 分段下标)；-1 表示没悬停
        #   悬停提示：`_tip_row` 用来判"换行了没有"（换了才换文字、才重置 5 s 计时）
        self._tip_txt = None
        self._tip_row = (-1, -1)
        self._tip_t0 = 0.0
        self._tip_lines = []          # 折好行的文字（**缓存**：只在文字/画布变时重算）
        self._tip_lines_key = None
        self._lbtn_down = False     # 上一帧的全局左键状态（边沿检测，见 tick 里关菜单那段）
        self.drag_slider = None     # 正在拖动的滑块 key（"size"/"mode"/"auto"/"state"）
        self.bubble = None
        self.bubble_until = 0.0
        # ★ 任务完成通知（对话框式卡片）：我干完活写 notify.json，桌宠读一次就弹
        self.notify_text = None
        self.notify_t0 = 0.0
        self.notify_secs = 12.0
        self.notify_kind = "work"   # ★ 决定卡片配色（干活/问答/闲聊），来自 notify.json 的 kind
        self.notify_poll_at = 0.0
        self._note_up = False       # ★ 通知卡是否已判定「落在进度条上方」（粘性，见 _overlay）
        # ★ 工作态进度条（2026-09-22 主人拍板接进主程序）
        #   `progress.json` 由我上报 0~100；它**跟工作态一起出现/消失**（都挂在 open_t 的过渡上），
        #   所以不需要额外的显隐状态机。没有文件 ⇒ 按 0% 起（"进度条从工作开始就存在"）。
        self.prog = None            # 0..100 ｜ None = 还没上报过
        #   ★★ `progress.json` 里的 "done" 标记（= 我上次收工 `--done` 写的那个 100）。
        #   它不是"当前进度"，是**上一次工作的残留值** ⇒ 新任务开始时不能显示（见 `_bar_pct`）。
        self.prog_done = False
        self.prog_planned = True    # ★ planned=False = 我刚开工、还没拆好任务 ⇒ 这轮先不显示进度条
        #   ★★ 2026-09-23 三段式规则：`--reply` 到 100% 那条带 `settle: true`
        #      ⇒ ① 这个 100% **要显示**（不像 `--done` 的收工残留那样按 0 处理）；
        #         ② `DONE_SETTLE_S` 秒后**自己回常态**（主人："过 10 秒后切换到正常态"）。
        self.prog_settle = False        # progress.json 里的 settle 标记
        self._done_seen_at = None       # 首次看到"已交付"的时刻（计时用）
        self._settled = False           # 已交付超 DONE_SETTLE_S ⇒ 强制常态，直到下一轮开工
        self.prog_poll_at = 0.0
        self._prog_mtime = 0.0      # progress.json 的最后写入时间（判"是不是这一轮新上报的"）
        self._bar_epoch = 0.0       # 本轮工作开始时 progress.json 的版本（见 _bar_wanted 的 ⑤）
        self._bar_shown = False     # 这条进度条**本轮是否已经露过面**（决定要不要播出现动画）
        self._bar_rev_t0 = None     # 出现动画的起始时刻（perf_counter）
        self._bar_rev = None        # 出现动画进度（None = 已出现完 / 不该出现）
        self._pct_disp = 0.0        # ★ 显示值（0~100 浮点）—— 以 1%/帧 向 `_pct_tgt` 追
        self._pct_tgt = 0.0         # ★ 目标值（= 我上报的那个数）
        self._bar_want = False      # 此刻该不该画进度条（见 _bar_wanted）
        self._left_working_at = None   # 离开工作态的时刻（短暂离开不算换轮，见 tick）
        #   ★ 启动那一刻的 progress.json 版本：只有**启动之后新写下的**进度才算数
        #     （否则上一轮被打断留下的旧值会在新任务开头冒出来 + 白播一次出现动画）
        try:
            self._bar_epoch = os.path.getmtime(PROGRESS_FILE)
        except Exception:
            self._bar_epoch = 0.0
        # ★ 开机动画结束后"登场"用：位移计划 + 淡入
        #   `_move` = (t0, 起点x, 起点y, 终点x, 终点y, 时长, 延迟)
        self._move = None
        self._appear_t0 = 0.0
        self._appear_s = 0.0
        self._appear_k = 1.0
        self._bar_k = 0.0           # 进度条的不透明度：跟着工作态的过渡（= 1 − open_t）
        self.bar = FB.BarView(self.win, self.px / 160.0)
        self.bar.warm()
        self._bar_ready = True
        self.size_idx = SIZES.index(size) if size in SIZES else 1
        self.auto_mode = "follow"   # "follow" = 跟随 state.json；"loop60" = 60 秒循环（演示）
        self.auto_loop_at = time.perf_counter() + AUTO_LOOP_S   # 「60 秒循环」下一次切换
        self._md = ImageDraw.Draw(Image.new("RGB", (8, 8)))   # 只用来量文字宽度

        self._alpha = np.zeros((self.win, self.win), dtype=np.uint8)
        self.hwnd = None
        self._show_requested = False   # True 才做"窗口不见了就亮回来"的自愈（离线自检不打扰）
        self._dib = self._bits = self._hdc = None
        self._wndproc = None
        self.f13 = self._mkfont(13)      # ★ 别叫 self._font：会覆盖同名方法
        self.f12 = self._mkfont(12)
        self._tick_n = 0
        self._tick_ts = []          # 最近 1 秒内的 tick 时刻（算真实帧率）
        self._cost = 0.0            # tick 内部耗时的指数滑动平均（ms）
        self._period_max = 0.0      # 最差帧周期（ms）
        self._last_tick = 0.0
        self._boot_note = u"-"        # 给心跳用：开机动画状态
        # ★★ 2026-09-23：**开机动画期间不许把桌宠顶到前面**（见 `_force_show`）。
        #   两个窗口都是 TOPMOST，`ShowWindow(TOPMOST)` 会把桌宠顶到组最前
        #   ⇒ 动画演到一半、屏幕中央站着一只 Fairy（主人 09:47 抓到的"图层上下又出错"）。
        self._boot_guard = False
        # ★★ 2026-09-23：主人点了尺寸、但那一档还在**后台烘焙**里 ⇒ 先记下来，烘完再切
        #   （避免"右键调大小要卡一两分钟"，见 `_switch_size` / `_prebake_others`）。
        self._pending_size = None
        self._quit = False
        self._fail = 0              # 连续渲染失败计数（心跳里会带出来）

    # ------------------------------------------------------------ 工具
    @staticmethod
    def _dpi_scale():
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
        try:
            hdc = user32.GetDC(0)
            dpi = gdi32.GetDeviceCaps(hdc, 88)
            user32.ReleaseDC(0, hdc)
            return dpi / 96.0
        except Exception:
            return 1.0

    # ★★★ 通知卡字体：**档位表在模块顶部**（`FONT_CHOICES`），这里只负责"按当前档取字体"。
    #   主人 2026-09-23：「参照游戏里的字体改一下」（只借**字形字重**，**不借**那套灰蓝版式色），
    #   随后又说「**C 和 D 都要**」⇒ 做成右键菜单里可切的档（见 `_menu_rows` 的「显示」页）。
    #   ★★ **不把 Black 当默认**：13 px 下极粗字重会糊字 ——「圈」成实心块、「疆」墨迹覆盖
    #     0.944（Bold 只有 0.73），判据 `_work/step108_legibility.py`；但主人要就**给他可选**。
    _font_used = None          # 实际用上的那一档（写日志，换机器时可核对）

    def _mkfont(self, sz):
        u"""按 `self.font_key` 那一档的**候选优先级**取字体；全不行才退 PIL 默认。

        ★ 候选顺序 = 仓库自带 `pet-v2\\fonts\\` → 系统字体 → PIL 默认
          ⇒ **缺字体只会降级，绝不崩**（家里那台没装思源也能跑）。
        """
        _seg, name, cands = font_meta(self.font_key)
        for fname, var in cands:
            for d in FONT_DIRS:
                path = fname if os.path.isabs(fname) else os.path.join(d, fname)
                if not os.path.exists(path):
                    continue
                try:
                    f = ImageFont.truetype(path, sz)
                    if var:
                        f.set_variation_by_name(var)
                except Exception:
                    continue              # 文件坏了 / 不支持变量 ⇒ 试下一个候选
                tag = os.path.basename(path) + (u"@%s" % var if var else u"")
                if self._font_used != (name, tag):
                    self._font_used = (name, tag)
                    out(u"[font] 通知卡字体 = %s（%s）" % (name, tag))
                return f
        return ImageFont.load_default()

    def _apply_font(self):
        u"""按当前字体档重建 f12/f13 —— 切换后**立刻生效**（通知卡每帧现画，不用重烘）。"""
        self.f13 = self._mkfont(13)
        self.f12 = self._mkfont(12)

    def _work_area(self):
        u"""→ 主屏工作区（已躲开任务栏）。

        ★★ 2026-09-23 改成调 `fairy_screen`（**唯一真源**）：
          "默认落在主屏"是主人的要求，而 `SPI_GETWORKAREA` 拿到的**永远**是主屏工作区
          —— 这个语义写在 `fairy_screen.work_area()` 里，这里不再自己查一遍。
        """
        try:
            return SCR.work_area()
        except Exception:
            return 0, 0, 1920, 1080

    @staticmethod
    def _virtual_screen():
        """多屏：虚拟桌面范围（副屏在右时 x 会 > 主屏宽；在左时为负）"""
        try:
            return SCR.virtual()
        except Exception:
            return 0, 0, 1920, 1080

    def _load_pos(self):
        """→ (x, y, topmost, sense, boot, font)。

        ★ 置顶开关、活动感知开关、**开机是否播动画**、**通知卡字体档**都跟位置存在
          同一个文件里 ⇒ **重启后保持你的选择**。
        """
        try:
            d = json.load(open(POS_FILE, "r", encoding="utf-8"))
            x, y = int(d["x"]), int(d["y"])
            # ★★ 2026-09-23（主人："位置能**根据 1 块屏幕**去定位"）：
            #   存档位置可能来自"已经拔掉的那块屏"（外接显示器 / 投屏 / 换机器），
            #   也可能贴到屏幕外（旧版本没有夹取）⇒ 一律 `SCR.fit()` 夹进**主屏**，
            #   并按"可见内容"算 ⇒ 通知卡不会被屏幕切掉。夹动了就写一行日志，别静默。
            fx, fy = SCR.fit(x, y, self.win)
            if (fx, fy) != (x, y):
                out(u"[screen] 存档位置 (%d,%d) 超出主屏可见范围 ⇒ 夹到 (%d,%d)"
                    % (x, y, fx, fy))
            fk = str(d.get("font", FONT_DEFAULT))
            if fk not in FONT_KEYS:          # ★ 存档里是个不认识的 key ⇒ 回默认，别崩
                out(u"[font] 存档里的字体档 %r 不认识 ⇒ 用默认 %s" % (fk, FONT_DEFAULT))
                fk = FONT_DEFAULT
            return (fx, fy,
                    bool(d.get("topmost", True)), bool(d.get("sense", True)),
                    bool(d.get("boot", True)), fk)
        except Exception:
            pass
        # 没有存档 / 存档不可用 ⇒ 主屏右下角（`fairy_screen.home`，已含卡片夹取）
        hx, hy = self._home_xy()
        return hx, hy, True, True, True, FONT_DEFAULT

    def _save_pos(self):
        try:
            json.dump({"x": self.x, "y": self.y, "topmost": bool(self.topmost),
                       "sense": bool(self.sense), "boot": bool(self.boot_anim),
                       "font": self.font_key},
                      open(POS_FILE, "w", encoding="utf-8"))
        except Exception:
            pass

    def _apply_topmost(self):
        """把窗口插到（或移出）TOPMOST 层。

        ★ 只在扩展样式里去掉 WS_EX_TOPMOST 是**不生效**的 —— 已经置顶的窗口必须
          用 SetWindowPos 显式移动到 HWND_NOTOPMOST 位置。反过来开也一样。
        """
        if not self.hwnd:
            return
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        try:
            user32.SetWindowPos(
                self.hwnd,
                ctypes.c_void_p(-1 if self.topmost else -2),   # HWND_TOPMOST / HWND_NOTOPMOST
                0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        except Exception as e:
            log_err("topmost", e)

    def _cursor(self):
        p = POINT()
        user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    # ------------------------------------------------------------ 可见性
    def _home_xy(self):
        u"""默认位置：**主屏工作区右下角**（本体贴角，画布多出来的那圈空白抵掉）。

        ★ 2026-09-23 改成调 `fairy_screen.home()` —— 边距（20/16）与"用哪个矩形"都在那儿定义。
        """
        try:
            return SCR.home(self.win, self.px)
        except Exception:
            l, t, r, b = self._work_area()
            off = (self.win - self.px) // 2
            return (r - off - self.px - 20, b - off - self.px - 16)

    def _center_pos(self):
        u"""把**画布**摆在**主屏几何中心** —— 开机动画与"中央登场"都用它。

        ★★ 2026-09-23 主人要"真正的中心点" ⇒ 改成 `fairy_screen.center(窗口边长)`：
          ① 用**窗口（画布）尺寸**算，不是本体尺寸、更不是拍脑袋的屏幕中心；
          ② 用**主屏**，不是虚拟桌面（双屏时虚拟桌面中心落在两屏中间 —— 就是那个 bug）；
          ③ 用**几何中心**而不是工作区中心（后者会被任务栏抬高 20 px，
             和倒计时窗/动画窗口对不齐 —— 它们都按几何中心摆）。
        """
        try:
            return SCR.center(self.win)
        except Exception:
            l, t, r, b = 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
            return (int(round((l + r) / 2.0 - self.win / 2.0)),
                    int(round((t + b) / 2.0 - self.win / 2.0)))

    def _go_home(self, say=True):
        """回到默认位置（右下角）。本体贴角，画布多出来的那圈空白抵掉。"""
        self.x, self.y = self._home_xy()
        self._save_pos()
        if say:
            self._say("已归位", 1.6)

    def _force_show(self, why=""):
        """把窗口重新显示并顶到最上层。返回最终是否可见。

        用途：① 被 poke（主人又双击了一次启动器）时把窗口亮出来；
              ② 自检发现窗口不可见时自愈。

        ★ 记一次教训：我曾把"窗口看不见"归咎于 `sh.Run(cmd, 0, ...)` 带来的
          STARTUPINFO.wShowWindow = SW_HIDE，**实测证伪**（用 SW_HIDE 启动，
          ShowWindow(4) 照样生效、窗口可见）。真正让主人以为"双击没反应"的是
          `main()` 里那句静默 `sys.exit(0)`。别再把这两件事混为一谈。
        """
        if not self.hwnd:
            return False
        # ★★ 2026-09-23：**开机动画期间一概不碰窗口可见性**。
        #   两个窗口都带 WS_EX_TOPMOST，而 `ShowWindow` 一个 TOPMOST 窗口会把它
        #   顶到 TOPMOST 组**最前面**（这是 Windows 的行为，`SetWindowPos(hwnd, None…)` 改不了）；
        #   动画期间窗口本来就是亮的（由 `on_ready` 露的面），所以这里当成功返回即可。
        #   ⇒ 修掉了"开机动画刚起，屏幕中央就站着一只 Fairy"（主人："图层上下又出错了"）。
        if self._boot_guard:
            out(u"[boot] 上锁期间拦下一次 _force_show(%s) —— 动画在上，桌宠不许亮出来"
                % why)
            return True
        try:
            if not user32.IsWindowVisible(self.hwnd):
                user32.ShowWindow(self.hwnd, 4)          # SW_SHOWNOACTIVATE
            if not user32.IsWindowVisible(self.hwnd):
                user32.ShowWindow(self.hwnd, 5)          # SW_SHOW（退一步用会激活的那个）
            user32.SetWindowPos(self.hwnd, None, self.x, self.y, self.win, self.win,
                                0x0010 | 0x0040)         # SWP_NOACTIVATE | SWP_SHOWWINDOW
            return bool(user32.IsWindowVisible(self.hwnd))
        except Exception as e:
            log_err("force_show(%s)" % why, e)
            return False

    # ------------------------------------------------------------ 状态机
    def _read_state_json(self):
        """返回 (state, note)；文件不存在/坏了 → ('idle', '')"""
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            raw = str(d.get("state", "idle")).strip().lower()
            work = raw in ("working", "thinking", "busy", "work", "running", "1", "true")
            st = "working" if work else "idle"
            if st == "working":
                ttl = float(d.get("ttl", AUTO_TIMEOUT_S) or AUTO_TIMEOUT_S)
                age = time.time() - os.path.getmtime(STATE_FILE)
                if age > ttl:                       # ★ 兜底：超时自动回落到 idle
                    return "idle", "超时兜底（%.0f min 未更新）" % (age / 60.0)
            return st, str(d.get("note", ""))
        except FileNotFoundError:
            return "idle", ""
        except Exception as e:
            log_err("read_state", e)
            return "idle", ""

    def _read_progress(self):
        u"""读 `progress.json` → `(0..100 的 float 或 None, done, planned, mtime, settle)`。

    `settle=True` = `--reply` 交付收尾写下的那条 ⇒ 见 `DONE_SETTLE_S`。

        格式：`{"percent": 45, "ts": ..., "note": ..., "done": false, "planned": true}`
        （`value` / `progress` 也认）。与 `state.json` 不同：**不消费、不删**（进度是"当前值"）。

        `done=True`   = 这条是**上次收工**写下的 100%（见 `_bar_pct` 的说明）。
        `planned=False` = 我刚开工、**还没把任务拆好** ⇒ 这轮先不显示进度条
                          （主人 2026-09-22 定的时序："你收到后进入工作态（不显示进度条）→
                           保持此形态至你切分好任务 → 出现进度条"）。
        `mtime` = 文件最后修改时间。★ 用它比"这一轮工作开始时"的版本 ——
                  只有**新写下的**进度才算数，否则上一轮被打断留下的旧值会在新任务开头冒出来。
        """
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            mt = os.path.getmtime(PROGRESS_FILE)
            v = d.get("percent", d.get("value", d.get("progress")))
            if v is None:
                return None, False, True, mt, False
            return (float(max(0.0, min(100.0, float(v)))), bool(d.get("done")),
                    bool(d.get("planned", True)), mt, bool(d.get("settle")))
        except FileNotFoundError:
            return None, False, True, 0.0, False
        except Exception as e:
            log_err("read_progress", e)
            return None, False, True, 0.0, False

    def _bar_wanted(self):
        u"""此刻**该不该**画出进度条。

        ★★ 主人 2026-09-22 定的时序（原话）：
            「我输入信息并回车 → 你收到后进入工作态（**不显示进度条**）→ 保持此形态至
             **你切分好任务** → 出现进度条（工作态完全体）」

        ⇒ 判据只有一条：**这一轮工作里，我重新上报过进度**。四个条件缺一不可：
            ① 工作态（调用方保证）      ② `progress.json` 存在
            ③ 不是收工残留（`done`）    ④ 已拆分（`planned`）
            ⑤ `mtime` 比"本轮开始时"更新 —— 这条最要紧：
               上一轮被打断（没发 `--done`）会留下 `planned=true, done=false` 的旧进度，
               没有 ⑤ 的话新任务一开头就会顶着旧百分比 + 播一遍出现动画。
        """
        # ★★ 2026-09-23：`--reply` 到 100% 写的是 `done=True, settle=True`，那个 100%
        #   **是要显示出来的**（主人要"看它跑到 100%、卡片弹出、再回常态"）。
        #   原来 `prog_done ⇒ 不画` 只该管 `--done` 的收工残留。
        if self.prog is None or not self.prog_planned:
            return False
        if self.prog_done and not self.prog_settle:
            return False
        if self._prog_mtime <= self._bar_epoch:
            return False
        return True

    def _bar_pct(self):
        u"""进度条**该显示**的百分比。

        ★★ 2026-09-22 主人抓到的 bug（原话：「你变工作态有几秒，进度条是 100% 的，
          然后变回了常态，然后就到正常的 0% 了」）：
          `progress.json` **不消费不删**，而我每次收工都用 `--done` 把它置成 100
          ⇒ 主人一发新指令、感知让我进工作态 ⇒ 进度条立刻顶着**上次的 100%**，
            等我真正上报 `--progress 0` 才归零 —— 观感就是"先 100、再回到 0"。

        ⇒ `--done` 写下的那条带 `"done": true`；**工作态里看到它一律按 0 显示**。
          （收工那一刻进度条本来随 idle 淡出 ⇒ 这个 100 没有"看得见"的场合。）
          任何 `--progress N` 都会把 `done` 清掉 ⇒ 我上报的真实值原样显示。
        """
        if self.prog is None:
            return 0.0
        if self.prog_done and self.cur_state == "working" and not self.prog_settle:
            return 0.0                    # 上次收工的残留值（`--done`），不算数
        #   ★ `settle`（= `--reply` 的收尾）那个 100% 是**这一轮真正的终点**
        #     ⇒ 原样显示（主人 2026-09-23："进度条正好跑到 100%"）。
        return self.prog

    def _pct_shown(self):
        u"""**画在条上**的百分比 —— 显示值量化到 **1%**（台阶的最小单位）。

        ★★ 2026-09-23 主人：「90% 以内的进度条和百分比数字，应该按最小 1% 的阶梯做」。
          条与数字**都读这一个值** ⇒ 永远说的是同一个数（不会再出现"填充到 22.5、
          数字写 23"这种半格错位）。缓动见 `PCT_EASE_RATE`。
        """
        v = int(self._pct_disp)
        return 0 if v < 0 else (100 if v > 100 else v)

    # ---------------------------------------------------------------- 收尾让位
    def _pid_exe(self, pid):
        u"""pid → exe 全路径（**带缓存**：收尾期每 0.5 s 问一次，同一个 pid 只查一次）。

        ★ `OpenProcess` 失败不写缓存 —— 权限不足可能只是暂时的（对方也可能刚退出）。
        """
        hit = self._exe_cache.get(pid)
        if hit is not None:
            return hit
        h = kernel32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return u""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                self._exe_cache[pid] = buf.value
                return buf.value
            return u""
        except Exception:
            return u""
        finally:
            kernel32.CloseHandle(h)

    def _typing_in_wb(self):
        u"""主人**此刻正在 WorkBuddy 里打字/操作**吗？—— 收尾让位用的判据。

        ★★ 2026-09-23 主人定：原话 ——「工作态的退出我觉得不用等 60 秒或者我下一轮输入，
          或者你能监控到输入窗口我在打字，就退出工作态也许，**但仅限于 100% 跑完之后的**，
          中间我插嘴的话，你是不能退出工作态的。」
          ⇒ 本方法**只被收尾段调用**（`prog_done and prog_settle` 那一支）——
            所以"任务中途插嘴"不可能误退：那时 `prog_done` 为假，压根进不到那一支。

        两条判据缺一不可（都够便宜：跑在 0.5 s 一次的量级上，不在每帧里）：
          ① 前台窗口的 exe == `workbuddy.exe`  ⇒ 他正对着 WorkBuddy，而不是别的程序；
          ② 系统空闲时间 < `TYPING_IDLE_MS`     ⇒ 刚有输入（键/鼠任一）。
             用 `GetLastInputInfo` 而不是扫键盘：后者要轮询几百个虚拟键，而且抓不到
             "用滚轮翻我的回复"——那同样说明**他在看**。
        ★ 任何异常/取不到信息 ⇒ **一律 False**（宁可不退也不误退）：假阴性只是多挂一会儿
          （还有 `DONE_SETTLE_S` 兜底）；假阳性会把"他正看着我干活"变成"它睡了"。
        """
        try:
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = self._pid_exe(pid.value)
            if not exe or not exe.lower().endswith(WB_EXE):
                return False
            li = LASTINPUTINFO()
            li.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if not user32.GetLastInputInfo(ctypes.byref(li)):
                return False
            idle_ms = int(kernel32.GetTickCount()) - int(li.dwTime)
            # ★ GetTickCount 是 32 位、约 49.7 天回绕 ⇒ 出现负值时按"刚有输入"处理（保守）
            return idle_ms < TYPING_IDLE_MS
        except Exception as e:
            log_err("typing_probe", e)
            return False

    def _target_state(self, now=None):
        """当前该显示哪个状态。

        优先级（★ 2026-09-22 加第 3 条 —— 主人："从我发出指令、你开始思考时就切工作态"）：
          1. 手动模式：听主人的
          2. 自动·跟随：`state.json` 说 working ⇒ working
          3. 自动·跟随 && 感知开：**会话记录显示我正在干活** ⇒ working
             （主人在输入框一按回车，用户消息就落盘 ⇒ 比我写 state.json 更早）
          4. 其余 ⇒ idle
        """
        if not self.follow and self.manual:
            return self.manual
        # ★★ 2026-09-23 主人定：「输出完成后过 10 秒左右自动切回正常态」。
        #   这一条**压过感知** —— 交付已经完成，不该因为"我还在写文件"被拖住。
        #   `_settled` 由 tick 按 `progress.json` 的 `settle` 计时置位；下一轮开工自动复位。
        if self._settled:
            return "idle"
        if self.follow and self.auto_mode == "follow" and self.sense:
            if self.act.active():
                return "working"
        return self.auto_state

    def _set_state(self, st, now):
        if st == self.cur_state:
            return
        self.cur_state = st
        self.trans = (now, self.open_t, 0.0 if st == "working" else 1.0)

    def _open_t(self, now):
        if self.trans:
            t0, a, b = self.trans
            dur = T_CLOSE if b < a else T_OPEN
            u = (now - t0) / dur if dur > 0 else 1.0
            if u >= 1.0:
                self.open_t = b
                self.trans = None
            else:
                self.open_t = a + (b - a) * smoothstep(u)
        return self.open_t

    def _flicker_op(self, now):
        if self.cur_state == "working":
            gap = FLICKER_GAP_WORK
        else:
            gap = (M.FLICKER_GAP_MS[0] / 1000.0, M.FLICKER_GAP_MS[1] / 1000.0)
        if now >= self.next_flicker:
            dur = random.uniform(*M.FLICKER_DUR_MS) / 1000.0
            self.flickers.append([now, dur,
                                  random.uniform(*M.FLICKER_OP_1),
                                  random.uniform(*M.FLICKER_OP_MID),
                                  random.uniform(*M.FLICKER_OP_2)])
            self.next_flicker = now + dur + random.uniform(*gap)
        op = 0.0
        for ev in self.flickers:
            if ev[0] <= now < ev[0] + ev[1]:
                op = M.flicker_opacity((now - ev[0]) / ev[1], ev[2], ev[3], ev[4])
        self.flickers = [e for e in self.flickers if e[0] + e[1] > now - 0.5]
        return op

    def _say(self, text, secs=2.6):
        # ★ 先压成单行再限长（气泡是**一行**，多行会溢出）
        t = " ".join((text or "").split())
        if len(t) > BUBBLE_MAX_CHARS:
            t = t[:BUBBLE_MAX_CHARS] + "\u2026"
        self.bubble = t
        self.bubble_until = time.perf_counter() + secs

    # ------------------------------------------------------------ 自绘 UI（滑块式菜单）
    # 菜单页标识："" = 主菜单；PAGE_DISPLAY = 二级「显示」页（尺寸 / 字体）
    PAGE_DISPLAY = "display"
    #   二级「行为」页（模式 / 来源 / 感知 / 状态 / 置顶 / 开机）
    PAGE_BEHAVIOR = "behavior"

    def _menu_rows_main(self):
        """主菜单行。kind = title / nav / slider / button。

        ★★ 2026-09-23 主人：「**模式和自动这两个地方的逻辑要再捋一捋，感知也是**，
           而且也可以加二级菜单」⇒ 主菜单**只留导航与动作**，所有设置项分进两个二级页：
             · 「显示」= 尺寸 / 字体（外观）
             · 「行为」= 模式 / 来源 / 感知 / 状态 / 置顶 / 开机（状态怎么判断出来的）
           每个 `nav` 行右侧写**当前值摘要** ⇒ 不展开也知道现在什么状态。

        ★ 为什么"感知"必须从主菜单搬走：它原来固定显示，但 `_target_state()` 只在
          「自动 + 来源=WorkBuddy」时才看它 ⇒ 手动 / 演示循环下拨它**毫无反应**。
          现在它只出现在「行为」页里、且**仅在自动模式下出现**，从属关系一眼可见。
        """
        seg, fname, _ = font_meta(self.font_key)
        if self.follow:
            beh = (u"WorkBuddy" + (u" · 感知开" if self.sense else u" · 感知关")
                   if self.auto_mode == "follow" else u"演示循环")
        else:
            beh = u"手动 · " + (u"工作态" if self.manual == "working" else u"常态")
        return [
            {"kind": "title",
             "text": "Fairy · %s" % ("自动" if self.follow else "手动")},
            {"kind": "nav", "key": "display", "label": "显示",
             "extra": "%d px · %s" % (self.px, seg),
             "help": u"尺寸与字体；改完立刻生效"},
            {"kind": "nav", "key": "behavior", "label": "行为", "extra": beh,
             "help": u"我的状态怎么判断出来的；置顶 / 开机也在这儿"},
            {"kind": "button", "key": "boot", "text": "播放开机动画",
             "help": u"立刻播一遍；不影响「开机」那一项的配置"},
            {"kind": "button", "key": "home", "text": "归位（右下角）",
             "help": u"回到主屏右下角的默认位置"},
            {"kind": "button", "key": "quit", "text": "退出 Fairy",
             "help": u"关掉 Fairy（下次双击 .vbs 再启动）"},
        ]

    def _menu_rows_display(self):
        """二级「显示」页：**大小 / 字体**这类"显示设置"都收在这里。

        ★ 字体段名取"中 / 粗 / 特粗"（由细到粗），右侧写出**真实字体名**，
          免得切完不知道用的是哪个文件。
        """
        _seg, fname, _ = font_meta(self.font_key)
        segs = [font_meta(k)[0] for k in FONT_KEYS]
        idx = FONT_KEYS.index(self.font_key) if self.font_key in FONT_KEYS else 0
        return [
            {"kind": "title", "text": "显示"},
            {"kind": "slider", "key": "size", "label": "尺寸",
             "segs": ["小", "中", "大"], "idx": int(self.size_idx),
             "extra": "%d px" % self.px,
             "help": u"小 200 / 中 260 / 大 320 px"},
            {"kind": "slider", "key": "font", "label": "字体",
             "segs": segs, "idx": idx, "extra": fname,
             "help": u"通知卡字体：中 / 粗 / 特粗（思源黑体）"},
            {"kind": "button", "key": "back", "text": "‹ 返回"},
        ]

    def _menu_rows_behavior(self):
        r"""二级「行为」页：**状态是怎么判断出来的** + 其余开关。

        ★★ 2026-09-23 主人：「模式和自动这两个地方的逻辑要再捋一捋，感知也是」。
          捋出来的三层从属关系（＝ `_target_state()` 的真实优先级）：
            模式 = 自动 ⇒ 状态由**来源**决定：
                            ① 来源 = WorkBuddy ⇒ 读 `state.json`（我上报的）
                            ② **感知 = 开**     ⇒ 再叠加**会话记录**（我忘了上报也认）
                            ③ 来源 = 演示循环   ⇒ 本地 60 s 交替（纯演示，与 WorkBuddy 无关）
            模式 = 手动 ⇒ 直接听主人的「状态」档
        ⇒ 三处改动：
          ① 行标签「自动」→「**来源**」—— 原来标题、"模式：自动"、行标签**三处都叫自动**，
             读起来像绕口令；
          ② 「感知」**紧跟「来源」**、且**只在自动模式下出现**（它对手动/演示循环无效，
             固定显示会让人以为拨了有用）；
          ③ 段名「跟随 WorkBuddy / 60 秒循环」→「**WorkBuddy / 演示循环**」（短，
             且"演示"二字说清它非正式）。
        """
        rows = [{"kind": "title", "text": "行为"},
                {"kind": "slider", "key": "mode", "label": "模式",
                 "segs": ["自动", "手动"], "idx": 0 if self.follow else 1,
                 "help": u"自动 = Fairy 自己判断状态；手动 = 听你的"}]
        if self.follow:
            rows.append({"kind": "slider", "key": "auto", "label": "来源",
                         "segs": ["WorkBuddy", "演示循环"],
                         "idx": 0 if self.auto_mode == "follow" else 1,
                         "help": u"WorkBuddy = 读我上报的状态；"
                                 u"演示循环 = 本地 60 秒自己交替（演示用）"})
            # ★★ 判据必须是 **`auto_mode == "follow"`**，不能只看 `self.follow`：
            #   `_target_state()` 里感知的生效条件正是
            #   「follow && auto_mode=="follow" && sense」三连 ⇒ 演示循环下它**同样无效**。
            #   （第一版只判 follow，被自己的 T13 抓到 —— 同一个"死开关"换了个入口。）
            if self.auto_mode == "follow":
                rows.append({"kind": "slider", "key": "sense", "label": "感知",
                             "segs": ["开", "关"], "idx": 0 if self.sense else 1,
                             "help": u"开 = 你一发指令我就半睁（读会话记录）；"
                                     u"关 = 只认我上报的状态"})
        else:
            rows.append({"kind": "slider", "key": "state", "label": "状态",
                         "segs": ["常态", "工作态"],
                         "idx": 1 if (self.manual or self.cur_state) == "working" else 0,
                         "help": u"手动时的状态：常态 = 睁眼；工作态 = 半睁 + 01 数字"})
        rows.append({"kind": "slider", "key": "topmost", "label": "置顶",
                     "segs": ["开", "关"], "idx": 0 if self.topmost else 1,
                     "help": u"开 = 一直压在所有窗口上层；关 = 会被别的窗口盖住"})
        # ★ 2026-09-22 23:1x 主人要的：「右键菜单里增加开机播放动画，或者开机不播放动画的滑动选项」
        #   —— 只改**配置**，**下次启动生效**（立刻看请用主菜单的「播放开机动画」）。
        rows.append({"kind": "slider", "key": "bootmode", "label": "开机",
                     "segs": ["播放动画", "跳过"], "idx": 0 if self.boot_anim else 1,
                     # ★★ 主人点名要交代的：**这一项改完要重启**（只写配置、不重播）。
                     "help": u"★ 这项改完要「重启」才生效。\n"
                             u"播放动画 = 开机播一遍；跳过 = 直接落在右下角"})
        rows.append({"kind": "button", "key": "back", "text": "‹ 返回"})
        return rows

    def _menu_rows(self):
        """当前页的行（主菜单 / 「显示」/「行为」）。"""
        if self.menu_page == self.PAGE_DISPLAY:
            return self._menu_rows_display()
        if self.menu_page == self.PAGE_BEHAVIOR:
            return self._menu_rows_behavior()
        return self._menu_rows_main()

    def _slider_need(self, d, segs, extra=None):
        """一行滑块需要多宽（按文字实测宽 + 留白）"""
        segs = [s for s in segs if s]
        n = sum(int(d.textlength(s, font=self.f12)) + 2 * SEG_PAD for s in segs)
        n += 2 * max(0, len(segs) - 1) + 10
        if extra:
            n += int(d.textlength(extra, font=self.f12)) + 8
        return n

    def _menu_layout(self):
        """算菜单几何与每行/每段的命中区（菜单打开时才调，纯小算术）。

        ★ 段宽按**字体实测宽度**给 ⇒ 换字体、改文案都不会溢出（固定宽度会）。
        ★★ 宽度按**两页所有行**一起算 ⇒ 在主菜单与「显示」页之间来回切时**宽度不跳**
          （跟"另一种模式那一行也算进来"是同一个套路）。
        """
        rows = self._menu_rows()
        d = self._md
        #   ★ 主菜单 + 两个二级页**一起**参与宽度计算 ⇒ 来回切页时**宽度不跳**
        all_rows = (self._menu_rows_main() + self._menu_rows_display()
                    + self._menu_rows_behavior())
        lab_w = 0
        for r in all_rows:
            if r["kind"] in ("slider", "nav"):
                lab_w = max(lab_w, int(d.textlength(r["label"], font=self.f12)))
        x_slider = MENU_PAD + lab_w + 10
        need = 0
        for r in all_rows:
            if r["kind"] == "slider":
                need = max(need, self._slider_need(d, r["segs"], r.get("extra")))
            elif r["kind"] == "nav":
                need = max(need, self._slider_need(d, [], r.get("extra")) + 12)
        # ★ 把**另一种模式**的那一行也算进来 ⇒ 菜单宽度恒定，切自动/手动时不会横向跳
        for cand in (["WorkBuddy", "演示循环"], ["常态", "工作态"]):
            need = max(need, self._slider_need(d, cand, None))
        w = min(x_slider + need + MENU_PAD, max(200, self.win - 12))
        y0 = max(6, int(self.win * 0.035))
        # ★★ 行高**自适应**（2026-09-22 23:3x 加）：多了「开机」一行之后，200 px 档
        #   （画布 284）塞不下 10 行 × 26 ⇒ 菜单会顶出画布底（实测底部 285 > 284）。
        #   这里按**可用高度反算行高**，下限 20 px（再矮字就贴在一起了）。
        #   260 / 320 档算下来仍是 26 ⇒ 那两档的样子**一点没变**。
        row_h = MENU_ROW_H
        avail = self.win - y0 - 6
        if MENU_PAD * 2 + MENU_TITLE_H + row_h * (len(rows) - 1) > avail:
            spare = avail - MENU_PAD * 2 - MENU_TITLE_H
            row_h = max(20, spare // max(1, len(rows) - 1))
        h = MENU_PAD * 2 + MENU_TITLE_H + row_h * (len(rows) - 1)
        L = {"x0": (self.win - w) // 2, "y0": y0, "w": w, "h": h, "rows": [],
             "page": self.menu_page}
        y = y0 + MENU_PAD
        for r in rows:
            rh = MENU_TITLE_H if r["kind"] == "title" else row_h
            it = dict(r)
            it["y"], it["rh"] = y, rh
            if r["kind"] == "slider":
                sx = L["x0"] + x_slider
                segs = []
                for k, s in enumerate(r["segs"]):
                    sw = int(d.textlength(s, font=self.f12)) + 2 * SEG_PAD
                    segs.append((sx, sx + sw, k))
                    sx += sw + 2
                it["segs"] = segs
                it["texts"] = list(r["segs"])
                it["extra_x"] = sx + 6
            else:
                it["segs"] = None
            L["rows"].append(it)
            y += rh
        return L

    def _hit_menu(self, lx, ly, L=None):
        """(行下标, 分段下标)；分段下标 -1 = 落在滑块行但不在任何分段上；不在菜单里 = (-1,-1)"""
        if L is None:
            L = self._menu_layout()
        if not (L["x0"] <= lx <= L["x0"] + L["w"] and L["y0"] <= ly <= L["y0"] + L["h"]):
            return -1, -1
        for i, it in enumerate(L["rows"]):
            if it["y"] <= ly < it["y"] + it["rh"]:
                if it["segs"]:
                    for (a, b, k) in it["segs"]:
                        # ★ 把 2 px 的段间缝隙归给**左段**：否则按住拖动经过缝隙时会"失灵"一下
                        if a <= lx < b + 2:
                            return i, k
                return i, -1
        return -1, -1

    def _tip_update(self, L, hover, now):
        r"""悬停换行 ⇒ 换提示文字并**重置计时**（同一行停着不动 ⇒ 满 5 s 就淡出）。"""
        if hover == self._tip_row:
            return
        self._tip_row = hover
        txt = u""
        if hover[0] >= 0:
            txt = L["rows"][hover[0]].get("help") or u""
        self._tip_txt = txt or None
        self._tip_t0 = now

    def _tip_span(self, now):
        r"""→ `(文字, 不透明度)`；没提示或已淡完 ⇒ None。"""
        if not self._tip_txt:
            return None
        u = now - self._tip_t0
        if u >= TIP_HOLD_S + TIP_FADE_S:
            return None
        if u <= TIP_HOLD_S:
            k = 1.0
        else:
            k = 1.0 - (u - TIP_HOLD_S) / TIP_FADE_S
        return (self._tip_txt, max(0.0, min(1.0, k)))

    def _wrap_tip(self, d, txt, maxw):
        r"""把提示文字折成不超过 `TIP_MAX_LINES` 行。

        ★★ **先均衡、再贪心**：逐字贪心折行会把「…只认我上报的状」+「态」这种
          **孤字**留在末行（第一版出图时看到的）。所以先扫一遍所有二分点，
          取「两行较长那条最短」的那个点（= 长短最接近）；两行仍放不下才退回贪心。
        ★ `\n` 是**强制换行**（「开机」那条靠它把"要重启"单独顶到第一行）。
        """
        lines = []
        for seg in txt.split(u"\n"):
            if not seg:
                continue
            if d.textlength(seg, font=self.f13) <= maxw:
                lines.append(seg)
                continue
            # ① 挑出**放得下**的二分点：② 标点处优先 ③ 同类里长短最接近
            cands = []
            for k in range(1, len(seg)):
                if seg[k] in TIP_NO_START:      # ★ 避头尾：标点不能落在行首
                    continue
                wa = d.textlength(seg[:k], font=self.f13)
                wb = d.textlength(seg[k:], font=self.f13)
                if wa <= maxw and wb <= maxw:
                    bond = 0 if seg[k - 1] in TIP_BREAK_CHARS else 1
                    cands.append((bond, abs(wa - wb), k))
            if cands:
                cands.sort()
                k = cands[0][2]
                lines.extend([seg[:k], seg[k:]])
                continue
            cur = u""                      # 均衡也放不下 ⇒ 逐字贪心
            for ch in seg:
                if cur and d.textlength(cur + ch, font=self.f13) > maxw:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            if cur:
                lines.append(cur)
        # ★ 避头尾兜底：贪心路径也可能留下**行首标点** ⇒ 挪回上一行行尾
        #   （上一行放得下才挪，放不下就留着 —— 宁可难看一点也不能超宽出画布）
        for i in range(1, len(lines)):
            s = lines[i]
            if (s and s[0] in TIP_NO_START and lines[i - 1]
                    and d.textlength(lines[i - 1] + s[0], font=self.f13) <= maxw):
                lines[i - 1] += s[0]
                lines[i] = s[1:]
        return lines[:TIP_MAX_LINES]

    def _draw_tip(self, d, S, L, txt, k):
        r"""画悬停提示：**纯文字、无底色块**，位于菜单面板**正下方**。

        ★ 主人 2026-09-23：「纯文字，不要底色」⇒ 不画卡片底，只加一圈**深色描边**
          让它在宠物辉光/浅色桌面上都能读（描边≠底色）。嫌重就把 `stroke_width` 调 0。
        ★ 位置：面板 12~210、提示 218~252、通知卡顶部约 252 ⇒ **刚好不打架**
          （万一同时弹卡，卡片后画会盖住提示，属可接受）。
        """
        a = int(round(max(0.0, min(1.0, k)) * 255.0))
        if a < 8 or not txt:
            return
        maxw = S - 2 * TIP_PAD_X
        # ★ 折行只在**文字或画布变了**时算一次（原来每帧重算 ⇒ 30+ 次 textlength/帧）
        key = (txt, S)
        if self._tip_lines_key != key:
            self._tip_lines = self._wrap_tip(d, txt, maxw)
            self._tip_lines_key = key
        lines = self._tip_lines
        y = L["y0"] + L["h"] + 8
        if y + TIP_LINE_H * len(lines) > S - 6:
            y = max(L["y0"] + L["h"] + 4, S - 6 - TIP_LINE_H * len(lines))
        for i, ln in enumerate(lines):
            w = d.textlength(ln, font=self.f13)
            d.text(((S - w) / 2.0, y + i * TIP_LINE_H), ln,
                   fill=bgr("#e2e9f7") + (a,), font=self.f13,
                   stroke_width=1, stroke_fill=bgr("#080a10") + (a,))

    def _menu_click(self, lx, ly):
        r"""菜单里的一次左键点击（`lx/ly` = **窗口内**像素坐标）。

        ★★ 单独抽出来是为了**可测**（2026-09-23）：原来判定与派发全塞在
          `_on_message` 里、取位置又用真实光标 `GetCursorPos()` ⇒ 脚本无法喂坐标，
          只能"信代码"。而这次要修的恰恰是**派发分支漏了 `nav`**
          （「显示 ▸」点不开）⇒ 必须能把每个入口在离屏状态下点一遍。
          `_on_message` 现在只负责取位置，判定/派发都在这里。
        返回 True = 这一下点击被菜单吃掉了。
        """
        i, k = self._hit_menu(lx, ly)
        if i < 0:
            self.menu_open = False           # 点在菜单外 ⇒ 关掉
            return True
        it = self._menu_layout()["rows"][i]
        if it["kind"] == "slider":
            # ★ 按住后继续左右拖也能改（"点击滑动"）⇒ 必须 SetCapture，
            #   否则指针一移出窗口就收不到 WM_MOUSEMOVE。
            self.drag_slider = it["key"]
            if self.hwnd:
                user32.SetCapture(self.hwnd)
            if k >= 0:
                self._set_slider(it["key"], k)
        elif it["kind"] in ("button", "nav"):
            # ★★ 2026-09-23 修：`nav`（二级页入口）原来**没有分支** ——
            #   绘制正常、命中测试也正常，但**点下去什么都不做**
            #   ⇒ 主人报的「上面调尺寸字体的二级菜单点不开」。
            self._do_menu_key(it["key"])
        return True

    # ------------------------------------------------------------ 任务完成通知
    # 机制一句话：**我（fairy）干完活写一个 notify.json，桌宠读一次就弹一张卡片。**
    # 这就是"你在 Agent 里干活 → 干完 → 桌宠弹框告诉你"的全部链路，不涉及任何网络/权限。
    def _read_notify(self):
        """读 notify.json。★ 消费即删（含坏文件）—— 通知是一次性的，重启不会重弹。

        返回 `(文本, 停留秒数, kind)`；kind 决定卡片配色（见 `note_group()`）。
        """
        if not os.path.exists(NOTIFY_FILE):
            return None
        txt, secs, kind = "", 12.0, "work"
        try:
            with open(NOTIFY_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            txt = str(d.get("text", "")).strip()
            secs = max(3.0, float(d.get("secs", 12.0)))
            kind = str(d.get("kind", "") or "work")
        except Exception as e:
            log_err("read_notify", e)
        try:
            os.remove(NOTIFY_FILE)      # ★ 坏文件也要删：否则每 0.5 s 报一次错、刷满日志
        except Exception:
            pass
        return (txt, secs, kind) if txt else None

    def _note_span(self, now):
        """返回 (文本, 不透明度, kind)；没通知或已过期 → None。淡入 0.25 s、淡出 0.5 s。"""
        if not self.notify_text:
            return None
        el = now - self.notify_t0
        if el >= self.notify_secs:
            self.notify_text = None
            return None
        if el < 0.25:
            op = el / 0.25
        elif el > self.notify_secs - 0.5:
            op = max(0.0, (self.notify_secs - el) / 0.5)
        else:
            op = 1.0
        return self.notify_text, op, self.notify_kind

    def _wrap_text(self, text, font, maxw):
        """按**像素宽度**折行（中文没空格，只能逐字累加）。最多 3 行。"""
        d = self._md
        out, cur = [], ""
        for ch in text:
            if ch == "\n":
                out.append(cur)
                cur = ""
                continue
            if cur and d.textlength(cur + ch, font=font) > maxw:
                out.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            out.append(cur)
        return out[:3] or [""]

    def _draw_note(self, d, S, txt, op, kind=None, bottom=None):
        """通知卡（对话框式）。★ 独立成方法，不塞进 `_overlay` —— 那个已经管菜单+气泡了。

        ★ 配色按 `kind` 分**三组**（干活 / 问答 / 闲聊）—— 主人 2026-09-22 的要求。

        `bottom`：卡片底边落在哪。默认画布底部（`S - m`）；
        **工作态有进度条时传进度条的顶边**（主人定的布局：**卡在上、条在下**，卡片优先）。
        """
        g = NOTE_GROUPS[note_group(kind)]
        A = int(round(240 * op))        # 卡片底
        T = int(round(255 * op))        # 字与描边
        m, pad, lh, head = 16, 14, 21, 20
        lines = self._wrap_text(txt, self.f13, S - 2 * m - 2 * pad)
        bh = pad + head + len(lines) * lh + 6
        bw = S - 2 * m
        by = (S - m if bottom is None else int(bottom)) - bh
        by = max(m, min(by, S - bh - m))        # 别顶出画布
        bx = m
        d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=11,
                            fill=bgr(g["bg"]) + (A,), outline=bgr(g["line"]) + (T,))
        d.rounded_rectangle([bx + 3, by + 9, bx + 6, by + bh - 9], radius=2,
                            fill=bgr(g["accent"]) + (T,))       # 左侧强调竖线（分组的标志色）
        d.ellipse([bx + pad, by + 10, bx + pad + 8, by + 18], fill=bgr(g["dot"]) + (T,))
        d.text((bx + pad + 14, by + 7), "Fairy", fill=bgr(g["head"]) + (T,), font=self.f12)
        d.line([(bx + pad, by + head + 5), (bx + bw - pad, by + head + 5)],
               fill=bgr(g["line"]) + (T,))
        for i, ln in enumerate(lines):
            d.text((bx + pad, by + head + 11 + i * lh), ln,
                   fill=bgr("#e9eef8") + (T,), font=self.f13)

    def _overlay(self, panel, bubble, note=None):
        """生成自绘 UI 覆盖层（PIL RGBA）。

        ★ 不要把它 paste 进画布 —— 画布的 alpha 是用 `max(R,G,B)` 反推的，
          深色面板的最大通道只有 28 ⇒ alpha 0.11 ⇒ 面板几乎全透明，
          浅色桌面上会变成一片惨白（实测就是这样被主人抓到的）。
          正确做法：把它交给 `FastMascot.to_bgra(ov=...)` 做真正的 alpha 合成。
        颜色统一走 `bgr()`（画布是 BGR 通道序）。
        """
        if not panel and not bubble and not note:
            return None
        S = self.win
        ov = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        if panel:
            L = panel["layout"]
            x0, y0, w, h = L["x0"], L["y0"], L["w"], L["h"]
            d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=10,
                                fill=bgr("#12141c") + (238,),
                                outline=bgr("#394060") + (255,))
            hv = panel["hover"]
            for i, it in enumerate(L["rows"]):
                ry, rh = it["y"], it["rh"]
                if it["kind"] == "title":
                    d.text((x0 + MENU_PAD, ry + 4), it["text"],
                           fill=bgr("#8fa0c0") + (255,), font=self.f12)
                    d.line([(x0 + 6, ry + rh - 3), (x0 + w - 6, ry + rh - 3)],
                           fill=bgr("#333a52") + (255,))
                    continue
                if it["kind"] == "button":
                    if hv[0] == i:
                        d.rounded_rectangle([x0 + 4, ry, x0 + w - 4, ry + rh - 2], radius=6,
                                            fill=bgr("#252c42") + (255,))
                    d.text((x0 + MENU_PAD + 2, ry + 4), it["text"],
                           fill=bgr("#e6ebf5") + (255,), font=self.f13)
                    continue
                if it["kind"] == "nav":
                    # ★ 二级页入口（主人 2026-09-23）：整行可点，右侧写当前值 + 一个 › 指示
                    if hv[0] == i:
                        d.rounded_rectangle([x0 + 4, ry, x0 + w - 4, ry + rh - 2], radius=6,
                                            fill=bgr("#252c42") + (255,))
                    d.text((x0 + MENU_PAD, ry + 5), it["label"],
                           fill=bgr("#c8d3e8") + (255,), font=self.f12)
                    _arrow = u"\u203a"
                    _aw = int(d.textlength(_arrow, font=self.f13))
                    _ex = it.get("extra") or ""
                    _ew = int(d.textlength(_ex, font=self.f12))
                    d.text((x0 + w - MENU_PAD - _aw - 10 - _ew, ry + 5), _ex,
                           fill=bgr("#9aa8c4") + (255,), font=self.f12)
                    d.text((x0 + w - MENU_PAD - _aw, ry + 3), _arrow,
                           fill=bgr("#7f8db0") + (255,), font=self.f13)
                    continue
                # ---- 滑块行：左侧标签 + 分段（选中=亮蓝，悬停=微亮，其余=暗）----
                d.text((x0 + MENU_PAD, ry + 5), it["label"],
                       fill=bgr("#9aa8c4") + (255,), font=self.f12)
                ty = ry + (rh - SEG_H) // 2
                for (a, b, k) in it["segs"]:
                    on = (k == it["idx"])
                    hot = (hv[0] == i and hv[1] == k)
                    if on:
                        fill, line, tc = "#3f6bd8", "#7d97ee", "#ffffff"
                    elif hot:
                        fill, line, tc = "#242c44", "#54617f", "#e6ebf5"
                    else:
                        fill, line, tc = "#1a1f2e", "#2c3350", "#9aa8c4"
                    d.rounded_rectangle([a, ty, b, ty + SEG_H], radius=5,
                                        fill=bgr(fill) + (255,),
                                        outline=bgr(line) + (255,))
                    txt = it["texts"][k]
                    tw = d.textlength(txt, font=self.f12)
                    d.text((a + (b - a - tw) / 2.0, ty + 3), txt,
                           fill=bgr(tc) + (255,), font=self.f12)
                if it.get("extra"):
                    d.text((it["extra_x"], ry + 5), it["extra"],
                           fill=bgr("#c8d4ea") + (255,), font=self.f12)
            # ★ 悬停提示（纯文字、无底色）—— 见 `_draw_tip`
            tip = panel.get("tip")
            if tip:
                self._draw_tip(d, S, L, tip[0], tip[1])
        if note:
            # ★★ 判据要用**状态**，不能用进度条的淡入进度（2026-09-22 主人截图抓到）：
            #   原来写的是 `self._bar_k > 0.02` ⇒ 重启后头 2~3 帧 `_bar_k≈0` ⇒ `bottom=None`
            #   ⇒ 卡片画在**画布最底**（正好压在进度条的位置上），随后条淡入、
            #   `composite()` 又把条叠在卡片之上 ⇒ 就是主人看到的"对话框在进度条下面"。
            #   改成看**状态**而不是进度条的淡入进度。
            # ★★ 2026-09-22 第二轮加固（主人又抓到「重启后卡片在进度条下面闪一下」）—— 四重保险：
            #   ① `cur_state`（进入 working 那一帧就翻转）② `auto_state`（状态刚读完、过渡还没起）
            #   ③ `prog is not None`（progress.json 只在干活时存在，是**最早**可用的信号）
            #   ④ **粘性**：同一张卡片一旦落到条上方，就不再落回底部（避免中途跳一下）。
            # ★★ 2026-09-22 第三轮改判据（主人定了新时序之后）：**只看进度条在不在**。
            #   旧判据里有 `self.prog is not None` = "progress.json 存在"，
            #   但新时序下"文件存在"≠"条出现"（没拆好任务时文件也可能在，条却不画）
            #   ⇒ 用 `_bar_want` 一个真值就够（它已含工作态、done、planned、时间戳四条）。
            up = bool(self._bar_want)
            self._note_up = self._note_up or up
            bottom = (self.bar.card_bottom()
                      if (self.bar is not None and self._note_up) else None)
            self._draw_note(d, S, note[0], note[1], note[2] if len(note) > 2 else None, bottom)
        if bubble:
            # ★★ 硬 clamp（2026-09-22 第五批）：**矩形和文字都要限**。
            #   只限矩形是不够的 —— 文字按原文画出去会被画布裁掉，
            #   就是主人截到的「黑框文字贴在边缘、首字还没了」。所以这里按**像素宽度逐字回退**。
            padb = 13
            maxw = S - BUBBLE_MAX_W_PAD - 2 * padb
            txt = bubble
            while txt and d.textlength(txt, font=self.f13) > maxw:
                txt = txt[:-1]
            if txt != bubble and len(txt) >= 2:
                txt = txt[:-1] + "\u2026"
            tw = d.textlength(txt, font=self.f13)
            bw = min(int(tw) + 26, S - BUBBLE_MAX_W_PAD)
            bh = 28
            bx = max(6, (S - bw) // 2)
            by = S - bh - int(S * 0.06)
            d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=9,
                                fill=bgr("#12141c") + (238,),
                                outline=bgr("#394060") + (255,))
            d.text((bx + 13, by + 6), txt, fill=bgr("#e6ebf5") + (255,), font=self.f13)
        return ov

    # ------------------------------------------------------------ 帧
    def _init_dib(self):
        hdc_screen = user32.GetDC(0)
        self._hdc = gdi32.CreateCompatibleDC(hdc_screen)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.win
        bmi.bmiHeader.biHeight = -self.win         # 负数 = 顶向下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0            # BI_RGB
        bits = ctypes.c_void_p()
        self._dib = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0,
                                           ctypes.byref(bits), None, 0)
        self._bits = bits
        gdi32.SelectObject(self._hdc, self._dib)
        user32.ReleaseDC(0, hdc_screen)

    def _blit(self, bgra, alpha):
        if self._bits is None or not self.hwnd:      # 防御：没建窗口时（离线预览/测试）直接跳过
            self._alpha = alpha
            return
        ctypes.memmove(self._bits, bgra.tobytes(), self.win * self.win * 4)
        self._alpha = alpha
        hdc_screen = user32.GetDC(0)
        pt_dst = POINT(self.x, self.y)
        sz = SIZE_T(self.win, self.win)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(self.hwnd, hdc_screen, ctypes.byref(pt_dst),
                                   ctypes.byref(sz), self._hdc, ctypes.byref(pt_src),
                                   0, ctypes.byref(blend), ULW_ALPHA)
        user32.ReleaseDC(0, hdc_screen)

    def _write_beat(self):
        u"""写心跳。★ 从 tick 里抽出来是为了**开机动画期间也能续命**（那 12 s 不跑 tick）。"""
        try:
            # ★ 心跳必须带上 pid / hwnd / vis：否则"进程活着、窗口却看不见"这种故障
            #   外部完全无从判断（主人报"双击没反应"时，就吃了这个亏）。
            with open(BEAT, "w", encoding="utf-8") as f:
                f.write("tick=%d  %s  pid=%d  hwnd=0x%X  vis=%d  pos=(%d,%d)  px=%d  "
                        "win=%d  state=%s  open_t=%.2f  fps=%.1f  cost=%.2fms  "
                        "err=%d  最差帧=%.1fms  follow=%s  sense=%s  act=%s  "
                        "prog=%s  bar=%s  boot=%s  typing=%d  dpi=%.2f\n"
                        % (self._tick_n, time.strftime("%H:%M:%S"), os.getpid(),
                           self.hwnd or 0,
                           int(bool(self.hwnd)
                               and user32.IsWindowVisible(self.hwnd)),
                           self.x, self.y, self.px, self.win, self.cur_state,
                           self.open_t, float(len(self._tick_ts)), self._cost,
                           self._fail, self._period_max, self.follow,
                           int(bool(self.sense)), self.act.info(),
                           ("-" if self.prog is None else "%.0f" % self.prog),
                           ("show" if self._bar_want else
                            ("wait" if self.cur_state == "working" else "off")),
                           self._boot_note,
                           # ★ 2026-09-23：只在**收尾期**才探（无收尾时 `_done_seen_at` 是 None
                           #   ⇒ 直接 0、一个 Win32 调用都不发）。这样以后能查证
                            #   "让位到底是打字触发的、还是 180 s 兜底触发的"。
                           1 if (self._done_seen_at and self._typing_in_wb()) else 0,
                           # ★ 2026-09-23：心跳带 DPI —— 高分屏上"桌宠偏小/首烘很慢"
                           #   这类问题，事后只看心跳就能知道当时系统缩放是多少。
                           self.dpi))
        except Exception:
            pass

    def play_boot(self, countdown=None, why="", then_home=False, keep_visible=False):
        u"""播一遍开机动画（**阻塞**到播完）；播完让主体**在屏幕中央登场**，再走到目标位。

        ★ 主人 2026-09-22 定：「将开机动画**固定在屏幕中央**，结束后**出现 fairy 主体**，
          然后**自动移动到默认位置：屏幕右下角**」。
        ★ 启动路径（`run()` 的 `boot=` 分支）：**桌宠窗口已经建好并显示在动画窗口下面**，
          这里只管播动画；白光擦除时露出的就是**真窗口本身**（主人 2026-09-23 定的方案）。
          ⇒ 所以启动路径要传 `keep_visible=True`，**不能把窗口藏起来**。
        ★ 重播路径（右键菜单）：桌宠已经在屏上 ⇒ 先 `SW_HIDE` 藏起来、居中播，播完在中央亮回来，
          再走回**播放前的位置**（尊重主人手动摆好的位置，而不是硬拽回右下角）。
        ★ 失败绝不影响桌宠：整段包在 try 里，出错就静默跳过（动画只是锦上添花）。
        """
        if BOOT is None:
            return False
        try:
            vl, vt, vr, vb = self._virtual_screen()
            hidden = False
            if (not keep_visible) and self.hwnd and user32.IsWindowVisible(self.hwnd):
                user32.ShowWindow(self.hwnd, 0)                  # SW_HIDE
                hidden = True
            here = (self.x, self.y)                              # 播之前在哪（重播时走回去）
            cx, cy = self._center_pos()                          # ★ 屏幕中央
            self._boot_note = u"播中(%s)" % (why or "?")
            out(u"[boot] %s 开始（倒计时 %.1f s，窗口 %dx%d，居中 @(%d,%d)）"
                % (why or "?", countdown if countdown else BOOT.T_CD,
                   self.win, self.px, cx, cy))
            # ★★ 2026-09-23：**不再合成"假主体"**（主人定的方案）。
            #   原来这里现场渲一帧主体，交给 `over_body()` 贴到白光下面 —— 那是**动画窗口里的一张图**：
            #   动画窗口一关它就没了，而真桌宠窗口那时才刚在中央登场
            #   ⇒ 屏幕中央会多留一瞬间的定格帧。
            #   主人原话：「中央 fairy 出现到走到右下角的过程中，平面中心还是残留了一张 fairy
            #   的定格帧类似的图……动画收尾和本体露出之间，夹了一层平面中心这个单独的图像」。
            #   ⇒ 现在：桌宠窗口**先建好、就待在动画窗口下面**，白光擦除露出的是**真窗口本身**；
            #     动画窗口一关，底下早就站好了，没有任何夹层。
            def _arms_up():
                u"""动画窗口就绪 ⇒ 先让桌宠露面，**随即上锁**。

                ★★ 2026-09-23：露面那一下 `ShowWindow` 会把桌宠顶到 TOPMOST 组最前
                  （动画窗口由 `fairy_boot.play()` 紧接着 `raise_top()` 抢回来）；
                  而上锁之后，动画期间**任何**别的 `_force_show` 都不再执行 ——
                  否则弹通知卡/被 poke/窗口自愈都会把桌宠重新顶到动画上面去。
                ★ 露面这一下要**临时解锁**（调用方已经在 `play()` 之前上了锁，
                  见下面的 `self._boot_guard = True`），完事立刻锁回去。
                """
                self._boot_guard = False
                try:
                    self._force_show("boot-ready")
                finally:
                    self._boot_guard = True

            # ★★★ 2026-09-23 二次加固（主人："刚 restart，图层上下又出错了"）：
            #   **两条路都必须在动画之前上锁**。原来只有启动路径会上锁（因为它传了
            #   `on_ready=_arms_up`），**重播路径 `on_ready=None` ⇒ 整段动画 guard 都是 False**，
            #   期间只要弹一张卡/被 poke/窗口自愈，桌宠就会被 `ShowWindow` 顶到动画上面。
            #   ⇒ 锁提前到这里；`_arms_up` 里只为"露面"那一下临时解锁。
            self._boot_guard = True
            try:
                ok, el, stats = BOOT.play(
                    cx, cy, self.win, vl, vt, vr, vb,
                    countdown=(countdown if countdown else BOOT.T_CD),
                    topmost=self.topmost, pulse=self._write_beat, log=out,
                    # ★★ 2026-09-23：动画每出一帧，也让桌宠渲染一帧。
                    #   否则桌宠窗口停在进动画前那一帧、12 s 不动；而收束擦除露出的正是它
                    #   ⇒ 人眼看到"中央先卡一张定格图，本体随后才活过来"（主人 09:32 抓到的）。
                    on_frame=self.tick,
                    # ★★ 2026-09-23：**动画窗口建好之后，才让桌宠露面**。
                    #   启动路径（`keep_visible=True`）下窗口在 `run()` 里被**故意不显示**，
                    #   等这里第一帧动画就绪再显示 —— 否则那几百毫秒的准备期里
                    #   本体已经可见，会被看到"先露一两帧再被动画盖住"。
                    on_ready=(_arms_up if keep_visible else None))
            finally:
                self._boot_guard = False        # ★ 无论成功/失败/异常，都必须解锁
            self._boot_note = u"就绪" if ok else u"跳过"
            out(u"[boot] 结束：ok=%s 用时 %.2f s ｜ 各阶段帧数 %s" % (ok, el, stats))
            # ---- ★ 登场：主体先在**中央**出现 →（待 MOVE_DELAY 秒）→ 自己走到目标位 ----
            #   ★ 淡入与位移**同时起算但不同起点**：淡入立刻开始（好接住白光收束的余辉），
            #     位移等 `MOVE_DELAY` 秒（主人要的"在中心待 1 秒"）。
            self.x, self.y = cx, cy
            tx, ty = self._home_xy() if then_home else here
            t0 = time.perf_counter()
            self._move = (t0, cx, cy, tx, ty, MOVE_S, MOVE_DELAY)
            # ★★ 2026-09-23：**启动路径不要再淡入**（主人："依旧会顿一下或者闪一下"）。
            #   现在动画窗口盖在桌宠上面、白光擦除露出的就是真窗口本身，而它**一直亮着**
            #   （动画期间由 `on_frame=self.tick` 每帧喂）。这时再走一次 0.30 s 淡入
            #   ⇒ 等于把刚露出来的本体又推回透明再淡回来 = **闪一下**。
            #   重播路径（`keep_visible=False`）窗口被 SW_HIDE 过，仍然需要淡入。
            if keep_visible:
                self._appear_t0 = 0.0
                self._appear_s = 0.0
            else:
                self._appear_t0 = t0
                self._appear_s = APPEAR_S
            out(u"[boot] 中央登场 @(%d,%d) → %s @(%d,%d)"
                % (cx, cy, u"默认位（右下角）" if then_home else u"原位", tx, ty))
            if hidden:
                self._force_show("after-boot")                   # ★ 一定亮回来
            return ok
        except Exception as e:
            self._boot_note = u"异常"
            log_err("play_boot", e)
            try:
                self._force_show("after-boot-error")
            except Exception:
                pass
            return False

    def tick(self):
        now = time.perf_counter()
        self._tick_n += 1
        # ★ 「1% 台阶」的缓动要用**真实帧间隔**（帧率无关）—— 别写死 1/60
        _dt = (now - self._last_tick) if self._last_tick else (1.0 / 60.0)
        if self._last_tick:
            self._period_max = max(self._period_max, (now - self._last_tick) * 1000.0)
        self._last_tick = now
        self._tick_ts.append(now)
        self._tick_ts = [t for t in self._tick_ts if now - t <= 1.0]
        _t_in = time.perf_counter()

        # --- 心跳 / 停止开关 ---
        #   ★ 心跳抽成 `_write_beat()`：开机动画期间（12 s）tick 不动，必须由它来续命，
        #     否则 `check_fairy.vbs` 与单实例逻辑会把"正在放动画的进程"当成死了。
        if self._tick_n % 60 == 1:
            self._write_beat()
            self._period_max = 0.0
        if self._tick_n % 30 == 0 and os.path.exists(STOP_FILE):
            try:
                os.remove(STOP_FILE)
            except Exception:
                pass
            self._say("收到停止指令。", 1.0)
            self._quit = True
            user32.PostQuitMessage(0)
            return

        # --- 又双击了启动器？把窗口亮出来并归位（否则主人会以为"没反应"）---
        #   ★ 开机动画期间整段跳过：那时亮窗口/归位/弹气泡都没意义（窗口被动画盖着），
        #     而 `_force_show` 会把桌宠顶到动画上面（Z 序事故）。
        if (self._tick_n % 30 == 0 and not self._boot_guard
                and os.path.exists(POKE_FILE)):
            try:
                os.remove(POKE_FILE)
            except Exception:
                pass
            self._go_home(say=False)
            ok = self._force_show("poke")
            self._say("我在这儿。" if ok else "窗口没能显示出来，看 pet_error.log。", 2.6)
            if not ok:
                log_err("poke", RuntimeError("poke 之后窗口仍不可见"))

        # --- ★ 尺寸换档排队：主人点的那一档还在后台预烘 ⇒ 烘完自动切（不冻住桌宠）---
        if self._pending_size is not None and self._pending_size not in _PREBAKING:
            _n = self._pending_size
            self._pending_size = None
            self._switch_size(_n)

        # --- 自愈：窗口若被系统/别的程序藏起来，自己亮回来 ---
        if (self._show_requested and self.hwnd and self._tick_n % 120 == 0
                and not self._boot_guard
                and not user32.IsWindowVisible(self.hwnd)):
            log_err("selfcheck", RuntimeError("发现窗口不可见 → 重新显示"))
            self._force_show("selfcheck")

        # --- ★ 任务完成通知：我干完活写 notify.json，这里读到就弹卡（无条件轮询，与自动/手动无关）---
        # ★★ 2026-09-22：等**状态读过至少一次**再消费通知（`state_read_at` 首帧就置 >0）。
        #   否则卡片可能在「还没判定成 working」的那一帧就落到底部，随后进度条淡入 ⇒ 看到的「闪一下」。
        #   只延后一帧 ≈16 ms（肉眼不可见）；notify.json 不消费不会丢。
        #   ★★ 2026-09-22 晚再加固（主人截图抓到"黑条压在进度条上"）：
        #   光等 `state_read_at` 还不够 —— 同一帧里**「读进度」排在「读通知」之后**（见下方 prog 段），
        #   所以首帧消费通知时 `self.prog` 还是 None ⇒ `_note_up` 判 False 并**粘住** ⇒ 卡片落画布底部、
        #   与进度条 + FAIRY WORKING 三层重叠（实测卡片 y 251~354 ｜ 条 307~332 ｜ 副标题 341~361）。
        #   ⇒ 条件再加 `self.prog_poll_at > 0`（进度也**读过至少一次**）。只多等一帧（≈16 ms），
        #     且**没有 progress.json 时同样成立**（读过了、只是值为 None）⇒ 不会把卡片卡死。
        if (now - self.notify_poll_at > 0.5 and self.state_read_at > 0.0
                and self.prog_poll_at > 0.0 and not self._boot_guard):
            self.notify_poll_at = now
            n = self._read_notify()
            if n:
                self.notify_text, self.notify_secs, self.notify_kind = n
                self.notify_t0 = now
                self._note_up = False        # ★ 新卡片：落位重判一次（见 _overlay）
                self.bubble = None          # 通知优先：清掉正在显示的普通气泡
                self._force_show("notify")  # ★ 万一窗口被系统藏了，弹卡前先把它亮出来

        # --- ★ 工作进度（我上报 progress.json）：与通知一样**无条件轮询** ---
        if now - self.prog_poll_at > 0.5:
            self.prog_poll_at = now
            (self.prog, self.prog_done, self.prog_planned,
             self._prog_mtime, self.prog_settle) = self._read_progress()
            # ★★ 2026-09-23：交付（`--reply` 到 100%）后，**等主人开口**才让位。
            #   「我做完」≠「他看见」—— `--reply` 是我做的最后一件事，回复正文还要生成/渲染，
            #   界面才显示；按秒数计时**必然**在他读到之前到期（10 s、60 s 都试过，都不够）。
            #   · 复位：看到一条**没有 done** 的进度（= 新一轮开工了）就把计时清干净；
            #   · 让位：主人发了**下一句指令** ⇒ 他显然看过了 ⇒ 立刻回常态；
            #   · 兜底：都没发生 ⇒ 挂满 `DONE_SETTLE_S` 也回常态（他走开了）。
            if not self.prog_done:
                self._settled = False
                self._done_seen_at = None
            elif self.prog_settle:
                if self._done_seen_at is None:
                    self._done_seen_at = now
                    out(u"[bar] 交付完成 ⇒ 100%% 挂着，等主人下一句指令（最多 %.0f s）"
                        % DONE_SETTLE_S)
                elif not self._settled:
                    # ★ 必须**显式**调一次 `active()`：`_settled` 为真时 `_target_state()`
                    #   会提前返回、根本走不到 `active()` ⇒ `user_at` 永远不刷新。
                    self.act.active()
                    _why = u""
                    if self.act.user_at > self._done_seen_at:
                        _why = u"主人已发新指令 ⇒ 收尾态让位"
                    elif self._typing_in_wb():
                        # ★★ 2026-09-23 主人：「不用等 60 秒或者我下一轮输入 …… 你能监控到
                        #   输入窗口我在打字，就退出工作态」。**只在 100% 跑完之后**才允许
                        #   —— 能走到这一支就一定是 `prog_done and prog_settle`，
                        #     所以"任务中途插嘴"天然不可能误退（那时进不到这里）。
                        _why = u"主人在 WorkBuddy 开始打字 ⇒ 收尾态让位"
                    elif (now - self._done_seen_at) >= DONE_SETTLE_S:
                        _why = u"收尾已挂满 %.0f s ⇒ 回常态" % DONE_SETTLE_S
                    if _why:
                        self._settled = True
                        out(u"[bar] " + _why)
                        # ★★ 顺便把 `state.json` 也改成 idle —— **别让文件撒谎**：
                        #   原来只有内部切常态，文件一直写着 working，害我在真机自检里
                        #   误判过一轮（`_work/step137`）。只写这一次，不是每帧。
                        #   ★ 必须留在"真的收尾"这一支里：写早了 `state.json` 就说 idle，
                        #     而 `_target_state()` 拿它当 `auto_state` ⇒ 100% 会提前消失。
                        try:
                            _tmp = STATE_FILE + ".tmp"
                            with open(_tmp, "w", encoding="utf-8") as _f:
                                json.dump({"state": "idle",
                                           "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                           "ttl": 2700, "note": u"交付完成，自动回常态"},
                                          _f, ensure_ascii=False)
                            os.replace(_tmp, STATE_FILE)
                        except Exception as _e:
                            log_err("settle_write_state", _e)

        # --- 自动状态的来源：跟随 state.json，或本地「60 秒循环」（演示）---
        if self.auto_mode == "loop60":
            if now >= self.auto_loop_at:
                self.auto_state = "working" if self.auto_state == "idle" else "idle"
                self.auto_loop_at = now + AUTO_LOOP_S
                self._say("演示：%s" % ("工作态" if self.auto_state == "working" else "常态"), 2.2)
        elif now - self.state_read_at > 0.5:
            self.state_read_at = now
            #   ★★ 2026-09-22（主人定）：**通知只走「通知卡」一个渠道，不再弹气泡。**
            #   原因：`fairy_notify.py` 发通知时**同时**写 `notify.json`（卡片，全文）
            #     和 `state.json` 的 `note`（截前 40 字，只为记录）⇒ 这里再 `_say(note)`
            #     就是**把同一句话显示两遍**，而且气泡还被压成单行 + 截到 22 字 ⇒ 残缺副本。
            #   ★ `note` 仍然读出来（作为「为什么处于这个状态」的诊断记录），只是不再弹。
            #   ★ 气泡**没删** —— 它仍是菜单操作的即时反馈（已归位 / 感知已开 / 正在生成…），
            #     那类反馈没有卡片可用。
            st, _note = self._read_state_json()
            if st != self.auto_state:
                self.auto_state = st
        self._set_state(self._target_state(now), now)

        # --- ★ 进度条的出现状态机（2026-09-22 主人定的时序）---
        #   「进入工作态 ⇒ **先不显示进度条** ⇒ 我拆好任务、第一次上报进度 ⇒ 进度条以
        #     「01 数字从下方聚合」的动画出现（1.7 s）」。判据见 `_bar_wanted()`。
        #   ★ 短暂掉出工作态（感知空档）**不算换轮**：只有离开超过 `BAR_REHIDE_S` 才复位，
        #     否则条会一隐一现、出现动画还重播一遍。
        #   ★★ 2026-09-23 加 `not self._boot_guard`：**开机动画期间整条状态机挂起**。
        #     否则进度条会在动画播放时"露面"一次 —— 出现动画白播（被动画窗口盖着），
        #     等动画结束只剩一根静止的条，而主人要看的恰恰是那个"出现的动画"
        #     （主人："这边工作态缺少进度条出现的动画"）。
        if self.cur_state == "working" and not self._boot_guard:
            if (self._left_working_at is not None
                    and (now - self._left_working_at) > BAR_REHIDE_S):
                self._bar_epoch = self._prog_mtime     # 新的一轮 ⇒ 上一轮的进度不算数
                self._bar_shown = False
                self._bar_rev_t0 = None
            self._left_working_at = None
            if not self._bar_shown and self._bar_wanted():
                self._bar_shown = True
                self._bar_rev_t0 = now                 # ★ 从这一刻开始"聚合"
        elif self._left_working_at is None and not self._boot_guard:
            self._left_working_at = now
        self._bar_want = ((self.cur_state == "working") and self._bar_wanted()
                          and not self._boot_guard)
        self._bar_rev = None
        if self._bar_want and self._bar_rev_t0 is not None:
            _u = (now - self._bar_rev_t0) / BAR_REVEAL_S
            self._bar_rev = _u if _u < 1.0 else None   # None = 出现完了，走稳态路径

        # --- ★★ 1% 台阶：把**显示值**往目标值上追（目标本身不动）---
        #   条与数字都读显示值（`_pct_shown()`）⇒ 两处永远一致，且都以 1% 为最小格。
        self._pct_tgt = self._bar_pct() if self._bar_want else self._pct_disp
        if self._pct_tgt >= self._pct_disp:
            self._pct_disp = min(self._pct_tgt, self._pct_disp + PCT_EASE_RATE * _dt)
        else:
            self._pct_disp = self._pct_tgt        # 目标变小 = 新一轮 ⇒ 吸附，不播倒退动画

        # --- ★ 登场位移：开机动画结束 ⇒ 主体在屏幕中央出现 ⇒ 自己走到目标位 ---
        #   （见 `play_boot`；`_blit` 是按 self.x/self.y 上传的，所以改这两个数就是移动）
        if self._move is not None:
            t0, fx, fy, tx, ty, dur, delay = self._move
            mu = (now - t0 - delay) / max(1e-6, dur)
            if mu >= 1.0:
                self.x, self.y = int(tx), int(ty)
                self._move = None
                self._save_pos()
            elif mu > 0.0:
                e = 1.0 - (1.0 - mu) ** 3              # ease-out-cubic：起步快、落位稳
                self.x = int(round(fx + (tx - fx) * e))
                self.y = int(round(fy + (ty - fy) * e))

        # --- ★ 登场淡入（0.3 s；别让主体在动画结束后"啪"地跳出来）---
        if self._appear_s > 0.0:
            au = (now - self._appear_t0) / self._appear_s
            if au >= 1.0:
                self._appear_s = 0.0
                self._appear_k = 1.0
            elif au > 0.0:
                self._appear_k = au * au * (3.0 - 2.0 * au)
            else:
                self._appear_k = 0.0

        t_ms = (now - self.t0) * 1000.0
        op = self._open_t(now)
        fl = self._flicker_op(now)
        self.R.draw(t_ms=t_ms, open_t=op, flicker_op=fl)

        panel = None
        if not self.menu_open:
            self._tip_txt = None            # 菜单关了 ⇒ 提示立刻清掉（下次开菜单重新计时）
            self._tip_row = (-1, -1)
        if self.menu_open:
            p = POINT()
            user32.GetCursorPos(ctypes.byref(p))
            L = self._menu_layout()
            self.hover = self._hit_menu(p.x - self.x, p.y - self.y, L)
            self._tip_update(L, self.hover, now)     # ★ 换行才换文字、才重置 5 s
            panel = {"layout": L, "hover": self.hover, "tip": self._tip_span(now)}
            # ★★ 窗口**之外**的左键按下也要能关菜单（2026-09-22 主人报的 bug 的另一半）。
            #   窗口内那条走 WM_LBUTTONDOWN（已让菜单开着时整窗 HTCLIENT）；
            #   窗口外我们收不到消息 ⇒ 只能取**全局**左键状态，并做**边沿检测**
            #   （只认"抬起→按下"那一下，否则拖滑块 / 按住不放时会被误关）。
            down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            if down and not self._lbtn_down and self.hover[0] < 0:
                self.menu_open = False
                panel = None
            self._lbtn_down = down
            if panel is not None and now - self.menu_at > 8.0 and self.hover[0] < 0:
                self.menu_open = False
                panel = None
        bubble = self.bubble if (self.bubble and now < self.bubble_until) else None
        if bubble is None:
            self.bubble = None
        self._bar_k = 1.0 - op          # 进度条的显隐跟着工作态的过渡（一起淡入淡出）
        ov = self._overlay(panel, bubble, self._note_span(now))

        bgra, alpha = self.R.to_bgra(ov=ov)
        # ★ 登场淡入：**颜色和 alpha 必须一起缩**（预乘语义）——
        #   只缩 alpha 会让显示结果变亮（预乘色不缩、背景透得更多）。
        #   顺带记住上一条教训：`alpha` 是独立数组，`bgra[...,3]` 才是上屏读的那份 ⇒ 两处都写。
        if self._appear_k < 0.999:
            _kk = int(round(max(0.0, self._appear_k) * 255.0))
            bgra[...] = ((bgra.astype(np.uint16) * _kk) >> 8).astype(np.uint8)
            alpha = ((alpha.astype(np.uint16) * _kk) >> 8).astype(np.uint8)
            bgra[..., 3] = alpha
        # ★ 工作态进度条：合成到 BGRA/alpha 的**那一小块区域**上。
        #   为什么不塞进 `ov`：`to_bgra(ov=...)` 要全幅合成，实测 **+4.4 ms/帧**；
        #   局部合成只要 ~0.15 ms（算法完全一致：source-over，直色 × alpha）。
        #   ★★ `self._bar_want`：主人定的时序 —— 没拆好任务之前**不画条**
        #      （旧版是"工作态一来就淡入"，已作废）。
        if self.bar is not None and self._bar_k > 0.02 and self._bar_want:
            # ★ 传给合成的是**显示值** `_pct_shown()`（1% 台阶 + 缓动），不是目标值。
            self.bar.composite(bgra, alpha, self._pct_shown(), t_ms, self._bar_k,
                               self._bar_rev)
        self._blit(bgra, alpha)
        self._cost = 0.9 * self._cost + 0.1 * (time.perf_counter() - _t_in) * 1000.0

    # ------------------------------------------------------------ 消息
    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_NCHITTEST:
                p = POINT()
                user32.GetCursorPos(ctypes.byref(p))
                lx, ly = p.x - self.x, p.y - self.y
                if 0 <= lx < self.win and 0 <= ly < self.win:
                    if self.menu_open:
                        # ★★ 菜单开着时**整窗可点**（2026-09-22 主人报的 bug：在空白处点左键关不掉菜单）。
                        #   根因：空白处 alpha ≤ 10 ⇒ 判 HTTRANSPARENT ⇒ 点击**穿透**到底下的窗口，
                        #   我们根本收不到 WM_LBUTTONDOWN ⇒ 菜单只能等 8 s 自动关。
                        #   菜单是"模态"的，这段时间整窗吃掉左键才是对的。
                        return HTCLIENT
                    if self._alpha[ly, lx] > 10:
                        return HTCLIENT
                return HTTRANSPARENT
            if msg == WM_LBUTTONDOWN:
                p = POINT()
                user32.GetCursorPos(ctypes.byref(p))
                lx, ly = p.x - self.x, p.y - self.y
                if self.menu_open:
                    self._menu_click(lx, ly)     # ★ 判定与派发都在那儿（可离屏测试）
                    return 0
                self.dragging = True
                self.drag_off = (lx, ly)
                user32.SetCapture(hwnd)
                return 0
            if msg == WM_MOUSEMOVE:
                if self.dragging:
                    p = POINT()
                    user32.GetCursorPos(ctypes.byref(p))
                    # ★★ 2026-09-23 主人："往右下角移动后，超出屏幕边界了……位置能**根据 1 块屏幕**
                    #   去定位，而且尤其**通知框**不要超出屏幕"。
                    #   原来拖动**一点夹取都没有**（甩出屏幕就找不回来了）。
                    #   ⇒ 每帧夹进**主屏**，且按"可见内容"算（通知卡在画布里左右各留
                    #     `CARD_INSET`）⇒ 卡片右端不会再被屏幕切掉。
                    self.x, self.y = SCR.fit(p.x - self.drag_off[0],
                                             p.y - self.drag_off[1], self.win)
                    self.tick()
                elif self.drag_slider and self.menu_open:
                    p = POINT()
                    user32.GetCursorPos(ctypes.byref(p))
                    i, k = self._hit_menu(p.x - self.x, p.y - self.y)
                    if i >= 0 and k >= 0:
                        it = self._menu_layout()["rows"][i]
                        if it["kind"] == "slider" and it["key"] == self.drag_slider:
                            self._set_slider(self.drag_slider, k)
                    self.tick()
                return 0
            if msg == WM_LBUTTONUP:
                if self.dragging:
                    self.dragging = False
                    self._save_pos()
                if self.drag_slider:
                    self.drag_slider = None
                user32.ReleaseCapture()
                return 0
            if msg == WM_LBUTTONDBLCLK:
                self._say(random.choice(SPEAK), 2.4)
                return 0
            if msg == WM_RBUTTONUP:
                self.menu_open = not self.menu_open
                self.menu_at = time.perf_counter()
                self.menu_page = ""          # ★ 每次开菜单都回主菜单（别在子页里"卡住"）
                self.hover = -1
                # ★ 记下"开菜单这一刻左键是不是按着"：右键开菜单时它是抬起的 ⇒ 紧接着左键按下
                #   就是"点到了别处" ⇒ 关菜单。若将来改成双击/长按开，也不会被自己那一下误关。
                self._lbtn_down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception as e:
            log_err("WndProc msg=0x%04X" % msg, e)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _set_slider(self, key, k):
        """滑块被点到第 k 段。★ 拖动时会**反复**调用，所以每一档都必须幂等。"""
        if key == "size":
            new = SIZES[max(0, min(k, len(SIZES) - 1))]
            if new != self.px:
                self._switch_size(new)
        elif key == "mode":
            if k == 0:
                if not self.follow:
                    self.follow = True
                    self.manual = None
                    self.auto_loop_at = time.perf_counter() + AUTO_LOOP_S
                    self._say("自动：%s" % ("跟随 WorkBuddy" if self.auto_mode == "follow"
                                            else "60 秒循环"), 2.2)
            else:
                if self.follow:
                    self.follow = False
                    self.manual = self.cur_state     # ★ 保留当前状态 ⇒ 切换不跳变
                    # ★ 手动模式下「感知」不再参与判断（见 `_target_state()`）⇒ 顺口说明，
                    #   免得主人切到手动后以为感知坏了。
                    self._say(u"手动（%s）%s"
                              % (u"工作态" if self.manual == "working" else u"常态",
                                 u"，感知暂不参与" if self.sense else u""), 2.6)
        elif key == "auto":
            m = "follow" if k == 0 else "loop60"
            if m != self.auto_mode:
                self.auto_mode = m
                self.auto_loop_at = time.perf_counter() + AUTO_LOOP_S
                self._say("跟随 WorkBuddy" if m == "follow" else "60 秒循环（演示）", 2.2)
        elif key == "state":
            st = "idle" if k == 0 else "working"
            if st != self.manual:
                self.manual = st
                self._say("常态（手动）" if st == "idle" else "工作态（手动）", 2.0)
        elif key == "topmost":
            want = (k == 0)                      # 左格=开
            if want != self.topmost:             # ★ 幂等：拖动反复调用时不会重复触发
                self.topmost = want
                self._apply_topmost()
                self._save_pos()
                self._say("已置顶（一直在最上层）" if want else "已取消置顶（可被别的窗口盖住）", 2.0)
        elif key == "bootmode":
            # ★ 只写配置（pos.json）⇒ **下次启动生效**（主人 2026-09-22 明确要求的那种行为）。
            #   想立刻看一遍请用下面的「播放开机动画」按钮。
            want = (k == 0)
            if want != self.boot_anim:
                self.boot_anim = want
                self._save_pos()
                self._say("下次开机播放动画（重启生效）" if want
                          else "下次开机跳过动画（重启生效）", 2.6)
        elif key == "sense":
            want = (k == 0)                      # 左格=开
            if want != self.sense:               # 幂等：拖动反复调用时不会重复触发
                self.sense = want
                self._save_pos()
                if want:
                    self._say("感知已开：你一发指令我就半睁", 2.2)
                else:
                    self._say("感知已关：只认 state.json", 2.2)
        elif key == "font":
            # ★★ 主人 2026-09-23：「C 和 D 都要」⇒ 字体做成可切档（中 / 粗 / 特粗）。
            #   切换**立刻生效**（通知卡每帧现画，不用重烘），并写进 pos.json 记住。
            if 0 <= k < len(FONT_KEYS):
                nk = FONT_KEYS[k]
                if nk != self.font_key:          # 幂等：拖动反复调用时不重复触发
                    self.font_key = nk
                    self._apply_font()
                    self._save_pos()
                    _seg, name, _ = font_meta(nk)
                    tip = (u"（13 px 下笔画最密，多笔画字会有点糊 —— 嫌糊就退回「粗」）"
                           if nk == "black" else u"")
                    self._say(u"通知卡字体：%s%s" % (name, tip), 3.0)

    def _do_menu_key(self, key):
        """菜单里的「按钮行」与「二级页导航行」。"""
        if key in ("display", "behavior"):
            # ★ 进二级页（主人要的"设置单独放二级菜单"）；顺带重置自动关闭计时，
            #   免得刚进去就被菜单超时关掉。
            self.menu_page = (self.PAGE_DISPLAY if key == "display"
                              else self.PAGE_BEHAVIOR)
            self.menu_at = time.perf_counter()
            self.hover = (-1, -1)
            # ★ 换页后**行下标含义变了**（第 3 行在两页里不是同一件事）⇒ 强制重算提示。
            #   用一个不可能等于任何 hover 的值，下个 tick 必然触发 `_tip_update`。
            self._tip_row = (-2, -2)
            self._tip_txt = None
            return
        if key == "back":
            self.menu_page = ""
            self.menu_at = time.perf_counter()
            self.hover = (-1, -1)
            self._tip_row = (-2, -2)         # ★ 同上：换页强制重算提示
            self._tip_txt = None
            return
        if key == "boot":
            # ★ 重播：倒计时用同一个 3.0 s（主人 2026-09-22 定：「重播动画也一样」）
            self.menu_open = False
            self._say("开机动画…", 1.2)
            self.tick()                       # 先把"菜单已关"这一帧画出去
            self.play_boot(why="replay")
            self._say("已就绪", 1.6)
        elif key == "home":
            self.menu_open = False
            self._go_home()
            self._say("已归位", 1.6)
        elif key == "quit":
            self.menu_open = False
            self._quit = True
            user32.PostQuitMessage(0)

    def _switch_size(self, new):
        """切换显示尺寸：重建渲染器（有缓存则秒开），并重建 DIB + 窗口尺寸

        ★★ 2026-09-23 主人：「不要我右键调大小的时候还得卡一段时间」。
          ⇒ 若那一档**正在后台预烘**（`_prebake_others`），这里**不阻塞**：
            只记进 `_pending_size`，由 `tick()` 在烘完后自动切过去，气泡先跟主人说明。
            必须错开：两处同时烘同一档会写坏同一个缓存文件。
        """
        if new in _PREBAKING:
            self._pending_size = new
            self._say("%d px 正在烘焙，好了自动换…" % new, 2.4)
            out(u"[size] %d px 正在后台烘焙 ⇒ 排队等它（烘完自动切换）" % new)
            return
        self._say("正在生成 %d px…" % new, 1.0)
        self.tick()
        try:
            self.R = FastMascot(size=new, ss=2, cache_dir=CACHE_DIR, verbose=True)
        except Exception as e:
            log_err("switch_size", e)
            self._say("尺寸切换失败", 2.0)
            return
        self.size_idx = SIZES.index(new)
        self.px = new
        self.win = self.R.N          # ★★ 漏了这一句的后果（实测崩过）：
        #   渲染器已按新尺寸重建（画布 520），而窗口/alpha 仍是老尺寸（422）
        #   ⇒ to_bgra 里 (520,520,3) 和 (422,422,1) 广播失败 ⇒ 未捕获异常 ⇒ 桌宠直接消失。
        #   所以：换尺寸必须 px / win / alpha / DIB / SetWindowPos 五样一起换。
        self._alpha = np.zeros((self.win, self.win), dtype=np.uint8)
        # ★ 换尺寸必须连进度条一起重建（它的几何、横纹周期、数字字号都按画布算）
        self.bar = FB.BarView(self.win, self.px / 160.0)
        self.bar.warm()
        gdi32.DeleteObject(self._dib)
        gdi32.DeleteDC(self._hdc)
        self._init_dib()
        # ★★ 2026-09-23：换尺寸后按**主屏**重新夹一次（原来按虚拟桌面夹，副屏会被算进来）。
        #   用 `SCR.fit()` ⇒ 连通知卡的可见范围一起保证（主人："通知框不要超出屏幕"）。
        self.x, self.y = SCR.fit(self.x, self.y, self.win)
        user32.SetWindowPos(self.hwnd, None, self.x, self.y, self.win, self.win, 0x0014)
        self._save_pos()
        self._say("%d px 就绪" % new, 2.0)

    # ------------------------------------------------------------ 后台预烘
    def _prebake_others(self):
        u"""后台把**另外两档尺寸**也烘好 —— 主人换尺寸时就不用等（2026-09-23 主人要求）。

        ★ 为什么不在"换尺寸那一刻"烘：那是**同步**的，一档 1~2 分钟，期间桌宠整个冻住
          （主人："不要我右键调大小的时候还得卡一段时间"）。
        ★ 为什么要限速：烘焙是纯 CPU 活，跑满会把桌宠帧率压下去。三招一起用：
            ① 子线程（daemon，桌宠退出即随进程结束）；
            ② `SetThreadPriority(BELOW_NORMAL)` ⇒ 跟主循环抢 CPU 时永远让位；
            ③ `bake_progress` 回调里 `sleep(0.05)` ⇒ 把长任务切碎，别一口气占住 GIL。
        ★ 与 `_switch_size` 用 `_PREBAKING` 互斥：两处同时烘同一档会写坏同一个缓存文件。
        ★ 已命中缓存的档位只是"载入"（约 0.3 s）⇒ 长期用起来这一步几乎不花时间。
        """
        import threading

        cur = SIZES[self.size_idx]
        todo = [s for s in SIZES if s != cur]
        if not todo:
            return

        def job():
            try:
                kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -1)   # BELOW_NORMAL
            except Exception:
                pass
            for s in todo:
                _PREBAKING.add(s)
                t0 = time.perf_counter()
                try:
                    FastMascot(size=s, ss=2, cache_dir=CACHE_DIR, verbose=False,
                               bake_progress=lambda f, lab: time.sleep(0.05))
                    out(u"[prebake] %d px 就绪（%.1f s）" % (s, time.perf_counter() - t0))
                except Exception as e:
                    log_err("prebake(%d)" % s, e)
                finally:
                    _PREBAKING.discard(s)

        th = threading.Thread(target=job, name="fairy-prebake")
        th.daemon = True
        th.start()
        out(u"[prebake] 后台预烘：%s（当前 %d px）" % (todo, cur))

    # ------------------------------------------------------------ 运行
    def run(self, smoke=0.0, show=True, max_frames=0, seconds=0.0, boot=False):
        try:                                  # ★ 没这一句，sleep 粒度是 15.6 ms ⇒ 60fps 掉到 ~30fps
            _winmm = ctypes.WinDLL("winmm")
            _winmm.timeBeginPeriod(1)
        except Exception:
            _winmm = None
        hinst = kernel32.GetModuleHandleW(None)
        cls = "FairyPetV2"
        self._wndproc = WNDPROC(self._on_message)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.cast(ctypes.c_void_p(32512),
                                                          wintypes.LPCWSTR))
        wc.lpszClassName = cls
        user32.RegisterClassExW(ctypes.byref(wc))

        # ★ 置顶与否由 self.topmost 决定（右键菜单可关，且跟位置一起持久化）
        ex_style = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if self.topmost:
            ex_style |= WS_EX_TOPMOST
        # ★★ 2026-09-23：要播开机动画 ⇒ **先落到屏幕中央、再建窗口**。
        #   动画窗口是以桌宠为中心铺开的，白光擦除要在正中把它露出来；
        #   建完再挪会先闪一下原来的位置。
        if boot:
            self.x, self.y = self._center_pos()
        self.hwnd = user32.CreateWindowExW(
            ex_style, cls, "Fairy", WS_POPUP, self.x, self.y, self.win, self.win,
            None, None, hinst, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._init_dib()
        self._show_requested = bool(show)
        # ★★ 2026-09-23：要播开机动画时**先不显示**。
        #   动画窗口的创建 + 视频管道启动要几百毫秒，这段时间若桌宠已经可见，
        #   人眼就会先看到本体"露一两帧"再被动画盖住（主人 09:40 指出的现象）。
        #   改由 `play_boot` → `play(on_ready=...)` 在**动画窗口就绪之后**才显示。
        if show and not boot:
            user32.ShowWindow(self.hwnd, 4)      # SW_SHOWNOACTIVATE：显示但不抢焦点
            if not user32.IsWindowVisible(self.hwnd):
                # 首帧 ShowWindow 没生效（少见）：重试一次并把结果写进日志 —— 绝不静默。
                user32.ShowWindow(self.hwnd, 5)                  # SW_SHOW
                log_err("startup", RuntimeError(
                    "首帧 ShowWindow 后仍不可见，已重试 SW_SHOW；现在 vis=%d"
                    % user32.IsWindowVisible(self.hwnd)))
        # 启动时若发现残留的 poke：说明上一次双击没被响应，立刻归位并亮出来
        if os.path.exists(POKE_FILE):
            try:
                os.remove(POKE_FILE)
            except Exception:
                pass
            self._go_home(say=False)
            self._force_show("startup-poke")
        # ★★ 2026-09-23：**先定状态、再渲第一帧、再播动画**。
        #   原来顺序是「tick() → play_boot() → 读状态」，于是动画那 12 s 里桌宠用的还是
        #   构造函数里的初始状态，动画一结束又按 state.json 切一次
        #   ⇒ 白光刚退去就"变一下"（主人："依旧会顿一下"）。
        #   现在动画开播前状态就已经定了，整个过程只有一种形态。
        st, _ = self._read_state_json()
        self.auto_state = st
        self.cur_state = st
        self.open_t = 0.0 if st == "working" else 1.0
        self.tick()
        # ★★ 2026-09-23（主人："这边（公司电脑）上的工作态，缺少进度条出现的动画"）：
        #   构造函数里 `_bar_epoch` 被设成 `progress.json` 的当前 mtime
        #   ⇒ **我在这之前写下的进度全被判成"上一轮的"** ⇒ 整轮工作都看不到进度条。
        #   主人每重启一次桌宠就复现一次（我开工时写的进度，往往早于他双击那一刻）。
        #   这里放宽：**启动时就在工作态**、且那份进度还够新（< 45 min）⇒ 认它；
        #   之后的出现动画照常播一遍（见 tick 里的状态机）。
        if st == "working":
            try:
                _age = time.time() - os.path.getmtime(PROGRESS_FILE)
                if self.prog is not None and _age < AUTO_TIMEOUT_S:
                    self._bar_epoch = 0.0
                    out(u"[bar] 启动即在工作中 ⇒ 接受已有 progress.json"
                        u"（%.0f%%，写在 %.0f s 前）" % (self.prog, _age))
            except Exception:
                pass
        # ★★ 2026-09-23：开机动画挪到**建好窗口之后**播 ——
        #   动画窗口（后创建 ⇒ Z 序在上）盖住桌宠，白光向中心擦除时露出的是
        #   **真桌宠窗口本身**，不再是"现场合成的一张假主体"。
        if boot:
            self.play_boot(why="startup", then_home=True, keep_visible=True)
        # ★★ 2026-09-23 主人：「烘焙最好把另外两档大小一起做了，不要我右键调大小的时候
        #   还得卡一段时间」⇒ 启动流程（含开机动画）走完后，在**后台线程**里把另外两档烘好。
        #   `--smoke` 自检默认不烘（免得污染帧率测量）；`FAIRY_PREBAKE=1` 可强制开。
        if smoke <= 0 or os.environ.get("FAIRY_PREBAKE"):
            self._prebake_others()

        PM_REMOVE = 0x0001
        WM_QUIT = 0x0012
        msg = wintypes.MSG()
        # ★★ 进消息循环前**先抽干队列**（2026-09-22 真事故的兜底防线）：
        #   开机动画窗口销毁时会 `PostQuitMessage` ⇒ 队列里留下 WM_QUIT ⇒
        #   下面第一轮 PeekMessage 取到它就会立刻 `self._quit = True` ⇒ 桌宠闪一下就没。
        #   `fairy_boot.drain_quit()` 已经堵住了主路径，这里是第二道保险
        #   （比如将来又有什么窗口在 run() 之前被销毁）。
        try:
            _nq = 0
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                _nq += 1
            if _nq:
                out("启动前清掉 %d 条残留消息（含可能的 WM_QUIT）" % _nq)
        except Exception as e:
            log_err("drain-before-run", e)
        interval = 1.0 / FPS
        last = time.perf_counter()
        t_start = last
        next_t = last
        t_end = (last + (seconds or smoke)) if (seconds > 0 or smoke > 0) else 0.0
        out("Fairy v2 启动：hwnd=%s  本体 %d px / 窗口 %d px  dpi=%.2f  pos=(%d,%d)  state=%s"
            % (self.hwnd, self.px, self.win, self.dpi, self.x, self.y, st))
        # ★ 2026-09-23 高分屏：系统缩放 >100% 时补一句原因 ——
        #   高 DPI 下画布面积是**平方级**增长的（缓存 ∝ N²、首烘时长 ∝ N²），
        #   主人双击后对着倒计时窗等首烘时会想知道"这次为什么这么久"。
        #   推演表见 `_work/v2_04_dpi_matrix.py`。
        if self.dpi > 1.01:
            _ref = canvas_px(260)          # 参考：100% 缩放、260 档的画布边长
            out(u"[dpi] 系统缩放 %.0f%% ⇒ 画布 %d px，面积是 100%% 缩放 260 档的 %.2f 倍"
                u"（首烘时长与缓存按面积同比涨）"
                % (round(self.dpi * 100.0), self.win,
                   (self.win * self.win) / float(_ref * _ref)))
        while not self._quit:
            idle = True
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                idle = False
                if msg.message == WM_QUIT:
                    self._quit = True
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            if self._quit:
                break
            now = time.perf_counter()
            if now >= next_t:
                # ★ 用绝对时间网格推进：写成 `now - last >= interval` 会每帧固定漂移，
                #   实测只能跑到 56 fps；对齐网格后能贴住 60。
                next_t += interval
                if next_t < now:                 # 落后超过一整帧才重锚，避免雪崩
                    next_t = now + interval
                last = now
                try:
                    self.tick()
                    self._fail = 0
                except Exception as e:
                    # ★ 渲染出一次错，绝不能把桌宠整个干掉 —— 实测换尺寸时崩过，
                    #   表现就是"宠物突然消失，什么提示都没有"。这里改成记日志 + 计数，
                    #   连续失败够多才体面退出。
                    self._fail += 1
                    log_err("tick #%d" % self._fail, e)
                    if self._fail == 1:
                        self._say("渲染异常，看 pet_error.log", 2.4)
                    if self._fail > 600:
                        out("连续 %d 帧渲染失败，退出。" % self._fail)
                        self._quit = True
            elif idle:
                time.sleep(0.001)
            if max_frames and self._tick_n >= max_frames:
                out("帧数到：tick=%d  平均 %.2f ms/帧"
                    % (self._tick_n, (time.perf_counter() - t_start) / self._tick_n * 1000))
                break
            if t_end and now >= t_end:
                el = now - t_start
                out("运行结束：%.2fs  tick=%d  实测 %.1f fps  tick 耗时 %.2f ms  最差帧 %.1f ms"
                    % (el, self._tick_n, self._tick_n / el, self._cost, self._period_max))
                break
        try:
            gdi32.DeleteObject(self._dib)
            gdi32.DeleteDC(self._hdc)
            user32.DestroyWindow(self.hwnd)
        except Exception:
            pass
        try:
            # ★ 必须注销窗口类。否则**同一进程内**第二次建窗口时，RegisterClassExW 会失败
            #   （类名已存在），而系统仍会用**第一次注册的那个 WndProc** —— 它绑在已释放的
            #   对象上 ⇒ 二次运行直接崩（实测：selftest 跑两态时第二轮 exit 127、且
            #   最后一行的打印都来不及执行）。生产里只建一次窗口，但这个坑值得堵死。
            user32.UnregisterClassW(cls, hinst)
        except Exception:
            pass
        if _winmm is not None:
            try:
                _winmm.timeEndPeriod(1)
            except Exception:
                pass


def selftest(size=SIZE, frames=180, only=None):
    """只建窗口**不显示**：完整跑 Win32 管线（CreateWindow → DIB → UpdateLayeredWindow）
    + 渲染，报出实测帧时。桌面上不会出现任何东西。

    ★ 把单帧耗时**拆成三段**（渲染 / 装箱 / 上屏）。
    起因：真机实测工作态只有 **30 fps / 33.7 ms**，而离屏纯渲染只有 **5.1 ms** ⇒
    差值必须落在这三段里的某一段，拆开就能一眼定位（别靠猜）。

    `only` = "idle" / "working" 时只测那一态 —— 推荐这么用（一进程一态），
    虽然已经注销了窗口类可以同进程连跑两态，但分开跑更好排查。
    """
    import tempfile
    global STATE_FILE, BEAT
    orig_state_file = STATE_FILE
    orig_beat = BEAT
    # ★ 心跳也改到临时目录：selftest 与**主人的常驻进程**同时在跑时，
    #   两边都会写 BASE/heartbeat.txt ⇒ 会把主人那份冲掉，
    #   表现成"心跳怎么跳来跳去/位置变了"。ERRLOG 则**故意保持真实**——
    #   测试真出错时主人应该看得见。
    BEAT = os.path.join(tempfile.gettempdir(), "fairy_selftest_heartbeat.txt")
    cases = [("常态", "idle"), ("工作态", "working")]
    if only:
        cases = [c for c in cases if c[1] == only]
        if not cases:
            print("[selftest] --state 只认 idle / working")
            return
    for lab, stt in cases:
        tmp = os.path.join(tempfile.gettempdir(), "fairy_selftest_%s.json" % stt)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"state": stt, "ts": "", "ttl": 2700, "note": "selftest"}, f)
        STATE_FILE = tmp                    # ★ 临时改全局：绝不碰主人的 state.json
        # ★ 清空本线程残留的 WM_QUIT：上一轮 DestroyWindow 会往队列里留一个，
        #   不清的话下一个实例一进消息循环就立刻退出 —— 实测第二轮只跑了 1 帧（白测一轮）。
        _m = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(_m), None, 0, 0, 0x0001):
            pass
        pet = FairyPet(size=size)
        pet.sense = False     # ★ 自检必须关掉"会话活动感知" —— 否则我自己在跑测试
                              #   就等于"会话正忙"，state=idle 那一轮永远测不出 idle。
        acc = {"draw": 0.0, "pack": 0.0, "blit": 0.0}

        def wrap(fn, key):
            def g(*a, **k):
                t0 = time.perf_counter()
                r = fn(*a, **k)
                acc[key] += time.perf_counter() - t0
                return r
            return g

        pet.R.draw = wrap(pet.R.draw, "draw")         # ① 渲染（含数字层）
        pet.R.to_bgra = wrap(pet.R.to_bgra, "pack")   # ② 装箱：alpha + 预乘 + BGRA
        pet._blit = wrap(pet._blit, "blit")           # ③ 上屏：memmove + UpdateLayeredWindow
        pet.run(show=False, max_frames=frames)
        n = max(pet._tick_n, 1)
        a = {k: acc[k] / n * 1000.0 for k in acc}
        # ★ 即时打印：数据先落地，别攒到最后（下一轮若崩，攒着的全丢）。
        print("[selftest]   %-6s 渲染 %6.2f ｜ 装箱 %6.2f ｜ 上屏 %6.2f ｜ 三段合计 %6.2f ｜ tick总 %6.2f ms（n=%d）"
              % (lab, a["draw"], a["pack"], a["blit"], sum(a.values()), pet._cost, n),
              flush=True)
        pet = None          # ★ 用 None 而**不是 `del`**：显式 del 会立刻释放 ctypes 回调
        #   对象（WNDPROC），交给 GC 更省心。
    STATE_FILE = orig_state_file
    BEAT = orig_beat
    if os.path.exists(ERRLOG):
        print("[selftest] ⚠ pet_error.log 有内容：")
        print(open(ERRLOG, encoding="utf-8", errors="replace").read()[-800:])
    else:
        print("[selftest] 无错误日志 ✅")


def _heartbeat_pid():
    """从 heartbeat.txt 里读正在运行那个实例的 pid（它自己写的，最可靠）。"""
    try:
        with open(BEAT, "r", encoding="utf-8") as f:
            t = f.read()
        for kv in t.replace("  ", " ").split(" "):
            if kv.startswith("pid="):
                return int(kv[4:])
    except Exception:
        pass
    return None


def _heartbeat_stale(sec=2.5):
    """心跳超过 sec 秒没更新 ⇒ 那个实例多半已经死了。"""
    try:
        return (time.time() - os.path.getmtime(BEAT)) > sec
    except Exception:
        return True


def _retire_existing(grace=4.0):
    """请已有实例退出；真赖着就按 pid 强杀。

    ★ 为什么必须做这一步：**宁可多花 4 秒，也不能出现两个窗口** ——
      两个实例会各自弹一张通知卡，主人看到的就是"重叠的对话框"（实测截图）。
    ★ 强杀只认 heartbeat 里那个 pid，**不做按名字批量杀进程**（避免误伤别的 python）。
    """
    try:
        with open(STOP_FILE, "w", encoding="utf-8") as f:
            f.write("quit\n")
    except Exception as e:
        log_err("retire.write_stop", e)
    t0 = time.time()
    while time.time() - t0 < grace:
        if _heartbeat_stale(1.2):
            out("旧实例已退出（%.1f s）" % (time.time() - t0))
            return True
        time.sleep(0.2)
    pid = _heartbeat_pid()
    if pid:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=8)
            time.sleep(0.8)
            out("旧实例不响应停止指令 → 已按 pid=%d 结束它" % pid)
            return True
        except Exception as e:
            log_err("retire.kill", e)
    out("⚠ 无法确认旧实例已退出（它可能仍在画面上）—— 建议双击 check_fairy.vbs 看一眼")
    return False


def _poke_existing(timeout=4.0):
    """已有实例在跑时的处理：写 poke.txt 请它把窗口亮出来并归位。
    返回 True = 它响应了（本次可以安全退出，主人也已经"看见了"反应）。

    ★ timeout 4.0 s：在跑的那个实例每 30 tick（≈0.5 s）检查一次 poke.txt，
      给 4 秒是留足余量。**别为了"快"把这个值压小** —— 压小的代价是
      误判"它不理我"进而把好好的实例退掉、白重启一次。

    ★ 原来的实现是直接 `sys.exit(0)`：静默退出、没窗口、没提示、连日志都没有。
      只要有任何残留实例占着那把单实例锁（例如早先测试留下的、窗口对用户不可见的
      实例），主人双击启动器就什么都看不到 —— 实测就是这个把人坑了（"双击没反应啊"）。
    """
    try:
        with open(POKE_FILE, "w", encoding="utf-8") as f:
            f.write("%.3f\n" % time.time())
    except Exception as e:
        log_err("poke.write", e)
        return False
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not os.path.exists(POKE_FILE):        # 被在跑的那个实例消费掉了
            return True
        time.sleep(0.05)
    return False


if __name__ == "__main__":
    a = sys.argv[1:]
    # ★★ 2026-09-23：**DPI 感知必须在建任何窗口之前声明**。
    #   原来只有 `FairyPet.__init__` → `_dpi_scale()` 里调，而倒计时窗
    #   （`fairy_boot.Splash`）是在 `FairyPet(...)` **之前**建的 ⇒ 系统若开了缩放，
    #   那扇窗就会按"虚拟化坐标"落位，看起来就是"没在屏幕正中"。
    #   （实测本机 100% 缩放：窗口 658x370 与算出来的完全一致，但这条得提前摆对。）
    try:
        FairyPet._dpi_scale()
    except Exception:
        pass
    if "--selftest" in a:
        sz = SIZE
        for i, v in enumerate(a):
            if v == "--size" and i + 1 < len(a):
                sz = int(a[i + 1])
        only = None
        for i, v in enumerate(a):
            if v == "--state" and i + 1 < len(a):
                only = a[i + 1]
        selftest(sz, only=only)
    else:
        sm = 0.0
        if "--smoke" in a:
            i = a.index("--smoke")
            sm = float(a[i + 1]) if i + 1 < len(a) else 5.0
        sz0 = SIZE
        if "--size" in a:
            i = a.index("--size")
            if i + 1 < len(a):
                sz0 = int(a[i + 1])
        else:
            # ★★ 2026-09-23 主人：「窗口定位需个测量程序先跑 —— 发现是笔记本的分辨率
            #   （1366x768）这种小的，就默认按 200 启动；台式（1920x1080）按中间档；
            #   2K 就按最大档」。⇒ **每次启动先量屏幕再定档**（分辨率跟硬件走，
            #   插拔投影/换机器就会变，绝不能写死）。`--size N` 仍然优先（自检/调试用）。
            try:
                sz0, _why = SCR.pick_size()
                out(u"[screen] %s ⇒ 默认 %d px" % (_why, sz0))
            except Exception as _e:
                out(u"[screen] 测量失败（用默认 %d px）：%r" % (sz0, _e))
        mutex = kernel32.CreateMutexW(None, False, "FairyPetV2_SINGLETON")
        if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
            # ★★ 原来这里直接 sys.exit(0)。后果：只要有任何残留实例占着这把锁
            #   （比如早先测试留下的、窗口对用户不可见的实例），主人双击启动器就会
            #   **一声不响地退出** —— 没窗口、没提示、连日志都没有。实测就是这个把
            #   主人坑了（"双击没反应啊"）。
            #   现在：先请已有实例亮出来；它不响应就请它退出；再赖着才按 pid 强杀。
            if _poke_existing():
                out("已有实例在运行 → 已请它归位并亮出来，本次不再开新窗口")
                sys.exit(0)
            # ★★★ 2026-09-22 修一个**看得见的 bug**：原来这里"仍然启动新实例" ⇒
            #   两个窗口同时存在，各自弹各自的卡片 —— 主人截图里那张"**重叠的对话框**"
            #   就是这么来的（两张卡错开几十像素、文字还被窄画布切掉一截）。
            #   ⇒ 启动新实例之前，必须**确保旧的真的死了**：先 stop.txt 请它退，
            #     等心跳停；还不停就照 heartbeat 里的 pid 强杀（精确，不误伤别的程序）。
            log_err("singleton", RuntimeError(
                "已有实例占着单实例锁但没响应 poke → 先请它退出（不再直接开第二个窗口）"))
            _retire_existing()
        # ★★ 2026-09-23：首次烘焙要 90~100 s，而烘焙就发生在 `FairyPet()` 里面
        #   ⇒ 先竖一块**倒计时窗**（`fairy_boot.Splash`），把烘焙进度喂给它，
        #   别让主人双击之后对着黑屏干等（主人原话：「双击 start，平面应该先出现倒计时窗口」）。
        #   ★ 有缓存时 0.3 s 就载入完 ⇒ splash 在 `show_after` 之前一个字都不显示，不会闪。
        #   ★★ 2026-09-23：尺寸必须用**这一档的真实画布边长**（`canvas_px(sz0)`：
        #     200→284 / 260→370 / 320→456），不能写死 370 —— 否则换了档位，
        #     倒计时窗与随后的开机动画窗口尺寸对不上（会"缩一下"）。
        try:
            out(SCR.report(sz0))            # ★ 启动先把"屏幕体检"写进日志（主人要的"先跑一次"）
        except Exception as _e:
            out(u"[screen] 体检报告失败：%r" % (_e,))
        splash = None
        if BOOT is not None and "--hidden" not in a:
            try:
                splash = BOOT.Splash(canvas_px(sz0))   # ★ 内部按**主屏**几何中心定位
                if not splash.begin():
                    splash = None
            except Exception as e:
                log_err("splash", e)
                splash = None
        pet = FairyPet(size=sz0, bake_progress=(splash.tick if splash is not None else None))
        if splash is not None:
            try:
                splash.close()
            except Exception:
                pass
            splash = None
        # ★ 开机动画：`--hidden`（离屏自检）不播；`--no-boot` 可手动跳过。
        #   ★★ 2026-09-23：改由 `run(boot=True)` 在**建好桌宠窗口之后**播 ——
        #     动画窗口（后创建，Z 序在上）盖住桌宠，白光向中心擦除时露出的是
        #     **真桌宠窗口**，中间不再夹一层"现场合成的假主体"。
        _keep = ("--hidden" not in a and "--no-boot" not in a)
        _play = bool(_keep and pet.boot_anim)
        if _keep and not pet.boot_anim:
            out(u"[boot] 菜单里设成「跳过」⇒ 本次开机不播动画，直接落在默认位"
                u"（想改回来：右键菜单 →「开机」→ 播放动画，然后重启）")
        pet.run(smoke=sm, show=("--hidden" not in a), boot=_play)
