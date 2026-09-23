# -*- coding: utf-8 -*-
"""工作态进度条 —— **生产渲染器**（设计稿与预览在 `step20_bar_v6.py`）。

★ 单一真源：主程序与设计稿脚本都从这里取，避免两边各写一份、越走越远。

## 结构（三层，照主人 2026-09-22 三轮反馈定稿）

| 层 | 内容 | 关键约束 |
|---|---|---|
| 外框 | 方角，每边 **1 px 深线 + 1 px 亮线** | 浅色桌面上也读得清 |
| **色块** | 整块：外框内 = 槽底（纵向渐变）+ 填充（横向渐变） | **一根条纹都不画**（实测相邻行跳变 0.00/255） |
| **横纹** | **独立一层**，`alpha_composite` 叠在上面 | **只有一组水平纹**；**零均值**（亮带叠亮色、暗带叠暗色） |

## 主人的定档（勿擅改）

- **条宽 = 通知卡宽** = `S − 32 px`（同一个 `m = 16`）⇒ **画布不用加宽**。
- 宽高比 **17.6:1**（参照图实测 1054:60）。
- **横纹间距 2.00 px @260px**（主人选的"最密那版"），换成 unit 是 `BAR_MOIRE_PER_U`，随尺寸自动缩放。
  ⚠️ 与辉光原值（6.66 px）差 3.33 倍 —— 这是主人**明确选定**的取舍。
- **横纹浓淡 `BAR_MOIRE_PEAK = 0.10`**（0.32 → 0.14 → 0.098 → **凑整 0.10**）。
- 位置：**通知卡在上、进度条在下**（卡片短命、进度条常驻）。

## 三条踩过的坑（别再犯）

1. **别给直条安"第二组倾斜纹"**：辉光的摩尔纹是 `角向纹 + 水平纹`；**直条没有圆心**，
   硬造第二组会形成时强时弱的包络 ⇒ 看起来就是"中间间隔断开"。
   直条只留一组水平纹：`cos(2π(y/PER − DY·u + φ))`，**与 x 无关** ⇒ 沿整条逐行均匀。
2. **别用"超采样 + LANCZOS 降采样"做抗锯齿**：核宽会把细纹整个平均掉
   （实测峰谷只剩 12/255）⇒ 用**解析抗锯齿** `sinc(1/per)`。
3. **填充的软过渡必须挪到边界"之外"**（`+ 1.0`）：否则边界前还留一段半透明，
   100% 时最右端会"空着"（主人抓到过）。判据用**最右列纵向斜率**，别用"相邻行最大差"。

## 性能

`BarView` 预烘 **101 档色块** + **90 档横纹相位**（7.5 s / 90 ≈ 83 ms），
每帧只做 2 次 `alpha_composite`（+ 过渡期一次 alpha 缩放）。数字层按需缓存。
"""
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import dsh_mascot as M

