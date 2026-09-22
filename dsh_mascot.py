# -*- coding: utf-8 -*-
"""
fairy v2 · 按 Chengzhibense/Fairy-DSH 官方 SVG 几何数值重建 mascot（含动效）

来源（逐字抄自仓库，勿凭记忆改）：
  mascot-geometry.js : outerDisc 68 / outerStroke 1.2 / outerHalo 79 / sclera 48
                       scleraContact .8 / scleraHalo 56 / pupil 16
                       highlightCenter (98,100.5) / highlight 11 / highlightHalo 18
  mascot-eye-svg.js  : 颜色、图层顺序、clipPath 路径（thinking / comforting）
  mascot-style.js    : --dsh-fairy-outer-halo-color: #c9f8ff（dark 主题）
                       .dsh-fairy-corners { animation: dsh-fairy-lashes calc(15s/rate) linear infinite }
                       .dsh-fairy-sclera { pulse-outer .72s cubic-bezier(.72,0,.28,1) 0s infinite alternate }
                       .dsh-fairy-layer-three { -.045s }  layer-two { -.09s }  layer-one { -.18s }
                       [data-state="thinking"] .dsh-fairy-eye { clip-path: url(#dsh-fairy-thinking-eye-clip) }
                       .dsh-fairy-thinking-clip-shape { transform-origin: 80px 60px }
  mascot-runtime.js  : renderMotionFrame() 各层 scale 区间 + thinking 的 translateY/scaleY 公式
"""
import os
import numpy as np
import fairy_digits as FD      # 工作态「0/1 数字流」（自带闭合/同速约束）

# ---------- 官方常量（mascot-geometry.js）----------
OUTER_DISC = 68.0
OUTER_STROKE = 1.2
OUTER_HALO = 79.0
SCLERA = 48.0
SCLERA_CONTACT = 0.8
SCLERA_HALO = 56.0
PUPIL = 16.0
HL_C = (98.0, 100.5)
HL_R = 11.0
HL_HALO = 18.0
OUTER_VISIBLE_EDGE = OUTER_DISC + OUTER_STROKE / 2      # 68.6
SCLERA_VISIBLE_EDGE = SCLERA + SCLERA_CONTACT / 2       # 48.4
CORNER_CIRCLE = 51.75
CORNER_SQUARE_HALF = 43.0
CORNER_SQUARE_R = 2.0
CORNER_BASE_ROT = 3.0

# ---------- 颜色（mascot-eye-svg.js / mascot-style.js）----------
OUTER_HALO_COLOR = (0xC9, 0xF8, 0xFF)          # dark 主题
DISC_GRAD = [("#4053f0", 0.0), ("#3045dc", 0.54), ("#3d50c8", 1.0)]
DARK_CORNERS = "#2b3388"
SCLERA_COL = "#eef0f5"
LAYER3 = "#9daee0"
IRIS = "#317bcf"
PUPIL_COL = "#3b3d8a"
HILIGHT = "#f5f8fd"
STROKE_RIM = "#f2fbff"
SCAN_COL = "#c9f8ff"
L1_R = 16.6
L2_W = 24.15
L2_I = 23.5
L3_R = 33.0

# ---------- ★ 外发光（"最外圈特效"）：按视频实拍反解，不用官方那套偏紧的 ----------
# 反解方法：从 mp4 实拍帧量"盘缘外"的径向亮度，`a = (L - L_bg)/(L_白 - L_bg)`。
# 实测（r 以盘半径 outerDisc=68 为单位）：
#   1.00 → a=0.218 ；1.05 → 0.127 ；1.10 → 0.081 ；1.15 → 0.056
#   1.20 → 0.030 ；1.25 → 0.014 ；1.30 → 0.000
# 官方那套只到 1.16R 且尾部衰减过快 ⇒ 观感"最外圈没有效果"。
R_OUT = OUTER_DISC
HALO_COLOR = (0xC9, 0xF8, 0xFF)
HALO_STOPS = [(0.929 * R_OUT, 0.000),
              (1.007 * R_OUT, 0.260),
              (1.050 * R_OUT, 0.130),
              (1.100 * R_OUT, 0.081),
              (1.150 * R_OUT, 0.056),
              (1.200 * R_OUT, 0.030),
              (1.250 * R_OUT, 0.014),
              (1.300 * R_OUT, 0.000)]

# ---------- ★ 实测辉光贴图：外圈"不规则向外发散 + 自然消失" ----------
# 来源：GIF 原素材逐像素量测（`_work/measure_glow_shape.py` → `glow_polar.npz`）
# 实测结论（不要再凭印象改）：
#   · 黑电平 = **0.00%** ⇒ 辉光真实延伸到 **rho ≈ 2.4**（R_disc 的 2.4 倍），不是 1.3 倍；
#   · 紧贴盘缘处 alpha 高达 **72%**（峰值在 rho≈1.10），随后缓慢衰减：
#     1.30→50%  1.50→26%  1.75→7.3%  1.90→4%  2.10→1.5%  2.40→0.3%
#   · **角度上不规则**：k=3/5/6/7 谐波幅度 8~20%（k=6 六瓣最明显），
#     外缘半径随角度的**峰峰起伏 15.6%**。
# 所以不用"径向停靠点 + 圆形"去建模，直接把 (rho, theta) 实测贴图采进来 ——
# 形状、颜色、衰减全部来自原素材。
GLOW_R_DISC = 68.6          # rho = 1 对应官方外盘可见外缘（outerDisc 68 + 描边一半 0.6）
GLOW_RHO_END = 2.40         # 贴图外端（此处置 alpha ≈ 0.27%）
GLOW_RHO_IDENT = 1.75       # （旧名，仅注释参考）

# ============ ★★ 2026-09-21 晚：辉光整形 + 动态（主人四条反馈）============
# ① "内边缘与主体之间有缝隙"：白描边(rho=1.00, alpha 233)之外**直接掉到 128**
#    （RGB 26,70,128 的中深藏蓝），再往外才亮回去(1.07→189) ⇒ 一圈宽约 5.7px 的暗带。
#    实测素材本身在 1.04→126 / 1.06→172 也有这个跳变（我原先照抄了它），但那是 GIF 的
#    bloom 产物；260px 下肉眼就是"缝"。⇒ 主动偏离素材：rho ≤ GLOW_FILL_TO 直接取峰值颜色。
# ② "发散区有点大"：alpha≥2 一直拖到 rho=1.99（135 unit / 220px），占满整块画布，
#    连四角都被染色（alpha 8~11）⇒ 暗色桌面上就是"一个方框"。⇒ 拖尾压缩 GLOW_TAIL_K 倍，
#    可见半径 1.90 → 1.33（约 −30%），并在 GLOW_END 前严格归零。
# ★★ 关键决定（2026-09-21 晚二，主人第二轮反馈）：
#   素材那条径向剖面**本身带 bloom 亮环**（1.04→126 跳 1.06→172、峰值在 1.07~1.10）。
#   照抄它，无论怎么调都会出现"一圈比较实的光晕"—— 主人两次都指到这个。
#   所以：**径向剖面不再照抄素材**，改成合成的**单调衰减**（像烟从主体往外散，越远越淡）；
#   素材只保留两样：① 角向不规则（六瓣等）② 配色。
GLOW_ANG_R = 1.07           # 从这个半径取素材的「角向图案」（含实测的六瓣不规则）
GLOW_ANG_GAIN = 1.60        # 角向不规则放大倍数（主人："白底上不规则感不够强"）

# ★★ 2026-09-21 晚三（主人："辉光边界太均匀，应该有的区域厚有的区域薄"）：
#   根因 —— GLOW_PROF 只跟 rho 有关 ⇒ 每个角度的衰减曲线一模一样 ⇒ **外缘是个正圆**。
#   角向图案只改了亮度，没改"到哪儿消失"。实测素材的外缘半径是**起伏的**（阈值 8/255）：
#     均值 1.913、标准差 0.095、峰峰 **20.3%**；主谐波 k=2（上下薄）4.8% + k=4 3.7%
#     ⇒ 正下方(90°)最薄、右下(165°)最厚。
#   做法：把实测的外缘半径做成角向的**厚度因子**，直接缩放径向采样半径 ⇒ 边界变成波浪形。
GLOW_THICK_AMP = 0.11       # 厚薄幅度。★ 定这个值要按"表的极值差"算，不能只看系数：
                            #   峰峰 = AMP × (tz.max − tz.min)。谐波全保留后极值差 ≈ 1.699
                            #   ⇒ 0.11 × 1.699 ≈ 18.7%，实测均值 20.3%（与素材一致）。
                            #   上限受画布约束：厚处归零半径 = (1 + 0.11×0.70) × 1.52R = 1.637R
                            #   = 112.3 unit < 画布半宽 114.0 —— 已核算，不会出直边。
GLOW_THICK_TONE = 0.35      # 厚的方向同时更亮（±35%，随后整体归一化回 GLOW_PEAK）⇒ 厚薄更分明
# ★★ 2026-09-21 晚四（主人："厚薄变动速度跟主体不匹配 —— 应该让辉光看起来在**绕圈**，
#    速度不快，但跟瞳孔的变化、尖角绕圈的速度是匹配的"）：
#    原来是 `sin` 往复（1.5 s 一摇），而睫毛是**匀速转** —— 性质不同，所以看着不搭。
#    改成**匀速绕圈**，角速度与睫毛完全一致。
GLOW_CYCLE_MS = 7500.0        # 辉光相位表的周期 = 5 个呼吸周期（也是循环片的长度）
GLOW_ROT_PERIOD_MS = 15000.0  # 与睫毛同一角速度：15 s 一整圈 ⇒ 7.5 s 转 180°
                              #   180° 对 2 重对称的形状等同 0° ⇒ 循环无缝
