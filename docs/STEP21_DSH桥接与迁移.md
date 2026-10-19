# STEP21 · 把「跟 AI 联动」从 WorkBuddy 迁到 DSH（DeepSeek Harness）

> 2026-10-19。主人原话：**「读这个文件夹里的文件，并将桌宠与原 workbuddy 互动的能力移至到当前 agent 上」**。
> 本文是这次迁移的**唯一权威说明**：改了什么、凭什么这么改、怎么装/卸、怎么排障。

---

> ## ⚠ 2026-10-19 10:50 勘误（**先读这段，它推翻下面一部分结论**）
>
> 当晚复测发现：**打包版（Electron 桌面版）DSH 里根本没有钩子桥接**。
> 直接读 `resources\app.asar` 的头部索引：`dsh/node_modules/@deepseek-ai/` 下 **306 个包**，
> 含 `dsh-tool-todo` / `dsh-session-projection` / `dsh-desktop-host`，
> 但**没有任何名字里带 `hook` 的包** —— `@deepseek-ai/dsh-hooks-claude-code` **只随 CLI 分发**。
>
> ⇒ **桌面版没有 `SessionStart / UserPromptSubmit / Stop` 这三个事件**，
> 本文 §2~§5 那套"注入协议"在桌面版**装不上、也不会响**（配置本身没错，是包里没有）。
> 已做三件事：
> 1. `dsh_bridge_install.py` 现在**先查 app.asar**，桌面版缺插件就**拒绝安装**并说明原因；
> 2. 本机那条装不上的配置**已摘掉**（`--uninstall`），`--status` 现在会如实报"桌面版没有钩子桥接"；
> 3. **真正补上进度能力的是 [`STEP22_阶段进度（todos投影）.md`](STEP22_阶段进度（todos投影）.md)**：
>    桌宠直接读 `todos` 投影**自动推进度条**，不需要钩子、不需要 AI 上报。
>
> 还想让桌面版"AI 知道要发完成卡"：只有两条路 ——
> ① 换 **CLI 版 DSH** 跑任务（本安装器对它有效）；② 给桌面版写一个**本地原生插件**
> （放 profile 目录、按路径引用，不依赖那个缺失的包）。②还没做。

---

## 0 一句话

宿主从 **WorkBuddy** 换成 **DSH**（DeepSeek Harness），但**能力一模一样**：

| 能力 | WorkBuddy 时代 | DSH 时代 |
|---|---|---|
| 主人一发指令 ⇒ 桌宠**立刻半睁** | 读 `~/.workbuddy/projects/**.jsonl`（明文） | 读 **DSH 会话投影缓存**（明文 JSON）+ 钩子事件流 |
| AI 说完了 ⇒ **自己睁眼** | 末事件 + 8/25 s 保持窗口 | `openTurnStartSeq` 变空 / `stop` 事件 + 25 s 尾巴 |
| AI **主动报进度**（`--plan/--item/--memo/--reply`） | `~/.workbuddy/settings.json` 里的钩子 | `~/.dsh/profiles/<profile>/cordis.patch.yml` 里的插件行 + `dsh_hooks.json` |
| 干完弹**完成卡** | `notify.json` | 同左（这一层完全没变） |

★ **桌宠的"下游"一个字没改**：它照旧只读 `state.json` / `progress.json` / `_prog/*.json` /
`notify.json` / `pos.json`。变的只是"上游由谁喂"。

---

## 1 为什么不能照搬 WorkBuddy 那套

DSH 把会话写成 **zstd 压缩**的

```
%DSH_HOME%\sessions\<项目编码>\<session-id>\session.vN.jsonl.zstd
```

而且**是多帧追加**（每写一批事件就压成一个新帧），Python 标准库解不了；
DSH 自己的钩子文档也写明「默认 zstd 压缩的会话日志无法被 hook 脚本读取」。
⇒ 两条老路（tail jsonl、`aiTitle` 读标题）都断了。

**但是**，DSH 有两样 WorkBuddy 没有的好东西：

### ① 会话投影缓存（**明文 JSON**，零配置）
```
%DSH_HOME%\storages\session_projcache\sessions\<session-id>.json
    record.identity.cwd                ← 这个会话属于哪个工作目录
    record.rows.turnBoundary.val:
        openTurnStartSeq == null  ⇒ 没人在跑
        openTurnStartSeq == 整数  ⇒ ★ **这一轮正在跑**
```
★ 它是 DSH 自己维护的**逐事件投影**，**中止/出错时 `openTurnStartSeq` 一样会清回 null**
—— 正好补上 DSH 钩子"主人按停时不发 `Stop`"的缺口。

