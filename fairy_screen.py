# -*- coding: utf-8 -*-
u"""屏幕测量 —— 启动流程里**先跑它**，决定「默认档位 / 居中点 / 默认落点」。

主人 2026-09-23 的原话：
  「窗口定位需个测量程序先跑 —— 如果发现是笔记本电脑的分辨率（1366×768）这种小的，
    就默认按 200 的启动；如果是台式电脑显示器（1920×1080）这种，就按中间档启动；
    如果屏幕是 2K 的，就按最大的档位启动。然后根据窗口大小，定位出真正的中心点。
    哦，还要先识别有几块屏幕，直接默认在主屏幕上启动。」

⇒ 三件事，全部收在这一个模块里（**纯计算 + 一次系统查询，不建窗口**）：

 ① **数屏**：`EnumDisplayMonitors` 数出显示器块数；主屏 = 系统标了 `MONITORINFOF_PRIMARY`
    的那块（通常原点在 (0,0)）。**所有定位都落在主屏上**（副屏只在"夹取"时参与）。
 ② **定档**：按**主屏**分辨率三档（见 `SIZE_RULES`）。★ 分辨率跟硬件走，插拔投影/换机就会变
    ⇒ **每次启动都重新测**，不写死。
 ③ **定位**：居中用**窗口（画布）尺寸**算（`center()`）；默认落点在主屏**工作区**右下，
    并且**可见内容整体躲开任务栏**（`home()` —— 见下面 `CONTENT_BOTTOM_F` 的说明）。

单独跑（主人要的"先跑一次看看"）：

    python fairy_screen.py            # 人读的体检报告
    python fairy_screen.py --json     # 给脚本/我读
    python fairy_screen.py --pet 320  # 顺便列出 320 档的窗口尺寸与两个位置
"""
from __future__ import print_function

import ctypes
import ctypes.wintypes as wintypes
import json
import sys
import time

user32 = ctypes.windll.user32

# ---------------------------------------------------------------- 档位规则
#   ★ 判据用**主屏分辨率**；三条从上往下匹配，命中即止（最后一条兜底）。
#   ★ 想让新机器有别的行为，改这张表就行 —— 不要在调用方写 if/else。
SIZE_RULES = (
    (u"小屏（宽≤1600 或 高≤900；典型 1366x768 / 1440x900 / 1280x800）",
     lambda w, h: w <= 1600 or h <= 900, 200),
    (u"全高清（宽≤1920 或 高≤1200；典型 1920x1080 / 1680x1050 / 1920x1200）",
     lambda w, h: w <= 1920 or h <= 1200, 260),
    (u"2K 及以上（2560x1440 / 3440x1440 / 3840x2160）",
     lambda w, h: True, 320),
)

#   默认落点的边距（与 `_home_xy` 原来的 20 / 16 一致，改这里就够）
HOME_MARGIN_X = 20
HOME_MARGIN_Y = 16

#   画布里**可见内容**的左右留白（通知卡/进度条都按它画，见 `fairy_pet._draw_note`
#   的 `m = 16`）——`fit()` 用它保证"卡片贴到屏幕边时也不越界"。
CARD_INSET = 16
#   再多留一点，别让边框正好压在屏幕最外一列像素上。
EDGE_PAD = 2

# ★★★ 底边那条"实心内容"离画布底有多近（占画布边长的比例）。
#   画布是**正方形**，可见内容只占中间一块，但 **进度条 + `FAIRY WORKING` 副标题
#   是贴着画布底部画的**（实测 `fairy_bar.BarView`：本体 200/260/320 ⇒
#   画布 284/370/456，副标题底边 277/361/445 ⇒ 只剩 **7/9/11 px**，比例 0.0243）。
#   ⇒ 定位/夹取如果只按"本体贴角"，副标题就会被推到**任务栏**上
#     （主人 2026-09-23 截图：`FAIRY WORKING` 压在状态栏上）。
#   ⇒ 默认落点与夹取一律把这块算成"可见内容"。
CONTENT_BOTTOM_F = 0.0243


def content_inset_b(win_px):
    u"""可见内容（进度条/副标题）底边离画布底边的像素数 → `int`。"""
    return max(2, int(round(int(win_px) * CONTENT_BOTTOM_F)))


# ---------------------------------------------------------------- 显示器
class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


