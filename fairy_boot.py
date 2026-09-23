# -*- coding: utf-8 -*-
u"""开机动画（启动窗口）—— 倒计时 → CRT 开机 → 拨片 → 收尾

★★ 这是**生产代码**（不是预览脚本）。`fairy_pet.py` 直接调 `play()`。
   离线预览脚本 `_work/step63_boot_preview.py` 与现场测试 `_work/step62_boot_live.py`
   **都 import 本模块** ⇒ 一处实现、多处复用，不会出现"预览好看、实机不一样"的漂移。

主人 2026-09-22 定的规则（逐条）：
  · 三段式：**S1 倒计时（程序生成，与素材时钟同风格）→ S2 拨片 → S3 常态**
  · 拨片 = `启动动画.mp4` 从 **3.90 s** 起（前面 1.5~3.8 s 是层叠对话窗口，不要）
  · 转场 = **CRT 开关机**质感：点（停留）→ 拉成横线 → 纵向展开（有过冲）
  · **展开期间素材已经在播**（画面在动才像真电视）⇒ 全屏播放段 = 素材总长 − 开机时长
  · **末尾不做 CRT 关机** —— 素材末尾自带「漩涡(10.8s) → 白光充满(11.9s) → 黑(12.15s)」
  · 尺寸 = **16:9**、**覆盖桌宠区**（260 px 档 658×370）
  · 整段**默认静音**（预留 `audio` 接口，见 `AUDIO_PATH`）
  · **不需要烘焙时倒计时固定 3.0 s**（重播同样）

依赖：Pillow + numpy + **imageio-ffmpeg**（借它的 ffmpeg.exe 解拨片）。
解帧方式 = **ffmpeg 管道 + 后台线程**（不落地帧序列、内存恒定几 MB、启动 ~0.2 s）。

显示尺寸与渲染尺寸**解耦**：内部恒定按 `658×370` 渲染（所有常量都挂在这个尺寸上），
贴到窗口时再缩放 ⇒ 200/320 px 档不需要第二套参数。
"""
from __future__ import annotations

import ctypes
import math
import os
import queue
import subprocess
import sys
import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:                                     # ★ 屏幕测量（居中/多屏）：缺失也不影响动画
    import fairy_screen as SCR
except Exception:                        # pragma: no cover
    SCR = None
BOOT_DIR = os.path.join(HERE, "boot")
PLATE_PATH = os.path.join(BOOT_DIR, "count_plate.png")
CLIP_PATH = os.path.join(BOOT_DIR, u"clip_658x370.mp4")

# ================================================================ 时间线
T_CD = 3.00          # S1 倒计时（不需要烘焙 ⇒ 固定 3.0 s；重播也一样）
T_CD = 3.00          # S1 倒计时（不需要烘焙 ⇒ 固定 3.0 s；重播也一样）
T_FADE = 0.40        # 倒计时归零后渐暗
T_ON = 0.90          # CRT 开机（这段时间拨片**已在播**）
CLIP_VFPS = 15       # 拨片打包帧率
CLIP_DUR = 7.70      # 素材 3.90 ~ **11.60 s**（停在画面最亮的帧 115）
CLIP_WHITE_FRAME = 119   # ★ 素材里**白光**那一帧（均值 252）—— 收束的源色用它，不用末帧
#   ★★ 2026-09-22 23:2x 实测（逐帧均值）：素材末尾**自己会变暗**，主人说的
#      「最后几帧的黑屏」**一半是它**（另一半是我原来加的 `T_TAIL` 渐黑）。
#        帧 117 (7.80s) 均值   0.0   ← 白闪之前的黑
#        帧 118 (7.87s) 均值 207.8
#        帧 119 (7.93s) 均值 252.4   ← 白光峰值
#        帧 120 (8.00s) 均值 252.7   ← 白光峰值（收在这里）
#        帧 121 (8.07s) 均值 199.3   ← 开始回落
#        帧 123+        均值  34.3   ← 暗灰（这就是"最后的黑屏"）
#      ⇒ `CLIP_DUR` 从 8.29 **收到 8.00**：拨片正好停在白光峰值，由 `T_COLLAPSE` 接手。
T_PLAY = CLIP_DUR - T_ON     # 全屏播放段 = 7.39 s（★ 必须减去开机段，否则末尾播不完）
# ---------------------------------------------------------------- S3 白光收束
#   ★★ 2026-09-22 23:1x 主人改的口径（原话）：
#      「我希望开机动画在白光结束，然后**白光再收束，变成 fairy 本体**，在中心待 1 秒后，
#        再去右下角，**最后几帧的黑屏就不要了**」
#      ⇒ 原来的 `T_TAIL`（收尾渐黑 0.30 s）**整个删掉**，换成这一段"光收束"：
#        全屏白光 → 边缘向中心收 → 收到**与主体外盘等大的圆盘** → 交棒给桌宠本体。
#      ★ 末帧是"半亮的圆盘"，**不是黑屏**；那点残余亮度正好被本体的登场淡入接住。
T_COLLAPSE = 0.90       # 收束时长
#   ★★ 2026-09-23 00:1x：下面这四个圆盘参数（R0/R1/F0/F1）**已不再使用** ——
#      收束改成「整幅白光向中心径向淡出」了（见 COLLAPSE_SOFT 与 _collapse），
#      保留在这里只为了说明历史。
COLLAPSE_R0 = 380.0     # 起始半径（≥ 半对角线 377.5 ⇒ 整幅都是白的）
COLLAPSE_R1 = 111.0     # 终止半径 = 主体外盘半径（H × 0.5 × 0.6018）
COLLAPSE_F0 = 18.0      # 起始羽化宽（软边）
COLLAPSE_F1 = 6.0       # 终止羽化宽（收紧之后才像"一个圆盘"）
COLLAPSE_BOOST = 1.25   # 起始时把来源帧提亮（拨片末帧不一定纯白）
COLLAPSE_DIM = 0.24     # 收束过程中整体压暗（**不归零** ⇒ 不留黑屏）
COLLAPSE_TINT = 0.45    # 末段往主体色偏多少
COLLAPSE_FLASH = 0.12   # ★ 收束最前面这一小段：从「亮处」闪到**全白**（避免硬切）
COLLAPSE_SOFT = 0.34   # ★ 径向淡出的过渡带宽度（占半对角线的比例）
COLLAPSE_RGB = (208, 234, 255)

W, H, FPS = 658, 370, 30     # ★ 内部渲染尺寸固定（16:9，260 px 档的覆盖尺寸）
TOTAL = T_CD + T_FADE + T_ON + T_PLAY + T_COLLAPSE

AUDIO_PATH = None    # ★ 预留：整段默认静音。将来要加声音，在这里给一个 wav/mp3 路径即可。

# ================================================================ CRT 参数
#   （数值全部照 `_work/step52_crt_v2.py` 定稿，**别凭记忆改**）
LINE_COL = np.array([203, 242, 254], np.float64)   # #CBF2FE（素材时钟的青色）
LINE_H = 3.0
EASE_K = 2.4
OVERSHOOT = 0.02
OVER_AT = 0.82
SCAN_PERIOD = 3
SCAN_K = 0.70
FOCUS_BLUR = 7.5
#   ★★ `GLOW_DOWNSCALE`：辉光在**半分辨率**上算（辉光是低频 ⇒ 视觉等价，实测最大差 ≤ 2 级）。
#      = 1 时是"逐像素精确"路径 —— `_work/step66_boot_verify.py` 的①用它跟已批准的
#      `step52_crt_v2.post()` 做**逐像素比对**（证明参数一个没抄错），再报半分辨率档的差值。
GLOW_DOWNSCALE = 2
GLOW_R = (2.2, 6.5, 15.0)
GLOW_W = (0.95, 0.62, 0.36)
GLOW_BOOST_LINE = 1.55
GLOW_GAIN = 0.55
GLOW_FLOOR = 22.0
GLOW_CEIL = 105.0
VIGNETTE = 0.26
EDGE_FADE = 0.16
EDGE_LINE_BOOST = 1.55

# ★ 预计算：垂直渐变与扫描线都**只跟 y 有关**（常数列）⇒ 合成一个 (H,1,1) 乘子，省一次整幅乘法。
_VIG = 1.0 - VIGNETTE * (np.abs(2.0 * np.arange(H, dtype=np.float64) / H - 1.0) ** 1.7)
_SCAN_COL = np.ones(H, dtype=np.float64)
_SCAN_COL[::SCAN_PERIOD] = SCAN_K
_VS = (_VIG * _SCAN_COL)[:, None, None]
# ★ 光收束用的**距离场**（到画面中心的距离，单位 px）—— 预计算一次，收束时只做一次减法。
_DY, _DX = np.mgrid[0:H, 0:W]
_DIST = np.sqrt((_DX - (W - 1) / 2.0) ** 2 + (_DY - (H - 1) / 2.0) ** 2)
_COL_RGB = np.array(COLLAPSE_RGB, np.float64).reshape(1, 1, 3)
_DMAX = float(_DIST.max()) if _DIST.max() > 0 else 1.0
_DNORM = _DIST / _DMAX      # ★ 归一化到 0（中心）~ 1（四角）—— 径向淡出用它

