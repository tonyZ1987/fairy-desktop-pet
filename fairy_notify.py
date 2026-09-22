# -*- coding: utf-8 -*-
"""fairy 通知器 —— 我（AI）干完活时调用它，桌宠就会弹一张卡片告诉主人。

用法（都在 pet-v2 目录下跑）：

    python fairy_notify.py "主人，湖州西凤漾方案文本已完成，请前往查阅。"     # 只弹卡
    python fairy_notify.py --done "主人，XX 已完成，请前往查阅。"           # 弹卡 + 收工（眼睛睁开）
    python fairy_notify.py --work "开始处理 XX（约 3 分钟）"                # 开工（半睁）+ 弹卡
    python fairy_notify.py --secs 25 "长一点，多看几眼"                     # 卡片多停一会儿

设计要点：
  · **原子写**（先写 .tmp 再 os.replace）—— 否则桌宠可能读到半个 JSON，那一轮通知就丢了。
  · 通知是一次性的：桌宠读完会把 notify.json 删掉，重启不会重复弹。
  · 不碰网络、不碰窗口，只写两个文件 ⇒ 桌宠开着就能收到，没开也不报错（下次启动不会补弹）。
"""
import argparse
import json
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
NOTIFY = os.path.join(BASE, "notify.json")
STATE = os.path.join(BASE, "state.json")


def atomic_write(path, obj):
    """先写 .tmp 再替换 —— 避免"桌宠正好在读、读到半个文件"。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="给 fairy 桌宠发一条通知")
    ap.add_argument("text", nargs="?", help="要显示的文本（自动折行，最多 3 行）")
    ap.add_argument("--test", action="store_true", help="发一条内置的测试通知（给双击的 vbs 用）")
    ap.add_argument("--done", action="store_true", help="同时收工（state.json → idle，眼睛睁开）")
    ap.add_argument("--work", action="store_true", help="同时开工（state.json → working，半睁眼）")
    ap.add_argument("--secs", type=float, default=12.0, help="卡片停留秒数（默认 12）")
    ap.add_argument("--ttl", type=float, default=2700.0, help="working 的兜底超时秒数（默认 2700）")
    a = ap.parse_args()

    text = a.text
    if a.test and not text:
        text = "主人，测试通知。链路正常，Fairy 待命中。"
    if not text:
        ap.error("要么给一句文本，要么加 --test")

    atomic_write(NOTIFY, {"text": text, "ts": time.time(), "secs": a.secs})

    if a.done or a.work:
        st = "working" if a.work else "idle"
        atomic_write(STATE, {"state": st, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                             "ttl": a.ttl, "note": text[:40]})
    print("已发给 fairy：%s" % text)
    if a.done or a.work:
        print("  同时把 state.json 置为 %s" % ("working" if a.work else "idle"))


if __name__ == "__main__":
    main()