# ---------------------------------------------------------------- 几何（按画布 S 归一化）
MARGIN_PX_AT_370 = 16.0                 # ★ 与通知卡 `fairy_pet._draw_note` 里的 m 同值（**只通知卡还在用**）
REF_S = 370.0
MARGIN_F = MARGIN_PX_AT_370 / REF_S     # 保留给旧脚本引用；**条宽不再用它**（见下）
# ---------------------------------------------------------------------------------
# ★★ 条宽：2026-09-22 主人第二次改定 —— 从"与通知卡等宽"改成"**与主体等宽**"。
#   主人从截图红框实测：红框宽 / 外盘可见外缘宽 = **1.112** ⇒ 条宽 = 1.112 × 137.2 unit ≈ **152.6 unit**
#   （画布 228 unit ⇒ 占 0.669）。原来是 208.3 unit（照通知卡：`S−32`，260px 档 338 px）。
#   ⚠️ 副作用：**通知卡仍按 m=16 画**，所以卡比条宽（会探出条外 57 px @260px 档）。
#      主人若要把卡一起收窄，说一声即可（改 `fairy_pet._draw_note` 的 m 与卡内文字换行宽度）。
DISC_W_U = 2.0 * M.OUTER_VISIBLE_EDGE   # 主体最外直径 = 2 × 68.6 = 137.2 unit（含白描边）
BAR_W_RATIO = 1.000                     # ★ 条宽 = 主体最外直径（主人 2026-09-22 明确："红框只是示意，
#                                          意思是宽度调到跟主体最外直径一样"）
#   量测留痕：第一张示意图红框 = 外盘 1.112×（我据此做过一版），第二张 = 1.042×；
#   主人说红框**只是示意** ⇒ 目标就是 **1.000**（正好等于最外直径）。
CANVAS_U = 160.0 + 2.0 * M.CANVAS_MARGIN_U      # 画布 = 160 + 2×34 = 228 unit
BAR_W_F = (BAR_W_RATIO * DISC_W_U) / CANVAS_U   # ≈ 0.6018（条宽 / 画布宽）
BOTTOM_F = 15.0 / REF_S                 # 底边距 / 画布（**无副标题**时的老定档）
CARD_GAP_F = 7.0 / REF_S                # 进度条与通知卡之间的间隙
ASPECT = 17.6                           # 宽 / 高（参照图实测）—— 定义的是**基准高**
# ---------------------------------------------------------------- 三个独立旋钮（主人 2026-09-22）
#   ★★ 关键设计：**字号一律挂在"基准高"（w/ASPECT）上，不挂在实际条高上** ——
#      否则"条加高"会把字号一起放大，两个旋钮互相干扰，主人就没法单独调。
#        · BAR_H_GAIN：条高 = 基准高 × 这个（1.00 = 复原参照图的 17.6:1；2.00 = 高度翻倍）
#        · NUM_F       ：百分比数字字号 = 基准高 × 1.35（**原式**，别改）
#        · NUM_GAIN    ：数字字号再乘这个（1.00 = 原大小）
#        · CAP_GAIN    ：副标题字号 = 基准高 × 这个（原 0.85；现 0.85×1.5 = 1.275）
BAR_H_GAIN = 2.00                       # ★ 主人："进度条高度加宽（高）1 倍"
NUM_F = 1.35                            # 数字字号 / 基准高（原来的关系式）
NUM_GAIN = 1.65                         # ★ 主人第四改："用 1.1 的挡位"（1.50 × 1.10 = 1.65）
#   整数像素下 ×1.08 与 ×1.10 同落 **28 px**（h_base 12.6705 × 1.35 × 1.65 = 28.22 ⇒ 28）；
#   四档参照（`_work/step38_num_size.py`）：1.50⇒26 / 1.56⇒27 / 1.62⇒28 / 1.68⇒29 px。
CAP_GAIN = 0.85 * 1.50                  # ★ 主人："英文字体放大到现在的 1.5 倍"（原 0.85 ⇒ 现 1.275）

# ---------------------------------------------------------------- 副标题（设计稿 v4 就有，v6 漏接）
# ★ 主人 2026-09-22 指出"进度条下面没有 FAIRY WORKING 的字样" —— 一查：这三个字
#   只在 `step17_hdd_progress.py`（v4 设计稿）里，**生产渲染器从来没接**。
#   现在按设计稿的样式补回：条**下方**、居中、描边斜体（深浅桌面都读得清）。
CAPTION = "FAIRY WORKING"
CAPTION_ENABLE = True
CAP_TRACK = 0.24                        # 字距 —— 照设计稿的 SUB_TRACK
CAP_GAP_F = 0.35                        # 条底 → 副标题顶 的间隙 / 条高
CAP_BOTTOM_F = 0.35                     # 副标题底 → 画布底 的留白 / 条高
#   ★ 2026-09-22 主人第四改：「进度条和主体距离可以增加一点点」⇒ 0.55 → 0.35。
#     原理：画布高度固定 ⇒ 只能把「条+副标题」整块**下移**（减小底部留白）才能拉开与主体的距离。
#     实测：条顶距主体底 260px 5.5→10.5 ｜ 200px 4.3→7.3 ｜ 320px 6.8→12.8 px；
#     副标题底距画布底仍有 7~11 px ⇒ 四边 alpha 依然为 0（不会出方框）。
#   ★ 为什么不能沿用 `BOTTOM_F`：那条留白只有 15/370·S ≈ 11 px @200px，
#     再塞一行字就顶到画布边（画布边必须 alpha=0，否则又出方框）。
#     所以加副标题时**整条往上挪**，由下面三个比例算 y。

# ---------------------------------------------------------------- 颜色（参照图实测）
C_OUTER = (8, 26, 62)                   # 外圈深线
C_FRAME = (227, 245, 253)               # 亮青白细边
C_SLOT_T = (30, 66, 108)                # 未填充：上暗
C_SLOT_B = (96, 132, 176)               # 未填充：下亮
C_FILL_L = (44, 94, 156)                # 填充：左端暗
C_FILL_R = (136, 190, 230)              # 填充：右端亮
C_MOIRE_HI = (232, 248, 255)            # 横纹亮带
C_MOIRE_LO = (16, 42, 88)               # 横纹暗带
SLOT_H_FAR = 0.80                       # 未填充区越往右越暗
FILL_FADE = 0.03                        # 填充右端过渡宽 / 条宽（参照图实测约 1.6%）

