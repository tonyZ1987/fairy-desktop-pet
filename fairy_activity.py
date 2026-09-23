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

EXIT_S = 8.0         # 末事件无定论时：安静超过这个秒数 ⇒ 认为已收工
HOLD_S = 20.0        # ★★ 主人提交指令后的"保持窗口"（见 `active()`，用来消掉"中途闪回常态"）
SCAN_S = 1.0         # 目录扫描间隔（没必要每帧扫）
TAIL_BYTES = 524288  # 读文件尾部多少字节来找最后一条事件（推理段落可能很长）

# 末尾是这些事件 ⇒ 板上钉钉在干活（长工具空档也不会误判）
HARD_WORK = ("function_call", "reasoning")
# 末尾是这些 ⇒ 必须靠时间判断
SOFT = ("function_call_result", "message", "file-history-snapshot", None)


class ActivityWatch:
    def __init__(self, root=None, exit_=EXIT_S, scan=SCAN_S, hold=HOLD_S):
        self.root = root or DEFAULT_ROOT
        self.exit = float(exit_)
        self.scan = float(scan)
        self.ok = os.path.isdir(self.root)
        self.last_act = 0.0          # 最近一次活动的墙钟时间
        self.last_file = ""          # 诊断：哪个会话文件在动
        self.last_type = ""          # 诊断：末尾事件类型
        self.last_role = ""          # 诊断：末尾事件 role
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
        try:
            self._subdirs = [e.path for e in os.scandir(self.root) if e.is_dir()]
        except Exception:
            self._subdirs = []

    def _newest(self, now):
        """返回 (最新 jsonl 路径, 其 mtime)。"""
        self._refresh_dirs(now)
        best, best_p = 0.0, ""
        for d in self._subdirs:
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if not e.name.endswith(".jsonl"):
                            continue
                        try:
                            m = e.stat().st_mtime
                        except OSError:
                            continue
                        if m > best:
                            best, best_p = m, e.path
            except Exception:
                continue
        if best:
            self.last_act = max(self.last_act, best)
            self.last_file = os.path.basename(best_p)
        return best_p, best

    @staticmethod
    def _tail_event(path):
        """读文件尾部，返回最后一条完整事件 (type, role)。失败 → (None, None)。"""
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                n = min(size, TAIL_BYTES)
                f.seek(size - n)
                data = f.read(n)
        except Exception:
            return None, None
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
            return d.get("type"), role
        return None, None

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
            path, mt = self._newest(now)
            if path:
                self.last_type, self.last_role = self._tail_event(path)
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
        if t == "message" and r == "user":
            self._hold_until = max(self._hold_until, now + self.hold)
        elif t == "message" and r == "assistant":
            self._hold_until = 0.0

        if t in HARD_WORK:
            self.hint = "%s（硬判据）" % t
            return True
        if t == "message" and r == "user":
            self.hint = "user 刚提交"
            return True
        if now < self._hold_until:
            self.hint = "保持窗口内（%s/%.1fs）" % (t or "?", age)
            return True
        self.hint = "%s/%.1fs" % (t or "?", age)
        return age < self.exit

    def info(self):
        """给 heartbeat 用的一行诊断。"""
        a = self.age()
        return "%s|%s|%.1fs" % (self.last_type or "-", self.hint, a if a < 1e6 else -1)


if __name__ == "__main__":
    w = ActivityWatch()
    print("root =", w.root, " 存在:", w.ok)
    for i in range(8):
        act = w.active()
        print("  t=%4.1fs  active=%-5s  末事件=%-22s 依据=%-22s 活动 %6.1fs 前"
              % (i, act, w.last_type, w.hint, w.age()))
        time.sleep(1.2)
