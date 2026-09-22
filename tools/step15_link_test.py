# -*- coding: utf-8 -*-
"""step15_link_test.py —— 端到端链路自测（**离屏**：不建窗口、不要主人操作）

验的是"我写文件 → 桌宠照做"这条链，逐环节：

  ① state.json = idle     → cur_state=idle、open_t=1.00（睁眼）
  ② state.json = working  → cur_state=working、open_t **平滑**降到 0.00（半睁 + 辉光变 01）
  ③ notify.json           → 弹出通知卡、**文件被消费**（删除）、到点自动消失
  ④ state.json = idle     → 回到常态、open_t 回 1.00
  ⑤ ttl 兜底              → 我忘了写 idle 时自动回落
  ⑥ 二次切换              → 对比首次/再次，判断首帧卡顿是不是"一次性预热"

做法：tick() 里只有 `_blit()`（要 DIB/HDC）和 `_force_show()`（要 hwnd）依赖窗口，
把它们打桩成空操作，其余逻辑**原样跑真的**。心跳与错误日志都改写到临时目录，
免得污染主人看的 heartbeat.txt / pet_error.log。

★ 两个**曾经踩过的坑**，写在最前面免得下一个人再踩：
  · 类名是 `FairyPet`，不是 `Pet`（写错会静默退出，见下条）。
  · `fairy_pet` 模块会设 `sys.excepthook` 把未捕获异常**只写进 pet_error.log、不打印** ⇒
    测试脚本里任何拼写错误都表现为"进程无声退出"。所以本脚本自己 try/except 打 traceback。

判据（不问"看起来对不对"，只认数）：
  · 状态切换必须在 **1 次轮询（0.5 s）+ 1 次过渡** 之内完成
  · open_t 必须**单调**，且**归一化斜率** ≤ 1.8
    （归一化斜率 = |Δopen_t| / (Δt / 过渡时长)；缓动是 smoothstep，理论峰值斜率 1.50）
    ★ 为什么判据是 1.8 而不是 1.5：分母的 Δt 取的是**采样时刻**之差，而 open_t 是
      tick() **内部稍早**（~10 ms）用 perf_counter 算出来的 —— 在 20 ms 采样间隔上这个
      偏差能把归一化斜率虚高 ~50%。这是测量分辨率极限，不是动画问题。
      判据的真实用途是**抓"生硬跳变"**：跳变会给出 5~10 的读数（本项目实测 0.39 的单步
      ≈ 9），1.8 完全抓得住，又不至于被时间戳抖动误报。
    ★ 不能用"固定阈值"判平滑 —— 采样间隔本身有抖动，固定阈值必误报。
  · 单帧间隔 < 60 ms（超过就是掉帧，要查首帧/GC）
  · notify.json 必须被删除（一次性消费）
"""
import json
import os
import sys
import tempfile
import time
import traceback

# ★ 本脚本在 tools/ 下 ⇒ 先把仓库根加进 sys.path，否则 import 不到 fairy_pet
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fairy_pet as FP   # noqa: E402

STATE = FP.STATE_FILE
NOTIFY = FP.NOTIFY_FILE
TMPDIR = tempfile.mkdtemp(prefix="fairy_test_")

FAIL = []
NOTES = []


def check(ok, msg):
    print("    %s %s" % ("✅" if ok else "❌", msg))
    if not ok:
        FAIL.append(msg)
    return ok


def note(msg):
    NOTES.append(msg)
    print("    ⓘ %s" % msg)


def wstate(st, note_="", ttl=2700):
    """原子写 state.json（与真实信号完全同格式）"""
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"state": st, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "ttl": ttl, "note": note_}, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def drive(p, secs, samples=None):
    """真实推 secs 秒的 tick（tick 内部走 perf_counter，没法快进）。

    samples 收 (open_t, 时刻) —— 时刻必须一起收，否则判"平滑"时
    无法把采样间隔归一化，会被自己的采样抖动骗到。
    """
    t_end = time.perf_counter() + secs
    while time.perf_counter() < t_end:
        p.tick()
        if samples is not None:
            samples.append((p.open_t, time.perf_counter()))
        time.sleep(1.0 / 240.0)


