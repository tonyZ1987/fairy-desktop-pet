# -*- coding: utf-8 -*-
"""fairy v2 · 实时渲染器（图层预栅格化 + 每帧只做缩放/合成/遮罩插值）

目的：把 `dsh_mascot.DSHSvg.render()` 的 **0.45 s/帧** 压到 **~4 ms/帧**，供 60fps 常驻程序用。

三条关键洞察（决定了为什么能做到）：

1. **静态层**（外发光 + 外盘渐变 + 白描边）永不变化 ⇒ 预烘焙成一张「黑底观感」的 RGB，
   运行时一次 `paste`（无遮罩 = memcpy，实测 0.01 ms）铺上。
2. **眼睛各层只是同心圆的半径缩放**，而缩放系数只随呼吸相位变化
   ⇒ 预烘焙 **90 个相位帧**（1.5 s × 60 fps），运行时按下标取用，零重算。
3. **眼睑遮罩可以解析求值**：官方 clipPath 是二次贝塞尔，在 `mid`（弧中点）这个自由度上
   可**拆成两个与 mid 无关的基函数**：
       cy(t) = A·g(t) + B·h(t)，  g(t)=(1-t)²+t²，  h(t)=2t(1-t)
   于是每帧只算 `ycurve = A·G + B·H`（G/H 预计算成一维）—— 不再逐帧跑 3001 点插值。

★ **通道序是 BGR，不是 RGB**（很重要，别改回去）：
   最终 alpha = max(R,G,B)/255，而 `UpdateLayeredWindow` 要**预乘 BGRA** —— 预乘后正好等于
   「黑底观感」本身。所以整条链只在黑底上合成，最后补一个 alpha 通道，一次除法都不用做。
   既然如此，就让画布直接以 BGR 存放，「转 BGRA」退化成一次连续内存拷贝 + 一次 max，
   实测从 1.72 ms 降到 ~0.7 ms。**自绘 UI 叠加时颜色也要按 BGR 给**（见 `bgr()`）。

精度：与参考实现逐像素比对见 `fairy_bench.py` —— **MAE 0.58 / 255（0.23%）**，
逐像素差异基本不超过 1~2 个色阶，肉眼与参考实现完全一致。
速度：**3.13 ms/帧**（260 px / 60fps 预算 16.7 ms 的 19%）。
"""
import os
import numpy as np
from PIL import Image, ImageChops
import dsh_mascot as M
import fairy_digits as FD        # 工作态「0/1 数字流」图层

PHASES = 90                 # 呼吸相位缓存数（1.5 s @ 60fps）
EYE_HALF_U = 49.5           # 眼睛裁剪半径（user unit）—— 眼白外缘 48.4，留一点余量
LASH_HALF_U = 67.5          # 睫毛裁剪半径 —— 被 clipPath 圆 r=67 裁住
FEATHER = 1.6               # 眼睑边缘羽化（unit），与参考实现一致
CYCLE_MS = M.PULSE_CYCLE_LOOP_MS
GLOW_CYCLE_MS = M.GLOW_CYCLE_MS      # 辉光的相位周期 = 7.5 s（5 个呼吸周期）
GLOW_PHASES = 180           # ★ 辉光相位表：180 帧 / 7.5 s = 24 fps。
                            #   为什么不是 60：辉光要做"匀速绕圈"，需要足够密的采样 ——
                            #   60 帧时亮环每帧移 4 px（看得出台阶），180 帧只移 2 px。
                            #   内存 ≈ 180 × N² × 3 字节（260 px 时 74 MB）。
                            #   每帧查表 ⇒ 运行时零计算。60 档 = 25ms/档，亮环每档移 0.8px。
                            #   内存 ≈ 60 × N² × 3 字节（260px 时 23MB）。想省内存就调小。
DISC_HALF_U = 71.0          # 外盘+白描边裁剪块的半径（unit）
BAKE_VERSION = 19            # ★ 烘焙格式版本：改过烘焙内容就 +1，否则会读到旧缓存
#   v19（2026-09-22）：`dsh_mascot.glow_cover` 由 `clip(v/0.02)` 改成 `smoothstep(v/0.10)`
#   ⇒ 扫描线（scan_a）是烘焙产物，必须重烘。
                             #   v18（2026-09-21 晚）= 工作态四层：底光 + 摩尔纹相位表 + 淡雾 mask
                            #   v17 = 烟团定稿为"② 差速剪切+拉丝"（剪切 2.60 / 拉丝 0.55）
                            #   v4 = 辉光合成内圈(去实心环) + 烟团湍流 + 亮底 alpha 增益
LASH_SS = 1                 # 睫毛遮罩超采样倍率。实测：1 → 3.13 ms/帧、MAE 0.582；
                            #   2 → 5.83 ms/帧、MAE 0.511。差异仅 0.07/255（旋转边缘的抗锯齿相位），
                            #   肉眼不可辨 —— 故取 1，省下 46% 的帧时间。


def _ds(a, ss):
    """超采样下采样（与参考实现同一算法，保证一致性）"""
    if ss == 1:
        return a
    n2 = a.shape[0] // ss
    if a.ndim == 2:
        return a.reshape(n2, ss, n2, ss).mean(axis=(1, 3))
    return a.reshape(n2, ss, n2, ss, a.shape[2]).mean(axis=(1, 3))


# ---- 烘焙进度：各段占总时间的比例（2026-09-22 实测 @200px，总 51.6 s）----
#   ★ 只用于「把分段进度折成总进度」，不需要精确（进度条平滑即可）。
#   ★★ 辉光占了 78.8% ⇒ 它的 180 次相位循环**必须**逐次报进度，否则进度条会跳。
BAKE_SHARE = {"glow": 0.788, "moire": 0.050, "misc0": 0.900, "eye": 0.050}
BAKE_LABEL = {u"glow": u"辉光相位表", u"moire": u"工作态摩尔纹", u"misc": u"外盘/扫描线/睫毛",
              u"eye": u"眼睛呼吸相位", u"save": u"写入缓存"}


