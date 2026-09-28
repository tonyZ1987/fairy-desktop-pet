# -*- coding: utf-8 -*-
"""会话活动侦测 —— 让桌宠「主人一发出指令就半睁」，不靠我记得发信号。

## 为什么需要它
桌宠没法自己知道我忙不忙，原先只能靠我主动写 `state.json`（我漏过一次）。
主人 2026-09-22：*"应该从我发出指令，你接收到指令开始思考时，桌宠就切换到工作态。"*

## 靠什么判断（2026-09-22 实测）
WorkBuddy 把会话事件流**实时**追加到：

    ~/.workbuddy/projects/<项目名>/<会话 id>.jsonl

实测事件类型只有五种：

| type | 含义 | 对状态判断的意义 |
|---|---|---|
| `message` (role=user) | 主人提交了指令 | ★ **立刻进入工作态**（这正是主人要的时机） |
| `reasoning` | 我在推理 | 在工作 |
| `function_call` | 我在调工具 | ★ 在工作（**哪怕已经过去很久**） |
| `function_call_result` | 工具返回了 | 在工作，我接着干 |
| `message` (role=assistant) | 我吐出的一段话 | **歧义**：可能是中途，也可能是最终答复 ⇒ 看时间 |
| `file-history-snapshot` | 系统记账 | 无关 ⇒ 看时间 |

## 为什么不能只看"文件多久没动"
实测：**执行一个长工具期间，事件之间可以空档几十秒**（本条命令自己就是例子：
开始时写一条 `function_call`，跑完才写 `function_call_result`）。
只看时间的话，pet 会在长命令中途闪回常态 —— 这是最容易被主人一眼看出的瑕疵。
⇒ 所以用**双判据**：先看末尾事件的类型，类型不足以判定时再看时间。

## 判据
```
末尾事件 = function_call / reasoning            → 工作态（无视时间）
末尾事件 = message 且 role=user                 → 工作态（主人刚提交）
末尾事件 = message 且 role=assistant            → 时间：< EXIT_S 算工作，否则常态
末尾事件 = function_call_result / snapshot/无   → 时间：< EXIT_S 算工作，否则常态
```
`EXIT_S` 默认 **8 s**：我吐出最后一段话之后 8 秒，眼睛睁开。
"""
import json
import os
import time

DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), ".workbuddy", "projects")
HERE = os.path.dirname(os.path.abspath(__file__))
# ★★ 2026-09-23 主人：「我会让 workbuddy 同时开两个或以上任务在工作，你只监控 fairy
#   项目这边或者只监视 20260920 这边，**不要混淆**」。
#   ⇒ 这个文件指定要盯哪些项目（一行一个目录名，`#` 开头是注释）：
#         pet-v2\activity_project.txt
#     不写它 ⇒ **自动认本仓库所在的那个项目**（见 `project_key()`）。
ONLY_FILE = os.path.join(HERE, "activity_project.txt")


def project_key(path):
    u"""项目根路径 → WorkBuddy 的 `projects` 目录名。

    实测规则（2026-09-23 本机）：
        E:\\AI成图实践\\Fairy           → `e-AI成图实践-Fairy`
        D:\\郑丁铭\\…\\PDF\\20260920     → `d-郑丁铭-…-PDF-20260920`
    即 **盘符小写** + 其余路径把分隔符换成 `-`。
    """
    p = os.path.abspath(path)
    drive, rest = os.path.splitdrive(p)
    return (drive.rstrip(u":").lower() + u"-"
            + rest.strip(u"\\/").replace(u"\\", u"-").replace(u"/", u"-"))


