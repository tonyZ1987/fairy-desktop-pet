# -*- coding: utf-8 -*-
u"""agent_bridge_install.py —— 一键「让 AI 会配合桌宠」的安装器（可卸载、可查看状态）。

它干五件事：
  1. 找到 **WorkBuddy 的用户级配置**（`~/.workbuddy/settings.json`）；
  2. **先备份**，再把三条钩子**合并**进去（**绝不动别的键**）；
  3. 装之前先**自测钩子本身**（喂假 payload，验它吐合法 JSON、退出码 0、日志有新增）；
  4. ★★ 自测**按 WorkBuddy 的方式跑** —— 它是用 **Git Bash** 执行命令的（见下）；
  5. 装完**回读校验**，并打印怎么卸载。

用法：
    python agent_bridge_install.py --install
    python agent_bridge_install.py --uninstall
    python agent_bridge_install.py --status

★★★ 两处"想当然"的教训（都是 2026-09-28 实测出来的，别再犯）

**① 命令必须写成 bash 语法（正斜杠 + 引号）。**
  宿主日志原话：
      [HookExecutor] spawn shell=…\\PortableGit\\…\\bash.exe cmd=C:\\Users\\…\\pythonw.exe "E:\\…\\fairy_hook.py"
      [Warning] [HookExecutor] abnormal exit code=127
      Hook exited with non-blocking error code 127:
        /usr/bin/bash: line 1: C:Userszhengdingming.workbuddybinaries…pythonw.exe: command not found
  ⇒ **bash 把反斜杠当转义吃掉了** ⇒ 命令根本找不到 ⇒ 钩子**静默不生效**。
  ⇒ 命令写成 `"C:/Users/…/pythonw.exe" "E:/…/fairy_hook.py"`。
  ★★ 而且**必须用 bash 跑一遍才算自检过** —— 直接 exec 是好的，走 bash 才复现出问题。
     （这条我栽了整整一轮：自检用的是直接 exec，于是它一路绿灯，真机上却一次没响。）
  ★ 好消息：钩子失败是 **non-blocking** 的，不会阻塞用户的任务、也不会报错给他看。

**② 配置文件的正确位置是 `~/.workbuddy/settings.json`。**
  依据：宿主日志 `[HookManager] event=SessionStart matched 2 distinct hook entries:`
  后面那条命令就是我们的（当时钩子正写在这个文件里）。
  `~/.codebuddy/settings.json` 是 Agent CLI 的通用惯例 —— **WorkBuddy 桌面版不读它**。
  ⇒ 第一版按惯例写进了 `.codebuddy`，白费一轮；现在改成"主配置 = `.workbuddy`，
     `.codebuddy` 只用来**清理**我们误写的那份"。

★ 为什么只挂三个事件（**故意不挂 `PreToolUse`**）：
  那一个会在**每次工具调用**前触发 ⇒ 白白给主人的每一步加几十毫秒，收益为零。
  我们只要「新会话」「每条用户消息（每会话一次）」「压缩后重注入」这三个时点。
"""
from __future__ import print_function

import io
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, u"fairy_hook.py")
LOG = os.path.join(HERE, u"fairy_bridge.log")
EVENTS = [u"SessionStart", u"UserPromptSubmit", u"PreCompact"]
#   ★ 认领判据见 `ours()`：**命令指向本目录里的脚本**就算我们的。


# ------------------------------------------------------------------ 找路径
def cfg_paths():
    u"""→ `(主配置, 旧配置)`。

    · 主配置 = **WorkBuddy 真正读的那份**（`~/.workbuddy/settings.json`，认环境变量
      `WORKBUDDY_CONFIG_DIR`）；
    · 旧配置 = CLI 惯例那份（`~/.codebuddy/settings.json`）——
      **只用来清理我们第一版误写进去的条目**，不再往里写。
    · `FAIRY_BRIDGE_DRYRUN=<目录>` ⇒ 两个都指到临时目录，**可离线自测**（不碰真配置）。
    """
    dry = (os.environ.get(u"FAIRY_BRIDGE_DRYRUN") or u"").strip()
    if dry:
        return (os.path.join(dry, u"app", u"settings.json"),
                os.path.join(dry, u"cli", u"settings.json"))
    home = os.path.expanduser(u"~")
    app = (os.environ.get(u"WORKBUDDY_CONFIG_DIR") or u"").strip() \
        or os.path.join(home, u".workbuddy")
    return os.path.join(app, u"settings.json"), os.path.join(home, u".codebuddy", u"settings.json")