GLOW_BREATH_PER_CYCLE = 5.0   # = GLOW_CYCLE_MS / PULSE_CYCLE_LOOP_MS：一个辉光周期含 5 次呼吸
GLOW_THICK_BREATHE = 0.30     # 厚薄**幅度**本身的起伏（±30%）⇒ 有时更不规则、有时接近圆
GLOW_THICK_IN0 = 1.02       # 厚薄重映射的生效起点（这个半径以内不缩放）
GLOW_THICK_IN1 = 1.16       # 到这儿完全生效
                            #   ★ 为什么要渐变：厚的地方会把剖面端点拖到盘缘附近，
                            #     于是盘缘外出现一段 prof=1.0 的"平板"——既更容易削顶，
                            #     又可能在薄的方向贴着白描边造出一道新的暗带（"缝隙"复发）。
                            #     渐变之后：盘缘附近保持不变，厚薄只作用在"往外散"的那一段。
GLOW_PEAK = 180.0           # ★ 角向图案归一化后的峰值。必须**留余量**：
                            #   否则烟团一叠加就削顶到 255 ⇒ 一片饱和 = 又是"一圈实心光晕"
                            #   （实测过：不归一化时辉光区 25% 的像素饱和）
GLOW_MULT_MAX = 1.30        # 动态乘数上限（超过就平顶 = 又变实心）
# ★ 演示专用：把「动态偏离」整体放大（1.0 = 正常）。**只在做"增强版演示片"时临时调大**，
#   目的是让主人看清运动的性质；生产时恒为 1.0。
#   实现上走 `if` 分支 ⇒ 取 1.0 时**不做任何算术** ⇒ BASE 逐位不变（浮点结合律不可靠，不能靠乘 1.0）。
SMOKE_DYN_GAIN = 1.0
                            # ★ 不饱和约束：GLOW_PEAK x 1.0 x GLOW_MULT_MAX = 180x1.30 = 234 < 250
                            #   （曾经 185x1.38 = 255.3 正好顶到 ⇒ 实测 28% 的像素变死白）
GLOW_PROF = [(1.00, 1.00), (1.04, 0.93), (1.10, 0.80), (1.18, 0.58),
             (1.26, 0.36), (1.34, 0.17), (1.42, 0.06), (1.52, 0.00)]
#   ↑ 单调衰减：可见半径(≈alpha 8/255) ≈ 1.44R = 158px@260px，与"收回 30%"一致；
#     末点严格为 0（104 unit < 画布半宽 110 unit）⇒ 边界永远干净，不会出方框。
CANVAS_MARGIN_U = 34.0      # 画布每边多留 34 unit ⇒ 半宽约 114.3 unit
                            # （30 -> 34：厚处的辉光要多占一点空间，见 GLOW_THICK_AMP）

# ---------- ③ 动态（三样叠加，纯乘法 ⇒ 不偏色）----------
RIPPLE_CYCLE_MS = 1500.0    # 与呼吸周期一致（循环片 7.5s = 15 个周期）
RIPPLE_AMP = 0.00           # ★ 关掉径向亮环。主人第五轮："一圈圈向外扩散的同心圆波纹
                            #   像水波/重力波，要更'自由'" —— 亮环就是这个波纹的直白来源。
                            #   动态全部交给"自由游荡的烟团"。（想回退：改回 0.16）
RIPPLE_W = 0.075            # 亮环径向宽度（rho）
RIPPLE_R0, RIPPLE_R1 = 1.02, 1.46   # 亮环从盘缘出发、扩散到这儿消失
RIPPLE_SECOND = 0.55        # 第二道环的强度系数
RIPPLE_PHASE_OFF = 0.5      # 相位错开半个周期
RIPPLE_FADE_IN = 0.15       # 起步 15% 行程内淡入（否则在盘缘"啪"地亮一下）
GLOW_BREATHE_AMP = 0.10     # 整体亮度随呼吸的 ±幅度（双向；有削顶检查兜着）

# ---------- ③b 烟团湍流：照"烟雾"那种感觉 —— 成团、不规则、向外飘散 ----------
# ★★ 主人第五轮反馈："辉光向外扩散的动态像**水波/重力波**（一圈圈同心圆波纹），
#    应该是更'自由'的运动（类似布朗运动那种无规律感，但不是真无规律）。"
#    根因：上一版每个团都沿径向**从 1.03R 单向漂到 1.52R**，外加两道径向亮环 ——
#    叠起来就是"一圈圈往外推"。新版把"定向漂移"换成"原地游荡"：
#      · 径向：**零均值来回**（有的向内、有的向外、有的几乎不动）
#      · 角向：绕自己的基准位置摆动，幅度/频率各团不同
#      · 强度：各有**生灭**（包络起伏，不再是"整圈一起明暗"）
#      · 亮环关掉（RIPPLE_AMP = 0）—— 那是最直白的"同心圆波纹"
#    所有运动都是**整数频率的正弦和** ⇒ 一个周期内逐点闭合（准周期，不是真随机）。
SMOKE_N = 14                # 团数（12 偏少；14 刚好"团"看得出来、又不糊成一片）
SMOKE_AMP = 0.42            # ★ 定稿：相对幅度（双向偏离，见 glow_layer）
SMOKE_WTH = 0.40            # 团的角度半宽（弧度）
SMOKE_WR = 0.095            # 团的径向半宽（rho）
SMOKE_R_IN, SMOKE_R_OUT = 1.03, 1.52   # 团的活动半径范围
SMOKE_WOB_TH = (0.22, 0.55) # 角向游荡幅度范围（弧度，各团随机取）
SMOKE_WOB_R = (0.04, 0.15)  # 径向游荡幅度范围（零均值 ⇒ 不向外漂）
SMOKE_ENV_MIN = 0.24        # ★ 定稿：生灭包络下限
SMOKE_SEED = 20260921       # 固定种子 ⇒ 每次烘焙同一形态（可复现）

# ---------- ③d ★ 2026-09-21 晚四：给烟团加两个新自由度（三种变体对比用）----------
#   · 差速剪切 SMOKE_SHEAR：角位移 ∝ (半径 − 中位半径) ⇒ 内侧团与外侧团**相互错动**
#     （不是刚体旋转，而是"剪切流"），视觉上像涡旋把团块卷开。
#     用 gphase（辉光周期 7.5 s）的 **1 整圈正弦** ⇒ u=1 时精确归零 ⇒ 闭合不受影响。
#   · 拉丝 SMOKE_ELONG：把每个团做成"两瓣"（角向分列 + 径向微错），
#     两瓣间距随 gphase 周期性开合 ⇒ 团被**拉长成涡丝**又收拢。
#     ★ BASE 时这两项乘数都是 0 ⇒ 加 0.0，逐位不变。
SMOKE_SHEAR = 2.60          # ★ 定稿（主人 2026-09-21 22:1x 选②）：角位移 per unit 半径
                            #   ±0.64 rad ≈ ±36°，内圈与外圈相互错动（剪切流）
SMOKE_SHEAR_F = 1           # 剪切的整数频率（在辉光周期内的圈数，必须整数才闭合）
SMOKE_SHEAR_R0 = 1.275      # 剪切的中性半径（= SMOKE_R_IN/OUT 的中点）
SMOKE_ELONG = 0.55          # ★ 定稿：两瓣最大角间距（弧度）。必须 **> 团宽 σ=0.40** 才看得出来
SMOKE_ELONG_R = 0.05        # 两瓣的径向错开（unit of rho）⇒ 涡丝是斜的
SMOKE_ELONG_F = 1           # 拉丝开合的整数频率
SMOKE_ELONG_S = (0.60, 1.35)  # 各团的拉丝强度随机范围（倍率）
#   · 拉丝的**关键**：静两只瓣的间距必须**大于**团本身的角向宽度才看得出来。
#     原先 sep 最大 0.30 rad 而团宽 σ=0.40 rad ⇒ 两瓣糊在一起 = 等于没拉丝（实测最大差仅 6/255）。
#     现在拉丝时把两瓣**收窄**（质量守恒），并把间距放大到 0.55 rad；两瓣权重也不对称
#     （0.62 : 0.38）⇒ 看起来是"一团带一条尾"的涡丝，而不是两个对称的球。
SMOKE_ELONG_NARROW = 0.35   # ★ 定稿：拉丝时把两瓣收窄（质量守恒思路）
SMOKE_ELONG_ASYM = 0.12     # 两瓣权重不对称（0.62:0.38）⇒ "一团带一条尾"
#   · 慢速漂移 SMOKE_WOB_SLOW_TH/R：游荡分量**挂在 7.5 s 慢相位**上（整数频率 1~2）。
#     为什么需要它：快的游荡（挂 1.5 s 呼吸相位，含 0.5/0.75/1.5 s 三个分量）幅度一放大
#     就变成"高频抖动"；叠加一条慢漂移才是"飘"。BASE 时为 0 ⇒ 逐位不变。
SMOKE_WOB_SLOW_TH = 0.0     # 慢漂移的角向幅度（弧度）
SMOKE_WOB_SLOW_R = 0.0      # 慢漂移的径向幅度

    # ---------- ③e 变体表（对比用；★ BASE 现在就是② 差速剪切+拉丝）----------
    #   "B" 项已并入 BASE 默认值，保留在此仅作历史记录。
