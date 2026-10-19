# -*- coding: utf-8 -*-
u"""fairy_activity_dsh.py —— **DSH（DeepSeek Harness）** 侧的会话活动侦测。

## 为什么要另写一个

`fairy_activity.py` 是给 **WorkBuddy** 写的：它 tail `~/.workbuddy/projects/<项目>/<会话>.jsonl`
（明文事件流），靠"末事件类型 + 时间"判我在不在干活。

DSH 把会话写成 **zstd 压缩**的 `~/.dsh/sessions/<项目编码>/<会话>/session.v4.jsonl.zstd`，
标准库读不了（DSH 自己的钩子文档也写明「默认 zstd 压缩的会话日志无法被 hook 脚本读取」）。
⇒ 不硬啃日志，改用**两个更准的事实源**：

### ① 会话投影缓存（**主判据，零配置**）
```
%DSH_HOME%\\storages\\session_projcache\\sessions\\<session-id>.json
    record.identity.cwd                 ← 这个会话属于哪个工作目录
    record.rows.turnBoundary.val        ← ★ 这一轮开着没有
        openTurnStartSeq == null  ⇒ 没人在跑
        openTurnStartSeq == 整数  ⇒ **正在跑**（连"主人按停"也会被置回 null）
```
★ 这一份是 DSH 自己维护的**明文 JSON**，中止/出错时 `openTurnStartSeq` 一样会清掉
  —— 正好补上 DSH 钩子在"主人按停"时**不发 Stop**的那个缺口（见 §钩子）。

### ② 钩子事件流（**补充判据，装了钩子才有**）
```
<桌宠目录>\\dsh_events.jsonl    一行一个事件，由 fairy_dsh_hook.py 写
    {"t": 1792380000.1, "ev": "user", "s": "<session>", "p": "e-AI成图实践-Fairy"}
```
| ev | 谁写 | 用途 |
|---|---|---|
| `session` | SessionStart | 会话刚开 |
| `user`    | UserPromptSubmit | ★ **主人刚一按回车**（比缓存落盘更早，0 秒级） |
| `stop`    | Stop | 我这一轮说完了 ⇒ "最后一句之后的尾巴"从这一刻算 |

## 判据（每个会话各算各的；多项目同时跑就是多行）

```
任一会话"开着"（缓存 openTurnStartSeq 非空，且没超过 STALE_TURN_S）→ 工作态
刚开单（有 user 事件、STALE_PROMPT_S 内、缓存还没落盘）             → 工作态
刚收工（观察到期关闭，ASSIST_HOLD_S 内）                            → 工作态（尾巴）
其余                                                              → 常态
```

★ `ASSIST_HOLD_S=25` 就是主人要的那句：「从进入工作态起就保持，一直到你回复结束」——
  最后一句之后的 25 秒尾巴（与 WorkBuddy 版 `fairy_activity.ASSIST_HOLD_S` 同值）。
★ `STALE_TURN_S=1800`（30 min）是**唯一兜底**：DSH 崩了/主人强杀进程时缓存再不会更新，
  靠它把"永远半睁"挡在半小时以内。

## 与 fairy_activity 的关系
接口**完全一致**（`active()` / `age()` / `user_at` / `last_dir` / `per_proj` / `info()`），
所以 `fairy_pet` 不用区分来源 —— 由 `fairy_activity.make_watcher()` 把两边合起来。
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 事实源 ①：DSH 会话投影缓存 ----
DSH_HOME = ((os.environ.get(u"DSH_HOME") or u"").strip()
            or os.path.join(os.path.expanduser(u"~"), u".dsh"))
CACHE_ROOT = os.path.join(DSH_HOME, u"storages", u"session_projcache", u"sessions")
#   ★ 测试/演练用：`FAIRY_DSH_CACHE=<目录>` 指到别处，绝不碰真缓存。
CACHE_ROOT = ((os.environ.get(u"FAIRY_DSH_CACHE") or u"").strip() or CACHE_ROOT)

# ---- 事实源 ②：钩子事件流（fairy_dsh_hook.py 写）----
EVENT_FILE = ((os.environ.get(u"FAIRY_DSH_LOG") or u"").strip()
              or os.path.join(HERE, u"dsh_events.jsonl"))

# ---- 时间常数 ----
SCAN_S = 1.0            # 扫描间隔（60 fps 下不能每帧读盘）
TAIL_BYTES = 262144     # 事件流只看尾部这么多字节
ASSIST_HOLD_S = 25.0    # ★ 我"说完最后一句"之后的尾巴（= 主子要的"一直到回复结束"）
STALE_PROMPT_S = 120.0  # 刚开单、缓存还没落盘的宽限（正常几秒内就落盘）
STALE_TURN_S = 1800.0   # 一轮"开着"最长认多久（DSH 崩了/被强杀时的兜底）

#   ★★★ 2026-10-19 主人定的：**工作段只占 0~90**，90→100 留给"这一轮真的交付完"。
#      原话：「当时 90-95-100 的规则，是因为在 workbuddy 时期做不到 agent 回复完 + 交付完
#      正好到 100% 状态；现在的 dsh 时期如果能做到，那就用你自己的……只要能做到，
#      回复+交付完，进度跳正好到 100%，桌宠弹卡片提醒我去查收就行。」
STAGE_TOP = 90.0        # 工作段上限（= 活干完了，还在写回复）
FINISH_WINDOW_S = 180.0  # 观察到"这一轮关掉"之后多久内还算"刚交付"（给弹卡用）


def project_key(path):
    u"""工作目录 → 项目标识（**与 `fairy_activity.project_key` 同一套规则**）。

    `E:\\AI成图实践\\Fairy` → `e-AI成图实践-Fairy`
    ★ 两边必须同一条规则：`fairy_notify` 写的 `project`、`_prog\\progress_<项目>.json`
      的文件名、`fairy_pet._bar_project()` 读的键，全靠它对齐。
    """
    p = os.path.abspath(path or u".")
    drive, rest = os.path.splitdrive(p)
    return (drive.rstrip(u":").lower() + u"-"
            + rest.strip(u"\\/").replace(u"\\", u"-").replace(u"/", u"-"))


def _ev_ts(d):
    u"""事件里的时刻（wall clock 秒）；取不到 → None。兼容秒/毫秒。"""
    v = d.get(u"t", d.get(u"ts"))
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v / 1000.0 if v > 1e11 else v


class DshActivityWatch(object):
    u"""DSH 活动侦测。接口与 `fairy_activity.ActivityWatch` 一致（鸭子类型）。"""

    def __init__(self, cache_root=None, event_file=None,
                 assist_hold=ASSIST_HOLD_S, stale=STALE_TURN_S,
                 scan=SCAN_S, exit_=8.0, hold=STALE_PROMPT_S, only=None):
        self.root = cache_root or CACHE_ROOT
        self.events = event_file or EVENT_FILE
        self.assist_hold = float(assist_hold)
        self.stale = float(stale)
        self.scan = float(scan)
        self.exit = float(exit_)
        self.hold = float(hold)              # 刚开单的宽限（名字沿用 WorkBuddy 版）
        self.only = list(only) if only else []
        self.ok = os.path.isdir(self.root) or os.path.isfile(self.events)
        #   诊断字段（`heartbeat.txt` 里就是它们）
        self.hint = u"init"
        self.last_type = u""
        self.last_dir = u""
        self.skipped = 0
        self.last_act = 0.0
        self.user_at = 0.0
        self.per_proj = {}
        #   内部状态
        self._scan_at = 0.0
        self._sess = {}          # sid → dict(open=, proj=, open_seen_at=, closed_at=, last_seen=)
        self._cache_mt = {}      # 缓存文件 → 上次读到的 mtime（没变就不重解析）
        self._ev_at = 0.0        # 事件流里最新一条的时刻
        self._ev_type = u""
        self._ev_sess = {}       # sid → (ev, ts)
        self._ev_offset = 0      # 下次只读新增部分（首轮从尾部读）
        #   ★★ 两个"时刻"必须分开（2026-10-19 自测抓到）：
        #      · `user_at` = 主人这一轮是什么时候开口的（**投影和钩子都能给**）——
        #        冷启动判据、收尾让位用它；
        #      · `_prompt_at` = **钩子**明确报告"主人刚提交"的时刻 —— 只有它才配
        #        "开单宽限"（缓存还没落盘时先当在工作）。若拿 `user_at` 去宽容，
        #        **每一轮都会多挂 120 s**（实测自测当场红）。
        self._prompt_at = 0.0
        #   ★ "刚交付"只弹一次卡：`(sid, turn)` → 报到时刻（进程级记忆，最多 64 条）。
        self._reported = {}

    # ---------------------------------------------------------------- 内部
    def _read_events(self):
        u"""把 `dsh_events.jsonl` 新增的部分读进来。

        ★ 首轮从**尾部**读（只关心最近发生了什么，老事件没意义）；
          之后按 `_ev_offset` 增量读 —— 一轮对话几十条事件，天天全量解析没必要。
        ★ 读失败/半行一律跳过（不抛），下一轮自会重来。
        """
        try:
            if not os.path.isfile(self.events):
                return
            size = os.path.getsize(self.events)
            start = self._ev_offset
            if start == 0 or start > size:
                start = max(0, size - TAIL_BYTES)
            if start >= size:
                self._ev_offset = size
                return
            with open(self.events, u"rb") as f:
                f.seek(start)
                data = f.read(size - start)
            self._ev_offset = size
            for raw in data.split(b"\n"):
                raw = raw.strip()
                if not raw.startswith(b"{"):
                    continue
                try:
                    d = json.loads(raw.decode(u"utf-8", u"replace"))
                except Exception:
                    continue
                ev = d.get(u"ev")
                ts = _ev_ts(d)
                if not ev or ts is None:
                    continue
                sid = d.get(u"s") or u""
                if sid:
                    #   项目名由钩子一并写进来（`p`）—— 缓存还没落盘时就用它
                    self._ev_sess[sid] = (ev, ts, d.get(u"p") or u"")
                if ts >= self._ev_at:
                    self._ev_at = ts
                    self._ev_type = ev
                if ev == u"user":
                    if ts > self.user_at:
                        self.user_at = ts
                    self._prompt_at = max(self._prompt_at, ts)
        except Exception:
            pass

    def _scan_cache(self, now, sessions):
        u"""扫投影缓存 → 把"开着/关了"写进 `sessions`。

        ★ 只解析 **mtime 变了** 的文件：目录里可能有几十个历史会话，
          每秒全量解析是白烧 CPU（实测单文件 5~30 KB）。
        ★ mtime 变了但 JSON 解析失败（正在被原子替换）⇒ **保留上一轮结论**，
          绝不当成"关了" —— 那会让桌宠在每一轮开始时闪一下常态。
        """
        try:
            names = os.listdir(self.root)
        except Exception:
            return
        for n in names:
            if not n.endswith(u".json"):
                continue
            p = os.path.join(self.root, n)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            sid = n[:-5]
            if self._cache_mt.get(p) == mt:
                continue            # 没变 ⇒ 用上一轮的结论
            try:
                with open(p, u"r", encoding=u"utf-8", errors=u"replace") as f:
                    j = json.load(f)
            except Exception:
                self._cache_mt[p] = mt      # 记下 mtime，免得每秒重试同一个坏文件
                continue
            self._cache_mt[p] = mt
            try:
                rec = j.get(u"record") or {}
                ident = rec.get(u"identity") or {}
                rows = rec.get(u"rows") or {}
                tb = (rows.get(u"turnBoundary") or {}).get(u"val") or {}
                cwd = ident.get(u"cwd") or u""
                proj = project_key(cwd) if cwd else u""
                if not proj:
                    continue
                st = sessions.setdefault(sid, {})
                st[u"proj"] = proj
                st[u"open"] = tb.get(u"openTurnStartSeq") is not None
                st[u"cache_mt"] = mt
                st[u"last_turn"] = tb.get(u"lastTurn") or 0
                #   ★★ 2026-10-19：**阶段进度**（这次迁移的重点之一）。
                #      `todos` = 这一轮的任务清单（`todo_write` 写的），**每个 turn 开始时
                #      由投影重置成 null**（见 dsh-tool-todo 的 projection apply）
                #      ⇒ 它天然就是"本轮拆成几步、走到第几步"。
                td = (rows.get(u"todos") or {}).get(u"val")
                st[u"todos"] = _clean_todos(td)
                #   `sessionStats.openStep` = 我此刻在第几步（诊断/兜底用）
                ss = (rows.get(u"sessionStats") or {}).get(u"val") or {}
                osp = ss.get(u"openStep") or {}
                if isinstance(osp, dict) and osp.get(u"turn") is not None:
                    st[u"step"] = (osp.get(u"turn"), osp.get(u"step"))
                    st[u"step_at"] = osp.get(u"startTime")
                    #   ★★★ 2026-10-19：**"主人发指令的时刻"也得从缓存里补**。
                    #      没有钩子（打包版 DSH 就没随包分发钩子桥接）时，事件流是空的
                    #      ⇒ `user_at` 一直为 0 ⇒ 桌宠重启后 `_cold_boot()` 永远为真
                    #      ⇒ **永远不半睁**（实测：靠 WorkBuddy 那路的 user_at 才碰巧正常）。
                    #      `openStep.startTime` 就是这一轮开始的墙钟（毫秒），语义等价。
                    try:
                        _t = float(osp.get(u"startTime") or 0.0) / 1000.0
                        if _t > 1e9:
                            st[u"turn_at"] = _t
                    except Exception:
                        pass
            except Exception:
                continue

    def _tick(self, now):
        u"""一次扫描：把两个来源合成每个会话的"开着/关了"。"""
        sessions = {}
        self._scan_cache(now, sessions)
        self._read_events()
        # 事件流里出现过、但缓存里没有的会话（钩子装了、缓存还没落盘）
        for sid, (ev, ts, pj) in self._ev_sess.items():
            st = sessions.setdefault(sid, {})
            st.setdefault(u"proj", u"")
            if pj and not st.get(u"proj"):
                st[u"proj"] = pj
            st[u"ev"] = ev
            st[u"ev_ts"] = ts
        # 逐会话判活 + 记录"什么时候第一次看到它关了"（尾巴要用）
        for sid, st in sessions.items():
            prev = self._sess.get(sid) or {}
            st[u"proj"] = st.get(u"proj") or prev.get(u"proj") or u""
            #   ★ 没变（这一轮没重新解析）的缓存文件要把上一轮的 mtime 接着用 ——
            #     否则 `last_seen` 会退化成 0，够不上"项目还活着"的判断。
            st[u"cache_mt"] = (float(st.get(u"cache_mt") or 0.0)
                               or float(prev.get(u"cache_mt") or 0.0))
            opened = bool(st.get(u"open"))
            ev = st.get(u"ev") or prev.get(u"ev") or u""
            ts = float(st.get(u"ev_ts") or prev.get(u"ev_ts") or 0.0)
            if not opened and ev in (u"user", u"session") and (now - ts) <= self.hold:
                # 刚开单、缓存还没落盘 ⇒ 先按开着算（避免"发完指令要等缓存"）
                opened = True
            st[u"ev"], st[u"ev_ts"] = ev, ts
            st[u"last_seen"] = max(ts, float(st.get(u"cache_mt") or 0.0))
            #   缓存 mtime 没变 ⇒ 这一轮没重新解析，`todos` / `step` 要沿用上一轮的结论
            if u"todos" not in st:
                st[u"todos"] = prev.get(u"todos") or []
            if u"step" not in st:
                st[u"step"] = prev.get(u"step")
                st[u"step_at"] = prev.get(u"step_at")
            if u"turn_at" not in st:
                st[u"turn_at"] = prev.get(u"turn_at")
            #   ★★ 没钩子时 `user_at` 只能用缓存里的"这一轮开始时刻"顶替
            #      （见 `_scan_cache` 里 `turn_at` 的说明）。
            ta = float(st.get(u"turn_at") or 0.0)
            if opened and ta > self.user_at:
                self.user_at = ta
            if opened:
                st[u"closed_at"] = 0.0
            else:
                #   只在"本轮运行里见过它开着"时才记关闭时刻 ⇒ 尾巴不会在启动时凭空出现
                if prev.get(u"open") and not prev.get(u"closed_at"):
                    st[u"closed_at"] = now
                else:
                    st[u"closed_at"] = float(prev.get(u"closed_at") or 0.0)
            st[u"open"] = opened
            self._sess[sid] = st
        # 项目维度（多项目排队要用）
        per = {}
        for sid, st in self._sess.items():
            pj = st.get(u"proj") or u""
            if not pj:
                continue
            per[pj] = max(per.get(pj, 0.0), float(st.get(u"last_seen") or 0.0))
        self.per_proj = per
        # 诊断 + `last_dir`
        newest_sid, newest_at = u"", 0.0
        for sid, st in self._sess.items():
            at = max(float(st.get(u"last_seen") or 0.0), float(st.get(u"ev_ts") or 0.0))
            if at >= newest_at:
                newest_sid, newest_at = sid, at
        if newest_sid and self._sess[newest_sid].get(u"proj"):
            self.last_dir = self._sess[newest_sid][u"proj"]
        elif not self.last_dir:
            self.last_dir = u""
        if newest_at:
            self.last_act = max(self.last_act, newest_at)

    # ---------------------------------------------------------------- 对外
    def age(self, now=None):
        u"""最近一次活动距今多少秒（没有记录 → 很大的数）。"""
        if not self.last_act:
            return 1e9
        return max(0.0, (now if now is not None else time.time()) - self.last_act)

    def active(self, now=None):
        u"""现在是否"正在干活"。"""
        now = now if now is not None else time.time()
        if now - self._scan_at >= self.scan:
            self._scan_at = now
            try:
                self._tick(now)
            except Exception:
                #   ★ 感知坏了绝不能让桌宠崩：这一轮保持上一轮的结论。
                pass
        # ① 任一会话开着（缓存权威 + 钩子宽限）
        for sid, st in self._sess.items():
            if not st.get(u"open"):
                continue
            if now - float(st.get(u"last_seen") or 0.0) > self.stale:
                continue                    # 太久没动静 ⇒ 当它没了（DSH 崩/被强杀）
            self.hint = u"开着（%s，%s）" % (st.get(u"proj") or u"?",
                                            _short_sid(sid))
            return True
        # ② 刚收工的尾巴
        for sid, st in self._sess.items():
            c = float(st.get(u"closed_at") or 0.0)
            if c and (now - c) <= self.assist_hold:
                self.hint = u"刚收工（%.0fs 尾巴）" % (now - c)
                return True
        # ③ 钩子：刚收到指令（缓存还没落盘 / 缓存目录不可读时的兜底）
        #    ★ 只认**钩子事件**的 `_prompt_at`；不能拿 `user_at`（它现在也可能来自
        #      缓存的 turn 起始时刻）—— 否则每一轮都会多挂 `hold` 秒（自测抓到过）。
        if self._prompt_at and (now - self._prompt_at) <= self.hold:
            self.hint = u"刚收到指令（%.0fs）" % (now - self._prompt_at)
            return True
        self.hint = u"空闲（%s）" % ((u"%.0fs 前" % self.age(now))
                                    if self.age(now) < 1e6 else u"无记录")
        return False

    # ---------------------------------------------------------------- ★ 阶段进度
    def stage(self, now=None):
        u"""→ 本轮**阶段进度**：`{project, percent, total, completed, active, label, ts, sess}`；
        没有可用的 `todos` ⇒ `None`。

        ## 这是什么（也是**这次迁移最值钱的一件事**）

        WorkBuddy 那会儿桌宠拿不到任务进度 —— 宿主闭源、没有进出通道，
        只能靠我"记得`--item K P`"手工上报（漏报就断档）。DSH 不一样：
        **`todo_write` 的清单被投影成明文 JSON**，就在会话投影缓存里：
        ```
        record.rows.todos.val = [{"content": "...", "status": "completed|in_progress|pending"}, …]
        ```
        而且 DSH 在**每个 turn 开始**时把它重置成 null ⇒ 它天然就是
        **"这一轮拆成几步、走到第几步"**，不需要我额外做什么。

        ## 折算规则
        ```
        percent = (completed + 0.5 × in_progress) / total × 100   （向上取整到 1%）
        ```
        ★ 为什么 `in_progress` 算半格：主人要的是"看得见在走"，
          一项刚开始就 0%、做完才跳一格，中间那段时间条是死的 —— 半格能让它先动起来，
          又不至于虚报（做完才给满格）。

        ## 多个会话同时跑
        取**最近有动静**的那个（`last_seen` 最大）；只认 `open`（这一轮开着）的会话，
        免得把上一轮的旧清单翻出来。
        """
        now = now if now is not None else time.time()
        if now - self._scan_at >= self.scan:
            self._scan_at = now
            try:
                self._tick(now)
            except Exception:
                pass
        best, best_seen = None, -1.0
        for sid, st in self._sess.items():
            if not st.get(u"open"):
                continue
            seen = float(st.get(u"last_seen") or 0.0)
            if now - seen > self.stale:
                continue
            td = st.get(u"todos") or []
            n = len(td)
            if n <= 0:
                continue
            if seen <= best_seen:
                continue
            done = sum(1 for c, s in td if s == u"completed")
            active = sum(1 for c, s in td if s == u"in_progress")
            cur = u""
            for c, s in td:
                if s == u"in_progress":
                    cur = c
                    break
            if not cur:                       # 没有进行中的 ⇒ 拿最后一个已完成的当说明
                for c, s in reversed(td):
                    if s == u"completed":
                        cur = c
                        break
            #   ★★★ 2026-10-19 主人定的新口径：
            #     「90-95-100 那三档是 WorkBuddy 时期**做不到**才凑出来的；
            #       DSH 能做到"回复+交付完正好 100%"，那就用你自己的。
            #       只要能做到：回复+交付完，进度正好到 100%，桌宠弹卡提醒我去查收。」
            #   ⇒ 工作段**只占 0~90**（`STAGE_TOP`），**90 就是"活干完了"**；
            #     90→100 由"这一轮结束 + 清单全绿"那一触发（`finished()`）补上，
            #     并且**同一时刻弹完成卡**（见 `fairy_pet._auto_finish`）。
            #   ★ 这样条上永远不会"活没干完就冲到 100"。
            raw = (done + 0.5 * active) / float(n) * 100.0
            best = {u"project": st.get(u"proj") or u"",
                    u"percent": min(STAGE_TOP, _ceil_pct(raw * STAGE_TOP / 100.0)),
                    u"total": n, u"completed": done, u"active": active,
                    u"all_done": (done >= n),
                    u"label": cur, u"ts": now, u"sess": sid,
                    u"step": st.get(u"step")}
            best_seen = seen
        return best

    def finished(self, now=None):
        u"""→ **刚收工、且清单全部完成**的那一轮（每条只报一次）；没有 ⇒ `None`。

        这是"**回复+交付完 ⇒ 正好 100% + 弹卡**"的判据（主人 2026-10-19 定）：

        | 判据 | 为什么它够 |
        |---|---|
        | 投影里 `openTurnStartSeq` 变空 | = **这一轮结束了**（DSH 把回复写完才会空） |
        | `todos` 非空且**全部 completed** | = 活**真的**干完了，不是半截被打断 |
        | `closed_at` 是"本轮运行里亲眼看到它开→关" | = 桌宠重启**不会**把上一轮的旧任务当成刚交付、再弹一次卡 |
        | `(sid, turn)` 只报一次 | = 同一条 never 弹两张卡 |

        ★ 90→100 的那 10 个百分点**由这次触发补**（`fairy_pet._auto_finish`）：
          先弹卡、再把进度写成 100（`settle`），走的是和 `--reply` **同一条**收尾路径。
        """
        now = now if now is not None else time.time()
        if now - self._scan_at >= self.scan:
            self._scan_at = now
            try:
                self._tick(now)
            except Exception:
                pass
        for sid, st in self._sess.items():
            if st.get(u"open"):
                continue
            c = float(st.get(u"closed_at") or 0.0)
            if not c or (now - c) > FINISH_WINDOW_S:
                continue
            td = st.get(u"todos") or []
            if not td:
                continue
            if any(s != u"completed" for _c, s in td):
                continue          # 还有没做完的 ⇒ 不算交付（也不弹"已完成"的卡）
            turn = int(st.get(u"last_turn") or 0)
            key = (sid, turn)
            if key in self._reported:
                continue
            self._reported[key] = now
            if len(self._reported) > 64:      # 别无限长（进程级记忆）
                for k in sorted(self._reported, key=lambda k: self._reported[k])[:16]:
                    self._reported.pop(k, None)
            return {u"project": st.get(u"proj") or u"",
                    u"total": len(td), u"completed": len(td),
                    u"turn": turn, u"closed_at": c, u"sess": sid}
        return None

    def info(self):
        u"""给心跳用的一行诊断（`heartbeat.txt` 的 `act=` 段）。"""
        a = self.age()
        proj = self.last_dir or u"-"
        nopen = sum(1 for st in self._sess.values() if st.get(u"open"))
        return u"DSH:%s|%s|%.1fs|%s|开%d/%d个会话" % (
            self._ev_type or u"-", self.hint,
            a if a < 1e6 else -1, proj, nopen, len(self._sess))


def _short_sid(sid):
    return (sid or u"?")[-8:]


def _ceil_pct(x):
    u"""百分比**向上取整到 1%**（项目口径：只升不降、向上取整，见 `fairy_notify.roundup`）。"""
    import math
    return float(min(100.0, math.ceil(round(float(x) * 1000.0, 6) / 1000.0)))


def _clean_todos(v):
    u"""`todos.val` → `[(content, status), …]`（脏数据一律丢掉，绝不抛）。"""
    out = []
    if not isinstance(v, list):
        return out
    for it in v:
        if not isinstance(it, dict):
            continue
        c = it.get(u"content")
        s = it.get(u"status")
        if isinstance(c, str) and c.strip() and s in (u"pending", u"in_progress", u"completed"):
            out.append((c.strip(), s))
    return out


if __name__ == u"__main__":
    w = DshActivityWatch()
    print(u"cache = %s  （存在：%s）" % (w.root, os.path.isdir(w.root)))
    print(u"events= %s  （存在：%s）" % (w.events, os.path.isfile(w.events)))
    for i in range(8):
        act = w.active()
        print(u"  t=%4.1fs  active=%-5s  %s" % (i, act, w.info()))
        time.sleep(1.2)
