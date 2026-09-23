# -*- coding: utf-8 -*-
"""fairy 通知器 —— 我（AI）干完活时调用它，桌宠就会弹一张卡片告诉主人。

用法（都在 pet-v2 目录下跑）：

    python fairy_notify.py "主人，湖州西凤漾方案文本已完成，请前往查阅。"     # 只弹卡
    python fairy_notify.py --done "主人，XX 已完成，请前往查阅。"           # 弹卡 + 收工（眼睛睁开）
    python fairy_notify.py --work "开始处理 XX（约 3 分钟）"                # 开工（半睁）+ 弹卡
    python fairy_notify.py --secs 25 "长一点，多看几眼"                     # 卡片多停一会儿

★ 2026-09-22 主人反馈："每次都是『开工：XXX』有点呆板" ⇒ 新增 `--kind` 分类：
    work      真干活（改文件/写代码/出图/跑脚本）  → 前缀「行动目标」 + **半睁**
    research  查资料 / 检索 / 调研                  → 前缀「资料检索」 + **半睁**
    review    校验 / 核对 / 审稿                    → 前缀「校验」     + **半睁**
    answer    回答提问 / 解释概念                   → 前缀「应答」     + **常态**（不必装忙）
    chat      闲聊 / 寒暄                           → 前缀「待机对话」 + **常态**
    done      完成（= --done 的默认分类）            → 前缀「已完成」   + **常态**

    python fairy_notify.py --kind research "重构后的渲染器性能基准"
    python fairy_notify.py --kind chat "主人今天气色不错。"
    python fairy_notify.py --kind work --no-prefix "重建辉光相位表"

★ 2026-09-22 下午：新增**工作进度上报**（决定工作态那条进度条）：
    python fairy_notify.py --progress 45                    # 只上报进度（不弹卡、不动状态）
    python fairy_notify.py --work "开始处理 XX"             # 开工（这轮先不显示进度条）
    python fairy_notify.py --done "XX 已完成"               # 收工 ⇒ 顺带把进度置 100

⚠️ **以下"四阶段协议"已被 09-23 下午的三段式取代（保留只为对照历史，别再用）**：
★★ 2026-09-22 晚（**家里那台**）主人定的**四阶段进度协议**：
    ① **接单**：`--work "<要干什么>"` ⇒ 只切工作态，**没有进度条**
       （`--work` 写 `planned=false`，桌宠据此不画条）；
    ② **拆阶段**（我在后台想）：这段**也保持"无进度条"**；
    ③ **拆好了**：`--plan N "<阶段名列表>"` ⇒ 进度条**此刻登场**，并按 N **等分**
       （主人原话：「做几步就几等分」）；
    ④ **每完成一阶段**：`--stage K` ⇒ 自动算 `K/N×100`，**不要手写百分比**；
    ⑤ **交付前**：`--done "主人，…已就绪，请前往查阅。"` ⇒ 正好 100%，并弹完成卡。
    ★★ **绝不在开工那一刻带进度**：`--work --progress N` 会被**忽略并告警** ——
       否则进度条一开始就挂在屏上，把"先拆步骤"这一段抹掉（主人 2026-09-23 抓到）。

★★★ 2026-09-23 下午 主人定的**三段式进度规则**（**最终版，照这个用**）：
    ① **0 ~ 90%** = 实际工作，**有几项就几等分**；每一项内再分三小段：
         计划思考 = 该项的 **30%** ｜ 搜索修改 = **50%** ｜ 落位成果 = **20%**
         ⇒ `--item K P`（P=1/2/3），百分比由工具算，**我不手写**。
         例（N=3，一项占 30%）：计划思考完 ⇒ **9%**；搜索修改完 ⇒ **24%**；该项完成 ⇒ **30%**。
    ② **90 ~ 95%** = 我写记忆 / 落盘（主人直给 5% 权限）⇒ `--memo`（默认到 95%）。
    ③ **95 ~ 100%** = 我写回复 + 列交付物 ⇒ `--reply "主人，N 项修改已全部完成，请前往查阅。"`
         （★ `--reply` 是**开关**，**文案直接跟在后面**；只推进不带卡用 `--reply-at P`）。
         到 100% 那一刻：**弹完成卡**（自动带「共 N 项修改」）+ 桌宠**等主人开口**才回常态
        （他发下一句指令 ⇒ 让位；都没发生 ⇒ 兜底 180 s。**不是**"10 s 后自动回"）。
        ★ 为什么不用秒数：`--reply` 是我做的最后一件事，回复正文还要生成/渲染才显示出来
          ⇒ 按秒数计时**必然**在他读到之前到期（10 s、60 s 都试过，都不够）。
    ★ 旧写法 `--stage K` 仍然可用（等价 `--item K 3`）。

★ **卡面文案的语气 = Fairy 本人的语气**（主人 2026-09-23：「语气要跟 fairy 一样的」）：
    先交代结论、称呼「主人」、不谄媚、不用「您好 / 亲 / 感谢您的耐心等待」这类客套；压在一两行内。
    官方原文可参考（`REFERENCE_辉光与进度条.md` G/L 节）：
      「主人，作为您唯一的首席助手，Fairy 会随时在其他同事发言之前，向您提供可行的策略。」
      「主人，模拟演算系统已经搭建完成，为了确保数据传输顺利，期间我暂停了部分电影的下载进程。」
    好：`主人，通知卡的字体档与二级菜单已就绪，请前往查阅。`
    差：`任务完成` / `已完成工作` / `您好，本次任务已处理完毕，感谢您的耐心等待`

设计要点：
  · **原子写**（先写 .tmp 再 os.replace）—— 否则桌宠可能读到半个 JSON，那一轮通知就丢了。
  · 通知是一次性的：桌宠读完会把 notify.json 删掉，重启不会重复弹。
  · **进度不是**一次性的（那是"当前值"，桌宠反复读）⇒ progress.json **不消费、不删**。
  · 不碰网络、不碰窗口，只写这几个文件 ⇒ 桌宠开着就能收到，没开也不报错。
  · ★ 前缀用**系统语言**（行动目标/资料检索/校验/应答/待机对话），这是 Fairy 的官方语态，
    不是随口起的名 —— 见 `_work/dsh/.agent-presets__fairy__*`。
"""
import argparse
import json
import math
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
NOTIFY = os.path.join(BASE, "notify.json")
STATE = os.path.join(BASE, "state.json")
# ★★ 2026-09-23：允许用环境变量把它指到别处 —— **测试/演练脚本必须这么做**。
#   起因：`_work/step130`（协议回归）走 CLI 子进程、写的是**真的**这份 ⇒
#   跑一次回归就把主人正在看的进度从 95% 打回 68%（当天真发生）。
#   ⇒ 验收统一 `FAIRY_PROGRESS=<临时文件>`。测试不该动生产状态。
PROGRESS = os.environ.get("FAIRY_PROGRESS") or os.path.join(BASE, "progress.json")
# ★★ 2026-09-23 17:3x：进度**按项目各存一份**（`_prog\progress_<项目>.json`）。
#   起因（实测）：两个项目**同时**在跑时，上面那份全局 `progress.json`
#   **后写的覆盖先写的** —— 本会话报 `--item 1 2` 时被工具直接拒掉，理由是
#   "该报第 2 项"，因为另一个会话已经把 `item/phase` 写成了 `(2,1)`。
#   ⇒ 各写各的；桌宠按**当前活跃项目**去取（见 `fairy_pet._read_progress`）。
#   ★ 全局那份**照旧写**（老脚本 / 外部工具 / 推不出项目时都还要用它）。
PROGRESS_DIR = os.path.join(BASE, "_prog")
# ★ 演练 / 回归脚本用 `FAIRY_PROGRESS=<临时文件>` 做隔离 ⇒ 那种情况下**不碰**专属文件
#   （`_prog\` 里那份是真的，一次回归就会把主人正在看的进度写花 —— 09-23 真发生过）。
_ISOLATED = bool(os.environ.get("FAIRY_PROGRESS"))