def project_file_name(project):
    u"""项目目录名 → **文件名片段**（只留字母/数字/中文与 `-_`，其余换 `_`）。

    ★ 单点定义：`fairy_notify` 写进度、`fairy_pet` 读进度，**两边都必须用这一条规则**
      （两处各写一遍迟早会不一致 ⇒ 表现为"条永远不出现"或"读错项目的条"）。
    ★ 为什么需要它（2026-09-23 17:3x 实测）：两个项目**同时**在跑时，
      `progress.json` 是全局单文件、**后写的覆盖先写的** —— 本会话报 `--item 1 2` 时
      被工具拒掉，因为另一个会话已经把 `item/phase` 写成了 `(2,1)`。
      ⇒ 进度**按项目各存一份**，桌宠只读"当前活跃项目"那一份。
    """
    if not project:
        return u""
    return u"".join(c if (c.isalnum() or c in u"-_") else u"_" for c in project)


def config_only(path=ONLY_FILE, repo_root=None):
    u"""→ 要监控的项目目录名列表（**空列表 = 不限制**）。

    优先级：`activity_project.txt` > **自动推导本仓库所在项目** > 不限（兜底）。
    ★ 自动推导不会失手到"什么都监控不到"：`ActivityWatch._match` 用的是**双向前缀**
      匹配，所以哪怕跑的是发布副本（目录名 `fairy-desktop-pet` 与项目根不同名），
      `e-AI成图实践-Fairy` 照样命中。
    """
    names = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    names.append(ln)
    except Exception:
        pass
    if names:
        return names
    root = repo_root or os.path.dirname(HERE)
    return [project_key(root)]

EXIT_S = 8.0         # 末事件无定论时：安静超过这个秒数 ⇒ 认为已收工
HOLD_S = 60.0        # ★★ 主人提交指令后的"保持窗口"（见 `active()`，用来消掉"中途闪回常态"）
                     #   ★ 2026-09-23： 20 → 60。主人发完指令到我产出**第一个信号**
                     #     （`reasoning` 或 `function_call`）之间有一段**纯思考空档**，
                     #     原来 20 s 偶尔不够 ⇒ 观感就是"刚发完指令，它又睁眼了"。
                     #   ★★ 另有一个前提：`_hold_until` 只在 `active()` 里更新 ⇒
                     #     调用方**必须真的调它**（`fairy_pet._target_state()` 踩过这个坑）。
ASSIST_HOLD_S = 25.0  # ★★ 2026-09-23 主人抓到「半睁 → 睁眼 → 又半睁，过程有反复」：
                     #   原来一见 `message/assistant`（我开口说话）就**立刻关窗**，
                     #   理由是"很可能就是最终答复"。但一回合里我**会说好几段、中间夹工具调用**
                     #   ⇒ 每说一段就关窗、8 s 后睁眼，我接着干活又半睁 ⇒ **抖**。
                     #   ⇒ 改成"把窗口**缩到** 25 s"：说完话仍有缓冲，我继续干活会重新开窗。
                     #   ★ 这就是主人要的语义 ——「从进入工作态起就保持，一直到你回复结束」，
                     #     这 25 s 是"最后一段话之后的尾巴"。
SCAN_S = 1.0         # 目录扫描间隔（没必要每帧扫）
TAIL_BYTES = 524288  # 读文件尾部多少字节来找最后一条事件（推理段落可能很长）

# 末尾是这些事件 ⇒ 板上钉钉在干活（长工具空档也不会误判）
HARD_WORK = ("function_call", "reasoning")
# 末尾是这些 ⇒ 必须靠时间判断
SOFT = ("function_call_result", "message", "file-history-snapshot", None)
# ★★★ 2026-09-23 **只认这些类型**，其余（`file-history-snapshot` 等 harness 记账）一律**跳过**。
#   真根因：实测 `file-history-snapshot` 会**紧跟主人那条 user 消息之后**写进来
#   （16:41:38 user → 16:41:38 snapshot），而我**纯思考 3 分钟、jsonl 一个字都不写**
#   ⇒ 感知看到的"末事件"是那个记账事件 ⇒ 掉进「安静 8 s 算收工」
#   ⇒ 主人等我思考的那 3 分钟里桌宠**一直是常态**。
MEANINGFUL = ("message", "function_call", "function_call_result", "reasoning")


