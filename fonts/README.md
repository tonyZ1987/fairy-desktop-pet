# pet-v2\fonts —— 随包分发的字体

| 文件 | 说明 |
|---|---|
| `NotoSansSC-VF.ttf` | **Noto Sans SC 可变字体**（16.95 MB）。一个文件覆盖 `Thin…Black` 全部字重 ⇒ 右键菜单里「中 / 粗 / 特粗」三档都从它取。 |
| `OFL.txt` | SIL Open Font License 1.1 正文（OFL 要求随字体一起分发）。 |

## 为什么放进仓库

主人 2026-09-23：「有需要的话字体一起打包吧」。

通知卡的字体要跟《绝区零》那种**厚重几何黑体**对齐，而"思源黑体"在 Windows 上**不是自带字体**
（本机那份是别的软件装进去的）⇒ 只靠系统字体，换一台机器就会**降级成微软雅黑**，字形就变了。
把可变字体打进包，**任何机器上长得一模一样**。

## 版权与许可（勿删）

- 字体名：**Noto Sans SC**（= Source Han Sans / 思源黑体，同一套设计）
- 版权：Copyright 2014-2024 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'
  （Noto 版由 Google 发行；以 `NotoSansSC-VF.ttf` 内嵌的 name 表为准）
- 许可：**SIL Open Font License 1.1** —— 允许**随软件一起分发**（含商用），
  条件：① 保留本许可文件；② 不得单独售卖字体本身；③ 若改名再分发，不得使用保留字体名。
- 许可全文：见同目录 `OFL.txt`（亦见 <https://openfontlicense.org/>）

## 用法

主程序 `fairy_pet.FONT_DIRS` **把这个目录排在系统字体前面**：

```python
FONT_DIRS = (os.path.join(BASE, "fonts"), "C:/Windows/Fonts")
```

`_mkfont()` 按 `FONT_CHOICES[key]` 的候选顺序找：**先这里 → 再系统 → 最后 PIL 默认**
⇒ **缺字体只会降级、不会崩**。
