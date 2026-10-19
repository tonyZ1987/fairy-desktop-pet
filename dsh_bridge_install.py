# -*- coding: utf-8 -*-
u"""dsh_bridge_install.py —— 把「AI ↔ Fairy 桌宠」桥接装到 **DSH（DeepSeek Harness）** 上。

> 这是 v2.2 的 `agent_bridge_install.py`（WorkBuddy 版）的**对应件**。
> 同一个用途、同一套自检纪律，但挂载点换成了 DSH 的钩子。

## 它装的是什么

```
DSH 的 profile 补丁层  ~/.dsh/profiles/<profile>/cordis.patch.yml
    └─ insert 一行插件：@deepseek-ai/dsh-hooks-claude-code   ← DSH 官方自带的桥
           config.configPath = <桌宠目录>/dsh_hooks.json
                                   └─ 三个事件都指向 fairy_dsh_hook.py
                                        SessionStart / UserPromptSubmit / Stop
```

## 为什么走 DSH 的钩子，而不是照 WorkBuddy 那样写 settings.json

- DSH 的扩展点是 **cordis 插件树**（profile 的补丁层），没有 `hooks` 键那种配置；
- 但 DSH 自带 `dsh-hooks-claude-code`：它把 **Claude Code 格式的 hooks.json**
  映射到 DSH 自己的扩展点（`agent/session-start`、`agent/pre-step`、
  `tools/pre-execute`、`agent/turn-stopping` …）。官方文档在
  `node_modules/@deepseek-ai/dsh-hooks-claude-code/README.zh.md`。
- ⇒ 我们只要写一份 hooks.json + 把插件挂上，**不碰 DSH 的任何内部 API**。

## 用法

    python dsh_bridge_install.py --install      # 装（默认）
    python dsh_bridge_install.py --uninstall    # 卸（只摘我们这一行）
    python dsh_bridge_install.py --status       # 看状态

    FAIRY_DSH_DRYRUN=<目录>   把 ~/.dsh 指到临时目录（离线自测，不碰真配置）
    FAIRY_DSH_PROFILE=<名字>  指定 profile（默认读 %DSH_PROFILE%，否则 desktop）

## 三条自检纪律（都是 WorkBuddy 版用血换来的）

1. **装之前先自测**：喂真实格式的 payload 跑一遍钩子，验「退出码 0 + stdout 是合法
   JSON + 事件流有新增」。钩子不响却报"装好了"，用户会以为是桌宠坏了。
2. **按宿主真实方式跑**：DSH 在 Windows 上用 `powershell.exe -NoLogo -NoProfile
   -NonInteractive -Command <命令>` 执行钩子命令（实测：`dsh-pwsh-local` 的描述里写明
   "命令串作为**单个 argv** 交给 -Command，中间没有 shell 转义层"）。所以自检也走它。
3. **写配置前后都要回读校验**：YAML 必须能被解析（用 DSH 自带的 js-yaml 验），
   别人的键一个字不许动。
"""
from __future__ import print_function

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, u"fairy_dsh_hook.py")
HOOKS_JSON = os.path.join(HERE, u"dsh_hooks.json")
LOG = os.path.join(HERE, u"fairy_dsh_bridge.log")
BEAT = os.path.join(HERE, u"heartbeat.txt")
EVENTS = os.path.join(HERE, u"dsh_events.jsonl")
REPORT = os.path.join(HERE, u"dsh_bridge_report.txt")
SUMMARY = os.path.join(HERE, u"dsh_bridge_summary.txt")

EVENTS_MAP = [u"SessionStart", u"UserPromptSubmit", u"Stop"]

BEGIN = (u"# ==== Fairy 桌宠桥接 · 开始"
         u"（由 dsh_bridge_install.py 写入；删掉本段 = 卸载）====")
END = u"# ==== Fairy 桌宠桥接 · 结束 ===="
PLUGIN = u"@deepseek-ai/dsh-hooks-claude-code"
ROW_ID = u"fairy-dsh-hooks"


# ------------------------------------------------------------------ 路径
def dsh_home():
    dry = (os.environ.get(u"FAIRY_DSH_DRYRUN") or u"").strip()
    if dry:
        return dry
    return ((os.environ.get(u"DSH_HOME") or u"").strip()
            or os.path.join(os.path.expanduser(u"~"), u".dsh"))


def profile_name():
    return ((os.environ.get(u"FAIRY_DSH_PROFILE") or u"").strip()
            or (os.environ.get(u"DSH_PROFILE") or u"").strip()
            or u"desktop")


def profile_dir():
    return os.path.join(dsh_home(), u"profiles", profile_name())


def patch_path():
    return os.path.join(profile_dir(), u"cordis.patch.yml")


