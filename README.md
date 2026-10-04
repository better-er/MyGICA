# MyGICA 使用文档

## 🚀 快速开始

本项目已经发布至 PyPI，可以通过 pip 或兼容的工具安装：

```powershell
pip install MyGICA  # pip 安装方式
```

或者使用 uv 工具管理器安装（推荐）：

```powershell
uv tool install MyGICA    # uv 工具管理器安装方式
```

## 📄 简介

**MyGICA** (剪辑 MyGICA 视频的 MyGICA 工具, MyGICA 即 MyGO!!!!! & MUJICA) 是一个文本化、结构化的视频剪辑工具，用于盯帧剪辑
MyGICA 填词 / MAD 视频。简单地说，就是代码剪视频且剪辑单位为帧。

目前本项目**并非 AI 项目**，只是将**视频剪辑流程文本化、结构化**，可以作为与其他人或 AI **协作的接口**，因此并不能自动剪辑。MyGICA
使用 TOML 文本作为剪辑说明，扩展名为 .MyGICA.toml，Python + FFmpeg 作为编译器。

目前发展方向有二：1、传统功能。继续优化数据结构和编译器以支持更多功能或简化使用难度。2、AI 功能。使用 AI 生成 .MyGICA.toml
文件初稿（不看好）。

MyGICA 优先实现了符合通常 MyGICA 填词 / MAD 剪辑习惯的编译器，如：先确定时间范围，再在范围内定义字幕与片段；字幕常使用应援色；默认总有
bgm 等。

解决剪映不能导出 23.976 (24000 / 1001) 帧视频（导出24帧会和预览不一致，出现闪帧）及 PR 不能导入 .mkv 视频素材的问题。

## 🧩 功能特性

- 自动添加字幕（drawtext 滤镜，默认有入场出场动画）
- 自动填充时间空隙（自动接续上个片段，可指定 NEXT 接续下个片段，全留空时使用 black）
- 自动补全片段起止时间（自动推断单个缺失的 start / end）
- 自动处理响度为 -2dBTP
- 支持盯帧剪辑（基于帧的时间定义）
- 支持多源素材（如 MKV、MP4 等，由 ffmpeg 支持）
- 支持字体颜色映射（用于快速使用应援色）
- 支持缓存避免重复渲染，并可以手动清理指定时长未使用的缓存
- 最终拼接直接复制流不重编码，靠时长对齐消除接缝空档
- 自动生成选帧理由外挂字幕，把每个 Clip 的 reason 汇总成与视频同名的 .ass，播放时可实时查看
- 自动检查同一个源的同一帧是否被多个 Clip 重复使用，重复时直接以 error 级别报出

---

## 📁 项目结构说明

```
project/
├── pyproject.toml         # Python 项目配置文件
├── README.md              # 本说明文档
├── LICENSE                # AGPL-3.0 许可证
├── 示例.MyGICA.toml        # 主配置文件（.MyGICA.toml 格式）
├── SC-Heavy.otf           # 字体文件（思源粗宋或其他字体）
├── cache_dir/             # 缓存目录（临时片段）
├── output_dir/            # 输出目录（最终视频）
└── src/
    └── MyGICA/
        ├── __init__.py                  # 包标记
        ├── A_compiler.py                # 编译器脚本，主逻辑，对应 MyGICA 命令行工具
        ├── structure.py                 # 数据结构定义，用于解析 TOML，可以查看合法的定义
        ├── time_based_cache_cleaner.py  # 按时间管理缓存的工具，对应 MyGICA_cache_cleaner 命令行工具
        └── srt2MyGICA.py                # 将 SRT 转为 .MyGICA.toml 的脚本框架，对应 srt2MyGICA 命令行工具
```

---

## 🛠️ 配置文件格式说明（TOML）

### 全局设置

```toml
fps = '24000/1001'         # 帧率
project_suffix = ".mp4"    # 输出文件格式
#start = 0                 # 视频起始帧（闭区间）建议只在需要快速预览时定义以只渲染部分视频
#end = 5000                # 视频结束帧（开区间，下同）建议只在需要快速预览时定义以只渲染部分视频
```