# ---------------------------------------------------------------- 横纹
#   ★★ 2026-09-22 主人第 4 次调横纹：「之前的不清晰是被横纹搞的，**变密+减淡**估计就出效果了」
#      实测支撑：横纹在条面上造成**相邻行 10.9 级**的亮度跳变（134.7 ↔ 145.6），
#      而数字字身不透横纹（差 0.03 级）⇒ 干扰集中在**字边一圈**（抗锯齿 + 发光带）。
#      加密 ⇒ 单条纹更细、更像材质而非「条纹布」；减淡 ⇒ 跳变幅度直接降到约 1/2。
BAR_MOIRE_PER_U = 1.40 / (260.0 / 160.0)     # 1.40 px @260px（原 2.00 ⇒ 加密 1.43 倍）
BAR_MOIRE_PEAK = 0.060                       # ★ 0.10 → 0.06（减淡 40%）
BAR_MOIRE_AA = True                          # 解析抗锯齿 sinc(1/per)
BAR_MOIRE_DY = M.WORK_MOIRE_DY               # 7.5 s 内走过的周期数（整数 ⇒ 与辉光同周期闭合）
BAR_MOIRE_PH = M.WORK_MOIRE_PH_B
MOIRE_PHASES = 90                            # 相位表帧数（7.5 s / 90 ≈ 83 ms）
BAR_MOIRE_CANDS = [0.200, 0.140, 0.100, 0.070, 0.050, 0.032]   # 供对照用

# ================================================================ 出现动画（2026-09-23 00:0x 主人改口径）
# ★★ 主人：「**先不搞数字堆积成动画这套了，直接用类似 ppt 的动画模板，从画面中淡入的方式做**」
#   —— 前四轮都在做「01 数字凝成条」（7px 方格 → 字符网格 → 从下往上 → 复用工态图集），
#      主人看了四版都不满意：数字跟工作态的颜色/扩散形态**对不上**。
#   ⇒ 回到最朴素、最不会出错的做法：**PPT 的「淡入」** ——
#     整块（条 ＋ 数字 ＋ 英文）**一起淡入**，配一点点上浮（PPT 的「浮入」），时长 0.90 s。
#   ★★ 教训：**「像工作态的数字」这件事，靠「我再造一套」是做不到的** ——
#      要么真的复用同一份渲染（第四轮试过，仍不满意），要么就**不要数字**。
REVEAL_S = 0.90
REVEAL_FADE_W = 0.80        # 淡入占整段的比例（余下 20% 只做位移收尾 ⇒ 落位更稳）
REVEAL_FLOAT_PX = 9.0       # ★ 上浮位移（px）：起点比终点低这么多（PPT「浮入」的手感）
#   ★ 把它设成 0 就是**纯淡入**（不带位移）—— 预览脚本用它出对照片。


def _ramp(u, at, w):
    """从 `at` 起、历时 `w` 的 smoothstep 0→1。出现动画的所有"淡入淡出/位移"都用它，
    好处是：**同一个函数、同一个形状**，各条曲线的节奏天然对齐（不要一个 ease-out、
    一个 linear，那样两条曲线永远对不上）。"""
    t = max(0.0, min(1.0, (float(u) - at) / max(1e-6, float(w))))
    return t * t * (3.0 - 2.0 * t)


def geom(S, cap_h=0):
    """→ (w, h, x, y)：进度条在画布上的位置与尺寸。

    `cap_h` = 副标题位图的高度（0 = 不带副标题，沿用旧定档的底边距）。
    ★ 带副标题时条会**整体上移**，让"条 + 副标题"一起落在画布内、且四边留白 ≥ 若干 px
      —— 画布边缘必须保持 alpha=0，否则分层窗口会出现方框。
    """
    S = float(S)
    w = max(24, int(round(S * BAR_W_F)))     # ★ 条宽 = 主体等宽（主人 2026-09-22 定），不再用 MARGIN_F
    h = max(6, int(round(w / ASPECT * BAR_H_GAIN)))   # ★ 条高 = 基准高 × BAR_H_GAIN（主人定 2.0）
    x = int(round((S - w) / 2.0))
    if cap_h:
        gap = max(2, int(round(h * CAP_GAP_F)))
        bot = max(3, int(round(h * CAP_BOTTOM_F)))
        y = int(round(S - bot - cap_h - gap - h))
    else:
        y = int(round(S - h - BOTTOM_F * S))
    return w, h, x, y


_CAP = {}