### ② Claude Code 兼容钩子（官方自带桥）
`@deepseek-ai/dsh-hooks-claude-code` 把一份**标准 `hooks.json`** 映射到 DSH 自己的扩展点：

| 我们的 `hooks.json` 事件 | DSH 那边实际挂的扩展点 |
|---|---|
| `SessionStart` | `agent/session-start` |
| `UserPromptSubmit` | `agent/pre-step`（能注入上下文） |
| `Stop` | `agent/turn-stopping` |

⇒ 不需要碰 DSH 的任何内部 API，也不用自己写 cordis 插件。

---

## 2 新增/改动的文件

### 新增（都是"给 DSH 用"的）

| 文件 | 干什么 |
|---|---|
| `fairy_activity_dsh.py` | **DSH 活动感知**。接口与老 `fairy_activity.ActivityWatch` 完全一致（`active()` / `age()` / `user_at` / `last_dir` / `per_proj` / `info()`），实现换成"读投影缓存 + 读钩子事件流" |
| `fairy_dsh_hook.py` | **钩子脚本**。两件事：① 往 `dsh_events.jsonl` 追一行事实；② `SessionStart`/`UserPromptSubmit` 时把协议正文注入给 AI（每会话一次 + 40 min 刷新） |
| `fairy_dsh_bridge_prompt.md` | **注入正文**（给人改：想换措辞直接改这个文件，下次注入就生效） |
| `dsh_hooks.json` | **安装器生成**的钩子配置（不手改；里面是本机解释器绝对路径） |
| `dsh_bridge_install.py` | **一键装/卸/体检**：`--install` / `--uninstall` / `--status`。装之前**先自测**，写 YAML 前后**都校验** |
| `install_dsh_bridge.vbs` / `uninstall_dsh_bridge.vbs` | 双击入口（纯 ASCII + CRLF，无 BOM），结论用 MsgBox 弹出来 |

### 改动（**最小侵入**）

| 文件 | 改了什么 | 为什么 |
|---|---|---|
| `fairy_activity.py` | 新增 `MergeWatch` + `make_watcher()`（**没动**老的 `ActivityWatch`） | 两边合并：**DSH 优先、WorkBuddy 兜底、谁在干活跟谁**。任何一个源坏了另一个照常 |
| `fairy_pet.py` | ① `self.act = FA.make_watcher()`；② `WB_EXE` → `WB_EXES = (workbuddy.exe, DeepSeek Harness.exe)`（`_typing_in_wb` 用）；③ 菜单「来源」的 `WorkBuddy` 段改名 **`AI`** | ① 一行接入；② 收尾"主人一动手就让位"在 DSH 上也要成立；③ 写死宿主名会随换工具过期 |
| `fairy_notify.py` | `_env_project()` 增加 **DSH 分支**：`cwd` → 项目标识 | DSH 不给工具调用注入 `CODEBUDDY_PROJECT_DIR`；但**工具的工作目录就是会话工作区** |
| `fairy_status.py` | 体检多一段 `DSH桥接：hooks.json / 事件流 / 桥接日志` | 主人只会问"它怎么不动了"，这条链路有四段，不逐段报就只能猜 |

---

## 3 判据（DSH 侧到底怎么判"在不在干活"）

每个会话各算各的（多项目并行就是多行），**任一成立即工作态**：

```
① 投影缓存说这一轮开着（openTurnStartSeq 非 null，且 30 min 内有动静）
② 有 user 事件、且 120 s 内（缓存还没落盘的宽限）
③ 刚观察到这一轮关掉，25 s 内（"我最后一句之后的尾巴"）
```

- `ASSIST_HOLD_S = 25`：主人要的「从进入工作态起就保持，一直到你回复结束」。
- `STALE_TURN_S = 1800`：**唯一兜底**。DSH 崩了/被强杀时缓存不再更新，靠它把"永远半睁"挡在半小时内。
- 关掉「感知」开关（右键 → 行为 → 感知）⇒ 以上全部不生效，只认 AI 上报的 `state.json`。

---

## 4 装 / 卸

**装**（任选其一）：
- 双击 **`install_dsh_bridge.vbs`**（推荐，结论会弹窗告诉你）；
- 或 `python dsh_bridge_install.py --install`。

它做四件事，**每一步都可回读**：

1. 生成 `dsh_hooks.json`（三个事件各一条命令，事件名作为**命令行参数**传）；
2. **自测**：按 DSH 的真实方式 `powershell -NoProfile -NonInteractive -Command …` 跑三个事件，
   验「退出码 0 / stdout 是合法 JSON / `hookEventName` 对得上 / 事件流有新增」——
   还专门跑一遍**没有 stdin** 的形态（DSH 沙箱里的实际情形）；