def bash_exe():
    u"""找 WorkBuddy 用的那个 Git Bash（自检必须走它，否则测不出真问题）。

    顺序：环境变量 → `~/.workbuddy/binaries/PortableGit/versions/*/bin/bash.exe`（取最新）
    → PATH 里的 bash。
    """
    env = (os.environ.get(u"FAIRY_BRIDGE_BASH") or u"").strip()
    if env and os.path.isfile(env):
        return env
    home = os.path.expanduser(u"~")
    base = os.path.join(home, u".workbuddy", u"binaries", u"PortableGit", u"versions")
    found = []
    try:
        for v in os.listdir(base):
            p = os.path.join(base, v, u"bin", u"bash.exe")
            if os.path.isfile(p):
                found.append(p)
    except Exception:
        pass
    if found:
        found.sort(key=lambda p: os.path.getmtime(p))
        return found[-1]
    try:
        w = shutil.which(u"bash")
        if w:
            return w
    except Exception:
        pass
    return u""


def find_pythonw():
    u"""不闪黑框的解释器。顺序与桌宠启动器一致。"""
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
    exe = sys.executable or u""
    w = os.path.join(os.path.dirname(exe), u"pythonw.exe")
    return w if os.path.isfile(w) else exe


def build_command(pyw, hook):
    u"""→ 写进配置的**命令串**。

    ★★★ 必须是 **bash 语法**：正斜杠 + 双引号。
      反斜杠会被 Git Bash 当转义吃掉（`C:\\Users\\…` → `C:Users…` ⇒ exit 127）。
    """
    return u'"%s" "%s"' % (pyw.replace(u"\\", u"/"), hook.replace(u"\\", u"/"))


# ------------------------------------------------------------------ 自测
def _log_lines():
    try:
        return len([x for x in io.open(LOG, u"r", encoding=u"utf-8",
                                       errors=u"replace").read().splitlines() if x.strip()])
    except Exception:
        return 0


def selftest(pyw, bash):
    u"""喂一份假 payload，验钩子「吐合法 JSON」+「退出码 0」+「日志有新增」。

    ★★★ 关键：**要用 Git Bash 跑**（`bash -c "<cmd>"`）—— 那是 WorkBuddy 的真实调用方式。
      直接 exec 是测不出"反斜杠被吃"这类问题的（我上一版就是这么白测的一轮）。
    ★ 这条是整个安装器最值钱的一步：装完才发现钩子吐不出东西
      ⇒ 用户会以为"装好了但没效果"，然后无从排查。
    """
    cmd = build_command(pyw, HOOK)
    print(u"   写进配置的命令：%s" % cmd)
    print(u"   反斜杠检查：%s" % (u"干净（无 \\）"if u"\\" not in cmd else u"★ 还有反斜杠，bash 会吃"))
    payload = json.dumps({u"hook_event_name": u"SessionStart",
                          u"session_id": u"selftest",
                          u"cwd": HERE,
                          u"source": u"startup"}, ensure_ascii=False)
    #   ★★ 自检**不许动**主人的关注清单（`activity_project.txt`）——
    #     这里的 cwd 是桌宠目录，`proj` 会算成 `pet-v2` 这种非项目名；
    #     写进去就把清单污染了（清单是"写了就只认这几行"）。
    env = dict(os.environ)
    env[u"FAIRY_BRIDGE_NOWATCH"] = u"1"
    env[u"FAIRY_BRIDGE_NOREG"] = u"1"    # 别把自检写进事实登记表

    runs = []
    if bash:
        runs.append((u"按 WorkBuddy 的方式（Git Bash -c）", [bash, u"-c", cmd]))
    else:
        print(u"   ★ 没找到 Git Bash ⇒ 只能直接 exec 自测，**测不出路径转义问题**")
    runs.append((u"直接 exec（对照）", [pyw, HOOK]))

    ok = True
    for label, argv in runs:
        n0 = _log_lines()
        try:
            r = subprocess.run(argv, input=payload.encode(u"utf-8"),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
                               env=env)
        except Exception as e:
            print(u"   [X] %s：起不来（%r）" % (label, e))
            ok = False
            continue
        out = (r.stdout or b"").decode(u"utf-8", u"replace").strip()
        err = (r.stderr or b"").decode(u"utf-8", u"replace").strip()
        n1 = _log_lines()
        print(u"   [%s] 退出码=%s ｜ stdout %d 字节 ｜ 日志 %d→%d"
              % (label, r.returncode, len(out), n0, n1))
        if r.returncode != 0:
            print(u"        ★ 退出码非 0 —— 必须修（2 会阻塞操作；127 = 命令没找到）")
            if err:
                print(u"        stderr: %s" % err[:220])
            ok = False
            continue
        try:
            d = json.loads(out)
        except Exception as e:
            print(u"        ★ stdout 不是合法 JSON：%r … %s" % (out[:120], e))
            ok = False
            continue
        ac = (d.get(u"hookSpecificOutput") or {}).get(u"additionalContext") or u""
        print(u"        OK：JSON 合法；注入正文 %d 字符；continue=%s" % (len(ac), d.get(u"continue")))
        if not ac:
            print(u"        ★ 注入正文是空的 —— WorkBuddy 读不到协议，等于没装")
            ok = False
        if n1 <= n0:
            print(u"        ★ 日志没新增 —— 钩子没真跑到写盘那一步")
            ok = False
    return ok