def caption_rgba(px):
    """副标题位图（描边斜体 + 内外发光），按字号缓存 —— 与 `number_rgba` 同一套字体处理。"""
    key = int(px)
    hit = _CAP.get(key)
    if hit is not None:
        return hit
    import step17_hdd_progress as S17
    s = TEXT_STYLE["cap"]
    _dk, _lk = _stroke_k(s, key)
    t = S17.styled_text(CAPTION, key, track=CAP_TRACK,
                        glow_r=s["glow_r"], glow_a=s["glow_a"],
                        dark_k=_dk, light_k=_lk,
                        face_top=s["face_top"], face_bot=s["face_bot"],
                        dark_col=s["dark_col"], edge_col=s["edge_col"],
                        dark_a=s["dark_a"],
                        fonts=S17.FONT_SMALL)
    _CAP[key] = t
    return t


def thin(per_px):
    """一个像素的方框对周期 per_px 的正弦积分 = sinc(1/per) —— 解析抗锯齿系数。"""
    return float(np.sinc(1.0 / max(1.4, per_px))) if BAR_MOIRE_AA else 1.0


def _grad_v(h, w, c_top, c_bot):
    t = np.linspace(0.0, 1.0, h).reshape(h, 1, 1)
    return ((1.0 - t) * np.array(c_top, float).reshape(1, 1, 3)
            + t * np.array(c_bot, float).reshape(1, 1, 3))


def solid(w, h, pct):
    """★ 整块色块（外框 + 槽底 + 填充）。**一根条纹都不画**。→ (RGBA 图, 内区 box)。"""
    w, h = int(round(w)), int(round(h))
    ix0, iy0 = 2, 2
    iw, ih = max(1, w - 4), max(1, h - 4)

    # ① 未填充：**纵向**渐变 × 越往右越暗
    slot = _grad_v(ih, iw, C_SLOT_T, C_SLOT_B) * np.linspace(1.0, SLOT_H_FAR, iw).reshape(1, iw, 1)
    # ② 填充：**横向**渐变、**纵向完全均匀**（参照图的关键特征）
    fx = np.linspace(0.0, 1.0, iw).reshape(1, iw, 1)
    fill = np.broadcast_to((1.0 - fx) * np.array(C_FILL_L, float).reshape(1, 1, 3)
                           + fx * np.array(C_FILL_R, float).reshape(1, 1, 3), (ih, iw, 3))
    # ③ 按进度取用。★ `+ 1.0`：软过渡在边界**之外**（否则 100% 时最右端会空着）
    fw = iw * float(np.clip(pct, 0.0, 1.0))
    xs = np.arange(iw, dtype=float)[None, :] + 0.5
    a_fill = np.clip((fw - xs) / max(1.0, iw * FILL_FADE) + 1.0, 0.0, 1.0)
    inner = slot * (1.0 - a_fill[..., None]) + fill * a_fill[..., None]

    arr = np.zeros((h, w, 3), np.uint8)
    arr[iy0:iy0 + ih, ix0:ix0 + iw] = np.clip(inner + 0.5, 0, 255).astype(np.uint8)
    im = Image.fromarray(arr, "RGB").convert("RGBA")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w - 1, h - 1], outline=C_OUTER + (215,), width=1)
    d.rectangle([1, 1, w - 2, h - 2], outline=C_FRAME + (252,), width=1)
    return im, (ix0, iy0, iw, ih)


def moire_alpha(w, h, phase_u, per_px):
    """横纹层的不透明度图（0..1）。**只有一组水平纹**，与 x 无关 ⇒ 沿条长完全均匀。"""
    u = float(phase_u) % 1.0
    per = max(1.4, float(per_px))
    ys = np.arange(h, dtype=float)[:, None]
    ph = ys / per - BAR_MOIRE_DY * u + BAR_MOIRE_PH
    m = 0.5 + 0.5 * thin(per) * np.cos(2 * np.pi * ph)
    return np.clip(np.broadcast_to(m, (h, w)), 0.0, 1.0)


def moire_rgba(w, h, phase_u, per_px, peak=BAR_MOIRE_PEAK):
    """合成好的横纹叠加层（亮带 + 暗带已合到一张 RGBA 上；零均值 ⇒ 不整体提亮色块）。"""
    m = moire_alpha(w, h, phase_u, per_px)
    hi = np.clip((m - 0.5) * 2.0, 0.0, 1.0) * peak
    lo = np.clip((0.5 - m) * 2.0, 0.0, 1.0) * peak
    ov = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    for a, col in ((hi, C_MOIRE_HI), (lo, C_MOIRE_LO)):
        lay = Image.new("RGBA", (int(w), int(h)), col + (255,))
        lay.putalpha(Image.fromarray(np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8), "L"))
        ov.alpha_composite(lay)
    return ov


