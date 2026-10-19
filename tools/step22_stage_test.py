# -*- coding: utf-8 -*-
u"""step22_stage_test.py —— **阶段进度写盘**的离屏自测（不建窗口、不碰真状态）。

覆盖 `fairy_pet._prog_from_stage()` 的四条规矩：
  ① 有 todos ⇒ 写 `<项目>` 专属进度文件（`auto: true`）
  ② 这一轮 AI 手工上报过 ⇒ **一个字都不动**
  ③ 这一轮关掉（stage 为 None）⇒ 把自己那条标 `done`（否则会多挂 5 分钟）
  ④ 没有项目 ⇒ 什么都不写

跑法：`python _work/step22_stage_test.py`
★ 全程把 `PROG_DIR` 指到临时目录，**绝不碰**真实的 `_prog/`。
"""
from __future__ import print_function

import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS = []


def check(name, cond, extra=u""):
    print(u"   [%s] %s%s" % (u"OK " if cond else u"!! ", name, (u"   " + extra) if extra else u""))
    if not cond:
        FAILS.append(name)


class _Stub(object):
    def __init__(self, stage):
        self._s = stage

    def stage(self, now=None):
        return self._s


def main():
    import fairy_pet as P

    tmp = tempfile.mkdtemp(prefix=u"fairy_stage_")
    old_dir = P.PROG_DIR
    P.PROG_DIR = tmp
    try:
        pet = object.__new__(P.FairyPet)
        pet.sense = True
        pet.follow = True
        pet.auto_mode = "follow"
        pet._bar_epoch = 0.0
        pet._stage_at = 0.0
        pet._stage_pct = None

        path = P.proj_file(u"e-AI成图实践-Fairy")
        stage = {u"project": u"e-AI成图实践-Fairy", u"percent": 62.0, u"total": 4,
                 u"completed": 2, u"active": 1, u"label": u"丙", u"ts": time.time()}

        print(u"\n① 有 todos ⇒ 写进项目专属进度文件")
        pet.act = _Stub(dict(stage))
        pet._prog_from_stage()
        d = json.load(open(path, encoding="utf-8"))
        check(u"percent = 62", abs(float(d.get("percent", 0)) - 62.0) < 0.01)
        check(u"打上 auto 标记", d.get("auto") is True)
        check(u"planned = True、done = False",
              d.get("planned") is True and d.get("done") is False)
        check(u"带 started_at（排队逻辑要用）", float(d.get("started_at") or 0) > 0)
        check(u"note 里有 2/4", u"2/4" in (d.get("note") or u""), d.get("note"))

        print(u"\n② 本轮 AI 手工上报过 ⇒ 不覆盖")
        manual = {u"percent": 90.0, u"ts": time.time(), u"planned": True,
                  u"project": u"e-AI成图实践-Fairy", u"stages": 3, u"item": 3, u"phase": 3}
        json.dump(manual, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        pet._stage_at = 0.0
        pet._stage_pct = None
        pet.act = _Stub(dict(stage, percent=10.0))
        pet._prog_from_stage()
        d = json.load(open(path, encoding="utf-8"))
        check(u"手工的 90% 还在", abs(float(d.get("percent", 0)) - 90.0) < 0.01,
              u"实际=%s" % d.get("percent"))
        check(u"没被标成 auto", d.get("auto") is not True)

        print(u"\n③ 这一轮关掉 ⇒ 标 done（收工要撤）")
        #   先让 auto 重新接管（把 _bar_epoch 推到手工那条之后）
        pet._bar_epoch = time.time() + 1
        pet._stage_at = 0.0
        pet._stage_pct = None
        pet.act = _Stub(dict(stage, percent=62.0))
        pet._prog_from_stage()
        d = json.load(open(path, encoding="utf-8"))
        check(u"auto 重新接管", d.get("auto") is True and d.get("done") is False)
        pet.act = _Stub(None)
        pet._stage_at = 0.0
        pet._prog_from_stage()
        d = json.load(open(path, encoding="utf-8"))
        check(u"标成 done（不再算未交付任务）", d.get("done") is True, str(d))
        check(u"done 之后不再反复写", pet._stage_pct is None)

        print(u"\n④ 没有项目 ⇒ 什么都不写")
        pet.act = _Stub({u"project": u"", u"percent": 50.0})
        pet._stage_at = 0.0
        before = os.listdir(tmp)
        pet._prog_from_stage()
        check(u"没多出文件", sorted(os.listdir(tmp)) == sorted(before))

        # ============================================================ ⑤
        print(u"\n⑤ 交付完成 ⇒ 正好 100% + 弹卡（主人 2026-10-19 要的那条）")
        nf, sf = os.path.join(tmp, "notify.json"), os.path.join(tmp, "state.json")
        old_nf, old_sf = P.NOTIFY_FILE, P.STATE_FILE
        P.NOTIFY_FILE, P.STATE_FILE = nf, sf
        try:
            pet.prog_done = False
            pet.prog_settle = False
            pet._prog_mtime = 0.0
            pet._bar_epoch = 0.0
            pet._stage_pct = None
            fin = {u"project": u"e-AI成图实践-Fairy", u"total": 6,
                   u"completed": 6, u"turn": 2, u"closed_at": time.time()}
            check(u"弹了卡", pet._auto_finish(fin) is True)
            card = json.load(open(nf, encoding="utf-8"))
            check(u"卡面用的是主人定稿的句式",
                  card.get("text") == u'主人，"Fairy"工作空间的项目已完成6项修改，请前往查阅。',
                  card.get("text"))
            d = json.load(open(path, encoding="utf-8"))
            check(u"进度 = 100 且 done + settle",
                  abs(float(d.get("percent", 0)) - 100.0) < 0.01
                  and d.get("done") is True and d.get("settle") is True,
                  str(d))
            st = json.load(open(sf, encoding="utf-8"))
            check(u"state.json = working（100% 挂着，靠 settle 收尾）",
                  st.get("state") == u"working")
            #   已经交过（桌宠读到了 done+settle）⇒ 不许再弹第二张
            pet.prog_done = True
            pet.prog_settle = True
            pet._prog_mtime = time.time()
            check(u"已交过 ⇒ 不重复弹卡", pet._auto_finish(fin) is False)
        finally:
            P.NOTIFY_FILE, P.STATE_FILE = old_nf, old_sf
    finally:
        P.PROG_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)

    print(u"\n" + u"=" * 60)
    if FAILS:
        print(u"★ 失败 %d 项：%s" % (len(FAILS), u" / ".join(FAILS)))
        return 1
    print(u"全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