# ------------------------------------------------------------------ 读 / 写
def load_cfg(path):
    u"""读配置 → `(dict, 原文或 None)`。

    ★★ 2026-09-28：**空文件 / 只有空白** 要当成 `{}`，不能直接 `json.loads("")`
      —— 那会抛 JSONDecodeError，整个安装器就崩了。
    """
    if not os.path.isfile(path):
        return {}, None
    t = io.open(path, u"r", encoding=u"utf-8").read()
    if not t.strip():
        return {}, t
    j = json.loads(t)
    if not isinstance(j, dict):
        raise ValueError(u"%s 里不是一个 JSON 对象" % path)
    return j, t


def ours(entry):
    u"""这条 hook 是不是**我们的**？

    ★★ 判据不是"命令里含 `fairy_hook.py`"，而是"**命令指向桌宠目录里的脚本**"
      —— 2026-09-28 实测栽过：我先挂了 `hook_probe.py`（探针）验证通路，
      而老判据只认 `fairy_hook.py` ⇒ 装正式件时**探针会被留在配置里**。
      ⇒ 按**目录归属**认领：这个目录里的任何脚本都算我们的。
      ★ 两种斜杠都要认（命令已改成正斜杠写法）。
    """
    try:
        cmd = (entry.get(u"hooks") or [{}])[0].get(u"command") or u""
    except Exception:
        return False
    flat = cmd.lower().replace(u"\\", u"/")
    return HERE.lower().replace(u"\\", u"/") in flat and u".py" in flat


def strip_ours(hooks):
    u"""把之前装的（**我们的**）统统摘掉 ⇒ 可重复安装、可升级、可换脚本。"""
    removed = 0
    for ev in list(hooks.keys()):
        arr = hooks.get(ev) or []
        keep = []
        for entry in arr:
            if ours(entry):
                removed += 1
                continue
            keep.append(entry)
        if keep:
            hooks[ev] = keep
        else:
            hooks.pop(ev, None)
    return removed


def purge(path, why):
    u"""把**我们的**钩子从这个文件里摘掉；摘空就删 `hooks` 键。→ 摘掉的条数。"""
    if not os.path.isfile(path):
        return 0
    try:
        j, _ = load_cfg(path)
    except Exception as e:
        print(u"   [跳过] %s 解析不了（%r）" % (os.path.basename(path), e))
        return 0
    hooks = j.get(u"hooks")
    if not hooks:
        return 0
    n = strip_ours(hooks)
    if not n:
        return 0
    if hooks:
        j[u"hooks"] = hooks
    else:
        j.pop(u"hooks", None)
    shutil.copy2(path, path + u".bak_fairy_" + time.strftime(u"%Y%m%d_%H%M%S"))
    io.open(path, u"w", encoding=u"utf-8", newline=u"") \
        .write(json.dumps(j, ensure_ascii=False, indent=2) + u"\n")
    print(u"   ✓ %s：摘掉 %d 条（%s）" % (path, n, why))
    return n