def _ev_ts(d):
    u"""从一条事件里取**它的落盘时刻**（wall-clock 秒）；取不到 → None。

    ★★ 2026-09-23 21:1x 新增（根治"被记账事件抢镜"）：
      在这之前，"谁最新"一律看**文件 mtime**。而 harness 会往 jsonl 追加记账事件
      （`file-history-snapshot`）—— **哪怕没人干活**也会把 mtime 推新。
      实测：主人在 Fairy 窗口问一句话 ⇒ Fairy 的 jsonl mtime 变最新 ⇒ 感知认了 Fairy、
      去读 Fairy 那份（`planned=False`）⇒ **工作态对、但进度条不显示**（主人 21:12 截图）。
    ⇒ 改看**事件自带的 `timestamp`**：它只随**真实事件**推进，不受记账干扰。
    ★ 兼容毫秒 / 秒 / ISO 字符串三种写法（实测见过 `1790168256096` 这种毫秒）。
    """
    v = d.get("timestamp")
    if v is None:
        v = d.get("ts")
    if v is None:
        v = d.get("time")
    if isinstance(v, bool):          # bool 是 int 的子类，先挡掉
        return None
    if isinstance(v, (int, float)):
        v = float(v)
        return v / 1000.0 if v > 1e11 else v      # >1e11 ⇒ 是毫秒
    if isinstance(v, str):
        try:
            return time.mktime(time.strptime(v[:19], u"%Y-%m-%dT%H:%M:%S"))
        except Exception:
            try:
                return float(v)
            except Exception:
                return None
    return None