def bar_image(w, h, pct, t_ms, ppu, per_px=None, peak=BAR_MOIRE_PEAK):
    """完整的一条（色块 + 横纹）。设计稿脚本用；主程序走 `BarView`（带缓存）。"""
    im, box = solid(w, h, pct)
    per = float(per_px) if per_px else BAR_MOIRE_PER_U * ppu
    ov = moire_rgba(box[2], box[3], (t_ms / M.GLOW_CYCLE_MS) % 1.0, per, peak)
    im.alpha_composite(ov, (box[0], box[1]))
    return im


_NUM = {}
_NUM_CAP = 48                    # 缓存上限（★ 别用 `clear()`：那会让**每帧**都重渲染数字图，
                                 #   实测 3.4 ms/帧 —— 我第一版就是这么栽的）


# ---------------------------------------------------------------- 文字特效（2026-09-22 定稿）
#   三次迭代的结论（别再走回头路）：
#   ① 旧版「**白字身 + 近黑粗边**」:`dark_k=0.18` ⇒ 26px 字号时深边 **4.7 px** ⇒ 主人「黑边太大」；
#      但**字身是好的**（白 → 浅蓝渐变），主人后来看到「深字身」版直接说「太丑了，没之前的好」。
#   ② 深色版「深字身 + 粗白边」(`dark_k=0.055/light_k=0.165`) ⇒ 压在浅蓝条上变成深色贴纸，
#      主人否掉 ⇒ **字身回滚到旧版浅色渐变**。
#   ③ ★ 主人真正的诊断：「之前的不清晰是**被横纹搞的**」—— 实测证实：字身 α=255 不透横纹
#      （含/不含横纹的逐行差 **0.03 级**），但横纹在条面上造成 **相邻行 10.9 级** 的亮度跳变
#      （134.7 ↔ 145.6），数字**字边一圈**（抗锯齿 + 发光）就被这个量级的噪声包着 ⇒ 显得毛糙。
#      ⇒ 对策不是改字，是 **横纹加密 + 减淡**（见下方 BAR_MOIRE_*）。
#   ④ ★ 主人：「数字的框不用黑色，可以是暖灰、灰蓝……总之颜色从主体里找，整体协调就行」
#      ⇒ 描边取**主体实测色**（从主人截图里采样，面积最大的深色）：
#         面罩深藏青 `#2D3589` = (45, 53, 137)｜亮边 `#E4F5FF` = (228, 245, 255)
#   ⑤ 深边宽度 4.7 → **3.4 px**（`dark_k=0.13`）—— 比旧版收窄、比深色版加厚，取中间；
#      深边不透明度 200 → **230**（让描边更「实」，边缘不被条色/横纹混入 ⇒ 更清晰）。
#   ⑥ ★★ 机制备忘：亮边**后画**，会盖掉内侧那段深边 ⇒ 可见结构永远是
#      「亮边 0..light_k、深边 light_k..dark_k」，所以 dark_k 只需比 light_k 略大。
FACE_TOP = (255, 255, 255)          # ★ 字面渐变：回滚旧版（白 → 浅蓝）
FACE_BOT = (150, 205, 255)
#   ★ 描边色 = **主体色压暗**，不是黑。三档实测（`_work/step36_stroke_color.py`）：
#     面罩本色 #2D3589 (45,53,137) ⇒ 描边渲成 (49,62,142)、与字身差 **170.6** ⇒ 轮廓偏弱（蓝边与蓝条融）
#     **藏青压暗 #222A68 (34,42,104) ⇒ (38,50,111)、差 188.7** ⇒ ★ 定档（旧版近黑是 205.4）
#     灰蓝 #485678 (72,86,124) ⇒ (73,90,129)、差 157.4 ⇒ 更弱、字发灰
#   宽度同理：旧版 dark_k=0.18（26px 字号 ⇒ 4.7 px）主人嫌厚；太薄（0.13⇒3.4 px）又压不住。
#   ⇒ 取 **0.16（4.2 px）**，配合主体色把「协调」和「清晰」都拿到。
DARK_COL = (34, 42, 104)            # ★ 主体藏青压暗 #222A68（不是黑）★ 主人要的「从主体里找色」
EDGE_COL = (228, 245, 255)          # ★ 主体亮边 #E4F5FF
DARK_ALPHA = 235                    # 深边不透明度（旧 200；抬高让描边更实）
#   ★★ 描边宽度用**绝对像素**（`dark_px` / `light_px`），**不用比例** ——
#      主人 2026-09-22 明确要求：「数字放大到 1.1 挡位，**记得描边不要变大**」。
#      比例式（dark_k=0.16）的后果：字号 26→28 px 时描边 4.16→4.48 px，字大了、边也粗了，观感变重。
#      ⇒ `number_rgba` 每次按当前字号换算：`dark_k = dark_px / px`，字号怎么变、边都是 4.16 px。
#      （值 = 26px 档时的实测宽度：26 × 0.16 = 4.16 px、26 × 0.075 = 1.95 px）
DARK_PX = 4.16                      # 深边绝对宽度（px）
LIGHT_PX = 1.95                     # 亮边绝对宽度（px）
TEXT_STYLE = {
    "num": dict(glow_r=0.20, glow_a=0.45, dark_px=DARK_PX, light_px=LIGHT_PX,
                face_top=FACE_TOP, face_bot=FACE_BOT,
                dark_col=DARK_COL, edge_col=EDGE_COL, dark_a=DARK_ALPHA),
    "cap": dict(glow_r=0.14, glow_a=0.40, dark_px=DARK_PX, light_px=LIGHT_PX,
                face_top=FACE_TOP, face_bot=FACE_BOT,
                dark_col=DARK_COL, edge_col=EDGE_COL, dark_a=DARK_ALPHA),
}


