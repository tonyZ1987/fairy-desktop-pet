# -*- coding: utf-8 -*-
u"""step21_dsh_bridge_test.py —— 「DSH ↔ 桌宠」桥接的**离屏自测**（不建窗口、不碰真状态）。

跑法（用桌宠那个解释器）：

    <venv python> _work/step21_dsh_bridge_test.py

覆盖什么
--------
A. `DshActivityWatch` 的判据：缓存开/关、刚收工尾巴、只有钩子事件、失效兜底
B. `MergeWatch`：两个来源合并、`last_dir` 认"最近动过的那一方"、`per_proj` 合表
C. `fairy_dsh_hook`：真跑子进程（喂 JSON payload）→ 退出码 0 / 注入 JSON / 事件流
   / "每会话只注入一次" / `fairy_dsh_off` 静音
D. 安装器的 YAML 补丁：`render_block` 能被 js-yaml 解析、`strip_block` 幂等

★ 全部用**临时目录**（`tempfile.mkdtemp`）+ 环境变量隔离，**绝不写**真实的
  `dsh_events.jsonl` / `state.json` / `progress.json`。
"""
from __future__ import print_function

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILS = []


def check(name, cond, extra=u""):
    mark = u"OK " if cond else u"!! "
    print(u"   [%s] %s%s" % (mark, name, (u"   " + extra) if extra else u""))
    if not cond:
        FAILS.append(name)


