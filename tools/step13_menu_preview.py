# -*- coding: utf-8 -*-
"""滑块式菜单验收（2026-09-22）：四种状态各出一张 + 点击逻辑自测。

产出：
  STEP12_菜单_滑块式.png —— 自动(跟随) / 自动(60秒循环) / 手动(常态) / 手动(工作态)
  + 命中测试：模拟点每一段，检查状态是否按预期变化
"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)     # ★ 本脚本在 tools/ 下 ⇒ 仓库根在**上一级**
sys.path.insert(0, ROOT)         # 这样才 import 得到 fairy_pet
os.chdir(ROOT)                   # 工作目录也切到根：缓存 / state.json / 产物都落在根
from fairy_pet import FairyPet, SIZES, CACHE_DIR   # noqa: E402

try:
    F = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
    F2 = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 12)
except Exception:
    F = F2 = ImageFont.load_default()

pet = FairyPet(size=260)
print("窗口 %d px" % pet.win, flush=True)


def shot(title, hover=(1, 2)):
    ov = pet._overlay({"layout": pet._menu_layout(), "hover": hover}, None)
    # ★ overlay 的 PIL 数据实际是 **BGR** 序（见 _overlay 的注释），直接 convert("RGB") 会红蓝对调
    #   ⇒ 预览时必须把 R/B 翻回来（菜单本身没问题，是预览脚本的事）。
    im = Image.merge("RGB", ov.convert("RGB").split()[::-1])
    L = pet._menu_layout()
    x0, y0, w, h = L["x0"], L["y0"], L["w"], L["h"]
    pad = 8
    box = (max(0, x0 - pad), max(0, y0 - pad),
           min(im.width, x0 + w + pad), min(im.height, y0 + h + pad))
    c = im.crop(box)
    return c.resize((int(c.width * 1.5), int(c.height * 1.5)), Image.LANCZOS), title, (w, h)


shots = []


def snapshot(t):
    pet.follow = True
    pet.auto_mode = "follow"
    pet.size_idx = 1
    pet.px = 260
    pet.manual = None
    shots.append(shot(t))


snapshot("① 自动 · 跟随 WorkBuddy（默认）")
pet.auto_mode = "loop60"
shots.append(shot("② 自动 · 60 秒循环"))
pet.follow = False
pet.manual = "idle"
shots.append(shot("③ 手动 · 常态"))
pet.manual = "working"
shots.append(shot("④ 手动 · 工作态"))

CW = max(s[0].width for s in shots)
CH = max(s[0].height for s in shots)
cols = 2
rows = 2
out = Image.new("RGB", (cols * (CW + 14) + 14, rows * (CH + 40) + 14), (16, 18, 24))
d = ImageDraw.Draw(out)
for i, (im, lab, wh) in enumerate(shots):
    r, c = divmod(i, cols)
    x0 = 14 + c * (CW + 14)
    y0 = 14 + r * (CH + 40)
    out.paste(im, (x0, y0))
    d.text((x0 + 2, y0 + CH + 4), "%s   菜单 %d×%d" % (lab, wh[0], wh[1]),
           fill=(226, 233, 245), font=F)
d.text((14, out.height - 24),
       "右键呼出。滑块可**点击**也可**按住左右拖**；尺寸行右侧标实际像素。",
       fill=(170, 182, 198), font=F2)
p1 = os.path.join(ROOT, "STEP12_菜单_滑块式.png")
out.save(p1)
print("   -> %s %s" % (os.path.basename(p1), out.size), flush=True)

# ---------------- 命中与逻辑自测 ----------------
print("\n=== 命中测试 ===")
pet.follow = True
pet.auto_mode = "follow"
pet.size_idx = 1
pet.manual = None
L = pet._menu_layout()
print("  自动模式行数 = %d：%s" % (len(L["rows"]),
      [it.get("key") or it["kind"] for it in L["rows"]]))
for it in L["rows"]:
    if it["segs"]:
        cx = (it["segs"][0][0] + it["segs"][-1][1]) // 2
        cy = it["y"] + it["rh"] // 2
        print("    行 %-6s y=%3d 段=%s ｜ 正中点 (x=%d) 命中 → %s"
              % (it["key"], it["y"], [(a, b) for a, b, _ in it["segs"]], cx,
                 pet._hit_menu(cx, cy, L)))

# 点"手动"
pet._set_slider("mode", 1)
L2 = pet._menu_layout()
print("  点「手动」后：follow=%s manual=%s ⇒ 行 = %s"
      % (pet.follow, pet.manual, [it.get("key") or it["kind"] for it in L2["rows"]]))
pet._set_slider("state", 1)
print("  点「工作态」后：manual=%s（菜单应保持 6 行）" % pet.manual)
pet._set_slider("mode", 0)
print("  点回「自动」后：follow=%s manual=%s auto_mode=%s" % (pet.follow, pet.manual, pet.auto_mode))
pet._set_slider("auto", 1)
print("  点「60 秒循环」后：auto_mode=%s（%d 秒后首次切换）"
      % (pet.auto_mode, int(pet.auto_loop_at - 0)))
# 尺寸滑块（不动真的切尺寸，只验证索引映射）
print("  尺寸段映射：", [(k, SIZES[k]) for k in range(len(SIZES))], "｜ 当前 size_idx=%d px=%d"
      % (pet.size_idx, pet.px))