def analyse(samples, dur):
    """→ (单调?, 归一化斜率峰值, 单帧最大间隔 ms, 过渡段用时 ms)"""
    if len(samples) < 3:
        return True, 0.0, 0.0, 0.0
    mono_dec = all(b[0] <= a[0] + 1e-9 for a, b in zip(samples, samples[1:]))
    mono_inc = all(b[0] >= a[0] - 1e-9 for a, b in zip(samples, samples[1:]))
    slope = 0.0
    gap = 0.0
    for (a, ta), (b, tb) in zip(samples, samples[1:]):
        dt = tb - ta
        if dt <= 0:
            continue
        slope = max(slope, abs(b - a) / (dt / dur))
        gap = max(gap, dt)
    t_first = None
    t_last = None
    for (v, t) in samples:
        # ★ 只统计"正在过渡"的中间值（0.001~0.999）——首尾两端的静止段不算，
        #   否则会把"等轮询"和"到位后干等"的时间一起算进过渡时长（我踩过）。
        if 0.001 < v < 0.999:
            if t_first is None:
                t_first = t
            t_last = t
    span = (t_last - t_first) * 1000.0 if (t_first is not None and t_last is not None) else 0.0
    return (mono_dec or mono_inc), slope, gap * 1000.0, span


def main():
    print("=" * 70)
    print("Fairy 链路自测（离屏，不建窗口、不需要窗口）")
    print("  过渡时长：合眼 T_CLOSE=%.2fs ｜ 睁眼 T_OPEN=%.2fs" % (FP.T_CLOSE, FP.T_OPEN))
    print("  轮询周期：state.json 0.5s ｜ notify.json 0.5s")

    FP.BEAT = os.path.join(TMPDIR, "heartbeat_test.txt")
    FP.ERRLOG = os.path.join(TMPDIR, "pet_error_test.log")
    if os.path.exists(NOTIFY):
        os.remove(NOTIFY)

    print("\n■ 构造桌宠（离屏）…", end=" ", flush=True)
    t0 = time.perf_counter()
    p = FP.FairyPet(size=260)
    print("%.2f s" % (time.perf_counter() - t0))

    blits = {"n": 0}
    forced = []
    p._blit = lambda b, a: blits.__setitem__("n", blits["n"] + 1)
    p._force_show = lambda why="": (forced.append(why), True)[1]

    # ---------- ① 常态 ----------
    print("\n① state.json = idle（起手必须是睁眼）")
    wstate("idle", "自测：常态")
    drive(p, 0.9)
    check(p.cur_state == "idle", "cur_state = %s（期望 idle）" % p.cur_state)
    check(abs(p.open_t - 1.0) < 1e-6, "open_t = %.4f（期望 1.0000）" % p.open_t)
    check(blits["n"] > 60, "推了 %d 帧（>60 说明循环在跑）" % blits["n"])

    # ---------- ② 首次切工作态 ----------
    print("\n② state.json = working（我开工 → 半睁 + 辉光变 01）")
    sam = []
    wstate("working", "自测：任务开始")
    drive(p, 2.2, sam)
    check(p.cur_state == "working", "cur_state = %s（期望 working）" % p.cur_state)
    check(abs(p.open_t) < 1e-6, "open_t = %.4f（期望 0.0000 = 半睁到底）" % p.open_t)
    mono, slope, gap, span = analyse(sam, FP.T_CLOSE)
    check(mono, "open_t 单调（无来回跳）")
    check(slope <= 1.8, "归一化斜率峰值 %.2f（smoothstep 理论 1.50，判据 ≤1.8）" % slope)
    check(gap < 60.0, "单帧最大间隔 %.1f ms（判据 <60ms）" % gap)
    note("首次切工作态：过渡段实测 %.0f ms（标称 %.0f ms）｜ 最大帧间隔 %.1f ms"
         % (span, FP.T_CLOSE * 1000, gap))
    first_gap = gap

    # ---------- ③ 通知 ----------
    print("\n③ 写 notify.json（我干完活 → 弹通知卡）")
    tmp = NOTIFY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"text": "主人，自测任务已完成，请前往查阅。", "secs": 3.0},
                  f, ensure_ascii=False)
    os.replace(tmp, NOTIFY)
    drive(p, 0.8)
    check(p.notify_text == "主人，自测任务已完成，请前往查阅。",
          "notify_text = %r" % (p.notify_text or ""))
    check(abs(p.notify_secs - 3.0) < 1e-6, "停留时长 = %.1f s（期望 3.0）" % p.notify_secs)
    check(not os.path.exists(NOTIFY), "notify.json 已被消费（读完即删）")
    sp = p._note_span(time.perf_counter())
    check(sp is not None and sp[1] > 0.5, "卡片不透明度 = %.2f（淡入到 1.0）" % (sp[1] if sp else -1))
    check("notify" in forced, "弹卡前强制显示窗口（_force_show：%s）" % forced)
    drive(p, 3.4)
    check(p._note_span(time.perf_counter()) is None, "3s 后自动消失（一次性，不赖着）")

    # ---------- ④ 回常态 ----------
    print("\n④ state.json = idle（我收工 → 睁眼）")
    sam = []
    wstate("idle", "自测：收工")
    drive(p, 2.0, sam)
    check(p.cur_state == "idle", "cur_state = %s（期望 idle）" % p.cur_state)
    check(abs(p.open_t - 1.0) < 1e-6, "open_t = %.4f（期望 1.0000）" % p.open_t)
    mono, slope, gap, span = analyse(sam, FP.T_OPEN)
    check(mono, "open_t 单调")
    check(slope <= 1.8, "归一化斜率峰值 %.2f" % slope)
    check(gap < 60.0, "单帧最大间隔 %.1f ms" % gap)

    # ---------- ⑤ ttl 兜底 ----------
    print("\n⑤ ttl 兜底（我忘了写 idle 时，它不能一直半睁）")
    wstate("working", "自测：故意超时", ttl=1)
    drive(p, 3.0)
    check(p.cur_state == "idle",
          "ttl=1s 超时后自动回落：cur_state = %s" % p.cur_state)

    # ---------- ⑥ 二次切换（对比首次）----------
    print("\n⑥ 二次切工作态（看首帧卡顿是不是一次性的）")
    sam = []
    wstate("working", "自测：第二轮")
    drive(p, 2.2, sam)
    check(p.cur_state == "working", "cur_state = %s" % p.cur_state)
    mono, slope, gap, span = analyse(sam, FP.T_CLOSE)
    check(gap < 60.0, "单帧最大间隔 %.1f ms（对比首次 %.1f ms）" % (gap, first_gap))
    if first_gap > 60.0 >= gap:
        note("首次卡顿一次、之后正常 ⇒ 是**一次性预热**，应在启动时预热掉")
    elif gap > 60.0:
        note("⚠ 二次切换仍卡顿（%.1f ms）⇒ 不是预热问题，要查 GC/渲染" % gap)

    # ---------- 收尾 ----------
    wstate("idle", "")
    for f_ in (NOTIFY, NOTIFY + ".tmp"):
        if os.path.exists(f_):
            os.remove(f_)
    elog = os.path.join(TMPDIR, "pet_error_test.log")
    if os.path.exists(elog):
        txt = open(elog, encoding="utf-8", errors="replace").read()
        if txt.strip():
            print("\n⚠ 测试期间产生了错误日志：")
            print(txt[-900:])
            FAIL.append("测试期间有错误日志")

    print("\n" + "=" * 70)
    if FAIL:
        print("❌ 未通过 %d 项：" % len(FAIL))
        for m in FAIL:
            print("   ·", m)
        return 1
    print("✅ 全链路通过：idle → working → 通知 → idle → 超时兜底 → 二次切换")
    print("   共推 %d 帧，实际渲染 %d 次，零错误日志。" % (p._tick_n, blits["n"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise            # ★ sys.exit() 正常路径，不属于错误（不加这句会打出一串假 traceback）
    except BaseException:
        # ★ 必须自己打：fairy_pet 的 excepthook 只写日志到 pet_error.log、不打印，
        #   所以脚本里任何拼写错误都会表现为"进程无声退出"（本项目踩过）。
        traceback.print_exc()
        sys.exit(2)