3. 往 profile 补丁层追加**一段**（`# ==== Fairy 桌宠桥接 · 开始 … ==== ` 哨兵包着），
   写之前备份、写之后用 DSH 自带的 `js-yaml` **解析校验**，不合法就**回滚**；
4. 打印验证方法。

**卸**：双击 `uninstall_dsh_bridge.vbs`（= `--uninstall`）——只摘我们那一段，别的一行不动。

**临时静音**（不改配置）：在桌宠目录建一个空文件 `fairy_dsh_off`。

**生效时机**：desktop profile 的用户补丁层**不是热加载**的（`package.json` 里没有
`patchReload: live`），所以**要重启 DSH**。

> ★★ **但桌面版装了也不会响**（见文首勘误：打包版里没有这个插件）。
> 安装器现在会**先查 `app.asar`**，桌面版缺插件就拒绝安装并打印原因 ——
> 这样至少不会再出现"装好了却没动静"的假象。

---

## 5 ★★ 四个实测坑（踩过，别再踩）

1. **`pythonw.exe` 在 DSH 的 Windows 沙箱里静默不干活**
   - 现象：会话日志里 `hook/invoked` + `hook/result` 成对出现、`exitCode: 0`、`decision: pass`，
     但 `stdout`/`stderr` 重定向到文件都是 **0 字节**、事件流一条没写、桥接日志一行没有。
   - 对照：同一份命令换 `python.exe` **立刻正常**（事件、注入、退出码都对）。
   - ⇒ 桥接里所有"给 DSH 用"的解释器一律取 **`python.exe`**（见到 `pythonw.exe` 就地替换）。
     WorkBuddy 版必须用 pythonw 是为了不闪黑框；DSH 的 shell 本来就是隐藏的。

2. **钩子的 stdin 可能到不了子进程**（沙箱里）
   - ⇒ 事件名改成**命令行参数**（`dsh_hooks.json` 里三个事件各写一条命令，末尾带事件名）；
     `cwd` 用 `os.getcwd()`（DSH 保证钩子进程的工作目录 = 会话工作区）。
   - 但也要**保留 stdin 通路**（有就用）—— 实测有时 payload 是能到的（会话日志里能看到真实 session id）。

3. **desktop profile 的补丁层不是热加载**
   - 写进 `cordis.patch.yml` 后**当前进程不会挂上**，必须重启 DSH。
   - 判断方法：随便发一句话，看 `dsh_events.jsonl` 有没有新记录。

4. **中文路径本身没问题**（别冤枉它）
   - 做过对照实验：纯 ASCII 探针能跑；带中文 argv 的探针也能收到完整路径。
   - 出问题的从来是 `pythonw`，不是路径编码。

---

## 6 排障：一条链四段，逐段看

```
DSH 钩子 ──①──> dsh_events.jsonl ──②──> DshActivityWatch ──③──> state.json / 进度 ──④──> 桌宠
```

| 段 | 看哪儿 | 正常长什么样 |
|---|---|---|
| ① 钩子有没有跑 | `dsh_events.jsonl`（有 = 跑了）、`fairy_dsh_bridge.log`（每跑一次一行） | 说一句话后多一条 `{"ev":"user",…}` |
| ② 事件流有没有被读 | `heartbeat.txt` 里的 `act=DSH:…|开N/M个会话` | `开1/13个会话` 这类 |
| ③ 投影缓存有没有更新 | `%DSH_HOME%\storages\session_projcache\sessions\*.json` 的 mtime | 一轮工作时 mtime 每秒都在动 |
| ④ 桌宠状态机 | `heartbeat.txt` 的 `state=working/idle`、`bar=show/wait/off` | 我这条 turn 进行中 ⇒ `state=working` |

**一键体检**：双击 `check_fairy.vbs`（里面的「DSH桥接」那几行就是 ①②的结论）。

其它开关：
- `FAIRY_DSH_LOG=<路径>`：把事件流指到别处（安装器自检就用它，免得污染真实时间线）；
- `FAIRY_DSH_CACHE=<目录>`：把投影缓存指到别处（演练用）；
- `FAIRY_DSH_PROFILE=<名字>`：指定 profile（默认读 `%DSH_PROFILE%`，否则 `desktop`）。

---

## 7 自测（不需要装东西也能跑）

```bat
python _work\step21_dsh_bridge_test.py
```

覆盖：判据（缓存开/关、尾巴、开单宽限、失效兜底）｜`MergeWatch` 合并｜
钩子**真跑子进程**（退出码/注入 JSON/事件流/每会话只注入一次/静音开关）｜YAML 补丁片段的解析与幂等。
★ 全程用临时目录，**绝不碰**真实的 `dsh_events.jsonl` / `state.json` / `progress.json`。