MONITORINFOF_PRIMARY = 0x00000001
#   `MonitorFromPoint` 的"给我离这个点最近的那块屏"（屏外/缝隙里的点也能拿回一块）。
MONITOR_DEFAULTTONEAREST = 0x00000002
#   `monitors()` 的结果缓存多久 —— 拖动是 60 fps，没必要每帧枚举一遍显示器。
MON_CACHE_S = 1.0
#   最近一次枚举显示器时吃到的异常（正常时是空表）。
#   ★ 只记不写：桌宠跑在 pythonw 下 `sys.stderr` 是 `None`，写它会当场崩（铁律）
#     ⇒ 由调用方（`report()` / `fairy_pet`）负责呈现，**绝不静默吞掉**。
_ENUM_ERR = []
_CACHE = {"t": 0.0, "mons": []}
_MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                                      ctypes.POINTER(_RECT), ctypes.c_double)


def monitors(refresh=False):
    u"""→ `[{hmon, left, top, right, bottom, w, h, work, primary}, ...]`（按枚举顺序）。

    ★★ 2026-09-28 **多屏化**（主人："希望主副桌面都能去，甚至 3 屏 4 屏都可以去，
       但是不要挡到状态栏"）。每个元素比原来多两样：
         · `hmon`：这块屏的句柄 —— `MonitorFromPoint()` 拿回来的就是它，用来"认屏"；
         · `work`：`(l, t, r, b)` —— **这块屏自己的工作区**，已躲开**它自己那块屏上**的任务栏。
           以前只有 `SPI_GETWORKAREA`，而它**永远只给主屏** ⇒ 根本没办"按屏取"。
    ★ 结果**缓存 `MON_CACHE_S` 秒**（`refresh=True` 强制重查）：拖动是 60 fps，
      每帧枚举一遍显示器没必要；插拔显示器 1 秒内被发现也够用。
      ★ 返回的是**缓存里的同一份 list/dict** ⇒ 调用方**只读**，别就地改。
    ★ 枚举失败（极罕见）退化成"一块主屏"，但**把原因记进 `_ENUM_ERR`**（不静默）——
      启动流程不能被它挡住，可"失败了却什么都不说"就是下次要花几轮查的坑。
    """
    now = time.time()
    if not refresh and _CACHE["mons"] and (now - _CACHE["t"]) < MON_CACHE_S:
        return _CACHE["mons"]
    del _ENUM_ERR[:]
    out = []

    def cb(hmon, hdc, lprc, data):
        try:                       # ★ 回调里**绝不能**抛出去（ctypes 会静默吞掉）
            mi = _MONITORINFO()
            mi.cbSize = ctypes.sizeof(_MONITORINFO)
            if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                r, wk = mi.rcMonitor, mi.rcWork
                out.append({"hmon": int(hmon),
                            "left": r.left, "top": r.top,
                            "right": r.right, "bottom": r.bottom,
                            "w": r.right - r.left, "h": r.bottom - r.top,
                            "work": (wk.left, wk.top, wk.right, wk.bottom),
                            "primary": bool(mi.dwFlags & MONITORINFOF_PRIMARY)})
            else:
                _ENUM_ERR.append(u"GetMonitorInfoW(hmon=%s) 返回 0" % hmon)
        except Exception as e:
            _ENUM_ERR.append(repr(e))
        return 1

    try:
        user32.EnumDisplayMonitors(0, None, _MONITORENUMPROC(cb), 0)
    except Exception as e:
        _ENUM_ERR.append(repr(e))
    if not out:
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        if not _ENUM_ERR:
            _ENUM_ERR.append(u"EnumDisplayMonitors 一块屏都没返回")
        out = [{"hmon": 0, "left": 0, "top": 0, "right": w, "bottom": h,
                "w": w, "h": h, "work": (0, 0, w, h), "primary": True}]
    # ★ 系统没标主屏时（少见）把"包含原点"的那块当主屏
    if not any(m["primary"] for m in out):
        for m in out:
            if m["left"] <= 0 <= m["right"] and m["top"] <= 0 <= m["bottom"]:
                m["primary"] = True
                break
        else:
            out[0]["primary"] = True
    for m in out:                  # 兜底：任何记录都必须有 work（正常路径不会走到）
        if "work" not in m:
            m["work"] = (m["left"], m["top"], m["right"], m["bottom"])
    _CACHE["t"], _CACHE["mons"] = time.time(), out
    return out


def primary():
    u"""→ 主屏矩形 dict（含 `w/h`）。"""
    for m in monitors():
        if m["primary"]:
            return m
    return monitors()[0]