def _u8(a):
    """0..1 的遮罩 → 0..255"""
    return np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _u8rgb(a):
    """0..255 的 RGB → uint8（★ 别拿 _u8 干这个，会把一切都截成 255）"""
    return np.clip(a + 0.5, 0, 255).astype(np.uint8)


def _f32(a):
    return np.ascontiguousarray(a, dtype=np.float32)


def bgr(hex_or_rgb):
    """颜色 → BGR 元组。所有自绘 UI 的颜色都要过这一层。"""
    if isinstance(hex_or_rgb, str):
        c = M.hx(hex_or_rgb)
    else:
        c = hex_or_rgb
    return (int(c[2]), int(c[1]), int(c[0]))


def _solid(w, rgb):
    return Image.new("RGB", (w, w), bgr(rgb))


def grid_n(pet_px, ss=2):
    u"""超采样网格边长（含画布外扩）—— **不构造 FastMascot 也能算**。

    ★★ 2026-09-23 抽出来（`FastMascot._grid_n` 现在直接调它）：主程序要在**烘焙之前**
      就知道画布有多大 —— 倒计时窗 `Splash` 得按它定尺寸与居中位置，
      而那时 `FairyPet` 还没建（烘焙就发生在它构造里）。
      ⇒ 只能做成纯函数，否则两处各写一份公式、迟早漂移。
    """
    ppu = float(pet_px) / 160.0
    m = M.canvas_margin_px(ppu)
    n = int(round(160 * ppu * ss)) + 2 * m * ss
    return ((n + ss - 1) // ss) * ss


def canvas_px(pet_px, ss=2):
    u"""画布边长（= 窗口边长，像素）。本体 `pet_px` 之外每边多留一圈辉光余量。

    实测：200→284 ｜ 260→370 ｜ 320→456（三者都会被 `SIZES` 用到）。
    """
    return grid_n(pet_px, ss) // ss


class FastMascot:
    """size = 显示边长（px）。第一次构造会烘焙并写盘缓存（约 7 s），之后秒开。"""

    def __init__(self, size=260, ss=2, halo_gain=1.0, cache_dir=None, verbose=False,
                 tag=None, bake_progress=None):
        """size = **宠物本体**的像素尺寸（用户选的显示尺寸）。
        画布比它大：每边多留 CANVAS_MARGIN_U × ppu 像素，给辉光留出发散空间 ⇒ self.N 是画布边长。"""
        self.pet_px = int(size)
        self.ppu = self.pet_px / 160.0
        self.ss = int(ss)
        # ★ 画布（= 窗口）边长必须与"超采样网格下采样后"的尺寸**逐像素一致**，
        #   否则和参考渲染器差 1 px（踩过：四舍五入方向不同 ⇒ 423 vs 422）。
        self.N = self._grid_n(self.ss) // self.ss
        self.margin_px = self._margin_px()
        self.halo_gain = float(halo_gain)
        self.cache_dir = cache_dir
        # ★ 缓存标签：变体（A/B/C）各自独立成文件，互不覆盖。
        #   tag=None ⇒ 取 dsh_mascot.GLOW_TAG；为空时**文件名与旧版完全一致**（生产缓存不受影响）。
        self.tag = M.GLOW_TAG if tag is None else str(tag)
        self.tag = "".join(c for c in self.tag if c.isalnum() or c in "_-")
        self._work_cut_a = None          # 工作态外缘收缩包络（载入后算，见 _apply_work_cut）
        self._work_cut_ms = 0.0
        import time as _t
        t0 = _t.perf_counter()
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            _suf = ("_" + self.tag) if self.tag else ""
            p = os.path.join(cache_dir, "layers_v%d_%d_%d_%d_%d%s.npz"
                             % (BAKE_VERSION, self.N, self.ss, LASH_SS,
                                round(self.halo_gain * 100), _suf))
            if os.path.exists(p):
                try:
                    self._load(p)
                    self._work_cut_ms = self._apply_work_cut()   # ★ 见方法注释：不用重烘
                    self._make_pil()
                    if verbose:
                        print("[fast] 缓存载入 %.2fs" % (_t.perf_counter() - t0))
                    return
                except Exception as e:
                    print("[fast] 缓存不可用，重烘：%r" % (e,))
            self._bake(bake_progress)
            self._save(p)
        else:
            self._bake(bake_progress)
        self._work_cut_ms = self._apply_work_cut()
        self._make_pil()
        if verbose:
            print("[fast] 烘焙完成 %.1fs  size=%d" % (_t.perf_counter() - t0, self.N))

    # ------------------------------------------------------------ 工作态外缘收缩
    def _apply_work_cut(self):
        """把 `M.work_cut(rho)` 乘进**已烘焙**的工作态三层数组（载入/烘焙之后、`_make_pil` 之前）。

        ★★ 为什么可以放在这里、**不必重烘**（也就不必动 `BAKE_VERSION`）：
           包络是对"最终烘焙结果"做**逐像素径向乘**；而 alpha 的亮度查表（`_alpha_lut`）
           同样是**逐像素**函数 ⇒ "先乘色、再查表" 与 "把包络烘进 `work_bg_layer` /
           `work_moire_layer` / `mist_mask` 再查表" 得到的是同一条曲线（只差一个单调映射）。
           ⇒ **改收缩强度只要重启**：主人可以当场试几档，不用等 5 分钟重烘。
           （注意：**别**把包络也烘进 `dsh_mascot` 的层函数，那会乘两遍。）
        """
        a = self._work_cut_mask()
        if a is None:
            return 0.0
        import time as _t
        t0 = _t.perf_counter()
        self.work_bg_rgb = np.clip(self.work_bg_rgb.astype(np.float32) * a[..., None],
                                   0.0, 255.0).astype(np.uint8)
        self.moire_phases = np.clip(self.moire_phases.astype(np.float32) * a[None, :, :, None],
                                    0.0, 255.0).astype(np.uint8)
        self.mist_mask = np.clip(self.mist_mask.astype(np.float32) * a, 0.0, 255.0).astype(np.uint8)
        self._work_cut_a = a
        return (_t.perf_counter() - t0) * 1000.0

    def _work_cut_mask(self):
        """工作态外缘收缩包络（N×N float32），与烘焙产物**逐像素对齐**；强度全 1 时返回 None。"""
        if not M.work_cut_active():
            return None
        c = (self.N - 1) / 2.0
        yy, xx = np.mgrid[0:self.N, 0:self.N]
        rho = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / (M.GLOW_R_DISC * self.ppu)
        m = M.work_cut(rho).astype(np.float32)
        return None if float(m.min()) >= 0.999 else m

    # ------------------------------------------------------------------ 裁剪
    def _margin_px(self):
        """画布每边多留的最终像素数（取整 ⇒ 与超采样网格对齐）"""
        return M.canvas_margin_px(self.ppu)

    def _grid_n(self, ss):
        # ★ 含外扩：画布在 160 unit 视图之外每边多留 margin 像素，给辉光留发散空间
        return grid_n(self.pet_px, ss)

    def _grid_center(self, ss):
        """宠物中心在 ss 网格里的坐标。★ 必须与网格严格一致，否则裁剪框整体错 1px。"""
        return int(round(80 * self.ppu * ss)) + self._margin_px() * ss

    def _crop_box(self, half_u, ss):
        """返回 (起像素, 宽高像素)，已对齐 ss 网格、且不越界。"""
        n_full = self._grid_n(ss)
        half_px = half_u * self.ppu * ss
        # ★ 宽度取 2*ss 的整数倍，起点 = 中心 − w/2 ⇒ 裁剪框关于宠物中心**严格对称**。
        #   否则（w 为奇数倍 ss 时）中心会偏半个像素，眼睛/睫毛整体错 0.5 px。
        w = int(np.ceil(2 * half_px / (2 * ss))) * (2 * ss)
        i0 = self._grid_center(ss) - w // 2
        i0 -= i0 % ss
        i0 = max(0, min(i0, n_full - w))
        return i0, w

    # ------------------------------------------------------------------ 烘焙
    def _bake(self, on_progress=None):
        """烘焙全部层。`on_progress(fraction, label)` —— fraction 是 0~1 的**总**进度。

        ★ 默认 None ⇒ 与改造前逐位相同（只是多几次函数调用）。
        ★ 主程序可在这里面刷启动动画：**每调用一次 = 一个可插帧的点**。
        """
        r = M.DSHSvg(px_per_unit=self.ppu, ss=self.ss)
        x, y = r.x, r.y
        n = r.n
        rr = np.sqrt((x - 80) ** 2 + (y - 80) ** 2)
        g = self.halo_gain

        # ---- ① 辉光：GLOW_PHASES 个相位（动态 = 向外扩散的亮环 + 随呼吸起伏）----
        # ★ 辉光只做乘法与半径整形 ⇒ 色相不变。每帧"查表 + 整幅 paste"，运行时零计算。
        K = GLOW_PHASES
        gp = np.zeros((K, n // self.ss, n // self.ss, 3), dtype=np.uint8)
        for k in range(K):
            gl = M.glow_layer(x, y, phase=k / K) * g
            gp[k] = _u8rgb(_ds(gl, self.ss))[..., ::-1]        # → BGR
            if on_progress is not None:                        # ★ 逐相位报（占全程 79%）
                on_progress(BAKE_SHARE["glow"] * (k + 1) / float(K), BAKE_LABEL["glow"])
        self.glow_phases = gp

        # ---- ①c ★ 工作态三层（2026-09-21 晚，主人："加摩尔纹 + 淡色的雾 + 数字背后的光源"）----
        #   ★ 主人要的"一致性"：这三层**全部从辉光派生**（同一套配色 / 径向剖面 / 角向厚薄）
        #     —— "主体卸下伪装，辉光变成了 01 的二进制符号"，所以过渡时观感不能断层。
        #   ★ 摩尔纹在超采样网格上取样：细纹周期 ≈ 4.7 px × ss ⇒ 下采样 2×2 平均仍完整保留。
        #   ★ 底光是**静态**的（光源恒定）：运行时一次不带遮罩的 paste 就铺完（≈0.01 ms）。
        wb = M.work_bg_layer(x, y) * g
        self.work_bg_rgb = _u8rgb(_ds(wb, self.ss))[..., ::-1].copy()        # → BGR
        Km = M.MOIRE_PHASES
        mp = np.zeros((Km, n // self.ss, n // self.ss, 3), dtype=np.uint8)
        for k in range(Km):
            mo = M.work_moire_layer(x, y, phase=k / float(Km)) * g
            mp[k] = _u8rgb(_ds(mo, self.ss))[..., ::-1]                     # → BGR
            if on_progress is not None:
                on_progress(BAKE_SHARE["glow"] + BAKE_SHARE["moire"] * (k + 1) / float(Km),
                            BAKE_LABEL["moire"])
        self.moire_phases = mp
        #   淡雾 mask = **辉光的 alpha × WORK_MIST_K**（逐像素同源 ⇒ 辉光怎么动，雾就怎么动）
        #   注：gp[0] 已是 BGR，但 max(axis=2) 与通道序无关。
        #   ★★ 尺度陷阱（踩过）：本文件里 `_u8(a)` 期望 **0..1**（内部乘 255），
        #      而 `_u8rgb(a)` 期望 **0..255**。`work_mist_alpha` 返回的是 0..255（alpha 尺度），
        #      所以必须先 /255 —— 否则整个辉光区被 clip 成 mask=255 ⇒ 雾变成"辉光整幅贴上"，
        #      现象是"工作态亮得跟常态一样"＋参考/实时 MAE 从 1.7 飙到 8.5。
        self.mist_mask = _u8(M.work_mist_alpha(gp[0].astype(np.float64)) / 255.0)
        if on_progress is not None:
            on_progress(BAKE_SHARE["misc0"], BAKE_LABEL["misc"])

        # ---- ①b 外盘渐变 + 白描边：静态，裁成小块（半径 DISC_HALF_U）叠在辉光之上 ----
        ga = np.array([0.2 * 160, 0.0]); gb = np.array([0.8 * 160, 160.0]); v = gb - ga
        tt = np.clip(((x - ga[0]) * v[0] + (y - ga[1]) * v[1]) / (v @ v), 0, 1)
        disc_col = M._interp_stops(tt, [(0.0, M.hx(M.DISC_GRAD[0][0])),
                                        (0.54, M.hx(M.DISC_GRAD[1][0])),
                                        (1.0, M.hx(M.DISC_GRAD[2][0]))])
        d_disc = rr - M.OUTER_DISC
        a_d = r._aa(d_disc)
        a_s = r._aa(np.abs(d_disc) - M.OUTER_STROKE / 2)
        base = np.zeros((n, n, 3))
        base = base * (1 - a_d[..., None]) + disc_col * a_d[..., None]
        base = base * (1 - a_s[..., None]) + M.hx(M.STROKE_RIM) * a_s[..., None]
        di0, dw = self._crop_box(DISC_HALF_U, self.ss)
        self.disc_off = di0 // self.ss
        self.disc_w = dw // self.ss
        self.disc_rgb = _u8rgb(_ds(base[di0:di0 + dw, di0:di0 + dw], self.ss))[..., ::-1].copy()
        self.disc_a = _u8(_ds(np.clip(a_d + a_s, 0.0, 1.0)[di0:di0 + dw, di0:di0 + dw],
                              self.ss))

        # ---- ② 扫描线（静态覆盖层，叠在最上面之一）----
        # ★ 必须乘 footprint：否则四角全透明区也被铺上细横纹，
        #   在分层窗口里表现为"一个带横纹的方框"
        # ★ 必须按"辉光覆盖度"遮罩：否则四角全透明区也被铺上细横纹（表现为"带横纹的方框"）
        sl = ((y % 4.0) < 1.0).astype(np.float64) * 0.055 * 0.42 * M.glow_cover(x, y)
        self.scan_a = _u8(_ds(sl, self.ss))

        # ---- ③ 闪烁遮罩（白圆 r = outerVisibleEdge，透明度由运行时给）----
        self.flicker_a = _u8(_ds(r._aa(rr - M.OUTER_VISIBLE_EDGE), self.ss))

        # ---- ④ 睫毛/眼睑：静态圆 + 被同一 clip 裁住的旋转方（在 ss 分辨率下算，与参考一致）----
        lss = LASH_SS
        li0, lw = self._crop_box(LASH_HALF_U, lss)
        self.lash_off = li0 // lss
        self.lash_w = lw // lss
        # ★ 减掉画布外扩量：像素下标 ≠ 单位坐标（外扩后起点不在 unit 0）
        xl = (li0 + np.arange(lw) - self._margin_px() * lss) / (self.ppu * lss)
        XL, YL = np.meshgrid(xl, xl)
        self.lash_x2 = _f32(XL - 80.0)
        self.lash_y2 = _f32(YL - 80.0)
        rl = np.sqrt((XL - 80) ** 2 + (YL - 80) ** 2)
        self.lash_dclip = _f32(r._aa((rl - M.OUTER_DISC) + 1.0))
        self.lash_circ = _f32(r._aa(rl - M.CORNER_CIRCLE))

        # ---- ⑤ 眼睛：90 个呼吸相位 ----
        ei0, ew = self._crop_box(EYE_HALF_U, self.ss)
        xe = x[ei0:ei0 + ew, ei0:ei0 + ew]
        ye = y[ei0:ei0 + ew, ei0:ei0 + ew]
        rre = np.sqrt((xe - 80) ** 2 + (ye - 80) ** 2)
        self.eye_off = ei0 // self.ss
        self.eye_w = ew // self.ss
        stack = np.zeros((PHASES, self.eye_w, self.eye_w, 4), dtype=np.uint8)
        for k in range(PHASES):
            sc = M.eye_scales(k / PHASES * CYCLE_MS, CYCLE_MS)
            rgb, a1 = self._eye_layers(xe, ye, rre, sc, r)
            stack[k, ..., :3] = _u8rgb(_ds(rgb, self.ss))[..., ::-1]     # → BGR
            stack[k, ..., 3] = _u8(_ds(a1, self.ss))
            if on_progress is not None:
                on_progress(BAKE_SHARE["misc0"] + BAKE_SHARE["eye"] * (k + 1) / float(PHASES),
                            BAKE_LABEL["eye"])
        self.eye_stack = stack

        # ---- ⑥ 眼睑遮罩的基函数（G / H，与 mid 无关）----
        # ★ 必须与眼睛裁剪框**逐像素对齐**，否则遮罩会整体错位
        t = np.linspace(0, 1, 3001)
        cx = 20 * (1 - t) ** 2 + 160 * t * (1 - t) + 140 * t ** 2
        self.lid_off, self.lid_w = self.eye_off, self.eye_w
        yu = (self.lid_off + np.arange(self.lid_w) - self._margin_px()) / self.ppu   # ★ 同上去外扩
        self.lid_y = _f32(yu)[:, None]
        self.lid_G = _f32(np.interp(yu, cx, (1 - t) ** 2 + t ** 2))[None, :]
        self.lid_H = _f32(np.interp(yu, cx, 2 * t * (1 - t)))[None, :]
        self.lid_bot = _f32(np.clip(0.5 + (yu - 108.0) / FEATHER, 0.0, 1.0))[:, None]
        if on_progress is not None:
            on_progress(1.0, BAKE_LABEL["save"])

    def _eye_layers(self, xe, ye, rre, sc, r):
        """照抄参考实现第 4 节：眼睛各层。返回 (rgb_over_black, alpha)"""
        tmp = np.zeros(xe.shape + (3,))
        ds_ = rre / sc["sclera"]
        a1 = r._aa(ds_ - M.SCLERA)
        tmp = tmp * (1 - a1[..., None]) + M.hx(M.SCLERA_COL) * a1[..., None]
        sh = M._interp_stops(ds_ / M.SCLERA_HALO,
                             [(0.82, 0.0), (M.SCLERA_VISIBLE_EDGE / M.SCLERA_HALO, 0.22),
                              (0.875, 0.28), (0.89, 0.19), (0.91, 0.11),
                              (0.94, 0.07), (0.96, 0.035), (1.0, 0.0)])
        tmp = tmp * (1 - sh[..., None]) + 255.0 * sh[..., None]
        cst = r._aa(np.abs(ds_ - M.SCLERA) - M.SCLERA_CONTACT / 2) * 0.16
        tmp = tmp * (1 - cst[..., None]) + 255.0 * cst[..., None]
        for key, rad, col in (("l3", M.L3_R, M.LAYER3), ("l2", M.L2_W, M.SCLERA_COL),
                              ("l2", M.L2_I, M.IRIS)):
            aa = r._aa(rre / sc[key] - rad)
            tmp = tmp * (1 - aa[..., None]) + M.hx(col) * aa[..., None]
        d1 = rre / sc["l1"]
        aa = r._aa(np.abs(d1 - M.L1_R) - 0.05)
        tmp = tmp * (1 - aa[..., None]) + M.hx(M.HILIGHT) * aa[..., None]
        aa = r._aa(d1 - M.PUPIL)
        tmp = tmp * (1 - aa[..., None]) + M.hx(M.PUPIL_COL) * aa[..., None]
        hxc = 80 + (M.HL_C[0] - 80) * sc["l2"]
        hyc = 80 + (M.HL_C[1] - 80) * sc["l2"]
        dhl = np.sqrt((xe - hxc) ** 2 + (ye - hyc) ** 2)
        hh = M._interp_stops(dhl / (M.HL_HALO * sc["l2"]),
                             [(0.53, 0.0), (0.56, 0.16), (M.HL_R / M.HL_HALO, 0.43),
                              (0.72, 0.19), (0.77, 0.12), (0.83, 0.055),
                              (0.89, 0.02), (0.96, 0.004), (1.0, 0.0)])
        tmp = tmp * (1 - hh[..., None]) + M.hx(M.HILIGHT) * hh[..., None]
        hg = r._aa(dhl - M.HL_R * sc["l2"])
        tmp = tmp * (1 - hg[..., None]) + M.hx(M.HILIGHT) * hg[..., None]
        return tmp, a1

    # ------------------------------------------------------------------ 存盘
    def _save(self, p):
        np.savez(p, glow_phases=self.glow_phases,
                 work_bg_rgb=self.work_bg_rgb,
                 moire_phases=self.moire_phases,
                 mist_mask=self.mist_mask,
                 disc_rgb=self.disc_rgb, disc_a=self.disc_a,
                 scan_a=self.scan_a, flicker_a=self.flicker_a,
                 lash_circ=self.lash_circ.astype(np.float16),
                 lash_dclip=self.lash_dclip.astype(np.float16),
                 eye_stack=self.eye_stack,
                 meta=np.array([self.lash_off, self.lash_w, self.eye_off,
                                self.eye_w, self.ppu, self.ss,
                                self.disc_off, self.disc_w]))

    def _load(self, p):
        z = np.load(p)
        self.glow_phases = z["glow_phases"]
        self.work_bg_rgb = z["work_bg_rgb"]
        self.moire_phases = z["moire_phases"]
        self.mist_mask = z["mist_mask"]
        self.disc_rgb = z["disc_rgb"]
        self.disc_a = z["disc_a"]
        self.scan_a = z["scan_a"]
        self.flicker_a = z["flicker_a"]
        self.lash_circ = z["lash_circ"].astype(np.float32)
        self.lash_dclip = z["lash_dclip"].astype(np.float32)
        self.eye_stack = z["eye_stack"]
        mo = z["meta"]
        self.lash_off, self.lash_w = int(mo[0]), int(mo[1])
        self.eye_off, self.eye_w = int(mo[2]), int(mo[3])
        self.disc_off, self.disc_w = int(mo[6]), int(mo[7])
        self.lid_off, self.lid_w = self.eye_off, self.eye_w
        li0, lw = self.lash_off * LASH_SS, self.lash_w * LASH_SS
        xl = (li0 + np.arange(lw) - self._margin_px() * LASH_SS) / (self.ppu * LASH_SS)
        XL, YL = np.meshgrid(xl, xl)
        self.lash_x2 = _f32(XL - 80.0)
        self.lash_y2 = _f32(YL - 80.0)
        yu = (self.lid_off + np.arange(self.lid_w) - self._margin_px()) / self.ppu
        self.lid_y = _f32(yu)[:, None]
        self.lid_bot = _f32(np.clip(0.5 + (yu - 108.0) / FEATHER, 0.0, 1.0))[:, None]
        t = np.linspace(0, 1, 3001)
        cx = 20 * (1 - t) ** 2 + 160 * t * (1 - t) + 140 * t ** 2
        self.lid_G = _f32(np.interp(yu, cx, (1 - t) ** 2 + t ** 2))[None, :]
        self.lid_H = _f32(np.interp(yu, cx, 2 * t * (1 - t)))[None, :]

    def _make_pil(self):
        N, ss = self.N, self.ss
        self._glow_img = [Image.frombytes("RGB", (N, N), self.glow_phases[k].tobytes())
                          for k in range(GLOW_PHASES)]
        # ---- 工作态三层（BGR 序，与画布一致）----
        self._workbg_img = Image.frombytes("RGB", (N, N),
                                           np.ascontiguousarray(self.work_bg_rgb).tobytes())
        self._moire_img = [Image.frombytes("RGB", (N, N),
                                           np.ascontiguousarray(self.moire_phases[k]).tobytes())
                           for k in range(M.MOIRE_PHASES)]
        self._mist_mask = Image.frombytes("L", (N, N),
                                          np.ascontiguousarray(self.mist_mask).tobytes())
        self._disc_img = Image.frombytes("RGB", (self.disc_w, self.disc_w),
                                         self.disc_rgb.tobytes())
        self._disc_mask = Image.frombytes("L", (self.disc_w, self.disc_w),
                                          self.disc_a.tobytes())
        self._scan_img = _solid(N, M.hx(M.SCAN_COL))
        self._scan_mask = Image.frombytes("L", (N, N), self.scan_a.tobytes())
        self._white_img = Image.new("RGB", (N, N), (255, 255, 255))
        self._lash_img = _solid(self.lash_w, M.hx(M.DARK_CORNERS))
        self._eye_rgb = [Image.frombytes("RGB", (self.eye_w, self.eye_w),
                                         self.eye_stack[k, ..., :3].tobytes())
                         for k in range(PHASES)]
        self._eye_a = np.ascontiguousarray(self.eye_stack[..., 3])
        self._canvas = Image.new("RGB", (N, N), (0, 0, 0))
        self._blank = Image.new("RGB", (N, N), (0, 0, 0))
        self._bgra = np.empty((N, N, 4), dtype=np.uint8)
        # ---- 主体不透明区遮罩（外盘 + 描边的覆盖）----
        # ★ 用来兜住 alpha：单靠 max(R,G,B) 会让**深色**部位严重偏透明
        #   （深色睫毛 #2b3388 的最大通道只有 136 ⇒ alpha 0.53，
        #    在浅色桌面上会被冲淡成亮蓝；深色菜单面板更极端，只剩 0.11）。
        ii = np.arange(N) + 0.5
        dd = np.sqrt((ii[None, :] - N / 2.0) ** 2 + (ii[:, None] - N / 2.0) ** 2) / self.ppu
        self._body_a = (_u8(np.clip(0.5 - (dd - M.OUTER_VISIBLE_EDGE) / 0.7, 0.0, 1.0)))
        # ---- 亮底 alpha 抬升查表（256 项，单调不削顶）----
        self._alpha_lut = M.alpha_lut()

    # ------------------------------------------------------------------ 渲染
    def lid_mask(self, open_t=1.0, breathe=0.5):
        """解析求眼睑遮罩（只在眼睛裁剪框内算）——与参考实现数学等价"""
        if M.LID_MID_THINK is None:
            ty, sy = 14.5 + breathe, 0.55 + 0.45 * breathe
            mid_think, sag = 15.0 * sy + 60.0 + ty, 30.0 * sy
        else:
            u = (breathe - 0.5) * 2.0
            mid_think = M.LID_MID_THINK + (M.LID_DOWN_AMP if u > 0 else M.LID_UP_AMP) * u
            sag = M.LID_SAG_BASE + M.LID_SAG_AMP * u
        mid = M.LID_MID_OPEN + (mid_think - M.LID_MID_OPEN) * (1.0 - float(np.clip(open_t, 0, 1)))
        A = np.float32(mid - sag / 2.0)
        B = np.float32(mid + sag / 2.0)
        yc = A * self.lid_G + B * self.lid_H
        m = 0.5 + (self.lid_y - yc) * np.float32(1.0 / FEATHER)
        np.clip(m, 0.0, 1.0, out=m)
        np.maximum(m, self.lid_bot, out=m)
        return _u8(m)

    def draw(self, t_ms=0.0, open_t=1.0, flicker_op=0.0, cycle_ms=CYCLE_MS):
        """把一帧合成到内部画布（BGR 通道序）。返回该画布。"""
        N, ss = self.N, self.ss
        cv = self._canvas
        # ★ 辉光相位帧**整幅不带遮罩**地 paste —— 它本身就是"黑底观感"，一次覆盖即完成清底，
        #   所以连"先抹黑"都省了（省一次全幅 paste）。千万别再带遮罩贴一次（alpha 会乘两遍）。
        phg = int((t_ms / GLOW_CYCLE_MS) % 1.0 * GLOW_PHASES) % GLOW_PHASES
        # ★ 工作权重 k：由 open_t 推得（与眼睑同一条缓动）⇒ 衔接天然同步。
        k = 1.0 - float(np.clip(open_t, 0.0, 1.0))
        if k <= 0.004:
            cv.paste(self._glow_img[phg], (0, 0))       # 常态快路：整幅覆盖（顺带完成清底）
        else:
            # ★★ 工作态 / 过渡：**四层合成**（全部从辉光派生 ⇒ "辉光卸下伪装，变成 01 二进制"）
            #   L0 底光 undershine（数字背后的光源，静态）
            #   L1 淡雾 mist（辉光的淡残影，逐像素同源 ⇒ 自带全部动态）
            #   L2 摩尔纹 moire（角向细纹 × 水平细纹的干涉，缓慢流动）
            #   L3 数字 0/1（密度 −40%，颜色 = 辉光自己的颜色）
            phm = int((t_ms / GLOW_CYCLE_MS) % 1.0 * M.MOIRE_PHASES) % M.MOIRE_PHASES
            a_k = int(round(k * 255.0))
            if k >= 0.996:
                cv.paste(self._workbg_img, (0, 0))      # 底光整幅覆盖（顺带清底，≈0.01 ms）
                cv.paste(self._glow_img[phg], (0, 0), self._mist_mask)   # 淡雾
                cv = ImageChops.lighter(cv, self._moire_img[phm])        # 摩尔纹
            else:
                cv.paste(self._blank, (0, 0))                               # 清底
                cv.paste(self._glow_img[phg], (0, 0),
                         Image.new("L", (N, N), int(round((1.0 - k) * 255.0))))   # 辉光淡出
                cv.paste(self._workbg_img, (0, 0), Image.new("L", (N, N), a_k))  # 底光淡入
                cv.paste(self._glow_img[phg], (0, 0),
                         ImageChops.multiply(self._mist_mask,
                                             Image.new("L", (N, N), a_k)))       # 淡雾淡入
                cv = ImageChops.lighter(cv, ImageChops.multiply(
                    self._moire_img[phm], _solid(N, (a_k, a_k, a_k))))           # 摩尔纹淡入
            _dig, _nd = FD.digit_layer(N, (N - 1) / 2.0, (N - 1) / 2.0,
                                     M.GLOW_R_DISC * self.ppu, self.ppu, t_ms, k,
                                     bgr=True)      # ★ 本画布是 BGR 序
            cv = ImageChops.lighter(cv, _dig)       # 数字是"光" ⇒ 取 max 叠加
        # 外盘 + 白描边：只贴需要的那个小块
        cv.paste(self._disc_img, (self.disc_off, self.disc_off), self._disc_mask)

        # --- 睫毛：静态圆 ∪ 旋转方（在 ss 分辨率算，再下采样）---
        ang = np.radians(M.lash_angle(t_ms) + M.CORNER_BASE_ROT)
        c, s = np.float32(np.cos(ang)), np.float32(np.sin(ang))
        xr = self.lash_x2 * c + self.lash_y2 * s
        yr = self.lash_y2 * c - self.lash_x2 * s
        h = np.float32(M.CORNER_SQUARE_HALF - M.CORNER_SQUARE_R)
        np.abs(xr, out=xr); xr -= h; np.maximum(xr, 0.0, out=xr)
        np.abs(yr, out=yr); yr -= h; np.maximum(yr, 0.0, out=yr)
        np.multiply(xr, xr, out=xr)
        np.multiply(yr, yr, out=yr)
        xr += yr
        np.sqrt(xr, out=xr)
        xr -= M.CORNER_SQUARE_R
        m = 0.5 - xr * np.float32(1.0 / 0.7)
        np.clip(m, 0.0, 1.0, out=m)
        m *= self.lash_dclip
        # ★ 圆形 ∪ 旋转方：参考实现写的是 aa(min(d_cc, d_sq))。
        #   而 aa 对距离**单调递减** ⇒ aa(min(a,b)) == max(aa(a), aa(b))。
        #   （曾误写成 min，结果圆内那圈深色睫毛整段丢失 —— 半径 48~52 一带露出底盘蓝。）
        np.maximum(m, self.lash_circ, out=m)
        if LASH_SS > 1:
            m = _ds(m, LASH_SS)          # → 最终分辨率
        lash_u8 = _u8(m)
        cv.paste(self._lash_img, (self.lash_off, self.lash_off),
                 Image.frombytes("L", (self.lash_w, self.lash_w), lash_u8.tobytes()))

        # --- 眼睛（相位缓存 × 眼睑遮罩）---
        ph = int((t_ms / cycle_ms) % 1.0 * PHASES) % PHASES
        lid = self.lid_mask(open_t, M.read_eye_progress(t_ms / cycle_ms))
        a16 = self._eye_a[ph].astype(np.uint16)
        a16 *= lid.astype(np.uint16)
        a16 //= 255
        cv.paste(self._eye_rgb[ph], (self.eye_off, self.eye_off),
                 Image.frombytes("L", (self.eye_w, self.eye_w),
                                 a16.astype(np.uint8).tobytes()))

        # --- 扫描线 ---
        if k <= 0.004:
            cv.paste(self._scan_img, (0, 0), self._scan_mask)
        elif k < 0.996:                                # 工作态扫描线一并消失（辉光都没了）
            cv.paste(self._scan_img, (0, 0),
                     ImageChops.multiply(self._scan_mask,
                                         Image.new("L", (N, N), int(round((1.0 - k) * 255.0)))))

        # --- 眼睛闪烁（白圆；官方最高仅 4.8% 不透明度）---
        if flicker_op > 1e-4:
            fm = np.clip(self.flicker_a.astype(np.float32) * np.float32(flicker_op),
                         0, 255).astype(np.uint8)
            cv.paste(self._white_img, (0, 0),
                     Image.frombytes("L", (N, N), fm.tobytes()))
        # ★★ 必须把 cv 同步回 self._canvas —— 这里曾是真 bug（2026-09-22 主人截图抓到）。
        #   `ImageChops.lighter(a, b)` **返回新对象、不修改 a**。工作态分支里的
        #       cv = ImageChops.lighter(cv, self._moire_img[..])   # 摩尔纹
        #       cv = ImageChops.lighter(cv, _dig)                  # 数字
        #   一旦执行，cv 就不再是 self._canvas —— 之后所有 paste（外盘 / 睫毛 / 眼睛 /
        #   扫描线 / 闪烁）**全落在新对象上**，而 self._canvas 永远停在 lighter 之前那一刻。
        #   主程序上屏走 `to_bgra()`（无参 ⇒ 取 self._canvas）⇒ 屏幕上**主体和数字全部消失，
        #   只剩"底光 + 雾"的一片深蓝**，而且底光是整幅 paste ⇒ 画布方框直接暴露。
        #   ★ 离屏测试检查的是 draw() 的**返回值**，所以一直显示正常 —— 只有真机能暴露。
        #     教训：凡是"内部有原地/非原地两种更新语义"的画布，返回值和内部状态必须对齐，
        #     并且**至少有一条测试要验"内部状态"，而不是只验返回值**。
        self._canvas = cv
        return cv

    def to_bgra(self, cv=None, ov=None):
        """→ 预乘 BGRA（= 黑底观感本身）+ alpha。给 UpdateLayeredWindow 用。

        alpha 的两条来源必须一起用（★ 血泪教训）：
          ① `max(R,G,B)` —— 让**加性外发光**成立（这是整个 alpha 策略的根基）；
          ② 主体遮罩 `_body_a` —— 兜住**深色不透明部位**。
             只靠 ① 的话，深色睫毛 #2b3388 的 alpha 只有 0.53、深色菜单面板只剩 0.11，
             浅色桌面上会整片冲淡成惨白（实测面板中点被渲成 176,179,255）。

        ov: 可选的自绘 UI 覆盖层（PIL RGBA，BGR 通道序）。
            必须单独走**正确的 alpha 合成**，不能先 paste 进画布再靠 max(RGB) 反推。
        """
        cv = cv if cv is not None else self._canvas
        arr = np.asarray(cv, dtype=np.uint8)
        # ★★ 2026-09-22（高分屏计划的副产品）：`arr.max(axis=2)` → 三次 `np.maximum`，
        #   **实测快 18 倍、结果逐像素一致**。
        #   原因：numpy 对**长度为 3 的轴**做 reduce 走的是极慢的通用路径 ——
        #     260px 档 3.04 ms（占装箱 75%）⇒ 0.16 ms；520px 档 11.54 ms（占 71%）⇒ 0.63 ms。
        #   ★ 这是纯 numpy 反模式，与画质无关；当年写 `max(axis=2)` 是"看起来更清楚"，其实是坑。
        #   ★★ 以后再写"沿最后一维（通道）聚合"的操作，**一律手工展开**，别用 reduce。
        alpha = np.maximum(arr[..., 0], arr[..., 1])
        np.maximum(alpha, arr[..., 2], out=alpha)
        # ★ 亮底"凝实"：alpha 过一遍**亮度查表**（alpha → alpha + (255-alpha)·k·(alpha/255)^p）。
        #   黑底显示的是预乘色 ⇒ 这一步**完全不改变暗色桌面上的样子**；
        #   亮底显示 = 预乘色 + (1-alpha)×背景 ⇒ 白渗漏变少 = 浅色界面上更显形、更成团。
        #   ★★ 曾经用过"径向增益"，结果在 1.18 处造出一圈更高的 alpha —— 又是一圈"实心"。
        #      查表是**按亮度**抬升，天然单调、永不削顶，还放大了中间调的差异。
        alpha = self._alpha_lut[alpha]
        np.maximum(alpha, self._body_a, out=alpha)   # ★ 抬升之后再兜一次主体，保证主体=255
        if ov is not None:
            o = np.asarray(ov, dtype=np.uint8)                 # (N,N,4)  BGR+alpha
            a = o[..., 3].astype(np.uint16)
            inv = 255 - a
            rgb = (arr.astype(np.uint16) * inv[..., None]
                   + o[..., :3].astype(np.uint16) * a[..., None]) // 255
            alpha = (a + (alpha.astype(np.uint16) * inv) // 255).astype(np.uint8)
            arr = rgb.astype(np.uint8)
        out = self._bgra
        out[..., 0:3] = arr
        out[..., 3] = alpha
        return out, alpha

    def frame(self, t_ms=0.0, open_t=1.0, flicker_op=0.0, cycle_ms=CYCLE_MS, ov=None):
        cv = self.draw(t_ms, open_t, flicker_op, cycle_ms)
        return self.to_bgra(cv, ov)

    def frame_rgb(self, t_ms=0.0, open_t=1.0, flicker_op=0.0, cycle_ms=CYCLE_MS, ov=None):
        """给验收脚本用：把 BGR 序转回 RGB 序 + alpha"""
        bgra, alpha = self.frame(t_ms, open_t, flicker_op, cycle_ms, ov)
        return bgra[..., 2::-1].copy(), alpha

    def over_desktop(self, bg_rgb, t_ms=0.0, open_t=1.0, flicker_op=0.0,
                     cycle_ms=CYCLE_MS, ov=None):
        """把一帧**按分层窗口的真实规则**合成到某个桌面底色上 —— 验收用。
        规则：显示结果 = 预乘色 + (1 - alpha) × 桌面色

        ★ 必须用 int32：`(255-alpha)*bg` 最大 65025，**int16 会溢出成负数**，
          被 clip 成 0 —— 表现为"挂件周围一圈莫名其妙的黑方块"（踩过，白查半小时）。
        """
        bgra, alpha = self.frame(t_ms, open_t, flicker_op, cycle_ms, ov)
        prem = bgra[..., 2::-1].astype(np.int32)               # RGB（预乘）
        bg = np.asarray(bg_rgb, dtype=np.int32)
        return np.clip(prem + (255 - alpha.astype(np.int32))[..., None] * bg // 255,
                       0, 255).astype(np.uint8)