class ActivityWatch:
    def __init__(self, root=None, exit_=EXIT_S, scan=SCAN_S, hold=HOLD_S, only=None,
                 assist_hold=ASSIST_HOLD_S):
        self.root = root or DEFAULT_ROOT
        self.exit = float(exit_)
        self.scan = float(scan)
        self.ok = os.path.isdir(self.root)
        self.hold = float(hold)
        self.assist_hold = float(assist_hold)   # ★ 我说完话之后的保持窗口（见 `ASSIST_HOLD_S`）
        # ★★ 只盯这些项目（空 = 全部）。见文件顶部 `config_only()` 的说明 ——
        #   同时开多个任务时，不限定就会"谁在动就跟谁"，必然混淆。
        self.only = list(only) if only is not None else config_only()
        # ★★ 2026-09-23 17:4x：记下"白名单是不是**调用方显式传进来的**"。
        #   热读（`_refresh_dirs` 每 10 s 重读配置文件）**只该在"没显式传"时生效** ——
        #   原来它无条件覆盖 `self.only` ⇒ 显式传参被**静默无视**
        #   （`v2_12` 那条"换成只盯 20260920"的反证因此失败：实际跑的仍是配置里那两行）。
        #   这正是"改了 A 却跑的是 B"那一类 —— 只有真去反证才会暴露。
        self._only_explicit = only is not None
        self.skipped = 0             # 诊断：这一轮被"项目过滤"挡掉了几个目录
        self.last_act = 0.0          # 最近一次活动的墙钟时间
        self.last_file = ""          # 诊断：哪个会话文件在动
        # ★ 2026-09-23 17:3x：**当前活跃项目的目录名**（如 `e-AI成图实践-Fairy`）。
        #   用途：`progress.json` 是全局单文件 ⇒ 甲项目交付的 100% 会在乙项目开工时
        #   顶出来（主人原话："换个项目就出现这个问题了，刚进入工作态的时候，就是带工作条的"）
        #   ⇒ 我上报进度时打上**归属项目**，桌宠只认「属于当前活跃项目」的那条。
        #   取用见 `fairy_notify.current_project()` 与 `fairy_pet._proj_ok()`。
        self.last_dir = ""
        self.last_type = ""          # 诊断：末尾事件类型（已跳过 harness 记账类）
        self.last_role = ""          # 诊断：末尾事件 role
        # ★ 2026-09-23：尾部**最近一条 message** 的角色 —— 判"他还在等我"用（见 `active()`）
        self.last_msg_role = None
        # ★ 末事件**落盘时刻**（墙钟）—— 保持窗口用它算，用 `now` 会让窗口永不过期
        self._ev_at = 0.0
        self._hold_until = 0.0       # ★ 新指令后的"保持窗口"终点（见 active()）
        self.hold = float(hold)
        self.hint = "init"           # 诊断：这一轮判定的依据
        self._scan_at = 0.0
        self._dirs_at = 0.0
        self._subdirs = []
        # ★★ 2026-09-23：**"主人新发了一条指令"的时刻**（墙钟）。
        #   只在**进入** `message/user` 的那一瞬记一次 —— 不是每帧刷新，
        #   否则"末事件一直是 user"就等于没有时间点。用途见 `fairy_pet` 的收尾段：
        #   交付后 100% 一直挂着，直到**主人开口说下一件事**才让位。
        self.user_at = 0.0
        self._prev_tr = (None, None)

    # ---------------------------------------------------------------- 内部
    def _refresh_dirs(self, now):
        """项目子目录列表缓存 10 s —— 新开会话最多晚 10 s 被发现，可接受。"""
        if now - self._dirs_at < 10.0:
            return
        self._dirs_at = now
        # ★★ 2026-09-23 17:2x：白名单**热生效** —— 跟子目录列表一起，每 10 s 重读一次。
        #   原来 `self.only` 只在 `__init__` 里读一次 ⇒ 改一行配置要"退出桌宠 →
        #   双击 restart_fairy.vbs"，摩擦太大（主人 17:2x 就为加一个项目多重启一次）。
        #   ★ 只读一个小文本，开销可忽略。
        #   ★★ 读失败或读到空 ⇒ **沿用旧值**，绝不把白名单清空 ——
        #     清了就变回"谁在动跟谁"，正是我们要避免的混淆。
        try:
            if not self._only_explicit:      # ★ 显式传了白名单 ⇒ 不覆盖（见 __init__ 的说明）
                _new = config_only()
                if _new:
                    self.only = _new
        except Exception:
            pass
        try:
            self._subdirs = [e.path for e in os.scandir(self.root) if e.is_dir()]
        except Exception:
            self._subdirs = []

    def _match(self, dirname):
        u"""这个项目目录在不在白名单里（**双向前缀**匹配）。

        ★ 为什么用前缀、不用相等：WorkBuddy 的目录名**会被截断**
          （实测见过 `c-Users-…-2026-08-04-15-` 这种尾部带 `-` 的），
          而且"发布副本"跑起来时仓库名可能是项目名 + 后缀 ⇒ 两边都放宽最稳。
        """
        if not self.only:
            return True
        d = dirname.lower()
        for w in self.only:
            w = w.lower()
            if d == w or d.startswith(w) or w.startswith(d):
                return True
        return False

    def _newest(self, now):
        u"""→ `(最新 jsonl 路径, 它的**末事件落盘时刻**)`。★ 只扫白名单里的项目。

        ★★★ 2026-09-23 21:1x **根治**（主人 21:12 截图：「文成那边在动手，进度条也没出来」）：
          原来**跨项目**比的是**文件 mtime**：

              m = e.stat().st_mtime        # 谁的文件新就认谁
              if m > best: best, best_p = m, e.path

          而 harness 会往 jsonl 追加**记账事件**（`file-history-snapshot`）⇒ **没人干活也会推新 mtime**。
          实测：主人在 Fairy 窗口问一句话 ⇒ Fairy 的 jsonl 变最新 ⇒

              感知认了 Fairy  ⇒  `_read_progress()` 去读 `_prog/progress_e-AI成图实践-Fairy.json`
                             ⇒  那份是 `0% + planned=False`（只打过招呼、还没拆任务）
                             ⇒  `_bar_pct()` 见 `not prog_planned` ⇒ **返回 None ⇒ 进度条不显示**

          ⇒ 现象就是「**工作态是对的，但条不出现**」，而且**只有文成一个项目在跑时也会发生**
            （因为"抢镜"的是**另一个项目的记账写入**，与那个项目忙不忙无关）。

        ⇒ 现在分两层，各用各的判据：
          · **同一个项目内**：仍按文件 mtime 挑最新会话 —— 项目内不存在"被别的项目抢"的问题；
          · **跨项目**：比较各自的**末事件时刻**（`MEANINGFUL` 过滤之后那条，见 `_ev_ts`）。
            取不到时刻（= 文件里没有白名单事件，或读失败）⇒ **rank = 0，排到最后** ——
            ★ **绝不能回退用 mtime**，否则"只有记账事件的项目"照样会赢，等于没修。
        """
        self._refresh_dirs(now)
        self.skipped = 0
        cand = []                     # [(用来比较的时刻, 该文件 mtime, 路径, 项目目录名)]
        for d in self._subdirs:
            pdir = os.path.basename(d)
            if not self._match(pdir):
                self.skipped += 1
                continue                      # ★ 别的项目在动 ⇒ 与我无关，不看
            # ① 同一项目内：按 mtime 挑最新的那个会话文件
            best_m, best_p = 0.0, ""
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if not e.name.endswith(".jsonl"):
                            continue
                        try:
                            m = e.stat().st_mtime
                        except OSError:
                            continue
                        if m > best_m:
                            best_m, best_p = m, e.path
            except Exception:
                continue
            if not best_p:
                continue
            # ② 取这个文件的"末事件时刻"（`MEANINGFUL` 过滤之后那条）
            # ★★ 取不到 ⇒ **rank = 0（最低优先级）**，**不要回退用 mtime**！
            #   我第一版写的是"回退用 mtime"——那等于**没根治**：
            #   一个"只有记账事件、没人干活"的项目，它的 mtime 恰恰是最新的
            #   ⇒ 回退过去它照样赢（正是要消灭的那个现象）。
            #   "末事件取不到"只有两种成因：① 文件里**没有白名单事件**（= 真没干活）✓
            #   ② 读失败（罕见，下一轮会重试）⇒ 两种情况都该让它**排后面**。
            ev = self._tail_event(best_p)[3]
            cand.append((ev or 0.0, best_m, best_p, pdir))
        if not cand:
            return "", 0.0
        # ③ 跨项目：**谁的末事件更新就认谁**（不再看文件 mtime）
        # ★★ 2026-09-24：把**每个项目各自的末事件时刻**存下来 ——
        #   桌宠的"多项目排队"要用它判"那个项目还活着吗"（见 `_open_tasks`）。
        #   ★ 数据本来就在手里（上面每个候选都读了尾部），只是以前丢了。
        self.per_proj = {c[3]: c[0] for c in cand}
        cand.sort(key=lambda x: x[0], reverse=True)
        rank, mt, path, pdir = cand[0]
        self.last_act = max(self.last_act, rank)
        self.last_file = os.path.basename(path)
        # ★ 顺手记下"这个会话文件属于哪个项目"（见 `last_dir` 的说明）
        self.last_dir = pdir
        return path, rank

    @staticmethod
    def _tail_event(path):
        u"""读文件尾部 → `(末事件类型, role, 最近一条 message 的 role, 末事件时刻)`。

        ★★★ 2026-09-23 **真根因**（主人：「等了很久，看来这个 bug 你还是没修好」）：
          实测 `file-history-snapshot` 会**紧跟在主人那条 user 消息之后**写进来
          （16:41:38 user → 16:41:38 snapshot），而我**纯思考 3 分钟、jsonl 一个字都不写**
          ⇒ 感知看到的"末事件"是那个记账事件、既不是硬判据也不是 user
          ⇒ 掉进「安静 8 s 算收工」⇒ **主人等我思考的那 3 分钟里桌宠一直是常态**。

        ⇒ 两条修法：
          ① 只认白名单 `MEANINGFUL` 里的类型，harness 记账类一律**跳过**；
          ② 顺手带回"最近一条 `message` 的 role" —— 用它判"他还在等我"（见 `active()`）。
        ★ 2026-09-23 21:1x 加第 4 项：**末事件的落盘时刻**（`_ev_ts`）——
          挑项目与算保持窗口都要用它，不能再用文件 mtime（见 `_newest` / `_ev_ts`）。
        """
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                n = min(size, TAIL_BYTES)
                f.seek(size - n)
                data = f.read(n)
        except Exception:
            return None, None, None, None
        last_msg_role = None
        for raw in reversed(data.split(b"\n")):
            raw = raw.strip()
            if not raw.startswith(b"{"):
                continue          # 可能是被截断的半行
            try:
                d = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                continue
            # role 可能在顶层，也可能在嵌套的 message 里（两种写法都见过）
            msg = d.get("message")
            role = d.get("role") or (msg.get("role") if isinstance(msg, dict) else None)
            t = d.get("type")
            if last_msg_role is None and t == "message":
                last_msg_role = role          # ★ 尾部**最近一条** message 的角色
            if t in MEANINGFUL:
                # ★ 只回**白名单那条**的时刻 —— 记账事件的时间不算数
                return t, role, last_msg_role, _ev_ts(d)
        return None, None, last_msg_role, None

    # ---------------------------------------------------------------- 对外
    def age(self, now=None):
        """最近一次活动距今多少秒；没有记录 → 一个很大的数。"""
        if not self.last_act:
            return 1e9
        return max(0.0, (now if now is not None else time.time()) - self.last_act)

    def active(self, now=None):
        """现在是否"正在干活"。"""
        now = now if now is not None else time.time()
        if not self.ok:
            self.hint = "no-root"
            return False
        if now - self._scan_at >= self.scan:
            self._scan_at = now
            path, ev_at = self._newest(now)
            if path:
                (self.last_type, self.last_role,
                 self.last_msg_role, _ev) = self._tail_event(path)
                # ★ 记住"这条末事件是什么时候落盘的" —— 保持窗口必须用它来算（见下）
                #   ★★ 2026-09-23 21:1x：`_newest` 返回的第二项已从**文件 mtime** 换成
                #      **末事件时刻** ⇒ 这里不用改，但语义更准了：以前记账事件会把 mtime
                #      推新、把保持窗口**错误延长**，现在不会了。
                self._ev_at = ev_at
        age = self.age(now)
        t, r = self.last_type, self.last_role

        # ★ 只在**进入** `message/user` 的那一瞬记时间（见 `__init__` 的说明）
        if (t, r) == ("message", "user") and self._prev_tr != ("message", "user"):
            self.user_at = now
        self._prev_tr = (t, r)

        # ★★ 2026-09-22 主人抓到的"**中途闪回常态**"：
        #   你按下回车 ⇒ 末事件 = `message/user` ⇒ 工作态；但我思考/准备的头几秒里，
        #   harness 会往**同一个 jsonl** 追加别的记录（如 `file-history-snapshot`）
        #   ⇒ 末事件变成"无定论" ⇒ 落进「安静 8 s 就算收工」⇒ 桌宠中途睁眼、然后我又上报 0%，
        #     观感就是"闪一下收工了，又开工了"。
        #   ⇒ 见到 `message/user` **开窗 20 s**（窗口内无定论也保持工作态）；
        #     见到 `message/assistant`（我在说话＝很可能就是最终答复）**立刻关窗** ——
        #     这样既消掉闪回，也不会出现"三句话的回答也让桌宠半睁 20 秒"。
        # ★★★ 窗口一律用**事件落盘时刻** `_ev_at` 算，**绝不能用 `now`**：
        #   用 `now` 的话，桌宠每秒扫一次就把窗口往后推一次 ⇒ **窗口永不过期**
        #   ⇒ 桌宠会"一直半睁、永不回常态"。这个反向 bug 被 `_settled` 掩盖了很久，
        #   2026-09-23 做时序测试（`_work/v2_41_verify_snapshot.py`）才暴露出来。
        _ev = self._ev_at or now
        if t == "message" and r == "user":
            self._hold_until = max(self._hold_until, _ev + self.hold)
        elif t == "message" and r == "assistant":
            # ★★ 2026-09-23 主人抓到「半睁 → 睁眼 → 又半睁，过程有反复」。
            #   原来这里是 `= 0.0`（**立刻关窗**，理由是"我开口说话 ≈ 最终答复"），
            #   可一个回合里我**会说好几段、中间还夹着工具调用** ⇒ 每说一段就关窗、
            #   8 s（`EXIT_S`）后睁眼，我接着干活又半睁 ⇒ **抖**。
            #   ⇒ 改成"把窗口**缩到** `ASSIST_HOLD_S`"（**赋值**，不是清零）：
            #     说完话仍有 25 s 缓冲；我继续干活（新事件）会把窗口重新拉长。
            #   ★ 主人要的语义：「从进入工作态起就保持，一直到你回复结束」——
            #     这 25 s 就是"最后一段话之后的尾巴"，之后自然回常态。
            self._hold_until = max(self._hold_until, _ev + self.assist_hold)

        if t in HARD_WORK:
            self.hint = "%s（硬判据）" % t
            return True
        if t == "message" and r == "user":
            self.hint = "user 刚提交"
            return True
        # ★★★ 2026-09-23：**本轮最后一条 message 是主人说的** ⇒ 他还在等我
        #   ⇒ 一律工作态（**完全不看时间**）。
        #   为什么非要这条：主人发完消息后 harness 会**紧跟一条 `file-history-snapshot`**，
        #   而"我纯思考的那几分钟 jsonl 一个字都不写" ⇒ 末事件既不是 user、也不是硬判据
        #   ⇒ 掉进"安静 8 s 算收工" ⇒ 主人 16:41:38 问话、一直等到 16:44:40 我才有下一条事件，
        #     **那 3 分钟里桌宠一直是常态**。
        #   ★ 这正是主人要的语义：「从进入工作态起就保持，一直到你回复结束」——
        #     "我回复" = 尾部最近一条 message 变成 assistant（我开口说话）。
        if self.last_msg_role == "user":
            self.hint = u"最后一条消息是主人 ⇒ 还在等他（%s/%.1fs）" % (t or u"?", age)
            return True
        if now < self._hold_until:
            self.hint = "保持窗口内（%s/%.1fs）" % (t or "?", age)
            return True
        self.hint = "%s/%.1fs" % (t or "?", age)
        return age < self.exit

    def info(self):
        u"""给 heartbeat 用的一行诊断。

        ★ 2026-09-23 起带上"在盯哪个项目、挡掉了几个目录" —— 同时开多个任务时，
          排障第一件事就是确认"它有没有在看别的项目"。
        """
        a = self.age()
        # ★ 2026-09-23 17:3x：优先报**实际在动的那一个项目**（`last_dir`）。
        #   白名单允许写多行，只报"第一个"看不出桌宠到底在跟谁 ⇒ 排障白费。
        proj = self.last_dir
        if not proj:
            proj = ((u"%d个项目" % len(self.only)) if len(self.only) != 1
                    else self.only[0]) if self.only else u"全部"
        return "%s|%s|%.1fs|%s,挡%d" % (self.last_type or "-", self.hint,
                                        a if a < 1e6 else -1, proj, self.skipped)


if __name__ == "__main__":
    w = ActivityWatch()
    print("root =", w.root, " 存在:", w.ok)
    for i in range(8):
        act = w.active()
        print("  t=%4.1fs  active=%-5s  末事件=%-22s 依据=%-22s 活动 %6.1fs 前"
              % (i, act, w.last_type, w.hint, w.age()))
        time.sleep(1.2)