def system_powershell():
    root = os.environ.get(u"SystemRoot") or u"C:\\Windows"
    p = os.path.join(root, u"System32", u"WindowsPowerShell", u"v1.0",
                     u"powershell.exe")
    return p if os.path.isfile(p) else u"powershell.exe"


# ------------------------------------------------------------------ 打包版检测
def app_asar():
    u"""→ Electron 打包版 DSH 的 `resources\\app.asar` 路径（找不到 → 空串）。

    ★ 为什么要它：**桌面版和 CLI 不是一回事**。CLI 那棵树里有
      `@deepseek-ai/dsh-hooks-claude-code`，而**打包进 app.asar 的 306 个包里一个
      `*hook*` 都没有**（2026-10-19 实测）⇒ 桌面版**根本加载不了**钩子桥接。
      不先问这一句，安装器就会"装好了、但永远不响"。
    ★ 找法：问正在跑的 `DeepSeek Harness.exe` 的可执行文件路径（一次 powershell）。
    """
    ps = system_powershell()
    try:
        r = subprocess.run([ps, u"-NoLogo", u"-NoProfile", u"-NonInteractive",
                            u"-Command",
                            u"(Get-Process 'DeepSeek Harness' -ErrorAction SilentlyContinue"
                            u" | Select-Object -First 1 -ExpandProperty Path)"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        exe = (r.stdout or b"").decode(u"utf-8", u"replace").strip().strip(u'"')
        if exe and os.path.isfile(exe):
            p = os.path.join(os.path.dirname(exe), u"resources", u"app.asar")
            if os.path.isfile(p):
                return p
    except Exception:
        pass
    for c in (os.path.join(os.environ.get(u"ProgramFiles") or u"",
                           u"DeepSeek Harness", u"resources", u"app.asar"),
              os.path.join(os.environ.get(u"LOCALAPPDATA") or u"", u"Programs",
                           u"DeepSeek Harness", u"resources", u"app.asar")):
        if c and os.path.isfile(c):
            return c
    return u""


def asar_has_hook_bridge(asar):
    u"""→ `app.asar` 里有没有**钩子桥接**（`dsh/node_modules/@deepseek-ai/*hooks*`）。

    asar 的头部索引是**未压缩**的 JSON：前 16 字节里，偏移 12 的 uint32 是头长度。
    ⇒ 纯 Python 就能读，不需要 electron/asar 依赖。
    """
    try:
        with open(asar, u"rb") as f:
            head = f.read(16)
            if len(head) < 16:
                return None
            size = int.from_bytes(head[12:16], u"little")
            if not (0 < size < 64 * 1024 * 1024):
                return None
            js = json.loads(f.read(size).decode(u"utf-8", u"replace"))
        node = js
        for part in (u"dsh", u"node_modules", u"@deepseek-ai"):
            node = (node or {}).get(u"files", {}).get(part)
        if not node:
            return None
        for name in (node.get(u"files") or {}):
            if u"hook" in name.lower():
                return True
        return False
    except Exception:
        return None


def packaged_bridge_state():
    u"""→ `(asar 路径, True=有桥接 / False=没有 / None=看不出)`。"""
    a = app_asar()
    if not a:
        return u"", None
    return a, asar_has_hook_bridge(a)


def find_node():
    u"""DSH 自带的 node（只用来校验 YAML；找不到就算了，不阻塞安装）。"""
    cand = []
    env = (os.environ.get(u"FAIRY_NODE") or u"").strip()
    if env and os.path.isfile(env):
        return env
    for base in (os.path.join(dsh_home(), u"dsh-runtimes"),
                 os.path.join(os.path.expanduser(u"~"), u".dsh", u"dsh-runtimes")):
        try:
            for root, _dn, fn in os.walk(base):
                if u"node.exe" in fn:
                    cand.append(os.path.join(root, u"node.exe"))
        except Exception:
            pass
    if cand:
        cand.sort(key=lambda p: os.path.getmtime(p))
        return cand[-1]
    return u""


def find_python():
    u"""→ 给钩子用的解释器（**python.exe**，理由见下）。

    ★★ 顺序**和 WorkBuddy 版不同**：这个桥接是挂给 **DSH** 的 ⇒
      **DSH 自带运行时优先**（不依赖 WorkBuddy 的 venv，换机器也成立），
      其次才轮到 `python_path.txt` / 当前解释器。

    ★★★ 2026-10-19 实测（代价是 6 次 headless 复现）：**必须用 `python.exe`，
      不能用 `pythonw.exe`**。DSH 在 Windows 上把钩子放进 **workspace-write 沙箱**
      跑（受限令牌 + 私有临时目录），在这种环境里 `pythonw.exe` **静默失败**：
      会话日志里 `hook/result` 是 `exitCode: 0`，而子进程 **stdout/stderr 全空**
      （用 `1> out 2> err` 抓过文件，两个都是 0 字节）、事件流一条没写 ——
      同一份命令换成 `python.exe` 立刻正常（事件、注入、退出码都对）。
      ⇒ 本机所有"给 DSH 用的"解释器路径一律取 `python.exe`；见到 `pythonw.exe`
        就地在同目录换成 `python.exe`（WorkBuddy 版当年**必须**用 pythonw，
        是为了不闪黑框；DSH 这条链路跑在**隐藏的 pwsh** 里，不存在黑框问题）。
    """
    env = (os.environ.get(u"FAIRY_PYTHON") or u"").strip()
    if env and os.path.isfile(env):
        return _as_console(env)
    try:
        base = os.path.join(dsh_home(), u"dsh-runtimes")
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
                return _as_console(cand)
        except Exception:
            pass
    return _as_console(sys.executable or u"")


def _as_console(p):
    u"""`...\\pythonw.exe` → `...\\python.exe`（同目录有的话）。见 `find_python` 的说明。"""
    p = p or u""
    if os.path.basename(p).lower() == u"pythonw.exe":
        q = os.path.join(os.path.dirname(p), u"python.exe")
        if os.path.isfile(q):
            return q
    return p


# ------------------------------------------------------------------ hooks.json
def build_command(pyw, hook=None, event=None):
    u"""→ 写进 `dsh_hooks.json` 的命令串。

    ★★★ DSH 在 Windows 上用 **Windows PowerShell 5.1** 执行：
        `powershell.exe -NoLogo -NoProfile -NonInteractive -Command <命令串>`
      命令串**整段**作为 -Command 的参数（没有 bash 那层转义）⇒
      用 `& "exe" "script"` 最稳：`&` 是调用运算符，带空格的路径也不会被拆。
      —— WorkBuddy 版必须写正斜杠（bash 会吃反斜杠）；这里两种都行，
         但**仍统一写正斜杠**：同一份正文给 AI 抄命令时不会踩 bash 的坑。

    ★★★ 2026-10-19 实测：DSH 在 Windows 沙箱里跑钩子时 **stdin 到不了子进程**
      （会话日志里 `hook/result` 是 `exitCode: 0`，但钩子什么也没写）。
      ⇒ 事件名改成**命令行参数**传：三个事件各一条命令，末尾带各自的英文事件名。
        钩子脚本里 stdin 变成了"有就用、没有也能干"。
    """
    exe = (pyw or u"").replace(u"\\", u"/")
    scr = (hook or HOOK).replace(u"\\", u"/")
    s = u'& "%s" "%s"' % (exe, scr)
    if event:
        s += u' %s' % event
    return s


def hooks_config(pyw):
    u"""→ hooks.json 的内容：**三个事件各一条命令（带事件名参数）**。"""
    hooks = {}
    for ev in EVENTS_MAP:
        hooks[ev] = [{u"hooks": [{u"type": u"command",
                                  u"command": build_command(pyw, None, ev),
                                  u"timeout": 20}]}]
    return {
        u"_readme": (u"Fairy 桌宠桥接 · DSH 钩子配置（Claude Code 格式）。"
                     u"由 dsh_bridge_install.py 生成；改完重启/热加载 DSH 生效。"),
        u"hooks": hooks,
    }


def write_hooks_json(pyw):
    d = hooks_config(pyw)
    tmp = HOOKS_JSON + u".tmp"
    with io.open(tmp, u"w", encoding=u"utf-8", newline=u"") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2) + u"\n")
    os.replace(tmp, HOOKS_JSON)
    return d


