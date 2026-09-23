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


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


MONITORINFOF_PRIMARY = 0x00000001
_MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                                      ctypes.POINTER(_RECT), ctypes.c_double)


def monitors():
    u"""→ `[{left, top, right, bottom, w, h, primary}, ...]`（按系统枚举顺序）。

    ★ 枚举失败（极罕见）就退化成"一块主屏"，绝不抛异常 —— 启动流程不能被它挡住。
    """
    out = []

    def cb(hmon, hdc, lprc, data):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            out.append({"left": r.left, "top": r.top, "right": r.right,
                        "bottom": r.bottom, "w": r.right - r.left,
                        "h": r.bottom - r.top,
                        "primary": bool(mi.dwFlags & MONITORINFOF_PRIMARY)})
        return 1

    try:
        user32.EnumDisplayMonitors(0, None, _MONITORENUMPROC(cb), 0)
    except Exception:
        pass
    if not out:
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        out = [{"left": 0, "top": 0, "right": w, "bottom": h, "w": w, "h": h,
                "primary": True}]
    # ★ 系统没标主屏时（少见）把"包含原点"的那块当主屏
    if not any(m["primary"] for m in out):
        for m in out:
            if m["left"] <= 0 <= m["right"] and m["top"] <= 0 <= m["bottom"]:
                m["primary"] = True
                break
        else:
            out[0]["primary"] = True
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

    ★ `SPI_GETWORKAREA` 拿到的**永远**是主屏工作区 —— 这正是"默认落在主屏"想要的行为。
    """
    r = _RECT()
    try:
        if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            return int(r.left), int(r.top), int(r.right), int(r.bottom)
    except Exception:
        pass
    p = primary()
    return p["left"], p["top"], p["right"], p["bottom"]


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
    return fit(int(r - off - pet_px - margin_x),
               int(b - win_px + content_inset_b(win_px) - margin_y),
               win_px, use_work=True)


def fit(x, y, win_px, inset=None, pad=None, use_work=True, inset_b=None):
    u"""把窗口左上角夹进**屏内**，保证**可见内容不越界** → `(x, y)`。

    ★★ 为什么需要 `inset`（主人 2026-09-23 抓到的"往右下角移动后超出屏幕边界、
      通知框出屏"）：画布比本体每边大一圈（辉光余量），而**通知卡/进度条画在画布里、
      左右各留 `CARD_INSET` px** ⇒ 卡片的可见范围是 `[x+inset, x+win−inset]`。
      若按"窗口贴边"来夹（`x ≤ r − win`），卡片右端会正好压在屏幕外。
      ⇒ 按 `x ≤ r − win + inset − pad` 夹：允许画布那几像素**透明**余量溢出，
        卡片始终留在屏内（`pad` 再多让 2 px，别让描边正好贴在最外一列像素上）。
    ★★ 纵向**另算**（`inset_b`）：卡片/条是贴画布**底**画的（见 `CONTENT_BOTTOM_F`），
      底边留白比左右小得多 ⇒ `y ≤ b − win + inset_b − pad`。
    ★ `use_work=True`（**默认**）：夹进**主屏工作区** —— 连任务栏一起躲开
      （主人 2026-09-23：「默认位置整体避开状态栏」）。
      `False`：夹进**主屏整屏**（允许压任务栏，只在需要"能拖到最下面"时用）。
    ★ 主人要求「位置**按一块屏**定位」⇒ 这里**只认主屏**，不碰虚拟桌面（副屏不外扩）。
    """
    if inset is None:
        inset = CARD_INSET
    if pad is None:
        pad = EDGE_PAD
    if inset_b is None:
        inset_b = content_inset_b(win_px)
    if use_work:
        l, t, r, b = work_area()
    else:
        p = primary()
        l, t, r, b = p["left"], p["top"], p["right"], p["bottom"]
    lo_x, hi_x = l - inset + pad, r - win_px + inset - pad
    lo_y, hi_y = t - inset + pad, b - win_px + inset_b - pad
    if hi_x < lo_x:                       # 窗口比屏幕还宽 ⇒ 居中（别把它推到屏外）
        lo_x = hi_x = (l + r - win_px) // 2
    if hi_y < lo_y:
        lo_y = hi_y = (t + b - win_px) // 2
    return int(max(lo_x, min(x, hi_x))), int(max(lo_y, min(y, hi_y)))


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
    mons = monitors()
    p = primary()
    l, t, r, b = work_area()
    size, why = pick_size()
    lines = []
    lines.append(u"■ 显示器：共 %d 块" % len(mons))
    for i, m in enumerate(mons, 1):
        lines.append(u"   %d) %dx%d @(%d,%d) ~ (%d,%d)%s"
                     % (i, m["w"], m["h"], m["left"], m["top"], m["right"],
                        m["bottom"], u"  ← 主屏" if m["primary"] else u""))
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
