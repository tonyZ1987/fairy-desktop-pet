# -*- coding: utf-8 -*-
"""重烘三档尺寸的图层缓存（改过烘焙内容 / BAKE_VERSION 之后跑一次）。

用法：`python fairy_bake.py`            重烘 200/260/320 三档
      `python fairy_bake.py 260`        只烘某一档
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE                      # ★ 本脚本就在 pet-v2 根目录（_work 里的脚本才要 dirname 一级）
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from fairy_layers import FastMascot          # noqa: E402
import fairy_layers as FL                    # noqa: E402
import dsh_mascot as M                       # noqa: E402

sizes = [int(a) for a in sys.argv[1:]] or [200, 260, 320]
print("BAKE_VERSION = %d ｜ 辉光相位 %d ｜ 摩尔纹相位 %d ｜ 数字 %d 个"
      % (FL.BAKE_VERSION, FL.GLOW_PHASES, M.MOIRE_PHASES, __import__("fairy_digits").N_DIGITS),
      flush=True)
for sz in sizes:
    t0 = time.perf_counter()
    fm = FastMascot(size=sz, ss=2, cache_dir="_cache")
    dt = time.perf_counter() - t0
    print("  %3d px ｜ 画布 %d ｜ %.1f s" % (sz, fm.N, dt), flush=True)
    del fm
print("完成。")