def virtual():
    u"""→ (l, t, r, b)：虚拟桌面范围（副屏在右时 r > 主屏宽；在左时 l < 0）。"""
    g = user32.GetSystemMetrics
    return int(g(76)), int(g(77)), int(g(76) + g(78)), int(g(77) + g(79))


def work_area():
    u"""→ (l, t, r, b)：**主屏**工作区（已躲开任务栏）。

    ★ `SPI_GETWORKAREA` 拿到的**永远**是主屏工作区 —— 这正是"归位/开机落在主屏"想要的。
    ★★ 要"窗口所在那块屏"的工作区 ⇒ 用 `work_area_at(x, y)`（2026-09-28 新增）。
    """
    r = _RECT()
    try:
        if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            return int(r.left), int(r.top), int(r.right), int(r.bottom)
    except Exception:
        pass
    p = primary()
    return p["left"], p["top"], p["right"], p["bottom"]


def monitor_at(x, y, mons=None):
    u"""→ **包含点 (x,y) 的那块屏**（点在所有屏之外/缝隙里 ⇒ 最近的那块）。

    ★★ 判据用 `MonitorFromPoint(…, MONITOR_DEFAULTTONEAREST)` —— 2026-09-28 实测：
      ① 每块屏**自己的中心点**都能稳定拿回自己（2/2 通过）；
      ② 屏外与缝隙里的点回最近屏（跨屏拖动正需要这个）。
    ★ 拿不到时两级退路（按矩形包含 → 按中心距离最近），**绝不抛异常**（每帧都会被调）。
    """
    mons = mons if mons is not None else monitors()
    try:
        h = int(user32.MonitorFromPoint(_POINT(int(x), int(y)),
                                        MONITOR_DEFAULTTONEAREST))
        for m in mons:
            if m.get("hmon") == h:
                return m
    except Exception:
        pass
    for m in mons:                     # 退路①：点落在哪块屏的矩形里
        if m["left"] <= x < m["right"] and m["top"] <= y < m["bottom"]:
            return m
    best, best_d = mons[0], None       # 退路②：中心距离最近的那块
    for m in mons:
        cx = (m["left"] + m["right"]) / 2.0
        cy = (m["top"] + m["bottom"]) / 2.0
        d = (cx - x) ** 2 + (cy - y) ** 2
        if best_d is None or d < best_d:
            best, best_d = m, d
    return best


def work_area_at(x, y):
    u"""→ (l, t, r, b)：**点 (x,y) 所在那块屏**的工作区（那块屏的任务栏已躲开）。

    ★★ 这是"桌宠能去任何一块屏、但不压那块屏的任务栏"的**唯一真源**：
      Windows 给每块屏都单独算了 `rcWork`。2026-09-28 实测（1920+1920、任务栏都在底部）：
        主屏 rcWork (0,0)-(1920,1040) ｜ 副屏 rcWork (1920,-9)-(3840,1031)
      ⇒ 两屏都各自躲开了自己那条 40 px 的任务栏。
    """
    m = monitor_at(x, y)
    return tuple(m["work"])


def taskbar_bands(m):
    u"""→ 这块屏上"被任务栏占掉"的矩形列表（`rcMonitor` 减去 `rcWork`），每条 `(l,t,r,b)`。

    任务栏在底/顶/左/右都能覆盖；**没有任务栏的屏返回空表**（`rcWork == rcMonitor`）
    ⇒ 拖动时自然不会被推。
    ★ 上/下带与左/右带会**重叠**（左/右带铺满整高）—— 只用来判相交，重叠无害。
    """
    ml, mt, mr, mb = m["left"], m["top"], m["right"], m["bottom"]
    wl, wt, wr, wb = m["work"]
    out = []
    if wt > mt:
        out.append((ml, mt, mr, wt))
    if wb < mb:
        out.append((ml, wb, mr, mb))
    if wl > ml:
        out.append((ml, mt, wl, mb))
    if wr < mr:
        out.append((wr, mt, mr, mb))
    return out


def all_taskbar_bands(mons=None):
    u"""→ **所有屏**的任务栏带（`taskbar_bands` 的合集）—— 拖动时一次判完。"""
    mons = mons if mons is not None else monitors()
    out = []
    for m in mons:
        out.extend(taskbar_bands(m))
    return out


