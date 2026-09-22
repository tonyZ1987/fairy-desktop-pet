# -*- coding: utf-8 -*-
"""工作态「0/1 数字流」——铺在辉光所在的那一圈，**替代**辉光。

★ 设计铁律（2026-09-21 晚六 按主人反馈改定；22:4x 追加第 ④ 条）：

  ── ① 运动：只从主体边缘向外扩散，**不跟着转** ──
    · 角位置**静止**（不随主体现有的绕圈一起转）；
    · 径向：从 1.00R 向外走到该方向的"外缘"，到顶回起点重走；
      每个数字在 15 s 内走 **3~4 趟**（每趟 5.0 / 3.75 s = 慢档），整数趟 ⇒ 闭合。

  ── ② 形态：单个数字要**像辉光**，不是像图标 ──
    · **亮度包络直接照抄辉光**：径向用 `GLOW_PROF`（单调衰减）、
      角向用实测的**厚薄图案** `thick_at(θ)`（外加 ±30% 呼吸形变，整数频率 ⇒ 闭合）；
      于是"厚的地方更亮更远、薄的地方更淡更近"，外缘也是**不规则**的 ——
      这正是"辉光变成了 0 和 1 的形态"。
    · 字形做**高斯柔化**（0.85 unit）+ **半透明**（峰值不透明度 ≈ 0.90）⇒ 与背景自然融合。

  ── ③ 无缝：所有自由度都取整数频率/整数趟 ──
    周期 15 s = 睫毛一整圈 = 10 × 呼吸(1.5s) = 2 × 辉光周期(7.5s)。

  ── ④ ★ 2026-09-21 22:4x：颜色与密度（主人三条反馈）──
    · **颜色 = 辉光自己的颜色**（原来写死 #c9f8ff，是"近乎白的青"，肉眼就是另一个颜色）；
      本模块按**半径**取 `M.glow_color_at(rho)`（5 档）⇒ 内侧亮青蓝、外侧深蓝。
    · **密度 −40%**（900 → 540）：太密就不像"光变成的字"，而像"贴了一层字符"。
    · 数字**不是**工作态的全部 —— 它上面/下面还有三层（见 dsh_mascot ④ 区块说明）：
      L0 底光（数字背后的光源）、L1 淡雾（辉光残影）、L2 摩尔纹（干涉细纹）。

性能（★ 实测调优过，别退回去）：
  · 逐数字循环里**只用 Python 标量 + 列表查表**：先 `.tolist()`，再用
    `lut[值][字号][颜色档][透明度档]` 直接索引 —— 比"numpy 标量索引 + 字典元组键"快 3~4 倍。
  · 工作态（k≈1）调用方合成四层，数字层仍是最后一道 `lighter`。
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------- 常量化参数 ----------------
T_MS = 15000.0                 # 周期（ms）
ROTATE = False                 # ★ 数字**不跟着转**（主人：只从主体边缘向外扩散）
TRAV_CHOICES = (3, 4)          # 15 s 内走几趟（3 趟=5.0 s、4 趟=3.75 s ⇒ 慢档）
RHO_IN, RHO_OUT = 0.97, 1.50   # 径向范围（0.97 起、1.50 止 —— 与辉光的可见范围对齐，回到基准）
N_DIGITS = 540                 # ★ 数量（主人 2026-09-21 22:4x："数字太密，减少 40%"）
                               #   900 → 540。密度是覆盖率的唯一直接杠杆；降低后由
                               #   L0 底光 + L1 淡雾补上"能量感" ⇒ 更透气但不空。
SIZES_U = (5.0, 6.3, 7.8)      # 三档字号（宠物 unit）
BLUR_U = 0.68                  # ★ 字形高斯柔化半径（unit）：软，但仍看得出是 0/1
                               #   （0.80 太糊 ⇒ 放大看只剩"圆点"，看不出是 0/1；0.62 又像实心贴纸）
FADE_IN = 0.05                 # 起步淡入（很短：让数字紧贴主体边缘就亮起来）
OPACITY_PEAK = 0.86            # ★ 峰值不透明度：要与**辉光同亮度量级**
                               #   辉光的角向峰值 = GLOW_PEAK = 180 ⇒ 0.86×255×(base≈0.9) ≈ 193，
                               #   与辉光同量级且是全场最亮的一层（层次：数字 > 摩尔纹 > 底光 > 雾）。
FADE_EPOCH_MODE = 1            # 不透明度更随"行程尾段"衰减（由径向剖面统一负责，这里只做端点收口）
PULSE_PER_LOOP = 10            # 亮度脉动次数/周期（= 1.5 s 一次，与呼吸同节奏）
THICK_BREATHE = 10             # 厚薄幅度的呼吸次数/周期（15 s 内 10 次 ⇒ 整数 ⇒ 闭合）
OPS = 8                        # 不透明度量化档数
DIG_AMP_POS = 1.00             # ★ 行程缩放的**厚端**幅度（tz > 0 的方向）
DIG_AMP_NEG = 0.32             # ★ 行程缩放的**薄端**幅度（tz < 0 的方向）
                               #   为什么非对称：可见外缘的有效区间受**画布**夹住 ——
                               #   最远只能到 ≈1.58R（画布半宽 1.66R 减半个字），再往外就是硬墙。
                               #   所以要凑出 30% 的起伏，只能"厚端顶到上限、薄端让一点"：
                               #     厚端 0.72 → 可见外缘 ≈1.58R（顶到画布允许的极限）
                               #     薄端 0.43 → 可见外缘 ≈1.18R
                               #   ⇒ 峰峰 ≈30%（与辉光的 30.2% 一致）
                               #   ⇒ 位置 >1.44R（辉光可见边界）的方向约占 30%
                               #     = 主人要的"**部分**数字的厚度超出辉光厚度，估计 30% 左右"。
                               #   标定史：0.11 → 18.3%（"变化不太大"）；
                               #          对称 0.45 + 亮度缩放 → 38.2%（"又有些过于夸张了"）；
                               #          0.42 + 独立逸散 → 超界方向 43~52%（"部分"变成了一半）。
DIG_RHO_CAP = 1.58             # 位置硬上限（rho）：见下面 np.minimum 处的说明
DIG_FADE_R0, DIG_FADE_R1 = 1.56, 1.70   # 位置收口区间（只做兜底，防硬上限处堆成一道"墙"）。
                               #   ★ 起点必须**晚于**目标逸散半径（≈1.55R），否则逸散刚出门就
                               #     被淡出吃掉（踩过：0/72 个方向能突破）。
#   ★ 亮度曲线用辉光那条（`GLOW_PROF`，末端在行程终点归零）——
#     也**不要**为了"逸散"另做一条更长的剖面：上一版做过，副作用是**字号被一起放大**
#     （`si_dyn ∝ sqrt(prof)` 跟着同一条剖面走），主人："这次数字怎么看起来大了不少，
#     上一版数字的大小我觉得挺好。" 现在字号另有独立的参考曲线（见函数内 `sz_ref`）。

# ---------------- ★ 配色：**不再是写死的 #c9f8ff，而是辉光自己的颜色** ----------------
# 主人 2026-09-21 22:4x："数字颜色要跟辉光一样，现在颜色不同。"
# 根因：辉光实测是**蓝**的（rho=1.10 → 比例 (0.260,0.643,1.0)），
#       而 #c9f8ff → (0.788,0.973,1.0) 近乎白的青 ⇒ 肉眼就是另一个颜色。
# 做法：按**半径**取辉光的角向平均色（5 档），内侧偏白、外侧偏深蓝 ——
#       这同时给了"层次"：同一批数字在往外走的过程中自己会变深蓝，像辉光的衰减。
COL_RHOS = (1.06, 1.18, 1.30, 1.42, 1.56)   # 5 个颜色档的采样半径（rho）
COL_STEP = 0.12                # 档间距（= COL_RHOS 的等差）
COL_R0 = 1.06                  # 档 0 的中心半径
SEED = 20260921

_D = None
_ATLAS = {}


# ---------------- 每个数字的固定参数（确定性） ----------------
def digit_params():
    global _D
    if _D is not None:
        return _D
    rng = np.random.RandomState(SEED)
    n = N_DIGITS
    _D = dict(
        th0=rng.uniform(0, 360.0, n).tolist(),            # 基准角（度）——**静止**
        trav=rng.choice(TRAV_CHOICES, n).tolist(),        # 15 s 走几趟
        ph_r=rng.uniform(0, 1.0, n).tolist(),             # 径向起点相位（打散 ⇒ 环上均匀）
        size=rng.uniform(0.0, 1.0, n).tolist(),           # 用于**动态**字号档（见下：内侧大、外侧小）
        base=rng.uniform(0.82, 1.0, n).tolist(),          # 基础亮度（再乘到 OPACITY_PEAK 上）
        f_bl=rng.randint(5, 12, n).tolist(),              # 数值跳变频率（整数 ⇒ 闭合）
        ph_bl=rng.uniform(0, 2 * np.pi, n).tolist(),      # 跳变相位
        ph_pu=rng.uniform(0, 2 * np.pi, n).tolist(),      # 脉动相位（略错开，别整圈一起亮）
        n=n)
    return _D


def _font(px):
    for name in ("consola.ttf", "consolab.ttf", "msyh.ttc", "simhei.ttf"):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/" + name, int(px))
        except Exception:
            continue
    return ImageFont.load_default()


def _atlas_for(ppu, bgr=False):
    # ★ bgr：实时渲染器的画布是 **BGR 通道序**（见 fairy_layers 头部说明），参考渲染器是 RGB。
    #   传错会让数字红蓝对调（实测踩过：数字看起来发黄）。
    """返回 (cells, lut)。lut[值][字号档][颜色档][透明度档-1] = (预乘 RGB 小图, 遮罩)。

    ★ 遮罩先做**高斯柔化**再归一到峰值 255 —— 这样"不透明度档"说的就是峰值，
    而边缘是软的（像辉光），不是图标那种硬边。
    ★ **颜色档**（2026-09-21 22:4x 新增）：每个数字按自己当时的半径取辉光的角向平均色，
    所以同一批数字从内往外走时会由亮青蓝渐变到深蓝 —— 与辉光的衰减完全同源。
    """
    key = (round(ppu, 4), bool(bgr))
    hit = _ATLAS.get(key)
    if hit is not None:
        return hit
    import dsh_mascot as M                       # 延迟导入（避免循环 import）
    cols = [tuple(int(round(float(c) * 255.0)) for c in M.glow_color_at(r)) for r in COL_RHOS]
    cells = {}
    lut = [[[[] for _ in range(len(COL_RHOS))] for _ in range(len(SIZES_U))]
           for _ in range(2)]
    blur_px = max(0.6, float(BLUR_U) * ppu)
    for si, su in enumerate(SIZES_U):
        px = max(6, int(round(su * ppu)))
        fnt = _font(px * 1.32)
        tw, th_ = 0, 0
        for ch in ("0", "1"):
            b = fnt.getbbox(ch)
            tw = max(tw, b[2] - b[0])
            th_ = max(th_, b[3] - b[1])
        pad = int(np.ceil(blur_px * 3))            # 给模糊留边
        gw, gh = tw + 2 + 2 * pad, th_ + 2 + 2 * pad
        masks = []
        for ch in ("0", "1"):
            g = Image.new("L", (gw, gh), 0)
            b = fnt.getbbox(ch)
            ImageDraw.Draw(g).text((pad + (gw - 2 * pad - (b[2] - b[0])) // 2 - b[0],
                                    pad + (gh - 2 * pad - (b[3] - b[1])) // 2 - b[1]), ch,
                                   font=fnt, fill=255)
            g = g.filter(ImageFilter.GaussianBlur(blur_px))
            a = np.asarray(g, np.float64)
            m = float(a.max())
            if m > 1:
                g = Image.fromarray(np.clip(a * (255.0 / m) + 0.5, 0, 255).astype(np.uint8))
            masks.append(g)
        for v in (0, 1):
            for ci in range(len(COL_RHOS)):
                _c = cols[ci]
                if bgr:                            # ★ 通道序（BGR/RGB）
                    _c = (_c[2], _c[1], _c[0])
                slot = lut[v][si][ci]
                for oi in range(OPS):
                    op = (oi + 0.5) / OPS
                    col = tuple(int(round(x * op)) for x in _c)
                    slot.append((Image.new("RGB", (gw, gh), col), masks[v]))
        cells[si] = (gw, gh)
    _ATLAS.clear()                 # 只留最近一档 ppu（内存友好）
    _ATLAS[key] = (cells, lut)
    return cells, lut


def digit_layer(w, cx, cy, r_disc_px, ppu, phase_ms, k=1.0, bgr=False):
    """生成数字流图层（RGB，"黑底观感"= 预乘）。k = 出现权重 0..1。

    亮度包络 = 辉光的径向剖面 × 辉光的角向厚薄 ⇒ "辉光变成了 0/1 的形态"。
    返回 (layer_img, n_drawn)；k≈0 时 layer 为 None。
    """
    if k <= 0.004:
        return None, 0
    import dsh_mascot as M                       # 延迟导入（避免循环 import）
    cells, lut = _atlas_for(ppu, bgr)
    d = digit_params()
    p = (float(phase_ms) / T_MS) % 1.0

    th = np.radians(np.asarray(d["th0"]))                      # ★ 静止，不旋转
    trav = np.asarray(d["trav"], dtype=np.float64)
    f = (p * trav + np.asarray(d["ph_r"])) % 1.0               # 每趟的进度 0→1
    rho0 = RHO_IN + (RHO_OUT - RHO_IN) * f

    # ---- ★★ 2026-09-22：角向厚薄改成缩放「行程长度」----
    #   主人："数字显现的范围（跟辉光的厚薄同理）变化不太大，这块最好再调一下。"
    #   实测：数字可见外缘半径峰峰 **18.3%** vs 辉光 **30.2%**。根因两条 ——
    #     ① 幅度借用了辉光的 `GLOW_THICK_AMP`(0.11)；而数字的行程跨度只有 0.53R，
    #        同样系数给出的绝对偏移天然比辉光小；
    #     ② **更致命**：亮度包络是按**缩放后的绝对半径** `rho` 查表的 ⇒
    #        位置被推远的那一段，亮度又被按"远处"压暗 ⇒ 等于白推（起伏被抵消一半）。
    #   改法：用数字自己的幅度 `DIG_THICK_AMP` 缩放**行程长度**（厚处走得更远、薄处走不到），
    #        亮度仍按**未缩放的行程半径** `rho0` 查表 ⇒
    #        各方向的**外缘半径**真正不同，且末端都平滑归零（不会"啪"地消失）。
    #        副作用（正是要的）：厚处数字铺得更开、径向更快；薄处挤得紧、更慢。
    tz = np.asarray(M.thick_at(th), dtype=np.float64)
    _bre = 1.0 + M.GLOW_THICK_BREATHE * np.sin(2 * np.pi * THICK_BREATHE * p + 0.9)
    # ★ 非对称行程缩放：厚端顶到画布允许的极限、薄端让一点（见 DIG_AMP_POS/NEG 的说明）
    scale = (1.0 + (DIG_AMP_NEG * _bre) * np.minimum(tz, 0.0)
             + (DIG_AMP_POS * _bre) * np.maximum(tz, 0.0))
    rho = RHO_IN + (rho0 - RHO_IN) * scale
    #   ★ 位置硬上限：厚端行程可达 0.53R×1.72 ⇒ 外缘 1.88R，远超画布半宽 1.66R。
    #     截到 1.58R（108.4 unit + 半个字 ≈ 112 < 114）⇒ 四边永远干净。
    np.minimum(rho, DIG_RHO_CAP, out=rho)

    # ---- 亮度包络：辉光的径向剖面（按**行程半径** rho0 查 ⇒ 末端严格归零）----
    #   ★ 用"行程半径"而不是"缩放后的位置"是关键：位置被推远的那一段，亮度若按远处算
    #     就会被压暗 ⇒ 等于白推（踩过，可见外缘起伏只剩 7.7%）。
    prof = M._interp_stops(rho0, M.GLOW_PROF)
    prof = prof * (1.0 - M.smoothstep((rho - DIG_FADE_R0) / (DIG_FADE_R1 - DIG_FADE_R0)))
    tone = 1.0 + M.GLOW_THICK_TONE * tz                         # 厚处更亮（与辉光同一套）
    env = np.clip(f / FADE_IN, 0, 1)                            # 起步淡入
    env *= (0.84 + 0.16 * np.sin(2 * np.pi * PULSE_PER_LOOP * p + np.asarray(d["ph_pu"])))
    op = np.clip(np.asarray(d["base"]) * env * prof * tone * OPACITY_PEAK * float(k), 0, 0.999)
    val = (np.sin(2 * np.pi * np.asarray(d["f_bl"]) * p + np.asarray(d["ph_bl"])) > 0).astype(np.int64)

    # ★ 字号**跟亮度剖面挂钩**：内侧大、外侧小 ⇒ 覆盖率也随剖面衰减，
    #   观感才像"辉光云"而不是"一圈同样大的字符"（实测：固定字号时盘缘处只有辉光的 43%）。
    rnd_s = np.asarray(d["size"], dtype=np.float64)
    #   ★★ 字号与亮度**解耦**：字号用**辉光**的剖面算，亮度用 `DIG_PROF`（延伸更远）。
    #     两者共用一条曲线的话，为了"逸散"把亮度曲线拉长，字号会被连带放大
    #     （主人 2026-09-22："这次数字怎么看起来大了不少，上一版的大小我觉得挺好。"）。
    #   ★ 用 sqrt(参考)：尺寸衰减得比亮度慢 ⇒ 外圈仍有覆盖（用剖面本身会"尺寸+亮度"双重衰减，
    #     实测 1.20R 处只剩辉光的 19%）。
    sz_ref = M._interp_stops(rho0, M.GLOW_PROF)
    si_dyn = np.clip((2.9 * np.sqrt(np.clip(sz_ref, 0, 1)) + (rnd_s - 0.5) * 1.3).astype(np.int64),
                     0, len(SIZES_U) - 1)

    rr = rho * r_disc_px
    # ★ 颜色档：每个数字按**自己当时的半径**取辉光色（内侧亮青蓝 → 外侧深蓝）
    ci = np.clip(((rho - COL_R0) / COL_STEP + 0.5).astype(np.int64),
                 0, len(COL_RHOS) - 1).tolist()
    # ★ 一次性转 Python 标量列表：循环里不再碰 numpy（本函数的主要提速点）
    xs = (cx + rr * np.cos(th)).tolist()
    ys = (cy + rr * np.sin(th)).tolist()
    oi = np.clip((op * OPS).astype(np.int64), 0, OPS - 1).tolist()
    vv = val.tolist()
    sz = si_dyn.tolist()
    lay = Image.new("RGB", (w, w), (0, 0, 0))
    drew = 0
    for i in range(d["n"]):
        o = oi[i]
        if o == 0:
            continue
        si = sz[i]
        gw, gh = cells[si]
        sp, mk = lut[vv[i]][si][ci[i]][o - 1]
        x = int(xs[i] + 0.5) - gw // 2
        y = int(ys[i] + 0.5) - gh // 2
        if x < -gw or y < -gh or x > w or y > w:
            continue
        lay.paste(sp, (x, y), mk)
        drew += 1
    return lay, drew


def check_closure():
    """自检：相位 0 与相位 1（一整个周期）必须逐位一致。"""
    w, ppu = 370, 260 / 160.0
    a, _ = digit_layer(w, (w - 1) / 2.0, (w - 1) / 2.0, 68.6 * ppu, ppu, 0.0)
    b, _ = digit_layer(w, (w - 1) / 2.0, (w - 1) / 2.0, 68.6 * ppu, ppu, T_MS)
    da = np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16))
    return int(da.max()), float(da.mean())
