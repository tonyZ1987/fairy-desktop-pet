# -*- coding: utf-8 -*-
u"""fairy_hook.py —— **桥接件**：让任意一台机器上的 AI 自动知道"要去配合 Fairy 桌宠"。

它挂在 WorkBuddy 的 hook 上（由 `install_agent_bridge.vbs` 一键装）：

| 事件 | 触发 | 本脚本干什么 |
|---|---|---|
| `SessionStart` | 新会话/清空会话 | 注入 **完整**协议 |
| `UserPromptSubmit` | **每次**发消息 | **每个会话只注入一次**（覆盖"装完之后的老任务"） |
| `PreCompact` | 上下文即将压缩 | 把标记清掉 ⇒ 压缩后会**重新注入**一次 |

★★ 三条设计红线（都是踩过的坑）：

1. **绝不 import 桌宠的其它模块** —— 那些模块**一 import 就往 stdout 打东西**
   （`[fast] 缓存载入 …`），而钩子的 stdout **就是**通信通道 ⇒ 会被污染成非法 JSON。
   所以本文件**零依赖、自包含**。
2. **永远 exit 0** —— 退出码 **2 = 阻止操作**（官方规范）。宁可什么都不做，
   也不许影响用户的正常使用。所以全程 try/except，最后 `os._exit(0)`。
3. **输出必须走 stdout、且只能是 JSON** —— `pythonw` 在 stdout 被重定向时
   `sys.stdout` 是好的（2026-09-28 实测），所以用 `pythonw` 跑**不会闪黑框**。

★ 关掉它的办法（不用改配置）：在桌宠目录建一个空文件 `fairy_bridge_off`。
★ 它会顺手把当前项目名写进 `activity_project.txt` —— 主人就不用手工维护关注清单了。
"""
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT_FILE = os.path.join(HERE, u"fairy_bridge_prompt.md")
#   ★ WorkBuddy 把每个项目的会话记录放在这个目录下，**目录名就是"项目名"**
#     （编码规则：盘符小写 + 其余路径把分隔符换成 `-`，例：E:\a\b → e-a-b）
#     `FAIRY_BRIDGE_PROJECTS` 只为测试留的口子。
PROJECTS_ROOT = ((os.environ.get(u"FAIRY_BRIDGE_PROJECTS") or u"").strip()
                 or os.path.join(os.path.expanduser(u"~"), u".workbuddy", u"projects"))
#   ★★ 事实登记表：钩子每次触发时把"这个会话 ↔ 哪个工作目录"记下来。
#     这一轮**只记录、不改任何判据** —— 先拿它跟"扫盘结论"对照，看有没有分歧。
REG_FILE = ((os.environ.get(u"FAIRY_BRIDGE_REG") or u"").strip()
            or os.path.join(HERE, u"hook_sessions.json"))
OFF_FILE = os.path.join(HERE, u"fairy_bridge_off")
WATCH_FILE = os.path.join(HERE, u"activity_project.txt")
LOG_FILE = os.path.join(HERE, u"fairy_bridge.log")
FLAG_DIR = os.path.join(os.environ.get(u"TEMP") or os.environ.get(u"TMP") or HERE,
                        u"fairy_bridge")
KEEP_FLAGS = 200                 # 标记文件最多留这么多（按 mtime 清）
WATCH_MAX = 12                   # `activity_project.txt` 里最多留几个项目（新的在上）
LOG_MAX = 200000                 # 日志超过就截断


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