def write_cache(path, cwd, open_turn_seq, todos=None):
    u"""写一份最小的"DSH 投影缓存"（只保留感知要读的字段）。"""
    d = {"version": 7,
         "record": {"identity": {"cwd": cwd},
                    "rows": {"turnBoundary": {"ver": 2, "seq": 1,
                                              "val": {"openTurnStartSeq": open_turn_seq,
                                                      "lastTurn": 1}},
                             "todos": {"ver": 2, "seq": 1, "val": todos},
                             "sessionStats": {"ver": 1, "seq": 1,
                                              "val": {"openStep": {"turn": 1, "step": 3,
                                                                   "startTime": int(time.time() * 1000)}}}}}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def main():
    import fairy_activity_dsh as D
    import fairy_activity as FA

    tmp = tempfile.mkdtemp(prefix="fairy_dsh_test_")
    croot = os.path.join(tmp, "cache")
    os.makedirs(croot)
    evfile = os.path.join(tmp, "events.jsonl")
    try:
        # ================================================================ A
        print(u"\nA. DshActivityWatch 判据")
        w = D.DshActivityWatch(cache_root=croot, event_file=evfile, scan=0.0)
        t0 = time.time()
        check(u"空目录 ⇒ 空闲", not w.active(now=t0))

        pth = os.path.join(croot, "s-open.json")
        write_cache(pth, r"E:\AI成图实践\Fairy", 3)
        check(u"缓存 openTurnStartSeq=3 ⇒ 工作态", w.active(now=t0 + 1))
        check(u"项目识别 = e-AI成图实践-Fairy", w.last_dir == u"e-AI成图实践-Fairy",
              u"实际=%r" % (w.last_dir,))

        # 关掉这一轮：观察者应保留 ASSIST_HOLD_S 的尾巴
        write_cache(pth, r"E:\AI成图实践\Fairy", None)
        t1 = t0 + 2
        check(u"刚关闭的那一轮仍在尾巴里", w.active(now=t1),
              u"hint=%s" % (w.hint,))
        check(u"尾巴 25 s 之后回常态",
              not w.active(now=t1 + D.ASSIST_HOLD_S + 2))
        check(u"项目仍记着（进度条归属要用）",
              w.last_dir == u"e-AI成图实践-Fairy")

        # ---- ★ A2：阶段进度（todos 投影折算）----
        print(u"\nA2. stage() 阶段进度")
        pth2 = os.path.join(croot, "s-stage.json")
        write_cache(pth2, r"E:\AI成图实践\Fairy", 5, todos=[
            {"content": u"甲", "status": "completed"},
            {"content": u"乙", "status": "completed"},
            {"content": u"丙", "status": "in_progress"},
            {"content": u"丁", "status": "pending"},
        ])
        w2 = D.DshActivityWatch(cache_root=croot, event_file=evfile, scan=0.0)
        t2 = time.time()
        w2.active(now=t2)
        st = w2.stage(now=t2 + 0.1)
        check(u"4 项（2 完成 + 1 进行中）⇒ 57%（工作段 0~90，向上取整）",
              bool(st) and abs(st["percent"] - 57.0) < 0.01,
              u"实际=%r" % (st and st.get("percent"),))
        check(u"总数/完成数/进行中数都对",
              bool(st) and (st["total"], st["completed"], st["active"]) == (4, 2, 1))
        check(u"label = 进行中的那一项", bool(st) and st["label"] == u"丙")
        #   全部完成 ⇒ 100%
        write_cache(pth2, r"E:\AI成图实践\Fairy", 5, todos=[
            {"content": u"甲", "status": "completed"},
            {"content": u"乙", "status": "completed"},
        ])
        st = w2.stage(now=time.time() + 1)
        check(u"全部完成 ⇒ 90%（还在写回复）", bool(st) and abs(st["percent"] - 90.0) < 0.01,
              u"实际=%r" % (st and st.get("percent"),))
        #   todos=null（这一轮没拆步骤）⇒ None（不猜）
        write_cache(pth2, r"E:\AI成图实践\Fairy", 5, todos=None)
        check(u"todos=null ⇒ 不给进度（不猜）", w2.stage(now=time.time() + 2) is None)

        # ---- ★ A3：finished()（"回复+交付完"的判据 → 100% + 弹卡）----
        print(u"\nA3. finished() 交付判据")
        pth3 = os.path.join(croot, "s-fin.json")
        done3 = [{"content": u"甲", "status": "completed"},
                 {"content": u"乙", "status": "completed"}]
        write_cache(pth3, r"E:\AI成图实践\Fairy", 9, todos=done3)
        w3 = D.DshActivityWatch(cache_root=croot, event_file=evfile, scan=0.0)
        tf = time.time()
        w3.active(now=tf)                      # 先"看见它开着"
        write_cache(pth3, r"E:\AI成图实践\Fairy", None, todos=done3)
        w3.active(now=tf + 1)                  # 再"看见它关了"
        fin = w3.finished(now=tf + 2)
        check(u"turn 关掉 + 清单全绿 ⇒ 报一次交付",
              bool(fin) and fin["project"] == u"e-AI成图实践-Fairy" and fin["total"] == 2,
              str(fin))
        check(u"同一条不重复报（不会弹两张卡）", w3.finished(now=tf + 2.5) is None)
        #   还有没做完的 ⇒ 不算交付
        pth4 = os.path.join(croot, "s-half.json")
        half = [{"content": u"甲", "status": "completed"},
                {"content": u"乙", "status": "pending"}]
        write_cache(pth4, r"E:\AI成图实践\Fairy", 11, todos=half)
        w4 = D.DshActivityWatch(cache_root=croot, event_file=evfile, scan=0.0)
        th = time.time()
        w4.active(now=th)
        write_cache(pth4, r"E:\AI成图实践\Fairy", None, todos=half)
        w4.active(now=th + 1)
        check(u"清单还有 pending ⇒ 不算交付（也不弹卡）", w4.finished(now=th + 2) is None)

        # 只有钩子事件（缓存还没落盘）
        tmp2 = tempfile.mkdtemp(prefix="fairy_dsh_ev_")
        ev2 = os.path.join(tmp2, "events.jsonl")
        with open(ev2, "w", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "ev": "user", "s": "sess-x",
                                "p": "e-AI成图实践-Fairy"}) + u"\n")
        w2 = D.DshActivityWatch(cache_root=os.path.join(tmp2, "nocache"),
                                event_file=ev2, scan=0.0)
        t2 = time.time()
        check(u"只有 user 事件 ⇒ 工作态（开单宽限）", w2.active(now=t2))
        #   ★ 宽限（120 s）过后，感知是"**这一轮眼看就关了**"⇒ 先给 25 s 尾巴，再回常态
        #     （真实场景里每秒扫一次，所以尾巴总是从"真关掉"那一刻算起）。
        check(u"宽限过后进入 25 s 尾巴", w2.active(now=t2 + D.STALE_PROMPT_S + 2))
        check(u"尾巴走完 ⇒ 常态",
              not w2.active(now=t2 + D.STALE_PROMPT_S + D.ASSIST_HOLD_S + 5))
        check(u"user_at 被记住了（收尾让位用）", w2.user_at > 0)
        shutil.rmtree(tmp2, ignore_errors=True)

        # ================================================================ B
        print(u"\nB. MergeWatch 合并")
        wa = D.DshActivityWatch(cache_root=croot, event_file=evfile, scan=0.0)
        wb = FA.ActivityWatch(only=[u"e-AI成图实践-Fairy"], scan=0.0)
        m = FA.MergeWatch([wa, wb])
        check(u"接口齐备：active/info/last_dir/per_proj/user_at",
              all(hasattr(m, k) for k in
                  ("active", "info", "last_dir", "per_proj", "user_at", "age", "ok")))
        m.active()
        check(u"info() 里两个来源都在", u"DSH:" in m.info() and u"+" in m.info(),
              m.info()[:80])
        check(u"per_proj 是合并出来的表（含 DSH 那个项目）",
              u"e-AI成图实践-Fairy" in (m.per_proj or {}))

        # ================================================================ C
        print(u"\nC. fairy_dsh_hook 真跑子进程")
        hook = os.path.join(ROOT, "fairy_dsh_hook.py")
        ev3 = os.path.join(tmp, "events_hook.jsonl")
        env = dict(os.environ)
        env[u"FAIRY_DSH_LOG"] = ev3
        env.pop(u"FAIRY_DSH_SELFTEST", None)
        py = sys.executable

        def run_hook(ev, sid, cwd=r"E:\AI成图实践\Fairy", extra=None):
            payload = {"hook_event_name": ev, "session_id": sid, "cwd": cwd}
            if extra:
                payload.update(extra)
            r = subprocess.run([py, hook],
                               input=(json.dumps(payload, ensure_ascii=False) + u"\n").encode("utf-8"),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, timeout=60)
            return r.returncode, (r.stdout or b"").decode("utf-8", "replace")

        #   ★ 会话 id 必须**每次跑都不同**：钩子"每会话只注入一次"的标记存在 %TEMP%，
        #     用固定 id 会让第二次跑直接不注入（自测把自己测红，本机实测踩过）。
        rid = str(int(time.time() * 1000))[-7:]
        S1, S2, S3 = "t1-" + rid, "t2-" + rid, "t3-" + rid
        rc, out = run_hook("SessionStart", S1)
        d = json.loads(out) if out.strip().startswith("{") else {}
        ac = ((d.get("hookSpecificOutput") or {}).get("additionalContext") or u"")
        check(u"SessionStart：退出码 0", rc == 0)
        check(u"SessionStart：注入正文非空 + 事件名正确",
              bool(ac) and (d.get("hookSpecificOutput") or {}).get("hookEventName") == "SessionStart",
              u"%d 字符" % len(ac))
        rc, out = run_hook("UserPromptSubmit", S1, extra={"prompt": "hello"})
        check(u"同一会话的 UserPromptSubmit：**不重复注入**", rc == 0 and out.strip() == u"",
              u"stdout=%r" % out[:40])
        rc, out = run_hook("UserPromptSubmit", S2, extra={"prompt": "新会话"})
        check(u"新会话：注入一次", rc == 0 and out.strip().startswith("{"))
        rc, out = run_hook("Stop", S1, extra={"stop_hook_active": False})
        check(u"Stop：退出码 0、不注入", rc == 0 and out.strip() == u"")

        rows = [json.loads(x) for x in open(ev3, encoding="utf-8").read().splitlines() if x.strip()]
        check(u"事件流写了 4 条", len(rows) == 4, u"实际=%d" % len(rows))
        check(u"事件名映射正确（session/user/stop）",
              [r["ev"] for r in rows] == ["session", "user", "user", "stop"])
        check(u"每条都带项目标识",
              all(r.get("p") == u"e-AI成图实践-Fairy" for r in rows), str(rows[0]))

        # 静音开关
        off = os.path.join(ROOT, "fairy_dsh_off")
        created = False
        try:
            if not os.path.exists(off):
                open(off, "w").close()
                created = True
            n0 = len(open(ev3, encoding="utf-8").read().splitlines())
            rc, out = run_hook("UserPromptSubmit", S3, extra={"prompt": "x"})
            n1 = len(open(ev3, encoding="utf-8").read().splitlines())
            check(u"fairy_dsh_off 存在 ⇒ 完全不动作", rc == 0 and n1 == n0 and out.strip() == u"")
        finally:
            if created:
                try:
                    os.remove(off)
                except Exception:
                    pass

        # ================================================================ D
        print(u"\nD. 安装器的 YAML 补丁")
        try:
            import dsh_bridge_install as INS
            block = INS.render_block(r"C:\Users\me\.dsh\profiles\desktop\hooks.json")
            check(u"render_block 含插件名与 configPath",
                  "@deepseek-ai/dsh-hooks-claude-code" in block and "configPath" in block)
            text = u"# header\n[]\n"
            check(u"strip_block 对没装过的文件幂等", INS.strip_block(text) == text)
            body = u"# header\n\n" + block
            check(u"strip_block 能把我们那一段摘干净（只剩注释）",
                  INS._core(INS.strip_block(body)) == [])
            #   真解析（有 js-yaml 才验；没有就跳过）
            tmpyml = os.path.join(tmp, "x.yml")
            with open(tmpyml, "w", encoding="utf-8") as f:
                f.write(body)
            ok, why = INS.yaml_validate(tmpyml)
            check(u"补丁片段能被 js-yaml 解析成数组", ok, why)
        except Exception as e:
            check(u"安装器模块可导入/可渲染", False, repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(u"\n" + u"=" * 60)
    if FAILS:
        print(u"★ 失败 %d 项：%s" % (len(FAILS), u" / ".join(FAILS)))
        return 1
    print(u"全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