### 源素材定义

```toml
[sources]
go1 = 'D:\path\to\mygo1.mkv'
# ...
ji13 = 'D:\path\to\mujica13.mkv'
black = 'cache_in\test_video_basic.mp4'  # 可省略，省略时 black 由工具用 lavfi 现场合成，不必自备素材
bgm = 'background_music.wav'  # 背景音乐，自动合并到视频中，必选
```

### 颜色映射（用于字幕）

```toml
[colors]
'灯' = '#77BBDD'
'爱音' = '#FF8899'
# ...
```

### 时间范围定义（剪辑段）

```toml
[[ranges]]
start = 2020
end = 2098
[[ranges.texts]]
text = "火爆脾气一脚踢到钛合金"
#drawtext = 'box=1:boxborderw=10'  # 可选，drawtext 自身的参数，写在后面可覆盖默认样式
#filters = 'gblur=sigma=4'  # 可选，这一层字幕图的滤镜链，只作用在这一条字幕上
#start = 0     # 可选，相对本 Range 起点的帧偏移，只在 [start, end) 里显示
#end = 40      # 可选，开区间，只给一端时按另一端补齐
[[ranges.clips]]
source = "go1"  # 可选 PREV 和 NEXT，表示接续上个或下个片段
start = 29590
end = 29626
reason = "<选这个画面的理由，AI 必写>"  # 选这个画面的理由，会汇总到与视频同名的 .ass 外挂字幕
#filters = 'hue=s=0,unsharp'  # 可选，透传给 ffmpeg 的滤镜串，不得改变帧数与帧率
[[ranges.clips]]
source = "go1"
start = 30852  # 可省略一个 start 或 end，脚本会自动补全
volume = -50
#sound = 'sound_1'  # 可选，替换片段自带音频
```

每个 `[[ranges.clips]]` 都必须写 `reason`，写明为什么选这个画面，AI 生成或修改剪辑时不得省略。编译后会汇总成与视频同名的 `.ass` 外挂字幕，播放视频即可实时核对每个画面。

`Text` 不写 `start` / `end` 时整段显示，写了就只在那个帧区间里显示，同一个 Range 可以排多条时间不同的字幕，区间按 Range 起点算，允许重叠，重叠时一起显示。淡入淡出按每条 Text 自己的显示区间各算各的，一句连续显示的字幕被别的字幕从中间插进来，不会跟着淡出再淡入。

`Clip` 的 `filters` 会拼进该片段的 ffmpeg 滤镜链，拿来调色、锐化、缩放都行，但不许改变帧数或帧率，否则会被帧数校验拦下。

---

## 🧠 自动功能说明

### 1. 自动补全时间（start / end）

如果某段 clip 中只写了 `start` 或 `end`，脚本会自动推断另一个值以填满整个 range。

### 2. 自动填充空隙

如果 ranges 之间有时间空隙，脚本会自动延续片段或插入 black 片段。

### 3. 字幕颜色映射

`fontcolor` 可以使用 `colors` 表中定义的别名，如 `'灯'` → `#77BBDD`。

### 4. 重复帧检查

同一个源的同一帧被多个 clip 使用时，解析配置阶段就会以 `error` 级别报出重复区间和它们所在的 range。

同一段画面在成片里出现两次，观众一眼就能看出是选帧重复，所以这里直接报错提醒，不等到成片出来才发现。

`black` 源不参与检查，它只是纯色占位，重复使用没有意义。

### 5. 帧数与帧率校验

每个片段、每个 Range 拼接后、以及最终成片，都会用 ffprobe 逐帧数一遍，和配置声明的长度比对，帧数或帧率对不上就报错并指出是哪个片段。`Clip.filters` 里混进变速或改帧率的滤镜，会在这一步现形。

### 6. 字号下限告警

`fontsize` 占视频高度的比例低于 `--min-font-ratio` 时打 warning，该参数默认 0.03，屏高 1080 下也就是字号低于 33 就告警。用 `drawtext` 顶掉 `fontsize` 的写法不参与这个检查，告警只看字段本身。