# ================================================================ 倒计时参数
#   （照 `_work/step55_countdown_v3.py`；数字的点位/字高/字宽都是**素材实测值**）
CX = (162.5, 330.0, 505.0)      # 三组数字的中心 x
CY = 177.0
NUM_H, NUM_W = 120.0, 105.0     # 素材数字的实测高 / 宽
NUM_RGB = (168, 214, 244)       # 降亮 + 偏青（2x 对照后定：素材最亮约 230）
NOISE_K, SCAN_K_NUM = 0.06, 0.68
FONT_BIG = "C:/Windows/Fonts/impact.ttf"
FONT_UI = "C:/Windows/Fonts/consolab.ttf"    # ★★ 只能画 ASCII！中文会静默渲成方块 ⊡
CAPTION = u"MIXED"                            # 占位（避免 lint 认为未使用）

UI_TOP = (u"ESTIMATED TIME", 87, 15, 4.0, (150, 200, 236))
UI_TAGS = (u"MIN", u"SEC", u"1/10 SEC")
UI_TAG_Y, UI_TAG_PX, UI_TAG_TRACK = 262, 12, 2.4
UI_BOTTOM = (u"SYSTEM BOOTING", 318, 16, 4.2, (206, 234, 252))


# ================================================================ 工具
def ease(u, k=EASE_K):
    return u ** k


def on_geom(p):
    u"""CRT 开机 (w_ratio, h_ratio)：点（停留 0.12）→ 拉成线 → 纵向展开（2% 过冲回落）

    ★ 给「点」留停留是实测结论：不下停留时 p=0 只有 4 px、下一帧就跳到 206 px（1 帧之差），
      观感只是「闪一下出来一条线」，看不到点。真 CRT 的亮点也会先亮一下再拉开。
    """
    p_dot, p_line = 0.12, 0.34
    h_min = LINE_H / H
    w_min = 4.0 / W
    if p < p_dot:
        return w_min, h_min
    if p < p_line:
        u = (p - p_dot) / (p_line - p_dot)
        return w_min + (1.0 - w_min) * (u ** 0.85), h_min
    u = (p - p_line) / (1.0 - p_line)
    if u < OVER_AT:
        v = u / OVER_AT
        return 1.0, h_min + (1.0 + OVERSHOOT - h_min) * (1.0 - (1.0 - v) ** EASE_K)
    v = (u - OVER_AT) / (1.0 - OVER_AT)
    return 1.0, (1.0 + OVERSHOOT) - OVERSHOOT * v


def on_gain(p):
    u"""展开完成瞬间的亮度过冲。

    ★★ 旧峰值 1.52 会把 `H.D.D. SYSTEM` 那种大白字顶到过曝（主人：「转场结束到拨片怎么这么亮」）
      ⇒ 基线 1.02、峰值 **1.18**。
    """
    if p < 0.25:
        return 1.02
    u = (p - 0.25) / 0.75
    return 1.02 + 0.16 * math.sin(math.pi * min(1.0, u * 1.15))


def geom(src, w_r, h_r, gain):
    u"""按比例缩放后居中贴到黑画布；线/点阶段与「虚拟青白线」混合（否则压扁后发灰看不见）。"""
    # ★ 快路径：正好满幅（拨片全屏段）时，下面那些重采样/混合/渐隐/边线**全都不生效**
    #   （mix=1、tw=W 不渐隐、th=H 不加边线）⇒ 原式等于 `src * gain`。省掉一次 resize +
    #   一次 (H,W,3) 的 `np.repeat` 造色块 —— 实测这一段从 ~4 ms 降到 ~0.6 ms。
    if w_r == 1.0 and h_r == 1.0:
        return np.clip(src.astype(np.float64) * gain, 0, 255)
    canvas = np.zeros((H, W, 3), np.float64)
    tw = max(1, int(round(W * w_r)))
    th = max(1, int(round(H * h_r)))
    real = np.asarray(Image.fromarray(src.astype(np.uint8)).resize((tw, th), Image.BOX),
                      dtype=np.float64)
    mix = min(1.0, h_r / 0.12)
    if mix >= 1.0:
        # ★★ `h_r ≥ 0.12` 时虚拟青白线的权重是 **0** ⇒ 那个 `np.repeat` 造出来的
        #    (th, tw, 3) 色块（658×286 时 ≈ 13.6 MB）纯属白造。实测 geom() 14.8 → 4 ms。
        #    等价性：`line*(1-1) + real*1` 就是 `real`（乘 0 得 0、乘 1 不变）⇒ 逐位一致。
        patch = real
    else:
        line = np.repeat(np.repeat(LINE_COL[None, None, :], th, 0), tw, 1)
        patch = line * (1.0 - mix) + real * mix

    if tw < W - 1:                       # 两端渐隐（电子束软化）
        fade = np.ones(tw)
        k = int(min(EDGE_FADE * tw, 48))
        if k > 2:
            ramp = np.linspace(0.0, 1.0, k) ** 0.75
            fade[:k] = ramp
            fade[-k:] = ramp[::-1]
        patch = patch * fade[None, :, None]      # patch 是 (th, tw, 3) ⇒ 横向沿**轴 1**

    if 2 < th < H - 2:                   # 画面上下边缘的亮线（电子束折返）
        patch[0] *= EDGE_LINE_BOOST
        patch[1] *= (EDGE_LINE_BOOST + 1.0) / 2.0
        patch[-1] *= EDGE_LINE_BOOST
        patch[-2] *= (EDGE_LINE_BOOST + 1.0) / 2.0
    if h_r < 0.05:                       # 线越扁越亮（能量集中）
        patch = patch * (1.0 + (0.05 - h_r) / 0.05 * 0.85)

    y0, x0 = (H - th) // 2, (W - tw) // 2
    sy0, sy1 = max(0, -y0), min(th, H - y0)
    sx0, sx1 = max(0, -x0), min(tw, W - x0)
    dy0, dy1 = max(0, y0), min(H, y0 + th)
    dx0, dx1 = max(0, x0), min(W, x0 + tw)
    if dy1 > dy0 and dx1 > dx0:
        canvas[dy0:dy1, dx0:dx1] = patch[sy0:sy1, sx0:sx1]
    return np.clip(canvas * gain, 0, 255)