def prog_file(project):
    u"""某项目的**专属**进度文件；`project` 为空 ⇒ 全局那份（老路径，保持兼容）。"""
    if not project:
        return PROGRESS
    try:
        import fairy_activity as _A
        frag = _A.project_file_name(project)
    except Exception:
        frag = project
    return os.path.join(PROGRESS_DIR, "progress_%s.json" % frag)


_PJ_CACHE = [0.0, u""]


def _env_project():
    u"""从**本次调用者的环境**里读出它属于哪个项目 —— 最精确的判据。

    ★ 实测（2026-09-23 17:4x）：WorkBuddy 给每个会话都注入了
        `CODEBUDDY_PROJECT_DIR = /e/AI成图实践/Fairy`
        `CODEBUDDY_SESSION_ID  = 212254f1-…`
      ⇒ 用它反推项目目录名是**零成本、零歧义**的（`/e/A` ⇒ `e-A`，与 `project_key()` 同规则）。

    ★★ 为什么**必须**用它，不能只靠"扫盘看谁的会话最新"：
      两个会话**交替**写 jsonl 时，"谁最新"每次都在变 ⇒ 推断结果来回摇摆。
      后果我实测到了：本会话的 `--item` 读到了**另一个项目**的进度（报格被拒），
      而且自己的进度被写进了**另一个项目的专属文件**里 —— 两边彻底串味。
    """
    for k in ("CODEBUDDY_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        v = (os.environ.get(k) or u"").strip().replace(u"\\", u"/").strip(u"/")
        parts = [p for p in v.split(u"/") if p]
        if not parts:
            continue
        if len(parts[0]) == 2 and parts[0][1] == u":":
            parts[0] = parts[0][0].lower()          # `C:` → `c`（与 `project_key()` 同规则）
        return u"-".join(parts)
    return u""


def current_project(ttl=2.0):
    u"""→ 我**此刻**在哪个 WorkBuddy 项目里干活（目录名，如 `e-AI成图实践-Fairy`）。

    ★ 为什么要它：`progress.json` 是全局单文件 ⇒ 甲项目交付的 100% 会在乙项目开工时
      顶出来（主人 2026-09-23 17:3x 原话：「换个项目就出现这个问题了，刚进入工作态
      的时候，就是带工作条的」）⇒ 每条进度都打上**归属项目**。
    ★ 判据**两级**：① 环境变量（`_env_project()`，精确、零成本）；
      ② 认不出才回落到"扫盘看哪个项目的会话文件最新"（旧法，只在手动跑脚本时走到）。
    ★ 都认不出 ⇒ 返回空串 = 不打标签，桌宠按旧行为处理（**不拦**）。
      宁可少拦，不可误拦。
    """
    now = time.time()
    if now - _PJ_CACHE[0] < ttl:
        return _PJ_CACHE[1]
    v = _env_project()
    if not v:
        try:
            import fairy_activity as _A
            w = _A.ActivityWatch()
            if w.ok:
                w.active()
                v = w.last_dir or u""
        except Exception:
            v = u""
    _PJ_CACHE[0], _PJ_CACHE[1] = now, v
    return v


# kind → (前缀, 是否进入工作态)
KINDS = {
    "work":     ("行动目标", "working"),
    "research": ("资料检索", "working"),
    "review":   ("校验", "working"),
    "answer":   ("应答", "idle"),
    "chat":     ("待机对话", "idle"),
    "done":     ("已完成", "idle"),
}


# ★★★ 2026-09-23 下午 主人定的三段式进度规则（数字都是从他的话里抄的，别改）
WORK_TOP = 90.0                    # 0~90：实际工作，N 项等分
MEMO_TOP = 95.0                    # 90~95：我写记忆 / 落盘
PHASE_CUM = (0.30, 0.80, 1.00)     # 每项内累计：计划思考 30% ⇒ +搜索修改 50% ⇒ +落位成果 20%
PHASE_NAMES = (u"计划思考", u"搜索修改", u"落位成果")
SETTLE_S = 180.0                   # 交付后**最多**挂多久等主人开口（写进 progress.json 给它看）
WORK_TTL_S = 120.0                 # ★★ 2026-09-23 17:1x 主人：「你回复完都过了这么久，还在半睁眼状态？」
                                   #   根因：**无进度条的任务（纯问答）只发 `--work`** —— 它把
                                   #     `state.json` 写成 working 且 `ttl=2700`（45 min），而收尾
                                   #     原来**只挂在 `--reply` 的 done/settle 上** ⇒ 感知一安静，
                                   #     `_target_state()` 就落到最后一行 `return self.auto_state`
                                   #     ⇒ **顶着 working 不放**（实测 17:01 写的，17:08 还半睁）。
                                   #   ⇒ 「开工」那一下（**还没带进度**）改用**短 ttl**：
                                   #     · 纯问答：我回复完 ⇒ 感知 25 s 尾巴走完 ⇒ ttl 也到期
                                   #       ⇒ 自己回常态（不用等 45 min）。
                                   #     · 正经任务**不受影响**：`--plan` 让条登场后，感知活跃时
                                   #       **优先级高于 `auto_state`**（见 `fairy_pet._target_state`），
                                   #       所以长任务不会被这段短 ttl 打断。
                                   #   ★ 只换**默认值**；显式 `--ttl N` 照旧听主人的。
#   ★★ 桌宠的主判据已经改成"**主人发下一句指令就让位**"，这个数只是兜底上限
#      ⇒ 必须与 `fairy_pet.DONE_SETTLE_S` 一致（不一致就是文件在撒谎）。
#   ★★ 2026-09-23：**必须与 `fairy_pet.DONE_SETTLE_S` 保持一致**。
#     桌宠计时用的是它自己的 `DONE_SETTLE_S`（60.0），这个数只是
#     "写进文件 + 打印在终端"给人看的 —— 两者不一致就是**文件在撒谎**
#     （当天就发生过：桌宠按 60 s 走，而终端打印"10 s 后回常态"）。
#     10 s 太短的原因见 `fairy_pet.DONE_SETTLE_S` 上的说明。


def roundup(x, nd=0):
    u"""**向上取整**（主人 2026-09-23：「小数的递进规则用 roundup」）。

    ★ 为什么不用内置 `round()`：它是**银行家舍入**（half-to-even）——
      `round(22.5, 1)` = 22.5 没问题，但 `round(11.25, 1)` = **11.2**（向下！）、
      `int(round(22.5))` = **22**。同一类"一半"有时进有时不进，进度条上看着像"卡了一下"，
      也违背"只增不减"的直觉。⇒ 一律**向上**。
    `nd` = 保留几位小数（仍向上）；`ceil` 前先按 1e-6 抹掉浮点噪声。
    """
    m = 10 ** int(nd)
    return math.ceil(round(float(x) * m, 6)) / float(m)


def pct_item(k, p, n):
    u"""第 `k` 项（1 起）的第 `p` 阶段（1/2/3）完成 ⇒ 该跑到百分之几。

    主人给的例子（N=3，一项占 90/3 = 30%）：计划思考完 ⇒ 30×30% = **9%**；
    搜索修改完 ⇒ 30×80% = **24%**；该项完成 ⇒ 30%。
    """
    n = max(1, int(n))
    # ★ 钳位放在**函数里**（原来只有 `do_item` 钳）—— 否则别处直接调用传 k=99
    #   会算出 2227.5%（`_work/step130` 的 A9 抓到的）。
    k = max(1, min(int(k), n))
    p = max(1, min(int(p), len(PHASE_CUM)))
    share = WORK_TOP / float(n)
    frac = PHASE_CUM[p - 1]
    return roundup(((k - 1) + frac) * share)      # ★ 向上取整（不保留小数）


def atomic_write(path, obj):
    """先写 .tmp 再替换 —— 避免"桌宠正好在读、读到半个文件"。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


# ★★ 2026-09-23 主人抓到"两个冒号标题" ⇒ 正文里我自己又写的前缀要**自动剥掉**。
#   现象：卡片上显示「行动目标：开工：现场测通知卡」「行动目标：复核：右边框那条细线…」
#   —— 抬头已经是 `KINDS[kind]` 的前缀，正文再写一遍就成了两个标题 + 两个冒号。
#   根因在我（调用习惯），但**在代码里兜住**比"我记着别写"可靠（我已经犯过两次）。
_LEAD_HEAD = re.compile(u"^\\s*([\\u4e00-\\u9fa5A-Za-z]{2,6})\\s*[：:]\\s*")
_REDUNDANT_HEADS = {
    u"开工", u"收工", u"干活", u"工作", u"开工中", u"行动目标", u"接单", u"接任务", u"开单",
    u"复核", u"校验", u"核对", u"审校", u"审稿", u"自检", u"验收", u"检查",
    u"检索", u"资料检索", u"查资料", u"调研", u"查阅",
    u"应答", u"回答", u"答疑", u"解答", u"说明",
    u"闲聊", u"对话", u"待机", u"待机对话",
    u"已完成", u"完成", u"收尾",
    u"修复", u"排查", u"测试", u"验证", u"比对", u"整理", u"更新",
    u"进度", u"汇报", u"报告",
}


def strip_redundant_head(body):
    u"""去掉正文开头的"重复抬头"（`开工：` / `复核：` / `校验：` …）。

    ★ 只在**词本身是前缀词**（或恰好等于该 kind 的抬头）时才剥，且剥完不能变空 ——
      像"备注：xxx""关于 XX：xxx"这种正常正文一律保留。
    """
    s = body or u""
    m = _LEAD_HEAD.match(s)
    if not m:
        return s
    word = m.group(1)
    if word not in _REDUNDANT_HEADS and word not in {p for p, _ in KINDS.values()}:
        return s
    rest = s[m.end():].strip()
    if not rest:
        return s
    print(u"（已自动去掉正文里重复的抬头「%s：」—— 卡片抬头已经写了）" % word)
    return rest


def build_text(kind, body, no_prefix=False, suffix=True):
    """拼出最终文案。`done` 的收尾会补一句"请前往查阅"（★ 已经写过就别再加）。"""
    prefix = KINDS.get(kind, ("", "idle"))[0]
    body = strip_redundant_head(body)
    if no_prefix or not prefix:
        return body
    s = "%s：%s" % (prefix, body)
    # ★★ 2026-09-23 实测抓到：判据得先**去掉句末标点**再比对。
    #    原来直接 `body.rstrip().endswith(("阅","看"))` —— 我写的句子末尾是"。"，
    #    判不出来 ⇒ 又补一句 ⇒ 屏幕上出现「…请前往查阅。。请前往查阅。」
    tail = body.rstrip(u"。！？!?. ")
    if kind == "done" and tail and not tail.endswith((u"阅", u"看")):
        # ★ 拼接前要**先把 `s` 末尾的标点去掉** —— 上面那个 `tail` 只用于"判要不要补"，
        #   拼接用的还是带标点的 `s` ⇒ 文案自己带「。」时会拼成「…vbs。。请前往查阅。」
        s = s.rstrip(u"。！？!?. ") + u"。请前往查阅。"
    return s


def set_progress(pct, note="", done=False, planned=True, **extra):
    """上报工作进度（0~100）。★ 与 state.json 不同：**不消费**（桌宠反复读当前值）。

    `done=True`：这是"收工"写下的 100% ⇒ 桌宠在**工作态**里看到它会按 **0** 显示
      （否则新任务一开始就顶着上次收工的 100%，见 2026-09-22 主人抓到的 bug）。
      任何普通 `--progress N` 都会把它清成 False。

    `planned=False`：我刚开工、**还没把任务拆好**（主人 2026-09-22 定的时序：
      「你收到后进入工作态（不显示进度条）→ 保持此形态至你切分好任务 → 出现进度条」）。
      桌宠看到 `planned=False` ⇒ 这轮**先不画进度条**；等 `--plan N` 真的上报时才画。

    `**extra`：额外字段（`stages` / `stage`）。桌宠**忽略不认识的键**（只读它要的几个），
      写进去是给**我自己**看的 —— 免得"这轮拆了几步"只记在脑子里。
    """
    v = max(0.0, min(100.0, float(pct)))
    d = {"percent": v, "ts": time.time(), "note": note,
         "done": bool(done), "planned": bool(planned)}
    # ★★ 2026-09-23 17:3x：打上**归属项目** —— 桌宠据此判断"这条该不该在现在这个
    #   项目里显示"（见 `current_project()` 的说明）。
    _pj = current_project()
    if _pj:
        d["project"] = _pj
    d.update(extra)
    atomic_write(PROGRESS, d)
    # ★★ 专属文件：两个项目同时跑时，全局那份会被**后写的覆盖**
    #   （实测被它坑过：`--item` 直接被跳步防护拒掉）。
    #   ⇒ 再写一份"属于本项目的"。写失败**绝不影响主流程**（它只是更准的那一份）。
    if _pj and not _ISOLATED:
        try:
            if not os.path.isdir(PROGRESS_DIR):
                os.makedirs(PROGRESS_DIR)
            atomic_write(prog_file(_pj), d)
        except Exception:
            pass
    return v


def read_progress(project=None):
    u"""读回进度（没有 / 坏了 ⇒ 空 dict）。

    `project` 非空 ⇒ 先读它**专属**那份；读不到再回落全局那份 ——
    但回落时**要认归属**：全局那份若写着"属于别的项目"，就当它不存在。
    ★ 实测被这个坑过（2026-09-23 17:4x）：回落读到另一个项目的 `stages/item/phase`
      ⇒ 拿别人的格数算我的百分比，报格被工具拒掉（"3 项已全部报完"）。
    """
    mine = prog_file(project) if project else None
    for p in (mine, PROGRESS):
        if not p:
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if project and p != mine:
            _own = d.get("project")
            if _own and _own != project:
                continue              # ★ 别人的进度 ⇒ 当它不存在
        return d
    return {}


def do_plan(stages, note=""):
    u"""③ 阶段拆好了（N 个）⇒ **进度条此刻登场**（planned=true），并记住 N。

    ★ 这是"进度条出现"的**唯一触发点** —— 之前 `--work` 写的是 `planned=false`
      （开工 + 拆阶段这两段都**不该有进度条**）。
    """
    n = max(1, int(stages))
    # ★ item / phase 一起归零 —— 否则"重排"之后残留的格数会让下一格判定错位
    set_progress(0, note or (u"拆好 %d 阶段" % n), planned=True, stages=n, stage=0,
                 item=0, phase=0)
    return n


def do_item(k, p, note=""):
    u"""② 第 K 项的**第 P 阶段**完成 ⇒ 自动算百分比（`pct_item`）。返回 (K, N, P, 百分比)。"""
    # ★ 读**本项目**的那一份 —— 全局那份可能已被另一个项目覆盖（见 `PROGRESS_DIR`）。
    d = read_progress(current_project())
    n = int(d.get("stages") or 0)
    if n <= 0:
        raise SystemExit(u"⚠️ 还没 `--plan N`，不知道要等分成几项（progress.json=%r）" % d)
    k = max(1, min(int(k), n))
    p = max(1, min(int(p), len(PHASE_CUM)))
    # ★★ 2026-09-23 加：**跳步防护**。
    #   主人当天的话：「有几项修改就几等分，改完一项跑一段，然后每项再分
    #   计划思考 30% / 搜索修改 50% / 落位成果 20%」⇒ 台阶是**逐格**跳的。
    #   我上一轮报 N=3 时只报了第 1 项和第 3 项、**漏掉第 2 项** ⇒ 条从 30% 直接蹦到 90%
    #   （主人当场看出「有个不对的地方」）。逻辑没错，是**漏报**。
    #   漏报靠记性防不住 ⇒ 做进工具：报的顺序必须正好是**下一格**，否则拒绝。
    _pi = int(d.get("item") or 0)
    _pp = int(d.get("phase") or 0)
    if _pi <= 0:
        _exp = (1, 1)
    elif _pp >= len(PHASE_CUM):
        _exp = (_pi + 1, 1)
    else:
        _exp = (_pi, _pp + 1)
    # ★★ 逃生口**只判一次**（2026-09-23 修）：原来它只挂在"跳步"那道，
    #   于是 9 格报完之后 `--skip-ok` 也照样被"全部报完"那道拒掉
    #   ⇒ 演练/验算式时必须先 `--plan` 重来，很别扭。现在两道拒绝都在 `if not _skip` 里。
    _skip = "--skip-ok" in sys.argv
    if not _skip:
        if _exp[0] > n:
            raise SystemExit(
                u"⚠️ %d 项已全部报完（90%%）—— 接下来该 `--memo`，不是 `--item`。" % n)
        if (k, p) != _exp:
            raise SystemExit(
                u"⚠️ 跳步了：这一格该报「第 %d 项 · %s」，却收到「第 %d 项 · %s」。\n"
                u"   台阶要**一格一格**走 —— 漏报会让进度条一次蹦过去（主人 09-23 当场抓到过）。\n"
                u"   确实要重排：`--plan N` 重新拆 ⇒ 从 0 重走；只想硬推：加 `--skip-ok`。"
                % (_exp[0], PHASE_NAMES[_exp[1] - 1], k, PHASE_NAMES[p - 1]))
    v = pct_item(k, p, n)
    set_progress(v, note or (u"第 %d/%d 项 · %s 完成" % (k, n, PHASE_NAMES[p - 1])),
                 planned=True, stages=n, item=k, phase=p)
    return k, n, p, v


def do_memo(p=5.0, note=""):
    u"""③ 写记忆 / 落盘（90~95%）⇒ 默认写到 95%。返回百分比。"""
    d = read_progress(current_project())     # ★ 读本项目的（见 `PROGRESS_DIR`）
    n = int(d.get("stages") or 0)
    p = max(0.0, min(MEMO_TOP - WORK_TOP, float(p)))
    v = roundup(WORK_TOP + p, 1)      # 保留 1 位小数，仍向上
    set_progress(v, note or (u"写记忆落盘 → %.0f%%" % v), planned=True,
                 stages=n, item=n, phase=len(PHASE_CUM))
    return v


def do_stage(k, note=""):
    u"""旧写法：第 K 项完成 —— **等价 `--item K 3`**，直接**转发**过去。

    ★★ 2026-09-23：原来这里是**独立的代码路径**（自己算 `K×90/N`），
      于是它**绕过了 `--item` 的跳步防护** —— 等于给"漏报"留了个后门，
      而会用这个后门的正是我自己（上一轮就是漏报了第 2 项，条从 30% 蹦到 90%）。
      ⇒ 两条路合一：`--stage K` = `--item K 3`，防护没有缺口。
    返回 (K, N, percent)（保持旧调用方的形状）。
    """
    kk, n, _p, v = do_item(k, len(PHASE_CUM), note)
    return kk, n, v


def do_reply(p=5.0, text="", note="", secs=12.0, ttl=2700.0):
    u"""④ 写回复 / 交付（95~100%）。

    ★ **只有走到 100% 才收尾**：弹完成卡 + 写 `settle` + 状态留在 `working`
      （桌宠看到 `settle` ⇒ `DONE_SETTLE_S` 秒后自己回常态）。
      `p<5` 时只是把条往前推一点，不弹卡、不动状态。
    返回 (百分比, 是否收尾, 卡面文案)。
    """
    d = read_progress(current_project())      # ★ 读本项目的（见 `PROGRESS_DIR`）
    n = int(d.get("stages") or 0)
    p = max(0.0, min(5.0, float(p)))
    v = roundup(MEMO_TOP + p, 1)      # 保留 1 位小数，仍向上
    if p < 5.0:
        set_progress(v, note or (u"写回复 → %.0f%%" % v), planned=True,
                     stages=n, item=n, phase=len(PHASE_CUM))
        return v, False, u""
    # === 100%：交付收尾 ===
    body = (text or "").strip()
    if not body:
        body = (u"主人，%d 项修改已全部完成，请前往查阅。" % n if n
                else u"主人，任务已完成，请前往查阅。")
    card = build_text("done", body, no_prefix=False)
    atomic_write(NOTIFY, {"text": card, "ts": time.time(), "secs": secs, "kind": "done"})
    set_progress(100, u"交付完成（%d 项）" % n, planned=True, done=True,
                 settle=True, settle_s=SETTLE_S, stages=n, item=n,
                 phase=len(PHASE_CUM))
    # ★ 状态刻意**留在 working** —— 主人要的是"100% 显示着 + 卡片弹出来，
    #   过 10 s 再回常态"；真正切 idle 由桌宠按 `settle` 计时做。
    atomic_write(STATE, {"state": "working",
                         "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                         "ttl": ttl, "note": card[:40]})
    return v, True, card


def main():
    ap = argparse.ArgumentParser(description="给 fairy 桌宠发一条通知 / 上报进度")
    ap.add_argument("text", nargs="?", help="要显示的文本（自动折行，最多 3 行）")
    ap.add_argument("--test", action="store_true", help="发一条内置的测试通知（给双击的 vbs 用）")
    ap.add_argument("--kind", default=None, choices=sorted(KINDS),
                    help="任务分类，决定前缀与是否进入工作态（默认按 --work/--done 推断）")
    ap.add_argument("--no-prefix", action="store_true", help="不要前缀，原样显示")
    ap.add_argument("--done", action="store_true", help="收工（state.json → idle，眼睛睁开；进度置 100）")
    ap.add_argument("--work", action="store_true", help="开工（state.json → working，半睁眼；进度归 0）")
    ap.add_argument("--stay", action="store_true", help="只弹卡，绝不动 state.json")
    ap.add_argument("--secs", type=float, default=12.0, help="卡片停留秒数（默认 12）")
    ap.add_argument("--ttl", type=float, default=2700.0, help="working 的兜底超时秒数（默认 2700）")
    ap.add_argument("--progress", type=float, default=None, metavar="N",
                    help="上报工作进度 0~100（只写 progress.json，不弹卡、不动状态）")
    ap.add_argument("--plan", type=int, default=None, metavar="N",
                    help="③ 阶段拆好了（N 个）⇒ 进度条**此刻登场**；之后用 --stage K 汇报（自动等分）")
    ap.add_argument("--stage", type=int, default=None, metavar="K",
                    help="④ 第 K 阶段完成 ⇒ 自动算 K/N×100（需先 --plan N）")
    ap.add_argument("--item", nargs=2, type=int, default=None, metavar=("K", "P"),
                    help="② 第 K 项的第 P 阶段完成（P=1 计划思考 / 2 搜索修改 / 3 落位成果）"
                         "⇒ 自动算 ((K-1)+累计)×90/N")
    ap.add_argument("--memo", nargs="?", type=float, const=5.0, default=None, metavar="P",
                    help="③ 写记忆落盘 ⇒ 90+P%%（默认 5 ⇒ 95%%）")
    # ★★ 2026-09-23 改：`--reply` 做成**开关**（文案走位置参数）。
    #   原来 `nargs="?"`+`type=float` ⇒ `--reply "主人，…"` 会被当成数值 ⇒
    #   `invalid float value: '主人，…'`。而交付这一步**总要带文案**，注定天天撞。
    #   要"95~100 之间推进"用 `--reply-at P`（很少用）。
    ap.add_argument("--reply", action="store_true",
                    help="④ 交付：进度到 100%%，弹完成卡（文案走位置参数），桌宠 10 s 后自动回常态")
    ap.add_argument("--reply-at", type=float, default=None, metavar="P",
                    help="④' 只把条推到 95+P%%（P=0~5），不弹卡、不写 settle")
    ap.add_argument("--skip-ok", action="store_true",
                    help="允许 --item 跳格（默认会拦住漏报：台阶必须一格一格走）")
    ap.add_argument("--note", default=None, help="给 --plan/--stage/--item/--memo 附一句说明（只记进 progress.json）")
    ap.add_argument("--progress-clear", action="store_true", help="删掉 progress.json（工作态不再显示进度条）")
    a = ap.parse_args()

    # ---- ② 第 K 项的第 P 阶段完成（0~90%）----
    if a.item is not None:
        k, n, p, v = do_item(a.item[0], a.item[1], a.note or "")
        print(u"第 %d/%d 项 · %s 完成 ⇒ 进度 %.1f%%" % (k, n, PHASE_NAMES[p - 1], v))
        return

    # ---- ③ 写记忆落盘（90~95%）----
    if a.memo is not None:
        v = do_memo(a.memo, a.note or "")
        print(u"已进记忆落盘阶段 ⇒ 进度 %.1f%%" % v)
        return

    # ---- ④' 只在 95~100 之间推进（不弹卡）----
    if a.reply_at is not None:
        v, _f, _c = do_reply(a.reply_at, "", a.note or "", a.secs, a.ttl)
        print(u"已进写回复阶段 ⇒ 进度 %.1f%%" % v)
        return

    # ---- ④ 交付（100% + 弹卡 + settle）；文案走位置参数 ----
    if a.reply:
        v, fin, card = do_reply(5.0, a.text or "", a.note or "", a.secs, a.ttl)
        print(u"已交付 ⇒ 进度 100%%，已弹卡：%s\n  状态留在 working，桌宠 %.0f s 后自动回常态"
              % (card, SETTLE_S))
        return

    # ---- ③ 拆好阶段：进度条**此刻登场**（不弹卡）----
    if a.plan is not None:
        n = do_plan(a.plan, a.note or "")
        print(u"已拆好 %d 阶段 ⇒ 进度条登场（0%%）；以后每完成一步就用 `--stage K`" % n)
        return

    # ---- ④ 第 K 阶段完成：自动按阶段数等分（不手写百分比）----
    if a.stage is not None:
        k, n, pct = do_stage(a.stage, a.note or "")
        print(u"第 %d/%d 阶段完成 ⇒ 进度 %d%%" % (k, n, pct))
        return

    # ★★ 拦住最常犯的错：开工那一刻带进度 ⇒ 进度条会一开始就挂在屏上，
    #    把"先拆步骤"这一段抹掉（主人 2026-09-23 抓到的）。开工就只写 planned=false。
    if a.work and a.progress is not None:
        print(u"⚠️ 开工不带进度（已忽略 --progress %.0f）—— 开工只要 `--work \"…\"`，"
              u"拆好阶段后再 `--plan N` 让条登场" % a.progress)
        a.progress = None
    if a.done and a.progress is not None:
        a.progress = None            # 收工固定 100，不接受覆盖

    # ---- 只上报进度（不带文字、也不改状态）：不动别的，直接返回 ----
    if a.progress is not None and not a.text:
        v = set_progress(a.progress, a.note or "")
        print("已上报进度：%.0f%%" % v)
        return
    if a.progress_clear:
        try:
            os.remove(PROGRESS)
            print("已清除 progress.json（工作态不再显示进度条）")
        except FileNotFoundError:
            print("progress.json 本来就不存在")
        if not a.text:
            return

    body = a.text
    if a.test and not body:
        body = "链路正常，待命中。"
        a.kind = a.kind or "chat"
        a.stay = True
    if not body and not (a.work or a.done):
        ap.error("要么给一句文本，要么加 --progress N / --plan N / --item K P / "
                 "--memo / --reply / --stage K / --progress-clear")

    kind = a.kind or ("done" if a.done else ("work" if a.work else None))
    # ★★ `--work` / `--done` **允许不带文案**（只切状态）—— 开工那一下本来就不必弹卡
    #    （主人要的是"进工作态、没进度条"，不是"弹一张卡"）。没文案 ⇒ `text=""`。
    text = build_text(kind, body, a.no_prefix) if body else u""
    if text:
        # ★ kind 一起写进去：桌宠据此选卡片配色（干活=蓝 / 问答=青 / 闲聊=紫）
        atomic_write(NOTIFY, {"text": text, "ts": time.time(), "secs": a.secs,
                              "kind": kind or "work"})

    # 状态：显式 --work/--done 优先；否则按 kind 的默认；--stay 则一律不动
    if not a.stay:
        if a.done:
            st = "idle"
        elif a.work:
            st = "working"
        else:
            st = KINDS.get(kind, ("", "idle"))[1] if kind else None
        if st:
            # ★★ 「开工」那一下（working 且**还没带进度**）用短 ttl —— 见 `WORK_TTL_S` 的说明。
            #   纯问答只发 `--work`（没有 `--reply` 的 done/settle），短 ttl 就是它**唯一**的
            #   收尾路径。带进度的档要么提前 return（`--reply`），要么不改这里的默认值。
            _ttl = a.ttl
            if st == "working" and a.progress is None and abs(a.ttl - 2700.0) < 1e-6:
                _ttl = WORK_TTL_S
            atomic_write(STATE, {"state": st, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "ttl": _ttl, "note": text[:40]})
            # ★ 进度与状态一起走：开工归 0、收工置满（否则会出现"WorkBuddy 说忙、
            #   进度条却停在上一次的 87%"这种自相矛盾的画面）
            if a.progress is not None:
                set_progress(a.progress)
            elif st == "working":
                # ★★ 主人 2026-09-22 定的时序：**开工这一刻先不显示进度条**
                #   （「你收到后进入工作态（不显示进度条）→ 保持此形态至你切分好任务
                #     → 出现进度条」）。⇒ 开工写 `planned=False`，等 `--plan N` 让条登场。
                set_progress(0, "开工", planned=False)
            else:
                set_progress(100, "收工", done=True)
            print("已发给 fairy：%s\n  同时把 state.json 置为 %s（%s）"
                  % (text or u"（只切状态，没发卡）", st,
                     "半睁眼干活" if st == "working" else "睁眼常态"))
            return
    print("已发给 fairy：%s\n  只弹卡，未改状态" % (text or u"（空文案，没发卡）"))


if __name__ == "__main__":
    main()