### 7. 校验帧导出

每个 clip 渲染完成后，会把它的首、中、末三帧导到 `verify_dir`，文件名形如 `0585_00_c58a13_0026.png`，依次是所在 Range 起点、clip 序号、片段身份的摘要与帧号。摘要取自片段的源和取帧区间，所以同一个位置换了画面会导新图，不会拿上一次留下的旧图糊弄过去。只抽一个时间点很容易看走眼，三帧能看出这一段是否稳定、有没有夹进转场。`verify_dir` 是纯产物，随时可以整个删掉。

### 8. 时长对齐加直接复制，接缝不留空档

早先拼接走 concat 分离器配 `-c:v copy`。AAC 按 1024 个采样一帧对齐，段内音频往往比视频长十几毫秒，而分离器是**按容器时长累加偏移**的，下一段于是晚十几毫秒才开始，接缝处留下一个不到一帧的空档，成片在那里定格一下。

中途改成 concat 滤镜加 `setpts=N/FRAME_RATE/TB`，按帧序号重写时间戳，接缝是没有了，代价是整条片子必须重编码。

现在两头都要：先把每段的音频 pad 或裁到精确的 `帧数 × 分母 / 分子`，容器时长与视频严格相等，最终拼接就能回到 concat 分离器加 `-c copy`，一帧画面都不用再编，成片也不再多掉一代画质。`align_duration` 负责对齐，`cat_video_copy` 负责复制拼接，段内拼接仍走滤镜。

`check_video` 会核对时间戳连续性，对齐漏了或者哪段没对齐，成片校验会当场报出来并指出帧号。

### 9. 文本可单独指定字体

`--font-file` 给整个工程定一种字体，某一段想换字体就在那个 `[[ranges.texts]]` 里写 `fontfile`，相对路径以项目根为基准，文件不存在会直接报错。亏损句用更重的字、标题句用手写体这类需求不用再拆工程。

### 10. 文本样式：drawtext 参数与图层滤镜

`Text` 只暴露了常用的那几个字段，剩下的分两个口子往外接，一在 drawtext 之内，一在它之后。

`drawtext` 接的是 **drawtext 自身的参数**，拼在默认值后面。ffmpeg 的同名选项后者覆盖前者，所以能直接顶掉默认的 `fontsize`、`fontcolor`、`shadowx`：

```toml
[[ranges.texts]]
text = "你咋可能玩过我"
drawtext = "box=1:boxcolor=black@0.5:boxborderw=12"  # 垫一块半透明底板
```

`filters` 接的是**这一层字幕图的滤镜链**，和 `Clip.filters` 一个意思，接在 drawtext 之后，只作用在这一条字幕上，同屏的别的字幕不受影响：

```toml
[[ranges.texts]]
text = "我玩的就是贷款仓"
filters = "gblur=sigma=4"  # 这一句糊一点，让它在背景前退后
```

`filters` 不许改变图层尺寸。字幕图是整屏大小，缩放会让叠加错位，编译时直接报错。改帧数的滤镜也没有意义，一层只有一帧。

`boxcolor` 的 `@透明度` 会被 ffmpeg 平方：写 `@0.6`，图层里拿到的是 RGB 已按 0.6 预乘、alpha 却只有 0.36，叠到画面上还要再乘一次，最终约等于 0.22。想要看着像半透明就往上写，`@0.8` 大约得到 0.5 的观感。

两个字段的值都不做转义，里面带逗号时要么整段用单引号包住，要么写成 `\,`，否则逗号会被当成两个滤镜的分界。`drawtext` 里的颜色必须是 ffmpeg 认的颜色，写 `0x00FF00`、`white`、`black@0.5` 这些，`colors` 表里的中文别名只对 `fontcolor` 字段生效。

> 天哪，这也太自动了！接下来就要自动生成 bug 了！

---

## 🧪 示例配置

[示例.MyGICA.toml](示例.MyGICA.toml)

[示例.MyGICA.mp4](output_dir/示例.MyGICA.mp4)