def cmd_install(pyw):
    primary, legacy = cfg_paths()
    print(u"=== 安装「AI ↔ Fairy 桌宠」桥接 ===")
    print(u"   主配置（WorkBuddy 真正读的）：%s" % primary)
    print(u"   旧配置（只做清理）          ：%s" % legacy)
    print(u"   钩子脚本：%s" % HOOK)
    print(u"   解释器  ：%s" % pyw)
    if not os.path.isfile(HOOK):
        print(u"\n★ 找不到 %s —— 安装器必须和它放在同一个目录。" % os.path.basename(HOOK))
        return 2
    if not os.path.isdir(os.path.dirname(primary)):
        print(u"\n★ 配置目录不存在：%s（WorkBuddy 装过吗？）" % os.path.dirname(primary))
        return 2

    bash = bash_exe()
    print(u"   Git Bash：%s" % (bash or u"★ 没找到（自检会降级）"))

    print(u"\n=== ① 先自测钩子本身（★ 按 WorkBuddy 的方式：Git Bash）===")
    if not selftest(pyw, bash):
        print(u"\n★ 自测没过 ⇒ **不装**（装了也是个不响的钩子）。")
        return 3

    print(u"\n=== ② 清理旧配置（第一版误写进 `.codebuddy` 的那份）===")
    if not purge(legacy, u"第一版写错地方，摘掉"):
        print(u"   （旧配置里没有我们的条目，不用清）")

    print(u"\n=== ③ 备份并合并到主配置 ===")
    j, old_text = load_cfg(primary)
    if old_text is None:
        print(u"   主配置不存在 ⇒ 新建一个（只放 hooks，别的键一个不加）")
    else:
        bak = primary + u".bak_fairy_" + time.strftime(u"%Y%m%d_%H%M%S")
        shutil.copy2(primary, bak)
        print(u"   备份：%s" % os.path.basename(bak))
    before_others = json.dumps({k: v for k, v in j.items() if k != u"hooks"},
                               sort_keys=True, ensure_ascii=False)

    hooks = j.setdefault(u"hooks", {})
    n = strip_ours(hooks)
    if n:
        print(u"   摘掉旧的 %d 条（可重复安装 / 可升级）" % n)
    c = build_command(pyw, HOOK)
    for ev in EVENTS:
        entry = {u"hooks": [{u"type": u"command", u"command": c, u"timeout": 15}]}
        if ev == u"SessionStart":
            entry[u"matcher"] = u""     # ★ 空串 = 全部匹配；写 "*" 在正则里是非法写法
        hooks.setdefault(ev, []).append(entry)
        print(u"   %-16s +1 条" % ev)

    print(u"\n=== ④ 写回 ===")
    if not os.path.isdir(os.path.dirname(primary)):
        os.makedirs(os.path.dirname(primary))
    io.open(primary, u"w", encoding=u"utf-8", newline=u"") \
        .write(json.dumps(j, ensure_ascii=False, indent=2) + u"\n")

    print(u"\n=== ⑤ 回读校验 ===")
    back = json.loads(io.open(primary, u"r", encoding=u"utf-8").read())
    others = json.dumps({k: v for k, v in back.items() if k != u"hooks"},
                        sort_keys=True, ensure_ascii=False)
    print(u"   ★ 其它键一个字没动？", u"是 OK" if others == before_others else u"★ 否！")
    print(u"   hooks 事件 = %s" % sorted(back.get(u"hooks", {}).keys()))
    ours_n = sum(1 for ev in back.get(u"hooks", {}) for e in back[u"hooks"][ev] if ours(e))
    print(u"   我们装的条数 = %d（应为 %d）%s"
          % (ours_n, len(EVENTS), u"OK" if ours_n == len(EVENTS) else u"★"))

    print(u"\n" + u"=" * 62)
    print(u"装好了。生效方式：")
    print(u"  · 先直接在任意任务里说一句话试试（配置可能是热读的）；")
    print(u"  · 没反应就**重启 WorkBuddy**，或新开一个任务。")
    print(u"\n验证：看 %s 里有没有新记录。" % LOG)
    print(u"取消：双击 uninstall_agent_bridge.vbs。")
    print(u"临时静音（不改配置）：在桌宠目录建一个空文件 fairy_bridge_off。")
    return 0


def cmd_uninstall():
    primary, legacy = cfg_paths()
    print(u"=== 卸载「AI ↔ Fairy 桌宠」桥接 ===")
    total = purge(primary, u"主配置") + purge(legacy, u"旧配置")
    if total == 0:
        print(u"   没找到我们装的条目（可能已经卸过了）")
    else:
        print(u"   共摘掉 %d 条。★ 同样要重启 WorkBuddy 才生效。" % total)
    return 0