def emit(event, text):
    u"""把「注入内容」按官方格式写到 **stdout**。

    ★ 用 `os.write(1, …)` 而不是 `print()` —— 前者不依赖 `sys.stdout` 对象是否存在，
      在 `pythonw` 下更稳（实测被重定向时两者都行，但 fd 直写少一层）。
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


def find_pythonw():
    u"""找一个**不闪黑框**的解释器（给注入的正文里写命令用）。

    顺序：`%FAIRY_PYTHON%` → `python_path.txt`（启动器同一套）→ 托管 venv → 当前解释器。
    """
    env = (os.environ.get(u"FAIRY_PYTHON") or u"").strip()
    if env and os.path.isfile(env):
        return env
    for p in (os.path.join(HERE, u"python_path.txt"),
              os.path.join(os.path.dirname(HERE), u"python_path.txt")):
        try:
            if not os.path.isfile(p):
                continue
            cand = io.open(p, encoding=u"utf-8", errors=u"replace").read().strip().strip(u'"')
            if os.path.isdir(cand):
                cand = os.path.join(cand, u"pythonw.exe")
            if os.path.isfile(cand):
                return cand
        except Exception:
            pass
    home = os.path.expanduser(u"~")
    for sub in (os.path.join(home, u".workbuddy", u"binaries", u"python", u"envs"),
                os.path.join(home, u".workbuddy", u"binaries", u"python", u"versions")):
        try:
            for root, _dn, fn in os.walk(sub):
                if u"pythonw.exe" in fn:
                    return os.path.join(root, u"pythonw.exe")
        except Exception:
            pass
    #   兜底：用运行本钩子的解释器（把 python.exe 换成 pythonw.exe）
    exe = sys.executable or u""
    if exe:
        w = os.path.join(os.path.dirname(exe), u"pythonw.exe")
        if os.path.isfile(w):
            return w
        return exe
    return u"pythonw.exe"


def _enc_dir(path):
    u"""把工作目录**编码成 WorkBuddy 的项目目录名**：盘符小写 + 分隔符换成 `-`。

    例：`E:\\AI成图实践\\Fairy` → `e-AI成图实践-Fairy`；
        `C:\\Users\\me\\WorkBuddy\\2026-09-28-13-13-33` → `c-Users-me-WorkBuddy-2026-09-28-13-13-33`。
    """
    s = (path or u"").replace(u"/", u"\\").rstrip(u"\\")
    parts = [x for x in s.split(u"\\") if x]
    if not parts:
        return u""
    head = parts[0].rstrip(u":")
    if len(head) == 1:                       # 盘符 → 小写
        head = head.lower()
    parts[0] = head
    return u"-".join(parts)


def proj_from(data):
    u"""当前**项目目录名**（编码形式）—— `CODEBUDDY_PROJECT_DIR` 优先，其次 payload 的 `cwd`。

    ★★★ 2026-09-28 修：原版返回的是路径**末段**（`Fairy`），而
      `activity_project.txt` 里写的是**编码目录名**（`e-AI成图实践-Fairy`）
      ⇒ 钩子自动写进去的那行**永远匹配不到**，纯属垃圾（实测已抓到一行 `Fairy`）。
      更糟的是那个文件"写了就只认这几行"，垃圾行会**白占名额**。

    ★ 三条判据（依次）：
      1. 编码后**真实存在** ⇒ 用它；
      2. workspace 是项目根的**子目录**（如 `…\\Fairy\\pet-v2`）⇒ 取**最长前缀**能对上的那个；
      3. 都认不出来 ⇒ **返回空**（宁可不写，也不往清单里塞垃圾）。
    """
    raw = u""
    for v in (os.environ.get(u"CODEBUDDY_PROJECT_DIR"),
              os.environ.get(u"CLAUDE_PROJECT_DIR"),
              data.get(u"cwd")):
        v = (v or u"").strip()
        if v:
            raw = v
            break
    if not raw:
        return u""
    try:
        names = set(os.listdir(PROJECTS_ROOT))
    except Exception:
        return u""
    if raw in names:                         # 传进来的本来就是目录名
        return raw
    enc = _enc_dir(raw)
    if enc in names:
        return enc
    best = u""
    for n in names:
        if (enc == n or enc.startswith(n + u"-")) and len(n) > len(best):
            best = n
    return best                              # 认不出 ⇒ 空串


def note_session(data, proj, event):
    u"""把钩子白拿到的**事实**记下来：`session_id ↔ cwd ↔ 项目目录 ↔ transcript`。

    ★★ 这一轮**只记录、不改任何判据**（零风险）。
      目的：拿它和桌宠"扫盘猜"的结论对照；**若长期一致**，才有资格谈切换。
    ★ 自检（`FAIRY_BRIDGE_NOWATCH`）不写 —— 免得测试污染这张表。
    """
    sid = (data.get(u"session_id") or u"").strip()
    if not sid:
        return
    j = {}
    try:
        if os.path.isfile(REG_FILE):
            with io.open(REG_FILE, u"r", encoding=u"utf-8", errors=u"replace") as f:
                j = json.loads(f.read() or u"{}")
        if not isinstance(j, dict):
            j = {}
    except Exception:
        j = {}
    tp = data.get(u"transcript_path") or u""
    j[sid] = {u"cwd": data.get(u"cwd") or u"",
              u"proj": proj or u"",
              u"transcript": os.path.basename(tp) if tp else u"",
              u"event": event or u"",
              u"ts": time.strftime(u"%Y-%m-%d %H:%M:%S")}
    if len(j) > 40:                          # 只留最近 40 个会话
        #   ★ 2026-09-28：原写法是「按 ts 倒序取前 40」，但**同一秒**里的多条 ts 完全相同
        #     ⇒ 排序稳定反而把**最旧的**留下来了（被 v2_o4 当场抓到）。
        #     ⇒ 改成「按 ts 升序排稳、取**末尾** 40 条」：既保最新，同 ts 时也保后写入的。
        items = sorted(j.items(), key=lambda kv: (kv[1] or {}).get(u"ts") or u"")
        j = dict(items[-40:])
    tmp = REG_FILE + u".tmp"
    with io.open(tmp, u"w", encoding=u"utf-8", newline=u"") as f:
        f.write(json.dumps(j, ensure_ascii=False, indent=1))
    os.replace(tmp, REG_FILE)                # 原子替换（多会话同时触发也不打架）


def touch_watch(proj):
    u"""把项目名追加进 `activity_project.txt`（去重、最多 12 行、新的在上面）。

    ★ 这条是给主人的便利：**不用手工维护"盯哪些项目"** —— 他做过活的项目自己进来。

    ★★ **注释必须原样保留**（2026-09-28 自查抓到的 bug）：
      那个文件顶上有一大段 `#` 说明（怎么编码目录名、几行生效、热读周期）。
      第一版我"读所有行→过滤空行→写回"，会把**整段说明冲掉**。
      ⇒ 现在只动**非注释行**，注释按原顺序留在前面。
    """
    if not proj:
        return
    try:
        lines = []
        if os.path.isfile(WATCH_FILE):
            with io.open(WATCH_FILE, u"r", encoding=u"utf-8", errors=u"replace") as f:
                lines = f.read().splitlines()
        comments = [x for x in lines if x.lstrip().startswith(u"#")]
        act = [x.strip() for x in lines
               if x.strip() and not x.lstrip().startswith(u"#")]
        if act[:1] == [proj]:
            return                        # 已经在最上面 ⇒ **不写盘**（否则每句都写文件）
        act = [x for x in act if x != proj]
        act.insert(0, proj)
        act = act[:WATCH_MAX]
        body = (comments + [u""] if comments else []) + act
        with io.open(WATCH_FILE, u"w", encoding=u"utf-8", newline=u"") as f:
            f.write(u"\n".join(body) + u"\n")
    except Exception as e:
        log(u"touch_watch failed: %r" % (e,))


def read_prompt(proj):
    u"""读模板 → 替换占位符 → 注入正文。

    ★★★ 2026-09-28：替换进去的路径**一律转成正斜杠**。
      为什么：这份正文是给 **AI** 看的，它照着敲命令时走的是 **Bash**，
      而 bash 会把反斜杠当转义吃掉（`C:\\Users\\…` → `C:Users…` ⇒ 找不到命令）。
      —— 这正是本项目钩子配置自己栽过的那个坑（见 REFERENCE AI.9）；
      指令里的路径不能只是"看着对"，得**能在对方那个 shell 里跑起来**。
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

    return (txt.replace(u"{FAIRY_PY}", fwd(find_pythonw()))
               .replace(u"{FAIRY_DIR}", fwd(HERE))
               .replace(u"{PROJECT}", proj or u"（没识别出来）"))


