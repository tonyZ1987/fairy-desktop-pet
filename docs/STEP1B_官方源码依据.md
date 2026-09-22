# fairy 桌宠 v2 · STEP ①-B：官方源码依据（比逆向素材可靠得多）

> 主人提示"看 mp4 或 git 链接里的图"。
> **核对结果：那个 GitHub 仓库里一张图片都没有**（整个 tree 里没有任何 png/jpg/gif/svg/webp）。
> 但里面有**更好的东西——官方 mascot 的完整 SVG 源码**。本文件记录从源码里读到的权威事实。
> 日期：2026-09-21 ｜ 仓库：[Chengzhibense/Fairy-DSH](https://github.com/Chengzhibense/Fairy-DSH)（Apache-2.0）

---

## 0. 一句话结论

**半睁眼不用猜了，也不用"压扁"——官方本来就这么做的，而且和视频里拍到的完全一致。**
`半睁眼` = 官方活动态 **`thinking`**：眼睑是一段向下压的贝塞尔弧，把眼白上部裁掉，露出下方月牙。

---

## 1. 官方状态机（直接照抄需求）

`fairy-visual/dsh-fairy-visual/README.md` 原文：

> The visual controller projects **one activity value** from the active session: **`normal`, `thinking`, or `comforting`**.
> Its current session lifecycle projection is **`idle`, `running`, or `completed`**.

| 官方活动态 | 眼睛表现 | 主人的需求对应的场景 |
|---|---|---|
| `normal` | 睁眼（完整眼白） | **平时** |
| **`thinking`** | **半睁眼**（眼睑下压，露下方月牙） | **执行任务时 ← 就是这个** |
| `comforting` | 另一种眯眼（弧是向上弯的，"a gentle, slightly drooped brow"） | 备用/安慰态 |

生命周期 `idle` / `running` / `completed` ↔ 活动态 `normal` / `thinking` / `normal`。
→ 那么"任务中 = 半睁、完成 = 睁眼"**就是官方原本的设计**，之前 v1 的做法（垂直压扁 + 随机半眯）是自创的。

---

## 2. 官方几何（`mascot-geometry.js` 逐字抄录）

画布 `viewBox="0 0 160 160"`，中心 (80, 80)。

| 元素 | 半径/坐标 | 颜色 |
|---|---|---|
| `outerHaloRadius` | **79** | `--dsh-fairy-outer-halo-color`：暗色主题 **`#c9f8ff`**（浅青白）；亮色主题 `#172531` |
| `outerDiscRadius` | **68** | 线性渐变 `#4053f0` →(54%) `#3045dc` → `#3d50c8`，方向 (.2,0)→(.8,1) |
| `outerStrokeWidth` | **1.2** | 描边 **`#f2fbff`**（就是那圈细白边） |
| 深色"角/眼睑" | 圆 r=**51.75** ＋ 方 rect 86×86 rx=2 **旋转 3°** | **`#2b3388`** |
| `scleraRadius` | **48** | **`#eef0f5`** |
| `scleraContactStrokeWidth` | 0.8 | 描边 `#ffffff` @ 0.16 |
| `scleraHaloRadius` | **56** | 白色径向光晕 |
| layer-three | **33** | `#9daee0` |
| layer-two | **24.15** | `#eef0f5` |
| layer-two（虹膜） | **23.5** | `#317bcf` |
| layer-one（细白环） | **16.6** | 描边 `#f5f8fd` 宽 0.1 |
| **`pupilRadius`** | **16** | `#3b3d8a` |
| `highlightCenter` | **(98, 100.5)** → 距心 27.3、方向 **48.7°** | — |
| `highlightRadius` | **11** | `#f5f8fd` |
| `highlightHaloRadius` | **18** | 白色光晕 |
| 扫描线 | pattern 4×4，1 单位高 | `#c9f8ff` @ 0.055，组 opacity 0.42，裁到盘内 |

---

## 3. ★ 与 GIF 的交叉验证（证明 GIF 就是这个设计）

把官方半径换算到 GIF 的像素尺度（以官方 `outerDisc=68` 对齐 GIF 实测"蓝亮环外缘"÷）后逐项比对：

| 特征 | 官方（user unit） | GIF 实测换算 | 结论 |
|---|---|---|---|
| 瞳孔 | 16 | **16.4** | ✅ |
| 眼白环（layer-three 33 → sclera 48 之间） | 33 – 48 | **30.5 – 42** | ✅ |
| 深色角环 | 48 – 51.75 / 60.8（角向） | **44.7 – 64.6** | ✅ |
| 外盘 | 68 | **65.6** | ✅ |

**→ GIF 与官方 SVG 是同一个形象。** （此前我一度以为对不上，原因是把 GIF 的"发光半径"误当成官方 `outerHaloRadius` 去比，而 GIF 是**额外加了 bloom 的资产**。）

**唯一系统性差异**：GIF 的**外发光比官方宽约 1.5 倍、且更蓝**。
官方 halo 只到 `79/68 = 1.16 ×` 盘半径、颜色是浅青白 `#c9f8ff`；
GIF 的辉光延伸到 `256/148 = 1.73 ×` 盘半径，且明显偏蓝。
→ 推测 GIF 是**带额外 bloom 的宣传/游戏资产**，官方 SVG 是网页端的忠实复刻。**这一项要主人定**（见第 6 节）。

---

## 4. ★ `thinking`（半睁眼）的官方实现（`mascot-eye-svg.js` + `mascot-runtime.js`）

**眼睑 = 一个 clipPath，作用在 `.dsh-fairy-eye` 组上**（不是压扁整只眼）：

```svg
<clipPath id="dsh-fairy-thinking-eye-clip">
  <path class="dsh-fairy-thinking-clip-shape" d="M20 60 Q80 90 140 60 V160 H20 Z"/>
  <rect x="0" y="108" width="160" height="52"/>
</clipPath>
```
CSS：`[data-state="thinking"] .dsh-fairy-eye { clip-path: url(#dsh-fairy-thinking-eye-clip); }`

**动画**（`renderMotionFrame()`）：
```js
clip.style.transform = "translateY(" + (14.5 + clipProgress) + "px) scaleY(" + (.55 + .45*clipProgress) + ")";
```
即：弧**整体下移 14.5→15.5**、**纵向拉长 0.55→1.0**，随主时钟相位往复 → 眼睑"压下去又抬起来"。
`comforting` 用另一条弧（`Q80 30`，向上弯）＋ `translateY(-4 + 8*clipProgress)`。

**已验证**：本仓库 `dsh_mascot.py` 按上述公式数值重建，`thinking p=.80` 与**视频 t=50s 实拍半睁眼几乎逐像素一致**（见 `TEST_G`）。

---

## 5. 其余官方动画参数（`mascot-runtime.js`）

| 项 | 参数 |
|---|---|
| 眼睛分层呼吸 | 四层各自 `scale()`，周期由主时钟相位驱动（`leadMs/1440` ⇒ **周期 1440 ms**）：sclera `0.985→0.91`、layer3 `1→0.90`(lead 45ms)、layer2 `1→0.87`(lead 90ms)、layer1 `1→0.85`(lead 180ms) |
| 缓动 | 三次贝塞尔反解（cubic ease-out），三角波相位 |
| **"四根睫毛/眼睑"旋转** | `.dsh-fairy-corners { transform: rotate(var(--dsh-lash-angle)) }`，`@keyframes dsh-fairy-lashes { to { transform: rotate(360deg) } }` → **深色圆+方持续转 360°** |
| 眼睛闪烁 | 随机 `1750–3600 ms` 触发一次，单次 `155–255 ms`，`#ffffff` 覆盖层 |
| todo 故障效果 | 5 条水平切片克隆 + 横向位移噪声 `feTurbulence`/`feDisplacementMap`（scale 5），随机 `2300–4600 ms` |
| 动画速率 | `animationRate` 可调（设置项 `dsh-fairy-mascot-animation-speed`） |

> ⚠️ 注意：**我的 122 帧互相关实测结论"不旋转"与此冲突**。官方"角"是 4 重对称的方，转 360° 时互相关会以 90° 为周期；实测 ±40° 搜索内最佳偏移恒 0、相关 0.991，说明**GIF 这 122 帧里"角"没转，或转动幅度被 bloom 淹没**。→ 待定项，需要主人或进一步量测确认（见第 6 节）。

---

## 6. 待主人拍板（更新版）

1. **形象基准换不换成官方源码？**
   我建议换：官方几何是**权威且精确**的，还自带 `normal/thinking/comforting` 三态与全部动画参数，"逆向"的建模风险直接归零。
2. **外发光听谁的？** 官方 = 浅青白 `#c9f8ff`、范围 1.16× 盘半径；GIF = 偏蓝、1.73× 盘半径。
   （我倾向：形状与配色用官方，**辉光范围按 GIF 放大**，因为你给的观感依据是 GIF 和视频。）
3. **"四根睫毛"要不要转？** 官方会 360° 慢转；GIF 实测没转出来。转 = 更接近官方产品，不转 = 更接近 GIF。
4. **`thinking` 的半睁幅度**：官方是 14.5→15.5 / 0.55→1.0 往复。要不要固定在一个"看着像在认真干活"的中间值（如 p≈0.7）静止，或跟随呼吸小幅动？
5. 上一轮还没定的：**十二边形纹理**（现在有官方答案了：那是"圆＋方"的角，不是十二边形，可作废这条）、**`state.json` 触发机制**、**显示尺寸**、**git 首次提交**。

---

## 7. 本轮新增文件

```
pet-v2/
├─ TEST_F_官方SVG重建_vs_GIF.png        ← 官方几何重建 vs GIF 中位帧
├─ TEST_G_官方thinking_vs_视频半睁眼.png ← ★ 决定性：官方 thinking vs 视频实拍半睁眼
├─ dsh_mascot.py                        ← 按官方 SVG 数值重建的渲染器（含 normal/thinking/comforting）
└─ _work/dsh/                           ← 从 GitHub 拉下来的官方源码原文（13 个文件）
```