SMOKE_VARIANTS = {
    "A": dict(
        SMOKE_WOB_R=(0.10, 0.32),      # 径向游荡幅度加大（原 0.04~0.15）
        SMOKE_WOB_TH=(0.50, 1.20),     # 角向游荡幅度加大（原 0.22~0.55）
        SMOKE_WOB_SLOW_TH=0.55,        # + 慢漂移（7.5 s 上的整数圈）⇒ 是"飘"不是"抖"
        SMOKE_WOB_SLOW_R=0.16,
        SMOKE_ENV_MIN=0.16,            # 生灭更明显（原 0.30）
        SMOKE_AMP=0.44,
    ),
    "B": dict(
        SMOKE_SHEAR=2.60,              # 内快外慢的剪切（±0.64 rad ≈ ±36°）
        SMOKE_ELONG=0.55,              # 两瓣拉开到 ±0.55 rad（> 团宽，才成涡丝）
        SMOKE_ELONG_NARROW=0.35,       # 拉丝时收窄（否则两瓣糊在一起 = 看不出来）
        SMOKE_ELONG_ASYM=0.12,         # 不对称 ⇒ "一团带一条尾"
        SMOKE_ELONG_R=0.05,            # 两瓣径向错开 ⇒ 丝是斜的
        SMOKE_ENV_MIN=0.24,
        SMOKE_AMP=0.42,
    ),
    "C": dict(
        SMOKE_WOB_R=(0.10, 0.32),
        SMOKE_WOB_TH=(0.50, 1.20),
        SMOKE_WOB_SLOW_TH=0.55,
        SMOKE_WOB_SLOW_R=0.16,
        SMOKE_ENV_MIN=0.16,
        SMOKE_AMP=0.46,
        SMOKE_SHEAR=2.60,
        SMOKE_ELONG=0.55,
        SMOKE_ELONG_NARROW=0.35,
        SMOKE_ELONG_ASYM=0.12,
        SMOKE_ELONG_R=0.05,
    ),
}

# ---------- ④ ★ 2026-09-21 晚：工作态 = "辉光卸下伪装，变成 01 二进制" ----------
# ★★ 主人的设计意图（原话 2026-09-21 22:4x）：
#   "思考辉光变成 01 状态的一致性，即主体因为进入工作状态而产生的变化，
#    卸下了伪装，辉光变成了 01 的二进制符号。"
#   ⇒ 工作态**不是**"把辉光换成另一种特效"，而是**同一束光换了形态**。所以：
#     · **颜色** = 辉光的实测色（不再用 #c9f8ff 那种青白 —— 主人："颜色跟辉光不一样"）；
#     · **亮度包络** = 辉光的径向剖面 × 角向厚薄（数字在"厚"的方向更亮更远）；
#     · **底光 / 淡雾 / 摩尔纹**三层都从辉光派生（同源 ⇒ 过渡时观感连续、不断层）。
#
#   自底向上的四层（实时渲染见 fairy_layers.draw，参考渲染见 DSHSvg.render）：
#     L0 底光 undershine —— "数字背后的光源"：平滑、低频、非正圆（静态，光源恒定）
#     L1 淡雾 mist        —— 辉光的淡残影（**复用辉光相位表** × WORK_MIST_K ⇒ 自带动态）
#     L2 摩尔纹 moire     —— 角向细纹 × 水平细纹的干涉（缓慢流动，整数圈 ⇒ 闭合）
#     L3 数字 digits      —— 540 个 0/1（密度已按主人要求 −40%）
WORK_BG_GAIN = 0.42         # 底光峰值 = GLOW_PEAK × 这个（贴近盘缘最亮 ⇒ 有"光源"感）
                            #   ★ 0.24 时实测"看不见"：底光的预乘色只有 (12,31,48)，
                            #     在暗色桌面上就是一片近黑 —— 主人要的"数字背后的光源"必须**看得见**。
WORK_BG_PROF = [(1.00, 1.00), (1.08, 0.90), (1.16, 0.74), (1.26, 0.55),
                (1.36, 0.34), (1.46, 0.15), (1.58, 0.00)]
                            # ↑ 比 GLOW_PROF 更平缓、延伸更远 ⇒ 像"一团柔光"而不是"一圈亮环"
                            #   ★ 末点 1.58 是**硬约束**：画布边中点的 rho = (80+34)/68.6 = 1.66，
                            #     厚薄缩放最坏 1.66/1.05 = 1.581 —— 必须 ≥ 末点，否则四边会带 3~4
                            #     的残余 alpha（= 暗色桌面上一个极淡的方框，主人对这个很敏感）。
WORK_BG_TONE = 0.18         # 底光的角向厚薄（静态、低频）⇒ 不是正圆
WORK_MIST_K = 0.30          # 淡雾 = 辉光 × 这个（主人："淡色的雾也补上"）
WORK_MOIRE_AMP = 0.48       # 摩尔纹幅度（× GLOW_PEAK ⇒ 峰值 ~65/255）
                            #   ★ 为什么不能更小：最终是 `lighter`（取 max）叠加 ⇒ 图案的**暗部无效**，
                            #     只有亮部算数 ⇒ 实际对比度只有幅度的一半；而且它必须比**底光**
                            #     （同半径处 30~50）更亮才浮得出来。0.10 时实测 1.0~1.5R 内均值仅
                            #     3.6/255（等于看不见）。
WORK_MOIRE_N = 84           # 角向细纹条数（1.2R 处 ≈ 6.2 px/条 @260px；越大越像"放射光芒"、
                            #   越小越像"条纹"—— 84 是"看得出是干涉网、又不至于像光栅"的档位）
WORK_MOIRE_PY = 4.10        # 水平细纹周期（unit ⇒ ≈ 6.7 px @260px）
WORK_MOIRE_ROT = 2          # 7.5 s 内角向纹走过的**细纹间距数**（整数 ⇒ 闭合）
WORK_MOIRE_DY = 2           # 7.5 s 内水平纹走过的**周期数**（整数 ⇒ 闭合）
                            #   ↑ 两组都慢（1.7 px/s 量级）⇒ 是"缓慢流动"不是"扫过去"
WORK_MOIRE_PH_A = 0.137     # 两组细纹的初始相位（任意值，只为不落在对称位置）
WORK_MOIRE_PH_B = 0.411
MOIRE_PHASES = 36           # 摩尔纹相位表帧数（7.5 s / 36 = 208 ms ⇒ 细纹近似连续）
WORK_COL_WHITEN = 0.10      # 数字配色向白靠的比例（越小越接近辉光的**纯蓝**；
                            #   0.22 时实测"字看起来是白的"—— 因为 B 通道是 255，R 又有 108，
                            #   肉眼就判成白。0.10 ⇒ (89,175,255)，才是辉光那个蓝）

# ---------- ③f 变体开关 + 缓存标签 ----------
GLOW_TAG = ""               # 空 = 生产默认版；变体会设成 "A"/"B"/"C"（拼进缓存文件名）


def _smoke_param_names():
    return ("SMOKE_N", "SMOKE_AMP", "SMOKE_WTH", "SMOKE_WR", "SMOKE_R_IN", "SMOKE_R_OUT",
            "SMOKE_WOB_TH", "SMOKE_WOB_R", "SMOKE_ENV_MIN", "SMOKE_SEED",
            "SMOKE_SHEAR", "SMOKE_SHEAR_F", "SMOKE_SHEAR_R0",
            "SMOKE_ELONG", "SMOKE_ELONG_R", "SMOKE_ELONG_F", "SMOKE_ELONG_S",
            "SMOKE_WOB_SLOW_TH", "SMOKE_WOB_SLOW_R",
            "SMOKE_ELONG_NARROW", "SMOKE_ELONG_ASYM")


_SMOKE_BASE_PARAMS = None


def use_smoke_variant(name="base"):
    """切换烟团变体：改模块常量 + 设 GLOW_TAG + 清派生缓存。

    ★ 每次调用都从「导入时的基准值」重新展开，所以来回切不会叠加污染。
    """
    global GLOW_TAG, _SMOKE_BASE_PARAMS
    g = globals()
    if _SMOKE_BASE_PARAMS is None:
        _SMOKE_BASE_PARAMS = {k: g[k] for k in _smoke_param_names()}
    prm = dict(_SMOKE_BASE_PARAMS)
    prm.update(SMOKE_VARIANTS.get(str(name), {}))
    for k, v in prm.items():
        g[k] = v
    GLOW_TAG = "" if str(name).lower() in ("base", "", "none") else str(name)
    _SMOKE_CACHE.clear()
    _THICK_PH.clear()
    return prm

SMOKE_IN0, SMOKE_IN1 = 1.02, 1.18   # 变化量的径向权重：内侧收敛(0.3)、外侧放开(1.0)
SMOKE_IN_W = 0.30           #   目的：内侧底盘亮，避免烟团把它顶到削顶(255)

# ---------- ③c 亮底"凝实"：alpha 按径向增益放大 ----------
# ★ 黑底观感 = 预乘色，与 alpha **无关** ⇒ 放大 alpha 完全不改变暗色桌面上的样子；
#   而亮底显示 = 预乘色 + (1-alpha)×背景，alpha 变大 ⇒ 白渗漏变少 ⇒ 辉光在浅色界面上更显形。
# ★ 但**不要**用"径向增益"：那会在 1.18 处造出一圈更高的 alpha（又是"实心圈"）。
#   改成按**亮度值**抬升：alpha → alpha + (255-alpha)·k·(alpha/255)^p
#   —— 单调、永不削顶、且把中间调的差异放大（亮底上的不规则感更强）。
GLOW_ALPHA_LIFT = 0.55      # k：往 255 压的强度（0 = 不动）
GLOW_ALPHA_POW = 0.80       # p：<1 = 中间调抬得更多

