# -*- coding: utf-8 -*-
u"""fairy_dsh_hook.py —— **DSH（DeepSeek Harness）** 侧的桥接件。

由 `dsh_bridge_install.py` 一键挂到 DSH 的 Claude-Code 兼容钩子上
（`@deepseek-ai/dsh-hooks-claude-code` → `dsh_hooks.json`）。

## 它干两件事

| 事 | 事件 | 干什么 |
|---|---|---|
| ① **上报活动** | 全部 | 往 `dsh_events.jsonl` 追一行事实（`fairy_activity_dsh` 读它） |
| ② **注入协议** | SessionStart / UserPromptSubmit | 让 AI 知道"这台机器上有只桌宠在看着你"（每会话一次） |

事件映射（DSH 的钩子名 → 桌宠的语言）：

| DSH 钩子 | 写进事件流的 `ev` | 含义 |
|---|---|---|
| `SessionStart`      | `session` | 会话刚开 |
| `UserPromptSubmit`  | `user`    | ★ 主人刚按回车 ⇒ 桌宠 0 秒级半睁 |
| `Stop`              | `stop`    | 我这一轮说完了 ⇒ 桌宠再挂 25 s 尾巴回常态 |

★ **DSH 没有 `PreCompact`**（Claude Code 桥接目前只支持 7 个事件，不含压缩事件）
  ⇒ 压缩后注入的协议可能被压掉。对策：**同一个会话里间隔超过 `REINJECT_S`（40 min）
  就再注入一次**，既补压缩、又不至于每句话都烧 token。

## 三条设计红线（都是从 WorkBuddy 版 `fairy_hook.py` 继承的教训）

1. **绝不 import 桌宠的其它模块** —— 那些模块**一 import 就往 stdout 打东西**
   （`[fast] 缓存载入 …`），而钩子的 stdout **就是**通信通道 ⇒ 会被污染成非法 JSON。
2. **永远 exit 0** —— 退出码 **2 = 阻止操作** ⇒ 绝不能漏出去。宁可什么都不做。
3. **输出必须是 stdout 上的 JSON**，且带 `hookSpecificOutput.hookEventName`
   —— DSH 会按事件名校验，名字不对整块上下文都被丢弃。

★ 关掉它（不改配置）：在桌宠目录建一个空文件 `fairy_dsh_off`。
★ 兜底开关：`FAIRY_DSH_LOG=<别的路径>` 把事件流指到别处（安装器的自检就是这么做的，
  免得自检把主人的真实事件流写花）。
"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_FILE = os.path.join(HERE, u"fairy_dsh_bridge_prompt.md")
LOG_FILE = os.path.join(HERE, u"fairy_dsh_bridge.log")
#   ★ 事件流：`fairy_activity_dsh.EVENT_FILE` 读的是同一个文件（同名、同环境变量）
EVENT_FILE = ((os.environ.get(u"FAIRY_DSH_LOG") or u"").strip()
              or os.path.join(HERE, u"dsh_events.jsonl"))
OFF_FILE = os.path.join(HERE, u"fairy_dsh_off")
#   ★★ 标记文件放**桌宠目录里**，不放 %TEMP%：
#      DSH 的钩子进程可能被**沙箱**约束（默认 `workspace-write`），
#      往工作区**外面**写是会被拒的 —— 而桌宠目录一定在工作区里。
#      （WorkBuddy 版放 %TEMP% 没问题，那是因为它的钩子不受沙箱管。）
FLAG_DIR = os.path.join(HERE, u"_flags_dsh")
KEEP_FLAGS = 200            # 标记文件最多留这么多（按 mtime 清）
LOG_MAX = 200000            # 日志超过就截断
EVENT_MAX = 2 * 1024 * 1024  # 事件流超过 2 MB 就裁到最近 512 KB
EVENT_KEEP = 512 * 1024
REINJECT_S = 2400.0         # 同一会话里隔这么久再注入一次（DSH 没有 PreCompact）

#   DSH 钩子名 → 事件流里的名字
EVENTS = {
    u"SessionStart": u"session",
    u"UserPromptSubmit": u"user",
    u"Stop": u"stop",
}


def project_key(path):
    u"""工作目录 → 项目标识（**必须与 `fairy_activity.project_key` / `fairy_activity_dsh`
    完全同一条规则**，否则 `_prog\\progress_<项目>.json` 对不上）。

    `E:\\AI成图实践\\Fairy` → `e-AI成图实践-Fairy`
    """
    p = os.path.abspath(path or u".")
    drive, rest = os.path.splitdrive(p)
    return (drive.rstrip(u":").lower() + u"-"
            + rest.strip(u"\\/").replace(u"\\", u"-").replace(u"/", u"-"))


def log(msg):
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX:
            with io.open(LOG_FILE, u"r", encoding=u"utf-8", errors=u"replace") as f:
                tail = f.read()[-40000:]
            with io.open(LOG_FILE, u"w", encoding=u"utf-8", newline=u"") as f:
                f.write(u"…（截断）\n" + tail)
        with io.open(LOG_FILE, u"a", encoding=u"utf-8", newline=u"") as f:
            f.write(u"%s  %s\n" % (time.strftime(u"%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def append_event(ev, sid, proj, extra=None):
    u"""往事件流追一行。**一行一次 write**，尽量原子。

    ★ 这是桌宠"我在不在干活"的**唯一**事实来源之一（另一个是 DSH 的投影缓存）。
      所以写失败要留痕（`fairy_dsh_bridge.log`），但**绝不能抛**。
    """
    try:
        d = {u"t": time.time(), u"ev": ev, u"s": sid or u"", u"p": proj or u""}
        if extra:
            d.update(extra)
        line = json.dumps(d, ensure_ascii=False) + u"\n"
        _rotate_events()
        with io.open(EVENT_FILE, u"a", encoding=u"utf-8", newline=u"") as f:
            f.write(line)
            f.flush()
        return True
    except Exception as e:
        log(u"append_event failed: %r" % (e,))
        return False


def _rotate_events():
    u"""事件流太大 ⇒ 裁到最近 512 KB（保留整行：从第一个换行之后开始）。"""
    try:
        if not os.path.exists(EVENT_FILE) or os.path.getsize(EVENT_FILE) <= EVENT_MAX:
            return
        with io.open(EVENT_FILE, u"rb") as f:
            f.seek(-EVENT_KEEP, os.SEEK_END)
            tail = f.read()
        i = tail.find(b"\n")
        if i >= 0:
            tail = tail[i + 1:]
        tmp = EVENT_FILE + u".tmp"
        with io.open(tmp, u"wb") as f:
            f.write(tail)
        os.replace(tmp, EVENT_FILE)
    except Exception as e:
        log(u"rotate events failed: %r" % (e,))


def emit(event, text):
    u"""把「注入内容」按官方格式写到 **stdout**。

    ★ 用 `os.write(1, …)` 而不是 `print()`：前者不依赖 `sys.stdout` 对象是否存在。
    """
    payload = {u"continue": True,
               u"suppressOutput": True,
               u"hookSpecificOutput": {u"hookEventName": event,
                                       u"additionalContext": text}}
    data = json.dumps(payload, ensure_ascii=False).encode(u"utf-8")
    try:
        os.write(1, data)
        return True
    except Exception as e:
        log(u"emit via fd1 failed: %r" % (e,))
    try:
        sys.stdout.write(data.decode(u"utf-8"))
        sys.stdout.flush()
        return True
    except Exception as e:
        log(u"emit via sys.stdout failed: %r" % (e,))
    return False


def find_python():
    u"""找一个给 **AI 照着抄**的解释器（写进注入正文的命令里）。

    ★★ 顺序**与 WorkBuddy 版不同**：这个桥接是给 **DSH** 用的，
      所以 DSH 自带的运行时排第一（不依赖 WorkBuddy 的 venv），
      其次才是 `python_path.txt` / 当前解释器。

    ★★★ 2026-10-19 实测：**必须是 `python.exe`**。DSH 把钩子（以及 AI 的工具调用）
      放进 **Windows 沙箱**里跑，在这种环境里 `pythonw.exe` **静默失败**：
      钩子的 `hook/result` 显示 `exitCode: 0`，但 printf 到文件是 0 字节、
      事件流一条没写；同一份命令换 `python.exe` 立刻正常。
      （WorkBuddy 版必须用 pythonw 是为了不闪黑框；DSH 的 shell 本身是隐藏的，
        不存在黑框问题 —— 反之 pythonw 在这里是"看着成功、其实没干"。）
    """
    def as_console(p):
        p = p or u""
        if os.path.basename(p).lower() == u"pythonw.exe":
            q = os.path.join(os.path.dirname(p), u"python.exe")
            if os.path.isfile(q):
                return q
        return p

    env = (os.environ.get(u"FAIRY_PYTHON") or u"").strip()
    if env and os.path.isfile(env):
        return as_console(env)
    home = os.path.expanduser(u"~")
    dsh_home = (os.environ.get(u"DSH_HOME") or u"").strip() or os.path.join(home, u".dsh")
    try:
        base = os.path.join(dsh_home, u"dsh-runtimes")
        for root, _dn, fn in os.walk(base):
            if u"python.exe" in fn:
                return os.path.join(root, u"python.exe")
    except Exception:
        pass
    for p in (os.path.join(HERE, u"python_path.txt"),
              os.path.join(os.path.dirname(HERE), u"python_path.txt")):
        try:
            if not os.path.isfile(p):
                continue
            cand = io.open(p, encoding=u"utf-8", errors=u"replace").read().strip().strip(u'"')
            if os.path.isdir(cand):
                cand = os.path.join(cand, u"python.exe")
            if os.path.isfile(cand):
                return as_console(cand)
        except Exception:
            pass
    exe = sys.executable or u""
    if exe:
        return as_console(exe)
    return u"python.exe"


def read_prompt(proj):
    u"""读模板 → 替换占位符 → 注入正文。

    ★ 替换进去的路径**一律转成正斜杠**：这份正文是给 **AI** 看的，
      它照着敲命令时可能走 bash（DSH 同时提供 PowerShell 与 bash），
      反斜杠会被 bash 当转义吃掉（WorkBuddy 版栽过这个坑，整轮白测）。
    """
    try:
        with io.open(PROMPT_FILE, u"r", encoding=u"utf-8", errors=u"replace") as f:
            txt = f.read()
    except Exception as e:
        log(u"read prompt failed: %r" % (e,))
        txt = (u"这台机器上有一只 Fairy 桌宠（在 {FAIRY_DIR}）。接活时先跑 "
               u"`fairy_notify.py --work \"接单：…\"`，干完发 `--reply \"…\"`。")

    def fwd(s):
        return (s or u"").replace(u"\\", u"/")

    return (txt.replace(u"{FAIRY_PY}", fwd(find_python()))
               .replace(u"{FAIRY_DIR}", fwd(HERE))
               .replace(u"{PROJECT}", proj or u"（没识别出来）"))


def flag_path(sid):
    safe = u"".join(c for c in (sid or u"nosession") if c.isalnum() or c in u"-_")[:64]
    return os.path.join(FLAG_DIR, safe + u".flag")


def need_inject(sid):
    u"""这个会话**现在要不要注入**？

    · 没标记 ⇒ 要（第一次）；
    · 标记超过 `REINJECT_S` ⇒ 要（DSH 没有 PreCompact，压缩后可能把协议压掉了）。
    """
    try:
        fp = flag_path(sid)
        if not os.path.exists(fp):
            return True
        return (time.time() - os.path.getmtime(fp)) > REINJECT_S
    except Exception:
        return True


def touch_flag(sid, prune=True):
    try:
        if not os.path.isdir(FLAG_DIR):
            os.makedirs(FLAG_DIR)
        with io.open(flag_path(sid), u"w", encoding=u"utf-8", newline=u"") as f:
            f.write(time.strftime(u"%Y-%m-%d %H:%M:%S"))
        if prune:
            _prune_flags()
    except Exception as e:
        log(u"touch_flag failed: %r" % (e,))


def _prune_flags():
    try:
        names = os.listdir(FLAG_DIR)
        if len(names) <= KEEP_FLAGS:
            return
        full = [(os.path.getmtime(os.path.join(FLAG_DIR, n)), n) for n in names]
        full.sort()
        for _t, n in full[:len(full) - KEEP_FLAGS]:
            try:
                os.remove(os.path.join(FLAG_DIR, n))
            except Exception:
                pass
    except Exception:
        pass


def read_stdin_json():
    u"""读 stdin 上的 payload —— **必须按字节读、显式 UTF-8 解码**。

    ★★★ 2026-10-19 实测踩到的坑：`sys.stdin.read()` 在 Windows 上按**系统 ANSI
      代码页**（本机 cp936/GBK）解码，而 DSH 写进来的是 **UTF-8** JSON
      ⇒ 中文路径被解成 `e-AI鎴愬浘瀹炶返-Fairy`（乱码）⇒ 项目标识全错、
        进度文件对不上、`--item` 被"跳步防护"乱拒。
      ⇒ 走 `sys.stdin.buffer`（原始字节）自己解 UTF-8，与系统语言无关。
      ★ `pythonw` 下 `sys.stdin` 也可能是 `None`，这里一并兜住。
    """
    raw = b""
    try:
        buf = getattr(sys.stdin, "buffer", None)
        if buf is not None:
            raw = buf.read()
        elif sys.stdin is not None:
            raw = sys.stdin.read()
            if isinstance(raw, bytes):
                return _parse(raw)
            return _parse((raw or u"").encode(u"utf-8", "replace"))
    except Exception as e:
        log(u"read stdin failed: %r" % (e,))
        return {}
    return _parse(raw)


def _parse(raw):
    try:
        txt = raw.decode(u"utf-8", u"replace") if isinstance(raw, bytes) else (raw or u"")
        d = json.loads(txt) if txt.strip() else {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def main():
    u"""跑一次钩子 → **退出码**（恒 0）。

    ★ 早退一律用 `return 0`（而不是 `os._exit`）—— 这样本文件**既能当脚本跑、
      也能被测试脚本 import**（否则 import 就会把测试进程杀掉）。
      `cli()` 才是那个真正 `os._exit` 的人。
    """
    if os.path.exists(OFF_FILE):
        return 0                               # ★ 开关：主人建了这个文件 ⇒ 什么都不做

    data = read_stdin_json()

    #   ★★★ 2026-10-19 实测：DSH 在 **Windows 沙箱**里跑钩子时，payload（stdin）
    #     到不了本进程 —— 会话日志里 `hook/result` 是 `exitCode: 0`，但事件流一条没写、
    #     桥接日志一行没有（= 本脚本压根没拿到 `hook_event_name`，走了"不认识就安静退出"）。
    #     ⇒ **不许依赖 stdin**：事件名同时用**命令行参数**传进来（见 dsh_hooks.json，
    #       三个事件各写一条命令，末尾带 `SessionStart` / `UserPromptSubmit` / `Stop`）。
    #     ⇒ `cwd` 同理：钩子进程的工作目录**就是**会话工作区（DSH 明确保证），
    #       所以 `os.getcwd()` 与 payload 里的 `cwd` 等价。
    #     ⇒ `session_id` 拿不到时，退化成"按项目"记标记：同一项目 40 分钟内只注入一次。
    arg_event = sys.argv[1] if len(sys.argv) > 1 else u""
    event = data.get(u"hook_event_name") or arg_event
    if not event:
        #   ★ 真拿不到就留一行证据（否则排障只能靠猜：见上面那次实测）
        log(u"★ 没有事件名（stdin 与 argv 都空）—— 检查 dsh_hooks.json 的命令是否带事件名")
        return 0
    ev = EVENTS.get(event)
    if ev is None:
        return 0                               # 不认得的事件：安静退出
    cwd = data.get(u"cwd") or os.getcwd()
    proj = project_key(cwd)
    sid = data.get(u"session_id") or (u"proj:" + proj if proj else u"")

    # ① 先落事件（哪怕后面注入失败，桌宠也该知道"主人在说话"）
    ok = append_event(ev, sid, proj, {u"src": data.get(u"source") or u""})

    # ② 再决定要不要注入协议
    inj = False
    if ev in (u"session", u"user"):
        #   ★★ SessionStart = **新会话开张** ⇒ 无条件注入（并刷新标记）。
        #   为什么要"无条件"：DSH 沙箱里拿不到 session_id（见上面的实测），标记只能
        #   按**项目**记 ⇒ 若也按 40 分钟节流，同一个项目里 5 分钟后开的**新会话**
        #   就再也拿不到协议了。而 SessionStart 本身就是"新会话"的权威信号，
        #   刷新标记正好让紧随其后的 UserPromptSubmit **不重复**注入。
        if ev == u"session":
            if emit(event, read_prompt(proj)):
                inj = True
                touch_flag(sid)
        elif need_inject(sid):
            if emit(event, read_prompt(proj)):
                inj = True
                touch_flag(sid)

    log(u"%-16s proj=%-28s sid=%s  event=%s  inject=%s"
        % (event, proj, (sid or u"-")[:8], u"ok" if ok else u"★失败",
           u"yes" if inj else u"no"))
    return 0


def cli():
    u"""脚本入口：**恒 0 退出**。

    ★★ 退出码 2 在钩子规范里是「**阻止操作**」⇒ 绝不允许漏出去。
    """
    code = 0
    try:
        code = main() or 0
    except Exception as e:
        try:
            log(u"UNCAUGHT %r" % (e,))
        except Exception:
            pass
        code = 0
    if code == 2:
        code = 0                               # 兜底：绝不放行 2
    os._exit(code)


if __name__ == u"__main__":
    cli()
