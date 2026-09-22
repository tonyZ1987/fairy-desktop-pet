# -*- coding: utf-8 -*-
"""任务完成通知验收（2026-09-22）：样式预览 + **端到端**链路自测。

产出：
  STEP13_任务完成通知.png —— 三种文案长度 / 淡入过程 / 浅色桌面 / 半睁时收到
  + 端到端：写 notify.json → 桌宠 tick 一次 → 确认收到且文件被消费
"""
import json
import os
import sys
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)     # ★ 本脚本在 tools/ 下 ⇒ 仓库根在**上一级**
sys.path.insert(0, ROOT)         # 这样才 import 得到 fairy_pet
os.chdir(ROOT)                   # 工作目录也切到根：缓存 / state.json / 产物都落在根
from fairy_pet import FairyPet, NOTIFY_FILE   # noqa: E402

try:
    F = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
    F2 = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 12)
except Exception:
    F = F2 = ImageFont.load_default()

DARK = (27, 29, 35)
LIGHT = (242, 243, 245)
pet = FairyPet(size=260)
S = pet.win

T1 = "主人，任务已完成。"
T2 = "主人，湖州西凤漾商业策划方案文本已完成，请前往查阅。"
T3 = "主人，湖州西凤漾 XSS-03-02-05A 商业开发策划方案文本（含 PDF 排版核对与规范版本更新）已完成，请前往查阅。"

print("=== 端到端链路 ===")
with open(NOTIFY_FILE, "w", encoding="utf-8") as f:
    json.dump({"text": T2, "ts": time.time(), "secs": 12.0}, f, ensure_ascii=False)
print("  ① 写入 notify.json（模拟我干完活）")
pet.tick()
got = pet.notify_text
print("  ② 桌宠 tick 一次 → notify_text = %r" % got)
print("  ③ notify.json 是否已被消费（应删除）：%s" % (not os.path.exists(NOTIFY_FILE)))
sp = pet._note_span(time.perf_counter())
print("  ④ 当前应显示的不透明度 = %.2f" % (sp[1] if sp else -1))
pet.notify_text = None


def cell(bg, txt, op, label, open_t=1.0):
    ov = pet._overlay(None, None, (txt, op) if txt else None)
    im = Image.fromarray(pet.R.over_desktop(bg, t_ms=1000.0, open_t=open_t,
                                            flicker_op=0.0, ov=ov))
    c = Image.new("RGB", (S + 16, S + 44), bg)
    c.paste(im, (8, 34))
    tc = (215, 222, 235) if sum(bg) < 300 else (60, 64, 74)
    ImageDraw.Draw(c).text((10, 8), label, fill=tc, font=F2)
    return c


CASES = [
    (DARK, T1, 1.0, "① 一句话（1 行）", 1.0),
    (DARK, T2, 1.0, "② 常规（2 行）", 1.0),
    (DARK, T3, 1.0, "③ 长文案（3 行，自动折行）", 1.0),
    (DARK, T2, 0.35, "④ 淡入中 opacity 0.35", 1.0),
    (DARK, T2, 1.0, "⑤ 半睁时也要看得清", 0.0),
    (LIGHT, T2, 1.0, "⑥ 浅色桌面上同样成立", 1.0),
]
tiles = [cell(*c) for c in CASES]
cols = 3
rows = 2
CW, CH = tiles[0].size
out = Image.new("RGB", (cols * CW + (cols + 1) * 8, rows * CH + (rows + 1) * 8 + 26), (16, 18, 26))
d = ImageDraw.Draw(out)
for i, t in enumerate(tiles):
    r, c = divmod(i, cols)
    out.paste(t, (8 + c * (CW + 8), 8 + r * (CH + 8)))
d.text((10, out.height - 22),
       "我写 notify.json → 桌宠读到弹卡 → 停留（默认 12 s，可配）→ 淡出。通知是一次性的，重启不重弹。",
       fill=(170, 182, 198), font=F2)
p = os.path.join(ROOT, "STEP13_任务完成通知.png")
out.save(p)
print("\n   -> %s %s" % (os.path.basename(p), out.size))
