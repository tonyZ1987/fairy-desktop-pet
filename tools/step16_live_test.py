# -*- coding: utf-8 -*-
"""step16_live_test.py —— **现场**测试（桌宠必须是主人双击启动的那个真实进程）

与 step15 的区别：
  · step15 是"离屏逻辑自测"（不建窗口，验代码逻辑）
  · 本脚本是"现场联调"（对着**真窗口**发信号，验端到端）

它与桌宠之间**只靠文件说话**，不注入、不挂钩：
    写 state.json  → 眼睛睁/半睁（辉光变 01）
    写 notify.json → 弹通知卡

同时每步都抓一次 `heartbeat.txt` 当**客观证据**（心跳里有 state= / open_t= 字段），
所以"它到底切没切"不靠肉眼，靠数。

用法：主人双击 `restart_fairy.vbs` 之后，我跑这个脚本；主人在旁边看屏幕即可。
"""
import json
import os
import sys
import time
import traceback

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tools/ 的上一级 = 仓库根
STATE = os.path.join(BASE, "state.json")
NOTIFY = os.path.join(BASE, "notify.json")
BEAT = os.path.join(BASE, "heartbeat.txt")

STEPS = [
    (0.0, "① 复位为常态 idle", "眼睛睁开、周边是柔和辉光"),
    (3.0, "② 切工作态 working", "眼睛半睁；**辉光那一圈变成 0/1 数字流**（约 0.7s 平滑过渡）"),
    (9.0, "③ 发通知 notify", "弹出一张通知卡（停 12 秒后淡出）"),
    (23.0, "④ 切回常态 idle", "眼睛睁开、数字流变回辉光"),
]
TOTAL = 28.0


def wjson(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def beat():
    try:
        return open(BEAT, encoding="utf-8").read().strip()
    except Exception:
        return "（读不到心跳）"


def alive():
    """心跳 3 秒内更新过 ⇒ 桌宠在跑"""
    try:
        return (time.time() - os.path.getmtime(BEAT)) < 3.0
    except Exception:
        return False


def brief(b):
    """把心跳缩成关心的几个字段"""
    out = {}
    for kv in b.replace("  ", " ").split(" "):
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k] = v
    return "%s ｜ open_t=%s ｜ fps=%s ｜ 帧耗时=%s ｜ err=%s" % (
        out.get("state", "?"), out.get("open_t", "?"),
        out.get("fps", "?"), out.get("cost", "?"), out.get("err", "?"))


def main():
    if not alive():
        print("❌ 桌宠没在跑（heartbeat.txt 超过 3 秒没更新）。")
        print("   → 请主人先双击 restart_fairy.vbs，再跑这个脚本。")
        return 1

    print("=" * 72)
    print("Fairy 现场测试（桌宠已在运行）")
    print("  现在心跳：%s" % brief(beat()))
    print()
    print("  主人请看屏幕，接下来 %d 秒会依次发生：" % int(TOTAL))
    for t, what, see in STEPS:
        print("    %5.1fs  %-22s → %s" % (t, what, see))
    print("=" * 72, flush=True)

    snaps = []
    t0 = time.perf_counter()
    for k, (at, what, see) in enumerate(STEPS):
        wait = at - (time.perf_counter() - t0)
        if wait > 0:
            time.sleep(wait)

        if k == 0:
            wjson(STATE, {"state": "idle", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "ttl": 2700, "note": "现场测试：复位"})
        elif k == 1:
            wjson(STATE, {"state": "working", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "ttl": 2700, "note": "现场测试：开工"})
        elif k == 2:
            wjson(NOTIFY, {"text": "主人，链路测试已完成，请前往查阅。", "secs": 12})
        elif k == 3:
            wjson(STATE, {"state": "idle", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "ttl": 2700, "note": "现场测试：收工"})

        print("[%6.1fs] 发出：%s" % (time.perf_counter() - t0, what), flush=True)
        # 等心跳刷新（每 60 帧 ≈ 1 秒）后抓快照
        time.sleep(2.2)
        b = beat()
        snaps.append((what, b))
        print("           心跳：%s" % brief(b), flush=True)

    # 收尾：复位成常态，并确保没有残留通知
    wjson(STATE, {"state": "idle", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "ttl": 2700, "note": ""})
    for p in (NOTIFY, NOTIFY + ".tmp"):
        if os.path.exists(p):
            os.remove(p)

    print("\n" + "=" * 72)
    print("客观证据（心跳快照，这是桌宠自己写的，不是我的推测）：")
    for what, b in snaps:
        print("  %-22s %s" % (what, brief(b)))

    # 判读完
    ok = True
    st = []
    for what, b in snaps:
        d = dict(kv.split("=", 1) for kv in b.replace("  ", " ").split(" ") if "=" in kv)
        st.append((what, d.get("state"), d.get("open_t"), d.get("err")))
    for what, s, o, e in st:
        try:
            if e and int(e) != 0:
                ok = False
                print("  ⚠ %s 期间 err=%s（有渲染失败）" % (what, e))
        except Exception:
            pass
    print()
    print("✅ 已把四个信号全部发出，桌宠侧的心跳如上。" if ok else "❌ 心跳里有 err，需查日志")
    print("   通知卡那一步（③）心跳**看不出来**——必须靠主人肉眼确认。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.exit(2)