def _stroke_k(style, px):
    """把**绝对像素宽**的描边换算成 `styled_text` 要的比例（字号变、描边不变）。"""
    if "dark_px" in style:
        p = max(8.0, float(px))
        return style["dark_px"] / p, style["light_px"] / p
    return style["dark_k"], style["light_k"]          # 向后兼容（旧式比例档）


def number_rgba(pct_int, px):
    """中央百分比数字（复用 `step17_hdd_progress.styled_text`：粗斜体 + 描边 + 渐变 + 发光）。

    ★ 单次生成约 **3 ms**（有高斯发光），所以必须缓存；并把 0/5/…/100 预热掉。
    """
    key = (int(pct_int), int(px))
    hit = _NUM.get(key)
    if hit is not None:
        return hit
    import step17_hdd_progress as S17
    s = TEXT_STYLE["num"]
    _dk, _lk = _stroke_k(s, key[1])          # ★ 绝对宽 ⇒ 比例（不随字号变粗）
    t = S17.styled_text("%d%%" % key[0], key[1], track=0.10,
                        glow_r=s["glow_r"], glow_a=s["glow_a"],
                        dark_k=_dk, light_k=_lk,
                        face_top=s["face_top"], face_bot=s["face_bot"],
                        dark_col=s["dark_col"], edge_col=s["edge_col"],
                        dark_a=s["dark_a"])
    if len(_NUM) >= _NUM_CAP:
        _NUM.pop(next(iter(_NUM)))       # 只淘汰最老的一条，不再整表清空
    _NUM[key] = t
    return t