def post(a, h_r):
    u"""CRT 质感。★ 顺序是重点：几何 → 失焦 → 辉光(只取亮部) → 垂直渐变 → **扫描线最后**。

    · 失焦用**钟形**：点/线必须**锐**（电子束集中），展开中段最糊。
      ★ 初版写成「越接近线越糊」⇒ 7.4 px 的模糊把 4×3 的点抹成峰值 7（点消失了）。
    · 辉光**只对亮部**（否则是「整幅提灰」）；且只取 `max(0, 辉光 − 原图)` 的溢出量
      （`a + 辉光` 会把白色顶过 255 被 clip 削平 ⇒ 整片发白）。
    · 扫描线**必须在辉光之后**：辉光半径 15 px > 扫描线周期 3 px，放前面会被抹平（实测对比度 1%）。
    """
    a = np.clip(a, 0, 255).astype(np.float64)
    ds = max(1, int(GLOW_DOWNSCALE))
    sW, sH = max(1, W // ds), max(1, H // ds)
    if ds > 1:
        small = Image.fromarray(a.astype(np.uint8)).resize((sW, sH), Image.BILINEAR)
        # ★★ 失焦也搬到**半分辨率**做：模糊本来就是低频操作，半分辨率 + 放大回去视觉等价。
        #   （原来这一句是全分辨率的最大单项 —— 半径最大 7.5 px，实测 ~18 ms/帧。）
        if 0.02 < h_r < 0.85:
            b = FOCUS_BLUR * (math.sin(math.pi * (h_r / 0.85)) ** 1.2)
            if b > 0.4:
                small = small.filter(ImageFilter.GaussianBlur(b / float(ds)))
                a = np.asarray(small.resize((W, H), Image.BILINEAR), dtype=np.float64)
        sa = np.asarray(small, dtype=np.float64)
        # ★ `mean(axis=2)` 是 numpy 反模式（沿长度为 3 的轴 reduce 走通用慢路径，实测慢 18×）
        #   ⇒ 手工展开成「求和 ÷ 3」，与 mean 逐位一致。
        lum = (sa[..., 0] + sa[..., 1] + sa[..., 2]) / 3.0
        mask = np.clip((lum - GLOW_FLOOR) / GLOW_CEIL, 0.0, 1.0)[..., None]
        sim = Image.fromarray((sa * mask).astype(np.uint8))
        acc = np.zeros((H, W, 3), np.float64)
        for r, w in zip(GLOW_R, GLOW_W):
            bl = sim.filter(ImageFilter.GaussianBlur(r / float(ds)))
            acc += np.asarray(bl.resize((W, H), Image.BILINEAR), dtype=np.float64) * w
    else:
        if 0.02 < h_r < 0.85:
            b = FOCUS_BLUR * (math.sin(math.pi * (h_r / 0.85)) ** 1.2)
            if b > 0.4:
                a = np.asarray(Image.fromarray(a.astype(np.uint8))
                               .filter(ImageFilter.GaussianBlur(b)), dtype=np.float64)
        lum = a.mean(axis=2, keepdims=True)
        mask = np.clip((lum - GLOW_FLOOR) / GLOW_CEIL, 0.0, 1.0)
        im = Image.fromarray((a * mask).astype(np.uint8))
        acc = np.zeros_like(a)
        for r, w in zip(GLOW_R, GLOW_W):
            acc += np.asarray(im.filter(ImageFilter.GaussianBlur(r)), dtype=np.float64) * w
    if h_r < 0.06:
        acc *= GLOW_BOOST_LINE
    a = a + np.maximum(0.0, acc - a) * GLOW_GAIN

    if ds > 1:
        # ★ 垂直渐变 × 扫描线**合并成一个预乘子**（少一次整幅 float64 乘法）：
        #   两者都只跟 y 有关 ⇒ 先各自取 H 长度、相乘再广播，数学上等价。
        #   精确路径（ds=1）仍走下面原来的两步，保持与已批准实现**逐位一致**。
        return np.clip(a * _VS, 0, 255)
    yy = np.arange(H, dtype=np.float64)
    a = a * (1.0 - VIGNETTE * (np.abs(2.0 * yy / H - 1.0) ** 1.7))[:, None, None]
    m = np.ones((H, 1, 1))
    m[::SCAN_PERIOD] = SCAN_K
    return np.clip(a * m, 0, 255)


def frame_for(src, w_r, h_r, gain):
    return post(geom(src, w_r, h_r, gain), h_r).astype(np.uint8)


# ================================================================ 拨片（ffmpeg 管道）
class VideoPipe(object):
    u"""顺序读拨片帧：`get(i)` 拿第 i 帧（15 fps）。

    ★ 为什么用管道 + 线程、而不是先解成帧序列/缓存文件：
      · 帧序列落地 ≈ 18 MB/秒的 PNG，或 81 MB 的裸数组 ⇒ 快照与内存都不划算；
      · 管道方式内存恒定（队列 12 帧 ≈ 8.5 MB），启动只等 ~0.2 s；
      · 消费端是**单调递增**的索引 ⇒ 只要按序丢弃即可，不需要随机访问。
    ❗ffmpeg 是控制台程序：**必须 CREATE_NO_WINDOW**，否则从 pythonw 里拉起会闪黑框。
    """
    FRAME_BYTES = W * H * 3

    def __init__(self, path=CLIP_PATH, fps=CLIP_VFPS):
        self.ok = False
        self.err = None
        self.last = np.zeros((H, W, 3), np.uint8)
        self.n = 0
        self._q = queue.Queue(maxsize=12)
        self.pos = -1            # ★ `last` 是第几帧（0 基）；-1 = 还没有帧
        self.eof = False
        self._stop = False
        self._proc = None
        self._th = None
        self.n_expected = int(round(CLIP_DUR * fps))
        if not os.path.exists(path):
            self.err = u"找不到拨片 %s" % os.path.basename(path)
            return
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as e:
            self.err = u"imageio-ffmpeg 不可用：%r" % (e,)
            return
        try:
            flags = 0x08000000 if os.name == "nt" else 0        # CREATE_NO_WINDOW
            self._proc = subprocess.Popen(
                [ff, "-v", "error", "-i", path, "-f", "rawvideo",
                 "-pix_fmt", "rgb24", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, bufsize=self.FRAME_BYTES * 3,
                creationflags=flags)
        except Exception as e:
            self.err = u"ffmpeg 启动失败：%r" % (e,)
            return
        self._th = threading.Thread(target=self._reader, name="boot-video", daemon=True)
        self._th.start()
        self.ok = True

    def _reader(self):
        try:
            while not self._stop:
                b = self._proc.stdout.read(self.FRAME_BYTES)
                if not b or len(b) < self.FRAME_BYTES:
                    break
                f = np.frombuffer(b, np.uint8).reshape(H, W, 3).copy()
                try:
                    self._q.put(f, timeout=0.5)
                except queue.Full:
                    pass
        except Exception:
            pass
        finally:
            try:
                self._q.put(None, timeout=0.3)
            except queue.Full:
                pass

    def get(self, i):
        u"""取**第 i 帧**（0 基）。

        ★★ 第一版这里有两个错（离线验证抓到的）：
          · **索引差 1**：原实现 `if self.n > i + 1: break` ⇒ `get(0)` 实际返回的是第 2 帧；
          · **没有"等帧"语义**：队列为空就直接返回 `self.last`（初始是全黑）⇒
            验证脚本里连取 10 帧全是亮度 0。
        现在：用 `pos` 记录 `last` 的位置，一路推进到第 i 帧；队列空时**最多等 60 ms**
        （播放期管道是跑在播放前面的，正常不会等；等不到就沿用上一帧 —— 宁可重帧，不卡住）。
        """
        if self.eof and self.pos >= i:
            return self.last
        while self.pos < i:
            try:
                item = self._q.get(timeout=0.06 if self.pos < 0 or self.pos < i else 0.0)
            except queue.Empty:
                break
            if item is None:
                self.eof = True
                break
            self.last = item
            self.pos += 1
            self.n += 1
        return self.last

    def close(self):
        self._stop = True
        if self._proc is not None:
            try:
                self._proc.stdout.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self.ok = False


# ================================================================ 倒计时帧
class Countdown(object):
    u"""素材真帧做底 + 我们的文字。

    底图 = `boot/count_plate.png`（来自 `启动动画.mp4` t=0.80 s，素材自己的符号已抹掉），
    数字按**素材实测点位**排（中心 x、字高 120、字宽压到 105）。
    ★ 三组数字 + 标签 + 顶部/底部英文 = **全部 ASCII**（`FONT_UI` 只能画 ASCII）。
    """
    BAND0, BAND1 = 84, 272            # 数字带（含发光/膨胀余量；模糊半径 3.2 需要 ~10 px 余量）
    TEXT_BANDS = ((76, 116), (250, 288), (306, 350))   # 顶行 / 标签行 / 底行（同样留余量）

    def __init__(self, plate_path=PLATE_PATH):
        self.plate = None
        self.err = None
        try:
            self.plate = Image.open(plate_path).convert("RGB")
            if self.plate.size != (W, H):
                self.plate = self.plate.resize((W, H), Image.LANCZOS)
        except Exception as e:
            self.err = u"找不到倒计时底图：%r" % (e,)
            return
        self.font_px = self._fit(NUM_H)
        # ★★ 2026-09-23（主人："同样的数字，一会大一会儿小，大小粗细没个统一"）：找到真凶了。
        #   Impact 的数字**不等宽** —— 实测 @120px：'1' advance 46、'7' 47、'2' 60、
        #   '0' 64、'6'/'9' 65。而 `_tile()` 原来把**每串自己的墨迹宽**压缩到 NUM_W
        #   ⇒ 压缩系数 = NUM_W / 墨迹宽，随数字在 **0.81 ~ 1.14** 之间乱跳（差 40%）：
        #     "90" 被压到 0.814、"11" 被拉到 1.141 ⇒ 同一个字号，'1' 有时胖有时瘦。
        #   ⇒ 改成全文**共用一个横向系数**（按"两位数字 = NUM_W"定标，与已批准的观感一致），
        #     排版也改用**字宽（advance）**而不是墨迹 bbox（墨水宽随数字变，当基准就错了）。
        #   ★ 竖向同理：用一个**公共上下边界**（"0123456789" 的并集），所有数字共用一条基线。
        _f0 = ImageFont.truetype(FONT_BIG, self.font_px)
        self._sx = NUM_W / (2.0 * float(_f0.getlength(u"0")))
        _b0 = ImageDraw.Draw(Image.new("RGB", (4, 4))).textbbox(
            (0, 0), u"0123456789", font=_f0)
        self._bb_top, self._bb_h = _b0[1], _b0[3] - _b0[1]
        self._tiles = {}
        self._frames = {}
        self._plate_u8 = np.asarray(self.plate, dtype=np.uint8).copy()
        b0, b1 = self.BAND0, self.BAND1
        self._plate_band = self._plate_u8[b0:b1].astype(np.float64)
        # ★ 噪点场**只生成一次**：原来是**每帧** `rng.normal(size=(H, W, 1))`
        #   —— 24 万个正态样本 ≈ 20 ms/帧，白烧。种与形状都不变 ⇒ 切带后与整幅取值一致。
        rng = np.random.default_rng(20260922)
        self._noise = rng.normal(0.0, NOISE_K, size=(H, W, 1))[b0:b1]
        # 扫描线的**奇偶必须按绝对 y**（原来用整幅的 `np.arange(H)`）⇒ 这里从 b0 起算
        self._scan = np.where((np.arange(b0, b1) % 2) == 0, SCAN_K_NUM, 1.0)[:, None, None]
        self._overlay = self._build_overlay()

    # ---------- 静态层（只算一次）
    @staticmethod
    def _fit(px_h):
        d = ImageDraw.Draw(Image.new("RGB", (4, 4)))
        lo, hi = 20, 400
        for _ in range(24):
            mid = (lo + hi) // 2
            bb = d.textbbox((0, 0), u"0123456789", font=ImageFont.truetype(FONT_BIG, mid))
            if bb[3] - bb[1] < px_h:
                lo = mid
            else:
                hi = mid
        return (lo + hi) // 2

    @staticmethod
    def _tracked(d, cx, y, txt, font, fill, track):
        w = sum(d.textlength(c, font=font) for c in txt) + track * max(0, len(txt) - 1)
        x = cx - w / 2.0
        for c in txt:
            d.text((x, y), c, font=font, fill=fill)
            x += d.textlength(c, font=font) + track

    def _build_overlay(self):
        u"""顶部 `ESTIMATED TIME` / 数字下标签 / 底部 `SYSTEM BOOTING`（静态 ⇒ 只画一次）。

        ★ 连**外发光**一起预存，并按**文字带**切片：带外 alpha 恒 0 ⇒ 每帧只碰三条窄带。
          （漏掉外发光这一步曾让倒计时帧与已批准版本差 **53 级** —— 离线验证②抓到的。）
        """
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        cxm = W / 2.0
        txt, y, px, tr, rgb = UI_TOP
        self._tracked(d, cxm, y, txt, ImageFont.truetype(FONT_UI, px), rgb, tr)
        ft = ImageFont.truetype(FONT_UI, UI_TAG_PX)
        for cx, lab in zip(CX, UI_TAGS):
            self._tracked(d, cx, UI_TAG_Y, lab, ft, (150, 200, 236), UI_TAG_TRACK)
        txt, y, px, tr, rgb = UI_BOTTOM
        self._tracked(d, cxm, y, txt, ImageFont.truetype(FONT_UI, px), rgb, tr)
        arr = np.asarray(layer, dtype=np.float64)
        glow = np.asarray(
            Image.fromarray(arr[..., :3].astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.8)),
            dtype=np.float64)
        self._ov_bands = []
        for y0, y1 in self.TEXT_BANDS:
            self._ov_bands.append((y0, y1, arr[y0:y1, :, :3],
                                   arr[y0:y1, :, 3:4] / 255.0, glow[y0:y1]))
        return layer

    # ---------- 数字瓦片（按文本缓存：同一条显示 3 帧，缓存后 2/3 的帧省掉重绘）
    def _tile(self, txt):
        t = self._tiles.get(txt)
        if t is not None:
            return t
        f = ImageFont.truetype(FONT_BIG, self.font_px)
        # ★ 按**字宽**排版（`getlength` 之和 = 自然排版宽度），**不用墨迹 bbox** ——
        #   墨迹宽随数字变（'1'=46 / '0'=64），拿它当缩放基准就会让每串各自缩放
        #   ⇒ "同样的数字一会大一会小"（见 `__init__` 里的说明）。
        box_w = sum(f.getlength(c) for c in txt)
        pad = 6
        tile = Image.new("RGBA", (int(round(box_w)) + 2 * pad, self._bb_h + 2 * pad),
                         (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((pad, pad - self._bb_top), txt, font=f,
                                  fill=NUM_RGB + (255,))
        tile = tile.filter(ImageFilter.MaxFilter(3))     # 轻微膨胀 ⇒ 盖住素材残影
        # ★ 全文**同一个**横向系数 `self._sx` ⇒ 所有数字的宽窄/粗细完全一致
        #   （"00" 这类常态数字的结果与改前逐像素相同，只有 '1'/'7' 这类窄数字被纠正过来）
        tile = tile.resize((max(1, int(round(tile.width * self._sx))), tile.height),
                           Image.LANCZOS)
        self._tiles[txt] = tile
        return tile

    def frame(self, remain):
        u"""`remain` 秒 → 一帧。同一 0.1 s 内的帧直接复用（内部缓存按 (mm, ss, tt)）。"""
        mm, ss = int(remain // 60), int(remain % 60)
        # ★★ 后两位的跳动节奏（主人：「最好也是 10、20、30 这样跳，不然跟抽风一样」）：
        #    取「1/10 秒」那一位 ×10 ⇒ 只有 00/10/…/90 十个值、每 0.1 s 变一次。
        tt = (int(round((remain - int(remain)) * 10)) % 10) * 10
        key = (mm, ss, tt)
        hit = self._frames.get(key)
        if hit is not None:
            return hit

        b0, b1 = self.BAND0, self.BAND1
        layer = Image.new("RGBA", (W, b1 - b0), (0, 0, 0, 0))
        for cx, txt in zip(CX, (u"%02d" % mm, u"%02d" % ss, u"%02d" % tt)):
            tl = self._tile(txt)
            layer.alpha_composite(tl, (int(round(cx - tl.width / 2.0)),
                                       int(round(CY - tl.height / 2.0)) - b0))
        arr = np.asarray(layer, dtype=np.float64)
        arr[..., :3] *= self._scan
        arr[..., :3] = np.clip(arr[..., :3] * (1.0 + self._noise), 0, 255)
        # 素材的数字**不是锐的**（CRT 上本来就有光斑扩散）⇒ 轻微失焦
        soft = Image.fromarray(arr[..., :3].astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.15))
        arr[..., :3] = np.asarray(soft, dtype=np.float64)
        glow = np.asarray(soft.filter(ImageFilter.GaussianBlur(3.2)), dtype=np.float64)
        out_band = self._plate_band * (1.0 - arr[..., 3:4] / 255.0) + arr[..., :3] * (arr[..., 3:4] / 255.0)
        out_band = np.clip(out_band + glow * 0.22 * (arr[..., 3:4] / 255.0), 0, 255)

        out = self._plate_u8.copy()
        out[b0:b1] = out_band.astype(np.uint8)
        # 静态文字层（含外发光）—— 只碰三条文字带
        for y0, y1, rgb, a, gl in self._ov_bands:
            seg = out[y0:y1].astype(np.float64)
            seg = seg * (1.0 - a) + rgb * a
            out[y0:y1] = np.clip(seg + gl * 0.30 * a, 0, 255).astype(np.uint8)
        img = Image.fromarray(out)
        self._frames[key] = img
        return img


# ================================================================ 时间线
class Sequence(object):
    u"""把「时间 t → 一帧」的规则收在一处（窗口与离线预览共用）。"""
    def __init__(self, countdown=T_CD, dump_dir=None):
        self.t_cd = float(countdown)
        self.cd = Countdown()
        self.video = VideoPipe()
        # ★★ `has_video` 必须**构造时快照**：第一版直接用 `video.ok`，而它读到 EOF 后会变 False
        #   ⇒ `total` 当场变短 ⇒ 序列在 7.5 s 处提前结束（离线验证④抓到：135 帧被判成 "end"）。
        self.has_video = bool(self.video.ok)
        self._memo = (None, None, None)     # (配方, 帧, 阶段)
        self._last = Image.new("RGB", (W, H), (0, 0, 0))
        self._white = None        # ★ 素材的白光帧（收束的源色，见 _collapse / _white_img）
        self.last_alpha = None
        self.dump_dir = dump_dir
        self.problems = []
        if self.cd.err:
            self.problems.append(self.cd.err)
        if self.video.err:
            self.problems.append(self.video.err)

    @property
    def total(self):
        u"""总时长。拨片不可用 ⇒ 跳过全屏播放段（宁可短，也不要黑屏干等 7 秒）。"""
        play = T_PLAY if self.has_video else 0.0
        return self.t_cd + T_FADE + T_ON + play + T_COLLAPSE

    # ---------- 配方（轻量：只看时间，不做任何重活）
    def _plan(self, t):
        u"""算出这一帧的「配方」—— 用来判断能不能**直接复用上一帧**。

        ★★ 第一版把 `_frame_keyed(t)` 写在比对之前 ⇒ 帧已经算完了才比 key ⇒ 记忆**永远命中不了**
          （离线验证④看到的"拨片 221 帧全是 51 ms"就是这个原因）。
        ★ 倒计时/拨片的配方粒度**故意取显示粒度**（0.1 s / 源帧号）：
          拨片 15 fps、显示 30 fps ⇒ 相邻两帧是同一张源帧，能省掉一半的 `post()`。
        """
        if self.cd.plate is None:
            return None                              # 降级路径不记忆
        if t < self.t_cd:
            remain = self.t_cd - t
            mm, ss = int(remain // 60), int(remain % 60)
            tt = (int(round((remain - int(remain)) * 10)) % 10) * 10
            return ("cd", mm, ss, tt)
        t -= self.t_cd
        if t < T_FADE:
            return ("fade", int(t * 60))
        t -= T_FADE
        vt = t
        if t < T_ON:
            return None                              # CRT 段几何每帧都变 ⇒ 不记忆
        t -= T_ON
        play = T_PLAY if self.has_video else 0.0
        if t < play:
            return ("play", int(vt * CLIP_VFPS))
        if t < play:
            return ("play", int(vt * CLIP_VFPS))
        return None                                  # 光收束段几何每帧都在变 ⇒ 不做记忆


    def frame(self, t):
        self.last_alpha = None       # ★ 收束阶段才有（径向淡出掩膜），其余阶段 None = 全不透明
        u"""返回 (PIL 图, 阶段名)。t 超过总时长 ⇒ (None, "end")。"""
        key = self._plan(t)
        if key is not None and key == self._memo[0]:
            return self._memo[1], self._memo[2]
        img, phase, _ = self._frame_keyed(t)
        if key is not None:
            self._memo = (key, img, phase)
        return img, phase

    def _frame_keyed(self, t):
        u"""真正算帧的地方。第三项 `key` 保留给调用方（这里恒 None，记忆由 `frame()` 管）。"""
        if self.cd.plate is None:                    # 底图都没了 ⇒ 退化成 CRT 只用拨片
            return self._crt_only(t)
        if t < self.t_cd:                            # S1 倒计时
            return self.cd.frame(self.t_cd - t), u"倒计时", None
        t -= self.t_cd
        if t < T_FADE:                               # 渐暗（把倒计时最后一帧压黑）
            f = np.asarray(self.cd.frame(0.0), dtype=np.float64) * (1.0 - t / T_FADE)
            self._last = Image.fromarray(f.astype(np.uint8))
            return self._last, u"渐暗", None
        t -= T_FADE
        vt = t                                       # ★ 拨片从「CRT 开机」那一刻开始走
        if t < T_ON:                                 # S2a CRT 开机（画面在动）
            p = t / T_ON
            wr, hr = on_geom(p)
            img = Image.fromarray(frame_for(self.video.get(int(vt * CLIP_VFPS)),
                                            wr, hr, on_gain(p)))
            self._last = img
            return img, u"CRT 开机", None
        t -= T_ON
        play = T_PLAY if self.has_video else 0.0
        if t < play:                                 # S2b 全屏播放
            img = Image.fromarray(frame_for(self.video.get(int(vt * CLIP_VFPS)), 1.0, 1.0, 1.0))
            self._last = img                         # ★ 收尾要渐黑的"最后一张"是它
            return img, u"拨片", None
        t -= play
        if t < T_COLLAPSE:                           # S3 白光收束（末帧不黑 ⇒ 直接交棒）
            img, al = self._collapse(t / T_COLLAPSE)
            self.last_alpha = al
            return img, u"光收束", None
        return None, u"end", None

    def _crt_only(self, t):
        u"""底图缺失时的降级：只有 CRT + 拨片（至少不是黑屏）。"""
        if t < T_FADE:
            return Image.new("RGB", (W, H), (0, 0, 0)), u"黑", None
        t -= T_FADE
        if t < T_ON:
            p = t / T_ON
            wr, hr = on_geom(p)
            img = Image.fromarray(frame_for(self.video.get(int(t * CLIP_VFPS)),
                                            wr, hr, on_gain(p)))
            self._last = img
            return img, u"CRT 开机", None
        t -= T_ON
        play = T_PLAY if self.has_video else 0.0
        if t < play:
            img = Image.fromarray(frame_for(self.video.get(int(t * CLIP_VFPS)), 1.0, 1.0, 1.0))
            self._last = img
            return img, u"拨片", None
        t -= play
        if t < T_COLLAPSE:
            img, al = self._collapse(t / T_COLLAPSE)
            self.last_alpha = al
            return img, u"光收束", None
        return None, u"end", None

    def _collapse(self, k):
        u"""S3：**整幅白光 → 向中心径向淡出**（2026-09-23 00:1x 主人改口径）。

        ★ 主人原话：「白光应该**铺满整个屏幕**……将整个开机动画**向中心方向淡出**，
          自然显露出主体来，而不是开机动画消失后**再蹦出来主体**」。
        ★ 做法：白光**始终铺满整个覆盖区**（不再缩成圆盘 —— 那会露黑边，主人叫它"内边"），
          只让 **alpha 从边缘向中心退**：`thr` 由 `1+SOFT` 线性降到 `−SOFT`，
          `alpha = clip((thr − d) / SOFT)`（d = 归一化到中心距离，四角 = 1）。
        ⇒ k=0 全不透明（满屏白）、k=1 全透明，中间是"从外往里吃掉"。
        ★ 返回 `(RGB 图, alpha 的 numpy 数组 0..1)`；主体由调用方（`play`）叠在下面。
        """
        k = max(0.0, min(1.0, float(k)))
        # ---- 白光本身：最前面一小段从"上一帧"闪到素材白光帧（避免硬切）----
        white = np.asarray(self._white_img(), dtype=np.float64)
        fl = min(1.0, k / max(1e-6, COLLAPSE_FLASH))
        if fl < 1.0:
            a = np.asarray(self._last, dtype=np.float64) * (1.0 - fl) + white * fl
        else:
            a = white
        # ---- 径向 alpha：边缘先透明、中心最后透明 ----
        thr = 1.0 + COLLAPSE_SOFT - k * (1.0 + 2.0 * COLLAPSE_SOFT)
        al = np.clip((thr - _DNORM) / COLLAPSE_SOFT, 0.0, 1.0)
        return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)), al

    def _white_img(self):
        u"""素材的**白光帧**（只取一次）。取不到、或取回来不够亮 ⇒ 退回纯白。

        ★ 为什么不用 `_last`：拨片停在帧 115（画面最亮处，但**不是白**），
          而素材的白光在帧 119。这里从当前位置继续推进 4 帧即可拿到
          （`VideoPipe.get()` 是单调推进的，不会超时）。
        """
        if self._white is not None:
            return self._white
        img = None
        try:
            img = Image.fromarray(frame_for(self.video.get(CLIP_WHITE_FRAME),
                                            1.0, 1.0, 1.0))
            if float(np.asarray(img, dtype=np.float64).mean()) < 150.0:
                img = None      # ★ 判据：均值 < 150 = 没取到白帧（超时拿到了旧帧）
        except Exception:
            img = None
        if img is None:
            img = Image.new("RGB", (W, H), (255, 255, 255))
        self._white = img
        return self._white

    def close(self):
        self.video.close()



# ================================================================ 窗口
WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
SWP_NOACTIVATE = 0x0010
SWP_NOMOVE = 0x0002                     # ★ 2026-09-23：raise_top 用
SWP_NOSIZE = 0x0001                     # ★ 同上
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
PM_REMOVE = 0x0001
WM_QUIT = 0x0012


# ★★ Win32 声明**照 `fairy_pet.py` 抄**（那套已经跑通）。用裸 c_void_p/c_uint 会出事：
#   `CreateWindowExW` 会同步发 WM_NCCREATE/WM_CREATE ⇒ 回调一调用就
#   `ArgumentError: int too long to convert` ⇒ 窗口返回 0 ⇒ 整个开机动画起不来（现场验证抓到的）。
class _POINT(ctypes.Structure):
    _fields_ = [(u"x", ctypes.c_long), (u"y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [(u"cx", ctypes.c_long), (u"cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [(u"BlendOp", ctypes.c_ubyte), (u"BlendFlags", ctypes.c_ubyte),
                (u"SourceConstantAlpha", ctypes.c_ubyte), (u"AlphaFormat", ctypes.c_ubyte)]



from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
LRESULT = ctypes.c_ssize_t
_WNDPROC_T = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM)

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, ctypes.c_void_p]
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ValidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                   wintypes.DWORD]
gdi32.SetDIBitsToDevice.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                    wintypes.DWORD, wintypes.DWORD, ctypes.c_int,
                                    ctypes.c_int, wintypes.UINT, wintypes.UINT,
                                    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(_POINT), ctypes.POINTER(_SIZE),
    wintypes.HDC, ctypes.POINTER(_POINT), wintypes.DWORD,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [(u"cbSize", ctypes.c_uint), (u"style", ctypes.c_uint),
                (u"lpfnWndProc", _WNDPROC_T), (u"cbClsExtra", ctypes.c_int),
                (u"cbWndExtra", ctypes.c_int), (u"hInstance", wintypes.HINSTANCE),
                (u"hIcon", wintypes.HICON), (u"hCursor", wintypes.HANDLE),
                (u"hbrBackground", wintypes.HBRUSH), (u"lpszMenuName", wintypes.LPCWSTR),
                (u"lpszClassName", wintypes.LPCWSTR), (u"hIconSm", wintypes.HICON)]


class _BMIH(ctypes.Structure):
    _fields_ = [(u"biSize", ctypes.c_uint32), (u"biWidth", ctypes.c_int32),
                (u"biHeight", ctypes.c_int32), (u"biPlanes", ctypes.c_uint16),
                (u"biBitCount", ctypes.c_uint16), (u"biCompression", ctypes.c_uint32),
                (u"biSizeImage", ctypes.c_uint32), (u"biXPelsPerMeter", ctypes.c_int32),
                (u"biYPelsPerMeter", ctypes.c_int32), (u"biClrUsed", ctypes.c_uint32),
                (u"biClrImportant", ctypes.c_uint32)]


_WNDPROC = _WNDPROC_T


class BootWindow(object):
    u"""独立**普通窗口**（16:9、无边框、不进任务栏）。

    ★ 为什么不做成分层窗口：`WS_EX_LAYERED` 父窗口会吞掉子控件（实测），而且开机动画是
      **不透明**的全覆盖面板 —— 用普通窗口 + `SetDIBitsToDevice` 最简单也最快。
    """
    CLS = u"FairyBootWndV1"

    def __init__(self, w, h, x, y, topmost=True):
        self.u, self.g = user32, gdi32
        self.w, self.h, self.x, self.y, self.topmost = int(w), int(h), int(x), int(y), topmost
        self.hwnd = None
        self.quit = False
        self._ulw_ok = None          # ★ UpdateLayeredWindow 的返回值（分层窗口自检）
        self._bits = None
        self._dib = None
        self._last = Image.new("RGB", (self.w, self.h), (0, 0, 0))
        self._proc = _WNDPROC_T(self._on_msg)     # ★ 必须保住引用，否则回调被 GC
        self._hinst = kernel32.GetModuleHandleW(None)

    def create(self):
        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = self._proc
        wc.hInstance = self._hinst
        wc.lpszClassName = self.CLS
        self.u.RegisterClassExW(ctypes.byref(wc))
        # ★★ WS_EX_LAYERED（2026-09-23 00:1x）：收束阶段要半透明地淡出
        #   —— 只有分层窗口能让“下面的主体”透出来；普通窗口只能硬切。
        ex = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
        if self.topmost:
            ex |= WS_EX_TOPMOST
        self.hwnd = self.u.CreateWindowExW(ex, self.CLS, u"Fairy Boot", WS_POPUP,
                                           self.x, self.y, self.w, self.h,
                                           None, None, self._hinst, None)
        if not self.hwnd:
            raise ctypes.WinError()
        # DIB（32bpp 顶朝下，不直接画到窗口 ⇒ 放到内存 DC 里）
        self._hdc_scr = self.u.GetDC(None)
        self._hdc_mem = self.g.CreateCompatibleDC(self._hdc_scr)
        bmi = _BMIH()
        bmi.biSize = ctypes.sizeof(_BMIH)
        bmi.biWidth = self.w
        bmi.biHeight = -self.h
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0
        self._bmi = bmi
        bits = ctypes.c_void_p()
        self._dib = self.g.CreateDIBSection(self._hdc_scr, ctypes.byref(bmi), 0,
                                            ctypes.byref(bits), None, 0)
        self._bits = ctypes.cast(bits, ctypes.POINTER(ctypes.c_ubyte))
        self.g.SelectObject(self._hdc_mem, self._dib)
        self.u.SetWindowPos(self.hwnd, HWND_TOPMOST if self.topmost else 0,
                            self.x, self.y, self.w, self.h, SWP_NOACTIVATE)
        self.u.ShowWindow(self.hwnd, 4)          # SW_SHOWNOACTIVATE
        self.u.UpdateWindow(self.hwnd)
        return self.hwnd

    def _on_msg(self, hwnd, msg, wp, lp):
        if msg == WM_ERASEBKGND:
            return 1                              # ★ 不擦背景 ⇒ 不闪
        if msg == WM_PAINT:
            self.u.ValidateRect(hwnd, None)       # ★ 分层窗口的内容由 UpdateLayeredWindow 供，不自己画
            return 0
        if msg == WM_DESTROY:
            self.quit = True
            self.u.PostQuitMessage(0)
            return 0
        return self.u.DefWindowProcW(hwnd, msg, wp, lp)

    def show(self, img, alpha=None):
        u"""贴一帧。`alpha` = L 图（None = 全不透明）。

        ★ 分层窗口要求 DIB 里的颜色是**预乘**的（AC_SRC_ALPHA）⇒ 这里做一次乘法。
        ★ `alpha` 允许是 PIL("L") 或 numpy 数组（`_collapse` 直接返回的就是 numpy 0..1）
          —— 但**缩放只对 PIL 做**：numpy 没有 `.resize`，以前尺寸一不等就在这里崩
          （`body is None` 时就会走到这条路），现在统一先转 PIL 再缩。
        """
        if img.size != (self.w, self.h):
            img = img.resize((self.w, self.h), Image.BILINEAR)
            if alpha is not None:
                if not isinstance(alpha, Image.Image):
                    alpha = Image.fromarray(np.clip(np.asarray(alpha, dtype=np.float64) * 255.0,
                                                    0, 255).astype(np.uint8), "L")
                alpha = alpha.resize((self.w, self.h), Image.BILINEAR)
        self._last = img
        a = np.asarray(img, dtype=np.uint16)
        if alpha is None:
            al = np.full((self.h, self.w), 255, np.uint8)
        else:
            al = np.clip(np.asarray(alpha, dtype=np.float64) * 255.0 + 0.5,
                         0, 255).astype(np.uint8)
        prem = (a[..., 0:3] * al[..., None].astype(np.uint16) // 255).astype(np.uint8)
        out = np.empty((self.h, self.w, 4), np.uint8)
        out[..., 0] = prem[..., 2]      # B
        out[..., 1] = prem[..., 1]
        out[..., 2] = prem[..., 0]      # R
        out[..., 3] = al
        ctypes.memmove(self._bits, out.tobytes(), out.size)
        ok = self.u.UpdateLayeredWindow(
            self.hwnd, self._hdc_scr,
            ctypes.byref(_POINT(self.x, self.y)),
            ctypes.byref(_SIZE(self.w, self.h)),
            self._hdc_mem, ctypes.byref(_POINT(0, 0)), 0,
            ctypes.byref(BLENDFUNCTION(0, 0, 255, 1)), 2)   # AC_SRC_OVER / AC_SRC_ALPHA / ULW_ALPHA
        if not ok and self._ulw_ok is None:
            self._ulw_ok = False
            print(u"[boot] ⚠ UpdateLayeredWindow 失败 ⇒ 分层窗口没上屏（动画会看不见）", flush=True)
        elif ok and self._ulw_ok is None:
            self._ulw_ok = True

    def raise_top(self):
        u"""把动画窗口重新顶到最上层。

        ★★ 2026-09-23 必需：桌宠窗口和动画窗口**都是 TOPMOST**，而 `ShowWindow(一个 TOPMOST 窗口)`
          会把它提到 TOPMOST 组的最前面 ⇒ 桌宠一"露面"就压在动画上面了
          （主人 09:43 截图：倒计时中间赫然站着一只 Fairy，还有拨片背景上也是）。
          `SetWindowPos(hwnd, None, ...)` **不改 Z 序**，必须显式传 `HWND_TOPMOST(-1)`。
        """
        if not self.hwnd:
            return
        try:
            self.u.SetWindowPos(self.hwnd, ctypes.c_void_p(-1),   # HWND_TOPMOST
                                0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        except Exception:
            pass

    def pump(self):
        u"""只泵**本窗口**的消息。"""
        m = wintypes.MSG()
        while self.u.PeekMessageW(ctypes.byref(m), self.hwnd, 0, 0, PM_REMOVE):
            if m.message == WM_QUIT:
                self.quit = True
                return False
            self.u.TranslateMessage(ctypes.byref(m))
            self.u.DispatchMessageW(ctypes.byref(m))
        return not self.quit

    def close(self):
        try:
            if self.hwnd:
                self.u.DestroyWindow(self.hwnd)
            if self._dib:
                self.g.DeleteObject(self._dib)
            self.u.UnregisterClassW(self.CLS, self._hinst)
        except Exception:
            pass
        self.hwnd = None


def drain_quit():
    u"""把线程消息队列**抽干**。

    ★★ 2026-09-22 真事故（主人："动画结束是白光，然后应该出现 fairy 本体，但是没有了"）：
      `BootWindow._on_msg` 在 `WM_DESTROY` 里执行了 `PostQuitMessage(0)`（= 常规写法），
      而 `play()` 的 finally 里 `close()` → `DestroyWindow()` 会**同步**触发 WM_DESTROY
      ⇒ 队列里留下一个 **WM_QUIT**。
      紧接着 `fairy_pet.run()` 建桌宠窗口、跑完第一帧 tick，进消息循环第一轮：
          `PeekMessageW(..., None, 0, 0, PM_REMOVE)` 取到的就是那个 WM_QUIT
      ⇒ `self._quit = True` ⇒ 立刻 break ⇒ 销毁窗口、进程退出。
      现场证据：`heartbeat.txt` 停在 `tick=1 ... boot=就绪`（tick 只跑了 1 帧）。

    ★ WM_QUIT 是**线程级**消息，`pump()` 里按 hwnd 过滤对它无效 —— 所以它一定漏到外面。
    ★ 必须在**播完动画之后、进桌宠消息循环之前**抽掉。`peek(hwnd=None)` 能取到它。
    ★ 这里**全清**而不是"只挑 WM_QUIT 放回别的"：此刻队列里剩下的（重播时的鼠标消息等）
      已经过时，放回去反而要维护顺序。清空是安全的。
    """
    m = wintypes.MSG()
    n = 0
    while user32.PeekMessageW(ctypes.byref(m), None, 0, 0, PM_REMOVE):
        n += 1
    return n


# ================================================================ 对外入口
def over_body(white, wa, body_rgb, body_a):
    u"""把「白光（带径向 alpha）」盖在「主体」上面。

    ★ 主体贴到画面**正中**（与桌宠窗口登场处同一个点）。
    ★ 两者都按 source-over 的**预乘**式算：`C = c1·a1 + c2·a2(1−a1)` ⇒ 再除回直色交给 `show()`。
      ★★ **注意两边的色值约定不一样**（2026-09-23 修过一个 bug）：
        - `white` 是**直色**（拨片/白光帧没有 alpha 通道）⇒ 要乘自己的 `w`；
        - `body_rgb` 是**预乘色**（`fairy_pet.play_boot` 从 `to_bgra()` 出来时已经是"直色×alpha"）
          ⇒ **只乘 `(1−w)`，不能再乘 `ba2`**。
          原先写成 `bp * ba2 * (1−w)` = 双重预乘 ⇒ 主体连同辉光被压暗成一片深蓝，
          现象正是「播完动画后本体带一坨深蓝方块」（主人 2026-09-23 09:0x 截图）。
    ★ 主体帧由 `fairy_pet` 现场渲染（窗口还没建，但渲染器已经在）。
    """
    W_, H_ = white.size
    bw, ba = body_rgb.width, body_rgb.height
    wrgb = np.asarray(white, dtype=np.float64)
    w = np.asarray(wa, dtype=np.float64)
    bp = np.zeros((H_, W_, 3), dtype=np.float64)
    ba2 = np.zeros((H_, W_), dtype=np.float64)
    ox, oy = (W_ - bw) // 2, (H_ - ba) // 2
    ex, ey = min(W_, ox + bw), min(H_, oy + ba)
    if ex > ox and ey > oy:
        bp[oy:ey, ox:ex] = np.asarray(body_rgb, dtype=np.float64)[:ey - oy, :ex - ox]
        ba2[oy:ey, ox:ex] = (np.asarray(body_a, dtype=np.float64)[:ey - oy, :ex - ox] / 255.0)
    out_a = w + ba2 * (1.0 - w)
    den = np.maximum(out_a, 1e-6)[..., None]
    prem = wrgb * w[..., None] + bp * (1.0 - w)[..., None]
    rgb = np.clip(prem / den, 0, 255).astype(np.uint8)
    a8 = np.clip(out_a * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, "RGB"), Image.fromarray(a8, "L")


def cover_rect(pet_x, pet_y, pet_win, vx0, vy0, vx1, vy1):
    u"""算出「16:9 覆盖桌宠区」的窗口矩形，并夹在虚拟桌面内。

    ★ 必须夹取：桌宠贴屏幕边时，覆盖窗口（比桌宠宽）会有一半跑到屏幕外。
    """
    w = int(round(pet_win * 16.0 / 9.0))
    h = int(pet_win)
    x = pet_x + (pet_win - w) // 2
    y = pet_y + (pet_win - h) // 2
    x = max(vx0, min(x, vx1 - w))
    y = max(vy0, min(y, vy1 - h))
    return w, h, int(x), int(y)


class Splash(object):
    u"""烘焙期间的倒计时窗 —— **独立一层，不进正式时间线**。

    ★★ 主人 2026-09-23：「开机动画真正的**倒计时应该跟烘焙时间相等**……比如双击 start，
      平面应该**先出现倒计时窗口**」。
    现状的毛病：烘焙在 `FairyPet.__init__` 里，首次（缓存不在）要 90~100 s，
    而这段时间**一个窗口都没有** ⇒ 双击后对着黑屏干等。

    用法（`__main__`）：先 `begin()` 建窗口，再构造 `FairyPet(bake_progress=splash.tick)`
    —— `fairy_layers._bake()` 的回调签名正好是 `(fraction, label)`，
    且注释里写明"每调用一次 = 一个可插帧的点"；烘焙完 `close()`，随后照常播开机动画。

    ★ 倒计时**按真实进度外推**：`remain = 已用时间 × (1−f) / f`
      ⇒ 硬件快就短、慢就长，不用写死 90 秒。
    ★ 有缓存时 0.3 s 就完事 ⇒ `show_after` 之前**一个字都不显示**，不会闪。
    ★ 建窗口（`CreateWindowExW`）本身不显示 —— 显示只发生在 `win.show()`（UpdateLayeredWindow），
      所以"先建、够久了再 show"是天然成立的。
    """

    def __init__(self, pet_win, topmost=True, show_after=0.60):
        # ★★ 2026-09-23：尺寸与位置**全部交给 `fairy_screen`**（唯一真源）。
        #   `SCR.center(pet_win)` 给的是「**边长 pet_win 的窗口**在主屏正中的左上角」——
        #   注意 `cover_rect()` 期望的正是**左上角**（它会把窗口撑到比桌宠宽），
        #   所以必须传这个值；直接传"屏幕中心"会让整个窗口**偏右**（主人抓到过两次）。
        #   ★ 多屏时"中央" = **主屏**的中央：虚拟屏中心落在两块屏中间，就是那个 bug。
        if SCR is not None:
            px_, py_ = SCR.center(pet_win)
            vx0, vy0, vx1, vy1 = SCR.virtual()
        else:                                     # 兜底：老写法（主屏几何中心）
            _u = ctypes.windll.user32
            sw, sh = _u.GetSystemMetrics(0), _u.GetSystemMetrics(1)
            px_, py_ = sw // 2 - pet_win // 2, sh // 2 - pet_win // 2
            vx0, vy0 = _u.GetSystemMetrics(76), _u.GetSystemMetrics(77)
            vx1, vy1 = vx0 + _u.GetSystemMetrics(78), vy0 + _u.GetSystemMetrics(79)
        self.cw, self.ch, self.cx, self.cy = cover_rect(px_, py_, pet_win,
                                                        vx0, vy0, vx1, vy1)
        self.win = BootWindow(self.cw, self.ch, self.cx, self.cy, topmost=topmost)
        self.cd = Countdown()
        self.show_after = float(show_after)
        self.t0 = None
        self.visible = False
        self.ok = False
        self.peak = 0.0                     # 记录最慢的一次外推，给日志用

    def begin(self):
        try:
            self.win.create()
            self.t0 = time.perf_counter()
            self.ok = bool(self.cd.plate is not None)
            return self.ok
        except Exception as e:
            print(u"[boot] splash 建窗失败（忽略）：%r" % (e,), flush=True)
            self.ok = False
            return False

    def tick(self, frac, label=u""):
        u"""烘焙进度回调（`_bake` 每次报点都会走到这里，包括 `frac=1.0`）。

        ★★ 2026-09-23 主人：「首次倒计时应该在倒计时 3 秒的位置结束，无缝切换到开机动画的
          3 秒倒计时，这样就能衔接上了」。实现：**显示值 = 烘焙剩余 + 开机动画倒计时长度(T_CD)**
          ⇒ 烘焙恰好跑完那一刻正好数到 `03`，紧接着开机动画从 `03` 开始往下数
          ⇒ 数字连成一条线：既没有"00 之后又冒出一个新倒计时"，中间也没有空档。
          （烘焙剩余仍**按真实进度外推**，节奏是真实的，只是整段加了动画那 3 秒的尾巴；
            不这么做就得让动画的倒计时和烘焙抢同一个线程，那是另一种复杂。）
        """
        if not self.ok:
            return
        try:
            el = time.perf_counter() - self.t0
            if (not self.visible) and el >= self.show_after:
                self.visible = True
                print(u"[boot] 首次烘焙中 → 出倒计时窗（已等 %.1f s，当前 %.0f%% %s）"
                      % (el, 100.0 * float(frac), label), flush=True)
            if not self.visible:
                return
            f = max(1e-3, min(1.0, float(frac)))
            remain = max(0.0, el * (1.0 - f) / f)      # ★ 按进度比例外推剩余秒数
            self.peak = max(self.peak, remain)
            self.win.show(self.cd.frame(remain + T_CD))   # ★ +T_CD：接到动画的倒计时上
            self.win.pump()
        except Exception:
            pass

    def close(self):
        try:
            self.win.close()
        except Exception:
            pass
        try:
            drain_quit()                    # ★ 必须：DestroyWindow 会留下一条线程级 WM_QUIT
        except Exception:
            pass
        self.ok = False


def play(pet_x, pet_y, pet_win, vx0, vy0, vx1, vy1,
         countdown=T_CD, topmost=True, pulse=None, dump_dir=None, log=print,
         body=None, on_frame=None, on_ready=None):
    u"""播完整段开机动画（阻塞到播完）。返回 (ok, 用时, 阶段统计)。

    `pulse`：每 ~15 帧调一次（给桌宠写心跳用 —— 否则 12 s 不写心跳，会被
      `check_fairy.vbs` / 单实例逻辑误判成"死了"）。
    `on_frame`：**每帧**调一次（给桌宠渲染用）。★★ 2026-09-23 加：
      原来动画是纯阻塞的 ⇒ 桌宠窗口停在进动画前那一帧、整整 12 s 不动；
      而收束擦除露出的是**它**（因为现在动画窗口盖在桌宠上面、擦掉后露真窗口）
      ⇒ 主人看到"中央先卡一张定格图，然后本体才活过来"。
      现在动画每出一帧就让桌宠也渲染一帧，露出来的是**活的**本体。
    `on_ready`：**动画窗口建好之后立刻**调一次。★★ 2026-09-23 加：
      建窗口 + 起视频管道要几百毫秒；桌宠必须**等这之后**再显示，
      否则那几百毫秒里本体已经可见了 —— 会被看到"先露一两帧再被动画盖住"
      （主人 09:40：「开机动画之前，有那么一两帧时间本体先露出来了」）。
    """
    t0 = time.perf_counter()
    cw, ch, cx, cy = cover_rect(pet_x, pet_y, pet_win, vx0, vy0, vx1, vy1)
    seq = Sequence(countdown=countdown, dump_dir=dump_dir)
    for p in seq.problems:
        log(u"[boot] 提示：%s" % p)
    win = BootWindow(cw, ch, cx, cy, topmost=topmost)
    stats = {}
    try:
        win.create()
        # ★★ 2026-09-23：动画窗口现在已经能盖住屏幕了 ⇒ **此刻才允许桌宠露面**。
        #   必须在 `win.create()` 之后：更早的话桌宠一旦可见，就会被看到
        #   "先露一两帧本体、再被动画盖住"（主人 09:40 指出的现象）。
        if on_ready is not None:
            try:
                on_ready()
            except Exception:
                pass
            # ★ 桌宠一露面就会靠 `ShowWindow` 顶到 TOPMOST 组最前 ⇒ 立刻把动画窗口抢回来，
            #   否则倒计时/拨片中间会赫然站着一只 Fairy（主人 09:43 截到的）。
            win.raise_top()
        log(u"[boot] 窗口 %dx%d @(%d,%d) ｜ 时间线 %.2f s（倒计时 %.1f）"
            % (cw, ch, cx, cy, seq.total, countdown))
        n = 0
        t_start = time.perf_counter()
        interval = 1.0 / FPS
        next_t = t_start
        last_frame_t = -1.0
        while True:
            now = time.perf_counter()
            if not win.pump():
                break
            if now >= next_t:
                next_t += interval
                if next_t < now:
                    next_t = now + interval
                t = now - t_start
                if abs(t - last_frame_t) > interval * 0.5:
                    last_frame_t = t
                img, phase = seq.frame(t)
                al = seq.last_alpha
                if al is not None and body is not None:
                    img, al = over_body(img, al, body[0], body[1])   # ★ 主体在下面，被“露出来”
                if img is None:
                    break
                stats[phase] = stats.get(phase, 0) + 1
                win.show(img, al)
                n += 1
                # ★★ 2026-09-23（主人："刚 restart，图层上下又出错了"）：**定期自愈 Z 序**。
                #   两个窗口都带 WS_EX_TOPMOST，而"`ShowWindow` 一个 TOPMOST 窗口"会把它
                #   顶到 TOPMOST 组的最前面 ⇒ 只要桌宠那边有任何一次 ShowWindow
                #   （弹通知卡 / 被 poke / 窗口自愈），屏幕中央就会赫然站着一只 Fairy，
                #   动画被压在下面。桌宠那边已加 `_boot_guard` 从源头拦住；
                #   这里再加一道兜底：每 4 帧把动画窗口抢回最前（SetWindowPos 不激活、不移动，
                #   实测代价约 50 µs，可以忽略）。
                if n % 4 == 0:
                    win.raise_top()
                # ★★ 2026-09-23：让调用方（桌宠）**在动画期间照常渲染一帧**。
                #   否则桌宠窗口会停在进动画前那一帧、12 s 不动 ——
                #   而擦除后露出的正是它 ⇒ 主人看到的是"一张定格图"，摸不着头脑。
                if on_frame is not None:
                    try:
                        on_frame()
                    except Exception:
                        pass
                if pulse and (n % 15 == 0):
                    try:
                        pulse()
                    except Exception:
                        pass
                if dump_dir and (n % 10 == 0):
                    try:
                        os.makedirs(dump_dir, exist_ok=True)
                        img.save(os.path.join(dump_dir, u"f%03d_%s.png" % (n, phase)))
                    except Exception:
                        pass
            else:
                time.sleep(0.001)
        el = time.perf_counter() - t_start
        log(u"[boot] 播完 %d 帧 / %.2f s（实测 %.1f fps）｜ 各阶段帧数 %s"
            % (n, el, n / max(1e-6, el), stats))
        return True, time.perf_counter() - t0, stats
    except Exception as e:
        log(u"[boot] 失败（已忽略，不影响桌宠）：%r" % (e,))
        return False, time.perf_counter() - t0, stats
    finally:
        try:
            win.close()
        except Exception:
            pass
        # ★★ 必做：close() 里的 DestroyWindow 会在队列里留下一个 WM_QUIT（见 drain_quit 的说明），
        #   不清掉的话调用方（桌宠）的消息循环第一轮就被踢出去 —— 现场表现就是"白光之后本体不见了"。
        try:
            drain_quit()
        except Exception:
            pass
        seq.close()


if __name__ == "__main__":
    # 供现场测试用：在固定位置播一次短的（`_work/step62_boot_live.py` 会拉起它）
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="400,300")
    ap.add_argument("--win", type=int, default=370)
    ap.add_argument("--cd", type=float, default=T_CD)
    ap.add_argument("--dump", default=None)
    a = ap.parse_args()
    px, py = [int(v) for v in a.at.split(",")]
    ok, el, st = play(px, py, a.win, 0, 0, 3840, 2160, countdown=a.cd,
                      dump_dir=a.dump, log=lambda m: print(m, flush=True))
    print(u"ok=%s  %.2f s  %s" % (ok, el, st))