# ------------------------------------------------------------------ YAML 补丁
def _read_text(path):
    u"""读文本文件 → **去掉 BOM**。

    ★ 为什么不直接 `io.open(..., encoding='utf-8')`：那样 BOM 会留在字符串里
      （`\\ufeff# 注释` 不再以 `#` 开头）⇒ 被当成"真内容" ⇒ 我们会把新行**追加在
      `[]` 后面** ⇒ 整份 YAML 非法（干跑时实测踩到）。BOM 是 Windows 编辑器的常客，
      不能假设它是干净的。
    """
    if not os.path.isfile(path):
        return u""
    t = io.open(path, u"r", encoding=u"utf-8", errors=u"replace").read()
    if t.startswith(u"\ufeff"):
        t = t[1:]
    return t


def _core(text):
    u"""去掉注释行与空行 —— 用来判断"这个文件里到底有没有真内容"。"""
    return [ln for ln in (text or u"").splitlines()
            if ln.strip() and not ln.lstrip().startswith(u"#")]


def strip_block(text):
    u"""摘掉我们那一段（幂等：装/卸/重装都先调它）。"""
    out, on = [], False
    for ln in (text or u"").splitlines():
        if ln.strip().startswith(u"# ==== Fairy 桌宠桥接 · 开始"):
            on = True
            continue
        if on and ln.strip().startswith(u"# ==== Fairy 桌宠桥接 · 结束"):
            on = False
            continue
        if not on:
            out.append(ln)
    res = u"\n".join(out)
    #   ★ 末尾换行原样保留 —— 否则"没装过的文件"过一遍就少一个换行，
    #     `strip_block(text) == text` 这条幂等性质不成立（自测会当场红）。
    if (text or u"").endswith(u"\n") and not res.endswith(u"\n"):
        res += u"\n"
    return res