def flag_path(sid):
    safe = u"".join(c for c in (sid or u"nosession") if c.isalnum() or c in u"-_")[:64]
    return os.path.join(FLAG_DIR, safe + u".flag")


def prune_flags():
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


def main():
    u"""跑一次钩子 → **退出码**（恒 0）。

    ★ 早退一律用 `return 0`（而不是 `os._exit`）—— 这样本文件**既能当脚本跑、
      也能被测试脚本 import**（否则 import 就会把测试进程杀掉）。
      `cli()` 才是那个真正 `os._exit` 的人。
    """
    if os.path.exists(OFF_FILE):
        return 0                               # ★ 开关：主人建了这个文件 ⇒ 什么都不做

    try:
        raw = sys.stdin.read() if sys.stdin is not None else u""
    except Exception:
        raw = u""
    try:
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    event = data.get(u"hook_event_name") or (sys.argv[1] if len(sys.argv) > 1 else u"")
    sid = data.get(u"session_id") or u""
    proj = proj_from(data)
    #   ★★ 把钩子白拿到的"事实"记下来（只记录、不改判据）。
    #     ★ 单独一个开关 `FAIRY_BRIDGE_NOREG`：这样测试能**只关掉它、留着别的**，
    #       端到端验注册表时不必连它一起关（`FAIRY_BRIDGE_REG` 把路径指到临时文件即可）。
    if not os.environ.get(u"FAIRY_BRIDGE_NOREG"):
        try:
            note_session(data, proj, event)
        except Exception as e:
            log(u"note_session failed: %r" % (e,))

    #   ★★ 2026-09-28：自检/测试时**不许动主人的关注清单**。
    #     安装器的自检会拿 `cwd=桌宠目录` 喂一份假 payload ⇒ `proj` 会是
    #     `pet-v2` / `Fairy` 这种**根本不是项目**的名字，写进去就把清单污染了
    #     （而且清单是"写了就只认这几行"，垃圾条目会挤掉真的项目）。
    #     ⇒ 用环境变量关掉这一步，别让自检产生副作用。
    if not os.environ.get(u"FAIRY_BRIDGE_NOWATCH"):
        try:
            touch_watch(proj)
        except Exception:
            pass

    if event not in (u"SessionStart", u"UserPromptSubmit", u"PreCompact"):
        return 0                               # 不认得的事件：安静退出

    if event == u"PreCompact":
        #   压缩会把注入的内容一起压掉 ⇒ 把标记清掉，让下一次 UserPromptSubmit 重新注入
        try:
            fp = flag_path(sid)
            if os.path.exists(fp):
                os.remove(fp)
            log(u"PreCompact ⇒ 清掉标记（压缩后会重新注入）")
        except Exception:
            pass
        return 0

    if event == u"UserPromptSubmit":
        try:
            if not os.path.isdir(FLAG_DIR):
                os.makedirs(FLAG_DIR)
            fp = flag_path(sid)
            if os.path.exists(fp):
                return 0                       # ★ 这个会话已经注入过 ⇒ 不重复烧 token
            with io.open(fp, u"w", encoding=u"utf-8", newline=u"") as f:
                f.write(time.strftime(u"%Y-%m-%d %H:%M:%S"))
            prune_flags()
        except Exception as e:
            log(u"flag failed: %r" % (e,))

    ok = emit(event, read_prompt(proj))
    log(u"%s  注入 %s  proj=%r  sid=%s  ok=%s"
        % (event, u"成功" if ok else u"★失败", proj, sid[:8], ok))
    return 0


def cli():
    u"""脚本入口：**恒 0 退出**。

    ★★ 退出码 2 在钩子规范里是「**阻止操作**」⇒ 绝不允许漏出去。
      所以这里包一层，不管里面发生什么，都是 `os._exit(0)`。
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