class BarView:
    """带缓存的进度条。预烘：色块(101 档) + 横纹(90 相位) + 数字(按需缓存)。

    每帧成本（260px 档实测）：
      · `bake()` 把 条+横纹+数字 合到一个小 RGBA 缓冲 —— **约 0.02 ms**
      · `composite()` 在 BGRA/alpha 上做**局部** source-over —— **约 0.1 ms**
    ⇒ 合计 **< 0.2 ms**。★ 千万不要改成"塞进整幅 overlay 走 `to_bgra(ov=)`"：
      那条路要全幅合成，实测 **+4.4 ms/帧**。
    """

    def __init__(self, S, ppu):
        self.S, self.ppu = int(S), float(ppu)
        self.w, self.h, self.x, _ = geom(S)          # 先按「无副标题」取条尺寸
        # ★ 字号只挂**基准高**（不含 BAR_H_GAIN）⇒ 条加高不会连带放大字号
        self.h_base = self.w / ASPECT
        self.num_px = max(7, int(round(self.h_base * NUM_F * NUM_GAIN)))
        self.cap_px = max(7, int(round(self.h_base * CAP_GAIN)))
        self.cap = caption_rgba(self.cap_px) if CAPTION_ENABLE else None
        self.cap_gap = max(2, int(round(self.h * CAP_GAP_F))) if self.cap is not None else 0
        self.w, self.h, self.x, self.y = geom(S, self.cap.height if self.cap is not None else 0)
        self.cap_x = int((self.S - self.cap.width) / 2.0) if self.cap is not None else 0
        self.cap_y = self.y + self.h + self.cap_gap
        self.per_px = BAR_MOIRE_PER_U * self.ppu
        self._solid, self._moire = {}, {}
        self._box = None
        self._x0 = self._y0 = 0

    # ---------- 内部 ----------
    def _s(self, p):
        im = self._solid.get(p)
        if im is None:
            im, self._box = solid(self.w, self.h, p / 100.0)
            self._solid[p] = im
        return im

    def _m(self, ph):
        im = self._moire.get(ph)
        if im is None:
            b = self._box or solid(self.w, self.h, 0.0)[1]
            self._box = b
            im = moire_rgba(b[2], b[3], ph / float(MOIRE_PHASES), self.per_px)
            self._moire[ph] = im
        return im

    # ---------- 对外 ----------
    def card_bottom(self):
        """通知卡的底边该落在哪（有进度条时往上让位）。"""
        return self.y - int(round(CARD_GAP_F * self.S))

    def bake(self, pct, t_ms, k=1.0, rev=None):
        """→ (x0, y0, RGBA 缓冲) 或 None。把 条 + 横纹 + 数字 合到**一小块**缓冲里。

        区域 = 条矩形 ∪ 数字矩形 ∪ 副标题矩形（出现动画时再向下扩出数字云的落点）。
        `rev` = 出现动画进度 0..1；**None = 已经出现完了**（走稳态路径，与旧版完全一致）。
        """
        if k <= 0.02:
            return None
        # ★★ 2026-09-23 主人：「向上的递进规则用 roundup」。
        #   原来 `int(round(...))` 是**银行家舍入** ⇒ `int(round(22.5))` = **22**
        #   （该显示 23）、`int(round(2.5))` = 2 ⇒ 数字"该动没动"。
        #   ⇒ 一律向上。**不引 math**：本模块零依赖，用整数写法即可。
        _v = max(0.0, min(100.0, float(pct)))
        p = int(_v) if _v == int(_v) else int(_v) + 1
        ph = int(((t_ms / M.GLOW_CYCLE_MS) % 1.0) * MOIRE_PHASES) % MOIRE_PHASES
        b = self._box or solid(self.w, self.h, 0.0)[1]
        self._box = b
        n = number_rgba(p, self.num_px)          # ★ 挂基准高，见 __init__ 的说明
        nx = int((self.S - n.width) / 2.0)
        ny = int(self.y + self.h / 2.0 - n.height / 2.0)
        # ★★ 出现动画 = **PPT 式淡入**（2026-09-23 00:0x 主人改口径，见文件头的说明）：
        #   整块（条 ＋ 数字 ＋ 英文）一起淡入 + 轻微上浮若干像素 ⇒ 落位后完全静止。
        anim = rev is not None and float(rev) < 1.0
        dy, kk = 0, k
        if anim:
            _e = _ramp(rev, 0.0, REVEAL_FADE_W)
            dy = int(round(REVEAL_FLOAT_PX * (1.0 - _e)))
            kk = k * _e
        x0 = max(0, min(self.x, nx))
        y0 = max(0, min(self.y, ny))
        x1 = min(self.S, max(self.x + self.w, nx + n.width))
        y1 = min(self.S, max(self.y + self.h, ny + n.height) + dy)
        if self.cap is not None:                     # ★ 副标题也要进"一小块"缓冲
            x0 = max(0, min(x0, self.cap_x))
            y0 = max(0, min(y0, self.cap_y))
            x1 = min(self.S, max(x1, self.cap_x + self.cap.width))
            y1 = min(self.S, max(y1, self.cap_y + self.cap.height + dy))
        rw, rh = x1 - x0, y1 - y0
        if rw <= 0 or rh <= 0:
            return None
        self._x0, self._y0 = x0, y0
        buf = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
        buf.alpha_composite(self._s(p), (self.x - x0, self.y + dy - y0))
        buf.alpha_composite(self._m(ph), (self.x + b[0] - x0, self.y + b[1] + dy - y0))
        buf.alpha_composite(n, (nx - x0, ny + dy - y0))
        if self.cap is not None:
            buf.alpha_composite(self.cap, (self.cap_x - x0, self.cap_y + dy - y0))
        if kk < 0.999:
            buf.putalpha(buf.getchannel("A").point(lambda v: int(v * kk)))
        return x0, y0, buf

    def bar_only(self, pct, t_ms, k=1.0):
        u"""**只含进度条本体**的图层（不含百分比数字、不含副标题）→ `(x0, y0, RGBA)`。

        ★★ 2026-09-23 主人：「百分比数字应该是需要跟进度条**分开两个图层**的」。
          先答现状：**本来就是分开的** —— `_s()` 空槽 / `_m()` 填充 / `number_rgba()` 数字 /
          `self.cap` 副标题，四张独立 RGBA，最后在 `bake()` 里 `alpha_composite` 合成到
          **一小块缓冲**。所以"数字盖住条"是**合成之后**才发生的事，不是图层没分。
          我的"纠结"也不在渲染上 —— 在**量测**上：拿合成后的像素反推"填充到百分之几"时，
          数字（亮度 170~240）把真实填充（只比空槽亮 +5~+11）整个盖住，害我连栽三次
          （REFERENCE **T.9c**）。
          ⇒ 量测/自检一律用**这个纯条图层**：没有数字、没有副标题、没有文字干扰，
            一个简单的列扫描就能读准。**渲染路径仍走 `bake()`，外观一字不变。**
        """
        _v = max(0.0, min(100.0, float(pct)))
        pp = int(_v) if _v == int(_v) else int(_v) + 1
        ph = int(((t_ms / M.GLOW_CYCLE_MS) % 1.0) * MOIRE_PHASES) % MOIRE_PHASES
        b = self._box or solid(self.w, self.h, 0.0)[1]
        self._box = b
        buf = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        buf.alpha_composite(self._s(pp), (0, 0))              # 空槽（含已填充部分）
        buf.alpha_composite(self._m(ph), (b[0], b[1]))        # 横向摩尔纹
        if k < 0.999:
            buf.putalpha(buf.getchannel("A").point(lambda v: int(v * k)))
        return self.x, self.y, buf

    def draw_on(self, cv, pct, t_ms, k=1.0, rev=None):
        """把进度条画到**整幅画布 RGBA** 上（设计稿 / 预览脚本用）。"""
        r = self.bake(pct, t_ms, k, rev)
        if r:
            cv.alpha_composite(r[2], (r[0], r[1]))

    def composite(self, bgra, alpha, pct, t_ms, k=1.0, rev=None):
        """★ 主程序走这条：把进度条**局部**合成到 `to_bgra()` 的结果上。

        公式与 `fairy_layers.to_bgra(ov=...)` 完全一致（source-over，直色 × alpha），
        但只算**那一小块**（338×~45），所以几乎免费。

        ★★ **通道序**：本模块的图是 **RGB** 序（设计稿里直接给人看的），
           而 `bgra` / `alpha` 所在的画布是 **BGR** 序 ⇒ 这里**必须翻一次**
           （`o[..., 2::-1]`）。不翻的后果：蓝色进度条被渲成**橙褐色**
           —— 我在预览图上亲眼看到才发现（代码不报任何错）。

        `rev`：出现动画进度 0..1（None = 已经出现完）。
        """
        r = self.bake(pct, t_ms, k, rev)
        if r is None:
            return
        x0, y0, buf = r
        o = np.asarray(buf, dtype=np.uint8)
        a = o[..., 3].astype(np.uint16)
        inv = 255 - a
        sb = bgra[y0:y0 + o.shape[0], x0:x0 + o.shape[1], 0:3]      # BGR 色
        # ★★ 必须同时写 **两处 alpha**（2026-09-22 主人截图抓到的真 bug）：
        #   ① `alpha`（独立 2D 数组）—— 给 WM_NCHITTEST / 离屏自检用；
        #   ② `bgra[..., 3]`（4 通道缓冲的第 4 通道）—— **`_blit` 上传的就是它**
        #      （`memmove(self._bits, bgra.tobytes(), ...)`，走的是 4 通道整体）。
        #   只写 ① 的后果：DIB 里 alpha = 上屏前的旧值（辉光区本来就 ≈0）⇒ DWM 按
        #      `预乘色 + (1−alpha)×桌面` 合成 ⇒
        #        · 深色桌面：(75,110,150)+(20,20,20) → **看得见**（所以我一直以为没事）；
        #        · 浅色桌面：(75,110,150)+(255,255,255) → 削顶成 **纯白** ⇒ **整条消失**，
        #          而且白矩形会把工作态的辉光横切一刀（主人："边缘跟白底融合度差"）。
        #   ★ 教训与 §draw 里那次同源：**"内部有快照语义"的缓冲，必须让所有消费者同步**
        #     —— 离屏自检比的是 `alpha` 数组，正好漏掉 `bgra[...,3]`，所以它一路全绿。
        sb3 = bgra[y0:y0 + o.shape[0], x0:x0 + o.shape[1], 3]
        sa = alpha[y0:y0 + o.shape[0], x0:x0 + o.shape[1]]
        col = o[..., 2::-1].astype(np.uint16)                       # ★ RGB → BGR
        rgb = (sb.astype(np.uint16) * inv[..., None] + col * a[..., None]) // 255
        na = (a + (sa.astype(np.uint16) * inv) // 255).astype(np.uint8)
        sb[:] = rgb.astype(np.uint8)          # ★ 先算完再写，否则读到写过的值
        sa[:] = na
        sb3[:] = na                           # ★★ 上屏真正读的那份，漏了=白底消失

    def warm(self):
        """预热：色块第 0 档 + 横纹相位 0 + 数字 0/5/…/100（避免运行中偶发 3 ms 卡顿）。"""
        self._s(0)
        self._m(0)
        for p in range(0, 101, 5):
            number_rgba(p, self.num_px)