def render_block(cfg_path):
    u"""→ 往 `cordis.patch.yml` 里追加的那一段（YAML 列表项）。

    ★ 结构照 `dsh-base/cordis.patch.yml` 的写法：`- insert: [ ...rows... ]`
      没有 id 的 insert = 往**根**（profile 的空根）里插行。
    ★ 只用单引号包一个路径（Windows 路径不可能含单引号以外的怪字符；
      真含单引号就按 YAML 规则写两遍）。
    """
    p = cfg_path.replace(u"\\", u"/").replace(u"'", u"''")
    return u"\n".join([
        BEGIN,
        u"- insert:",
        u"    - id: %s" % ROW_ID,
        u"      name: '%s'" % PLUGIN,
        u"      config:",
        u"        configPath: '%s'" % p,
        u"        defaultTimeoutMs: 20000",
        END,
        u"",
    ])


def yaml_validate(path):
    u"""用 DSH 自带的 js-yaml 验一遍（node 找不到就跳过）。→ (ok, 说明)"""
    node = find_node()
    if not node:
        return True, u"（没找到 node，跳过 YAML 校验）"
    cands = [os.path.join(dsh_home(), u"profiles", u"node_modules", u"js-yaml"),
             os.path.join(os.path.expanduser(u"~"), u".dsh", u"profiles",
                          u"node_modules", u"js-yaml")]
    mod = u""
    for c in cands:
        if os.path.isdir(c):
            mod = c.replace(u"\\", u"/")
            break
    if not mod:
        return True, u"（没找到 js-yaml，跳过 YAML 校验）"
    code = (u"const fs=require('fs');"
            u"const Y=require('%s');"
            u"const v=Y.load(fs.readFileSync(process.argv[1],'utf8'));"
            u"if(!Array.isArray(v)) throw new Error('顶层不是数组');"
            u"console.log('ok rows='+v.length);" % mod)
    try:
        r = subprocess.run([node, u"-e", code, path],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        out = (r.stdout or b"").decode(u"utf-8", u"replace").strip()
        err = (r.stderr or b"").decode(u"utf-8", u"replace").strip()
        if r.returncode != 0:
            #   ★ 别只报最后一行（那是 `Node.js v24.x` 版本尾巴，等于什么都没说）——
            #     把 stderr 的前两行带出来，才看得出是"语法错"还是"找不到模块"。
            lines = [x for x in err.splitlines() if x.strip()]
            head = u" / ".join(lines[:2]) if lines else u"?"
            return False, head[:300]
        rows = u"?"
        for tok in out.split():
            if tok.startswith(u"rows="):
                rows = tok[5:]
        return True, u"合法（顶层 %s 条）" % rows
    except Exception as e:
        return True, u"（校验器起不来：%r）" % (e,)


def patch_install():
    u"""把插件行写进 profile 补丁层。→ (ok, 说明)。"""
    pth = patch_path()
    if not os.path.isdir(os.path.dirname(pth)):
        return False, u"profile 目录不存在：%s（DSH 装过吗？）" % os.path.dirname(pth)
    old = u""
    old = _read_text(pth)
    if old:
        bak = pth + u".bak_fairy_" + time.strftime(u"%Y%m%d_%H%M%S")
        shutil.copy2(pth, bak)
    base = strip_block(old)
    core = _core(base)
    block = render_block(HOOKS_JSON)
    if not core or [x.strip() for x in core] == [u"[]"]:
        #   ★ 原来只有注释 / 一个空数组 `[]` ⇒ **必须把 `[]` 去掉**，
        #     否则"流式空数组 + 块式列表项"是非法 YAML（实测过，DSH 会整份不加载）。
        keep = [ln for ln in base.splitlines() if ln.lstrip().startswith(u"#")]
        new = (u"\n".join(keep) + u"\n\n" if keep else u"") + block
    else:
        new = base.rstrip() + u"\n\n" + block
    io.open(pth, u"w", encoding=u"utf-8", newline=u"").write(new)
    ok, why = yaml_validate(pth)
    if not ok:
        #   ★ 校验没过 ⇒ **回滚**（绝不给 DSH 留一份坏配置）
        if old:
            io.open(pth, u"w", encoding=u"utf-8", newline=u"").write(old)
        else:
            try:
                os.remove(pth)
            except Exception:
                pass
        return False, u"写进去的 YAML 不合法，已回滚：%s" % why
    return True, why


def patch_remove():
    pth = patch_path()
    if not os.path.isfile(pth):
        return 0, u"补丁层不存在（可能已经卸过了）"
    old = _read_text(pth)
    if BEGIN not in old:
        return 0, u"补丁层里没有我们那一段"
    base = strip_block(old)
    core = _core(base)
    if not core:
        keep = [ln for ln in base.splitlines() if ln.lstrip().startswith(u"#")]
        new = (u"\n".join(keep) + u"\n\n" if keep else u"") + u"[]\n"
    else:
        new = base.rstrip() + u"\n"
    shutil.copy2(pth, pth + u".bak_fairy_" + time.strftime(u"%Y%m%d_%H%M%S"))
    io.open(pth, u"w", encoding=u"utf-8", newline=u"").write(new)
    ok, why = yaml_validate(pth)
    if not ok:
        io.open(pth, u"w", encoding=u"utf-8", newline=u"").write(old)
        return 0, u"卸载后 YAML 不合法，已回滚：%s" % why
    return 1, why


# ------------------------------------------------------------------ 自测
def _lines(path):
    try:
        return len([x for x in io.open(path, u"r", encoding=u"utf-8",
                                       errors=u"replace").read().splitlines() if x.strip()])
    except Exception:
        return 0


def selftest(pyw):
    u"""按 DSH 的真实方式跑三个事件，验：退出码 0 / JSON 合法 / 事件流有新增。

    ★ 自检**绝不碰真实事件流**（`FAIRY_DSH_LOG` 指到临时文件）——
      否则自检的假事件会混进主人的时间线，桌宠会莫名其妙半睁。
    """
    cmd = build_command(pyw)
    print(u"   写进配置的命令：%s" % cmd)
    tmp = os.path.join(HERE, u"dsh_bridge_selftest.jsonl")
    for p in (tmp,):
        try:
            os.remove(p)
        except Exception:
            pass
    env = dict(os.environ)
    env[u"FAIRY_DSH_LOG"] = tmp
    ps = system_powershell()
    ok_all = True

    def _one(label, argv, ev, payload=None, want_json=False, cwd=None):
        u"""跑一次钩子命令，验：退出码 0 / 事件流有新增 /（可选）stdout 是合法 JSON。"""
        n0 = _lines(tmp)
        kw = {u"stdout": subprocess.PIPE, u"stderr": subprocess.PIPE,
              u"timeout": 60, u"env": env}
        if cwd:
            kw[u"cwd"] = cwd
        if payload is None:
            kw[u"stdin"] = subprocess.DEVNULL      # ★ 模拟"沙箱里没有 stdin"
        else:
            kw[u"input"] = (json.dumps(payload, ensure_ascii=False) + u"\n").encode(u"utf-8")
        try:
            r = subprocess.run([ps, u"-NoLogo", u"-NoProfile", u"-NonInteractive",
                                u"-Command", argv], **kw)
        except Exception as e:
            print(u"   [X] %s 起不来：%r" % (label, e))
            return False
        out = (r.stdout or b"").decode(u"utf-8", u"replace").strip()
        err = (r.stderr or b"").decode(u"utf-8", "replace").strip()
        n1 = _lines(tmp)
        print(u"   [%s] %-34s 退出码=%s ｜ stdout %d 字节 ｜ 事件 %d→%d"
              % (u"OK" if r.returncode == 0 else u"X", label, r.returncode,
                 len(out), n0, n1))
        good = True
        if r.returncode != 0:
            print(u"        ★ 退出码非 0 —— 必须修（2 会阻塞主人的操作）")
            if err:
                print(u"        stderr: %s" % err[:300])
            good = False
        if n1 <= n0:
            print(u"        ★ 事件流没新增 —— 钩子没真跑到写盘那一步")
            good = False
        if want_json:
            try:
                d = json.loads(out)
            except Exception:
                print(u"        ★ stdout 不是合法 JSON：%r" % out[:160])
                return False
            ac = ((d.get(u"hookSpecificOutput") or {}).get(u"additionalContext") or u"")
            name = ((d.get(u"hookSpecificOutput") or {}).get(u"hookEventName") or u"")
            print(u"        OK：JSON 合法；注入正文 %d 字符；hookEventName=%s"
                  % (len(ac), name))
            if not ac:
                print(u"        ★ 注入正文是空的 —— AI 读不到协议，等于没装")
                good = False
            if name != ev:
                print(u"        ★ hookEventName 与事件名不一致 ⇒ DSH 会丢掉这块上下文")
                good = False
        return good

    #   ① 真机形态：**没有 stdin**，事件名走命令行参数（DSH 沙箱里的实际情形）
    #      ★ UserPromptSubmit 换一个**临时工作目录**跑：钩子"同一个项目 40 分钟内只注入
    #        一次"是正确行为，不换 cwd 就永远等不到第二次注入（自测会误判成坏）。
    print(u"   ① 按真机形态跑（无 stdin，事件名走命令行参数）")
    tmp_cwd = tempfile.mkdtemp(prefix=u"fairy_selftest_")
    try:
        ok_all = _one(u"SessionStart（无 stdin）", build_command(pyw, None, u"SessionStart"),
                      u"SessionStart", payload=None, want_json=True) and ok_all
        ok_all = _one(u"UserPromptSubmit（无 stdin）",
                      build_command(pyw, None, u"UserPromptSubmit"),
                      u"UserPromptSubmit", payload=None, want_json=True,
                      cwd=tmp_cwd) and ok_all
        ok_all = _one(u"Stop（无 stdin）", build_command(pyw, None, u"Stop"),
                      u"Stop", payload=None, want_json=False) and ok_all
    finally:
        try:
            os.rmdir(tmp_cwd)
        except Exception:
            pass

    #   ② 对照：stdin 通路也要能用（别的宿主 / 以后 DSH 修好了也不会白装）
    print(u"   ② 对照：stdin 通路（有 payload 时以 payload 为准）")
    for i, ev in enumerate(EVENTS_MAP):
        payload = {u"hook_event_name": ev,
                   u"session_id": u"selftest-%s-%d" % (time.strftime(u"%H%M%S"), i),
                   u"cwd": HERE}
        if ev == u"UserPromptSubmit":
            payload[u"prompt"] = u"selftest"
        if ev == u"Stop":
            payload[u"stop_hook_active"] = False
        ok_all = _one(u"%s（有 stdin）" % ev, build_command(pyw), ev,
                      payload=payload, want_json=(ev != u"Stop")) and ok_all
    try:
        os.remove(tmp)
    except Exception:
        pass
    return ok_all


# ------------------------------------------------------------------ 命令
def _status_rows():
    pth = patch_path()
    return _read_text(pth)


def cmd_status():
    pth = patch_path()
    print(u"=== Fairy × DSH 桥接状态 ===")
    print(u"   DSH_HOME : %s" % dsh_home())
    print(u"   profile  : %s" % profile_name())
    print(u"   补丁层   : %s（%s）"
          % (pth, u"存在" if os.path.isfile(pth) else u"不存在"))
    #   ★★ 先报"桌面版到底有没有这个插件" —— 没有的话，下面"钩子行已挂"是**假象**。
    _asar, _shipped = packaged_bridge_state()
    if _shipped is False:
        print(u"   桌面版   : ★ **没有钩子桥接**（%s）⇒ 钩子永远不会触发" % _asar)
    elif _shipped is True:
        print(u"   桌面版   : 有钩子桥接（%s）" % _asar)
    else:
        print(u"   桌面版   : 看不出（没找到打包版 / 读不了 asar）")
    txt = _status_rows()
    print(u"   钩子行   : %s" % (u"已挂（%s）" % ROW_ID if BEGIN in txt else u"**没挂**"))
    print(u"   hooks.json: %s（%s）"
          % (HOOKS_JSON, u"存在" if os.path.isfile(HOOKS_JSON) else u"**缺失**"))
    if os.path.isfile(HOOKS_JSON):
        try:
            d = json.loads(io.open(HOOKS_JSON, u"r", encoding=u"utf-8").read())
            for ev in EVENTS_MAP:
                arr = (d.get(u"hooks") or {}).get(ev) or []
                cmd = ((arr[0].get(u"hooks") or [{}])[0].get(u"command") or u"")
                print(u"      %-16s %s" % (ev, cmd[:96]))
        except Exception as e:
            print(u"   ★ hooks.json 解析失败：%r" % (e,))
    print(u"   静音开关 : %s" % (u"★ 已静音（fairy_dsh_off）"
                                if os.path.exists(os.path.join(HERE, u"fairy_dsh_off"))
                                else u"正常（没静音）"))
    if os.path.isfile(EVENTS):
        rows = [x for x in io.open(EVENTS, u"r", encoding=u"utf-8",
                                   errors=u"replace").read().splitlines() if x.strip()]
        print(u"   事件流   : %d 条（%s），最后 3 条：" % (len(rows), EVENTS))
        for x in rows[-3:]:
            print(u"      %s" % x[:120])
    else:
        print(u"   事件流   : **还没有**（%s）—— 说明钩子一次都没响过" % EVENTS)
    if os.path.isfile(LOG):
        rows = [x for x in io.open(LOG, u"r", encoding=u"utf-8",
                                   errors=u"replace").read().splitlines() if x.strip()]
        print(u"   桥接日志 : %d 条，最后 2 条：" % len(rows))
        for x in rows[-2:]:
            print(u"      %s" % x[:120])
    if os.path.isfile(BEAT):
        try:
            beat = io.open(BEAT, u"r", encoding=u"utf-8", errors=u"replace").read()
            print(u"   桌宠心跳 : %s" % beat.strip()[:160])
        except Exception:
            pass
    return 0


def running_pet_dirs():
    u"""同级的兄弟目录里有没有**正在跑的桌宠**（心跳 3 分钟内）→ 路径列表。

    ★★ 为什么要有这道闸：本仓库有两份 —— 开发树 `pet-v2/` 和发布副本
      `fairy-desktop-pet/`。桥接**必须装在"正在跑的那一份"里**（它要写那个目录下的
      `dsh_events.jsonl`，并且注入正文里的路径也指向那里）。
      装错一份的表现是：**装完一切正常、但桌宠永远不动** —— 排查成本极高。
      ⇒ 宁可当场拦住并告诉主人"去哪个目录装"。
    """
    out = []
    try:
        parent = os.path.dirname(HERE)
        for name in os.listdir(parent):
            d = os.path.join(parent, name)
            if os.path.abspath(d) == os.path.abspath(HERE):
                continue
            if not os.path.isfile(os.path.join(d, u"fairy_pet.py")):
                continue
            beat = os.path.join(d, u"heartbeat.txt")
            if os.path.isfile(beat) and (time.time() - os.path.getmtime(beat)) < 180:
                out.append(d)
    except Exception:
        pass
    return out


def cmd_install():
    print(u"=== 安装「DSH ↔ Fairy 桌宠」桥接 ===")
    print(u"   桌宠目录 : %s" % HERE)
    print(u"   补丁层   : %s" % patch_path())
    if not os.path.isfile(HOOK):
        print(u"\n★ 找不到 %s —— 安装器必须和它放在同一个目录。" % os.path.basename(HOOK))
        return 2
    others = running_pet_dirs()
    if others:
        print(u"\n★ **装错目录了**：正在跑的那只桌宠在：")
        for d in others:
            print(u"      %s" % d)
        print(u"   ⇒ 请到**那个目录**双击 install_dsh_bridge.vbs（装错的表现是"
              u"「装完一切正常、桌宠永远不动」，很难查）。")
        return 5
    asar, shipped = packaged_bridge_state()
    if shipped is False:
        print(u"\n★ **桌面版 DSH 装不了这个桥接**（不是配置错，是包里没有）：")
        print(u"     打包版程序：%s" % asar)
        print(u"     它的 306 个 @deepseek-ai 包里**没有一个钩子桥接**"
              u"（`@deepseek-ai/dsh-hooks-claude-code` 只随 CLI 分发）")
        print(u"   ⇒ 桌面版**没有** `SessionStart/UserPromptSubmit/Stop` 这三个事件，"
              u"所以「注入协议」这条路走不通。")
        print(u"\n   ★ 但这**不影响**桌宠干活：")
        print(u"     · 半睁/睁眼：读投影缓存，**已经在工作**；")
        print(u"     · 进度条：读 `todos` 投影**自动推进**（不需要 AI 上报）；")
        print(u"     · 少的是：AI 不知道要发完成卡 / 不知道怎么 `--abort`。")
        print(u"\n   想补上，只有两条路：")
        print(u"     ① 换成 **CLI 版 DSH**（`dsh` 命令）跑任务 —— 本安装器对它有效；")
        print(u"     ② 给桌面版写一个**本地原生插件**（放 profile 目录、按路径引用，"
              u"不依赖那个缺失的包）—— 还没做，需要主人点头。")
        return 6
    pyw = find_python()
    print(u"   解释器   : %s" % pyw)
    if not pyw or not os.path.isfile(pyw):
        print(u"\n★ 没找到 python.exe。在桌宠目录放一个 python_path.txt 写一行路径再试。")
        return 2

    print(u"\n=== ① 生成 hooks.json ===")
    write_hooks_json(pyw)
    print(u"   已写：%s" % HOOKS_JSON)

    print(u"\n=== ② 先自测钩子本身（★ 按 DSH 的方式：powershell -Command）===")
    if not selftest(pyw):
        print(u"\n★ 自测没过 ⇒ **不装**（装了也是个不响的钩子）。")
        return 3

    print(u"\n=== ③ 挂到 profile 补丁层 ===")
    ok, why = patch_install()
    print(u"   %s：%s" % (u"OK" if ok else u"★ 失败", why))
    if not ok:
        return 4

    print(u"\n=== ④ 回读校验 ===")
    txt = _status_rows()
    print(u"   钩子行在不在 : %s" % (u"在 OK" if BEGIN in txt else u"★ 不在！"))
    print(u"   YAML         : %s" % yaml_validate(patch_path())[1])

    print(u"\n" + u"=" * 62)
    print(u"装好了。生效方式：")
    print(u"  · desktop profile 的用户补丁层是**热加载**的（`patchReload: live`）⇒")
    print(u"    理论上**保存即生效**：下一条消息就会触发 UserPromptSubmit 钩子；")
    print(u"  · 若没反应：**重启 DSH**（配置只在启动时读一次 `configPath`）。")
    print(u"\n验证：看 %s 有没有新记录（%s）" % (os.path.basename(EVENTS), EVENTS))
    print(u"      或双击 check_fairy.vbs —— 里面会带上桥接状态。")
    print(u"取消：双击 uninstall_dsh_bridge.vbs，或 `--uninstall`。")
    print(u"临时静音（不改配置）：在桌宠目录建一个空文件 fairy_dsh_off。")
    return 0


def cmd_uninstall():
    print(u"=== 卸载「DSH ↔ Fairy 桌宠」桥接 ===")
    n, why = patch_remove()
    print(u"   补丁层：%s（%s）" % (u"已摘掉 1 条" if n else u"没动", why))
    if n:
        print(u"   ★ 热加载的 profile 立刻生效；否则重启 DSH。")
    print(u"   桌宠本身不受影响（hooks.json / 事件流都留着，重装时直接复用）。")
    return 0


def main():
    u"""入口。

    ★★ 为什么把 stdout 一边打印一边攒一份：用户是**双击 `.vbs`** 装的，而 `.vbs`
      用 `pythonw` **隐藏窗口**跑本脚本 ⇒ 控制台输出他一个字都看不到。
      ⇒ 顺手落到 `dsh_bridge_report.txt`，`.vbs` 再读摘要弹 MsgBox。
    ★ 报告写 **UTF-16**（VBS 的 `TristateTrue` 按 Unicode 读，UTF-8 会乱码）。
    ★★ 摘要里**不许写 Markdown** —— MsgBox 是纯文本。
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
            code = cmd_install()
    except Exception as e:
        import traceback
        print(u"\n★ 安装器自己出错了：%r" % (e,))
        print(traceback.format_exc())
        code = 9
    finally:
        sys.stdout = real
        try:
            txt = buf.getvalue()
            if code != 0:
                txt += u"\n\n★★ 没装成功（退出码 %s）。把上面这段发给 Fairy 的作者。" % code
            with io.open(REPORT, u"w", encoding=u"utf-16", newline=u"") as f:
                f.write(txt)
            mode = (u"卸载" if (u"--uninstall" in args or u"-u" in args)
                    else (u"状态" if (u"--status" in args or u"-s" in args) else u"安装"))
            if mode == u"安装" and code == 0:
                s = (u"装好了：以后 DSH 里的 AI 干活时，Fairy 会跟着半睁眼 + 走进度条。\n\n"
                     u"生效方式：\n"
                     u"  · 这个 profile 的配置是热加载的，理论上**保存即生效**；\n"
                     u"  · 如果没反应，就重启 DSH。\n\n"
                     u"怎么验证：看下面这个文件有没有新记录\n"
                     u"    %s\\dsh_events.jsonl\n\n"
                     u"想关掉：双击 uninstall_dsh_bridge.vbs（摘干净）\n"
                     u"        或在桌宠目录建一个空文件 fairy_dsh_off（临时静音）"
                     % HERE)
            elif mode == u"安装" and code == 6:
                #   ★ 这一档不是"出错"，是"这个宿主没有这个能力" —— 别吓主人。
                s = (u"这个版本的 DSH（桌面版）装不了桥接——**不是故障**。\n\n"
                     u"原因：桌面版的安装包里没有钩子桥接（只有 CLI 版才有），\n"
                     u"所以「让 AI 主动发完成卡」这条路在桌面版走不通。\n\n"
                     u"★ 但桌宠**照常工作**：\n"
                     u"    · 你一发消息它就半睁，说完 25 秒自己睁眼；\n"
                     u"    · 进度条读 DSH 的 todo 清单**自己会走**（不用 AI 报）。\n\n"
                     u"想补完成卡：换 CLI 版 DSH 跑任务，或让我给桌面版写一个本地插件。")
            elif mode == u"安装":
                s = (u"没装成功（退出码 %s）。\n\n完整原因在：\n    %s\\dsh_bridge_report.txt\n"
                     u"把那份文件发给 Fairy 的作者就行。" % (code, HERE))
            elif mode == u"卸载" and code == 0:
                s = (u"已卸载（DSH 的钩子行摘掉了）。\n\n"
                     u"桌宠本身还在，不受影响。")
            else:
                s = u"状态已写入：%s\\dsh_bridge_report.txt" % HERE
            with io.open(SUMMARY, u"w", encoding=u"utf-16", newline=u"") as f:
                f.write(s)
        except Exception:
            pass
    return code


if __name__ == u"__main__":
    sys.exit(main())