def primary_anchor():
    u"""→ 主屏中心点 `(x, y)`。

    凡是"必须落在主屏"的场合（**归位 / 开机落点 / 开机动画居中**）都拿它当锚点 ——
    这样 `fit()` 认屏时会认回主屏，而不是"窗口现在在哪块屏"。
    """
    p = primary()
    return ((p["left"] + p["right"]) // 2, (p["top"] + p["bottom"]) // 2)


# ---------------------------------------------------------------- 定档
def pick_size(w=None, h=None):
    u"""→ `(size, reason)`。`w/h` 省略时用主屏分辨率。"""
    if w is None or h is None:
        p = primary()
        w, h = p["w"], p["h"]
    for why, ok, size in SIZE_RULES:
        if ok(int(w), int(h)):
            return int(size), why
    return 260, u"兜底"


# ---------------------------------------------------------------- 定位
def center(win_px, use_work=False):
    u"""把**边长 `win_px` 的窗口**摆在主屏正中的左上角 → `(x, y)`。

    ★★ 主人要的"真正的中心点"：`x = 主屏中心 − 窗口/2`。
      注意传的是**窗口（画布）尺寸**，不是本体尺寸 —— 画布比本体每边多一圈辉光余量，
      用本体尺寸算会让整只宠物**偏向左上**（这里是靠 `canvas_px()` 拿到真实窗口边长的）。
    ★ `use_work=True` 时改用主屏**工作区**中心（要躲任务栏的场合）。
    """
    if use_work:
        l, t, r, b = work_area()
    else:
        p = primary()
        l, t, r, b = p["left"], p["top"], p["right"], p["bottom"]
    return (int(round((l + r) / 2.0 - win_px / 2.0)),
            int(round((t + b) / 2.0 - win_px / 2.0)))


def home(win_px, pet_px, margin_x=HOME_MARGIN_X, margin_y=HOME_MARGIN_Y):
    u"""默认落点：主屏**工作区右下角** → `(x, y)`。

    ★★ 2026-09-23 主人：「压到状态栏了，默认位置**整体避开状态栏**」。
      原来纵向按"本体贴角"推（`b − off − pet − margin`），于是画布底落到 1079，
      **比任务栏顶（1040）还低 39 px** ⇒ 进度条 + `FAIRY WORKING` 压在状态栏上。

      ⇒ 现在这么定：
        · **横向**照旧让**本体**贴右（右边溢出的只是画布那圈**透明**余量，肉眼无感）；
        · **纵向**让**可见内容**（副标题底边）落在工作区里 ⇒
          `y = 工作区底 − 画布 + content_inset_b(画布) − margin_y`。
      两个方向都用 `fit()` 再兜一道（`use_work=True`：绝不越到任务栏上）。
    """
    l, t, r, b = work_area()
    off = (win_px - pet_px) // 2
    # ★★ 2026-09-28 主人拍板：「**归位和开机全都默认在主屏幕右下**」
    #   ⇒ 锚点**显式**给主屏中心（不给的话 `fit()` 会认成"窗口现在在哪块屏"，
    #     于是人在副屏点归位、它就留在副屏 —— 与主人要的不符）。
    return fit(int(r - off - pet_px - margin_x),
               int(b - win_px + content_inset_b(win_px) - margin_y),
               win_px, use_work=True, anchor=primary_anchor())


def fit(x, y, win_px, inset=None, pad=None, use_work=True, inset_b=None, anchor=None):
    u"""把窗口左上角夹进**它所在那块屏**，保证**可见内容不越界** → `(x, y)`。

    ★★ 为什么需要 `inset`（主人 2026-09-23 抓到的"往右下角移动后超出屏幕边界、
      通知框出屏"）：画布比本体每边大一圈（辉光余量），而**通知卡/进度条画在画布里、
      左右各留 `CARD_INSET` px** ⇒ 卡片的可见范围是 `[x+inset, x+win−inset]`。
      若按"窗口贴边"来夹（`x ≤ r − win`），卡片右端会正好压在屏幕外。
      ⇒ 按 `x ≤ r − win + inset − pad` 夹：允许画布那几像素**透明**余量溢出，
        卡片始终留在屏内（`pad` 再多让 2 px，别让描边正好贴在最外一列像素上）。
    ★★ 纵向**另算**（`inset_b`）：卡片/条是贴画布**底**画的（见 `CONTENT_BOTTOM_F`），
      底边留白比左右小得多 ⇒ `y ≤ b − win + inset_b − pad`。
    ★★ 2026-09-28 **按屏夹**（多屏化）：`anchor`（默认 = **窗口中心**）经 `monitor_at()`
      认屏，然后夹进**那块屏**的矩形 —— `use_work=True`（默认）⇒ 它的**工作区**，
      连**它自己那条**任务栏一起躲开。
      ⇒ "桌宠能去主屏 / 副屏 / 3 屏 / 4 屏，且在哪块屏上都不压那块屏的任务栏"。
      ★ 要"**必须落在主屏**"的场合（**归位 / 开机落点 / 开机动画**），传
        `anchor=primary_anchor()`；否则就会认成"窗口现在在哪块屏"。
      ★ 拖动**过程中**别用它 ⇒ 用 `fit_drag()`（用它会撞上"两屏交界的墙"，见那边说明）。
    ★ `use_work=False`：夹进**该屏整屏**（允许压任务栏，只在需要"能拖到最下面"时用）。
    """
    if inset is None:
        inset = CARD_INSET
    if pad is None:
        pad = EDGE_PAD
    if inset_b is None:
        inset_b = content_inset_b(win_px)
    ax, ay = anchor if anchor else (int(x) + win_px // 2, int(y) + win_px // 2)
    m = monitor_at(ax, ay)
    if use_work:
        l, t, r, b = m["work"]
    else:
        l, t, r, b = m["left"], m["top"], m["right"], m["bottom"]
    lo_x, hi_x = l - inset + pad, r - win_px + inset - pad
    lo_y, hi_y = t - inset + pad, b - win_px + inset_b - pad
    if hi_x < lo_x:                       # 窗口比屏幕还宽 ⇒ 居中（别把它推到屏外）
        lo_x = hi_x = (l + r - win_px) // 2
    if hi_y < lo_y:
        lo_y = hi_y = (t + b - win_px) // 2
    return int(max(lo_x, min(x, hi_x))), int(max(lo_y, min(y, hi_y)))


def fit_drag(x, y, win_px, inset=None, pad=None, inset_b=None):
    u"""**拖动中**（过渡态）：宽松 —— 夹进整个虚拟桌面，再只把"压到任务栏"推开。

    ★★ 为什么不能直接用 `fit()`（2026-09-28 实测账 —— ★ 结论被自己的验证脚本修正过一次）：
      `fit()` 的锚点取的是"**请求坐标**"（你想放到哪），请求越过屏边界后它会切屏
      ⇒ **它其实跨得过去**。但中间有一段"**鼠标在动、窗口不动**"的死区，出死区再**瞬移**：
        主屏往右的夹取上限 = `1920 − 370 + 16 − 2 = 1564`，
        而锚点要等请求 `x ≥ 1735` 才切到副屏
        ⇒ **死区 171 px**（主屏 200 档 128 px），随后从 1564 **瞬移**到 1906（跳 342 px）。
      ⇒ 要"贴着鼠标走"就必须用它：拖动过程中**不按单屏工作区夹**。
    ★ 代价：拖动中可以停在"两屏之间的缝隙/空白"上 —— 松手时上层会用 `fit()` 吸回最近屏。
    ★ 但**任务栏照样躲**：可见内容一旦压到任何一块屏的任务栏带，就整体推开
      （优先往上推 —— 任务栏在底部时最自然；推不动才往下推）。
    """
    if inset is None:
        inset = CARD_INSET
    if pad is None:
        pad = EDGE_PAD
    if inset_b is None:
        inset_b = content_inset_b(win_px)
    l, t, r, b = virtual()
    x = int(max(l, min(int(x), r - win_px)))
    y = int(max(t, min(int(y), b - win_px)))
    for _round in range(4):                    # 多块屏可能叠着推 ⇒ 迭代几轮
        bands = all_taskbar_bands()
        if not bands:
            break
        cx0, cy0 = x + inset, y + inset                        # 可见内容左上
        cx1, cy1 = x + win_px - inset, y + win_px - inset_b    # 可见内容右下
        up = down = 0
        for (bl, bt_, br, bb) in bands:
            if cx1 <= bl or cx0 >= br or cy1 <= bt_ or cy0 >= bb:
                continue                       # 与这条带不相交
            up = max(up, cy1 - bt_)            # 往上推：内容底边挪到带顶之上
            down = max(down, bb - cy0)         # 往下推：内容顶边挪到带底之下
        if up <= 0 and down <= 0:
            break
        ny = y - up if (up and (not down or up <= down)) else y + down
        ny = int(max(t, min(ny, b - win_px)))
        if ny == y:
            break                              # 推不动了（屏太小）⇒ 别死循环
        y = ny
    return x, y


def clamp(x, y, win_px, to_virtual=True):
    u"""老接口（保留给旧调用）：`to_virtual=True` 时夹进**整个虚拟桌面**、否则夹进主屏。

    ★ 新代码请用 `fit()` —— 它会额外保证通知卡/副标题不越界（见上面的说明）。
    """
    if to_virtual:
        l, t, r, b = virtual()
    else:
        p = primary()
        l, t, r, b = p["left"], p["top"], p["right"], p["bottom"]
    return (int(max(l, min(x, r - win_px))), int(max(t, min(y, b - win_px))))


def on_primary(x, y, win_px):
    u"""窗口是否（至少部分）落在主屏上 —— 用来判断"存的坐标还能不能用"。"""
    p = primary()
    return not (x + win_px <= p["left"] or x >= p["right"]
                or y + win_px <= p["top"] or y >= p["bottom"])


# ---------------------------------------------------------------- 报告
def report(pet=None, win=None):
    u"""人读的体检报告（启动时也会写进日志）。"""
    mons = monitors(refresh=True)
    p = primary()
    l, t, r, b = work_area()
    size, why = pick_size()
    lines = []
    lines.append(u"■ 显示器：共 %d 块" % len(mons))
    for i, m in enumerate(mons, 1):
        wl, wt, wr, wb = m["work"]
        lines.append(u"   %d) %dx%d @(%d,%d) ~ (%d,%d)%s"
                     % (i, m["w"], m["h"], m["left"], m["top"], m["right"],
                        m["bottom"], u"  ← 主屏" if m["primary"] else u""))
        lines.append(u"      它自己的工作区 %dx%d @(%d,%d) ｜ 任务栏占 %d 条"
                     % (wr - wl, wb - wt, wl, wt, len(taskbar_bands(m))))
    if _ENUM_ERR:                       # ★ 绝不静默：退化到单屏时必须留下痕迹
        lines.append(u"[!] 枚举显示器时报了错（可能已退化成单屏）：%s"
                     % u" / ".join(_ENUM_ERR[:3]))
    lines.append(u"■ 主屏：%dx%d ｜ 工作区 %dx%d @(%d,%d)（任务栏已躲开）"
                 % (p["w"], p["h"], r - l, b - t, l, t))
    lines.append(u"■ 定档：%s ⇒ **默认 %d px**" % (why, size))
    if pet is None:
        pet = size
    if win is None:
        try:
            import fairy_layers as L
            win = L.canvas_px(pet)
        except Exception:
            win = pet
    cx, cy = center(win)
    hx, hy = home(win, pet)
    ib = content_inset_b(win)
    lines.append(u"■ 定位（本体 %d px ⇒ 画布/窗口 %d px）" % (pet, win))
    lines.append(u"   居中（主屏几何中心）：(%d, %d) ⇒ 窗口中心 (%d, %d) = (%d, %d)"
                 % (cx, cy, cx + win // 2, cy + win // 2,
                    (p["left"] + p["right"]) // 2, (p["top"] + p["bottom"]) // 2))
    lines.append(u"   默认落点（工作区右下）：(%d, %d)" % (hx, hy))
    lines.append(u"     纵向校验：副标题底边落在 y=%d（工作区底 %d，画布底留白 %d px）"
                 u" ⇒ %s"
                 % (hy + win - ib, b, ib,
                    u"在任务栏之上 ✅" if hy + win - ib <= b else u"⚠️ 仍压任务栏"))
    lines.append(u"     横向校验：通知卡右缘 x=%d（屏宽/工作区右 %d）⇒ %s"
                 % (hx + win - CARD_INSET, r,
                    u"在屏内 ✅" if hx + win - CARD_INSET <= r else u"⚠️ 出屏"))
    return u"\n".join(lines)


def _main(argv):
    as_json = "--json" in argv
    pet = None
    if "--pet" in argv:
        i = argv.index("--pet")
        if i + 1 < len(argv):
            pet = int(argv[i + 1])
    win = None
    if "--win" in argv:
        i = argv.index("--win")
        if i + 1 < len(argv):
            win = int(argv[i + 1])
    if as_json:
        p = primary()
        size, why = pick_size()
        out = {"monitors": monitors(), "primary": p, "work": work_area(),
               "size": size, "why": why}
        if pet is not None:
            import fairy_layers as L
            w = win or L.canvas_px(pet)
            out["pet"] = pet
            out["win"] = w
            out["center"] = center(w)
            out["home"] = home(w, pet)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(report(pet, win))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