def cmd_status():
    primary, legacy = cfg_paths()
    print(u"=== 桥接状态 ===")
    for label, path in ((u"主配置（WorkBuddy 读的）", primary), (u"旧配置（只清理）", legacy)):
        print(u"\n   %s：%s（%s）" % (label, path, u"存在" if os.path.isfile(path) else u"不存在"))
        if not os.path.isfile(path):
            continue
        try:
            j, _ = load_cfg(path)
        except Exception as e:
            print(u"      解析失败：%r" % (e,))
            continue
        hooks = j.get(u"hooks") or {}
        hits = [(ev, e) for ev in hooks for e in hooks[ev] if ours(e)]
        print(u"      我们的 %d 条：" % len(hits))
        for ev, e in hits:
            print(u"         %-16s %s"
                  % (ev, ((e.get(u"hooks") or [{}])[0].get(u"command") or u"")[:84]))
    print(u"\n   钩子脚本：%s（%s）" % (HOOK, u"存在" if os.path.isfile(HOOK) else u"★不存在"))
    print(u"   Git Bash ：%s" % (bash_exe() or u"★ 没找到"))
    print(u"   开关文件：%s" % (u"★ 已静音" if os.path.exists(os.path.join(HERE, u"fairy_bridge_off"))
                              else u"正常（没静音）"))
    if os.path.isfile(LOG):
        rows = [x for x in io.open(LOG, u"r", encoding=u"utf-8", errors=u"replace")
                .read().splitlines() if x.strip()]
        print(u"   日志 %d 条，最后 3 条：" % len(rows))
        for x in rows[-3:]:
            print(u"      %s" % x[:104])
    return 0


def main():
    u"""入口。

    ★★ 为什么把 stdout **一边打印一边攒一份**：
      用户是**双击 `.vbs`** 装的，而 `.vbs` 用 `pythonw` **隐藏窗口**跑本脚本
      ⇒ 控制台输出**他一个字都看不到**。
      ⇒ 所以顺手把输出落到 `agent_bridge_report.txt`，`.vbs` 再读它弹 MsgBox。
    ★ 报告写成 **UTF-16**：VBS 的 `OpenTextFile(..., TristateTrue)` 按 Unicode 读，
      写 UTF-8 会变乱码（`check_fairy.vbs` 当年就踩过，它读的也是 UTF-16）。
    ★★ 摘要里**不许写 Markdown** —— MsgBox 是纯文本，`**加粗**` 会原样显示。
    """
    class _Tee(object):
        def __init__(self, a, b):
            self.a, self.b = a, b

        def write(self, s):
            for f in (self.a, self.b):
                try:
                    f.write(s)
                except Exception:
                    pass

        def flush(self):
            try:
                self.a.flush()
            except Exception:
                pass

    args = [a.lower() for a in sys.argv[1:]]
    buf = io.StringIO()
    real = sys.stdout
    sys.stdout = _Tee(real, buf)
    code = 0
    try:
        if u"--uninstall" in args or u"-u" in args:
            code = cmd_uninstall()
        elif u"--status" in args or u"-s" in args:
            code = cmd_status()
        else:
            code = cmd_install(find_pythonw())
    except Exception as e:
        print(u"\n★ 安装器自己出错了：%r" % (e,))
        code = 9
    finally:
        sys.stdout = real
        try:
            txt = buf.getvalue()
            if code != 0:
                txt += u"\n\n★★ 没装成功（退出码 %s）。把上面这段发给 Fairy 的作者。" % code
            with io.open(os.path.join(HERE, u"agent_bridge_report.txt"),
                         u"w", encoding=u"utf-16", newline=u"") as f:
                f.write(txt)
            mode = (u"卸载" if (u"--uninstall" in args or u"-u" in args)
                    else (u"状态" if (u"--status" in args or u"-s" in args) else u"安装"))
            if mode == u"安装" and code == 0:
                s = (u"装好了：以后你的 AI 干活时，Fairy 会跟着半睁眼 + 走进度条。\n\n"
                     u"下一步：先直接在任意任务里说一句话试试。\n"
                     u"    如果没反应，就重启 WorkBuddy 或新开一个任务。\n\n"
                     u"怎么验证：看下面这个文件有没有新记录\n"
                     u"    %s\\fairy_bridge.log\n\n"
                     u"想关掉：双击 uninstall_agent_bridge.vbs（删干净）\n"
                     u"        或在桌宠目录建一个空文件 fairy_bridge_off（临时静音）"
                     % HERE)
            elif mode == u"安装":
                s = (u"没装成功（退出码 %s）。\n\n完整原因在：\n    %s\\agent_bridge_report.txt\n"
                     u"把那份文件发给 Fairy 的作者就行。" % (code, HERE))
            elif mode == u"卸载" and code == 0:
                s = (u"已卸载（钩子配置清掉了）。\n\n"
                     u"同样要重启 WorkBuddy 才生效。\n桌宠本身还在，不受影响。")
            else:
                s = u"状态已写入：%s\\agent_bridge_report.txt" % HERE
            with io.open(os.path.join(HERE, u"agent_bridge_summary.txt"),
                         u"w", encoding=u"utf-16", newline=u"") as f:
                f.write(s)
        except Exception:
            pass
    return code


if __name__ == u"__main__":
    sys.exit(main())
