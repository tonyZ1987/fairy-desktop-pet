# -*- coding: utf-8 -*-
# ★★★ 注意：**这是生产模块，不是设计稿**（名字里的 step17 是历史遗留）。
#   `fairy_bar.py` 用它画进度条的百分比数字与副标题（`styled_text` / `FONT_SMALL`）。
#   ⇒ 它**必须留在 pet-v2 根目录**，不能像其它 step*.py 那样收进 `_dev/`。
#   2026-09-23 主人问「主体文件夹里这些 step 文件是启动必须的吗」时确认的。
"""工作态进度条 · 设计稿 v3（直接复刻《绝区零》H.D.D. SYSTEM 载入界面）

v2 → v3 改了什么（全部来自主人 2026-09-22 13:06 的三条反馈）：

  ① **斜体方向反了** —— v2 用的是 `shear = -0.30`。按 PIL `transform(AFFINE)` 的约定
     （矩阵作用方向是"输出坐标 → 输入坐标"），`-shear` 得到的是**后倾（backslant）**，
     而参考图是**前倾斜体**。v3 改成 `+shear`，并在 `check_shear()` 里**量头顶/底部的质心**自检。
  ② **进度条要扁平** —— 参考图里槽的宽高比约 **20:1**；v2 是 11.6:1，太厚。
     v3：槽高 `0.030×S`、槽宽 `0.86×S` ⇒ **≈ 28:1**，并复刻参考图的"细亮边框 + 内部暗纹 + 前端亮边"。
  ③ **英文字糊在一起看不清** —— 根因是 v2 把小字**压到 ~6.8 实际像素**（为了塞进槽宽，
     字号被 fit 到极小）。v3：副标题**单独一行、按整幅画布宽度**拟合，字号 ≈ 0.050×S（260px 档 ≈ 18px），
     字距从 0.40 收到 0.26、发光减半、描边减薄 —— 并且副标题文案缩短（长文案必然糊）。

参考图的要素（逐项复刻）：
  粗体**前倾斜体** + 字距放宽 ｜ 白芯→浅蓝**纵向渐变字面** ｜ 外描边再外发光
  扁平槽：细亮边框 + 内部暗蓝 + 细横纹 ｜ 填充：蓝渐变 + **前端亮边**
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
SUP = 4                                     # 超采样（画完缩回 = 平滑边缘）

C_EDGE = (236, 248, 255)
C_DARK = (4, 12, 30)
C_FILL_HI = (150, 210, 255)
C_FILL_LO = (22, 70, 160)
C_SLOT_HI = (128, 162, 200)     # 未填充区（复刻参照图的中蓝灰、上亮下暗）
C_SLOT_LO = (78, 108, 146)
C_TEXT_TOP = (255, 255, 255)
C_TEXT_BOT = (150, 205, 255)
C_TEXT_EDGE = (228, 245, 255)
C_TEXT_DARK = (6, 20, 48)
C_GLOW = (104, 182, 255)

# ---- 几何（全部按画布 S 归一化；260px 宠物 ⇒ S=370）----
#   ★ 2026-09-22 13:14 主人："进度条还是得粗的啊" + 给了新参照 ⇒ 槽高 0.030 → **0.065**
#     宽高比 28:1 → **13.2:1**，与参照图（实测约 13:1）一致。
BAR_W_F = 0.860                             # 槽宽 / 画布
BAR_H_F = 0.065                             # 槽高 / 画布（粗槽）
BAR_Y_F = 0.884                             # 槽中心 y / 画布
PCT_PX_F = 0.070                            # 中央百分比字号 / 画布（略小于槽高 ⇒ 落在槽内）
SUB_PX_F = 0.048                            # 副标题字号 / 画布
SUB_TRACK = 0.24                            # 副标题字距
SHEAR = 0.28                                # ★ 正值 = 前倾斜体（v2 误用了负值）

# 字体候选：★ 参照图的字**很粗**（粗体 + 白描边 + 渐变字面）。
#   v3 的副标题用了 bahnschrift（偏纤细）⇒ 主人："字体又有点过纤细了" ⇒ 换成粗体。
FONT_BIG = ["C:/Windows/Fonts/impact.ttf",
            "C:/Windows/Fonts/Swis721 BlkCn BT Black.ttf",
            "C:/Windows/Fonts/arialbd.ttf"]
FONT_SMALL = ["C:/Windows/Fonts/arialbd.ttf",
              "C:/Windows/Fonts/bahnschrift.ttf",
              "C:/Windows/Fonts/impact.ttf",
              "C:/Windows/Fonts/ariali.ttf"]

_CACHE = {}


def _font(paths, px):
    key = (tuple(paths), px)
    if key in _CACHE:
        return _CACHE[key]
    f = None
    for p in paths:
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, px)
                break
            except Exception:
                continue
    if f is None:
        f = ImageFont.load_default()
    _CACHE[key] = f
    return f


def _stroke_mask(txt, px, shear, track, stroke_k, fonts):
    """带字距的文字遮罩（含描边）+ 斜体剪切。

    ★ 画布尺寸**只由 px 决定**，与 `stroke_k` 无关 —— 否则"字面遮罩"和"粗描边遮罩"
      尺寸不一致，做差集时会 `ValueError: images do not match`（踩过）。
    """
    f = _font(fonts, px)
    gap = px * track
    ws = []
    for ch in txt:
        b = f.getbbox(ch)
        ws.append(b[2] - b[0])
    w = int(sum(ws) + gap * (len(txt) - 1))
    st = max(0.0, px * stroke_k)
    pad = int(px * 0.5 + px * 0.25 * 2 + 4)          # 固定留白（≥ 任何 stroke_k）
    W, H = w + pad * 2, int(px * 1.45) + pad * 2
    m = Image.new("L", (W, H), 0)
    dm = ImageDraw.Draw(m)
    x = float(pad)
    for ch, cw in zip(txt, ws):
        dm.text((x, pad), ch, font=f, fill=255,
                stroke_width=int(round(st)), stroke_fill=255)
        x += cw + gap
    # ★ 前倾斜体：x_in = x_out + shear*y_out − c ⇒ y 越大（越靠下）采样越靠右 ⇒ 顶在右 = 前倾
    m = m.transform((W, H), Image.AFFINE,
                    (1, shear, -shear * H * 0.55, 0, 1, 0),
                    resample=Image.BICUBIC)
    return m, W, H


def _ring(txt, px, shear, track, stroke_k, fonts):
    face, W, H = _stroke_mask(txt, px, shear, track, 0.0, fonts)
    allm, _, _ = _stroke_mask(txt, px, shear, track, stroke_k, fonts)
    return Image.composite(allm, Image.new("L", (W, H), 0),
                           Image.eval(face, lambda v: 255 - v)), W, H


def styled_text(txt, px, shear=SHEAR, track=0.12, glow_r=0.22, glow_a=0.55,
                dark_k=0.16, light_k=0.06, fonts=None,
                face_top=None, face_bot=None,
                dark_col=None, edge_col=None, dark_a=200):
    """粗斜体 + 字距 + 深描边 + 亮描边 + 渐变字面 + 外发光。返回 RGBA（已按内容裁剪）。

    ★ 描边的**可见结构**（从字面向外）：亮边 0..light_k，深边 light_k..dark_k
      —— 因为亮边是**后画**的，会把内侧那一段深边盖住。所以：
        · 「白字身 + 细亮边 + 粗深边」= 参照图那种深色外框（dark_k 大）
        · 「深字身 + 粗亮边 + 细深边」= 贴纸式高对比，压在浅色条上最清楚（dark_k 略大于 light_k）
    `face_top/face_bot`：字面渐变的两端色（默认 C_TEXT_TOP → C_TEXT_BOT 的浅色渐变）。
    """
    fonts = fonts or FONT_BIG
    face, W, H = _stroke_mask(txt, px, shear, track, 0.0, fonts)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow = face.filter(ImageFilter.GaussianBlur(max(1, int(px * glow_r))))
    glow = glow.point(lambda v: int(v * glow_a))
    out.paste(Image.new("RGBA", (W, H), C_GLOW + (255,)), (0, 0), glow)
    rk, _, _ = _ring(txt, px, shear, track, dark_k, fonts)
    out.paste(Image.new("RGBA", (W, H), (dark_col or C_TEXT_DARK) + (int(dark_a),)), (0, 0), rk)
    rl, _, _ = _ring(txt, px, shear, track, light_k, fonts)
    out.paste(Image.new("RGBA", (W, H), (edge_col or C_TEXT_EDGE) + (255,)), (0, 0), rl)
    grad = Image.new("RGB", (W, H))
    dg = ImageDraw.Draw(grad)
    _ct = face_top or C_TEXT_TOP
    _cb = face_bot or C_TEXT_BOT
    for y in range(H):
        t = min(1.0, max(0.0, (y - H * 0.16) / (H * 0.68)))
        c = tuple(int(_ct[i] + (_cb[i] - _ct[i]) * t) for i in range(3))
        dg.line([(0, y), (W, y)], fill=c)
    fill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fill.paste(grad.convert("RGBA"), (0, 0), face)
    out.alpha_composite(fill)
    bb = out.getbbox()
    return out.crop(bb) if bb else out


def fit_px(txt, max_w, start_px, track, fonts, shear=SHEAR):
    px = int(start_px)
    while px > 8:
        _, W, _ = _stroke_mask(txt, px, shear, track, 0.0, fonts)
        if W <= max_w:
            return px
        px -= 1
    return 8


def check_shear():
    """自检斜体方向：量文字遮罩**顶部与底部**的横向质心。顶在右 ⇒ 前倾（对）。"""
    m, W, H = _stroke_mask("H", 60, SHEAR, 0.0, 0.0, FONT_BIG)
    a = np.asarray(m, np.float64)
    ys = np.where(a.sum(axis=1) > 0)[0]
    if len(ys) < 4:
        return None
    top = ys[:max(1, len(ys) // 5)]
    bot = ys[-max(1, len(ys) // 5):]
    xs = np.arange(W)

    def cx(rows):
        sub = a[rows[0]:rows[-1] + 1]
        w = sub.sum(axis=0)
        return float((xs * w).sum() / max(1e-9, w.sum()))
    ct, cb = cx(top), cx(bot)
    return ct - cb, (ct, cb)


def bar_layer(S, pct, layout="A", cap_top="", cap_bot="", sub_fonts=None,
              sub_px_f=SUB_PX_F, gold=False):
    """画布尺寸 S 的工作态进度条（RGBA）。pct: 0..1

    layout A = 扁平槽 + 中央大数字 + 下方一行副标题（推荐，不压主体）
    layout B = 完整复刻：标题(上) + 扁平槽(中) + 副标题(下)
    """
    S4 = S * SUP
    im = Image.new("RGBA", (S4, S4), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    bw, bh = BAR_W_F * S4, BAR_H_F * S4
    bx, by = (S4 - bw) / 2.0, BAR_Y_F * S4 - bh / 2.0
    r = bh * 0.42

    # ① 外层细深线（浅底也能看清）
    lw_d = max(2, int(bh * 0.17))
    d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=r,
                        fill=(6, 14, 30, 130), outline=C_DARK + (210,), width=lw_d)
    # ② 内层亮线（很细，参考图就是一条发光细边）
    ins = lw_d * 1.5
    lw_l = max(2, int(bh * 0.10))
    d.rounded_rectangle([bx + ins, by + ins, bx + bw - ins, by + bh - ins],
                        radius=r * 0.8, outline=C_EDGE + (238,), width=lw_l)
    # ③ 槽内底（★ 参照图的未填充区是**中蓝灰**、上亮下暗，不是深藏青 —— 复刻它）
    ins2 = ins + lw_l * 1.6
    ix0, iy0, ix1, iy1 = bx + ins2, by + ins2, bx + bw - ins2, by + bh - ins2
    h4i = max(2, int(iy1 - iy0))
    w4i = max(2, int(ix1 - ix0))
    slot = Image.new("RGB", (w4i, h4i))
    ds = ImageDraw.Draw(slot)
    for y in range(h4i):
        t = y / float(h4i - 1)
        k = 1.0 - 0.42 * abs(t - 0.26) * 2.0            # 上部高光、下部略暗
        k = max(0.0, min(1.0, k))
        c = tuple(int(C_SLOT_LO[i] + (C_SLOT_HI[i] - C_SLOT_LO[i]) * k) for i in range(3))
        ds.line([(0, y), (w4i, y)], fill=c)
    simg = Image.new("RGBA", (w4i, h4i), (0, 0, 0, 0))
    simg.paste(slot.convert("RGBA"), (0, 0), Image.new("L", (w4i, h4i), 168))
    im.alpha_composite(simg, (int(ix0), int(iy0)))

    # ④ 填充：横向蓝渐变（左暗右亮）+ 上部高光带 + 前端亮边
    fw = max(0.0, (bw - ins2 * 2) * max(0.0, min(1.0, pct)))
    if fw > 1.5:
        fx0, fy0 = bx + ins2, by + ins2
        fx1, fy1 = fx0 + fw, by + bh - ins2
        w4, h4 = max(2, int(fw)), max(2, int(fy1 - fy0))
        grad = Image.new("RGB", (w4, h4))
        dg = ImageDraw.Draw(grad)
        for x in range(w4):
            t = x / float(max(1, w4 - 1))            # 左 → 右 变亮
            for y in range(h4):
                v = y / float(max(1, h4 - 1))
                k = (0.28 + 0.72 * t) * (1.0 - 0.42 * abs(v - 0.34) * 2.0)
                k = max(0.0, min(1.0, k))
                c = tuple(int(C_FILL_LO[i] + (C_FILL_HI[i] - C_FILL_LO[i]) * k)
                          for i in range(3))
                dg.point((x, y), fill=c)
        fimg = Image.new("RGBA", (w4, h4), (0, 0, 0, 0))
        fimg.paste(grad.convert("RGBA"), (0, 0), Image.new("L", (w4, h4), 255))
        im.alpha_composite(fimg, (int(fx0), int(fy0)))
        d.line([(fx1 - max(1, lw_l // 2), fy0), (fx1 - max(1, lw_l // 2), fy1)],
               fill=(246, 253, 255, 255), width=max(2, int(bh * 0.14)))

    # ⑤ 槽内细横纹（参考图的扫描线）
    for k in range(1, 3):
        y = by + bh * k / 3.0
        d.line([(bx + ins2, y), (bx + bw - ins2, y)],
               fill=(255, 255, 255, 34), width=max(1, int(bh * 0.06)))

    # ⑥ 中央百分比（字号独立于槽高 ⇒ 允许溢出槽体，像 HUD）
    t = styled_text("%d%%" % int(round(pct * 100)), int(PCT_PX_F * S4),
                    track=0.10, glow_r=0.20, glow_a=0.55,
                    dark_k=0.16, light_k=0.055, fonts=FONT_BIG)
    im.alpha_composite(t, (int((S4 - t.width) / 2.0), int(by + bh / 2.0 - t.height / 2.0)))

    # ⑦ layout B 的标题（在槽上方）
    if layout == "B" and cap_top:
        px = fit_px(cap_top, bw * 0.62, int(0.062 * S4), 0.22, FONT_BIG)
        c = styled_text(cap_top, px, track=0.22, glow_r=0.20, glow_a=0.6,
                        dark_k=0.16, light_k=0.055, fonts=FONT_BIG)
        im.alpha_composite(c, (int((S4 - c.width) / 2.0),
                               int(by - c.height - bh * 0.9)))

    # ⑧ 副标题：**按整幅宽度**拟合（v2 按槽宽拟合 ⇒ 被压到只有 6.8 实际像素 ⇒ 糊）
    if cap_bot:
        maxw = S4 * 0.94
        px = fit_px(cap_bot, maxw, int(sub_px_f * S4), SUB_TRACK, sub_fonts or FONT_SMALL)
        c = styled_text(cap_bot, px, track=SUB_TRACK, glow_r=0.14, glow_a=0.35,
                        dark_k=0.14, light_k=0.05, fonts=sub_fonts or FONT_SMALL)
        cy = int(by + bh + bh * 0.9)
        cy = min(cy, S4 - c.height - int(bh * 0.2))
        im.alpha_composite(c, (int((S4 - c.width) / 2.0), cy))
        if gold:                                     # 仅用于强调"清晰度"的对比
            pass
    return im.resize((S, S), Image.LANCZOS)


# ------------------------------------------------------------------ 拼版
def mascot_rgba(px_size=260, t_ms=6000.0, open_t=0.0):
    from fairy_layers import FastMascot
    fm = FastMascot(size=px_size, ss=2, verbose=False,
                    # ★ 2026-09-23：原来是相对路径 "_cache"（跟着 cwd / 脚本目录走），
                    #   从 _dev 里跑脚本时会长出一份 94.79 MB 的游离缓存 ⇒ 一律绝对路径。
                    cache_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache"))
    fm.draw(t_ms=t_ms, open_t=open_t, flicker_op=0.0)
    bgra = np.asarray(fm.to_bgra(ov=None)[0]).astype(np.uint8)
    rgb = bgra[..., 2::-1]
    return np.dstack([rgb, bgra[..., 3]])


def compose(bg_rgb, pet_rgba, bar_rgba):
    out = Image.fromarray(bg_rgb).convert("RGBA")
    if pet_rgba is not None:
        out.alpha_composite(Image.fromarray(pet_rgba, "RGBA"))
    out.alpha_composite(bar_rgba)
    return out.convert("RGB")


def main():
    S = 370
    pet = mascot_rgba(260, 6000.0, 0.0)

    sh = check_shear()
    print("■ 斜体方向自检：顶部质心 − 底部质心 = %+.2f px（%s）"
          % (sh[0], "前倾 ✅" if sh[0] > 0 else "后倾 ❌"))

    try:
        F1 = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
        F2 = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 13)
    except Exception:
        F1 = F2 = ImageFont.load_default()

    states = [0.0, 0.35, 0.72, 1.0]
    CAP = "FAIRY WORKING"
    gap = 12
    W = gap + (S + gap) * len(states)
    H = 40 + (S + gap) * 2 + 30
    out = Image.new("RGB", (W, H), (20, 22, 28))
    d = ImageDraw.Draw(out)
    d.text((gap, 8), "工作态进度条 · 设计稿 v4（粗槽 13:1 + 前倾斜体 + 加粗字 + 中蓝灰未填充）",
           fill=(232, 238, 248), font=F1)
    y = 40
    for ri, bgc in enumerate(((10, 11, 14), (238, 240, 245))):
        bg = np.zeros((S, S, 3), np.uint8)
        bg[:, :, :] = bgc
        for ci, p in enumerate(states):
            out.paste(compose(bg, pet, bar_layer(S, p, "A", cap_bot=CAP)),
                      (gap + ci * (S + gap), y))
            if ri == 0:
                d.text((gap + ci * (S + gap) + 4, y + S + 2), "%d%%" % int(p * 100),
                       fill=(205, 215, 230), font=F2)
        y += S + gap
    p1 = os.path.join(ROOT, "STEP15_进度条_设计稿.png")
    out.save(p1)
    print("已出:", p1, out.size)

    # 备选：① 布局 A vs B ② 副标题文案/字体
    variants = [
        ("A · FAIRY WORKING", dict(layout="A", cap_bot="FAIRY WORKING")),
        ("B · 完整复刻", dict(layout="B", cap_top="H.D.D. SYSTEM", cap_bot="FAIRY WORKING")),
        ("A · H.D.D. SYSTEM", dict(layout="A", cap_bot="H.D.D. SYSTEM")),
        ("A · SYSTEM ONLINE", dict(layout="A", cap_bot="SYSTEM ONLINE")),
    ]
    alt = Image.new("RGB", (gap + (S + gap) * len(variants), 40 + S + 74), (18, 20, 26))
    da = ImageDraw.Draw(alt)
    da.text((gap, 8), "备选：布局 A/B 与副标题文案（同一份代码，改参数即可）",
            fill=(232, 238, 248), font=F1)
    for i, (lab, kw) in enumerate(variants):
        im = compose(np.full((S, S, 3), 11, np.uint8), pet, bar_layer(S, 0.72, **kw))
        alt.paste(im, (gap + i * (S + gap), 40))
        da.text((gap + i * (S + gap) + 4, 40 + S + 4), lab, fill=(150, 205, 255), font=F2)
    p2 = os.path.join(ROOT, "STEP15_进度条_备选.png")
    alt.save(p2)
    print("已出:", p2, alt.size)

    # 3× 放大：看斜体方向 / 副标题清晰度
    z = compose(np.full((S, S, 3), 12, np.uint8), pet, bar_layer(S, 0.72, "A", cap_bot=CAP))
    z = z.crop((0, int(S * 0.80), S, S))
    z = z.resize((int(z.width * 3), int(z.height * 3)), Image.LANCZOS)
    p3 = os.path.join(ROOT, "STEP15_进度条_放大.png")
    z.save(p3)
    print("已出:", p3, z.size)


if __name__ == "__main__":
    main()