# ★★ 外扩量必须**取整到最终像素再乘回 ss**，否则宠物的中心会落在超采样网格的半个像素上，
#    所有"裁剪框"就会整体错 1 px（踩过：MAE 从 0.48 飙到 9.1，且误差 85% 集中在宠物内部）。
def canvas_margin_px(px_per_unit, margin_u=CANVAS_MARGIN_U):
    return max(0, int(round(margin_u * px_per_unit)))


GLOW_RHO_CANVAS = (80.0 + 49.6) / GLOW_R_DISC                # ≈ 1.888（按最坏情况取保守值）

_GLOW = None


def glow_table():
    global _GLOW
    if _GLOW is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glow_polar.npz")
        d = np.load(p)
        _GLOW = (d["rho"].astype(np.float64), d["theta"].astype(np.float64),
                 d["rgb"].astype(np.float64))
    return _GLOW


def smoothstep(u):
    """0..1 平滑阶跃（u 自动截断）。★ 上一版整体替换时被误删过 —— 它必须留着。"""
    u = np.clip(np.asarray(u, dtype=np.float64), 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def alpha_lut():
    """亮底抬升用的 256 项查表（单调、不削顶）。黑底观感不受影响（黑底显示的是预乘色）。"""
    v = np.arange(256, dtype=np.float64)
    out = v + (255.0 - v) * GLOW_ALPHA_LIFT * (v / 255.0) ** GLOW_ALPHA_POW
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


_THICK_CACHE = {}
_THICK_PH = {}

_THICK_TURNS = None


def thick_rot_turns():
    """每个角向谐波在一个辉光周期（7.5 s）内转的**圈数**：n_k = round(k/2)。

    · 偶数谐波：n_k/k = 1/2 ⇒ 视觉角速度全部 = (360°/7.5 s)/2 = 24°/s（与睫毛同速）
    · 奇数谐波：n_k/k ≈ 0.57~1.0 ⇒ 略快 ⇒ **形状持续变形**，不保持镜像对称
    · 每个谐波都转整数圈 ⇒ 7.5 s 后逐点吻合（首尾无缝）
    """
    global _THICK_TURNS
    if _THICK_TURNS is None:
        t = np.zeros(129, dtype=np.float64)
        for k in range(1, 9):
            t[k] = int(k / 2 + 0.5)
        _THICK_TURNS = t
    return _THICK_TURNS


def glow_thick_coefs():
    """基准形状的 FFT 系数（保留 k=1..8 全部谐波 —— 不删！）。

    为什么不能删奇数谐波：删了就等于把形状做成了 2 重对称，
    厚和薄会呈**镜像关系**（主人一眼看出来"太对称"）。
    """
    hit = _THICK_CACHE.get("F")
    if hit is not None:
        return hit
    rho_t, th_t, rgb_t = glow_table()
    al = rgb_t.max(axis=2)                    # (NR,NT)：黑底观感的 max 通道即 alpha×255
    NR, NT = al.shape
    thr = 8.0
    edge = np.full(NT, np.nan)
    for j in range(NT):
        col = al[:, j]
        idx = np.nonzero(col < thr)[0]
        idx = idx[idx > 0]
        if idx.size == 0:
            edge[j] = rho_t[-1]
            continue
        i = int(idx[0])
        a0, a1 = float(col[i - 1]), float(col[i])
        w = 0.0 if a0 == a1 else (a0 - thr) / (a0 - a1)
        edge[j] = rho_t[i - 1] + w * (rho_t[i] - rho_t[i - 1])
    edge = np.where(np.isfinite(edge), edge, float(np.nanmean(edge)))
    sh = edge / edge.mean() - 1.0
    F = np.fft.rfft(sh)
    keep = np.zeros_like(F)
    keep[1:9] = F[1:9]                        # ★ 全保留（含奇数谐波）
    base = np.fft.irfft(keep, n=NT)
    base = base - base.mean()
    base = base / max(float(np.abs(base).max()), 1e-9)
    _THICK_CACHE["F"] = keep
    _THICK_CACHE["base"] = base
    _THICK_CACHE["edge"] = edge
    return keep


def glow_thick_table():
    """基准形状（t=0，含全部谐波 ⇒ 本来就不规则、不是镜像对称的）。"""
    glow_thick_coefs()
    return _THICK_CACHE["base"]


def thick_table_phase(u):
    """该时刻的厚薄形状表（256 点，零均值、峰值 ±1）。u=None → 基准形状。

    实现：把基准形状的各谐波系数按 `exp(i·n_k·2π·u)` 各自旋转 —— 这就是"每个谐波
    各转各的整数圈"。因为每个 n_k 都是整数，u=1 时与 u=0 完全重合（首尾无缝）。
    """
    if u is None:
        return glow_thick_table()
    key = int(round((float(u) % 1.0) * 1440)) % 1440
    hit = _THICK_PH.get(key)
    if hit is not None:
        return hit
    F = glow_thick_coefs()
    # ★ 符号很关键：屏幕坐标 y 向下时 θ 增大 = 顺时针，必须与睫毛同向。
    #   数学上 S(θ,t)=Σ|F_k|cos(kθ+ψ_k+φ_k) 的特征满足 dθ/dt = −(1/k)·dφ_k/dt，
    #   所以取 **−i** 才让形状顺时针转（取 +i 会转反 —— 实测 k=2 得到 −24.27°/s）。
    ph = np.exp(-1j * 2 * np.pi * (key / 1440.0) * thick_rot_turns())
    rec = np.fft.irfft(F * ph, n=F.size * 2 - 2)
    rec = rec - rec.mean()
    rec = rec / max(float(np.abs(rec).max()), 1e-9)
    _THICK_PH.clear()                          # 只留最近一个，别囤内存
    _THICK_PH[key] = rec
    return rec


def thick_at(th, u=None):
    """按角度取厚薄因子（对 256 点表做周期线性插值）。u=None → 基准形状。"""
    T = thick_table_phase(u)
    n = T.size
    f = (np.asarray(th, dtype=np.float64) / (2 * np.pi) % 1.0) * n
    j0 = np.floor(f).astype(np.int32) % n
    j1 = (j0 + 1) % n
    w = f - np.floor(f)
    return T[j0] * (1.0 - w) + T[j1] * w


def glow_sample_rho(rho):
    """画面半径 → 贴图采样半径。内侧走合成斜坡（见 glow_layer）；外侧按 GLOW_TAIL_K 压缩拖尾。"""
    rho = np.asarray(rho, dtype=np.float64)
    return np.where(rho <= GLOW_SYNTH_TO, GLOW_SYNTH_TO,
                    GLOW_SYNTH_TO + (rho - GLOW_SYNTH_TO) * GLOW_TAIL_K)


def _bilerp_polar(tbl, fi, fj):
    """在极坐标表 (NR,NT[,C]) 上双线性插值；角向按周期环绕。"""
    NR, NT = tbl.shape[0], tbl.shape[1]
    i0 = np.clip(fi.astype(np.int32), 0, NR - 2)
    j0 = np.floor(fj).astype(np.int32) % NT
    j1 = (j0 + 1) % NT
    wi = fi - i0
    wj = fj - np.floor(fj)
    if tbl.ndim == 3:
        wi = wi[..., None]
        wj = wj[..., None]
    v0 = tbl[i0, j0] * (1 - wj) + tbl[i0, j1] * wj
    v1 = tbl[i0 + 1, j0] * (1 - wj) + tbl[i0 + 1, j1] * wj
    return v0 * (1 - wi) + v1 * wi


_SMOKE_CACHE = {}


def smoke_field(phase, gphase=None, NR2=96, NT2=192):
    """烟团湍流场（粗极坐标网格，再插值到像素）。同相位只算一次。

    phase  = 呼吸相位（1.5 s 一圈，团块的小幅游荡/生灭都挂在这个快节奏上）
    gphase = 辉光相位（7.5 s 一圈，**剪切与拉丝**挂在这个慢节奏上，1 整圈 ⇒ 闭合）

    ★★ 与上一版的根本区别：上一版每个团沿径向**从内向外单向漂移**（像水波往外推），
    新版让每个团**在自己的位置上自由游荡**：
      · 径向零均值来回（有正有负 ⇒ 有的向内、有的向外）；
      · 角向绕基准位置摆动，幅度/频率各团不同；
      · 强度各有生灭（包络起伏）。
    所有运动由**整数频率的正弦和**合成 ⇒ 相位走满一圈时逐点闭合（准周期，非真随机）。
    """
    key = int(round((float(phase) % 1.0) * 600)) % 600
    if gphase is not None:
        key = (key * 1009 + int(round((float(gphase) % 1.0) * 720))) % 1000003
    hit = _SMOKE_CACHE.get(key)
    if hit is not None:
        return hit

    rng = np.random.RandomState(SMOKE_SEED)
    N = SMOKE_N
    th0 = rng.uniform(0, 2 * np.pi, N)                       # 基准角位置
    r0 = rng.uniform(SMOKE_R_IN, SMOKE_R_OUT, N)             # 基准半径
    amp0 = rng.uniform(0.55, 1.25, N)                        # 各团强度
    wob_th = rng.uniform(SMOKE_WOB_TH[0], SMOKE_WOB_TH[1], N)   # 角向游荡幅度
    wob_r = rng.uniform(SMOKE_WOB_R[0], SMOKE_WOB_R[1], N)      # 径向游荡幅度
    fr = rng.randint(1, 4, size=(N, 3))                      # 整数频率 1/2/3
    ph_t = rng.uniform(0, 2 * np.pi, (N, 3))
    ph_r = rng.uniform(0, 2 * np.pi, (N, 3))
    env_f = rng.randint(1, 3, size=N)                        # 生灭频率
    env_p = rng.uniform(0, 2 * np.pi, N)

    # ★ 新参数走**独立随机流**：若混用同一个 rng，会把它后面的抽样整体挪位，
    #   于是 BASE（乘数为 0、本该零影响）的输出也会变 —— 生产缓存就作废了。
    rng2 = np.random.RandomState(SMOKE_SEED + 977)
    elong_p = rng2.uniform(0, 2 * np.pi, N)                  # 各团拉丝的开合相位
    elong_s = rng2.uniform(SMOKE_ELONG_S[0], SMOKE_ELONG_S[1], N)
    fr_s = rng2.randint(1, 3, size=(N, 2))                   # 慢漂移的整数频率 1/2
    ps_t = rng2.uniform(0, 2 * np.pi, (N, 2))
    ps_r = rng2.uniform(0, 2 * np.pi, (N, 2))
    sd_t = rng2.uniform(0.55, 1.45, N)                       # 各团慢漂移强度
    sd_r = rng2.uniform(0.55, 1.45, N)

    u = float(phase) % 1.0
    # 剪切/拉丝用的慢相位：给了就用辉光相位，没给就退回快相位（老行为）
    gg = (float(gphase) % 1.0) if gphase is not None else u
    rr = np.linspace(0.0, 1.7, NR2)[:, None]
    tt = np.linspace(0, 2 * np.pi, NT2, endpoint=False)[None, :]
    acc = np.zeros((NR2, NT2))
    for i in range(N):
        # 角向游荡（绕基准位置的摆动）—— 三个整数频率分量叠加，各团系数不同
        dth = (np.sin(2 * np.pi * fr[i, 0] * u + ph_t[i, 0])
               + np.sin(2 * np.pi * fr[i, 1] * u + ph_t[i, 1])
               + np.sin(2 * np.pi * fr[i, 2] * u + ph_t[i, 2])) / 3.0
        # 径向游荡（**零均值** ⇒ 不向外漂；有正有负 ⇒ 有的向内有的向外）
        dr = (np.sin(2 * np.pi * fr[i, 0] * u + ph_r[i, 0])
              + np.sin(2 * np.pi * fr[i, 1] * u + ph_r[i, 1])
              + np.sin(2 * np.pi * fr[i, 2] * u + ph_r[i, 2])) / 3.0
        # 生灭包络（各团各有节奏 ⇒ 不再"整圈一起明暗"）
        env = SMOKE_ENV_MIN + (1.0 - SMOKE_ENV_MIN) * max(
            0.0, float(np.sin(2 * np.pi * env_f[i] * u + env_p[i]))) ** 1.2
        a_i = amp0[i] * env
        # ---- 差速剪切：角位移 ∝ (半径 − 中性半径) ⇒ 内侧与外侧相互错动（剪切流）----
        #   用 gphase 的整数圈正弦 ⇒ u=1 时精确归零；SMOKE_SHEAR=0 时加 0.0（不动 BASE）。
        sh = SMOKE_SHEAR * (r0[i] - SMOKE_SHEAR_R0) * float(np.sin(2 * np.pi * SMOKE_SHEAR_F * gg))
        # ---- 慢速漂移：挂在 7.5 s 慢相位上的整数圈正弦和（BASE 幅度为 0 ⇒ 加 0.0）----
        sl_t = float(np.sin(2 * np.pi * fr_s[i, 0] * gg + ps_t[i, 0])
                     + np.sin(2 * np.pi * fr_s[i, 1] * gg + ps_t[i, 1])) / 2.0
        sl_r = float(np.sin(2 * np.pi * fr_s[i, 0] * gg + ps_r[i, 0])
                     + np.sin(2 * np.pi * fr_s[i, 1] * gg + ps_r[i, 1])) / 2.0
        thi = th0[i] + wob_th[i] * dth + SMOKE_WOB_SLOW_TH * sd_t[i] * sl_t + sh
        ri = r0[i] + wob_r[i] * dr + SMOKE_WOB_SLOW_R * sd_r[i] * sl_r
        # ---- 拉丝：团 = 两瓣（角向分列、径向微错），间距周期性开合 ⇒ 拉长成涡丝又收拢 ----
        sep = SMOKE_ELONG * elong_s[i] * (0.5 - 0.5 * float(np.cos(
            2 * np.pi * SMOKE_ELONG_F * gg + elong_p[i])))
        # ★ 两瓣各占 0.5 权重：sep=0 时两瓣重合，0.5x+0.5x 在 IEEE 下**精确等于** x ⇒ BASE 逐位不变。
        # ★ 拉丝时把两瓣收窄（kk∈[0,1] 是"拉丝开启度"）：sep 必须 > 团宽才看得出来。
        kk = 0.0 if SMOKE_ELONG <= 1e-9 else float(sep) / SMOKE_ELONG
        wt = SMOKE_WTH * (1.0 - SMOKE_ELONG_NARROW * kk)
        w0 = 0.5 + SMOKE_ELONG_ASYM * kk            # 不对称 ⇒ "一团带一条尾"
        for sg, wl in ((1.0, w0), (-1.0, 1.0 - w0)):
            tha = thi + sg * sep
            ra = ri + sg * sep * SMOKE_ELONG_R
            d = (tt - tha + np.pi) % (2 * np.pi) - np.pi
            acc += wl * a_i * np.exp(-0.5 * (d / wt) ** 2) \
                       * np.exp(-0.5 * ((rr - ra) / SMOKE_WR) ** 2)

    out = acc.astype(np.float32)
    # 零均值 + 稳健尺度（P96）归一化到 ±1：这样 SMOKE_AMP 才是真正的"相对幅度"，
    # 与团数/叠加无关（否则原始团块峰值能到 2.5，一乘就顶穿不饱和预算）。
    out = out - float(out.mean())
    sc = float(np.percentile(np.abs(out), 96.0))
    out = out / max(sc, 1e-6)
    _SMOKE_CACHE.clear()                 # 只留最近一个，别囤内存
    _SMOKE_CACHE[key] = out
    return out


def glow_layer(x, y, phase=None):
    """辉光 = 合成的**单调径向剖面** × 实测的**角向不规则** × 烟团湍流。

    ★ 为什么不直接采素材的径向剖面：素材带 bloom 亮环，照抄就会出现"一圈实心光晕"。
      现在只用素材的角向图案与配色，径向改成单调衰减 —— 这才是"从主体向外发散"的观感。

    phase=None → 静态（供扫描线遮罩与对照）；0..1 → 该相位的动态辉光。
    """
    rho_t, th_t, rgb_t = glow_table()
    rho = np.sqrt((x - 80.0) ** 2 + (y - 80.0) ** 2) / GLOW_R_DISC
    th = np.arctan2(y - 80.0, x - 80.0) % (2 * np.pi)
    NR, NT = rgb_t.shape[0], rgb_t.shape[1]
    step = rho_t[1] - rho_t[0]
    fj = th / (2 * np.pi) * NT

    # ---- 角向图案：取素材在 GLOW_ANG_R 处的一整圈颜色（保留实测六瓣等不规则）----
    idx_pk = int(np.clip(round((GLOW_ANG_R - rho_t[0]) / step), 0, NR - 1))
    mean_ang = rgb_t[idx_pk].mean(axis=0)          # 该圈的角向均值（3 通道）
    ang = _bilerp_polar(rgb_t, np.full(rho.shape, float(idx_pk)), fj)
    ang = mean_ang + (ang - mean_ang) * GLOW_ANG_GAIN      # 放大不规则度
    ang = np.clip(ang, 0.0, 255.0)

    # ---- ★ 厚薄图案的角向游移（只移动位置、不抬峰值 ⇒ 不引入削顶）----
    #   u0 = 相位；摆动取 sin(2πu) + sin(4πu+φ)：一个 7.5s 循环里 u 走 5 个呼吸周期，
    #   两个频率都整周期闭合 ⇒ 首尾帧完全同姿态（实测接缝差 0）。
    if phase is None:
        u_g, amp_t = None, GLOW_THICK_AMP
    else:
        u_g = float(phase) % 1.0
        _ub = (u_g * GLOW_BREATH_PER_CYCLE) % 1.0
        amp_t = GLOW_THICK_AMP * (1.0 + GLOW_THICK_BREATHE * float(np.sin(2 * np.pi * _ub + 0.9)))

    # ---- ★ 角向厚薄：厚处更亮、薄处更淡（随后整体归一化回 GLOW_PEAK，不会削顶）----
    #   ★★ 形状**不是刚体旋转**：每个谐波按自己的整数圈数转（见 thick_rot_turns）。
    #      主导的偶数谐波仍同速（24°/s，与睫毛锁相），奇数谐波略快 ⇒ 形状持续变形
    #      ⇒ "看起来无规律"，但每个谐波都整圈闭合 ⇒ 首尾帧逐点吻合。
    Tz = thick_table_phase(u_g)                 # 该时刻的 256 点形状表
    tz = thick_at(th, u_g)                      # 投到像素
    ang = ang * (1.0 + GLOW_THICK_TONE * tz)[..., None]
    base = np.clip(mean_ang + (rgb_t[idx_pk] - mean_ang) * GLOW_ANG_GAIN, 0.0, 255.0)
    base = base * (1.0 + GLOW_THICK_TONE * Tz)[:, None]
    ang = ang * (GLOW_PEAK / max(float(base.max()), 1.0))

    # ---- ★ 径向：按角度缩放采样半径 ⇒ 边界不再是正圆（有的区域厚、有的区域薄）----
    #   scale > 1（厚）⇒ rho/scale 偏小 ⇒ 衰减更慢 ⇒ 延伸更远；薄则相反。
    w_th = smoothstep(np.clip((rho - GLOW_THICK_IN0) / (GLOW_THICK_IN1 - GLOW_THICK_IN0),
                              0.0, 1.0))
    scale = 1.0 + amp_t * tz * w_th
    prof = _interp_stops(rho / scale, GLOW_PROF)
    rgb = ang * prof[..., None]

    if phase is None:
        return rgb

    # ---- 动态：全局呼吸 + （亮环 + 烟团）× 径向权重 ----
    #   ★ 只有"厚薄的旋转"是 7.5 s 的匀速圈；呼吸/亮环/烟团仍在 **1.5 s 呼吸节奏**上
    #     （由辉光相位换算而来，7.5 s 内整 5 次 ⇒ 闭合）。
    u0 = (u_g * GLOW_BREATH_PER_CYCLE) % 1.0
    extra = np.zeros_like(rho)
    for off, amp in ((0.0, 1.0), (RIPPLE_PHASE_OFF, RIPPLE_SECOND)):
        u = (u0 + off) % 1.0
        rc = RIPPLE_R0 + (RIPPLE_R1 - RIPPLE_R0) * u
        ring = np.exp(-((rho - rc) / RIPPLE_W) ** 2)
        ring = ring * min(1.0, u / RIPPLE_FADE_IN) * (1.0 - u) ** 1.4
        extra = extra + RIPPLE_AMP * amp * ring
    sm = smoke_field(u0, u_g)      # u_g = 辉光相位（剪切/拉丝挂在这条 7.5 s 慢节奏上）
    fi2 = np.clip(rho / 1.7, 0.0, 1.0) * (sm.shape[0] - 1.001)
    fj2 = th / (2 * np.pi) * sm.shape[1]
    # ★ 烟团场已是「零均值、稳健尺度 ±1」（见 smoke_field）：飘过时**既能提亮也能压暗**。
    #   只往上加 → 亮区直接被顶到 255 → 一片饱和 → 又变成"一圈实心光晕"（实测过）。
    turb = _bilerp_polar(sm, fi2, fj2)
    w = SMOKE_IN_W + (1.0 - SMOKE_IN_W) * smoothstep(
        np.clip((rho - SMOKE_IN0) / (SMOKE_IN1 - SMOKE_IN0), 0.0, 1.0))
    mult = (1.0 + GLOW_BREATHE_AMP * float(np.sin(2 * np.pi * u0))
            + w * extra + w * SMOKE_AMP * turb)
    if SMOKE_DYN_GAIN != 1.0:   # ★ 演示用放大；生产恒为 1.0（走分支，不动 BASE 的算术）
        mult = 1.0 + (mult - 1.0) * SMOKE_DYN_GAIN
    return rgb * np.clip(mult, 0.15, GLOW_MULT_MAX)[..., None]


def glow_cover(x, y):
    """辉光覆盖度（0..1），用来给扫描线做遮罩 —— 原素材里扫描线只出现在辉光区内。"""
    v = glow_layer(x, y).max(axis=2) / 255.0
    return np.clip(v / 0.02, 0.0, 1.0)


# ---------- ④b 工作态三层的"形状语言"：一律从辉光派生 ----------
def glow_color_ratio(rho_target=1.12):
    """辉光在某个半径上的**角向平均色比例**（最大通道归一化到 1）= 数字流该用的颜色。

    ★ 为什么不能写死 #c9f8ff：实测辉光是**蓝**的 ——
        rho=1.10 → mean(47.0, 116.2, 180.7) → 比例 (0.260, 0.643, 1.000)
        rho=1.30 → (17.2, 64.4, 124.4)      → 比例 (0.138, 0.518, 1.000)
        rho=1.45 → ( 7.8, 37.1,  80.5)      → 比例 (0.097, 0.461, 1.000)
      而 #c9f8ff 的比例是 (0.788, 0.973, 1.000) —— 近乎白的青。
      两者的**色相完全不同**，肉眼就是"另一个颜色"（主人 2026-09-21 指出的正是这个）。
    """
    rho_t, th_t, rgb_t = glow_table()
    step = rho_t[1] - rho_t[0]
    i = int(np.clip(round((float(rho_target) - rho_t[0]) / step), 0, rgb_t.shape[0] - 1))
    c = rgb_t[i].mean(axis=0)
    m = float(c.max())
    return c / (m if m > 1e-6 else 1.0)


def glow_color_at(rho_target=1.12):
    """数字流用的**颜色档**：辉光色比例 + 向白靠一点（越内侧越白 ⇒ 与辉光同源的层次）。"""
    return glow_color_ratio(rho_target) * (1.0 - WORK_COL_WHITEN) + WORK_COL_WHITEN


def work_bg_layer(x, y):
    """工作态 L0：数字背后的**光源**（undershine）—— 平滑、低频、非正圆。

    静态（光源恒定），但形状语言与辉光同一套（同样的角向厚薄基准形状、同样的配色）
    ⇒ 常态→工作态切换时"光还在，只是换了形态"，不会断层。
    返回"黑底观感"RGB（float64 0..255）。
    """
    rho = np.sqrt((x - 80.0) ** 2 + (y - 80.0) ** 2) / GLOW_R_DISC
    th = np.arctan2(y - 80.0, x - 80.0) % (2 * np.pi)
    tz = thick_at(th)                       # u=None ⇒ 基准形状（静态、不规则、非镜像对称）
    prof = _interp_stops(rho / (1.0 + 0.05 * tz), WORK_BG_PROF)
    v = WORK_BG_GAIN * GLOW_PEAK * prof * (1.0 + WORK_BG_TONE * tz)
    return np.clip(v[..., None] * glow_color_ratio(1.12)[None, None, :], 0.0, 255.0)


def work_moire_layer(x, y, phase=0.0):
    """工作态 L2：摩尔纹 —— 两组细纹的**干涉**（主人："需要加一层摩尔纹，辉光那边有这种效果"）。

    组 A = 角向细纹（从中心放射的细线，随主体**顺时针**缓慢移动，与睫毛/辉光同向）
    组 B = 水平细纹（沿屏幕 y 的细横线，缓慢**向下**流动）
    A·B 的**乘积相**给出菱形/双曲线状的干涉瓣 —— 缓慢变形，
    ★ 刻意**不做**同心圆环（那会变成主人明确讨厌的"水波/重力波"）。
    两组在 7.5 s 内各走整数个间距/周期 ⇒ 首尾逐点吻合。
    亮度包络照抄辉光（径向剖面 × 角向厚薄）⇒ 只在辉光区浮现、且外缘不规则。
    """
    u = float(phase) % 1.0
    rho = np.sqrt((x - 80.0) ** 2 + (y - 80.0) ** 2) / GLOW_R_DISC
    th = np.arctan2(y - 80.0, x - 80.0) % (2 * np.pi)
    # ★ θ 增大 = 屏幕上的顺时针（y 向下）⇒ 与睫毛、辉光厚薄同向
    A = np.cos(2 * np.pi * (WORK_MOIRE_N * (th / (2 * np.pi)
                                            + WORK_MOIRE_ROT * u / WORK_MOIRE_N)
                            + WORK_MOIRE_PH_A))
    B = np.cos(2 * np.pi * (y / WORK_MOIRE_PY - WORK_MOIRE_DY * u + WORK_MOIRE_PH_B))
    #   ★ 两组条纹**相加**（不是相乘）：cos(f1)+cos(f2) = 2cos(Δf/2)·cos(Σf/2) ⇒
    #     既保留了"看得见的细纹"（基频），又保留了拍频（摩尔纹的大块明暗）。
    #     相乘的话亮部只落在两组同时为峰的交点上 —— 稀疏亮点，视觉上就是"看不见"（实测过）。
    m = 0.5 + 0.25 * A + 0.25 * B      # 0..1（A=B=1 时恰好 1.0，不削顶），均值 0.5
    #   ★ env 两道：① 辉光的径向剖面（只在辉光区）② **盘缘内侧渐入** ——
    #     角向细纹是"固定条数"，半径越小弧长越短 ⇒ 盘缘内侧会密到 1 px 以下（视觉上像"放射光芒"
    #     而不是"干涉网"）。渐入之后细纹从 1.02R 才成形，观感是"贴着盘缘浮起来的一片网"。
    env = _interp_stops(rho, GLOW_PROF) * smoothstep((rho - 1.02) / 0.14)
    tz = thick_at(th)
    v = WORK_MOIRE_AMP * GLOW_PEAK * m * env * (1.0 + GLOW_THICK_TONE * tz)
    return np.clip(v[..., None] * glow_color_ratio(1.20)[None, None, :], 0.0, 255.0)


def work_mist_alpha(glow_rgb):
    """工作态 L1 淡雾的 mask：**直接取辉光的 alpha** × WORK_MIST_K。

    ⇒ 雾与辉光逐像素同源：辉光在哪儿亮、雾就在哪儿浓；辉光怎么动（呼吸/烟团/厚薄旋转），
      雾就怎么动 —— 这才是"同一束光的残影"，而不是另画一层雾。
    """
    return np.clip(np.asarray(glow_rgb, dtype=np.float64).max(axis=2) * WORK_MIST_K, 0.0, 255.0)


FOOT_IN, FOOT_OUT = 76.0, 79.6      # 兼容旧调用（见 _work 历史），glow 贴图已自带收边


def footprint(r):
    t = np.clip((FOOT_OUT - np.asarray(r, dtype=np.float64)) / (FOOT_OUT - FOOT_IN), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# ---------- 动画时间常量 ----------
PULSE_CYCLE_MS = 1440.0          # 官方值（浏览器的 0.72s alternate）
PULSE_CYCLE_LOOP_MS = 1500.0     # ★ 循环片用 1.5s：与睫毛视觉周期 3.75s 在 7.5s 内公倍数
PULSE_LEAD_MS = {"sclera": 0.0, "l3": 45.0, "l2": 90.0, "l1": 180.0}
PULSE_SCALE = {"sclera": (0.985, 0.91), "l3": (1.0, 0.90),
               "l2": (1.0, 0.87), "l1": (1.0, 0.85)}
LASH_PERIOD_MS = 15000.0         # 睫毛 15s 一整圈（视觉周期 3.75s）
THINK_CLIP_ORIGIN = (80.0, 60.0)
LOOP_MS = 7500.0                 # ★ 循环片长度：7.5s = 2×睫毛视觉周期 = 5×呼吸周期

# ---------- 闪烁（mascot-runtime.js scheduleEyeFlicker）----------
FLICKER_DUR_MS = (155, 255)
FLICKER_GAP_MS = (1750, 3600)        # data-state="normal" 时的间隔
FLICKER_OP_1 = (0.026, 0.048)        # 22% 处
FLICKER_OP_MID = (0.006, 0.016)      # 48% 处
FLICKER_OP_2 = (0.018, 0.040)        # 72% 处
FLICKER_BEZIER = (0.32, 0.0, 0.68, 1.0)

# ---------- ★ 半睁眼睑：位置 + 不对称浮动 ----------
# 眼睑弧中点 y：数值越大 = 越低 = 越闭。眼白为 (80,80) r=48 ⇒ y 从 32 到 128（96 单位）。
#   ★ 主人从对照条上指定"落位 86"那一格（遮挡 56%），不是红线的 88.4。
#     于是浮动区间 = 85.1 ↑ ～ 88.4 ↓ —— 向下摆到头正好触到那条红线，向上只轻轻抬。
LID_MID_THINK = 86.0             # 落位（主人指定），遮挡 (86-32)/96 = 56.3%
LID_DOWN_AMP = 2.0               # 向下（更闭）幅度 —— 主人指定 2.0 ⇒ 下探到 88.0
LID_UP_AMP = 0.9                 # 向上（更开）幅度 —— 故意小于向下
#   ⇒ 浮动区间 85.1 ↑ ～ 88.0 ↓，总峰峰 2.9 unit（略低于视频实测的 3.4）。
#   总峰峰 3.3 unit，与视频实拍量得的 ≈3.3~3.4 一致（不靠放大总幅度体现"向下大"）。
#   想还原官方那条公式：把 LID_MID_THINK 设为 None。
LID_SAG_BASE = 24.0              # 弧的垂度
LID_SAG_AMP = 1.0
LID_MID_OPEN = 22.0              # 全睁时弧的中点（在眼白之上，完全不遮）
RED_LINE_UNIT_Y = 88.4           # 主人那条红线换算出的位置（仅用于对照条标注）


def hx(h):
    h = h.lstrip("#")
    return np.array([int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)], dtype=np.float64)


def _bezier_x(t, x1, x2):
    return 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t ** 2 * x2 + t ** 3


def ease_eye(x, x1=0.72, x2=0.28):
    """CSS cubic-bezier(x1,0,x2,1) 的 y(x)。对应官方 easeEyePhase() 的二分反解。"""
    x = float(np.clip(x, 0.0, 1.0))
    lo, hi = 0.0, 1.0
    for _ in range(26):
        t = (lo + hi) / 2
        if _bezier_x(t, x1, x2) < x:
            lo = t
        else:
            hi = t
    t = (lo + hi) / 2
    return 3 * t * t - 2 * t ** 3


def read_eye_progress(phase):
    c = phase % 1.0
    tri = c * 2 if c <= 0.5 else 2 - c * 2
    return ease_eye(tri)


def eye_scales(t_ms, cycle_ms=PULSE_CYCLE_MS):
    return {k: a + (b - a) * read_eye_progress(t_ms / cycle_ms + PULSE_LEAD_MS[k] / cycle_ms)
            for k, (a, b) in PULSE_SCALE.items()}


def lash_angle(t_ms):
    return (360.0 * t_ms / LASH_PERIOD_MS) % 360.0


def cubic_bezier_y(x, x1, y1, x2, y2):
    """CSS cubic-bezier(x1,y1,x2,y2) 的 y(x)"""
    x = float(np.clip(x, 0.0, 1.0))
    lo, hi = 0.0, 1.0
    for _ in range(24):
        t = (lo + hi) / 2
        if _bezier_x(t, x1, x2) < x:
            lo = t
        else:
            hi = t
    t = (lo + hi) / 2
    return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3


def flicker_opacity(u, o1, o_mid, o2):
    """官方 @keyframes dsh-fairy-eye-flicker-overlay：0/22/48/72/100% 五个关键帧，
    每个区间各自套 cubic-bezier(.32,0,.68,1)。"""
    stops = [(0.0, 0.0), (0.22, o1), (0.48, o_mid), (0.72, o2), (1.0, 0.0)]
    u = float(np.clip(u, 0.0, 1.0))
    for i in range(len(stops) - 1):
        a, va = stops[i]
        b, vb = stops[i + 1]
        if u <= b:
            k = cubic_bezier_y((u - a) / (b - a), *FLICKER_BEZIER)
            return va + (vb - va) * k
    return 0.0


def _interp_stops(r, stops):
    pos = np.array([s[0] for s in stops], dtype=np.float64)
    vals = np.stack([np.atleast_1d(np.asarray(s[1], dtype=np.float64)) for s in stops])
    if vals.shape[1] == 1:
        return np.interp(r, pos, vals[:, 0], left=vals[0, 0], right=vals[-1, 0])
    out = np.empty(r.shape + (vals.shape[1],), dtype=np.float64)
    for k in range(vals.shape[1]):
        out[..., k] = np.interp(r, pos, vals[:, k], left=vals[0, k], right=vals[-1, k])
    return out


class DSHSvg:
    """按官方 SVG 数值渲染。px_per_unit = 显示尺寸；ss = 超采样倍率。"""

    def __init__(self, px_per_unit=2.6, ss=3, halo_boost=1.0, halo_gain=1.0,
                 margin_px=None):
        self.s = px_per_unit
        self.ss = ss
        self.halo_boost = halo_boost      # 外发光半径放大系数
        self.halo_gain = halo_gain        # 外发光强度系数
        # ★ margin_px：画布在 160unit 视图之外每边多留多少**最终像素**
        #   （取整到整像素 ⇒ 乘 ss 后天然落在网格上 ⇒ 宠物中心恰在 n/2，裁剪框不会错位）
        self.margin_px = (canvas_margin_px(px_per_unit) if margin_px is None
                          else max(0, int(margin_px)))
        pet_px = 160.0 * px_per_unit                       # 宠物本体像素数
        n = int(round(pet_px * ss)) + 2 * self.margin_px * ss
        n = ((n + ss - 1) // ss) * ss
        self.n = n
        off = self.margin_px * ss
        yy, xx = np.mgrid[0:n, 0:n].astype(np.float64)
        self.x = (xx - off) / (px_per_unit * ss)      # 仍是"宠物坐标"：中心 (80,80)
        self.y = (yy - off) / (px_per_unit * ss)

    def _aa(self, d, w=0.7):
        return np.clip(0.5 - d / w, 0.0, 1.0)

    # ---------- 眼睑遮罩（连续可调，用于平顺过渡）----------
    def lid_mask(self, open_t=1.0, breathe=0.5, feather=1.6):
        """open_t: 1.0 = 完全睁开（眼睑抬到眼白之上）；0.0 = thinking 半睁位置。
        breathe: 0..1 → ★ 落位后的**小幅**呼吸（LID_BREATH_AMP 控制幅度）。
        """
        x, y = self.x, self.y
        if LID_MID_THINK is None:                       # 严格还原官方
            ty, sy = 14.5 + breathe, 0.55 + 0.45 * breathe
            mid_think, sag = 15.0 * sy + 60.0 + ty, 30.0 * sy
        else:
            u = (breathe - 0.5) * 2.0                   # -1 .. +1
            # ★ 不对称：向下（u>0，更闭）幅度大，向上（u<0，更开）幅度小
            mid_think = LID_MID_THINK + (LID_DOWN_AMP if u > 0 else LID_UP_AMP) * u
            sag = LID_SAG_BASE + LID_SAG_AMP * u
        mid = LID_MID_OPEN + (mid_think - LID_MID_OPEN) * (1.0 - float(np.clip(open_t, 0.0, 1.0)))
        A = mid - sag / 2.0
        B = mid + sag / 2.0
        tt = np.linspace(0, 1, 3001)
        cx_ = 20 * (1 - tt) ** 2 + 160 * tt * (1 - tt) + 140 * tt ** 2
        cy_ = A * (1 - tt) ** 2 + 2 * B * tt * (1 - tt) + A * tt ** 2
        ycurve = np.interp(x, cx_, cy_)
        m = np.clip(0.5 + (y - ycurve) / max(feather, 1e-6), 0.0, 1.0)
        m = np.where((x >= 20) & (x <= 140), m, 1.0)
        m = np.maximum(m, np.clip(0.5 + (y - 108.0) / max(feather, 1e-6), 0.0, 1.0))
        return m

    def render(self, open_t=1.0, breathe=0.5, t_ms=0.0, flicker_op=0.0, work_k=0.0, cycle_ms=PULSE_CYCLE_MS):
        # work_k: 工作权重 0..1（>0 时把辉光那一圈换成 0/1 数字流）
        """open_t: 1=睁眼 0=thinking半睁（中间值 = 过渡中的连续形态）
        flicker_op: 眼睛闪烁层的不透明度（官方最大仅 0.048）
        cycle_ms: 眼睛分层呼吸周期（循环片用 1500 保证首尾对齐）"""
        x, y = self.x, self.y
        n = self.n
        sc = eye_scales(t_ms, cycle_ms)
        img = np.zeros((n, n, 3), dtype=np.float64)

        def over(col, a):
            nonlocal img
            img = img * (1 - a[..., None]) + np.asarray(col, dtype=np.float64) * a[..., None]

        # ---------- 1. 外圈辉光：**实测贴图 + 动态**（不规则外发散 + 向外扩散的亮环）----------
        # 贴图的 RGB 本身就是"黑底观感"（自身 alpha 已含在里边），所以直接铺底，不再乘 alpha。
        # ★ 相位必须与"实时渲染器烘焙的相位表"用同一套时间轴，否则两边对不上（fairy_bench 会报错差）。
        # ★ 辉光用自己的周期（7.5 s），不是眼睛的呼吸周期（1.5 s）
        img = glow_layer(x, y, phase=(t_ms / GLOW_CYCLE_MS) % 1.0) * self.halo_gain

        # ---------- 1b. ★ 工作态：辉光那一圈换成「底光 → 淡雾 → 摩尔纹 → 0/1 数字流」----------
        #   work_k（0=常态 1=工作态）由调用方给；生产里 k = 1 − open_t ⇒ 与眼睑同一条缓动，
        #   所以"眼睑压下来"与"辉光换形态"天然同步，不会各走各的。
        #   ★ 四层都是**从辉光派生**的（配色 / 径向剖面 / 角向厚薄 / 淡雾直接复用辉光）
        #     ⇒ 主人要的"一致性"：主体卸下伪装，辉光变成了 01 的二进制符号。
        if work_k > 0.004:
            _k = float(work_k)
            _u = (t_ms / GLOW_CYCLE_MS) % 1.0
            # L0 底光：与辉光的淡出**同时**淡入（一步 α 混合；分两步各乘 (1−k) 会把辉光淡出两次）
            img = img * (1.0 - _k) + work_bg_layer(x, y) * self.halo_gain * _k
            # L1 淡雾：辉光的淡残影（mask 逐像素取自辉光本身）
            _gl = glow_layer(x, y, phase=_u) * self.halo_gain
            _ma = (work_mist_alpha(_gl) * _k / 255.0)[..., None]
            img = img * (1.0 - _ma) + _gl * _ma
            # L2 摩尔纹
            img = np.maximum(img, work_moire_layer(x, y, phase=_u) * self.halo_gain * _k)
            # L3 数字（在**最终分辨率**画，再最近邻放大 ⇒ 笔画柔化不会被超采样平均掉）
            _n2 = self.n // self.ss
            _dl, _nd = FD.digit_layer(_n2, (_n2 - 1) / 2.0, (_n2 - 1) / 2.0,
                                      GLOW_R_DISC * self.s, self.s, t_ms, work_k)
            _arr = np.asarray(_dl, dtype=np.float64)
            if self.ss > 1:                                 # 数字在**最终分辨率**画，再最近邻放大
                _arr = np.repeat(np.repeat(_arr, self.ss, 0), self.ss, 1)
            img = np.maximum(img, _arr)                     # 都是"光" ⇒ 取 max 叠加

        # ---------- 2. 外盘（线性渐变 + 白描边）----------
        ga = np.array([0.2 * 160, 0.0]); gb = np.array([0.8 * 160, 160.0])
        v = gb - ga
        t = np.clip(((x - ga[0]) * v[0] + (y - ga[1]) * v[1]) / (v @ v), 0, 1)
        disc_col = _interp_stops(t, [(0.0, hx(DISC_GRAD[0][0])), (0.54, hx(DISC_GRAD[1][0])),
                                     (1.0, hx(DISC_GRAD[2][0]))])
        d = np.sqrt((x - 80) ** 2 + (y - 80) ** 2) - OUTER_DISC
        over(disc_col, self._aa(d))
        over(hx(STROKE_RIM), self._aa(np.abs(d) - OUTER_STROKE / 2))

        # ---------- 3. 睫毛/眼睑：深色圆 + 旋转方（裁到盘内）----------
        # CSS rotate(+360deg) 在屏幕坐标（y 向下）里是顺时针；采样要用逆旋转。
        ang = np.radians(lash_angle(t_ms) + CORNER_BASE_ROT)
        xr = 80 + (x - 80) * np.cos(ang) + (y - 80) * np.sin(ang)
        yr = 80 - (x - 80) * np.sin(ang) + (y - 80) * np.cos(ang)
        dx = np.maximum(np.abs(xr - 80) - (CORNER_SQUARE_HALF - CORNER_SQUARE_R), 0.0)
        dy = np.maximum(np.abs(yr - 80) - (CORNER_SQUARE_HALF - CORNER_SQUARE_R), 0.0)
        d_sq = np.sqrt(dx ** 2 + dy ** 2) - CORNER_SQUARE_R
        d_cc = np.sqrt((x - 80) ** 2 + (y - 80) ** 2) - CORNER_CIRCLE
        disc_clip = self._aa(d + 1.0)                     # clipPath circle r = 68-1 = 67
        over(hx(DARK_CORNERS), self._aa(np.minimum(d_cc, d_sq)) * disc_clip)

        # ---------- 4. eye 组（每层各自缩放；整组受 state clip）----------
        tmp = np.zeros((n, n, 3), dtype=np.float64)
        r_all = np.sqrt((x - 80) ** 2 + (y - 80) ** 2)
        ds = r_all / sc["sclera"]
        a1 = self._aa(ds - SCLERA)
        tmp = tmp * (1 - a1[..., None]) + hx(SCLERA_COL) * a1[..., None]
        sh = _interp_stops(ds / SCLERA_HALO, [(0.82, 0.0), (SCLERA_VISIBLE_EDGE / SCLERA_HALO, 0.22),
                                              (0.875, 0.28), (0.89, 0.19), (0.91, 0.11),
                                              (0.94, 0.07), (0.96, 0.035), (1.0, 0.0)])
        tmp = tmp * (1 - sh[..., None]) + 255.0 * sh[..., None]
        cst = self._aa(np.abs(ds - SCLERA) - SCLERA_CONTACT / 2) * 0.16
        tmp = tmp * (1 - cst[..., None]) + 255.0 * cst[..., None]
        for key, rr, col in (("l3", L3_R, LAYER3), ("l2", L2_W, SCLERA_COL), ("l2", L2_I, IRIS)):
            aa = self._aa(r_all / sc[key] - rr)
            tmp = tmp * (1 - aa[..., None]) + hx(col) * aa[..., None]
        d1 = r_all / sc["l1"]
        aa = self._aa(np.abs(d1 - L1_R) - 0.05)
        tmp = tmp * (1 - aa[..., None]) + hx(HILIGHT) * aa[..., None]
        aa = self._aa(d1 - PUPIL)
        tmp = tmp * (1 - aa[..., None]) + hx(PUPIL_COL) * aa[..., None]
        # 高光球（随 layer-two 缩放）
        hxc = 80 + (HL_C[0] - 80) * sc["l2"]
        hyc = 80 + (HL_C[1] - 80) * sc["l2"]
        dhl = np.sqrt((x - hxc) ** 2 + (y - hyc) ** 2)
        hh = _interp_stops(dhl / (HL_HALO * sc["l2"]),
                           [(0.53, 0.0), (0.56, 0.16), (HL_R / HL_HALO, 0.43), (0.72, 0.19),
                            (0.77, 0.12), (0.83, 0.055), (0.89, 0.02), (0.96, 0.004), (1.0, 0.0)])
        tmp = tmp * (1 - hh[..., None]) + hx(HILIGHT) * hh[..., None]
        hg = self._aa(dhl - HL_R * sc["l2"])
        tmp = tmp * (1 - hg[..., None]) + hx(HILIGHT) * hg[..., None]

        cm = self.lid_mask(open_t, breathe)
        ea = a1 * cm
        img = img * (1 - ea[..., None]) + tmp * ea[..., None]

        # ---------- 5. 扫描线 ----------
        # ★ 必须按"辉光覆盖度"遮罩：否则整块画布（含四角全透明区）都被铺上细横纹，
        #   在分层窗口里就变成"一个带横纹的方框"（主人实测反馈过）。
        #   原素材里扫描线也只出现在辉光区内（远场黑电平实测 0.00%）。
        _scan_k = 1.0 - min(1.0, float(work_k))    # 工作态扫描线一并消失（辉光都没了）
        sl = ((y % 4.0) < 1.0).astype(np.float64) * 0.055 * 0.42 * glow_cover(x, y) * _scan_k
        img = img * (1 - sl[..., None]) + hx(SCAN_COL) * sl[..., None]

        # ---------- 6. 眼睛闪烁（白色圆层，半径 outerVisibleEdge）----------
        if flicker_op > 1e-5:
            fl = self._aa(np.sqrt((x - 80) ** 2 + (y - 80) ** 2) - OUTER_VISIBLE_EDGE) * flicker_op
            img = img * (1 - fl[..., None]) + 255.0 * fl[..., None]

        # ---------- 下采样 ----------
        n2 = n // self.ss
        return np.clip(img.reshape(n2, self.ss, n2, self.ss, 3).mean(axis=(1, 3)), 0, 255).astype(np.uint8)