[日不落的爱音.MyGICA.toml](%E6%97%A5%E4%B8%8D%E8%90%BD%E7%9A%84%E7%88%B1%E9%9F%B3.MyGICA.toml)

[日不落的爱音.MyGICA.mp4](https://www.bilibili.com/video/BV1KQa9zFE69)

---

## ▶️ 使用方法

### 1. 准备工作

- Windows 系统 + uv 工具管理器 安装方式

```powershell
winget install Gyan.FFmpeg # 安装 ffmpeg 至系统 PATH
winget install astral-sh.uv # 安装 uv 工具管理器至系统 PATH
uv tool install MyGICA    # 安装 MyGICA 工具至系统 PATH
```

- pip 安装方式

```powershell
# 自行配置 FFmpeg 至系统 PATH
# 自行管理 Python 环境
pip install MyGICA
```

### 2. 修改配置文件

编辑 `示例.MyGICA.toml`，填写你的项目信息、素材路径、字幕内容等。

### 3. 运行脚本

编译项目，在 powershell 运行

```powershell
MyGICA 示例.MyGICA.toml
```

输出文件将保存在 `output_dir/{{project_name}}`。

做 MAD 时常常只想看其中几句，用 `--range` 只渲染指定 Range 即可，输出名会带上 `_r3-5` 后缀，不会盖掉完整版。

```powershell
MyGICA 示例.MyGICA.toml --range 3-5    # 只渲染第 3 到第 5 个 Range，1 起数
MyGICA 示例.MyGICA.toml --range 8      # 只渲染第 8 个 Range
```

素材、字体、缓存与输出目录都相对 TOML 所在目录解析，需要换基准时用 `--root` 指定。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--root` | TOML 所在目录 | 相对路径的解析基准 |
| `--font-file` | `SC-Heavy.otf` | 字体文件 |
| `--cache-dir` | `cache_dir` | 缓存目录 |
| `--output-dir` | `output_dir` | 输出目录 |
| `--range` | 不过滤 | 只渲染指定的 Range |
| `--min-font-ratio` | `0.03` | 字号相对屏高的下限 |
| `--verify` | 开启 | 导出每个 clip 的首中末帧 |
| `--verify-dir` | `verify_dir` | 校验帧的落盘目录 |

---

## 🧪 测试

测试位于 `tests/`，覆盖配置解析与校验、帧率解析、帧与时间换算、TOML 转义、字幕分层与淡入淡出排期、帧数计数、Range 选择解析、缓存清理策略，以及三个命令行入口的参数与报错行为。源码包 sdist 已包含 `tests/`，从源码仓库或 sdist 解包目录都能跑。

```powershell
uv run pytest
```

除少数构造完整 `ScriptConfig` 的用例需要系统 PATH 中的 ffmpeg 外，其余用例不调用 ffmpeg。

---

## 🎵 背景音乐处理

脚本会自动将背景音乐 `bgm` 合并到视频中：

---

## 🧾 输出目录结构

```
output_dir/
├── {{project.MyGICA.mp4}} # 最终视频
└── {{project.MyGICA.ass}} # 选帧理由外挂字幕，仅当存在 reason 时生成
cache_in/
└── ...                    # 可选的输入素材
cache_dir/
├── seg_*.mp4              # 各段缓存片段
└── concat_list.txt        # 拼接列表
verify_dir/
└── *.png                  # 每个 clip 的首中末帧，校验画面用，可整个删
```

---

## 🧠 注意事项

- 时间单位为帧（frame）。
- 所有视频源、字体文件、bgm 等必须存在，否则报错。

---

## 🎯 选帧约定

每个 range 的内容都需要逐帧查看，尤其是 AI。

---

## 🧰 依赖工具

- 需要配置
    - [ffmpeg](https://ffmpeg.org/)需要安装至系统 PATH （见使用方法）
- 无需配置
    - Python 3.13
    - 第三方库：`dacite`
    - 自用库 `betterer`（已打包）

---

## 📬 联系方式

如需技术支持，请联系项目维护者。bilibili@不死の祥云。

---

> 🎬 MyGICA —— 让视频剪辑变得简单又自动化！（并不能）