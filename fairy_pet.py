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
import ctypes
from ctypes import wintypes

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import dsh_mascot as M
from fairy_layers import FastMascot, bgr

# ---------------------------------------------------------------- 路径
STATE_FILE = os.path.join(BASE, "state.json")
POS_FILE = os.path.join(BASE, "pos.json")
BEAT = os.path.join(BASE, "heartbeat.txt")
ERRLOG = os.path.join(BASE, "pet_error.log")
STOP_FILE = os.path.join(BASE, "stop.txt")
POKE_FILE = os.path.join(BASE, "poke.txt")      # ★ 再次双击启动时，用它叫醒已在运行的实例
NOTIFY_FILE = os.path.join(BASE, "notify.json")  # ★ 任务完成通知：我干完活写它，桌宠弹通知卡
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
AUTO_LOOP_S = 60.0          # 「60 秒循环」周期：自动在常态/工作态之间来回切（演示用）


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


def smoothstep(u):
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


# ================================================================ 主程序
class FairyPet:
    def __init__(self, size=SIZE):
        self.dpi = self._dpi_scale()
        self.px = int(round(size * self.dpi)) if FOLLOW_DPI else int(size)
        self.px = max(120, min(640, self.px))
        # 渲染器（大小变化时重建；缓存命中则秒开）
        self.R = FastMascot(size=self.px, ss=2, cache_dir=CACHE_DIR, verbose=True)
        self.win = self.R.N      # ★ 窗口/画布边长；比 self.px 大（四周是辉光的发散空间）

        self.x, self.y, self.topmost = self._load_pos()   # topmost 也持久化（跟位置同文件）
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
        self.drag_slider = None     # 正在拖动的滑块 key（"size"/"mode"/"auto"/"state"）
        self.bubble = None
        self.bubble_until = 0.0
        # ★ 任务完成通知（对话框式卡片）：我干完活写 notify.json，桌宠读一次就弹
        self.notify_text = None
        self.notify_t0 = 0.0
        self.notify_secs = 12.0
        self.notify_poll_at = 0.0
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

    @staticmethod
    def _mkfont(sz):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", sz)
        except Exception:
            return ImageFont.load_default()

    def _work_area(self):
        r = RECT()
        try:
            user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
            return r.left, r.top, r.right, r.bottom
        except Exception:
            return 0, 0, 1920, 1080

    @staticmethod
    def _virtual_screen():
        """多屏：虚拟桌面范围（副屏在右时 x 会 > 主屏宽；在左时为负）"""
        try:
            g = user32.GetSystemMetrics
            return (g(76), g(77), g(76) + g(78), g(77) + g(79))   # XVIRT/YVIRT/CXVIRT/CYVIRT
        except Exception:
            return 0, 0, 1920, 1080

    def _load_pos(self):
        """→ (x, y, topmost)。

        ★ 置顶开关跟位置存在同一个文件里 ⇒ **重启后保持你的选择**（不然每次都要重设）。
        """
        try:
            d = json.load(open(POS_FILE, "r", encoding="utf-8"))
            return int(d["x"]), int(d["y"]), bool(d.get("topmost", True))
        except Exception:
            l, t, r, b = self._work_area()
            off = (self.win - self.px) // 2      # 画布比本体大，抵消掉这圈空白
            return r - off - self.px - 20, b - off - self.px - 16, True

    def _save_pos(self):
        try:
            json.dump({"x": self.x, "y": self.y, "topmost": bool(self.topmost)},
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
    def _go_home(self, say=True):
        """回到默认位置（右下角）。本体贴角，画布多出来的那圈空白抵掉。"""
        l, t, r, b = self._work_area()
        off = (self.win - self.px) // 2
        self.x, self.y = r - off - self.px - 20, b - off - self.px - 16
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

    def _target_state(self):
        if not self.follow and self.manual:
            return self.manual
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
        self.bubble = text
        self.bubble_until = time.perf_counter() + secs

    # ------------------------------------------------------------ 自绘 UI（滑块式菜单）
    def _menu_rows(self):
        """菜单行定义。kind = title / slider / button。

        ★ 主人 2026-09-22 的菜单要求：
          · 尺寸 → 三档滑块（小 / 中 / 大），右侧括号里带实际像素
          · 自动 / 手动 → 两档滑块
          · 切到**手动**时，下方**增加**一行：状态（常态 / 工作态）
          · 切到**自动**时，下方**增加**一行：自动（跟随 WorkBuddy / 60 秒循环）
        """
        rows = [{"kind": "title",
                 "text": "Fairy · %s" % ("自动" if self.follow else "手动")}]
        rows.append({"kind": "slider", "key": "size", "label": "尺寸",
                     "segs": ["小", "中", "大"], "idx": int(self.size_idx),
                     "extra": "%d px" % self.px})
        rows.append({"kind": "slider", "key": "mode", "label": "模式",
                     "segs": ["自动", "手动"], "idx": 0 if self.follow else 1})
        if self.follow:
            rows.append({"kind": "slider", "key": "auto", "label": "自动",
                         "segs": ["跟随 WorkBuddy", "60 秒循环"],
                         "idx": 0 if self.auto_mode == "follow" else 1})
        else:
            rows.append({"kind": "slider", "key": "state", "label": "状态",
                         "segs": ["常态", "工作态"],
                         "idx": 1 if (self.manual or self.cur_state) == "working" else 0})
        rows.append({"kind": "slider", "key": "topmost", "label": "置顶",
                     "segs": ["开", "关"], "idx": 0 if self.topmost else 1})
        rows.append({"kind": "button", "key": "home", "text": "归位（右下角）"})
        rows.append({"kind": "button", "key": "quit", "text": "退出 Fairy"})
        return rows

    def _slider_need(self, d, segs, extra=None):
        """一行滑块需要多宽（按文字实测宽 + 留白）"""
        n = sum(int(d.textlength(s, font=self.f12)) + 2 * SEG_PAD for s in segs)
        n += 2 * (len(segs) - 1) + 10
        if extra:
            n += int(d.textlength(extra, font=self.f12)) + 8
        return n

    def _menu_layout(self):
        """算菜单几何与每行/每段的命中区（菜单打开时才调，纯小算术）。

        ★ 段宽按**字体实测宽度**给 ⇒ 换字体、改文案都不会溢出（固定宽度会）。
        """
        rows = self._menu_rows()
        d = self._md
        lab_w = 0
        for r in rows:
            if r["kind"] == "slider":
                lab_w = max(lab_w, int(d.textlength(r["label"], font=self.f12)))
        x_slider = MENU_PAD + lab_w + 10
        need = 0
        for r in rows:
            if r["kind"] == "slider":
                need = max(need, self._slider_need(d, r["segs"], r.get("extra")))
        # ★ 把**另一种模式**的那一行也算进来 ⇒ 菜单宽度恒定，切自动/手动时不会横向跳
        for cand in (["跟随 WorkBuddy", "60 秒循环"], ["常态", "工作态"]):
            need = max(need, self._slider_need(d, cand, None))
        w = min(x_slider + need + MENU_PAD, max(200, self.win - 12))
        h = MENU_PAD * 2 + MENU_TITLE_H + MENU_ROW_H * (len(rows) - 1)
        y0 = max(6, int(self.win * 0.035))
        L = {"x0": (self.win - w) // 2, "y0": y0, "w": w, "h": h, "rows": []}
        y = y0 + MENU_PAD
        for r in rows:
            rh = MENU_TITLE_H if r["kind"] == "title" else MENU_ROW_H
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

    # ------------------------------------------------------------ 任务完成通知
    # 机制一句话：**我（fairy）干完活写一个 notify.json，桌宠读一次就弹一张卡片。**
    # 这就是"你在 Agent 里干活 → 干完 → 桌宠弹框告诉你"的全部链路，不涉及任何网络/权限。
    def _read_notify(self):
        """读 notify.json。★ 消费即删（含坏文件）—— 通知是一次性的，重启不会重弹。"""
        if not os.path.exists(NOTIFY_FILE):
            return None
        txt, secs = "", 12.0
        try:
            with open(NOTIFY_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            txt = str(d.get("text", "")).strip()
            secs = max(3.0, float(d.get("secs", 12.0)))
        except Exception as e:
            log_err("read_notify", e)
        try:
            os.remove(NOTIFY_FILE)      # ★ 坏文件也要删：否则每 0.5 s 报一次错、刷满日志
        except Exception:
            pass
        return (txt, secs) if txt else None

    def _note_span(self, now):
        """返回 (文本, 不透明度)；没通知或已过期 → None。淡入 0.25 s、淡出 0.5 s。"""
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
        return self.notify_text, op

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

    def _draw_note(self, d, S, txt, op):
        """通知卡（对话框式）。★ 独立成方法，不塞进 `_overlay` —— 那个已经管菜单+气泡了。"""
        A = int(round(240 * op))        # 卡片底
        T = int(round(255 * op))        # 字与描边
        m, pad, lh, head = 16, 14, 21, 20
        lines = self._wrap_text(txt, self.f13, S - 2 * m - 2 * pad)
        bh = pad + head + len(lines) * lh + 6
        bw = S - 2 * m
        bx, by = m, S - bh - m
        d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=11,
                            fill=bgr("#12141c") + (A,), outline=bgr("#3f4a70") + (T,))
        d.rounded_rectangle([bx + 3, by + 9, bx + 6, by + bh - 9], radius=2,
                            fill=bgr("#3f6bd8") + (T,))          # 左侧强调竖线
        d.ellipse([bx + pad, by + 10, bx + pad + 8, by + 18], fill=bgr("#5b8cf0") + (T,))
        d.text((bx + pad + 14, by + 7), "Fairy", fill=bgr("#8fa0c0") + (T,), font=self.f12)
        d.line([(bx + pad, by + head + 5), (bx + bw - pad, by + head + 5)],
               fill=bgr("#333a52") + (T,))
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
        if note:
            self._draw_note(d, S, note[0], note[1])
        if bubble:
            tw = d.textlength(bubble, font=self.f13)
            bw = int(tw) + 26
            bh = 28
            bx = (S - bw) // 2
            by = S - bh - int(S * 0.06)
            d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=9,
                                fill=bgr("#12141c") + (238,),
                                outline=bgr("#394060") + (255,))
            d.text((bx + 13, by + 6), bubble, fill=bgr("#e6ebf5") + (255,), font=self.f13)
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

    def tick(self):
        now = time.perf_counter()
        self._tick_n += 1
        if self._last_tick:
            self._period_max = max(self._period_max, (now - self._last_tick) * 1000.0)
        self._last_tick = now
        self._tick_ts.append(now)
        self._tick_ts = [t for t in self._tick_ts if now - t <= 1.0]
        _t_in = time.perf_counter()

        # --- 心跳 / 停止开关 ---
        if self._tick_n % 60 == 1:
            try:
                # ★ 心跳必须带上 pid / hwnd / vis：否则"进程活着、窗口却看不见"这种故障
                #   外部完全无从判断（主人报"双击没反应"时，就吃了这个亏）。
                with open(BEAT, "w", encoding="utf-8") as f:
                    f.write("tick=%d  %s  pid=%d  hwnd=0x%X  vis=%d  pos=(%d,%d)  px=%d  "
                            "win=%d  state=%s  open_t=%.2f  fps=%.1f  cost=%.2fms  "
                            "err=%d  最差帧=%.1fms  follow=%s\n"
                            % (self._tick_n, time.strftime("%H:%M:%S"), os.getpid(),
                               self.hwnd or 0,
                               int(bool(self.hwnd)
                                   and user32.IsWindowVisible(self.hwnd)),
                               self.x, self.y, self.px, self.win, self.cur_state,
                               self.open_t, float(len(self._tick_ts)), self._cost,
                               self._fail, self._period_max, self.follow))
            except Exception:
                pass
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
        if self._tick_n % 30 == 0 and os.path.exists(POKE_FILE):
            try:
                os.remove(POKE_FILE)
            except Exception:
                pass
            self._go_home(say=False)
            ok = self._force_show("poke")
            self._say("我在这儿。" if ok else "窗口没能显示出来，看 pet_error.log。", 2.6)
            if not ok:
                log_err("poke", RuntimeError("poke 之后窗口仍不可见"))

        # --- 自愈：窗口若被系统/别的程序藏起来，自己亮回来 ---
        if (self._show_requested and self.hwnd and self._tick_n % 120 == 0
                and not user32.IsWindowVisible(self.hwnd)):
            log_err("selfcheck", RuntimeError("发现窗口不可见 → 重新显示"))
            self._force_show("selfcheck")

        # --- ★ 任务完成通知：我干完活写 notify.json，这里读到就弹卡（无条件轮询，与自动/手动无关）---
        if now - self.notify_poll_at > 0.5:
            self.notify_poll_at = now
            n = self._read_notify()
            if n:
                self.notify_text, self.notify_secs = n
                self.notify_t0 = now
                self.bubble = None          # 通知优先：清掉正在显示的普通气泡
                self._force_show("notify")  # ★ 万一窗口被系统藏了，弹卡前先把它亮出来

        # --- 自动状态的来源：跟随 state.json，或本地「60 秒循环」（演示）---
        if self.auto_mode == "loop60":
            if now >= self.auto_loop_at:
                self.auto_state = "working" if self.auto_state == "idle" else "idle"
                self.auto_loop_at = now + AUTO_LOOP_S
                self._say("演示：%s" % ("工作态" if self.auto_state == "working" else "常态"), 2.2)
        elif now - self.state_read_at > 0.5:
            self.state_read_at = now
            st, note = self._read_state_json()
            if st != self.auto_state:
                self.auto_state = st
                if note:
                    self._say(note, 3.0)
        self._set_state(self._target_state(), now)

        t_ms = (now - self.t0) * 1000.0
        op = self._open_t(now)
        fl = self._flicker_op(now)
        self.R.draw(t_ms=t_ms, open_t=op, flicker_op=fl)

        panel = None
        if self.menu_open:
            p = POINT()
            user32.GetCursorPos(ctypes.byref(p))
            L = self._menu_layout()
            self.hover = self._hit_menu(p.x - self.x, p.y - self.y, L)
            panel = {"layout": L, "hover": self.hover}
            if now - self.menu_at > 8.0 and self.hover[0] < 0:
                self.menu_open = False
                panel = None
        bubble = self.bubble if (self.bubble and now < self.bubble_until) else None
        if bubble is None:
            self.bubble = None
        ov = self._overlay(panel, bubble, self._note_span(now))

        bgra, alpha = self.R.to_bgra(ov=ov)
        self._blit(bgra, alpha)
        self._cost = 0.9 * self._cost + 0.1 * (time.perf_counter() - _t_in) * 1000.0

    # ------------------------------------------------------------ 消息
    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_NCHITTEST:
                p = POINT()
                user32.GetCursorPos(ctypes.byref(p))
                lx, ly = p.x - self.x, p.y - self.y
                if 0 <= lx < self.win and 0 <= ly < self.win and self._alpha[ly, lx] > 10:
                    return HTCLIENT
                return HTTRANSPARENT
            if msg == WM_LBUTTONDOWN:
                p = POINT()
                user32.GetCursorPos(ctypes.byref(p))
                lx, ly = p.x - self.x, p.y - self.y
                if self.menu_open:
                    i, k = self._hit_menu(lx, ly)
                    if i < 0:
                        self.menu_open = False
                        return 0
                    it = self._menu_layout()["rows"][i]
                    if it["kind"] == "slider":
                        # ★ 按住后继续左右拖也能改（"点击滑动"）⇒ 必须 SetCapture，
                        #   否则指针一移出窗口就收不到 WM_MOUSEMOVE。
                        self.drag_slider = it["key"]
                        user32.SetCapture(hwnd)
                        if k >= 0:
                            self._set_slider(it["key"], k)
                    elif it["kind"] == "button":
                        self._do_menu_key(it["key"])
                    return 0
                self.dragging = True
                self.drag_off = (lx, ly)
                user32.SetCapture(hwnd)
                return 0
            if msg == WM_MOUSEMOVE:
                if self.dragging:
                    p = POINT()
                    user32.GetCursorPos(ctypes.byref(p))
                    self.x = p.x - self.drag_off[0]
                    self.y = p.y - self.drag_off[1]
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
                self.hover = -1
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
                    self._say("手动（%s）" % ("工作态" if self.manual == "working" else "常态"), 2.2)
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

    def _do_menu_key(self, key):
        """菜单里的"按钮行"（归位 / 退出）"""
        if key == "home":
            self.menu_open = False
            self._go_home()
            self._say("已归位", 1.6)
        elif key == "quit":
            self.menu_open = False
            self._quit = True
            user32.PostQuitMessage(0)

    def _switch_size(self, new):
        """切换显示尺寸：重建渲染器（有缓存则秒开），并重建 DIB + 窗口尺寸"""
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
        gdi32.DeleteObject(self._dib)
        gdi32.DeleteDC(self._hdc)
        self._init_dib()
        vl, vt, vr, vb = self._virtual_screen()        # ★ 按虚拟桌面夹取，别把副屏上的它拽回主屏
        off = (self.win - self.px) // 2
        self.x = max(vl + 4, min(self.x, vr - off - self.px - 4))
        self.y = max(vt + 4, min(self.y, vb - off - self.px - 4))
        user32.SetWindowPos(self.hwnd, None, self.x, self.y, self.win, self.win, 0x0014)
        self._save_pos()
        self._say("%d px 就绪" % new, 2.0)

    # ------------------------------------------------------------ 运行
    def run(self, smoke=0.0, show=True, max_frames=0, seconds=0.0):
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
        self.hwnd = user32.CreateWindowExW(
            ex_style, cls, "Fairy", WS_POPUP, self.x, self.y, self.win, self.win,
            None, None, hinst, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._init_dib()
        self._show_requested = bool(show)
        if show:
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
        self.tick()
        st, _ = self._read_state_json()
        self.auto_state = st
        self.cur_state = st
        self.open_t = 0.0 if st == "working" else 1.0

        PM_REMOVE = 0x0001
        WM_QUIT = 0x0012
        msg = wintypes.MSG()
        interval = 1.0 / FPS
        last = time.perf_counter()
        t_start = last
        next_t = last
        t_end = (last + (seconds or smoke)) if (seconds > 0 or smoke > 0) else 0.0
        out("Fairy v2 启动：hwnd=%s  本体 %d px / 窗口 %d px  dpi=%.2f  pos=(%d,%d)  state=%s"
            % (self.hwnd, self.px, self.win, self.dpi, self.x, self.y, st))
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


def _poke_existing(timeout=2.5):
    """已有实例在跑时的处理：写 poke.txt 请它把窗口亮出来并归位。
    返回 True = 它响应了（本次可以安全退出，主人也已经"看见了"反应）。

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
        mutex = kernel32.CreateMutexW(None, False, "FairyPetV2_SINGLETON")
        if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
            # ★★ 原来这里直接 sys.exit(0)。后果：只要有任何残留实例占着这把锁
            #   （比如早先测试留下的、窗口对用户不可见的实例），主人双击启动器就会
            #   **一声不响地退出** —— 没窗口、没提示、连日志都没有。实测就是这个把
            #   主人坑了（"双击没反应啊"）。
            #   现在：先请已有实例亮出来；只有它真的不响应才继续开新实例，并记日志。
            if _poke_existing():
                out("已有实例在运行 → 已请它归位并亮出来，本次不再开新窗口")
                sys.exit(0)
            log_err("singleton", RuntimeError(
                "已有实例占着单实例锁但没响应 poke（可能是不显示/卡住的残留实例）"
                " → 仍然启动新实例"))
        FairyPet(size=sz0).run(smoke=sm, show=("--hidden" not in a))
